"""Phase 2 bake-off and Phase 3 extractor scoring against the sealed pilot reference.

PLAN.md Phase 2 "Transcriber bake-off" and Phase 3 "Model choice".

Matching. The reference was built by pooled review (docs/annotation_procedure.md):
every run's rulings for an episode were grouped into candidates, and Rex
approved, fixed or rejected each candidate. That decision *is* the reviewed,
one-to-one match the plan asks for, made blind to which run found what and
without looking at any run's verdict. ``match`` replays the same grouping
(``Desk.candidates``) and writes one row per extracted ruling to
``data/gold/pilot_matches.jsonl`` before anything is scored. Scoring reads
only that frozen file.

Everything except the loaders is pure.
"""

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from jev_or_not.annotate import Desk
from jev_or_not.common import read_jsonl, write_lines_atomic
from jev_or_not.review_pool import same_subject, subject_tokens

MATCHES_PATH = Path("data/gold/pilot_matches.jsonl")
SCORECARD_LINKS_PATH = Path("data/gold/pilot_scorecard_links.jsonl")
# Rex's calls on proposed/unmatched rows: one JSON object per line with the
# run key, extracted_ruling_id, reference_ruling_id (null = not a reference
# ruling), decided_by, decided_at.
MATCH_REVIEWS_PATH = Path("data/gold/pilot_match_reviews.jsonl")
BAKEOFF_REPORT = Path("reports/transcriber-bakeoff.md")
EXTRACTION_REPORT = Path("reports/extraction-pilot.md")
REVIEW_DIR = Path("local/review")
ENROLLED_DIR = Path("local/transcripts/enrolled")

CANDIDATES = ("A", "A2", "B")
MODELS = {"claude-sonnet-subagent": "sonnet", "claude-haiku-subagent": "haiku"}
GATE = 0.95
# Haiku is chosen only within this many points of Sonnet on both metrics
HAIKU_MARGIN = 0.02
BINARY = ("yes", "no")
REVIEW_CONTEXT_S = 20.0

