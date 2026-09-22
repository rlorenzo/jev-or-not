"""Minimal self-check.

Run: uv run --project transcription/ python transcription/tests/test_worker.py
"""

import json
import tempfile
from pathlib import Path

from transcription_worker.__main__ import main, validate_request


def demo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        audio = tmp / "audio.wav"
        audio.write_bytes(b"fake")
        request = {
            "episode_id": "test",
            "audio_path": str(audio),
            "audio_sha256": "deadbeef",
            "candidate": "mlx-whisper",
            "device": "mps",
            "params": {"model": "large-v3"},
        }
        assert validate_request(request) == []
        assert validate_request({}) != []

        req_path = tmp / "request.json"
        out_path = tmp / "out.json"
        req_path.write_text(json.dumps(request))

        rc = main(["--request", str(req_path), "--output", str(out_path)])
        assert rc == 0
        out = json.loads(out_path.read_text())
        assert out["schema_version"] == 1
        assert out["engine"] == "mlx-whisper"
        assert out["segments"] == []

        # missing required field -> nonzero exit, no output written
        bad_req = tmp / "bad.json"
        bad_req.write_text(json.dumps({"episode_id": "test"}))
        bad_out = tmp / "bad_out.json"
        assert main(["--request", str(bad_req), "--output", str(bad_out)]) != 0
        assert not bad_out.exists()

    print("ok")


if __name__ == "__main__":
    demo()
