"""Tests for jev_or_not.download. No network: httpx.MockTransport only."""

import json
from unittest.mock import patch

import httpx
import pytest

from jev_or_not.download import MAX_DOWNLOAD_BYTES, download_one
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
            206,
            headers={
                "Content-Type": "audio/mpeg",
                "Content-Range": f"bytes {len(partial)}-{len(full) - 1}/{len(full)}",
            },
            content=full[len(partial) :],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    episode = _episode()

    result = download_one(client, conn, episode, audio_dir=tmp_path)

    assert result["status"] == "success"
    assert (tmp_path / "999.mp3").read_bytes() == full


@patch("jev_or_not.download.measure_duration_s", return_value=None)
def test_resume_with_mismatched_content_range_start_restarts_clean(_mock_duration, conn, tmp_path):
    """A 206 that doesn't honor the requested resume offset must not be appended blindly."""
    full = b"FAKEMP3DATA" * 100
    partial = full[:500]
    (tmp_path / "999.mp3").write_bytes(partial)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("range"))
        if "range" in request.headers:
            # Server ignores the resume offset and sends an unrelated slice.
            return httpx.Response(
                206,
                headers={"Content-Type": "audio/mpeg", "Content-Range": "bytes 0-99/1100"},
                content=full[:100],
            )
        return httpx.Response(200, headers={"Content-Type": "audio/mpeg"}, content=full)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = download_one(client, conn, _episode(), audio_dir=tmp_path)

    assert result["status"] == "success"
    assert calls == [f"bytes={len(partial)}-", None]  # mismatched 206, then a clean un-ranged GET
    assert (tmp_path / "999.mp3").read_bytes() == full


def test_unprompted_206_on_fresh_download_is_rejected(conn, tmp_path):
    """A 206 answering a request with no Range header can only be a slice;
    accepting it would silently record a truncated file as a success."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "range" not in {k.lower() for k in request.headers}
        return httpx.Response(
            206,
            headers={"Content-Type": "audio/mpeg", "Content-Range": "bytes 0-9/100"},
            content=b"x" * 10,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = download_one(client, conn, _episode(), audio_dir=tmp_path)

    assert result["status"] == "failed"
    assert result["reason"] == "unexpected_partial_content"
    assert not (tmp_path / "999.mp3").exists()


def test_html_body_on_resume_is_failure(conn, tmp_path):
    """A range request answered with an error page must not append HTML to the audio."""
    (tmp_path / "999.mp3").write_bytes(b"FAKEMP3DATA" * 10)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "range" in request.headers
        return httpx.Response(
            206, headers={"Content-Type": "text/html"}, content=b"<html>error page</html>"
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = download_one(client, conn, _episode(), audio_dir=tmp_path)

    assert result["status"] == "failed"
    assert result["reason"] == "html_body"
    assert not (tmp_path / "999.mp3").exists()


@patch("jev_or_not.download.measure_duration_s", return_value=None)
def test_416_with_complete_file_is_trusted(_mock_duration, conn, tmp_path):
    full = b"FAKEMP3DATA" * 100
    (tmp_path / "999.mp3").write_bytes(full)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("range"))
        return httpx.Response(416, headers={"Content-Range": f"bytes */{len(full)}"})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = download_one(client, conn, _episode(), audio_dir=tmp_path)

    assert result["status"] == "success"
    assert calls == [f"bytes={len(full)}-"]  # no re-fetch: the server confirmed the size
    assert (tmp_path / "999.mp3").read_bytes() == full


@patch("jev_or_not.download.measure_duration_s", return_value=None)
def test_416_with_size_mismatch_restarts_clean(_mock_duration, conn, tmp_path):
    full = b"FAKEMP3DATA" * 100
    (tmp_path / "999.mp3").write_bytes(b"STALE" * 200)  # oversized/stale partial
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("range"))
        if "range" in request.headers:
            return httpx.Response(416, headers={"Content-Range": f"bytes */{len(full)}"})
        return httpx.Response(200, headers={"Content-Type": "audio/mpeg"}, content=full)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = download_one(client, conn, _episode(), audio_dir=tmp_path)

    assert result["status"] == "success"
    assert calls == ["bytes=1000-", None]  # ranged 416, then a clean un-ranged GET
    assert (tmp_path / "999.mp3").read_bytes() == full


@patch("jev_or_not.download.MAX_DOWNLOAD_BYTES", 1000)
def test_content_range_total_over_cap_on_206_is_rejected(conn, tmp_path):
    """A 206 whose Content-Range total exceeds the cap must be rejected even
    though its own Content-Length (just the slice) is small."""
    partial = tmp_path / "999.mp3"
    partial.write_bytes(b"x" * 100)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["range"] == "bytes=100-"
        return httpx.Response(
            206,
            headers={
                "Content-Type": "audio/mpeg",
                "Content-Range": "bytes 100-199/2000",
            },
            content=b"x" * 100,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = download_one(client, conn, _episode(), audio_dir=tmp_path)

    assert result["status"] == "failed"
    assert result["reason"] == "too_large"
    assert "Content-Range total" in result["error"]
    assert not partial.exists()


def test_content_length_over_cap_is_rejected_early(conn, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "audio/mpeg",
                "Content-Length": str(MAX_DOWNLOAD_BYTES + 1),
            },
            content=b"FAKEMP3DATA",
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = download_one(client, conn, _episode(), audio_dir=tmp_path)

    assert result["status"] == "failed"
    assert result["reason"] == "too_large"
    assert not (tmp_path / "999.mp3").exists()


@patch("jev_or_not.download.MAX_DOWNLOAD_BYTES", 1000)
def test_streamed_body_over_cap_is_rejected(conn, tmp_path):
    """No Content-Length (chunked/unknown size): the cap bites mid-stream."""

    def handler(request: httpx.Request) -> httpx.Response:
        # stream= keeps httpx from adding Content-Length, so only the
        # mid-stream counter can trip.
        return httpx.Response(
            200, headers={"Content-Type": "audio/mpeg"}, stream=httpx.ByteStream(b"x" * 2000)
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = download_one(client, conn, _episode(), audio_dir=tmp_path)

    assert result["status"] == "failed"
    assert result["reason"] == "too_large"
    assert "body exceeded cap" in result["error"]
    assert not (tmp_path / "999.mp3").exists()


@patch("jev_or_not.download.MAX_DOWNLOAD_BYTES", 1000)
def test_oversized_partial_on_disk_is_discarded(conn, tmp_path):
    """A leftover partial above the cap must not be blessed by a matching 416."""
    partial = tmp_path / "999.mp3"
    partial.write_bytes(b"x" * 1500)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "range" not in {k.lower() for k in request.headers}
        return httpx.Response(200, headers={"Content-Type": "audio/mpeg"}, content=b"y" * 10)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = download_one(client, conn, _episode(), audio_dir=tmp_path)

    assert result["status"] == "success"
    assert partial.read_bytes() == b"y" * 10


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
