"""Phase 1 steps 1-4: episode catalog and scorecard cross-reference.

Fetches the RSS feed (https://feeds.theincomparable.com/robot) and the
archive index (https://www.theincomparable.com/robot/archive/), reconciles
them into ``data/episodes.jsonl``, and cross-references
``data/gold/scorecard_verified.jsonl`` against the resulting catalog.

See PLAN.md Phase 1 steps 1-4 and Research Notes.md sections C and D. The
archive index was verified by hand (curl, 2026-09-21) to return all 363
``/robot/<n>/`` links (episodes 0-362, no gaps) in one fetch; the visible
"Next" text is template chrome (no ``rel="next"`` link), not an active
paginator -- pagination is still followed defensively below in case that
ever changes.
"""

import contextlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

from defusedxml import ElementTree as ET  # hardened parser for network XML (bandit B314)

from jev_or_not.common import (
    EXCLUDED_PATH,
    FEED_URL,
    ITUNES_NS,
    episode_sort_key,
    http_get,
    page_title,
    parse_duration,
    read_excluded,
    read_jsonl,
    write_lines_atomic,
)
from jev_or_not.fingerprint import file_hash, fingerprint
from jev_or_not.models import Episode
from jev_or_not.scorecard import ensure_adjudication_file, read_adjudication_entries

CODE_VERSION = "catalog-v1"
SCHEMA_VERSION = 1
ARCHIVE_URL = "https://www.theincomparable.com/robot/archive/"
MAX_ARCHIVE_PAGES = 20

LOCAL_DIR = Path("local/catalog")
EPISODES_PATH = Path("data/episodes.jsonl")
REPORT_PATH = Path("data/catalog_report.md")
FINGERPRINT_PATH = Path("data/catalog.fingerprint")
SCORECARD_VERIFIED_PATH = Path("data/gold/scorecard_verified.jsonl")
ADJUDICATION_PATH = Path("data/gold/adjudication.jsonl")

ARCHIVE_HREF_RE = re.compile(r'href="https://www\.theincomparable\.com/robot/(\d+)/?"')
ARCHIVE_NEXT_RE = re.compile(r'<a[^>]+rel="next"[^>]+href="([^"]+)"')
URL_EPISODE_RE = re.compile(r"/robot/(\d+)/?$")
TITLE_EPISODE_RE = re.compile(r"^(\d+):")
BONUS_EPISODE_RE = re.compile(r"^(\d+)([a-zA-Z]+)$")
PAGE_AUDIO_RE = re.compile(r'<audio src="([^"]+)"')
PAGE_DESCRIPTION_RE = re.compile(r'<meta name="twitter:description" content="([^"]*)"')
PAGE_DATE_RE = re.compile(r'<span class="episode-date">([^<]*)</span>')


