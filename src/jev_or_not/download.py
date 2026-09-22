"""Phase 1 step 5: download episode audio to local/audio/ (gitignored).

See PLAN.md Phase 1 step 5. Sequential and polite: one request at a time, a
short pause between episodes, exponential-backoff retries, and HTTP Range
resume for partial files. Enclosure URLs are Podtrac tracking redirects, so
redirects are followed explicitly and the resolved URL is recorded. An HTML
body where audio was expected is a failure, not a download.

Uses the shared ledger (phase "download", item_id = episode_id) for
resumability: a second run skips already-succeeded episodes without
re-requesting. Manifest rows carry data the ledger's ``tasks`` table has no
column for (bytes, sha256, resolved URL, measured duration, ...), so each
successful download also writes a small JSON sidecar next to the audio file;
``export_manifest`` reads those sidecars back out in ledger order.
"""

import json
import os
import random
import re
import sqlite3
import subprocess  # nosec B404 - only used below for the fixed afinfo call
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

from jev_or_not.catalog import EPISODES_PATH
from jev_or_not.fingerprint import file_hash, fingerprint
from jev_or_not.ledger import claim, complete, ensure_task, fail, open_ledger, successes
from jev_or_not.models import AudioManifestEntry, Episode
from jev_or_not.pilot import PILOT_PATH, USER_AGENT

CODE_VERSION = "download-v1"
SCHEMA_VERSION = 1
AUDIO_DIR = Path("local/audio")
MANIFEST_PATH = Path("data/audio_manifest.jsonl")
MAX_ATTEMPTS = 3
BACKOFF_S = 1.0
POLITE_PAUSE_RANGE_S = (1.0, 2.0)
DURATION_MISMATCH_RATIO = 0.05
_AFINFO_DURATION_RE = re.compile(r"estimated duration:\s*([0-9.]+)", re.IGNORECASE)


class HTMLBodyError(Exception):
    """Raised when the response body looks like an HTML page, not audio."""


def episode_audio_path(episode: Episode, audio_dir: Path = AUDIO_DIR) -> Path:
    return audio_dir / f"{episode.episode_label}.mp3"


def _meta_path(audio_path: Path) -> Path:
    return audio_path.parent / f"{audio_path.stem}.meta.json"


def measure_duration_s(path: Path) -> float | None:
    """Parse afinfo's "estimated duration" (macOS built-in, no new dependency).

    Returns None (and the caller should say so) if afinfo is unavailable or
    the output can't be parsed, e.g. on non-macOS dev machines.
    """
    try:
        # afinfo is a fixed macOS built-in; args are a list (no shell=True).
        proc = subprocess.run(  # nosec - fixed argv list, no shell, no untrusted input
            ["afinfo", str(path)], capture_output=True, text=True, timeout=30, check=False
        )
    except OSError:  # includes FileNotFoundError if afinfo isn't installed
        return None
    if proc.returncode != 0:
        return None
    m = _AFINFO_DURATION_RE.search(proc.stdout)
    return float(m.group(1)) if m else None


def _fetch_to_file(client: httpx.Client, url: str, path: Path) -> tuple[str, str]:
    """GET url into path, resuming via Range if path already has bytes.

    Returns (resolved_url, content_type). Raises HTMLBodyError if the body
    looks like an HTML page rather than audio.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.stat().st_size if path.exists() else 0
    headers = {"Range": f"bytes={existing}-"} if existing else {}
    mode = "ab" if existing else "wb"

    with client.stream("GET", url, headers=headers) as resp:
        if resp.status_code == 416:
            # Server says our existing bytes already cover the full range:
            # treat the file on disk as complete (ponytail: trusts a prior
            # partial download rather than re-verifying with a fresh GET).
            return str(resp.url), resp.headers.get("content-type", "")
        if existing and resp.status_code == 200:
            mode = "wb"  # server ignored our Range header; restart clean
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        checked_html = mode != "wb"  # only the first bytes of a fresh file are meaningful
        with open(path, mode) as f:
            for chunk in resp.iter_bytes():
                if not checked_html:
                    probe = chunk.lstrip()[:20]
                    is_html_type = content_type.split(";")[0].strip().lower() == "text/html"
                    if is_html_type or probe.startswith(b"<"):
                        raise HTMLBodyError(
                            f"HTML body returned for {url} (content-type={content_type!r})"
                        )
                    checked_html = True
                f.write(chunk)
        resolved_url = str(resp.url)
    return resolved_url, content_type


def download_one(
    client: httpx.Client,
    conn: sqlite3.Connection,
    episode: Episode,
    *,
    force: bool = False,
    run_nonce: str | None = None,
    audio_dir: Path = AUDIO_DIR,
    max_attempts: int = MAX_ATTEMPTS,
    backoff_s: float = BACKOFF_S,
) -> dict:
    """Download one episode's audio, using the ledger for claim/resume/skip."""
    path = episode_audio_path(episode, audio_dir)
    meta_path = _meta_path(path)

    fp_parts: dict[str, object] = {"audio_url": episode.audio_url, "code_version": CODE_VERSION}
    if force:
        fp_parts["nonce"] = run_nonce
    fp = fingerprint(**fp_parts)

    task_id = ensure_task(conn, "download", episode.episode_id, fp)
    if not claim(conn, task_id):
        status = conn.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,)).fetchone()[
            0
        ]
        return {
            "status": "skipped",
            "reason": status,
            "episode_id": episode.episode_id,
            "episode_label": episode.episode_label,
        }

    error_detail = None
    resolved_url = content_type = None
    for attempt in range(1, max_attempts + 1):
        try:
            resolved_url, content_type = _fetch_to_file(client, episode.audio_url, path)
            error_detail = None
            break
        except HTMLBodyError as exc:
            path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            fail(conn, task_id, str(exc))
            return {
                "status": "failed",
                "reason": "html_body",
                "error": str(exc),
                "episode_id": episode.episode_id,
                "episode_label": episode.episode_label,
            }
        except httpx.HTTPError as exc:
            error_detail = f"{type(exc).__name__}: {exc}"
            if attempt < max_attempts:
                time.sleep(backoff_s * (2 ** (attempt - 1)))

    if error_detail is not None:
        fail(conn, task_id, error_detail)
        return {
            "status": "failed",
            "reason": "request_error",
            "error": error_detail,
            "episode_id": episode.episode_id,
            "episode_label": episode.episode_label,
        }

    bytes_ = path.stat().st_size
    measured = measure_duration_s(path)
    feed_duration = episode.duration_s
    mismatch = (
        measured is not None
        and feed_duration is not None
        and feed_duration > 0
        and abs(measured - feed_duration) / feed_duration > DURATION_MISMATCH_RATIO
    )
    meta = {
        "schema_version": SCHEMA_VERSION,
        "episode_id": episode.episode_id,
        "episode_label": episode.episode_label,
        "audio_url": episode.audio_url,
        "resolved_url": resolved_url,
        "file_path": str(path),
        "bytes": bytes_,
        "sha256": file_hash(str(path)),
        "content_type": content_type,
        "measured_duration_s": measured,
        "feed_duration_s": feed_duration,
        "duration_mismatch": mismatch,
        "downloaded_at": datetime.now(UTC).isoformat(),
        "status": "success",
    }
    meta_path.write_text(json.dumps(meta, separators=(",", ":")))
    complete(conn, task_id, str(path))
    return {"status": "success", **meta}


