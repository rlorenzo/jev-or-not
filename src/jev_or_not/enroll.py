"""Phase 2 speaker enrollment application (PLAN.md Phase 2 Output items 2-3).

Runs ``transcription_worker.enroll_apply`` as a subprocess per transcript,
the same env-isolation pattern ``jev_or_not.transcribe`` uses for the worker
itself: no Python objects or imports cross the environment boundary, only
the CLI args and the transcript/output JSON files. Uses the shared ledger
(phase "enroll", item_id = "<episode_id>|<candidate>") for fingerprint-based
reuse.
"""

import hashlib
import json
import os
import subprocess  # nosec B404 - only used below for the fixed uv/worker argv
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from jev_or_not.catalog import read_episodes
from jev_or_not.common import (
    episode_sort_key,
    parse_episode_numbers,
    read_jsonl,
    tally,
    write_lines_atomic,
)
from jev_or_not.fingerprint import fingerprint
from jev_or_not.ledger import (
    claim,
    complete,
    ensure_task,
    fail,
    latest_successes,
    open_ledger,
    task_status,
)
from jev_or_not.models import EnrolledIndexEntry
from jev_or_not.transcribe import WORKER_SOURCES, _worker_env, worker_code_hash

INDEX_PATH = Path("data/transcript_index.jsonl")
MANIFEST_PATH = Path("data/audio_manifest.jsonl")
ENROLLED_INDEX_PATH = Path("data/enrolled_index.jsonl")
ENROLLED_DIR = Path("local/transcripts/enrolled")
ENROLLMENT_PATH = Path("data/enrollment.json")
JOHN_WAV = Path("local/enroll/john.wav")
JASON_WAV = Path("local/enroll/jason.wav")
HOST_EMBEDDINGS_PATH = Path("data/enrollment_embeddings.json")
# enroll_apply runs the worker's enroll.py (host assignment, embedding) on top
# of the transcription modules it imports; a change to any of them, or to the
# worker lockfile, must invalidate enrolled outputs.
APPLY_SOURCES = (*WORKER_SOURCES, "enroll.py", "enroll_apply.py")

TIMEOUT_S = 10 * 60
WORKER_CMD_PREFIX = [
    "uv",
    "run",
    "--project",
    "transcription/",
    "python",
    "-m",
    "transcription_worker.enroll_apply",
]

DEFAULT_FLOOR = 0.6
DEFAULT_MARGIN = 0.15


def enrollment_hash(
    john_wav: Path = JOHN_WAV,
    jason_wav: Path = JASON_WAV,
    enrollment_path: Path = ENROLLMENT_PATH,
    host_embeddings_path: Path = HOST_EMBEDDINGS_PATH,
) -> str:
    """sha256 over john.wav + jason.wav + enrollment.json bytes, in that
    order. Must match ``transcription_worker.enroll_apply.enrollment_hash``
    exactly -- the two sides never share code across the environment
    boundary.

    Falls back to the cached ``enrollment_hash`` field in
    ``data/enrollment_embeddings.json`` when the wav clips aren't on this
    machine (they're gitignored, local-only audio derivatives) -- the same
    fallback ``enroll_apply._get_host_embeddings`` uses on the worker side.
    """
    if john_wav.exists() and jason_wav.exists():
        h = hashlib.sha256()
        for p in (john_wav, jason_wav, enrollment_path):
            h.update(p.read_bytes())
        return h.hexdigest()
    if host_embeddings_path.exists():
        return json.loads(host_embeddings_path.read_text())["enrollment_hash"]
    raise FileNotFoundError(
        f"no host clips at {john_wav}/{jason_wav} and no cached {host_embeddings_path}"
    )


def _thresholds_status(enrollment_path: Path = ENROLLMENT_PATH) -> str:
    try:
        data = json.loads(enrollment_path.read_text())
    except OSError, json.JSONDecodeError:
        return "provisional"
    return (data.get("thresholds") or {}).get("status", "provisional")


