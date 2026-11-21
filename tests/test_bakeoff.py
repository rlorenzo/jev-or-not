"""Tests for jev_or_not.bakeoff: scoring rules, not the real pilot data."""

import json

import pytest

from jev_or_not import bakeoff, review_pool

EP = "theincomparable/robot/1"


def ref(k: int, verdict: str = "no", start: float = 10.0, end: float = 14.0) -> dict:
    return {
        "episode_id": EP,
        "ruling_id": f"{EP}#{k}",
        "subject": f"thing {k}",
        "verdict": verdict,
        "ruling_start_s": start,
        "ruling_end_s": end,
        "speaker": "JOHN",
    }


def m(k: int | None, verdict: str = "no") -> dict:
    return {"reference_ruling_id": f"{EP}#{k}" if k else None, "extracted_verdict": verdict}


def test_score_run_counts_recall_agreement_and_extras():
    reference = [ref(1), ref(2, "yes"), ref(3, "yes")]
    matches = [m(1), m(2, "ambiguous"), m(None, "no_ruling")]
    s = bakeoff.score_run(reference, matches, {EP})
    assert (s["found"], s["reference"]) == (2, 3)
    # ambiguous on a binary reference ruling is a disagreement
    assert (s["agree"], s["matched_binary"], s["binary"]) == (1, 2, 3)
    assert s["extras"] == 1
    assert s["missed"] == [f"{EP}#3"]
    assert s["wrong"] == [(f"{EP}#2", "ambiguous")]
    assert (s["ambiguous"], s["no_ruling"]) == (1, 1)


def test_score_run_split_ruling_uses_first_call_and_counts_duplicate():
    s = bakeoff.score_run([ref(1)], [m(1, "no"), m(1, "yes")], {EP})
    assert (s["agree"], s["duplicates"]) == (1, 1)


@pytest.mark.parametrize(
    ("haiku_agree", "haiku_found", "choice"),
    [(10, 10, "haiku"), (9, 10, "sonnet"), (10, 9, "sonnet")],
)
def test_model_choice_needs_haiku_close_on_both(haiku_agree, haiku_found, choice):
    sonnet = {"agree": 10, "matched_binary": 10, "found": 10, "reference": 10}
    haiku = {"agree": haiku_agree, "matched_binary": 10, "found": haiku_found, "reference": 10}
    assert bakeoff.model_choice(sonnet, haiku) == choice


def _transcript(*words) -> dict:
    return {
        "episode_id": EP,
        "segments": [
            {
                "start": w[0],
                "end": w[1],
                "speaker": w[2],
                "text": w[3],
                "words": [{"start": w[0], "end": w[1], "speaker": w[2]}],
            }
            for w in words
        ],
    }


def test_span_speaker_is_dominant_by_word_time():
    t = _transcript((9, 11, "JASON", "a"), (11, 14, "JOHN", "b"), (20, 30, "JASON", "c"))
    speaker, share = bakeoff.span_speaker(t, 10, 14)
    assert speaker == "JOHN"
    assert share == pytest.approx(0.75)
    assert bakeoff.span_speaker(t, 40, 50) == (None, 0.0)


def test_term_hits_only_expects_terms_someone_transcribed():
    a = _transcript((0, 1, "JOHN", "Mr. Roboto by Styx"))
    b = _transcript((0, 1, "JOHN", "Mr. Roboto by Sticks"))
    hits = bakeoff.term_hits({"A": {"1": a}, "B": {"1": b}})
    assert hits["A"] == {"found": 2, "expected": 2, "missed": []}
    assert hits["B"] == {"found": 1, "expected": 2, "missed": ["1: Styx"]}


def test_review_sheet_flags_misattributed_ruling():
    t = _transcript((10, 14, "JASON", "not a robot"))
    sheet = bakeoff.review_sheet(t, [ref(1)], "B")
    assert "**CHECK**" in sheet
    assert "not a robot" in sheet


def test_pool_names_members_only_when_asked():
    verdicts = [
        {
            "run": "B|claude-sonnet-subagent",
            "ruling_id": f"{EP}#0",
            "subject": "Mr. Roboto",
            "category": "robot",
            "verdict": "no",
            "verdict_strength": "firm",
            "evidence_quotes": [{"quote": "q", "start_s": 5.0, "end_s": 7.0}],
        }
    ]
    scorecard = [{"ruling_ref": "sc-1", "subject": "Mr. Roboto", "label": "no"}]
    (blind,) = review_pool.pool(EP, 300, verdicts, scorecard, 1)
    assert not blind.keys() & {"members", "scorecard_refs"}
    (c,) = review_pool.pool(EP, 300, verdicts, scorecard, 1, with_members=True)
    run = "B|claude-sonnet-subagent"
    assert c["members"] == [{"run": run, "ruling_id": f"{EP}#0", "verdict": "no"}]
    assert c["scorecard_refs"] == [{"ruling_ref": "sc-1", "label": "no"}]


def test_load_sealed_reference_refuses_edited_reference(tmp_path):
    class FakeDesk:
        reference_path = tmp_path / "ref.jsonl"

        def seal(self):
            return {"reference_sha256": "0" * 64}

    FakeDesk.reference_path.write_text(json.dumps(ref(1)) + "\n")
    with pytest.raises(RuntimeError, match="no longer matches"):
        bakeoff.load_sealed_reference(FakeDesk())
