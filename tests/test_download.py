"""Tests for jev_or_not.download. No network: httpx.MockTransport only."""

import json
from unittest.mock import patch

import httpx
import pytest

from jev_or_not.download import download_one
from jev_or_not.ledger import open_ledger
from jev_or_not.models import Episode


def _episode(**overrides) -> Episode:
    fields = {
        "schema_version": 1,
        "episode_id": "theincomparable/robot/999",
        "episode": 999,
        "episode_label": "999",
        "title": "Test Episode",
        "released_at": None,
        "description": "",
        "show_notes_url": "https://www.theincomparable.com/robot/999/",
        "audio_url": "https://dts.podtrac.com/redirect.mp3/example.com/podcast/robot999.mp3",
        "duration_s": 300,
        "source": "feed",
        "retrieved_at": "2026-01-01T00:00:00+00:00",
    }
    fields.update(overrides)
    return Episode(**fields)


@pytest.fixture
def conn():
    c = open_ledger(":memory:")
    yield c
    c.close()


@patch("jev_or_not.download.measure_duration_s", return_value=123.0)
def test_download_success_records_redirect(_mock_duration, conn, tmp_path):
    final_url = "https://example.com/podcast/robot999.mp3"
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url).startswith("https://dts.podtrac.com"):
            return httpx.Response(302, headers={"Location": final_url})
        return httpx.Response(
            200, headers={"Content-Type": "audio/mpeg"}, content=b"FAKEMP3DATA" * 100
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    episode = _episode()

    result = download_one(client, conn, episode, audio_dir=tmp_path)

    assert result["status"] == "success"
    assert result["resolved_url"] == final_url
    assert result["audio_url"] != result["resolved_url"]
    assert len(calls) == 2  # tracking redirect, then the real host
    path = tmp_path / "999.mp3"
    assert path.read_bytes() == b"FAKEMP3DATA" * 100
    meta = json.loads((tmp_path / "999.meta.json").read_text())
    assert meta["status"] == "success"
    assert meta["measured_duration_s"] == 123.0


def test_html_body_is_failure(conn, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"Content-Type": "text/html"}, content=b"<html>not audio</html>"
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    episode = _episode()

    result = download_one(client, conn, episode, audio_dir=tmp_path)

    assert result["status"] == "failed"
    assert result["reason"] == "html_body"
    assert not (tmp_path / "999.mp3").exists()
    row = conn.execute("SELECT status, error_detail FROM tasks").fetchone()
    assert row[0] == "failed"
    assert "HTML body" in row[1]


@patch("jev_or_not.download.measure_duration_s", return_value=None)
def test_resume_from_partial_via_range(_mock_duration, conn, tmp_path):
    full = b"FAKEMP3DATA" * 100
    partial = full[:500]
    (tmp_path / "999.mp3").write_bytes(partial)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["range"] == f"bytes={len(partial)}-"
        return httpx.Response(
            206, headers={"Content-Type": "audio/mpeg"}, content=full[len(partial) :]
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    episode = _episode()

    result = download_one(client, conn, episode, audio_dir=tmp_path)

    assert result["status"] == "success"
    assert (tmp_path / "999.mp3").read_bytes() == full


@patch("jev_or_not.download.measure_duration_s", return_value=123.0)
def test_second_run_skips_via_ledger(_mock_duration, conn, tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, headers={"Content-Type": "audio/mpeg"}, content=b"DATA" * 50)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    episode = _episode()

    first = download_one(client, conn, episode, audio_dir=tmp_path)
    second = download_one(client, conn, episode, audio_dir=tmp_path)

    assert first["status"] == "success"
    assert second["status"] == "skipped"
    assert second["reason"] == "success"
    assert len(calls) == 1  # second run never re-requested
