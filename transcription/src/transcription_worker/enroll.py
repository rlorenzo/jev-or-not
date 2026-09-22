"""Speaker enrollment (PLAN.md Phase 2, "Speaker enrollment (runs before the
bake-off)", steps 1-4). Draft stage only: proposes enrollment candidate clips
and implements the pure embedding/assignment helpers. Does NOT freeze
floor/margin thresholds -- that needs the pilot reference, which does not
exist yet.

Embedding model: the community-1 diarization pipeline bundles a WeSpeaker
speaker-embedding model (see reports/hardware.md: "Speaker embedding:
WeSpeaker, loaded from the embedding/ subfolder bundled inside the
pyannote/speaker-diarization-community-1 repo itself"). The loaded
`pyannote.audio.Pipeline` instance exposes it as the `._embedding` attribute,
an `ONNXWeSpeakerPretrainedSpeakerEmbedding` -- see
`pipelines/speaker_diarization.py:261-264` (`self._embedding =
PretrainedSpeakerEmbedding(self.embedding, token=token, cache_dir=cache_dir)`)
and `pipelines/speaker_verification.py:386-622` (the class itself, 16 kHz
mono in, cosine metric, `__call__(waveforms) -> (batch, dimension)` ndarray)
of the installed pyannote.audio==4.0.7 package
(transcription/.venv/lib/python3.12/site-packages/pyannote/audio/). No new
dependency.
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import sys
import wave
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

# Importing diarize (not calling anything from it here) triggers its
# module-level `os.environ.setdefault("DYLD_LIBRARY_PATH", ...)` so pyannote
# can find Homebrew ffmpeg's dylibs (reports/hardware.md issue 2) -- must
# happen before any `from pyannote.audio import Pipeline`.
from transcription_worker import diarize as _diarize
from transcription_worker import engines, ffmpeg
from transcription_worker.__main__ import sha256_file, write_atomic

PIPELINE_NAME = "pyannote/speaker-diarization-community-1"
PARAKEET_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"

REPO_ROOT = Path(__file__).resolve().parents[3]
ENROLL_DIR = REPO_ROOT / "local" / "enroll"
DATA_PATH = REPO_ROOT / "data" / "enrollment.json"
PILOT_PATH = REPO_ROOT / "data" / "gold" / "pilot_episodes.json"
EXCLUDED_PATH = REPO_ROOT / "data" / "gold" / "excluded_episodes.json"


# --- shared helpers ---------------------------------------------------------


def reserved_episodes() -> set[int]:
    """Episode numbers enrollment must not touch: the frozen pilot set (whose
    transcripts enrollment would otherwise contaminate) plus the hand-excluded
    ones. Read from the gold files rather than re-declared here, so refreezing
    the pilot list can't leave this guard stale."""
    pilot = {pe["episode"] for pe in json.loads(PILOT_PATH.read_text())["episodes"]}
    excluded = {ex["episode"] for ex in json.loads(EXCLUDED_PATH.read_text())["excluded"]}
    return pilot | excluded


def _read_hf_token() -> str:
    token = os.environ.get("HF_TOKEN")
    if token:
        return token
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.strip().startswith("HF_TOKEN="):
                # .env values are commonly quoted; the quotes are not the token.
                return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError(f"HF_TOKEN not in environment or {env_path}")


