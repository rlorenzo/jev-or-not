"""Fetch episode titles and select the frozen Phase 0 pilot episode list.

See PLAN.md Phase 0 steps 4-5 and docs/annotation_procedure.md. This module
only fetches cheap title/duration metadata for pilot *selection* — it is not
the Phase 1 episode catalog (``data/episodes.jsonl``, built later by
``catalog``).
"""

import json
import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from defusedxml import ElementTree as ET  # hardened parser for network XML (bandit B314)

from jev_or_not.common import (
    FEED_URL,
    ITUNES_NS,
    http_get,
    page_title,
    parse_duration,
    read_excluded,
    read_jsonl,
    write_json_atomic,
    write_lines_atomic,
)

ARCHIVE_EP0_URL = "https://www.theincomparable.com/robot/0/"

TITLES_PATH = Path("data/episode_titles.jsonl")
PILOT_PATH = Path("data/gold/pilot_episodes.json")
SCORECARD_VERIFIED_PATH = Path("data/gold/scorecard_verified.jsonl")

CANDIDATE_EPISODES = [0, 1, 6, 50, 100, 200, 297, 300, 345, 350]
PINNED_EPISODES = {0, 1, 6, 297}  # must match the pinned list spelled out in RULE_TEXT
SEED = 42
RULE_TEXT = (
    "Start from plan candidates [0,1,6,50,100,200,297,300,345,350]; substitute a missing "
    "episode with the nearest existing higher episode number; then require at least 2 of 10 "
    "to be non-robot-title candidates with no scorecard coverage, replacing the "
    "highest-numbered non-pinned candidates (pinned: 0,1,6,297) with episodes chosen by "
    "random.Random(42).sample over the qualifying pool until that holds."
)


def fetch_episode_titles() -> dict:
    """Fetch the feed (and episode 0's archive page if missing) and write TITLES_PATH.

    Returns a summary dict: rows written, items missing itunes:episode, and
    duplicate episode numbers found (see PLAN.md Phase 1 step 1 anomaly).
    """
    root = ET.fromstring(http_get(FEED_URL).text)
    items = root.findall("./channel/item")
    retrieved_at = datetime.now(UTC).isoformat()

    rows: list[dict] = []
    episode_counts: dict[int, int] = {}
    missing_episode = 0
    non_numeric_episode: list[str] = []
    for item in items:
        guid_text = item.findtext("guid")
        episode_id = guid_text.strip() if guid_text else None
        title = (item.findtext("title") or "").strip()
        ep_text = item.findtext(f"{ITUNES_NS}episode")
        episode: int | None = None
        if ep_text and ep_text.strip():
            try:
                episode = int(ep_text.strip())
            except ValueError:
                non_numeric_episode.append(f"{episode_id}: {ep_text.strip()!r}")
        if episode is None:
            missing_episode += 1
        else:
            episode_counts[episode] = episode_counts.get(episode, 0) + 1
        pubdate_raw = item.findtext("pubDate")
        pub_date = (
            parsedate_to_datetime(pubdate_raw).astimezone(UTC).isoformat() if pubdate_raw else None
        )
        duration_s = parse_duration(item.findtext(f"{ITUNES_NS}duration"))
        rows.append(
            {
                "episode_id": episode_id,
                "episode": episode,
                "title": title,
                "pub_date": pub_date,
                "duration_s": duration_s,
                "retrieved_at": retrieved_at,
                "source": "feed",
            }
        )

    duplicate_episodes = sorted(ep for ep, count in episode_counts.items() if count > 1)

    if not any(r["episode"] == 0 for r in rows):
        rows.append(
            {
                "episode_id": "theincomparable/robot/0",
                "episode": 0,
                "title": page_title(http_get(ARCHIVE_EP0_URL).text, "Episode 0"),
                "pub_date": None,
                "duration_s": None,
                "retrieved_at": retrieved_at,
                "source": "archive",
            }
        )

    rows.sort(key=lambda r: (r["episode"] is None, r["episode"]))
    write_lines_atomic(TITLES_PATH, (json.dumps(r, separators=(",", ":")) for r in rows))

    return {
        "rows": len(rows),
        "total_feed_items": len(items),
        "missing_itunes_episode": missing_episode,
        "non_numeric_itunes_episode": non_numeric_episode,
        "duplicate_episode_numbers": duplicate_episodes,
    }


def _is_non_robot_candidate(
    episode: int, by_episode: dict[int, dict], rulings_by_episode: dict
) -> bool:
    title = by_episode.get(episode, {}).get("title", "")
    return "robot" not in title.lower() and rulings_by_episode.get(episode, 0) == 0