def apply_one(
    conn,
    index_row: dict,
    manifest_row: dict,
    floor: float,
    margin: float,
    ehash: str,
    chash: str,
    *,
    force: bool,
    run_nonce: str | None,
    apply_dir: Path = ENROLLED_DIR,
    enrollment_path: Path = ENROLLMENT_PATH,
    host_embeddings_path: Path = HOST_EMBEDDINGS_PATH,
    timeout_s: int = TIMEOUT_S,
) -> dict:
    """Apply enrollment to one (episode, candidate) transcript, using the
    ledger for reuse."""
    episode_id = index_row["episode_id"]
    episode_label = index_row["episode_label"]
    candidate = index_row["candidate"]

    fp_parts: dict[str, object] = {
        "transcript_hash": index_row["transcript_hash"],
        "enrollment_hash": ehash,
        "floor": floor,
        "margin": margin,
        "code_hash": chash,
    }
    if force:
        fp_parts["nonce"] = run_nonce
    fp = fingerprint(**fp_parts)

    ident = {"episode_id": episode_id, "episode_label": episode_label, "candidate": candidate}
    item_id = f"{episode_id}|{candidate}"
    task_id = ensure_task(conn, "enroll", item_id, fp)
    if not claim(conn, task_id):
        return {**ident, "status": "skipped", "reason": task_status(conn, task_id)}

    apply_dir.mkdir(parents=True, exist_ok=True)
    tmp_output = apply_dir / f"{episode_label}-{candidate}.json.tmp"
    final_output = apply_dir / f"{episode_label}-{candidate}.json"
    cmd = [
        *WORKER_CMD_PREFIX,
        "--transcript",
        index_row["transcript_path"],
        "--audio",
        manifest_row["file_path"],
        "--enrollment",
        str(enrollment_path),
        "--host-embeddings",
        str(host_embeddings_path),
        "--floor",
        str(floor),
        "--margin",
        str(margin),
        "--output",
        str(tmp_output),
    ]

    def failed(reason: str, detail: str, **extra) -> dict:
        fail(conn, task_id, detail)
        tmp_output.unlink(missing_ok=True)
        return {**ident, "status": "failed", "reason": reason, **extra}

    t0 = time.time()
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv list, no shell, no untrusted input
            cmd, env=_worker_env(), capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired:
        return failed("timeout", f"enroll_apply timed out after {timeout_s}s")
    wall_s = time.time() - t0

    if proc.returncode != 0:
        return failed(
            "worker_exit",
            f"enroll_apply exited {proc.returncode}: {proc.stderr[-4000:]}",
            error=proc.stderr[-500:],
        )

    try:
        output = json.loads(tmp_output.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return failed("bad_output", f"could not parse enroll_apply output: {exc}")

    if output.get("episode_id") != episode_id:
        return failed(
            "mismatch", f"output episode_id={output.get('episode_id')!r} expected {episode_id!r}"
        )

    os.replace(tmp_output, final_output)
    complete(conn, task_id, str(final_output))
    return {
        **ident,
        "status": "success",
        "enrolled_path": str(final_output),
        "fingerprint": fp,
        "wall_s": wall_s,
    }


def export_index(
    conn, index_rows: list[dict], number_by_id: dict[str, int | None] | None = None
) -> list[EnrolledIndexEntry]:
    """The newest successful enrollment application per (episode, candidate).

    ``index_rows`` is the full ``data/transcript_index.jsonl`` content, used
    to look up the episode_label and transcript_hash a ledger task's item_id
    alone doesn't carry. ``number_by_id`` (episode_id -> catalog episode
    number) orders rows like every other phase; unknown ids sort last.
    """
    number_by_id = number_by_id or {}
    by_key = {(r["episode_id"], r["candidate"]): r for r in index_rows}
    status = _thresholds_status()

    rows = []
    for task in latest_successes(conn, "enroll"):
        path = Path(task["result_path"])
        raw = path.read_bytes()
        output = json.loads(raw)
        episode_id, _, candidate = task["item_id"].rpartition("|")
        idx_row = by_key.get((episode_id, candidate), {})

        enr = output["enrollment"]
        speech_by_cluster = {c["id"]: c["total_speech_s"] for c in output.get("clusters", [])}
        cluster_summaries = []
        unknown_speech_s = 0.0
        for cluster_id, info in enr["clusters"].items():
            speech_s = speech_by_cluster.get(cluster_id, 0.0)
            cluster_summaries.append(
                {
                    "id": cluster_id,
                    "label": info["label"],
                    "similarity": info["similarity"],
                    "margin": info["margin"],
                    "speech_s": speech_s,
                }
            )
            if info["label"] == "UNKNOWN":
                unknown_speech_s += speech_s
        cluster_summaries.sort(key=lambda c: c["id"])

        rows.append(
            EnrolledIndexEntry(
                schema_version=1,
                episode_id=episode_id,
                episode_label=idx_row.get("episode_label", episode_id),
                candidate=candidate,
                enrolled_path=str(path),
                enrolled_hash=hashlib.sha256(raw).hexdigest(),
                transcript_hash=idx_row.get("transcript_hash", ""),
                fingerprint=task["fingerprint"],
                floor=enr["floor"],
                margin=enr["margin"],
                thresholds_status=status,
                clusters=cluster_summaries,
                unknown_speech_s=round(unknown_speech_s, 3),
                created_at=task["updated_at"],
            )
        )

    rows.sort(
        key=lambda r: (
            *episode_sort_key(number_by_id.get(r.episode_id), r.episode_label),
            r.candidate,
        )
    )
    write_lines_atomic(ENROLLED_INDEX_PATH, (r.model_dump_json() for r in rows))
    return rows


def run(
    candidate: str,
    *,
    episodes_arg: str | None = None,
    floor: float = DEFAULT_FLOOR,
    margin: float = DEFAULT_MARGIN,
    force: bool = False,
    on_result: Callable[[dict], None] | None = None,
) -> dict:
    """Apply enrollment to every present transcript for ``candidate`` and
    rebuild the enrolled index. Needs local/enroll/{john,jason}.wav OR a
    cached data/enrollment_embeddings.json (portable to a machine without
    the gitignored local/ audio) -- enrollment_hash() raises clearly if
    neither is present."""
    ehash = enrollment_hash()
    chash = worker_code_hash(APPLY_SOURCES)

    index_rows = list(read_jsonl(INDEX_PATH))
    manifest_by_id = {m["episode_id"]: m for m in read_jsonl(MANIFEST_PATH)}
    number_by_id = {e.episode_id: e.episode for e in read_episodes()}

    selected = [
        r for r in index_rows if r["candidate"] == candidate and r["episode_id"] in manifest_by_id
    ]
    if episodes_arg:
        wanted = set(parse_episode_numbers(episodes_arg))
        selected = [r for r in selected if number_by_id.get(r["episode_id"]) in wanted]

    def sort_key(r: dict) -> tuple:
        return episode_sort_key(number_by_id.get(r["episode_id"]), r["episode_label"])

    selected.sort(key=sort_key)

    conn = open_ledger()
    run_nonce = uuid.uuid4().hex if force else None
    results = []
    try:
        for row in selected:
            result = apply_one(
                conn,
                row,
                manifest_by_id[row["episode_id"]],
                floor,
                margin,
                ehash,
                chash,
                force=force,
                run_nonce=run_nonce,
            )
            results.append(result)
            if on_result is not None:
                on_result(result)
        index_out = export_index(conn, index_rows, number_by_id)
    finally:
        conn.close()

    return {
        "transcripts": len(selected),
        **tally(results),
        "results": results,
        "index_path": str(ENROLLED_INDEX_PATH),
        "index_rows": len(index_out),
        "total_unknown_speech_s": sum(r.unknown_speech_s for r in index_out),
    }
