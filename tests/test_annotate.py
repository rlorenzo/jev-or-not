"""Tests for jev_or_not.annotate: docket rules, not the HTTP layer."""

import json
import subprocess
import threading
import urllib.error
import urllib.request
from array import array
from pathlib import Path

import pytest
from pydantic import ValidationError

from jev_or_not import annotate
from jev_or_not.annotate import Desk, DeskError
from jev_or_not.models import PilotReference

EP1 = "theincomparable/robot/1"
EP2 = "theincomparable/robot/2"


@pytest.fixture
def desk(tmp_path: Path) -> Desk:
    gold = tmp_path / "gold"
    gold.mkdir()
    (gold / "pilot_episodes.json").write_text(
        json.dumps(
            {
                "episodes": [
                    {"episode_id": EP1, "episode": 1, "title": "1: Mr. Roboto", "duration_s": 311},
                    {"episode_id": EP2, "episode": 2, "title": "2: Kiosks", "duration_s": 257},
                ]
            }
        )
    )
    (gold / "scorecard.jsonl").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "ruling_ref": "sc-1-mr-roboto",
                "episode_id": EP1,
                "episode": 1,
                "subject": "Mr. Roboto",
                "label": "no",
                "source_row_ids": ["row-1"],
                "review_status": "auto",
            }
        )
        + "\n"
    )
    return Desk(
        pilot_path=gold / "pilot_episodes.json",
        reference_path=gold / "pilot_reference.jsonl",
        seal_path=gold / "pilot_reference.seal.json",
        adjudication_path=gold / "adjudication.jsonl",
        scorecard_path=gold / "scorecard.jsonl",
        verdicts_path=tmp_path / "verdicts.jsonl",
        enrolled_index_path=tmp_path / "enrolled_index.jsonl",
        transcript_index_path=tmp_path / "transcript_index.jsonl",
        manifest_path=tmp_path / "audio_manifest.jsonl",
        state_path=tmp_path / "local" / "state.json",
        peaks_dir=tmp_path / "local" / "peaks",
    )


def _entry(**overrides) -> dict:
    entry = {
        "subject": "Mr. Roboto",
        "category": "robot",
        "verdict": "no",
        "verdict_strength": "firm",
        "ruling_start_s": 290.0,
        "ruling_end_s": 300.5,
        "speaker": "JOHN",
    }
    entry.update(overrides)
    return entry


def _close_all(desk: Desk) -> None:
    desk.close_session(1)
    desk.close_session(2, no_rulings=True)


def test_entries_get_frozen_numbers_that_are_never_reused(desk):
    first = desk.save_entry(1, _entry())
    second = desk.save_entry(1, _entry(subject="Kilroy", ruling_start_s=10, ruling_end_s=12))
    assert [first["ruling_id"], second["ruling_id"]] == [f"{EP1}#1", f"{EP1}#2"]

    desk.delete_entry(1, second["ruling_id"])
    third = desk.save_entry(1, _entry(subject="Styx"))
    assert third["ruling_id"] == f"{EP1}#3"  # #2 is retired, not reissued


def test_update_keeps_the_ruling_id(desk):
    saved = desk.save_entry(1, _entry())
    updated = desk.save_entry(1, _entry(ruling_id=saved["ruling_id"], verdict="yes"))
    assert updated["ruling_id"] == saved["ruling_id"]
    assert [r.verdict for r in desk.rows()] == ["yes"]


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"ruling_end_s": 280.0}, "greater than ruling_start_s"),
        ({"verdict_strength": None}, "verdict_strength is required"),
        ({"subject": "  "}, "subject is required"),
        ({"ruling_end_s": 999.0}, "outside the episode"),
        ({"speaker": "NARRATOR"}, "speaker"),
    ],
)
def test_invalid_entries_are_refused(desk, overrides, message):
    with pytest.raises(DeskError, match=message):
        desk.save_entry(1, _entry(**overrides))
    assert desk.rows() == []


def test_entries_belong_to_their_session(desk):
    saved = desk.save_entry(1, _entry())
    with pytest.raises(DeskError, match="no entry"):
        desk.delete_entry(2, saved["ruling_id"])
    with pytest.raises(DeskError, match="no entry"):
        desk.save_entry(2, _entry(ruling_id=saved["ruling_id"]))
    assert len(desk.rows()) == 1


