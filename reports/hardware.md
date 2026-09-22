# Transcription engine smoke tests (P2.1)

Hardware: Apple M4, 16 GB RAM, macOS 26.6 (Darwin 25.6), platform string
`macOS-26.6.2-arm64-arm-64bit`. Subproject: `transcription/`, standalone uv
project (own `pyproject.toml`/`uv.lock`/`.venv`), `.python-version` 3.12.
All five target packages installed cleanly on Python 3.12 on the first
attempt (no need to try 3.11): `mlx-whisper==0.4.3`, `faster-whisper==1.2.1`,
`pyannote.audio==4.0.7`, `parakeet-mlx==0.5.2`, `onnx-asr==0.12.0`.

## Hardware / device detection

`transcription/transcription_worker/hardware.py`, run via
`uv run --project transcription/ python -m transcription_worker.hardware`:

```text
cpu: Apple M4
ram_gb: 16.0
platform: macOS-26.6.2-arm64-arm-64bit
mlx_metal_available: True
torch_mps_available: True
ctranslate2_devices: cpu=['float32', 'int8', 'int8_float32'] cuda=[] mps=[]
```

- `mlx.core.metal.is_available()` — <https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.metal.is_available.html>
- `torch.backends.mps.is_available()` — <https://docs.pytorch.org/docs/stable/backends.html#torch.backends.mps.is_available>
- `ctranslate2.get_supported_compute_types(device)` — <https://opennmt.net/CTranslate2/python/ctranslate2.get_supported_compute_types.html>

CTranslate2 confirms no MPS/CUDA compute types on this machine: CPU only,
matching the faster-whisper PyPI page (no documented Apple GPU support).

## Smoke test clip

60 s cut from `local/audio/345.mp3` (79 s pilot episode "345: Backpack") with
`ffmpeg` (found at `/opt/homebrew/bin/ffmpeg`, so `afconvert` was not
needed): `ffmpeg -y -i local/audio/345.mp3 -t 60 -ar 16000 -ac 1
local/smoke/clip60.wav`.

## Per-engine results

| Engine | Model | Device | Wall (s) | RTF | Timestamps | Peak RSS (MB) | Status |
|---|---|---|---|---|---|---|---|
| mlx-whisper 0.4.3 | `mlx-community/whisper-large-v3-mlx` | Metal (MLX) | 54.6 | 0.91 | word-level, confirmed | 1592 | OK |
| faster-whisper 1.2.1 | `large-v3`, int8 | CPU | 88.0 | 1.47 | word-level, confirmed | 3644 | OK |
| parakeet-mlx 0.5.2 | `mlx-community/parakeet-tdt-0.6b-v3` | Metal (MLX) | 47.8 | 0.80 | sentence-level (`.sentences`) | 1048 | OK |
| onnx-asr 0.12.0 | `nemo-parakeet-tdt-0.6b-v3` | CPU (CoreML EP partial) | — | — | not reached | — | **failed, 2 attempts** |
| pyannote.audio 4.0.7 | `pyannote/speaker-diarization-community-1` | MPS | 7.6 | 0.13 | n/a (diarization) | 745 | OK, 2 speakers found |

Model repo names/citations:

- mlx-whisper large-v3 repo — mlx-whisper PyPI README example (`mlx_whisper.transcribe(..., path_or_hf_repo=...)`), <https://pypi.org/project/mlx-whisper/>
- parakeet-mlx repo — <https://pypi.org/project/parakeet-mlx/> (senstella, MLX port)
- onnx-asr model id — <https://pypi.org/project/onnx-asr/> (`onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3")`)
- pyannote community-1 — <https://huggingface.co/pyannote/speaker-diarization-community-1>

Text previews (first ~100 chars, all engines transcribed the same clip
opening consistently): "Is it a robot or not? John, listener Caleb wrote in
and said, I was at a trade show recently and th…"

## Dependency conflicts

None among the five target packages on Python 3.12 — single `uv add` for
all five resolved without needing an extras split.

## Issues hit and resolutions

1. **onnx-asr crash (2/2 attempts, unresolved):** `onnxruntime.InferenceSession`
   refuses to load the Parakeet TDT v3 ONNX encoder's external-data weights
   file, raising `External data path escapes model directory` — the
   Hugging Face cache stores the actual blob under a different hash-prefixed
   directory than the one onnxruntime's path-containment check expects, via
   the cache's symlink layout. Retried with `HF_HUB_DISABLE_SYMLINKS=1`
   (had no effect — model was already fully cached from attempt 1); same
   error both times. This is an onnxruntime 1.30.0 / huggingface_hub cache
   interaction bug, not a model or install problem. Per the stop condition
   (crashes twice), recording as failed rather than debugging further; a
   fix would need either a non-symlinked cache path or a patched/older
   onnxruntime.
2. **pyannote: `libtorchcodec` load failure (fixed):** `pipeline(audio)`
   failed to load `libtorchcodec` against Homebrew's ffmpeg 9.0.2. Fixed by
   setting `DYLD_LIBRARY_PATH=/opt/homebrew/lib` so torchcodec's dynamic
   loader finds Homebrew's `libavcodec`/`libavformat` dylibs. This env var
   will need to be set wherever the transcription worker invokes pyannote.
3. **pyannote: `DiarizeOutput` API (fixed):** pyannote.audio 4.x's pipeline
   call returns a `DiarizeOutput` dataclass, not a bare `pyannote.core.Annotation`;
   the `Annotation` (with `.itertracks()`) is at `.speaker_diarization`.
4. A `std::__1::system_error` (`recursive_mutex lock failed`) aborted the
   Python process *after* the pyannote result was written and printed —
   a native-library shutdown crash (torch/MPS + interpreter teardown), not
   a failure of the diarization run itself.

## Pyannote gated access

**Granted.** `Pipeline.from_pretrained("pyannote/speaker-diarization-community-1",
token=HF_TOKEN)` downloaded the gated model successfully (no 403 /
"not in the authorized list") and ran diarization, finding 2 speakers on
the 60 s clip on MPS (tried MPS first via `pipeline.to(torch.device("mps"))`,
which succeeded — no CPU fallback needed): wall 7.6 s, peak RSS 745 MB.
Speaker embedding: **WeSpeaker**, loaded from the `embedding/` subfolder
bundled inside the `pyannote/speaker-diarization-community-1` repo itself
(per the model card, no separate embedding checkpoint/repo); clustering is
`VBxClustering`.

## Worker verification (step 6)

```text
$ uv run --project transcription/ python -m transcription_worker \
    --request local/smoke/request.json --output local/smoke/out.json
exit: 0
```

`local/smoke/out.json`: `{"schema_version": 1, "engine": "mlx-whisper",
"model": "mlx-community/whisper-large-v3-mlx", "device": "mps", "segments": []}`

## Downloads

Total HF/MLX cache after all runs: ~11 GB (within the ~15 GB budget).

## Unfinished

- onnx-asr smoke test did not complete (see above); needs a cache/onnxruntime
  fix before it can be included in the Phase 2 bake-off.
- No bake-off, quality scoring, or enrollment — out of scope for P2.1 per
  the assignment.
