"""Phase 2: drive the transcription worker per episode (PLAN.md Phase 2 Output).

See PLAN.md lines 24-31 (worker subprocess contract) and 90-135 (default
stack / output). The worker runs as a subprocess in the separate
``transcription/`` uv project; no Python objects cross that boundary, only
the request/output JSON files. Uses the shared ledger (phase "transcribe",
item_id = "<episode_id>|<candidate>") for fingerprint-based reuse, the same
pattern as ``download.py``.
"""

import hashlib
import json
import os
import subprocess  # nosec B404 - only used below for the fixed uv/worker argv
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from jev_or_not.catalog import eligible_episodes, read_episodes, total_duration_s
from jev_or_not.common import (
    episode_sort_key,
    read_jsonl,
    write_json_atomic,
    write_lines_atomic,
)
from jev_or_not.fingerprint import fingerprint
from jev_or_not.ledger import claim, complete, ensure_task, fail, open_ledger, successes
from jev_or_not.models import AudioManifestEntry, Episode, TranscriptIndexEntry
from jev_or_not.pilot import pilot_episode_ids

SCHEMA_VERSION = 1  # matches transcription_worker.__main__.SCHEMA_VERSION
MANIFEST_PATH = Path("data/audio_manifest.jsonl")
SCORECARD_VERIFIED_PATH = Path("data/gold/scorecard_verified.jsonl")
REQUESTS_DIR = Path("local/transcripts/requests")
TRANSCRIPTS_DIR = Path("local/transcripts")
INDEX_PATH = Path("data/transcript_index.jsonl")
WORKER_DIR = Path("transcription/src/transcription_worker")
WORKER_LOCK = Path("transcription/uv.lock")
# The worker modules a transcription run actually executes; see worker_code_hash.
WORKER_SOURCES = ("__main__.py", "assign.py", "diarize.py", "engines.py", "ffmpeg.py")
ENV_PATH = Path(".env")

TIMEOUT_S = 20 * 60
WORKER_CMD_PREFIX = [
    "uv",
    "run",
    "--project",
    "transcription/",
    "python",
    "-m",
    "transcription_worker",
]

BASE_TERMS = ["Siracusa", "Snell", "Incomparable", "Roomba", "droid", "Daleks"]

# Candidate defaults (PLAN.md Phase 2 bake-off A/B). ``prompt_supported`` says
# whether the engine accepts an ``initial_prompt``; parakeet-mlx has no prompt
# API, so the worker would only warn and drop one. The prompt itself is
# per-episode (base terms + that episode's scorecard subjects), so it is built
# in _build_params rather than kept static here.
CANDIDATES: dict[str, dict] = {
    "A": {"model": "mlx-community/whisper-large-v3-mlx", "prompt_supported": True},
    "B": {"model": "mlx-community/parakeet-tdt-0.6b-v3", "prompt_supported": False},
}
DIARIZATION = {"pipeline": "pyannote/speaker-diarization-community-1"}


def worker_code_hash() -> str:
    """sha256 over the worker modules the transcription run executes, plus its
    lockfile.

    Any change to that code or to its pinned dependencies can change the
    worker's output, so it must invalidate cached transcripts. Deliberately a
    fixed list rather than a glob of ``WORKER_DIR``: sibling tooling like
    ``enroll.py`` and ``hardware.py`` lives in the same package but cannot
    affect a transcript, and hashing it would throw away every cached
    transcript over an unrelated edit.
    """
    h = hashlib.sha256()
    for name in WORKER_SOURCES:
        h.update((WORKER_DIR / name).read_bytes())
    h.update(WORKER_LOCK.read_bytes())
    return h.hexdigest()


def _term_list(episode_id: str, scorecard_rows: list[dict]) -> list[str]:
    subjects = [r["subject"] for r in scorecard_rows if r.get("episode_id") == episode_id]
    seen: list[str] = []
    for term in BASE_TERMS + subjects:
        if term not in seen:
            seen.append(term)
    return seen


