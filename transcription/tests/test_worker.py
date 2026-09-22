"""Minimal self-check, no pytest fixtures/frameworks.

Run: uv run --project transcription/ python transcription/tests/test_worker.py
"""

import hashlib
import json
import tempfile
from pathlib import Path

import numpy as np
from transcription_worker import ffmpeg
from transcription_worker.__main__ import main, validate_request, verify_audio_hash
from transcription_worker.assign import assign_word_cluster, build_segments
from transcription_worker.enroll import (
    _merge_turns,
    assign_hosts,
    longest_stretch,
    reserved_episodes,
)


def _valid_request(audio_path: str) -> dict:
    return {
        "episode_id": "test",
        "audio_path": audio_path,
        "audio_sha256": "deadbeef",
        "candidate": "A",
        "device": "mps",
        "params": {"model": "mlx-community/whisper-large-v3-mlx"},
        "diarization": {"pipeline": "pyannote/speaker-diarization-community-1"},
        "schema_version": 1,
    }


def test_validate_request() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / "audio.wav"
        audio.write_bytes(b"fake")
        req = _valid_request(str(audio))
        assert validate_request(req) == []
        assert validate_request({}) != []
        assert validate_request({**req, "candidate": "C"}) != []
        assert validate_request({**req, "schema_version": 2}) != []
        assert validate_request({**req, "params": {}}) != []
        assert validate_request({**req, "diarization": {}}) != []
        bad_diar = {**req["diarization"], "min_speakers": "two"}
        assert validate_request({**req, "diarization": bad_diar}) != []


def test_assign_word_cluster() -> None:
    diar = [(0.0, 2.0, "SPEAKER_00"), (2.0, 4.0, "SPEAKER_01")]
    # clean overlap
    assert assign_word_cluster(0.5, 1.0, diar) == "SPEAKER_00"
    assert assign_word_cluster(2.5, 3.0, diar) == "SPEAKER_01"
    # straddles the boundary: more overlap with SPEAKER_01
    assert assign_word_cluster(1.8, 2.6, diar) == "SPEAKER_01"
    # no overlap, but within 0.5s of SPEAKER_01's start
    assert assign_word_cluster(4.3, 4.4, diar) == "SPEAKER_01"
    # no overlap and beyond 0.5s of anything -> null
    assert assign_word_cluster(5.0, 5.1, diar) is None


def test_build_segments() -> None:
    words = [
        {"start": 0.0, "end": 0.5, "text": "Hi ", "cluster": "A"},
        {"start": 0.6, "end": 1.0, "text": "there", "cluster": "A"},  # 0.1s gap: merges
        {"start": 2.5, "end": 3.0, "text": " John", "cluster": "A"},  # 1.5s gap: splits
        {"start": 3.1, "end": 3.5, "text": " hi", "cluster": "B"},  # cluster change: splits
    ]
    segs = build_segments(words)
    assert len(segs) == 3
    assert segs[0]["text"] == "Hi there"
    assert segs[0]["cluster"] == "A"
    assert segs[1]["text"] == "John"
    assert segs[2]["cluster"] == "B"
    assert build_segments([]) == []


def test_sha256_mismatch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        audio = tmp / "audio.wav"
        audio.write_bytes(b"real audio bytes")
        actual_hash = hashlib.sha256(b"real audio bytes").hexdigest()

        assert verify_audio_hash(str(audio), actual_hash) is None
        assert verify_audio_hash(str(audio), "wrong") is not None

        req = _valid_request(str(audio))
        req["audio_sha256"] = "0" * 64  # deliberately wrong
        req_path = tmp / "request.json"
        out_path = tmp / "out.json"
        req_path.write_text(json.dumps(req))

        rc = main(["--request", str(req_path), "--output", str(out_path)])
        assert rc != 0
        assert not out_path.exists()


def test_missing_field_no_output() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        bad_req = tmp / "bad.json"
        bad_req.write_text(json.dumps({"episode_id": "test"}))
        bad_out = tmp / "bad_out.json"
        assert main(["--request", str(bad_req), "--output", str(bad_out)]) != 0
        assert not bad_out.exists()


