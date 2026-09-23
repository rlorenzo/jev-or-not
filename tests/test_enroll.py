"""Tests for jev_or_not.enroll. No real worker, no models: subprocess.run is stubbed."""

import json
import subprocess
from pathlib import Path

import pytest

from jev_or_not import enroll as enroll_module
from jev_or_not import transcribe as transcribe_module
from jev_or_not.ledger import open_ledger


@pytest.fixture
def conn():
    c = open_ledger(":memory:")
    yield c
    c.close()


def _index_row(**overrides) -> dict:
    row = {
        "episode_id": "theincomparable/robot/6",
        "episode_label": "6",
        "candidate": "B",
        "transcript_path": "local/transcripts/6-B.json",
        "transcript_hash": "a" * 64,
    }
    row.update(overrides)
    return row


def _manifest_row(**overrides) -> dict:
    row = {"episode_id": "theincomparable/robot/6", "file_path": "local/audio/6.mp3"}
    row.update(overrides)
    return row


def _valid_apply_output(episode_id: str = "theincomparable/robot/6") -> dict:
    return {
        "episode_id": episode_id,
        "segments": [],
        "clusters": [{"id": "SPEAKER_00", "total_speech_s": 10.0}],
        "enrollment": {
            "enrollment_hash": "e" * 64,
            "floor": 0.6,
            "margin": 0.15,
            "clusters": {
                "SPEAKER_00": {"label": "JOHN", "similarity": 0.9, "margin": 0.3, "per_host": {}}
            },
        },
    }


def _stub_subprocess_run(*, output_builder=_valid_apply_output, returncode: int = 0, calls=None):
    def _stub(cmd, **kwargs):
        del kwargs  # env/capture_output/text/timeout/check; unused by the stub
        if calls is not None:
            calls.append(cmd)
        out_path = cmd[cmd.index("--output") + 1]
        if returncode == 0:
            Path(out_path).write_text(json.dumps(output_builder()))
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="boom")

    return _stub


def test_apply_one_reuses_matching_fingerprint(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(enroll_module, "ENROLLED_DIR", tmp_path)
    monkeypatch.setattr(enroll_module, "ENROLLED_INDEX_PATH", tmp_path / "enrolled_index.jsonl")
    monkeypatch.setattr(enroll_module, "_worker_env", lambda: {})
    calls: list[list[str]] = []
    monkeypatch.setattr(enroll_module.subprocess, "run", _stub_subprocess_run(calls=calls))

    row, manifest = _index_row(), _manifest_row()

    first = enroll_module.apply_one(
        conn, row, manifest, 0.6, 0.15, "e" * 64, "c" * 64, force=False, run_nonce=None
    )
    assert first["status"] == "success"
    assert len(calls) == 1
    # Output follows the patched ENROLLED_DIR, not the real local/ tree.
    assert (tmp_path / "6-B.json").exists()

    second = enroll_module.apply_one(
        conn, row, manifest, 0.6, 0.15, "e" * 64, "c" * 64, force=False, run_nonce=None
    )
    assert second["status"] == "skipped"
    assert len(calls) == 1  # fingerprint matched -> no second subprocess call

    index_rows = enroll_module.export_index(conn, [row])
    assert len(index_rows) == 1
    assert index_rows[0].clusters[0].label == "JOHN"
    assert index_rows[0].unknown_speech_s == 0.0


def test_apply_one_failure_writes_no_index_row(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(enroll_module, "ENROLLED_DIR", tmp_path)
    monkeypatch.setattr(enroll_module, "ENROLLED_INDEX_PATH", tmp_path / "enrolled_index.jsonl")
    monkeypatch.setattr(enroll_module, "_worker_env", lambda: {})
    monkeypatch.setattr(enroll_module.subprocess, "run", _stub_subprocess_run(returncode=1))

    row, manifest = _index_row(), _manifest_row()

    result = enroll_module.apply_one(
        conn, row, manifest, 0.6, 0.15, "e" * 64, "c" * 64, force=False, run_nonce=None
    )
    assert result["status"] == "failed"

    index_rows = enroll_module.export_index(conn, [row])
    assert index_rows == []


def test_apply_sources_all_exist():
    # Hand-maintained like transcribe.WORKER_SOURCES; a rename in the worker
    # must not turn the enroll code hash into a crash at the top of a run.
    for name in enroll_module.APPLY_SOURCES:
        assert (transcribe_module.WORKER_DIR / name).exists(), name


@pytest.mark.parametrize(
    "thresholds,expected",
    [
        (None, "provisional"),  # the pre-threshold enrollment.json shape
        ({"floor": 0.6, "margin": 0.15, "status": "frozen"}, "frozen"),
    ],
)
def test_thresholds_status(tmp_path, thresholds, expected):
    path = tmp_path / "enrollment.json"
    path.write_text(json.dumps({"thresholds": thresholds}))
    assert enroll_module._thresholds_status(path) == expected