# PLAN.md Phase 2 bake-off item 3: the fixed terms plus subject names from the
# reviewed pilot reference. A term counts as expected in an episode when any
# candidate's transcript of it contains the term, so a term no one transcribed
# (because it was never said) does not penalise anyone.
FIXED_TERMS = ("Siracusa", "Snell", "Incomparable", "Roomba", "droid", "Daleks")
EPISODE_TERMS = {
    "1": ("Roboto", "Styx", "Kilroy"),
    "2": ("self-checkout", "ATM"),
    "6": ("Johnny Cab", "Total Recall", "drone"),
    "50": ("Agent Smith", "Matrix", "Sentinel"),
    "100": ("Starship",),
    "200": ("microscopic",),
    "298": ("Chewbacca", "Boba Fett", "Saruman", "Uruk-hai", "Darth Vader", "Oddjob"),
    "300": ("Burger King", "Starbucks", "Marks & Spencer", "Subway", "Five Guys", "In-N-Out"),
    "345": ("backpack",),
    "350": ("marinara", "alfredo", "salsa"),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ep_label(episode_id: str) -> str:
    return episode_id.rsplit("/", 1)[-1]


def _pct(num: int, den: int) -> str:
    return f"{num}/{den} ({num / den:.1%})" if den else "0/0 (unmeasured)"


# ---- matching ------------------------------------------------------------


def load_sealed_reference(desk: Desk) -> list[dict]:
    """The live reference rows (superseded ones dropped), refusing anything
    but the sealed bytes."""
    seal = desk.seal()
    if seal is None:
        raise RuntimeError("the pilot reference is not sealed; seal it in the desk first")
    if _sha256(desk.reference_path) != seal["reference_sha256"]:
        raise RuntimeError("pilot_reference.jsonl no longer matches its seal")
    superseded = desk._superseded_ids()
    return [r for r in read_jsonl(desk.reference_path) if r["ruling_id"] not in superseded]


def resolve(ruling_id: str | None, superseded: dict[str, str]) -> str | None:
    """Follow supersessions to the live reference ruling."""
    seen = set()
    while ruling_id in superseded and ruling_id not in seen:
        seen.add(ruling_id)
        ruling_id = superseded[ruling_id]
    return ruling_id


def pooled_matches(desk: Desk) -> list[dict]:
    """One row per extracted ruling in the reviewed pool, matched through
    Rex's decision on its candidate. Run once, then frozen: later
    extractions change the pool, so it is never recomputed."""
    rows = []
    for ep in desk.pilot():
        for c in desk.candidates(ep, with_members=True):
            matched = c["ruling_id"] if c["decision"] in ("approved", "fixed") else None
            for m in c["members"]:
                candidate, model_id = m["run"].split("|", 1)
                rows.append(
                    {
                        "schema_version": 1,
                        "episode_id": ep["episode_id"],
                        "candidate": candidate,
                        "model": MODELS.get(model_id, model_id),
                        "prompt_version": m["prompt_version"],
                        "extracted_ruling_id": m["ruling_id"],
                        "subject": m["subject"],
                        "extracted_verdict": m["verdict"],
                        "pool_candidate_id": c["candidate_id"],
                        "decision": c["decision"],
                        "reference_ruling_id": matched,
                        "method": "pooled_review",
                    }
                )
    return rows


def _run_key(row: dict) -> tuple:
    return row["episode_id"], row["candidate"], row["model"], row["prompt_version"]


def alias_matches(
    new_rows: list[dict], frozen: list[dict], reference: list[dict], superseded: dict[str, str]
) -> list[dict]:
    """Match rulings from a run that was not in the reviewed pool.

    PLAN.md Phase 3: exact normalized names and reviewed aliases first. A
    reviewed alias is any subject phrasing the pooled review already tied to
    a reference ruling. Anything else gets at most a *proposed* match (same
    subject by token overlap) that stays unmatched until Rex accepts it;
    verdicts never take part.
    """
    aliases: dict[str, set[frozenset[str]]] = defaultdict(set)
    for r in reference:
        aliases[r["ruling_id"]].add(subject_tokens(r["subject"]))
    for f in frozen:
        if (rid := resolve(f["reference_ruling_id"], superseded)) and rid in aliases:
            aliases[rid].add(subject_tokens(f["subject"]))
    by_episode: dict[str, list[str]] = defaultdict(list)
    for r in reference:
        by_episode[r["episode_id"]].append(r["ruling_id"])

    out = []
    for row in new_rows:
        tokens = subject_tokens(row["subject"])
        here = by_episode.get(row["episode_id"], [])
        exact = [rid for rid in here if tokens in aliases[rid]]
        proposed = [rid for rid in here if any(same_subject(tokens, a) for a in aliases[rid])]
        if not proposed:  # a looser hint: any shared content word
            proposed = [rid for rid in here if any(tokens & a for a in aliases[rid])]
        if len(exact) == 1:
            matched, method = exact[0], "alias"
        elif len(proposed) == 1:
            matched, method = None, "proposed"
        else:
            matched, method = None, "unmatched"
        out.append(
            {
                **row,
                "reference_ruling_id": matched,
                "proposed_ruling_id": proposed[0] if method == "proposed" else None,
                "method": method,
            }
        )
    return out


def scorecard_links(desk: Desk) -> list[dict]:
    """Scorecard rows in the pilot and the reference ruling they pooled with."""
    links = []
    for ep in desk.pilot():
        for c in desk.candidates(ep, with_members=True):
            matched = c["ruling_id"] if c["decision"] in ("approved", "fixed") else None
            for sc in c["scorecard_refs"]:
                links.append(
                    {
                        "schema_version": 1,
                        "episode_id": ep["episode_id"],
                        "subject": c["subject"],
                        "scorecard_ref": sc["ruling_ref"],
                        "scorecard_label": sc["label"],
                        "reference_ruling_id": matched,
                        "decision": c["decision"],
                    }
                )
    return links


# ---- extractor scoring ---------------------------------------------------


def score_run(reference: list[dict], matches: list[dict], run_episodes: set[str]) -> dict:
    """PLAN.md Phase 3 metrics for one (candidate, model) run.

    ``matches`` are this run's rows only; ``run_episodes`` the episodes it
    produced output for. A reference ruling in an episode the run failed on
    still counts as missed, so failures cost recall rather than vanishing.
    """
    by_ref: dict[str, list[str]] = defaultdict(list)
    for m in matches:
        if m["reference_ruling_id"]:
            by_ref[m["reference_ruling_id"]].append(m["extracted_verdict"])
    binary = [r for r in reference if r["verdict"] in BINARY]
    found = [r for r in reference if r["ruling_id"] in by_ref]
    matched_binary = [r for r in binary if r["ruling_id"] in by_ref]
    # a run that split one ruling in two agrees only if its first call does
    agree = [r for r in matched_binary if by_ref[r["ruling_id"]][0] == r["verdict"]]
    verdicts = Counter(m["extracted_verdict"] for m in matches)
    return {
        "rows": len(matches),
        "episodes": len(run_episodes),
        "found": len(found),
        "reference": len(reference),
        "agree": len(agree),
        "matched_binary": len(matched_binary),
        "binary": len(binary),
        "extras": sum(1 for m in matches if not m["reference_ruling_id"]),
        "duplicates": sum(len(v) - 1 for v in by_ref.values()),
        "ambiguous": verdicts["ambiguous"],
        "no_ruling": verdicts["no_ruling"],
        "missed": [r["ruling_id"] for r in reference if r["ruling_id"] not in by_ref],
        "wrong": [
            (r["ruling_id"], by_ref[r["ruling_id"]][0])
            for r in matched_binary
            if by_ref[r["ruling_id"]][0] != r["verdict"]
        ],
        "verdict_by_ref": {k: v[0] for k, v in by_ref.items()},
    }


def model_choice(sonnet: dict, haiku: dict) -> str:
    """Sonnet unless Haiku is within HAIKU_MARGIN on agreement and recall."""

    def rate(n: int, d: int) -> float:
        return n / d if d else 0.0

    agree_gap = rate(sonnet["agree"], sonnet["matched_binary"]) - rate(
        haiku["agree"], haiku["matched_binary"]
    )
    recall_gap = rate(sonnet["found"], sonnet["reference"]) - rate(
        haiku["found"], haiku["reference"]
    )
    return "haiku" if agree_gap <= HAIKU_MARGIN and recall_gap <= HAIKU_MARGIN else "sonnet"


# ---- transcript scoring --------------------------------------------------


def span_speaker(transcript: dict, start: float, end: float) -> tuple[str | None, float]:
    """Enrolled speaker with the most word time inside [start, end]."""
    time: Counter[str] = Counter()
    for seg in transcript["segments"]:
        for w in seg.get("words") or []:
            overlap = min(w["end"], end) - max(w["start"], start)
            if overlap > 0:
                time[w.get("speaker") or seg.get("speaker") or "UNKNOWN"] += overlap
    if not time:
        return None, 0.0
    speaker, t = time.most_common(1)[0]
    return speaker, t / sum(time.values())


def _transcript_text(transcript: dict) -> str:
    return " ".join(s["text"] for s in transcript["segments"]).lower()


def term_hits(transcripts: dict[str, dict[str, dict]]) -> dict[str, dict]:
    """Per candidate: expected term occurrences found, and the terms it missed.

    ``transcripts`` is ``{candidate: {episode_label: transcript}}``.
    """
    texts = {
        c: {ep: _transcript_text(t) for ep, t in eps.items()} for c, eps in transcripts.items()
    }
    episodes = sorted({ep for eps in texts.values() for ep in eps}, key=int)
    out = {c: {"found": 0, "expected": 0, "missed": []} for c in texts}
    for ep in episodes:
        for term in (*FIXED_TERMS, *EPISODE_TERMS.get(ep, ())):
            needle = term.lower()
            have = {c for c in texts if needle in texts[c].get(ep, "")}
            if not have:
                continue
            for c in texts:
                out[c]["expected"] += 1
                if c in have:
                    out[c]["found"] += 1
                else:
                    out[c]["missed"].append(f"{ep}: {term}")
    return out


def review_sheet(transcript: dict, rulings: list[dict], candidate: str) -> str:
    """local/review/{episode}-{candidate}.md: segments around each final ruling."""
    ep = _ep_label(transcript["episode_id"])
    lines = [f"# Episode {ep}, candidate {candidate}: speaker attribution on rulings", ""]
    for r in sorted(rulings, key=lambda r: r["ruling_start_s"]):
        a, b = r["ruling_start_s"], r["ruling_end_s"]
        speaker, share = span_speaker(transcript, a, b)
        mark = "OK" if speaker == r["speaker"] else "CHECK"
        lines += [
            f"## {r['subject']} ({r['verdict']}) {a:.1f}-{b:.1f}s",
            "",
            f"Reference speaker {r['speaker']}; transcript says {speaker} "
            f"({share:.0%} of word time). **{mark}**",
            "",
        ]
        for seg in transcript["segments"]:
            if seg["end"] > a - REVIEW_CONTEXT_S and seg["start"] < b + REVIEW_CONTEXT_S:
                inside = seg["end"] > a and seg["start"] < b
                lines.append(
                    f"- {'**' if inside else ''}{seg['start']:.1f}-{seg['end']:.1f} "
                    f"{seg.get('speaker')}{'**' if inside else ''}: {seg['text'].strip()}"
                )
        lines.append("")
    return "\n".join(lines)


# ---- report --------------------------------------------------------------


def _attribution(reference: list[dict], transcripts: dict[str, dict]) -> tuple[int, list[str]]:
    ok, bad = 0, []
    for r in reference:
        t = transcripts.get(_ep_label(r["episode_id"]))
        speaker = span_speaker(t, r["ruling_start_s"], r["ruling_end_s"])[0] if t else None
        if speaker == r["speaker"]:
            ok += 1
        else:
            bad.append(f"{_ep_label(r['episode_id'])} {r['subject']}: {speaker}")
    return ok, bad


def _speed(transcripts: dict[str, dict], durations: dict[str, float]) -> tuple[float, float]:
    wall = sum((t.get("runtime") or {}).get("total_s", 0.0) for t in transcripts.values())
    audio = sum(durations[ep] for ep in transcripts)
    return wall, audio


def _gate(num: int, den: int) -> str:
    if not den:
        return "unmeasured"
    return "pass" if num / den >= GATE else "FAIL"


def _runs(reference: list[dict], matches: list[dict]) -> dict[tuple[str, str, str], dict]:
    """Scores per (candidate, model, prompt_version), in a stable order."""
    keys = sorted(
        {(m["candidate"], m["model"], m["prompt_version"]) for m in matches},
        key=lambda k: (CANDIDATES.index(k[0]), k[2], -ord(k[1][0])),
    )
    out = {}
    for key in keys:
        mine = [m for m in matches if (m["candidate"], m["model"], m["prompt_version"]) == key]
        out[key] = score_run(reference, mine, {m["episode_id"] for m in mine})
    return out


def build_reports(
    reference: list[dict],
    matches: list[dict],
    links: list[dict],
    transcripts: dict[str, dict[str, dict]],
    durations: dict[str, float],
    seal: dict,
) -> tuple[str, str]:
    """(transcriber bake-off report, extraction pilot report) as markdown.

    ``matches`` must already point at live reference rulings (see ``resolve``).
    """
    ref_by_id = {r["ruling_id"]: r for r in reference}
    runs = _runs(reference, matches)
    first_prompt = min(k[2] for k in runs)
    proposals = [m for m in matches if m["method"] in ("proposed", "unmatched")]

    def _order(ruling_id: str) -> tuple[int, str]:
        return int(_ep_label(ref_by_id[ruling_id]["episode_id"])), ruling_id

    def label(ruling_id: str) -> str:
        r = ref_by_id[ruling_id]
        return f"{_ep_label(r['episode_id'])} {r['subject']} ({r['verdict']})"

    def name(key: tuple[str, str, str]) -> str:
        return " / ".join(key)

    head = [
        f"Reference: `data/gold/pilot_reference.jsonl` v{seal['reference_version']}, "
        f"{len(reference)} live rulings, sha256 `{seal['reference_sha256'][:16]}…`. "
        "Matches frozen in `data/gold/pilot_matches.jsonl` (see `jev_or_not.bakeoff`).",
        "",
        "> The reference is **pooled**: it holds only rulings that a run, the scorecard, or "
        "Rex found while reviewing. Recall is measured against that inventory, not against "
        "every ruling in the audio, so it can overstate true recall.",
        "",
    ]
    if any(k[2] != first_prompt for k in runs):
        head += [
            f"> Runs after `{first_prompt}` used a prompt revised *after* seeing this pilot's "
            "errors, and are scored on the same pilot. Their numbers are optimistic until "
            "confirmed on episodes outside the pilot.",
            "",
        ]

    # -- transcriber bake-off --
    bk = ["# Transcriber bake-off (Phase 2 pilot)", "", *head]
    bk += [
        "## Gates (extractor: Sonnet)",
        "",
        "| Candidate / prompt | Verdict agreement | Subject recall | Raw speaker attribution "
        "| Gates |",
        "| --- | --- | --- | --- | --- |",
    ]
    attribution = {c: _attribution(reference, transcripts[c]) for c in CANDIDATES}
    sonnet_runs = [k for k in runs if k[1] == "sonnet"]
    for key in sonnet_runs:
        s = runs[key]
        ok, _ = attribution[key[0]]
        gates = [
            _gate(s["agree"], s["matched_binary"]),
            _gate(s["found"], s["reference"]),
            _gate(ok, len(reference)),
        ]
        bk.append(
            f"| {key[0]} / {key[2]} | {_pct(s['agree'], s['matched_binary'])} "
            f"| {_pct(s['found'], s['reference'])} "
            f"| {_pct(ok, len(reference))} | {' / '.join(gates)} |"
        )
    bk += [
        "",
        f"Gates are {GATE:.0%} each (PLAN.md Phase 2). Verdict agreement is over matched binary "
        "reference rulings; an `ambiguous` or `no_ruling` call counts as a disagreement.",
        "",
        "## Speaker attribution misses",
        "",
        "Ruling spans whose dominant enrolled speaker (by word time) is not the reference speaker.",
        "",
    ]
    for c in CANDIDATES:
        bad = attribution[c][1]
        bk.append(f"- **{c}:** " + ("; ".join(bad) if bad else "none"))
    terms = term_hits(transcripts)
    bk += [
        "",
        "## Proper nouns",
        "",
        "| Candidate | Expected terms found | Missed |",
        "| --- | --- | --- |",
    ]
    for c in CANDIDATES:
        t = terms[c]
        bk.append(f"| {c} | {_pct(t['found'], t['expected'])} | {', '.join(t['missed']) or '-'} |")
    bk += [
        "",
        "## Speed (as recorded in each transcript)",
        "",
        "| Candidate | Wall clock | Audio | RTF |",
        "| --- | --- | --- | --- |",
    ]
    for c in CANDIDATES:
        wall, audio = _speed(transcripts[c], durations)
        bk.append(f"| {c} | {wall / 60:.1f} min | {audio / 60:.1f} min | {wall / audio:.2f} |")
    bk += ["", "## Per-run detail (Sonnet)", ""]
    for key in sonnet_runs:
        s = runs[key]
        missed = ", ".join(label(x) for x in s["missed"]) or "none"
        wrong = ", ".join(f"{label(x)} -> {v}" for x, v in s["wrong"]) or "none"
        bk.append(
            f"- **{key[0]} / {key[2]}:** missed {missed}; wrong verdict {wrong}; "
            f"{s['extras']} unmatched extra rulings."
        )
    bk += ["", "Review sheets: `local/review/{episode}-{candidate}.md` (local only).", ""]

    # -- extraction pilot --
    ex = ["# Extraction pilot (Phase 3 model choice)", "", *head]
    ex += [
        "| Run | Episodes | Rows | Verdict agreement | End-to-end correct | Subject recall "
        "| ambiguous / no_ruling | Unmatched extras |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key, s in runs.items():
        ex.append(
            f"| {name(key)} | {s['episodes']} | {s['rows']} "
            f"| {_pct(s['agree'], s['matched_binary'])} | {_pct(s['agree'], s['binary'])} "
            f"| {_pct(s['found'], s['reference'])} | {s['ambiguous']} / {s['no_ruling']} "
            f"| {s['extras']} |"
        )
    pairs = [(k, (k[0], "haiku", k[2])) for k in sonnet_runs if (k[0], "haiku", k[2]) in runs]
    ex += ["", "## Model choice per transcriber (same prompt)", ""]
    for sk, hk in pairs:
        ex.append(f"- **{sk[0]} / {sk[2]}:** {model_choice(runs[sk], runs[hk])}")
    ex += [
        "",
        f"Rule: Sonnet unless Haiku is within {HAIKU_MARGIN:.0%} on both verdict agreement and "
        "subject recall. With 30 rulings one ruling is 3.3 points, so the margin is below the "
        "resolution of this pilot.",
        "",
        "## Haiku vs Sonnet disagreements",
        "",
    ]
    any_dis = False
    for sk, hk in pairs:
        sv, hv = runs[sk]["verdict_by_ref"], runs[hk]["verdict_by_ref"]
        for rid in sorted(set(sv) | set(hv), key=_order):
            if sv.get(rid) != hv.get(rid):
                any_dis = True
                ex.append(
                    f"- {sk[0]} / {sk[2]}: {label(rid)}: sonnet {sv.get(rid, 'missed')}, "
                    f"haiku {hv.get(rid, 'missed')}"
                )
    if not any_dis:
        ex.append("- none")
    if proposals:
        ex += [
            "",
            "## Unconfirmed matches (scored as unmatched until accepted)",
            "",
        ]
        for m in proposals:
            to = label(m["proposed_ruling_id"]) if m.get("proposed_ruling_id") else "nothing"
            ex.append(
                f"- {_ep_label(m['episode_id'])} {m['candidate']} / {m['model']} / "
                f'{m["prompt_version"]} `{m["extracted_ruling_id"]}` "{m["subject"]}" '
                f"({m['extracted_verdict']}): proposed {to}"
            )
    ex += ["", "## Scorecard vs reference", ""]
    any_sc = False
    for link in links:
        rid = link["reference_ruling_id"]
        ref_v = ref_by_id[rid]["verdict"] if rid in ref_by_id else None
        if ref_v != link["scorecard_label"]:
            any_sc = True
            where = (
                f"reference {ref_v}"
                if rid
                else f"not a ruling in the reference ({link['decision']})"
            )
            ex.append(
                f"- {_ep_label(link['episode_id'])} {link['subject']}: "
                f"scorecard {link['scorecard_label']}, {where}"
            )
    if not any_sc:
        ex.append("- none")
    ex += [
        "",
        "Cost and latency: not measured. Extraction ran as Claude Code subagents under a "
        "subscription (decision 2026-09-22), so there is no per-call usage to report.",
        "",
    ]
    return "\n".join(bk), "\n".join(ex)


# ---- command -------------------------------------------------------------


def _load_transcripts(desk: Desk, enrolled_dir: Path) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for c in CANDIDATES:
        out[c] = {}
        for ep in desk.pilot():
            path = enrolled_dir / f"{ep['episode']}-{c}.json"
            if path.exists():
                out[c][str(ep["episode"])] = json.loads(path.read_text())
    return out


def _verdict_rows(desk: Desk) -> list[dict]:
    """Current data/verdicts.jsonl rows in match-row shape."""
    by_hash = desk._candidate_by_hash()
    pilot_ids = {ep["episode_id"] for ep in desk.pilot()}
    rows = []
    for v in read_jsonl(desk.verdicts_path):
        if v["episode_id"] not in pilot_ids:
            continue
        rows.append(
            {
                "schema_version": 1,
                "episode_id": v["episode_id"],
                "candidate": by_hash.get(v["transcript_hash"], "?"),
                "model": MODELS.get(v["extractor_model"], v["extractor_model"]),
                "prompt_version": v["prompt_version"],
                "extracted_ruling_id": v["ruling_id"],
                "subject": v["subject"],
                "extracted_verdict": v["verdict"],
            }
        )
    return rows


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda r: json.dumps(r, sort_keys=True))
    write_lines_atomic(path, (json.dumps(r, sort_keys=True) for r in rows))


def run(
    desk: Desk | None = None,
    *,
    matches_path: Path = MATCHES_PATH,
    links_path: Path = SCORECARD_LINKS_PATH,
    match_reviews_path: Path = MATCH_REVIEWS_PATH,
    bakeoff_path: Path = BAKEOFF_REPORT,
    extraction_path: Path = EXTRACTION_REPORT,
    review_dir: Path = REVIEW_DIR,
    enrolled_dir: Path = ENROLLED_DIR,
) -> dict:
    desk = desk or Desk()
    reference = load_sealed_reference(desk)
    superseded = desk._superseded_ids()
    # The pooled matches and scorecard links are taken once, from the pool
    # Rex reviewed, and never recomputed: new extractions reshape the pool.
    if not matches_path.exists():
        _write_jsonl(matches_path, pooled_matches(desk))
    if not links_path.exists():
        _write_jsonl(links_path, scorecard_links(desk))
    frozen = list(read_jsonl(matches_path))
    known = {(*_run_key(m), m["extracted_ruling_id"]) for m in frozen}
    new_rows = [
        r for r in _verdict_rows(desk) if (*_run_key(r), r["extracted_ruling_id"]) not in known
    ]
    if new_rows:
        frozen += alias_matches(new_rows, frozen, reference, superseded)
        _write_jsonl(matches_path, frozen)
    reviews = {
        (*_run_key(r), r["extracted_ruling_id"]): r
        for r in (read_jsonl(match_reviews_path) if match_reviews_path.exists() else [])
    }
    scored = []
    for m in frozen:
        review = reviews.get((*_run_key(m), m["extracted_ruling_id"]))
        if review and m["method"] in ("proposed", "unmatched"):
            m = {**m, "reference_ruling_id": review["reference_ruling_id"], "method": "reviewed"}
        scored.append({**m, "reference_ruling_id": resolve(m["reference_ruling_id"], superseded)})
    links = [
        {**lk, "reference_ruling_id": resolve(lk["reference_ruling_id"], superseded)}
        for lk in read_jsonl(links_path)
    ]
    transcripts = _load_transcripts(desk, enrolled_dir)
    durations = {str(ep["episode"]): float(ep["duration_s"]) for ep in desk.pilot()}
    bakeoff, extraction = build_reports(
        reference, scored, links, transcripts, durations, desk.seal()
    )
    for path, text in ((bakeoff_path, bakeoff), (extraction_path, extraction)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    review_dir.mkdir(parents=True, exist_ok=True)
    sheets = 0
    for c, eps in transcripts.items():
        for ep, t in eps.items():
            rulings = [r for r in reference if _ep_label(r["episode_id"]) == ep]
            (review_dir / f"{ep}-{c}.md").write_text(review_sheet(t, rulings, c))
            sheets += 1
    return {
        "matches": len(scored),
        "matched": sum(1 for m in scored if m["reference_ruling_id"]),
        "new": len(new_rows),
        "unconfirmed": sum(1 for m in scored if m["method"] in ("proposed", "unmatched")),
        "sheets": sheets,
        "bakeoff": str(bakeoff_path),
        "extraction": str(extraction_path),
    }
