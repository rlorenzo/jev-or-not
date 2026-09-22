"""Transcription worker entry point.

Invoked by the main project as a subprocess (PLAN.md Phase 2):
`uv run --project transcription/ python -m transcription_worker --request <path> --output <path>`

Validates the request, transcribes with the chosen bake-off candidate,
diarizes with pyannote, assigns each word a speaker cluster, and writes a
schema-validated transcript atomically. No Python objects or imports cross
the environment boundary — everything comes from the request JSON and
environment variables (HF_TOKEN).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

from transcription_worker import assign, diarize, engines

REQUIRED_REQUEST_FIELDS = (
    "episode_id",
    "audio_path",
    "audio_sha256",
    "candidate",
    "device",
    "params",
    "diarization",
    "schema_version",
)

CANDIDATES = ("A", "B")  # A = mlx-whisper large-v3, B = parakeet-mlx tdt-0.6b-v3
SCHEMA_VERSION = 1


def validate_request(request: dict) -> list[str]:
    """Return a list of validation errors; empty means valid. Does not touch
    the filesystem beyond checking audio_path exists — sha256 is checked
    separately so it fails fast before any model loading."""
    errors = [f"missing field: {f}" for f in REQUIRED_REQUEST_FIELDS if f not in request]
    if errors:
        return errors

    if request["schema_version"] != SCHEMA_VERSION:
        errors.append(f"unsupported schema_version: {request['schema_version']}")
    if not os.path.exists(request["audio_path"]):
        errors.append(f"audio_path does not exist: {request['audio_path']}")
    if request["candidate"] not in CANDIDATES:
        errors.append(f"candidate must be one of {CANDIDATES}: got {request['candidate']!r}")

    params = request["params"]
    if not isinstance(params, dict) or not params.get("model"):
        errors.append("params.model is required")

    diar = request["diarization"]
    if not isinstance(diar, dict) or not diar.get("pipeline"):
        errors.append("diarization.pipeline is required")
    for key in ("min_speakers", "max_speakers"):
        if key in diar and diar[key] is not None and not isinstance(diar[key], int):
            errors.append(f"diarization.{key} must be an int")

    return errors


def verify_audio_hash(audio_path: str, expected_sha256: str) -> str | None:
    """Returns an error string on mismatch, else None."""
    h = hashlib.sha256()
    with open(audio_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual != expected_sha256:
        return f"audio_sha256 mismatch: expected {expected_sha256}, got {actual}"
    return None


def write_atomic(path: str, data: dict) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def run(request: dict) -> dict:
    """Runs the full pipeline and returns the output dict. Raises on any
    failure; caller is responsible for not writing output when this raises."""
    audio_path = request["audio_path"]
    params = request["params"]
    diar_req = request["diarization"]
    warnings: list[str] = []

    t0 = time.time()
    if request["candidate"] == "A":
        words, engine_meta = engines.transcribe_mlx_whisper(
            audio_path, params["model"], params.get("initial_prompt")
        )
    else:
        if params.get("initial_prompt"):
            warnings.append("candidate B (parakeet-mlx) does not support initial_prompt; ignored")
        words, engine_meta = engines.transcribe_parakeet_mlx(audio_path, params["model"])
    transcribe_s = time.time() - t0

    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        raise RuntimeError("HF_TOKEN environment variable is required for diarization")

    t0 = time.time()
    diar_segments, diar_meta = diarize.diarize(
        audio_path,
        diar_req["pipeline"],
        hf_token,
        min_speakers=diar_req.get("min_speakers"),
        max_speakers=diar_req.get("max_speakers"),
    )
    diarize_s = time.time() - t0

    t0 = time.time()
    words_with_clusters = [
        {**w, "cluster": assign.assign_word_cluster(w["start"], w["end"], diar_segments)}
        for w in words
    ]
    segments = assign.build_segments(words_with_clusters)
    assign_s = time.time() - t0

    unassigned = sum(1 for w in words_with_clusters if w["cluster"] is None)
    if unassigned:
        warnings.append(f"{unassigned} of {len(words_with_clusters)} words got no cluster")

    total_speech_by_cluster: dict[str, float] = {}
    for start, end, label in diar_segments:
        total_speech_by_cluster[label] = total_speech_by_cluster.get(label, 0.0) + (end - start)
    clusters = [
        {"id": cluster_id, "total_speech_s": round(total_s, 3)}
        for cluster_id, total_s in sorted(total_speech_by_cluster.items())
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "episode_id": request["episode_id"],
        "audio_sha256": request["audio_sha256"],
        "candidate": request["candidate"],
        "engine": {"name": engine_meta["engine_name"], "version": engine_meta["engine_version"]},
        "model": {"repo": engine_meta["model"], "revision": engine_meta["model_revision"]},
        "diarization": diar_meta,
        "device": engine_meta["device"],
        "timestamp_granularity": engine_meta["timestamp_granularity"],
        "segments": [
            {
                "start": seg["start"],
                "end": seg["end"],
                "cluster": seg["cluster"],
                "text": seg["text"],
                "words": [
                    {
                        "start": w["start"],
                        "end": w["end"],
                        "text": w["text"],
                        "cluster": w["cluster"],
                    }
                    for w in seg["words"]
                ],
            }
            for seg in segments
        ],
        "clusters": clusters,
        "runtime": {
            "transcribe_s": round(transcribe_s, 3),
            "diarize_s": round(diarize_s, 3),
            "assign_s": round(assign_s, 3),
            "total_s": round(transcribe_s + diarize_s + assign_s, 3),
        },
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="transcription_worker")
    parser.add_argument("--request", required=True, help="path to request JSON")
    parser.add_argument("--output", required=True, help="path to write transcript JSON")
    args = parser.parse_args(argv)

    try:
        with open(args.request) as f:
            request = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read request {args.request}: {exc}", file=sys.stderr)
        return 1

    errors = validate_request(request)
    if errors:
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        return 1

    hash_error = verify_audio_hash(request["audio_path"], request["audio_sha256"])
    if hash_error:
        print(f"error: {hash_error}", file=sys.stderr)
        return 1

    try:
        output = run(request)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    write_atomic(args.output, output)
    # torch/MPS can abort in interpreter teardown after a complete write (reports/hardware.md #4);
    # exit hard so the caller sees the real result.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