def test_lost_session_state_does_not_reissue_numbers(desk):
    desk.save_entry(1, _entry())
    desk.state_path.unlink()
    assert desk.save_entry(1, _entry(subject="Styx"))["ruling_id"] == f"{EP1}#2"


def test_pilot_reference_pairs_strength_with_verdict():
    base = {
        "schema_version": 1,
        "episode_id": EP1,
        "ruling_id": f"{EP1}#1",
        "subject": "Mr. Roboto",
        "category": "robot",
        "ruling_start_s": 1.0,
        "ruling_end_s": 2.0,
        "speaker": "JOHN",
        "annotator": "x",
        "annotated_at": "2026-01-01T00:00:00Z",
        "reference_version": "1.0.0",
    }
    PilotReference(**base, verdict="no_ruling", verdict_strength=None)
    for verdict, strength in (("yes", None), ("no_ruling", "firm")):
        with pytest.raises(ValidationError, match="verdict_strength is required"):
            PilotReference(**base, verdict=verdict, verdict_strength=strength)


def test_peaks_are_max_abs_per_bucket_and_cached(desk, tmp_path, monkeypatch):
    audio = tmp_path / "1.mp3"
    audio.write_bytes(b"mp3")
    desk.manifest_path.write_text(
        json.dumps({"episode_id": EP1, "status": "success", "file_path": str(audio)}) + "\n"
    )
    step = annotate.PEAKS_RATE_HZ // annotate.PEAKS_PER_S
    samples = array("h", [0] * step + [-16384] + [0] * (step - 1) + [8192])
    calls = []

    def fake_run(argv, **_):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=samples.tobytes())

    monkeypatch.setattr(annotate.subprocess, "run", fake_run)
    assert desk.peaks(1)["peaks"] == [0.0, 0.5, 0.25]
    assert desk.peaks(1)["peaks"] == [0.0, 0.5, 0.25]
    assert len(calls) == 1  # second call served from the cache
    with pytest.raises(DeskError, match="no audio"):
        desk.peaks(2)


def test_no_ruling_drops_strength(desk):
    saved = desk.save_entry(1, _entry(verdict="no_ruling", verdict_strength="firm"))
    assert saved["verdict_strength"] is None


def test_transcript_is_withheld_until_the_session_closes(desk, tmp_path):
    enrolled = tmp_path / "1-B.json"
    enrolled.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 0.0, "end": 2.0, "cluster": "S0", "speaker": "JOHN", "text": " Hi "}
                ],
            }
        )
    )
    desk.enrolled_index_path.write_text(
        json.dumps(
            {
                "episode_id": EP1,
                "episode_label": "1",
                "candidate": "B",
                "enrolled_path": str(enrolled),
                "enrolled_hash": "h1",
            }
        )
        + "\n"
    )
    desk.save_entry(1, _entry())
    assert desk.session(1)["transcript"] is None

    desk.close_session(1)
    assert desk.session(1)["transcript"] == [
        {"start": 0.0, "end": 2.0, "speaker": "JOHN", "text": "Hi"}
    ]


def test_closing_an_empty_session_needs_confirmation(desk):
    with pytest.raises(DeskError, match="no rulings"):
        desk.close_session(2)
    desk.close_session(2, no_rulings=True)
    assert desk.session(2)["no_rulings"] is True


def test_closed_session_refuses_edits_until_reopened(desk):
    desk.save_entry(1, _entry())
    desk.close_session(1)
    with pytest.raises(DeskError, match="closed"):
        desk.save_entry(1, _entry(subject="Other"))
    desk.reopen_session(1)
    desk.save_entry(1, _entry(subject="Other"))


def test_seal_requires_every_session_closed(desk):
    desk.save_entry(1, _entry())
    desk.close_session(1)
    with pytest.raises(DeskError, match=r"still open: \[2\]"):
        desk.seal_record()


