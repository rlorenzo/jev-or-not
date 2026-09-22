"""Transcription worker entry point.

Invoked by the main project as a subprocess (PLAN.md Phase 2):
`uv run --project transcription/ python -m transcription_worker --request <path> --output <path>`

For now (P2.1) this only validates the request and writes a stub transcript.
Full engine dispatch comes later.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REQUIRED_REQUEST_FIELDS = (
    "episode_id",
    "audio_path",
    "audio_sha256",
    "candidate",
    "device",
    "params",
)

SCHEMA_VERSION = 1


def validate_request(request: dict) -> list[str]:
    """Return a list of validation errors; empty means valid."""
    errors = [f"missing field: {f}" for f in REQUIRED_REQUEST_FIELDS if f not in request]
    if "audio_path" in request and not os.path.exists(request["audio_path"]):
        errors.append(f"audio_path does not exist: {request['audio_path']}")
    return errors


def write_atomic(path: str, data: dict) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


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

    transcript = {
        "schema_version": SCHEMA_VERSION,
        "engine": request["candidate"],
        "model": request["params"].get("model", ""),
        "device": request["device"],
        "segments": [],
    }
    write_atomic(args.output, transcript)
    return 0


if __name__ == "__main__":
    sys.exit(main())
