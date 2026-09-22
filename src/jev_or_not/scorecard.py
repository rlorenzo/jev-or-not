"""Scrape and verify the fan-maintained scorecard at https://robotornot.info/.

See PLAN.md Phase 0 steps 2-3. Structure per Research Notes.md section E: a
single ``<ul class="list">`` of ``<li>`` rows, each with a reference link, an
``<a class="ep">`` episode link, and a verdict ``<span>``.
"""

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx

from jev_or_not.fingerprint import file_hash, fingerprint
from jev_or_not.models import AdjudicationEntry, ScorecardRow, ScorecardRuling

SCORECARD_URL = "https://robotornot.info/"
USER_AGENT = "RobotOrNot-Evaluation/1.0 (+https://github.com/rlorenzo/jev-or-not)"
CODE_VERSION = "scorecard-v1"
LOCAL_DIR = Path("local/scorecard")
GOLD_DIR = Path("data/gold")
SCHEMA_VERSION = 1
EP_HREF_RE = re.compile(r"/robot/(\d+)/?")
LABEL_MAP = {"robot": "yes", "not_robot": "no", "unresolved": "unresolved"}
ANOMALY_THRESHOLD = 50
ADJUDICABLE_FIELDS = {"episode", "episode_id", "subject", "aliases", "label", "review_status"}


class _RawRow:
    __slots__ = ("ref_href", "item_text", "ep_href", "ep_text", "verdict_class", "glyph_text")

    def __init__(self) -> None:
        self.ref_href: str | None = None
        self.item_text = ""
        self.ep_href: str | None = None
        self.ep_text = ""
        self.verdict_class: str | None = None
        self.glyph_text = ""