def select_pilot(
    titles: list[dict],
    scorecard_rulings: list[dict],
    seed: int = SEED,
    excluded: frozenset[int] = frozenset(),
) -> dict:
    """Apply the frozen pilot-selection rule (see RULE_TEXT). No I/O, no network."""
    by_episode = {
        t["episode"]: t
        for t in titles
        if t.get("episode") is not None and t["episode"] not in excluded
    }

    rulings_by_episode: dict[int, int] = {}
    for r in scorecard_rulings:
        ep = r.get("episode")
        if ep is not None:
            rulings_by_episode[ep] = rulings_by_episode.get(ep, 0) + 1

    entries: list[dict[str, Any]] = []
    used: set[int] = set()
    for candidate in CANDIDATE_EPISODES:
        episode = candidate
        reason = "plan candidate"
        if episode not in by_episode or episode in used:
            higher = sorted(
                e
                for e in by_episode
                if e > episode and e not in used and e not in CANDIDATE_EPISODES
            )
            if not higher:
                raise ValueError(f"no substitute found for candidate episode {candidate}")
            episode = higher[0]
            why = "excluded" if candidate in excluded else "not found in titles"
            reason = f"substituted for {candidate}: episode {candidate} {why}"
        used.add(episode)
        entries.append({"episode": episode, "orig_candidate": candidate, "reason": reason})

    def qualifying_count() -> int:
        return sum(
            1
            for e in entries
            if _is_non_robot_candidate(e["episode"], by_episode, rulings_by_episode)
        )

    if qualifying_count() < 2:
        selected = {e["episode"] for e in entries}
        pool = sorted(
            ep
            for ep in by_episode
            if ep not in selected and _is_non_robot_candidate(ep, by_episode, rulings_by_episode)
        )
        need = 2 - qualifying_count()
        chosen = random.Random(seed).sample(pool, k=min(len(pool), need))

        replaceable = sorted(
            (e for e in entries if e["orig_candidate"] not in PINNED_EPISODES),
            key=lambda e: e["episode"],
            reverse=True,
        )
        for new_ep in chosen:
            if not replaceable:
                break
            victim = replaceable.pop(0)
            old_ep = victim["episode"]
            victim["episode"] = new_ep
            victim["reason"] = f"replaced {old_ep} for non-robot coverage"

    entries.sort(key=lambda e: e["episode"])

    episodes = []
    total_duration = 0
    for e in entries:
        ep = e["episode"]
        t = by_episode[ep]
        dur = t.get("duration_s") or 0
        total_duration += dur
        episodes.append(
            {
                "episode_id": t.get("episode_id"),
                "episode": ep,
                "title": t.get("title"),
                "duration_s": t.get("duration_s"),
                "scorecard_rulings": rulings_by_episode.get(ep, 0),
                "non_robot_candidate": _is_non_robot_candidate(ep, by_episode, rulings_by_episode),
                "reason": e["reason"],
            }
        )

    return {
        "pilot_version": "1.0",
        "seed": seed,
        "rule": RULE_TEXT,
        "episodes": episodes,
        "total_duration_s": total_duration,
    }


def run_selection(seed: int = SEED) -> dict:
    """Read TITLES_PATH + scorecard_verified.jsonl, select the pilot, freeze PILOT_PATH."""
    titles = list(read_jsonl(TITLES_PATH))
    rulings = list(read_jsonl(SCORECARD_VERIFIED_PATH))
    excluded = frozenset(e["episode"] for e in read_excluded())
    result = select_pilot(titles, rulings, seed=seed, excluded=excluded)
    result = {
        "pilot_version": result["pilot_version"],
        "seed": result["seed"],
        "frozen_at": datetime.now(UTC).isoformat(),
        "rule": result["rule"],
        "episodes": result["episodes"],
        "total_duration_s": result["total_duration_s"],
    }
    write_json_atomic(PILOT_PATH, result)
    return result


def pilot_episode_ids() -> set[str]:
    """The frozen pilot list's episode_ids, for phases that run pilot-first."""
    return {pe["episode_id"] for pe in json.loads(PILOT_PATH.read_text())["episodes"]}


def run(seed: int = SEED, force: bool = False) -> dict:
    """Freeze the pilot list once; a frozen list is never rewritten without --force."""
    if PILOT_PATH.exists() and not force:
        return {"fetch": None, "pilot": json.loads(PILOT_PATH.read_text()), "reused": True}
    fetch_summary = fetch_episode_titles()
    pilot = run_selection(seed=seed)
    return {"fetch": fetch_summary, "pilot": pilot, "reused": False}
