"""Speaker diarization via pyannote's speaker-diarization-community-1
pipeline (PLAN.md Phase 2 default — never speaker-diarization-3.1).

Model: https://huggingface.co/pyannote/speaker-diarization-community-1
API: pyannote.audio 4.x's pipeline call returns a `DiarizeOutput` dataclass,
not a bare `Annotation`; `.speaker_diarization` is the Annotation with
`.itertracks()`. See speaker_diarization.py:64-70 of the installed 4.0.7
package, and reports/hardware.md issue 3 (confirmed against the live model
in local/smoke/run_pyannote.py).
"""

from __future__ import annotations

import importlib.metadata as metadata
import os
import sys

# pyannote.audio's audio loader pulls in torchcodec, which failed to find
# Homebrew ffmpeg's dylibs at runtime on this machine (reports/hardware.md
# issue 2). Setting this before the first pyannote import lets dyld resolve
# them in-process; confirmed working (see worker report). If a future
# machine still fails here, the caller must export DYLD_LIBRARY_PATH itself
# before invoking the worker.
os.environ.setdefault("DYLD_LIBRARY_PATH", "/opt/homebrew/lib")


def diarize(
    audio_path: str,
    pipeline_name: str,
    hf_token: str,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
) -> tuple[list[tuple[float, float, str]], dict]:
    """Returns (segments, meta). segments: [(start, end, label), ...] in the
    order pyannote produces them (not necessarily time-sorted)."""
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(pipeline_name, token=hf_token)
    if pipeline is None:
        raise RuntimeError(
            f"Pipeline.from_pretrained({pipeline_name!r}) returned None — "
            "likely a missing/invalid HF_TOKEN or ungranted model access"
        )

    device = "cpu"
    try:
        import torch

        if torch.backends.mps.is_available():
            pipeline.to(torch.device("mps"))
            device = "mps"
    except Exception as exc:  # MPS is optional; CPU is the documented fallback
        print(f"warning: diarization on CPU, MPS unavailable: {exc}", file=sys.stderr)

    kwargs = {}
    if min_speakers is not None:
        kwargs["min_speakers"] = min_speakers
    if max_speakers is not None:
        kwargs["max_speakers"] = max_speakers
    output = pipeline(audio_path, **kwargs)
    annotation = output.speaker_diarization

    segments = [
        (float(segment.start), float(segment.end), label)
        for segment, _, label in annotation.itertracks(yield_label=True)
    ]
    meta = {
        "pipeline": pipeline_name,
        "version": metadata.version("pyannote.audio"),
        "device": device,
    }
    return segments, meta