class _ScorecardParser(HTMLParser):
    """Collects <li> rows inside <ul class="list"> only."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[_RawRow] = []
        self._in_list = False
        self._cur: _RawRow | None = None
        self._target: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_d = dict(attrs)
        classes = (attrs_d.get("class") or "").split()
        if tag == "ul" and "list" in classes:
            self._in_list = True
            return
        if not self._in_list:
            return
        if tag == "li":
            self._cur = _RawRow()
        elif tag == "a" and self._cur is not None:
            if "ep" in classes:
                self._cur.ep_href = attrs_d.get("href")
                self._target = "ep_a"
            else:
                self._cur.ref_href = attrs_d.get("href")
                self._target = "ref_a"
        elif tag == "span" and self._cur is not None:
            self._cur.verdict_class = (attrs_d.get("class") or "").strip()
            self._target = "span"

    def handle_endtag(self, tag: str) -> None:
        if tag == "ul" and self._in_list:
            self._in_list = False
        elif tag == "li" and self._cur is not None:
            self.rows.append(self._cur)
            self._cur = None
            self._target = None
        elif tag in ("a", "span"):
            self._target = None

    def handle_data(self, data: str) -> None:
        if self._cur is None or self._target is None:
            return
        if self._target == "ref_a":
            self._cur.item_text += data
        elif self._target == "ep_a":
            self._cur.ep_text += data
        elif self._target == "span":
            self._cur.glyph_text += data


def _map_verdict(verdict_class: str) -> str:
    if verdict_class == "robot":
        return "robot"
    if verdict_class == "not":
        return "not_robot"
    return "unresolved"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unknown"


def fetch_html() -> str:
    resp = httpx.get(
        SCORECARD_URL, headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
    )
    resp.raise_for_status()
    return resp.text


def parse_rows(html: str, retrieved_at: str, source_html_hash: str) -> list[ScorecardRow]:
    parser = _ScorecardParser()
    parser.feed(html)
    rows = []
    seen: dict[str, int] = {}
    for idx, raw in enumerate(parser.rows):
        item = (raw.item_text or "").strip()
        ep_text = (raw.ep_text or "").strip()
        verdict_class = raw.verdict_class or ""
        glyph = (raw.glyph_text or "").strip()
        verdict = _map_verdict(verdict_class)
        verdict_raw = f"{verdict_class or 'none'}:{glyph or 'none'}"

        episode = None
        if raw.ep_href:
            m = EP_HREF_RE.search(raw.ep_href)
            if m:
                episode = int(m.group(1))

        normalized = f"{item.lower()}|{ep_text.lower()}|{verdict_raw.lower()}"
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
        # suffix = occurrence count of this content, so IDs survive upstream row insertions
        seen[digest] = seen.get(digest, 0) + 1
        source_row_id = f"{digest}-{seen[digest]}"

        rows.append(
            ScorecardRow(
                schema_version=SCHEMA_VERSION,
                source_row_id=source_row_id,
                row_index=idx,
                item=item,
                episode=episode,
                episode_link_text=ep_text,
                verdict=verdict,
                verdict_raw=verdict_raw,
                reference_url=raw.ref_href or "",
                episode_url=raw.ep_href or "",
                retrieved_at=retrieved_at,
                source_html_hash=source_html_hash,
            )
        )
    return rows


def _detect_anomalous_row_indices(rows: list[ScorecardRow]) -> dict[int, str]:
    """Row indices whose episode number jumps >50 from both page-order neighbors."""
    valid = [(r.row_index, r.episode) for r in rows if r.episode is not None]
    anomalies: dict[int, str] = {}
    for pos, (row_index, ep) in enumerate(valid):
        prev_ep = valid[pos - 1][1] if pos > 0 else None
        next_ep = valid[pos + 1][1] if pos < len(valid) - 1 else None
        diffs = [abs(ep - n) for n in (prev_ep, next_ep) if n is not None]
        if diffs and all(d > ANOMALY_THRESHOLD for d in diffs):
            anomalies[row_index] = (
                f"episode {ep} out of page order by more than {ANOMALY_THRESHOLD} "
                f"relative to neighbors ({prev_ep!r}, {next_ep!r})"
            )
    return anomalies


def build_rulings(rows: list[ScorecardRow]) -> list[ScorecardRuling]:
    """Derive one ruling per distinct (episode, subject), preserving page order."""
    anomalies = _detect_anomalous_row_indices(rows)
    groups: dict[tuple, list[ScorecardRow]] = {}
    order: list[tuple] = []
    for row in rows:
        key = (row.episode, row.item.strip().lower())
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    rulings = []
    for key in order:
        episode, _subject_norm = key
        group_rows = groups[key]
        subject_display = group_rows[0].item.strip()
        labels = {LABEL_MAP[r.verdict] for r in group_rows}
        source_row_ids = [r.source_row_id for r in group_rows]

        reasons = []
        if episode is None:
            reasons.append("unparseable episode number")
        reasons.extend(anomalies[r.row_index] for r in group_rows if r.row_index in anomalies)

        if len(labels) > 1:
            reasons.append(f"conflicting verdicts within same episode+subject: {sorted(labels)}")
            label = "unresolved"
        else:
            label = next(iter(labels))

        episode_id = f"theincomparable/robot/{episode}" if episode is not None else None
        ruling_ref = f"sc-{episode if episode is not None else 'unk'}-{_slug(subject_display)}"

        rulings.append(
            ScorecardRuling(
                schema_version=SCHEMA_VERSION,
                ruling_ref=ruling_ref,
                episode_id=episode_id,
                episode=episode,
                subject=subject_display,
                label=label,
                source_row_ids=source_row_ids,
                review_status="quarantined" if reasons else "auto",
                quarantine_reason="; ".join(reasons) or None,
            )
        )
    return rulings


def ensure_adjudication_file() -> Path:
    path = GOLD_DIR / "adjudication.jsonl"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        header = {
            "schema_version": SCHEMA_VERSION,
            "adjudication_version": "0.1",
            "note": "manual decisions; append-only",
        }
        path.write_text(json.dumps(header, separators=(",", ":")) + "\n")
    return path


def read_adjudication_entries(path: Path) -> list[AdjudicationEntry]:
    lines = path.read_text().splitlines()
    entries = []
    for line in lines[1:]:  # first line is the header record, not a decision
        line = line.strip()
        if line:
            entries.append(AdjudicationEntry(**json.loads(line)))
    return entries


def apply_adjudication(
    rulings: list[ScorecardRuling], entries: list[AdjudicationEntry]
) -> list[ScorecardRuling]:
    if not entries:
        return rulings
    by_row_id: dict[str, list[ScorecardRuling]] = {}
    for ruling in rulings:
        for rid in ruling.source_row_ids:
            by_row_id.setdefault(rid, []).append(ruling)
    for entry in entries:
        for ruling in by_row_id.get(entry.source_row_id, []):
            if entry.field in ADJUDICABLE_FIELDS:
                setattr(ruling, entry.field, entry.new)
                ruling.correction_reason = entry.reason
    return rulings


def _write_jsonl_atomic(path: Path, records: list[ScorecardRow] | list[ScorecardRuling]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            for record in records:
                f.write(record.model_dump_json() + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _write_report(rows: list[ScorecardRow], rulings: list[ScorecardRuling], out_path: Path) -> None:
    distinct_episodes = {r.episode for r in rows if r.episode is not None}
    label_counts: dict[str, int] = {}
    for ruling in rulings:
        label_counts[ruling.label] = label_counts.get(ruling.label, 0) + 1

    subject_episodes: dict[str, dict[int | None, str]] = {}
    for ruling in rulings:
        subject_episodes.setdefault(ruling.subject.strip().lower(), {})[ruling.episode] = (
            ruling.label
        )
    duplicated = {s: eps for s, eps in subject_episodes.items() if len(eps) > 1}

    quarantined = [r for r in rulings if r.review_status == "quarantined"]
    ep263_rows = [r for r in rows if "263" in r.episode_link_text]

    lines = [
        "# Scorecard report",
        "",
        "## Counts",
        f"- rows: {len(rows)}",
        f"- distinct episodes: {len(distinct_episodes)}",
        f"- label distribution: {label_counts}",
        f"- unresolved rulings: {label_counts.get('unresolved', 0)}",
        "",
        "## Subjects duplicated across episodes",
    ]
    if duplicated:
        for subj, by_ep in duplicated.items():
            eps = ", ".join(
                f"ep {ep}: {label}"
                for ep, label in sorted(by_ep.items(), key=lambda kv: (kv[0] is None, kv[0]))
            )
            lines.append(f"- {subj}: {eps}")
    else:
        lines.append("- none")

    lines += ["", "## Quarantined rulings"]
    if quarantined:
        for r in quarantined:
            lines.append(
                f"- {r.ruling_ref} (episode {r.episode}, subject {r.subject!r}): "
                f"{r.quarantine_reason}"
            )
    else:
        lines.append("- none")

    excluded = [r for r in rulings if r.review_status == "excluded"]
    lines += ["", "## Excluded rulings (adjudicated)"]
    lines += [f"- {r.ruling_ref}: {r.correction_reason}" for r in excluded] or ["- none"]
    lines += ["", '## "Episode 263" row(s)']
    if ep263_rows:
        for row in ep263_rows:
            lines.append(f"```json\n{row.model_dump_json()}\n```")
    else:
        lines.append("- none found")

    out_path.write_text("\n".join(lines) + "\n")


def run(force: bool = False) -> dict:
    """Scrape the scorecard and (re)derive the verified scorecard. Returns a summary."""
    html = fetch_html()

    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    raw_path = LOCAL_DIR / f"robotornot-{stamp}.html"
    raw_path.write_text(html)
    source_html_hash = file_hash(str(raw_path))
    retrieved_at = datetime.now(UTC).isoformat()

    rows = parse_rows(html, retrieved_at, source_html_hash)
    _write_jsonl_atomic(GOLD_DIR / "scorecard.jsonl", rows)

    adjudication_path = ensure_adjudication_file()
    fp = fingerprint(
        source_html_hash=source_html_hash,
        code_version=CODE_VERSION,
        adjudication_hash=file_hash(str(adjudication_path)),
    )
    fp_path = GOLD_DIR / "scorecard.fingerprint"
    verified_path = GOLD_DIR / "scorecard_verified.jsonl"

    if (
        not force
        and fp_path.exists()
        and verified_path.exists()
        and fp_path.read_text().strip() == fp
    ):
        return {
            "rows": len(rows),
            "rulings": None,
            "reused_verified": True,
            "raw_html_path": str(raw_path),
        }

    entries = read_adjudication_entries(adjudication_path)

    rulings = build_rulings(rows)
    rulings = apply_adjudication(rulings, entries)
    _write_jsonl_atomic(verified_path, rulings)
    fp_path.write_text(fp)
    _write_report(rows, rulings, GOLD_DIR / "scorecard_report.md")

    return {
        "rows": len(rows),
        "rulings": len(rulings),
        "reused_verified": False,
        "raw_html_path": str(raw_path),
    }
