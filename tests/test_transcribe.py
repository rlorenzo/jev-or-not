"""Tests for jev_or_not.transcribe. No real worker, no network: subprocess.run is stubbed."""

import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from jev_or_not import transcribe as transcribe_module
from jev_or_not.ledger import open_ledger
from jev_or_not.models import AudioManifestEntry, Episode
from jev_or_not.transcribe import (
    WORKER_SOURCES,
    build_request,
    export_index,
    transcribe_one,
    worker_code_hash,
)


def _episode(**overrides) -> Episode:
    fields = {
        "schema_version": 1,
        "episode_id": "theincomparable/robot/6",
        "episode": 6,
        "episode_label": "6",
        "title": "6: Self-Driving Cars",
        "released_at": None,
        "description": "",
        "show_notes_url": "https://www.theincomparable.com/robot/6/",
        "audio_url": "https://example.com/robot6.mp3",
        "duration_s": 326,
        "source": "feed",
        "retrieved_at": "2026-01-01T00:00:00+00:00",
    }
    fields.update(overrides)
    return Episode(**fields)


def _manifest_entry(**overrides) -> AudioManifestEntry:
    fields = {
        "schema_version": 1,
        "episode_id": "theincomparable/robot/6",
        "episode_label": "6",
        "audio_url": "https://example.com/robot6.mp3",
        "resolved_url": "https://example.com/robot6.mp3",
        "file_path": "local/audio/6.mp3",
        "bytes": 1000,
        "sha256": "a" * 64,
        "content_type": "audio/mpeg",
        "measured_duration_s": 326.0,
        "feed_duration_s": 326,
        "duration_mismatch": False,
        "downloaded_at": "2026-01-01T00:00:00+00:00",
        "status": "success",
    }
    fields.update(overrides)
    return AudioManifestEntry(**fields)


SCORECARD_ROWS = [
    {"episode_id": "theincomparable/robot/6", "subject": "Johnny Cab"},
    {"episode_id": "theincomparable/robot/6", "subject": "Cruise missile"},
    {"episode_id": "theincomparable/robot/50", "subject": "Agent Smith"},  # different episode
]


def _valid_output(request: dict) -> dict:
    return {
        "schema_version": request["schema_version"],
        "episode_id": request["episode_id"],
        "audio_sha256": request["audio_sha256"],
        "candidate": request["candidate"],
        "engine": {"name": "mlx-whisper", "version": "1.0"},
        "model": {"repo": request["params"]["model"], "revision": "abc123"},
        "diarization": {"pipeline": "pyannote/speaker-diarization-community-1", "device": "mps"},
        "device": "mps",
        "timestamp_granularity": "word",
        "segments": [
            {"start": 0.0, "end": 1.0, "cluster": "SPEAKER_00", "text": "hi", "words": []}
        ],
        "clusters": [{"id": "SPEAKER_00", "total_speech_s": 1.0}],
        "runtime": {"transcribe_s": 1.0, "diarize_s": 1.0, "assign_s": 0.1, "total_s": 2.1},
        "warnings": [],
    }


def _stub_subprocess_run(*, output_builder=_valid_output, returncode=0, calls=None):
    def _stub(cmd, **kwargs):
        del kwargs  # subprocess.run's env/capture_output/text/timeout/check; unused by the stub
        if calls is not None:
            calls.append(cmd)
        req_path = cmd[cmd.index("--request") + 1]
        out_path = cmd[cmd.index("--output") + 1]
        request = json.loads(Path(req_path).read_text())
        if returncode == 0:
            output = output_builder(request)
            Path(out_path).write_text(json.dumps(output))
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="boom")

    return _stub


@pytest.fixture
def conn():
    c = open_ledger(":memory:")
    yield c
    c.close()


def test_build_request_includes_episode_scorecard_subjects():
    episode = _episode()
    manifest = _manifest_entry()

    request = build_request(episode, manifest, "A", SCORECARD_ROWS)

    prompt = request["params"]["initial_prompt"]
    assert "Siracusa" in prompt  # base term
    assert "Johnny Cab" in prompt  # this episode's scorecard subject
    assert "Cruise missile" in prompt
    assert "Agent Smith" not in prompt  # belongs to a different episode
    assert request["audio_path"] == manifest.file_path
    assert request["audio_sha256"] == manifest.sha256
    assert request["schema_version"] == 1