def test_sealed_record_is_read_only_and_corrections_supersede(desk):
    old = desk.save_entry(1, _entry())
    _close_all(desk)
    seal = desk.seal_record()
    assert seal["reference_version"] == "1.0.0"
    assert seal["no_ruling_sessions"] == [2]

    with pytest.raises(DeskError, match="sealed"):
        desk.save_entry(1, _entry(ruling_id=old["ruling_id"], verdict="yes"))
    with pytest.raises(DeskError, match="reason"):
        desk.supersede(1, old["ruling_id"], _entry(verdict="yes"), "")

    new = desk.supersede(1, old["ruling_id"], _entry(verdict="yes"), "re-listened")
    assert new["ruling_id"] == f"{EP1}#2"
    assert new["reference_version"] == "1.1.0"
    # The frozen row is untouched; the link lives in the adjudication file.
    assert [r.verdict for r in desk.rows()] == ["no", "yes"]
    adjudication = [json.loads(line) for line in desk.adjudication_path.read_text().splitlines()]
    assert adjudication[-1]["field"] == "superseded_by"
    assert adjudication[-1]["source_row_id"] == old["ruling_id"]
    assert adjudication[-1]["new"] == new["ruling_id"]
    assert desk.seal()["reference_version"] == "1.1.0"
    assert desk.session(1)["entries"][0]["superseded_by"] == new["ruling_id"]

    with pytest.raises(DeskError, match="already superseded"):
        desk.supersede(1, old["ruling_id"], _entry(), "again")


def test_scorecard_and_extractions_unlock_only_after_seal(desk):
    with pytest.raises(DeskError, match="sealed"):
        desk.scorecard(1)
    with pytest.raises(DeskError, match="sealed"):
        desk.extractions(1)

    entry = desk.save_entry(1, _entry())
    _close_all(desk)
    desk.seal_record()
    assert [r["ruling_ref"] for r in desk.scorecard(1)["rulings"]] == ["sc-1-mr-roboto"]

    logged = desk.record_disagreement(
        1, {"ruling_ref": "sc-1-mr-roboto", "ruling_id": entry["ruling_id"], "reason": "test"}
    )
    # Not an adjudicable field, so the scorecard itself is unchanged.
    assert logged["field"] == "pilot_disagreement"
    assert logged["old"] == "no" and logged["new"] == "no"
    assert desk.scorecard(1)["disagreements"][0]["evidence"] == entry["ruling_id"]


def test_extractions_group_by_candidate_and_model(desk):
    desk.enrolled_index_path.write_text(
        json.dumps({"episode_id": EP1, "candidate": "A2", "enrolled_hash": "h1"}) + "\n"
    )
    desk.verdicts_path.write_text(
        json.dumps(
            {
                "episode_id": EP1,
                "transcript_hash": "h1",
                "extractor_model": "claude-haiku-subagent",
                "subject": "Mr. Roboto",
                "category": "robot",
                "verdict": "no",
                "verdict_strength": "firm",
                "extractor_confidence": 0.9,
                "evidence_quotes": [],
            }
        )
        + "\n"
    )
    desk.save_entry(1, _entry())
    _close_all(desk)
    desk.seal_record()
    assert list(desk.extractions(1)["runs"]) == ["A2 · haiku"]


def test_http_routes_and_refusals(desk):
    server = annotate.serve(0, desk)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Direct connection: a system proxy must not intercept 127.0.0.1 here.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        ok = opener.open(f"http://127.0.0.1:{port}/api/overview")
        assert json.loads(ok.read())["reference_version"] == "1.0.0"

        foreign = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/overview", headers={"Host": "evil.example"}
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            opener.open(foreign)
        assert exc.value.code == 403

        form_post = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/seal",
            data=b"x=1",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            opener.open(form_post)
        assert exc.value.code == 415

        def post(path: str, body: dict) -> dict:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            return json.loads(opener.open(req).read())

        saved = post("/api/sessions/1/entries", _entry())
        assert saved["ruling_id"] == f"{EP1}#1"
        assert post("/api/sessions/1/close", {}) == {"ok": True}
        with pytest.raises(urllib.error.HTTPError) as exc:
            post("/api/sessions/1/entries", _entry())
        assert exc.value.code == 409  # DeskError status is passed through

        font = opener.open(f"http://127.0.0.1:{port}/fonts/public-sans-latin-wght-normal.woff2")
        assert font.headers["Content-Type"] == "font/woff2"
        for missing in ("/fonts/nope.woff2", "/api/nope"):
            with pytest.raises(urllib.error.HTTPError) as exc:
                opener.open(f"http://127.0.0.1:{port}{missing}")
            assert exc.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