def _wav_duration_s(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def _load_pipeline(hf_token: str):
    """Loads the community-1 pipeline once. Callers use it both for
    diarization (via `pipeline(audio_path, ...)`) and for embedding (via
    `pipeline._embedding`, see module docstring)."""
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(PIPELINE_NAME, token=hf_token)
    if pipeline is None:
        raise RuntimeError(
            f"Pipeline.from_pretrained({PIPELINE_NAME!r}) returned None -- "
            "likely a missing/invalid HF_TOKEN or ungranted model access"
        )
    try:
        import torch

        if torch.backends.mps.is_available():
            pipeline.to(torch.device("mps"))
    except Exception as exc:  # MPS optional; CPU is the documented fallback
        print(f"warning: enrollment pipeline on CPU, MPS unavailable: {exc}", file=sys.stderr)
    return pipeline


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


# --- step 1: embeddings ------------------------------------------------------


def embed_clip(pipeline, wav_path: str) -> np.ndarray:
    """Embeds a whole wav clip with the pipeline's bundled WeSpeaker model,
    L2-normalized."""
    from pyannote.audio.core.io import Audio

    audio = Audio(sample_rate=pipeline._embedding.sample_rate, mono="downmix")
    waveform, _ = audio(wav_path)  # (channel, time)
    emb = pipeline._embedding(waveform.unsqueeze(0))[0]  # (dimension,)
    return emb / np.linalg.norm(emb)


def embed_cluster(
    pipeline, wav_path: str, segments_for_cluster: list[tuple[float, float]]
) -> np.ndarray:
    """Embeds each speech turn >= 1.0 s in `segments_for_cluster` ((start,
    end), extra fields ignored) and averages, then L2-normalizes."""
    from pyannote.audio.core.io import Audio
    from pyannote.core import Segment

    audio = Audio(sample_rate=pipeline._embedding.sample_rate, mono="downmix")
    embs = []
    for seg in segments_for_cluster:
        start, end = seg[0], seg[1]
        if end - start < 1.0:
            continue
        waveform, _ = audio.crop(wav_path, Segment(start, end), mode="pad")
        embs.append(pipeline._embedding(waveform.unsqueeze(0))[0])
    if not embs:
        raise ValueError("no speech turns >= 1.0s in segments_for_cluster")
    mean = np.mean(embs, axis=0)
    return mean / np.linalg.norm(mean)


# --- step 2: candidate clip proposal ----------------------------------------


def _merge_turns(turns: list[tuple[float, float]], max_gap: float) -> list[tuple[float, float]]:
    """turns: (start, end) pairs for ONE cluster. Merges turns separated by
    a gap < max_gap into contiguous stretches."""
    if not turns:
        return []
    turns = sorted(turns)
    merged = [list(turns[0])]
    for start, end in turns[1:]:
        if start - merged[-1][1] < max_gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def longest_stretch(
    diar_segments: list[tuple[float, float, str]],
    cluster: str,
    max_gap: float = 0.5,
    cap_s: float = 60.0,
) -> tuple[float, float] | None:
    """Longest contiguous single-speaker stretch for `cluster` in
    diar_segments ((start, end, label) triples), merging turns with gaps <
    max_gap, capped at cap_s seconds. None if the cluster has no turns."""
    turns = [(s, e) for s, e, label in diar_segments if label == cluster]
    merged = _merge_turns(turns, max_gap)
    if not merged:
        return None
    start, end = max(merged, key=lambda se: se[1] - se[0])
    return start, min(end, start + cap_s)


def propose_candidates(episode_id: int = 3) -> dict:
    """Step 2-3: diarizes `episode_id` (min/max 2 speakers), cuts the longest
    single-speaker stretch per cluster to local/enroll/candidate_{cluster}.wav,
    transcribes each clip's first 20s for a human to identify the voice, and
    writes the unconfirmed draft to data/enrollment.json. Identity is NOT
    confirmed here -- proposed_host is a heuristic guess only."""
    if episode_id in reserved_episodes():
        raise ValueError(f"episode {episode_id} is pilot/excluded, not usable for enrollment")

    hf_token = _read_hf_token()
    mp3_path = REPO_ROOT / "local" / "audio" / f"{episode_id}.mp3"
    ENROLL_DIR.mkdir(parents=True, exist_ok=True)
    wav_path = ENROLL_DIR / f"source_{episode_id}.wav"
    ffmpeg.to_wav(str(mp3_path), str(wav_path))
    audio_sha256 = sha256_file(str(mp3_path))

    diar_segments, clusters = None, []
    for _attempt in range(2):
        segments, _meta = _diarize.diarize(
            str(wav_path), PIPELINE_NAME, hf_token, min_speakers=2, max_speakers=2
        )
        clusters = sorted({label for _, _, label in segments})
        if len(clusters) == 2:
            diar_segments = segments
            break
    if diar_segments is None:
        raise RuntimeError(
            f"diarization of episode {episode_id} did not yield 2 clusters on "
            f"either attempt (got {clusters}); stopping per instructions"
        )

    # Heuristic only, and a weak one: it assumes Jason opens the episode. The
    # human confirmation step below is what actually decides identity.
    first_label = min(diar_segments, key=lambda t: t[0])[2]
    proposed_host = {c: ("jason" if c == first_label else "john") for c in clusters}

    candidates = []
    for cluster in clusters:
        stretch = longest_stretch(diar_segments, cluster)
        if stretch is None:
            raise RuntimeError(f"no turns found for cluster {cluster}")
        start, end = stretch

        clip_path = ENROLL_DIR / f"candidate_{cluster}.wav"
        ffmpeg.to_wav(str(wav_path), str(clip_path), start=start, end=end)

        preview_path = ENROLL_DIR / f"candidate_{cluster}_preview20.wav"
        ffmpeg.to_wav(str(wav_path), str(preview_path), start=start, end=min(start + 20.0, end))
        words, _meta = engines.transcribe_parakeet_mlx(str(preview_path), PARAKEET_MODEL)
        transcript_preview = "".join(w["text"] for w in words).strip()

        candidates.append(
            {
                "cluster": cluster,
                "proposed_host": proposed_host[cluster],
                "wav_path": str(clip_path.relative_to(REPO_ROOT)),
                "start_s": round(start, 3),
                "end_s": round(end, 3),
                "duration_s": round(end - start, 3),
                "transcript_preview": transcript_preview,
            }
        )

    enrollment = {
        "schema_version": 1,
        "status": "unconfirmed",
        "source_episode_id": f"theincomparable/robot/{episode_id}",
        "source_episode": episode_id,
        "audio_sha256": audio_sha256,
        "sample_rate": 16000,
        "candidates": candidates,
        "confirmed": None,
        "thresholds": None,
        "created_at": datetime.now(UTC).isoformat(),
    }
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(str(DATA_PATH), enrollment)
    return enrollment


def confirm(mapping: dict[str, str]) -> dict:
    """Human-confirmation step from the plan: once a person has listened to
    the candidate clips and identified the voices, copies
    local/enroll/candidate_{cluster}.wav -> local/enroll/{host}.wav per
    `mapping` ({cluster: host}) and marks data/enrollment.json confirmed. NOT
    called automatically -- do not call until a human has actually listened.

    Copies rather than moves: the candidate clips are also sanity_check()'s
    input, and a corrected mapping should be re-confirmable, so confirmation
    must not be a one-way door.
    """
    if not DATA_PATH.exists():
        raise RuntimeError(f"{DATA_PATH} does not exist; run propose_candidates() first")
    enrollment = json.loads(DATA_PATH.read_text())

    for cand in enrollment["candidates"]:
        cluster = cand["cluster"]
        if cluster not in mapping:
            raise ValueError(f"mapping missing cluster {cluster!r}")
        host = mapping[cluster]
        old_path = REPO_ROOT / cand["wav_path"]
        new_path = ENROLL_DIR / f"{host}.wav"
        if old_path.exists():
            shutil.copy2(old_path, new_path)
        elif not new_path.exists():
            raise RuntimeError(
                f"{old_path} is missing and {new_path} does not exist; "
                "re-run propose_candidates() before confirming"
            )
        cand["confirmed_wav_path"] = str(new_path.relative_to(REPO_ROOT))
        cand["confirmed_host"] = host

    enrollment["status"] = "confirmed"
    enrollment["confirmed"] = mapping
    enrollment["confirmed_at"] = datetime.now(UTC).isoformat()
    write_atomic(str(DATA_PATH), enrollment)
    return enrollment


# --- step 4: cluster -> host assignment (pure) ------------------------------


def assign_hosts(
    cluster_embeddings: dict[str, np.ndarray],
    host_embeddings: dict[str, np.ndarray],
    floor: float,
    margin: float,
) -> dict[str, dict]:
    """Per cluster: cosine similarity to each host. label = nearest host,
    unless the top similarity is below `floor` (-> GUEST, no host matched)
    or the top two similarities are within `margin` of each other (->
    UNKNOWN, a host cleared the floor but the call is too close)."""
    result = {}
    for cluster, emb in cluster_embeddings.items():
        per_host = {host: _cosine(emb, h_emb) for host, h_emb in host_embeddings.items()}
        ranked = sorted(per_host.items(), key=lambda kv: kv[1], reverse=True)
        top_host, top_sim = ranked[0]
        second_sim = ranked[1][1] if len(ranked) > 1 else float("-inf")

        if top_sim < floor:
            label = "GUEST"
        elif top_sim - second_sim < margin:
            label = "UNKNOWN"
        else:
            label = top_host

        result[cluster] = {
            "label": label,
            "similarity": top_sim,
            "margin": top_sim - second_sim,
            "per_host": per_host,
        }
    return result


# --- step 5: sanity check (reports numbers only, freezes no thresholds) ----


def sanity_check(pipeline) -> dict:
    """Embeds both candidate clips, embeds the SPEAKER_00/SPEAKER_01 clusters
    from local/smoke/345_A.json against local/audio/345.mp3, and reports the
    candidate x cluster cosine matrix plus within/cross-candidate
    half-clip self-similarity. Informational only."""
    # The derivatives this function itself writes (_halfA/_halfB) and the
    # previews propose_candidates() writes share the candidate_* prefix, so a
    # second run would otherwise count 4 or 6 clips instead of the 2 originals.
    cand_wavs = sorted(
        p
        for p in ENROLL_DIR.glob("candidate_*.wav")
        if "preview" not in p.name and "_half" not in p.name
    )
    if len(cand_wavs) != 2:
        raise RuntimeError(f"expected 2 candidate clips in {ENROLL_DIR}, found {len(cand_wavs)}")
    cand_embs = {p.stem: embed_clip(pipeline, str(p)) for p in cand_wavs}

    src_mp3 = REPO_ROOT / "local" / "audio" / "345.mp3"
    src_wav = ENROLL_DIR / "345.wav"
    ffmpeg.to_wav(str(src_mp3), str(src_wav))
    transcript = json.loads((REPO_ROOT / "local" / "smoke" / "345_A.json").read_text())
    by_cluster: dict[str, list[tuple[float, float]]] = {}
    for seg in transcript["segments"]:
        by_cluster.setdefault(seg["cluster"], []).append((seg["start"], seg["end"]))
    cluster_embs = {
        c: embed_cluster(pipeline, str(src_wav), turns) for c, turns in by_cluster.items()
    }

    matrix = {
        cand_name: {cluster: _cosine(c_emb, cl_emb) for cluster, cl_emb in cluster_embs.items()}
        for cand_name, c_emb in cand_embs.items()
    }

    halves: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    within: dict[str, float] = {}
    for p in cand_wavs:
        dur = _wav_duration_s(p)
        mid = dur / 2
        half_a, half_b = ENROLL_DIR / f"{p.stem}_halfA.wav", ENROLL_DIR / f"{p.stem}_halfB.wav"
        ffmpeg.to_wav(str(p), str(half_a), start=0.0, end=mid)
        ffmpeg.to_wav(str(p), str(half_b), start=mid, end=dur)
        emb_a, emb_b = embed_clip(pipeline, str(half_a)), embed_clip(pipeline, str(half_b))
        halves[p.stem] = (emb_a, emb_b)
        within[p.stem] = _cosine(emb_a, emb_b)

    cross = {
        f"{a}_vs_{b}": _cosine(halves[a][0], halves[b][0])
        for a, b in itertools.combinations(halves, 2)
    }

    return {
        "candidate_x_cluster": matrix,
        "within_candidate_half_similarity": within,
        "cross_candidate_half_similarity": cross,
    }
