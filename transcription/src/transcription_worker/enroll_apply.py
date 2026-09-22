"""Applies confirmed host enrollment to one transcript's diarization clusters
(PLAN.md Phase 2 Output items 2-3): labels every cluster JOHN/JASON/GUEST/
UNKNOWN and stamps a `speaker` field on every segment and word.

Invoked as a subprocess by the main project's ``jev_or_not.enroll``, the same
env-isolation pattern as ``transcription_worker.__main__``:

    python -m transcription_worker.enroll_apply --transcript X --audio Y \
        --enrollment data/enrollment.json --floor F --margin M --output Z

No Python objects cross the environment boundary -- everything comes from
CLI args and files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime

import numpy as np

from transcription_worker import ffmpeg
from transcription_worker.__main__ import write_atomic
from transcription_worker.enroll import (
    PIPELINE_NAME,
    _load_pipeline,
    _read_hf_token,
    assign_hosts,
    embed_clip,
    embed_cluster,
)

HOST_LABELS = {"john": "JOHN", "jason": "JASON"}
HOST_EMBEDDINGS_SCHEMA_VERSION = 1
DEFAULT_HOST_EMBEDDINGS_PATH = "data/enrollment_embeddings.json"


def enrollment_hash(john_wav: str, jason_wav: str, enrollment_path: str) -> str:
    """sha256 over john.wav + jason.wav + enrollment.json bytes, in that
    order. Must match ``jev_or_not.enroll.enrollment_hash`` exactly -- the
    two sides never share code across the environment boundary (main
    project vs. this subproject)."""
    h = hashlib.sha256()
    for path in (john_wav, jason_wav, enrollment_path):
        with open(path, "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def _confirmed_host_wavs(enrollment: dict) -> tuple[str, str]:
    wavs: dict[str, str] = {}
    for cand in enrollment.get("candidates", []):
        host = cand.get("confirmed_host")
        if host in HOST_LABELS:
            wavs[host] = cand["confirmed_wav_path"]
    if "john" not in wavs or "jason" not in wavs:
        raise RuntimeError("enrollment file has no confirmed john/jason host clips")
    return wavs["john"], wavs["jason"]


def write_host_embeddings(
    path: str, ehash: str, embedding_model: str, host_embeddings: dict[str, np.ndarray]
) -> None:
    """Caches the two host embedding vectors so a machine without
    local/enroll/{john,jason}.wav (PLAN.md's audio is gitignored, local-only)
    can still run enrollment: see ``load_host_embeddings``."""
    dims = {len(v) for v in host_embeddings.values()}
    if len(dims) != 1:
        raise ValueError(f"host embeddings have inconsistent dimensions: {dims}")
    data = {
        "schema_version": HOST_EMBEDDINGS_SCHEMA_VERSION,
        "enrollment_hash": ehash,
        "embedding_model": embedding_model,
        "dimension": dims.pop(),
        "hosts": {host: emb.tolist() for host, emb in host_embeddings.items()},
        "created_at": datetime.now(UTC).isoformat(),
    }
    write_atomic(path, data)


def load_host_embeddings(path: str) -> tuple[dict[str, np.ndarray], str]:
    """The round-trip counterpart of ``write_host_embeddings``: returns
    (host_embeddings, enrollment_hash)."""
    with open(path) as f:
        data = json.load(f)
    hosts = {host: np.array(vec, dtype=np.float64) for host, vec in data["hosts"].items()}
    return hosts, data["enrollment_hash"]


def _get_host_embeddings(
    pipeline, john_wav: str, jason_wav: str, enrollment_path: str, host_embeddings_path: str | None
) -> tuple[dict[str, np.ndarray], str]:
    """Host vectors from the cached ``--host-embeddings`` file when its
    enrollment_hash matches the wav clips (or the clips are absent, e.g. on a
    machine without the gitignored local/ audio); otherwise embeds live from
    the clips (the source of truth) and refreshes the cache. Re-embedding only
    on a hash change keeps each per-transcript call from redoing two forward
    passes and rewriting the tracked cache file."""
    wavs_present = os.path.exists(john_wav) and os.path.exists(jason_wav)
    if not wavs_present:
        if not host_embeddings_path or not os.path.exists(host_embeddings_path):
            raise RuntimeError(
                f"no host wav clips at {john_wav!r}/{jason_wav!r} and no usable "
                f"--host-embeddings file at {host_embeddings_path!r}"
            )
        return load_host_embeddings(host_embeddings_path)

    ehash = enrollment_hash(john_wav, jason_wav, enrollment_path)
    if host_embeddings_path and os.path.exists(host_embeddings_path):
        cached, stored_ehash = load_host_embeddings(host_embeddings_path)
        if stored_ehash == ehash:
            return cached, ehash
        print(
            f"warning: {host_embeddings_path} is stale (enrollment_hash "
            f"{stored_ehash[:12]}... != live {ehash[:12]}...); re-embedding from wavs",
            file=sys.stderr,
        )
    host_embeddings = {
        "john": embed_clip(pipeline, john_wav),
        "jason": embed_clip(pipeline, jason_wav),
    }
    if host_embeddings_path:
        embedding_model = f"{PIPELINE_NAME}:{type(pipeline._embedding).__name__}"
        write_host_embeddings(host_embeddings_path, ehash, embedding_model, host_embeddings)
    return host_embeddings, ehash


def _cluster_turns(segments: list[dict]) -> dict[str, list[tuple[float, float]]]:
    """Turn (start, end) pairs per cluster, from every segment -- a null
    cluster (never assigned by diarization) has no host to embed against."""
    by_cluster: dict[str, list[tuple[float, float]]] = {}
    for seg in segments:
        cluster = seg.get("cluster")
        if cluster is None:
            continue
        by_cluster.setdefault(cluster, []).append((seg["start"], seg["end"]))
    return by_cluster


def apply_labels(transcript: dict, cluster_results: dict[str, dict]) -> dict:
    """Pure: stamps a `speaker` field on every segment and word from its
    cluster's assigned label; a null cluster, or one with no result (e.g. a
    word-level cluster no segment carries), becomes UNKNOWN. Returns a new
    dict -- does not mutate `transcript`."""

    def label_for(cluster: str | None) -> str:
        result = cluster_results.get(cluster) if cluster is not None else None
        return result["label"] if result else "UNKNOWN"

    out = dict(transcript)
    out["segments"] = [
        {
            **seg,
            "speaker": label_for(seg.get("cluster")),
            "words": [{**w, "speaker": label_for(w.get("cluster"))} for w in seg.get("words", [])],
        }
        for seg in transcript["segments"]
    ]
    return out


def run(
    transcript_path: str,
    audio_path: str,
    enrollment_path: str,
    floor: float,
    margin: float,
    host_embeddings_path: str | None = DEFAULT_HOST_EMBEDDINGS_PATH,
) -> dict:
    with open(transcript_path) as f:
        transcript = json.load(f)
    with open(enrollment_path) as f:
        enrollment = json.load(f)

    john_wav, jason_wav = _confirmed_host_wavs(enrollment)
    pipeline = _load_pipeline(_read_hf_token())
    host_embeddings, ehash = _get_host_embeddings(
        pipeline, john_wav, jason_wav, enrollment_path, host_embeddings_path
    )

    by_cluster = _cluster_turns(transcript["segments"])
    cluster_results: dict[str, dict] = {}
    embeddable = {}
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = ffmpeg.to_wav(audio_path, os.path.join(tmp, "audio.wav"))
        for cluster, turns in by_cluster.items():
            try:
                embeddable[cluster] = embed_cluster(pipeline, wav_path, turns)
            except ValueError:
                # No turn in this cluster reaches the 1.0s embedding floor --
                # too little signal to embed, so it can't be matched to a host.
                cluster_results[cluster] = {
                    "label": "UNKNOWN",
                    "similarity": None,
                    "margin": None,
                    "per_host": {},
                }
        assigned = assign_hosts(embeddable, host_embeddings, floor, margin)

    for cluster, info in assigned.items():
        label = HOST_LABELS.get(info["label"], info["label"])  # john/jason -> JOHN/JASON
        cluster_results[cluster] = {**info, "label": label}

    output = apply_labels(transcript, cluster_results)
    output["enrollment"] = {
        "enrollment_hash": ehash,
        "floor": floor,
        "margin": margin,
        "clusters": cluster_results,
    }
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="transcription_worker.enroll_apply")
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--enrollment", required=True)
    parser.add_argument("--floor", type=float, required=True)
    parser.add_argument("--margin", type=float, required=True)
    parser.add_argument(
        "--host-embeddings",
        default=DEFAULT_HOST_EMBEDDINGS_PATH,
        help="cache/fallback for host vectors when local/enroll/*.wav is absent",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    for path, label in (
        (args.transcript, "transcript"),
        (args.audio, "audio"),
        (args.enrollment, "enrollment"),
    ):
        if not os.path.exists(path):
            print(f"error: {label} path does not exist: {path}", file=sys.stderr)
            return 1

    try:
        output = run(
            args.transcript,
            args.audio,
            args.enrollment,
            args.floor,
            args.margin,
            args.host_embeddings,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    write_atomic(args.output, output)
    # torch/MPS can abort in interpreter teardown after a complete write
    # (reports/hardware.md #4); exit hard so the caller sees the real result.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