def _build_params(candidate: str, term_list: list[str]) -> dict:
    spec = CANDIDATES[candidate]
    params = {"model": spec["model"]}
    if spec["prompt_supported"]:
        params["initial_prompt"] = ", ".join(term_list)
    return params


def build_request(
    episode: Episode, manifest_entry: AudioManifestEntry, candidate: str, scorecard_rows: list[dict]
) -> dict:
    return {
        "episode_id": episode.episode_id,
        "audio_path": manifest_entry.file_path,
        "audio_sha256": manifest_entry.sha256,
        "candidate": candidate,
        "device": "mps",
        "params": _build_params(candidate, _term_list(episode.episode_id, scorecard_rows)),
        "diarization": DIARIZATION,
        "schema_version": SCHEMA_VERSION,
    }


def _read_dotenv_var(name: str, path: Path = ENV_PATH) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    return None


def _worker_env() -> dict:
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)  # the parent's venv would make uv warn inside the subproject
    # pyannote's torchcodec needs Homebrew ffmpeg's dylibs at runtime
    # (transcription_worker/diarize.py); required outside its own venv.
    dyld = env.get("DYLD_LIBRARY_PATH")
    env["DYLD_LIBRARY_PATH"] = f"/opt/homebrew/lib:{dyld}" if dyld else "/opt/homebrew/lib"
    if "HF_TOKEN" not in env:
        token = _read_dotenv_var("HF_TOKEN")
        if token:
            env["HF_TOKEN"] = token
    return env