def test_merge_turns() -> None:
    # 0.1s gap merges, 0.6s gap doesn't
    assert _merge_turns([(0.0, 1.0), (1.1, 2.0), (2.6, 3.0)], max_gap=0.5) == [
        (0.0, 2.0),
        (2.6, 3.0),
    ]
    assert _merge_turns([], max_gap=0.5) == []
    # unsorted input still merges correctly
    assert _merge_turns([(2.0, 3.0), (0.0, 1.0)], max_gap=0.5) == [(0.0, 1.0), (2.0, 3.0)]


def test_longest_stretch() -> None:
    diar = [
        (0.0, 1.0, "A"),
        (1.1, 2.0, "A"),  # merges with the above -> 0.0-2.0 (2.0s)
        (5.0, 5.5, "B"),  # isolated -> 0.5s
        (10.0, 200.0, "A"),  # isolated, long -> capped at 60s
    ]
    assert longest_stretch(diar, "A", max_gap=0.5, cap_s=60.0) == (10.0, 70.0)
    assert longest_stretch(diar, "B", max_gap=0.5, cap_s=60.0) == (5.0, 5.5)
    assert longest_stretch(diar, "C", max_gap=0.5, cap_s=60.0) is None


def test_assign_hosts() -> None:
    john = np.array([1.0, 0.0])
    jason = np.array([0.0, 1.0])
    hosts = {"john": john, "jason": jason}

    # clear match
    clear = assign_hosts({"c1": np.array([0.99, 0.14])}, hosts, floor=0.5, margin=0.2)
    assert clear["c1"]["label"] == "john"

    # below floor for both -> GUEST
    guest_vec = np.array([-1.0, -1.0])  # negative cosine to both hosts
    below = assign_hosts({"c2": guest_vec}, hosts, floor=0.5, margin=0.2)
    assert below["c2"]["label"] == "GUEST"

    # clears the floor but top two are close -> UNKNOWN
    close = np.array([1.0, 0.95])
    unk = assign_hosts({"c3": close}, hosts, floor=0.5, margin=0.2)
    assert unk["c3"]["label"] == "UNKNOWN"

    # determinism: same input, same output across repeated calls
    r1 = assign_hosts({"c1": np.array([0.99, 0.14])}, hosts, floor=0.5, margin=0.2)
    r2 = assign_hosts({"c1": np.array([0.99, 0.14])}, hosts, floor=0.5, margin=0.2)
    assert r1 == r2


def _captured_ffmpeg_argv(**kwargs) -> list[str]:
    """Run ffmpeg.to_wav with the subprocess call stubbed out; return the argv."""
    captured: list[list[str]] = []

    class _Done:
        returncode = 0
        stderr = ""

    real_run = ffmpeg.subprocess.run
    ffmpeg.subprocess.run = lambda cmd, **_: (captured.append(cmd), _Done())[1]
    try:
        ffmpeg.to_wav("in.mp3", "out.wav", **kwargs)
    finally:
        ffmpeg.subprocess.run = real_run
    return captured[0]


def test_ffmpeg_to_wav_argv() -> None:
    # Untrimmed: 16 kHz mono, no seek flags.
    argv = _captured_ffmpeg_argv()
    assert argv[0] == ffmpeg.FFMPEG
    assert argv[-5:] == ["-ac", "1", "-ar", "16000", "out.wav"]
    assert "-ss" not in argv and "-to" not in argv

    # Trimmed: -ss/-to come after -i so the seek is frame-accurate.
    argv = _captured_ffmpeg_argv(start=1.5, end=3.25)
    assert argv[argv.index("-ss") + 1] == "1.500"
    assert argv[argv.index("-to") + 1] == "3.250"
    assert argv.index("-i") < argv.index("-ss")

    # A start with no end is allowed (cut to the end of the file).
    argv = _captured_ffmpeg_argv(start=2.0)
    assert "-ss" in argv and "-to" not in argv


def test_reserved_episodes_comes_from_the_gold_files() -> None:
    reserved = reserved_episodes()
    assert {0, 297} <= reserved  # hand-excluded compilations
    assert {1, 6} <= reserved  # pinned pilot episodes
    assert 3 not in reserved  # the episode enrollment actually used


def demo() -> None:
    test_validate_request()
    test_assign_word_cluster()
    test_build_segments()
    test_sha256_mismatch()
    test_missing_field_no_output()
    test_merge_turns()
    test_longest_stretch()
    test_assign_hosts()
    test_ffmpeg_to_wav_argv()
    test_reserved_episodes_comes_from_the_gold_files()


if __name__ == "__main__":
    demo()
    print("ok")