class _TextExtractor(HTMLParser):
    """Strips tags, keeping text and decoding entities (convert_charrefs default)."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(text: str) -> str:
    parser = _TextExtractor()
    parser.feed(text or "")
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def _episode_from_url(url: str | None) -> int | None:
    if not url:
        return None
    m = URL_EPISODE_RE.search(url)
    return int(m.group(1)) if m else None


def _episode_from_title(title: str) -> int | None:
    m = TITLE_EPISODE_RE.match(title.strip())
    return int(m.group(1)) if m else None


def fetch_feed() -> str:
    return http_get(FEED_URL).text


def parse_feed_items(feed_text: str, retrieved_at: str) -> list[dict]:
    root = ET.fromstring(feed_text)
    items = root.findall("./channel/item")
    return [_parse_feed_item(item, retrieved_at) for item in items]


def fetch_archive_pages() -> tuple[list[str], set[int]]:
    """Follow archive pagination (if any). Returns (raw pages, episode numbers seen)."""
    to_visit = [ARCHIVE_URL]
    seen_urls: set[str] = set()
    pages: list[str] = []
    episode_numbers: set[int] = set()
    while to_visit and len(pages) < MAX_ARCHIVE_PAGES:
        url = to_visit.pop(0)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        text = http_get(url).text
        pages.append(text)
        episode_numbers.update(int(n) for n in ARCHIVE_HREF_RE.findall(text))
        next_match = ARCHIVE_NEXT_RE.search(text)
        if next_match and next_match.group(1) not in seen_urls:
            to_visit.append(next_match.group(1))
    return pages, episode_numbers


def _parse_feed_item(item, retrieved_at: str) -> dict:
    guid_text = item.findtext("guid")
    title = (item.findtext("title") or "").strip()
    canonical_url = (item.findtext("link") or "").strip()
    description_raw = item.findtext("description") or ""
    enclosure = item.find("enclosure")
    audio_url = enclosure.get("url", "") if enclosure is not None else ""
    duration_s = parse_duration(item.findtext(f"{ITUNES_NS}duration"))
    pubdate_raw = item.findtext("pubDate")
    released_at = (
        parsedate_to_datetime(pubdate_raw).astimezone(UTC).isoformat() if pubdate_raw else None
    )
    itunes_episode_raw = item.findtext(f"{ITUNES_NS}episode")
    itunes_episode_raw = itunes_episode_raw.strip() if itunes_episode_raw else None

    return {
        "guid": guid_text.strip() if guid_text else None,
        "title": title,
        "canonical_url": canonical_url,
        "description": strip_html(description_raw),
        "audio_url": audio_url,
        "duration_s": duration_s,
        "released_at": released_at,
        "itunes_episode_raw": itunes_episode_raw,
        "retrieved_at": retrieved_at,
    }


def resolve_episode_identity(item: dict) -> tuple[int | None, str, list[str], bool]:
    """Determine (episode, episode_label, review_queue, blocked) for one feed item.

    Priority: itunes:episode, then canonical URL, then an anchored title
    pattern (PLAN.md Phase 1 step 2). A non-numeric itunes:episode of the
    form ``<digits><letters>`` (e.g. "62b") is a bonus item: episode=None,
    label=the raw value, not blocked. Any other unparseable/missing
    identifier, or disagreement between the three sources, goes to the
    review queue and blocks the entry.
    """
    review: list[str] = []
    blocked = False
    raw = item["itunes_episode_raw"]
    url_ep = _episode_from_url(item["canonical_url"])
    title_ep = _episode_from_title(item["title"])

    episode: int | None = None
    label = ""
    if raw:
        bonus_match = BONUS_EPISODE_RE.match(raw)
        if bonus_match:
            label = raw
        else:
            try:
                episode = int(raw)
                label = str(episode)
            except ValueError:
                review.append(f"unrecognized itunes:episode value {raw!r}")
                blocked = True
                label = raw
    elif url_ep is not None:
        episode = url_ep
        label = str(episode)
        review.append("episode number derived from canonical URL; itunes:episode missing")
    elif title_ep is not None:
        episode = title_ep
        label = str(episode)
        review.append(
            "episode number derived from title pattern; itunes:episode and canonical URL "
            "both missing/unusable"
        )
    else:
        review.append(
            "no episode identifier found (missing itunes:episode, unparseable canonical "
            "URL, no anchored title number)"
        )
        blocked = True

    if episode is not None:
        if url_ep is not None and url_ep != episode:
            review.append(
                f"conflicting episode identifiers: resolved={episode} vs canonical URL={url_ep}"
            )
            blocked = True
        if title_ep is not None and title_ep != episode:
            review.append(
                f"conflicting episode identifiers: resolved={episode} vs title pattern={title_ep}"
            )
            blocked = True

    return episode, label, review, blocked


def fetch_archive_only_episode(episode: int, local_dir: Path) -> tuple[dict, str]:
    """Fetch a single archive-only episode page. Returns (fields, page_hash)."""
    url = f"https://www.theincomparable.com/robot/{episode}/"
    text = http_get(url).text
    local_dir.mkdir(parents=True, exist_ok=True)
    page_path = local_dir / f"episode_{episode}.html"
    page_path.write_text(text)

    title = page_title(text, str(episode))

    audio_match = PAGE_AUDIO_RE.search(text)
    audio_url = audio_match.group(1) if audio_match else ""

    desc_match = PAGE_DESCRIPTION_RE.search(text)
    description = strip_html(desc_match.group(1)) if desc_match else ""

    date_match = PAGE_DATE_RE.search(text)
    released_at = None
    if date_match:
        with contextlib.suppress(ValueError):
            released_at = (
                datetime.strptime(date_match.group(1).strip(), "%B %d, %Y")
                .replace(tzinfo=UTC)
                .isoformat()
            )

    return {
        "canonical_url": url,
        "title": title,
        "audio_url": audio_url,
        "description": description,
        "released_at": released_at,
    }, file_hash(str(page_path))


def build_episodes(
    feed_items: list[dict],
    archive_episode_numbers: set[int],
    retrieved_at: str,
    local_dir: Path,
    fetch_page=fetch_archive_only_episode,
) -> tuple[list[Episode], dict[str, str]]:
    """Reconcile feed items with archive links into Episode records.

    Returns (episodes, episode_page_hashes for archive-only fetches).
    """
    episodes: list[Episode] = []
    seen_guids: set[str] = set()
    matched_numbers: set[int] = set()

    for item in feed_items:
        if item["guid"] and item["guid"] in seen_guids:
            continue  # dedupe by guid: keep first occurrence
        if item["guid"]:
            seen_guids.add(item["guid"])

        episode, label, review, blocked = resolve_episode_identity(item)
        if episode is not None:
            matched_numbers.add(episode)
        source = "both" if episode is not None and episode in archive_episode_numbers else "feed"

        episodes.append(
            Episode(
                schema_version=SCHEMA_VERSION,
                episode_id=item["guid"] or f"archive:{item['canonical_url']}",
                episode=episode,
                episode_label=label,
                title=item["title"],
                released_at=item["released_at"],
                description=item["description"],
                show_notes_url=item["canonical_url"],
                audio_url=item["audio_url"],
                duration_s=item["duration_s"],
                source=source,
                retrieved_at=item["retrieved_at"],
                review_queue=review,
                blocked=blocked,
            )
        )

    episode_page_hashes: dict[str, str] = {}
    for episode in sorted(archive_episode_numbers - matched_numbers):
        fields, page_hash = fetch_page(episode, local_dir)
        episode_page_hashes[str(episode)] = page_hash
        episodes.append(
            Episode(
                schema_version=SCHEMA_VERSION,
                episode_id=f"archive:{fields['canonical_url']}",
                episode=episode,
                episode_label=str(episode),
                title=fields["title"],
                released_at=fields["released_at"],
                description=fields["description"],
                show_notes_url=fields["canonical_url"],
                audio_url=fields["audio_url"],
                duration_s=None,
                source="archive",
                retrieved_at=retrieved_at,
                aliases=[fields["canonical_url"]],
            )
        )

    return episodes, episode_page_hashes


def apply_exclusions(episodes: list[Episode]) -> None:
    by_episode_id = {e["episode_id"]: e["reason"] for e in read_excluded()}
    for ep in episodes:
        if ep.episode_id in by_episode_id:
            ep.excluded = True
            ep.exclusion_reason = by_episode_id[ep.episode_id]


def read_episodes(path: Path = EPISODES_PATH) -> list[Episode]:
    return [Episode(**d) for d in read_jsonl(path)]


def cross_reference(
    episodes: list[Episode],
    stamp: str,
    scorecard_path: Path = SCORECARD_VERIFIED_PATH,
    adjudication_path: Path = ADJUDICATION_PATH,
) -> dict:
    """Flag scorecard_verified rows whose episode has no catalog entry.

    See PLAN.md Phase 1 step 4. Appends to the adjudication file,
    idempotently: skips rows already quarantined/excluded and never
    duplicates an existing entry for the same (source_row_id, reason).
    """
    catalog_numbers = {e.episode for e in episodes if e.episode is not None}
    rulings = list(read_jsonl(scorecard_path)) if scorecard_path.exists() else []

    ensure_adjudication_file(adjudication_path)
    existing = read_adjudication_entries(adjudication_path)
    existing_keys = {(e.source_row_id, e.reason) for e in existing}

    unmatched_rulings: list[dict] = []
    new_entries: list[dict] = []
    decided_at = datetime.now(UTC).isoformat()
    for ruling in rulings:
        episode = ruling.get("episode")
        if episode is None or episode in catalog_numbers:
            continue
        if ruling.get("review_status") in ("quarantined", "excluded"):
            continue
        unmatched_rulings.append(ruling)
        reason = f"scorecard episode {episode} has no catalog episode"
        for row_id in ruling.get("source_row_ids", []):
            key = (row_id, reason)
            if key in existing_keys:
                continue
            entry = {
                "schema_version": 1,
                "source_row_id": row_id,
                "field": "review_status",
                "old": ruling.get("review_status"),
                "new": "quarantined",
                "reason": reason,
                "evidence": f"catalog run {stamp}",
                "decided_by": "catalog-crosscheck",
                "decided_at": decided_at,
            }
            new_entries.append(entry)
            existing_keys.add(key)

    if new_entries:
        with adjudication_path.open("a") as f:
            for entry in new_entries:
                f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    return {"unmatched_rulings": unmatched_rulings, "new_entries": new_entries}


def _write_report(
    episodes: list[Episode],
    feed_count: int,
    archive_count: int,
    xref: dict,
    out_path: Path,
) -> None:
    by_source = Counter(e.source for e in episodes)

    numeric = sorted(e.episode for e in episodes if e.episode is not None)
    gaps: list[int] = []
    if numeric:
        present = set(numeric)
        gaps = [n for n in range(numeric[0], numeric[-1] + 1) if n not in present]

    review_entries = [e for e in episodes if e.review_queue]

    non_excluded_non_blocked = [e for e in episodes if not e.excluded and not e.blocked]
    total_duration = sum(e.duration_s or 0 for e in non_excluded_non_blocked)
    null_duration = sum(1 for e in non_excluded_non_blocked if e.duration_s is None)

    excluded = [e for e in episodes if e.excluded]

    lines = [
        "# Catalog report",
        "",
        "## Counts",
        f"- feed items: {feed_count}",
        f"- archive links: {archive_count}",
        f"- entries in both: {by_source['both']}",
        f"- feed-only entries: {by_source['feed']}",
        f"- archive-only entries: {by_source['archive']}",
        "",
        "## Episode number range",
        f"- range: {numeric[0]}-{numeric[-1]}" if numeric else "- range: (none)",
        f"- gaps: {gaps or 'none'}",
        "",
        "## Review queue",
    ]
    if review_entries:
        for e in review_entries:
            lines.append(
                f"- {e.episode_id} (episode {e.episode}, label {e.episode_label!r}, "
                f"blocked={e.blocked}): {'; '.join(e.review_queue)}"
            )
    else:
        lines.append("- none")

    lines += ["", "## Scorecard rows with no matching catalog episode"]
    if xref["unmatched_rulings"]:
        for r in xref["unmatched_rulings"]:
            lines.append(f"- {r.get('ruling_ref')}: episode {r.get('episode')} not in catalog")
    else:
        lines.append("- none")

    lines += ["", "## Excluded episodes"]
    if excluded:
        for e in excluded:
            lines.append(f"- episode {e.episode} ({e.episode_id}): {e.exclusion_reason}")
    else:
        lines.append("- none")

    lines += [
        "",
        "## Audio duration (non-excluded, non-blocked episodes)",
        f"- total: {total_duration} s ({total_duration / 3600:.2f} h)",
        f"- episodes with null duration: {null_duration}",
    ]

    out_path.write_text("\n".join(lines) + "\n")


def run(force: bool = False) -> dict:
    """Fetch feed + archive, reconcile into data/episodes.jsonl, cross-reference scorecard."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    local_dir = LOCAL_DIR / stamp
    local_dir.mkdir(parents=True, exist_ok=True)

    feed_text = fetch_feed()
    feed_path = local_dir / "feed.xml"
    feed_path.write_text(feed_text)
    feed_hash = file_hash(str(feed_path))

    archive_pages, archive_episode_numbers = fetch_archive_pages()
    archive_hashes = []
    for i, text in enumerate(archive_pages, 1):
        page_path = local_dir / f"archive_{i}.html"
        page_path.write_text(text)
        archive_hashes.append(file_hash(str(page_path)))

    retrieved_at = datetime.now(UTC).isoformat()
    feed_items = parse_feed_items(feed_text, retrieved_at)

    episodes, episode_page_hashes = build_episodes(
        feed_items, archive_episode_numbers, retrieved_at, local_dir
    )

    fp = fingerprint(
        feed_hash=feed_hash,
        archive_hashes=archive_hashes,
        episode_page_hashes=episode_page_hashes,
        excluded_hash=file_hash(str(EXCLUDED_PATH)),
        code_version=CODE_VERSION,
    )

    reused = False
    if (
        not force
        and FINGERPRINT_PATH.exists()
        and EPISODES_PATH.exists()
        and FINGERPRINT_PATH.read_text().strip() == fp
    ):
        episodes = read_episodes(EPISODES_PATH)
        reused = True
    else:
        apply_exclusions(episodes)
        episodes.sort(key=lambda e: episode_sort_key(e.episode, e.episode_label))
        write_lines_atomic(EPISODES_PATH, (e.model_dump_json() for e in episodes))
        FINGERPRINT_PATH.write_text(fp)

    xref = cross_reference(episodes, stamp)
    _write_report(episodes, len(feed_items), len(archive_episode_numbers), xref, REPORT_PATH)

    return {
        "episodes": len(episodes),
        "reused": reused,
        "new_adjudication_entries": len(xref["new_entries"]),
        "local_dir": str(local_dir),
    }