def transcribe_one(
    conn,
    episode: Episode,
    manifest_entry: AudioManifestEntry,
    candidate: str,
    scorecard_rows: list[dict],
    *,
    force: bool = False,
    run_nonce: str | None = None,
    worker_hash: str | None = None,
    requests_dir: Path = REQUESTS_DIR,
    transcripts_dir: Path = TRANSCRIPTS_DIR,
    timeout_s: int = TIMEOUT_S,
) -> dict:
    """Run the worker for one (episode, candidate), using the ledger for reuse."""
    request = build_request(episode, manifest_entry, candidate, scorecard_rows)
    whash = worker_hash if worker_hash is not None else worker_code_hash()

    fp_parts: dict[str, object] = {
        "audio_sha256": manifest_entry.sha256,
        "candidate": candidate,
        "params": request["params"],
        "diarization": DIARIZATION,
        "worker_code_hash": whash,
    }
    if force:
        fp_parts["nonce"] = run_nonce
    fp = fingerprint(**fp_parts)

    # Every result identifies its (episode, candidate) the same way; only the
    # status-specific keys differ.
    ident = {
        "episode_id": episode.episode_id,
        "episode_label": episode.episode_label,
        "candidate": candidate,
    }

    item_id = f"{episode.episode_id}|{candidate}"
    task_id = ensure_task(conn, "transcribe", item_id, fp)
    if not claim(conn, task_id):
        status = conn.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,)).fetchone()[
            0
        ]
        return {**ident, "status": "skipped", "reason": status}

    requests_dir.mkdir(parents=True, exist_ok=True)
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    request_path = requests_dir / f"{episode.episode_label}-{candidate}.json"
    write_json_atomic(request_path, request)

    tmp_output = transcripts_dir / f"{episode.episode_label}-{candidate}.json.tmp"
    final_output = transcripts_dir / f"{episode.episode_label}-{candidate}.json"
    cmd = [*WORKER_CMD_PREFIX, "--request", str(request_path), "--output", str(tmp_output)]

    def failed(reason: str, detail: str, **extra) -> dict:
        """Mark the task failed, drop the half-written output, describe it."""
        fail(conn, task_id, detail)
        tmp_output.unlink(missing_ok=True)
        return {**ident, "status": "failed", "reason": reason, **extra}

    t0 = time.time()
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv list, no shell, no untrusted input
            cmd, env=_worker_env(), capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired:
        return failed("timeout", f"worker timed out after {timeout_s}s")
    wall_s = time.time() - t0

    if proc.returncode != 0:
        return failed(
            "worker_exit",
            f"worker exited {proc.returncode}: {proc.stderr[-4000:]}",
            error=proc.stderr[-500:],
        )

    try:
        output = json.loads(tmp_output.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return failed("bad_output", f"could not parse worker output: {exc}")

    if (
        output.get("episode_id") != episode.episode_id
        or output.get("schema_version") != SCHEMA_VERSION
    ):
        return failed(
            "mismatch",
            f"output mismatch: episode_id={output.get('episode_id')!r} "
            f"schema_version={output.get('schema_version')!r}",
        )

    os.replace(tmp_output, final_output)
    complete(conn, task_id, str(final_output))

    duration_s = manifest_entry.measured_duration_s or manifest_entry.feed_duration_s or 0
    return {
        **ident,
        "status": "success",
        "transcript_path": str(final_output),
        "fingerprint": fp,
        "wall_s": wall_s,
        "duration_s": duration_s,
        "rtf": wall_s / duration_s if duration_s else None,
    }


def export_index(
    conn, episodes: list[Episode], manifest_by_id: dict[str, AudioManifestEntry]
) -> list[TranscriptIndexEntry]:
    """The newest successful transcript per (episode, candidate), sorted by
    episode then candidate."""
    ep_by_id = {e.episode_id: e for e in episodes}
    # A re-run (--force, or an invalidated worker_code_hash) leaves an older
    # success row for the same item_id behind; keeping both would put the same
    # episode/candidate in the index twice. Same rule as download.export_manifest.
    latest: dict[str, tuple[str, dict]] = {}
    for task in successes(conn, "transcribe"):
        if not Path(task["result_path"]).exists():
            continue  # ledger is a rebuildable cache; a missing file just drops that row
        prev = latest.get(task["item_id"])
        if prev is None or task["updated_at"] > prev[0]:
            latest[task["item_id"]] = (task["updated_at"], task)

    rows = []
    for _, task in latest.values():
        path = Path(task["result_path"])
        # One read serves both the parsed fields and the hash; transcripts run
        # to hundreds of KB each and this loop covers the whole catalog.
        raw = path.read_bytes()
        output = json.loads(raw)
        episode_id, _, candidate = task["item_id"].rpartition("|")
        episode = ep_by_id.get(episode_id)
        episode_label = episode.episode_label if episode else episode_id
        manifest_entry = manifest_by_id.get(episode_id)
        duration_s = (
            (manifest_entry.measured_duration_s or manifest_entry.feed_duration_s)
            if manifest_entry
            else None
        )
        runtime = output.get("runtime", {})
        runtime_total_s = runtime.get("total_s", 0.0)
        rows.append(
            TranscriptIndexEntry(
                schema_version=SCHEMA_VERSION,
                episode_id=episode_id,
                episode_label=episode_label,
                candidate=candidate,
                transcript_path=str(path),
                transcript_hash=hashlib.sha256(raw).hexdigest(),
                fingerprint=task["fingerprint"],
                engine=output.get("engine", {}).get("name", ""),
                model=output.get("model", {}).get("repo", ""),
                diarization_device=output.get("diarization", {}).get("device", ""),
                n_segments=len(output.get("segments", [])),
                n_clusters=len(output.get("clusters", [])),
                runtime_total_s=runtime_total_s,
                rtf=(runtime_total_s / duration_s) if duration_s else None,
                created_at=task["updated_at"],
            )
        )

    def sort_key(r: TranscriptIndexEntry) -> tuple:
        ep = ep_by_id.get(r.episode_id)
        return (*episode_sort_key(ep.episode if ep else None, r.episode_label), r.candidate)

    rows.sort(key=sort_key)
    write_lines_atomic(INDEX_PATH, (r.model_dump_json() for r in rows))
    return rows


def _select_episodes(
    episodes: list[Episode],
    manifest_by_id: dict[str, AudioManifestEntry],
    *,
    pilot_only: bool,
    all_: bool,
    episodes_arg: str | None,
) -> list[Episode]:
    def sort_key(e: Episode) -> tuple:
        return episode_sort_key(e.episode, e.episode_label)

    if episodes_arg:
        ep_by_number = {e.episode: e for e in episodes if e.episode is not None}
        # dict.fromkeys: de-dupe `--episodes 1,2,1` without reordering.
        numbers = list(dict.fromkeys(int(x) for x in episodes_arg.split(",") if x.strip()))
        selected = []
        for n in numbers:
            episode = ep_by_number.get(n)
            if episode is None:
                raise ValueError(f"no episode numbered {n} in data/episodes.jsonl")
            if episode.episode_id not in manifest_by_id:
                raise ValueError(f"episode {n} has no successful audio_manifest.jsonl row")
            selected.append(episode)
        return sorted(selected, key=sort_key)

    candidates = [e for e in eligible_episodes(episodes) if e.episode_id in manifest_by_id]
    if all_ or not pilot_only:  # --no-pilot-only is a synonym for --all
        return sorted(candidates, key=sort_key)

    pilot_ids = pilot_episode_ids()
    return sorted((e for e in candidates if e.episode_id in pilot_ids), key=sort_key)


def run(
    candidate: str,
    *,
    pilot_only: bool = True,
    all_: bool = False,
    episodes_arg: str | None = None,
    force: bool = False,
    on_result: Callable[[dict], None] | None = None,
) -> dict:
    """Transcribe the selected episodes and rebuild the transcript index.

    ``on_result`` is called with each episode's result dict as it lands, so a
    CLI can report progress on a run that takes hours. Rendering stays with
    the caller; this module writes nothing to stdout.
    """
    if candidate not in CANDIDATES:
        raise ValueError(f"candidate must be one of {sorted(CANDIDATES)}: got {candidate!r}")

    episodes = read_episodes()
    manifest_by_id = {
        m.episode_id: m
        for m in (AudioManifestEntry(**row) for row in read_jsonl(MANIFEST_PATH))
        if m.status == "success"
    }
    scorecard_rows = list(read_jsonl(SCORECARD_VERIFIED_PATH))
    selected = _select_episodes(
        episodes, manifest_by_id, pilot_only=pilot_only, all_=all_, episodes_arg=episodes_arg
    )

    conn = open_ledger()
    run_nonce = uuid.uuid4().hex if force else None
    whash = worker_code_hash()
    results = []
    try:
        for episode in selected:
            result = transcribe_one(
                conn,
                episode,
                manifest_by_id[episode.episode_id],
                candidate,
                scorecard_rows,
                force=force,
                run_nonce=run_nonce,
                worker_hash=whash,
            )
            results.append(result)
            if on_result is not None:
                on_result(result)
        index_rows = export_index(conn, episodes, manifest_by_id)
    finally:
        conn.close()

    succeeded = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] == "failed"]
    skipped = [r for r in results if r["status"] == "skipped"]

    total_wall_s = sum(r["wall_s"] for r in succeeded)
    total_audio_s = sum(r["duration_s"] for r in succeeded)
    overall_rtf = total_wall_s / total_audio_s if total_audio_s else None
    catalog_total_s = total_duration_s(episodes)
    projected_full_s = overall_rtf * catalog_total_s if overall_rtf is not None else None

    return {
        "episodes": len(selected),
        "success": len(succeeded),
        "failed": len(failed),
        "skipped": len(skipped),
        "failures": failed,
        "results": results,
        "index_path": str(INDEX_PATH),
        "index_rows": len(index_rows),
        "total_wall_s": total_wall_s,
        "total_audio_s": total_audio_s,
        "overall_rtf": overall_rtf,
        "catalog_total_s": catalog_total_s,
        "projected_full_s": projected_full_s,
    }
