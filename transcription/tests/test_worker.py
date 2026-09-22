"""Minimal self-check, no pytest fixtures/frameworks.

Run: uv run --project transcription/ python transcription/tests/test_worker.py
"""

import hashlib
import json
import tempfile
from pathlib import Path

from transcription_worker.__main__ import main, validate_request, verify_audio_hash
from transcription_worker.assign import assign_word_cluster, build_segments


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


def demo() -> None:
    test_validate_request()
    test_assign_word_cluster()
    test_build_segments()
    test_sha256_mismatch()
    test_missing_field_no_output()


if __name__ == "__main__":
    demo()
    print("ok")