def _write_jsonl_atomic(path: Path, rows: list[AudioManifestEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            for row in rows:
                f.write(row.model_dump_json() + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def export_manifest(conn: sqlite3.Connection, episodes: list[Episode]) -> list[AudioManifestEntry]:
    """Export the newest successful download per episode, sorted by episode number."""
    ep_by_id = {e.episode_id: e for e in episodes}
    latest: dict[str, tuple[str, dict]] = {}
    for task in successes(conn, "download"):
        meta_path = _meta_path(Path(task["result_path"]))
        if not meta_path.exists():
            continue  # ledger is a rebuildable cache; a missing sidecar just drops that row
        prev = latest.get(task["item_id"])
        if prev is None or task["updated_at"] > prev[0]:
            latest[task["item_id"]] = (task["updated_at"], json.loads(meta_path.read_text()))

    def sort_key(meta: dict) -> tuple:
        ep = ep_by_id.get(meta["episode_id"])
        return (
            ep is None or ep.episode is None,
            (ep.episode if ep else None) or 0,
            meta["episode_label"],
        )

    metas = sorted((m for _, m in latest.values()), key=sort_key)
    rows = [AudioManifestEntry(**m) for m in metas]
    _write_jsonl_atomic(MANIFEST_PATH, rows)
    return rows


def _load_episodes(path: Path = EPISODES_PATH) -> list[Episode]:
    episodes = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            episodes.append(Episode(**json.loads(line)))
    return episodes


def _select_episodes(episodes: list[Episode], pilot_only: bool) -> list[Episode]:
    """Non-excluded, non-blocked episodes; pilot episodes first (or only)."""
    candidates = [e for e in episodes if not e.excluded and not e.blocked]
    pilot_ids = {pe["episode_id"] for pe in json.loads(PILOT_PATH.read_text())["episodes"]}

    def sort_key(e: Episode) -> tuple:
        return (e.episode is None, e.episode or 0, e.episode_label)

    if pilot_only:
        selected = [e for e in candidates if e.episode_id in pilot_ids]
        return sorted(selected, key=sort_key)

    pilot_first = sorted((e for e in candidates if e.episode_id in pilot_ids), key=sort_key)
    rest = sorted((e for e in candidates if e.episode_id not in pilot_ids), key=sort_key)
    return pilot_first + rest


def run(pilot_only: bool = False, force: bool = False) -> dict:
    episodes = _load_episodes()
    selected = _select_episodes(episodes, pilot_only)

    conn = open_ledger()
    client = httpx.Client(
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(30.0, read=120.0),
    )
    run_nonce = uuid.uuid4().hex if force else None
    results = []
    try:
        for i, episode in enumerate(selected):
            result = download_one(client, conn, episode, force=force, run_nonce=run_nonce)
            results.append(result)
            if result["status"] != "skipped" and i < len(selected) - 1:
                time.sleep(random.uniform(*POLITE_PAUSE_RANGE_S))
        manifest_rows = export_manifest(conn, episodes)
    finally:
        client.close()
        conn.close()

    downloaded = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] == "failed"]
    skipped = [r for r in results if r["status"] == "skipped"]
    mismatches = [r.episode_label for r in manifest_rows if r.duration_mismatch]

    return {
        "candidates": len(selected),
        "downloaded": len(downloaded),
        "skipped": len(skipped),
        "failed": len(failed),
        "failures": failed,
        "bytes_downloaded": sum(r["bytes"] for r in downloaded),
        "manifest_path": str(MANIFEST_PATH),
        "manifest_rows": len(manifest_rows),
        "mismatches": mismatches,
        "results": results,
    }
