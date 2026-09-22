"""The two bake-off transcription candidates (PLAN.md Phase 2 default stack).

Both functions return (words, meta):
  words: [{"start": float, "end": float, "text": str}] in time order, text
         carrying the engine's native spacing convention (join with "" and
         strip to get natural text, as pyannote-facing code needs to).
  meta:  {"engine_name", "engine_version", "model", "model_revision",
          "device", "timestamp_granularity", "prompt_supported"}
"""

from __future__ import annotations

import importlib.metadata as metadata


def _mlx_device() -> str:
    # Same detection mlx_whisper/parakeet-mlx use internally; both always run
    # on Metal via MLX's default device when it's available on this hardware.
    # https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.metal.is_available.html
    try:
        import mlx.core as mx

        return "mps" if mx.metal.is_available() else "cpu"
    except Exception:
        return "unknown"


def _hf_revision(repo_id: str) -> str | None:
    # huggingface_hub is already a transitive dep of mlx-whisper/parakeet-mlx
    # (both download weights from the Hub). Report the cached snapshot's
    # commit hash if one is present; None if it can't be determined.
    try:
        from huggingface_hub import scan_cache_dir

        for repo in scan_cache_dir().repos:
            if repo.repo_id == repo_id:
                revisions = sorted(repo.revisions, key=lambda r: r.last_modified, reverse=True)
                if revisions:
                    return revisions[0].commit_hash
    except Exception:
        return None
    return None


def transcribe_mlx_whisper(
    audio_path: str, model: str, initial_prompt: str | None = None
) -> tuple[list[dict], dict]:
    """Candidate A: mlx-whisper large-v3 with word-level timestamps.

    API: https://pypi.org/project/mlx-whisper/
    `mlx_whisper.transcribe(audio, path_or_hf_repo=..., word_timestamps=True,
    initial_prompt=...)` — signature confirmed in
    transcribe.py:62-78 of the installed 0.4.3 package (initial_prompt is a
    real, supported kwarg, not undocumented).
    """
    import mlx_whisper

    kwargs = {"path_or_hf_repo": model, "word_timestamps": True}
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt
    result = mlx_whisper.transcribe(audio_path, **kwargs)

    words = [
        {"start": float(w["start"]), "end": float(w["end"]), "text": w["word"]}
        for seg in result["segments"]
        for w in seg.get("words") or []  # non-speech segments can carry "words": None
    ]
    meta = {
        "engine_name": "mlx-whisper",
        "engine_version": metadata.version("mlx-whisper"),
        "model": model,
        "model_revision": _hf_revision(model),
        "device": _mlx_device(),
        "timestamp_granularity": "word",
        "prompt_supported": True,
    }
    return words, meta


def transcribe_parakeet_mlx(audio_path: str, model: str) -> tuple[list[dict], dict]:
    """Candidate B: parakeet-mlx tdt-0.6b-v3. No prompt/hotword support and no
    word-timestamps API; the finest granularity it exposes is per-token via
    `AlignedResult.tokens` (sentences are coarser, built from those same
    tokens — see alignment.py:39-48 of the installed 0.5.2 package).

    API: https://pypi.org/project/parakeet-mlx/ (`from_pretrained`,
    `model.transcribe(path) -> AlignedResult`).
    """
    from parakeet_mlx import from_pretrained

    m = from_pretrained(model)
    result = m.transcribe(audio_path)

    words = [
        {"start": float(t.start), "end": float(t.end), "text": t.text}
        for t in result.tokens
        if t.text.strip()
    ]
    meta = {
        "engine_name": "parakeet-mlx",
        "engine_version": metadata.version("parakeet-mlx"),
        "model": model,
        "model_revision": _hf_revision(model),
        "device": _mlx_device(),
        "timestamp_granularity": "token",
        "prompt_supported": False,
    }
    return words, meta