def test_candidate_b_gets_no_initial_prompt():
    # parakeet-mlx has no prompt API; sending one would only make the worker warn.
    request = build_request(_episode(), _manifest_entry(), "B", SCORECARD_ROWS)

    assert "initial_prompt" not in request["params"]
    assert request["params"]["model"] == "mlx-community/parakeet-tdt-0.6b-v3"
    assert "prompt_supported" not in request["params"]  # config, not a worker param


def _fake_worker_tree(tmp_path: Path) -> Path:
    worker = tmp_path / "transcription_worker"
    worker.mkdir()
    for name in WORKER_SOURCES:
        (worker / name).write_text(f"# {name}\n")
    (worker / "enroll.py").write_text("# enrollment tooling\n")
    (tmp_path / "uv.lock").write_text("lock v1\n")
    return worker


def test_worker_code_hash_ignores_sibling_tooling(tmp_path, monkeypatch):
    worker = _fake_worker_tree(tmp_path)
    monkeypatch.setattr(transcribe_module, "WORKER_DIR", worker)
    monkeypatch.setattr(transcribe_module, "WORKER_LOCK", tmp_path / "uv.lock")

    before = worker_code_hash()

    # enroll.py cannot change a transcript, so editing it must not invalidate
    # every cached transcript.
    (worker / "enroll.py").write_text("# edited enrollment tooling\n")
    assert worker_code_hash() == before

    # An engine change can change the output, so it must invalidate.
    (worker / "engines.py").write_text("# edited engine\n")
    assert worker_code_hash() != before


def test_worker_code_hash_covers_lockfile(tmp_path, monkeypatch):
    worker = _fake_worker_tree(tmp_path)
    monkeypatch.setattr(transcribe_module, "WORKER_DIR", worker)
    monkeypatch.setattr(transcribe_module, "WORKER_LOCK", tmp_path / "uv.lock")

    before = worker_code_hash()
    (tmp_path / "uv.lock").write_text("lock v2\n")

    assert worker_code_hash() != before


def test_worker_sources_all_exist():
    # The list is hand-maintained; a rename in the worker must not silently
    # turn worker_code_hash into a crash at the top of a long run.
    for name in WORKER_SOURCES:
        assert (transcribe_module.WORKER_DIR / name).exists(), name


def test_fingerprint_reuse_skips_subprocess_on_rerun(conn, tmp_path):
    episode = _episode()
    manifest = _manifest_entry()
    calls: list = []

    with patch(
        "jev_or_not.transcribe.subprocess.run", side_effect=_stub_subprocess_run(calls=calls)
    ):
        first = transcribe_one(
            conn,
            episode,
            manifest,
            "A",
            SCORECARD_ROWS,
            worker_hash="fixed-hash",
            requests_dir=tmp_path / "requests",
            transcripts_dir=tmp_path / "transcripts",
        )
        second = transcribe_one(
            conn,
            episode,
            manifest,
            "A",
            SCORECARD_ROWS,
            worker_hash="fixed-hash",
            requests_dir=tmp_path / "requests",
            transcripts_dir=tmp_path / "transcripts",
        )

    assert first["status"] == "success"
    assert second["status"] == "skipped"
    assert second["reason"] == "success"
    assert len(calls) == 1  # second run never re-invoked the worker


def test_nonzero_exit_is_failure_with_no_index_row(conn, tmp_path):
    episode = _episode()
    manifest = _manifest_entry()

    with patch(
        "jev_or_not.transcribe.subprocess.run",
        side_effect=_stub_subprocess_run(returncode=1),
    ):
        result = transcribe_one(
            conn,
            episode,
            manifest,
            "A",
            SCORECARD_ROWS,
            worker_hash="fixed-hash",
            requests_dir=tmp_path / "requests",
            transcripts_dir=tmp_path / "transcripts",
        )

    assert result["status"] == "failed"
    assert result["reason"] == "worker_exit"
    row = conn.execute("SELECT status FROM tasks").fetchone()
    assert row[0] == "failed"
    assert not (tmp_path / "transcripts" / "6-A.json").exists()


