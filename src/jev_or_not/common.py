"""Shared I/O, HTTP, and feed helpers used by every phase module.

Kept deliberately small: these are the pieces that were being re-derived in
``catalog``, ``download``, ``pilot``, and ``scorecard``. Phase modules import
from here rather than from each other.
"""

import json
import os
import re
import tempfile
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import IO

import httpx

FEED_URL = "https://feeds.theincomparable.com/robot"
USER_AGENT = "RobotOrNot-Evaluation/1.0 (+https://github.com/rlorenzo/jev-or-not)"
ITUNES_NS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
EXCLUDED_PATH = Path("data/gold/excluded_episodes.json")

PAGE_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_SITE_SUFFIX_RE = re.compile(r"\s*-\s*The Incomparable\s*$")


def http_get(url: str) -> httpx.Response:
    """GET with the project User-Agent, following redirects. Raises on 4xx/5xx."""
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    return resp


def page_title(html: str, default: str) -> str:
    """The ``<title>`` text with the shared site suffix stripped."""
    match = PAGE_TITLE_RE.search(html)
    if not match:
        return default
    return _SITE_SUFFIX_RE.sub("", match.group(1).strip()).strip()


def parse_duration(raw: str | None) -> int | None:
    """itunes:duration as seconds; accepts "SS", "MM:SS", and "HH:MM:SS"."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    try:
        # float() first: some feeds publish fractional seconds ("1234.5", "12:34.5").
        secs = 0.0
        for part in raw.split(":"):
            secs = secs * 60 + float(part)
    except ValueError:  # a malformed duration is missing metadata, not a fatal error
        return None
    return int(secs)


def episode_sort_key(episode: int | None, label: str) -> tuple:
    """Order episodes by number, with unnumbered (bonus) entries last."""
    return (episode is None, episode or 0, label)


def read_jsonl(path: Path) -> Iterator[dict]:
    """Yield each non-blank line of a JSONL file as a dict."""
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            yield json.loads(line)


def _replace_atomically(path: Path, write: Callable[[IO[str]], None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            write(f)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_lines_atomic(path: Path, lines: Iterable[str]) -> None:
    """Write newline-terminated lines via a temp file + rename."""

    def write(f: IO[str]) -> None:
        for line in lines:
            f.write(line + "\n")

    _replace_atomically(path, write)


def write_json_atomic(path: Path, data: dict) -> None:
    """Write one indented JSON document via a temp file + rename."""

    def write(f: IO[str]) -> None:
        json.dump(data, f, indent=2)
        f.write("\n")

    _replace_atomically(path, write)


def read_excluded() -> list[dict]:
    """The hand-maintained ``{episode, episode_id, reason}`` exclusion records."""
    return json.loads(EXCLUDED_PATH.read_text())["excluded"]
