"""The worker's single ffmpeg decode.

Every model in this package wants the same thing — 16 kHz mono PCM — so the
decode lives here once instead of being re-spelled at each call site, where
the flags had already started to drift.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404: fixed ffmpeg argv, no shell

# Homebrew's arm64 prefix is the fallback because that is where the dylibs
# diarize.py points DYLD_LIBRARY_PATH at live; $PATH wins when it has one.
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def to_wav(src: str, dst: str, *, start: float | None = None, end: float | None = None) -> str:
    """Decode ``src`` to a 16 kHz mono WAV at ``dst``, optionally trimmed to
    the ``[start, end]`` second range. Returns ``dst``.

    ``-ss``/``-to`` come after ``-i`` on purpose: output-side seeking is
    slower than input-side but frame-accurate, which matters for the short
    enrollment clips cut out of a two-hour episode.
    """
    cmd = [FFMPEG, "-loglevel", "error", "-y", "-i", str(src)]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    cmd += ["-ac", "1", "-ar", "16000", str(dst)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)  # nosec B603: fixed argv
    except subprocess.CalledProcessError as exc:
        # check=True swallows the captured stderr, which is the only place
        # ffmpeg says what actually went wrong.
        raise RuntimeError(f"ffmpeg failed on {src}: {exc.stderr.strip()}") from exc
    return str(dst)