def test_mismatched_episode_id_is_failure(conn, tmp_path):
    episode = _episode()
    manifest = _manifest_entry()

    def _wrong_episode_id(request: dict) -> dict:
        output = _valid_output(request)
        output["episode_id"] = "theincomparable/robot/999"
        return output

    with patch(
        "jev_or_not.transcribe.subprocess.run",
        side_effect=_stub_subprocess_run(output_builder=_wrong_episode_id),
    ):
        result = transcribe_one(
            conn,
            episode,
            manifest,
            "A",
            SCORECARD_ROWS,
            worker_hash="fixed-hash",
            requests_dir=tmp_path / "requests",
            transcripts_dir=tmp_path / "transcripts",
        )

    assert result["status"] == "failed"
    assert result["reason"] == "mismatch"
    assert not (tmp_path / "transcripts" / "6-A.json").exists()


def _transcribe_then_index(conn, tmp_path, monkeypatch) -> tuple[list, Path]:
    """Run one stubbed transcription, then export the index into tmp_path."""
    index_path = tmp_path / "transcript_index.jsonl"
    monkeypatch.setattr(transcribe_module, "INDEX_PATH", index_path)
    episode, manifest = _episode(), _manifest_entry()

    with patch("jev_or_not.transcribe.subprocess.run", side_effect=_stub_subprocess_run()):
        transcribe_one(
            conn,
            episode,
            manifest,
            "A",
            SCORECARD_ROWS,
            worker_hash="fixed-hash",
            requests_dir=tmp_path / "requests",
            transcripts_dir=tmp_path / "transcripts",
        )

    rows = export_index(conn, [episode], {manifest.episode_id: manifest})
    return rows, index_path


def test_export_index_row_matches_transcript_on_disk(conn, tmp_path, monkeypatch):
    rows, index_path = _transcribe_then_index(conn, tmp_path, monkeypatch)

    assert len(rows) == 1
    row = rows[0]
    assert row.episode_label == "6"
    assert row.candidate == "A"
    assert row.engine == "mlx-whisper"
    assert row.model == "mlx-community/whisper-large-v3-mlx"
    assert row.n_segments == 1
    assert row.n_clusters == 1
    assert row.runtime_total_s == 2.1
    assert row.rtf == pytest.approx(2.1 / 326.0)

    # transcript_hash is now taken from the bytes read for parsing, so it must
    # still be the plain sha256 of the file.
    on_disk = Path(row.transcript_path).read_bytes()
    assert row.transcript_hash == hashlib.sha256(on_disk).hexdigest()

    written = [json.loads(line) for line in index_path.read_text().splitlines() if line.strip()]
    assert [w["transcript_hash"] for w in written] == [row.transcript_hash]


def test_export_index_keeps_only_the_newest_success_per_item(conn, tmp_path, monkeypatch):
    """A --force re-run leaves an older success row for the same item_id behind;
    the index must carry the newest one only, not both."""
    rows, index_path = _transcribe_then_index(conn, tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO tasks (task_id, phase, item_id, fingerprint, status, result_path, "
        "created_at, updated_at) VALUES (?, 'transcribe', ?, 'stale-fingerprint', 'success', "
        "?, '2000-01-01 00:00:00', '2000-01-01 00:00:00')",
        ("stale-task", f"{_episode().episode_id}|A", rows[0].transcript_path),
    )
    conn.commit()

    episode, manifest = _episode(), _manifest_entry()
    reexported = export_index(conn, [episode], {manifest.episode_id: manifest})

    assert [r.fingerprint for r in reexported] == [rows[0].fingerprint]
    assert len(index_path.read_text().splitlines()) == 1


def test_export_index_drops_rows_whose_transcript_is_gone(conn, tmp_path, monkeypatch):
    rows, _ = _transcribe_then_index(conn, tmp_path, monkeypatch)
    Path(rows[0].transcript_path).unlink()

    episode, manifest = _episode(), _manifest_entry()
    assert export_index(conn, [episode], {manifest.episode_id: manifest}) == []
