"""Tests for jev_or_not.extract. No subagent, no network: output.json files
are written by hand, exactly like a Claude Code subagent would write them."""

import hashlib
import json

import pytest

from jev_or_not import extract as extract_module
from jev_or_not.ledger import open_ledger
from jev_or_not.models import MAX_QUOTE_WORDS, Episode, EvidenceQuote


def _episode(**overrides) -> Episode:
    fields = {
        "schema_version": 1,
        "episode_id": "theincomparable/robot/6",
        "episode": 6,
        "episode_label": "6",
        "title": "6: Self-Driving Cars",
        "released_at": "2015-06-01T00:00:00+00:00",
        "description": "",
        "show_notes_url": "https://www.theincomparable.com/robot/6/",
        "audio_url": "https://example.com/robot6.mp3",
        "duration_s": 300,
        "source": "feed",
        "retrieved_at": "2026-01-01T00:00:00+00:00",
    }
    fields.update(overrides)
    return Episode(**fields)


def _write_episodes(path, *episodes):
    path.write_text("\n".join(e.model_dump_json() for e in episodes) + "\n")


def _valid_ruling(**overrides) -> dict:
    ruling = {
        "subject": "a Roomba",
        "category": "robot",
        "question_as_posed": "Is a Roomba a robot?",
        "verdict": "yes",
        "verdict_strength": "firm",
        "evidence_quotes": [
            {"quote": "It senses, decides, and acts.", "start_s": 10.0, "end_s": 12.0}
        ],
        "reasoning_summary": "It senses its environment and decides how to act without control.",
        "extractor_confidence": 0.9,
    }
    ruling.update(overrides)
    return ruling


@pytest.fixture
def conn():
    c = open_ledger(":memory:")
    yield c
    c.close()


def test_prepare_renders_speakers_and_writes_packet_json(tmp_path):
    episode = _episode()
    _write_episodes(tmp_path / "episodes.jsonl", episode)

    transcript = {
        "segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "cluster": "SPEAKER_00",
                "speaker": "JOHN",
                "text": "Is it a robot?",
            },
            {
                "start": 2.0,
                "end": 4.0,
                "cluster": "SPEAKER_01",
                "text": "No idea.",
            },  # no `speaker` -> UNKNOWN
        ]
    }
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    transcript_path = transcripts_dir / "6-B.json"
    transcript_path.write_text(json.dumps(transcript))

    index_path = tmp_path / "transcript_index.jsonl"
    index_path.write_text(
        json.dumps(
            {
                "episode_id": episode.episode_id,
                "episode_label": episode.episode_label,
                "candidate": "B",
                "transcript_path": str(transcript_path),
                "transcript_hash": "abc123",
            }
        )
        + "\n"
    )

    prompt_path = tmp_path / "extract.md"
    prompt_path.write_text("prompt_version: extract-v1\n\nExtract every ruling.\n")

    packets_dir = tmp_path / "packets"
    summary = extract_module.prepare(
        "B",
        "sonnet",
        packets_dir=packets_dir,
        prompt_path=prompt_path,
        enrolled_index_path=tmp_path / "no_enrolled_index.jsonl",
        transcript_index_path=index_path,
        episodes_path=tmp_path / "episodes.jsonl",
    )

    assert len(summary["packets"]) == 1
    packet_md_path = packets_dir / "6-B-sonnet" / "packet.md"
    assert str(packet_md_path) == summary["packets"][0]

    packet_md = packet_md_path.read_text()
    assert "[00:00-00:02] JOHN: Is it a robot?" in packet_md
    assert "[00:02-00:04] UNKNOWN: No idea." in packet_md
    assert "UNKNOWN or GUEST" in packet_md  # note about unresolved speakers
    assert "Extract every ruling." in packet_md  # prompt verbatim
    output_path = packets_dir / "6-B-sonnet" / "output.json"
    assert str(output_path) in packet_md

    packet_meta = json.loads((packets_dir / "6-B-sonnet" / "packet.json").read_text())
    assert packet_meta["episode_id"] == episode.episode_id
    assert packet_meta["candidate"] == "B"
    assert packet_meta["model"] == "sonnet"
    assert packet_meta["prompt_version"] == "extract-v1"
    assert packet_meta["transcript_hash"] == "abc123"
    assert packet_meta["packet_hash"] == hashlib.sha256(packet_md.encode()).hexdigest()


def _packet(tmp_path, output: list[dict] | None, **packet_meta_overrides):
    packet_dir = tmp_path / "6-B-sonnet"
    packet_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "episode_id": "theincomparable/robot/6",
        "candidate": "B",
        "model": "sonnet",
        "prompt_version": "extract-v1",
        "transcript_hash": "abc123",
        "packet_hash": "deadbeef",
        "prepared_at": "2026-01-01T00:00:00+00:00",
    }
    meta.update(packet_meta_overrides)
    (packet_dir / "packet.json").write_text(json.dumps(meta))
    if output is not None:
        (packet_dir / "output.json").write_text(json.dumps(output))
    return packet_dir


def test_ingest_accepts_valid_output(conn, tmp_path):
    packet_dir = _packet(tmp_path, [_valid_ruling()])
    episodes_by_id = {"theincomparable/robot/6": _episode()}

    result = extract_module.ingest_one(
        conn, packet_dir, episodes_by_id, llm_log_path=tmp_path / "llm_log.jsonl"
    )

    assert result["status"] == "success"
    assert result["rulings"] == 1
    assert (packet_dir / "rulings.json").exists()
    log_lines = (tmp_path / "llm_log.jsonl").read_text().splitlines()
    assert len(log_lines) == 1
    log_entry = json.loads(log_lines[0])
    assert log_entry["phase"] == "extract"
    assert log_entry["model_alias"] == "claude-sonnet-subagent"

    verdicts = extract_module.export_verdicts(conn, tmp_path / "verdicts.jsonl")
    assert len(verdicts) == 1
    assert verdicts[0].ruling_id == "theincomparable/robot/6#0"
    assert verdicts[0].extractor_model == "claude-sonnet-subagent"
    assert (tmp_path / "verdicts.jsonl").exists()


def test_evidence_quote_accepts_up_to_the_word_cap():
    EvidenceQuote(quote=" ".join(["word"] * MAX_QUOTE_WORDS), start_s=1.0, end_s=2.0)
    with pytest.raises(ValueError, match="at most"):
        EvidenceQuote(quote=" ".join(["word"] * (MAX_QUOTE_WORDS + 1)), start_s=1.0, end_s=2.0)


@pytest.mark.parametrize(
    "reason,overrides",
    [
        (
            "over-cap quote",
            {
                "evidence_quotes": [
                    {
                        "quote": " ".join(["word"] * (MAX_QUOTE_WORDS + 1)),
                        "start_s": 1.0,
                        "end_s": 2.0,
                    }
                ]
            },
        ),
        (
            "4th quote",
            {
                "evidence_quotes": [
                    {"quote": "First short quote here.", "start_s": 1.0, "end_s": 2.0},
                    {"quote": "Second short quote here.", "start_s": 3.0, "end_s": 4.0},
                    {"quote": "Third short quote here.", "start_s": 5.0, "end_s": 6.0},
                    {"quote": "Fourth short quote here.", "start_s": 7.0, "end_s": 8.0},
                ]
            },
        ),
        ("bad verdict enum", {"verdict": "maybe"}),
        ("confidence 1.2", {"extractor_confidence": 1.2}),
    ],
)
def test_ingest_rejects_invalid_output(conn, tmp_path, reason, overrides):
    packet_dir = _packet(tmp_path, [_valid_ruling(**overrides)])
    episodes_by_id = {"theincomparable/robot/6": _episode()}

    result = extract_module.ingest_one(
        conn, packet_dir, episodes_by_id, llm_log_path=tmp_path / "llm_log.jsonl"
    )

    assert result["status"] == "failed", reason
    assert not (packet_dir / "rulings.json").exists()

    verdicts = extract_module.export_verdicts(conn, tmp_path / "verdicts.jsonl")
    assert verdicts == []


def test_ingest_rerun_of_success_is_skip(conn, tmp_path):
    packet_dir = _packet(tmp_path, [_valid_ruling()])
    episodes_by_id = {"theincomparable/robot/6": _episode()}

    first = extract_module.ingest_one(
        conn, packet_dir, episodes_by_id, llm_log_path=tmp_path / "llm_log.jsonl"
    )
    second = extract_module.ingest_one(
        conn, packet_dir, episodes_by_id, llm_log_path=tmp_path / "llm_log.jsonl"
    )

    assert first["status"] == "success"
    assert second["status"] == "skipped"


def test_ingest_of_rewritten_output_replaces_earlier_rulings(conn, tmp_path):
    packet_dir = _packet(tmp_path, [_valid_ruling()])
    episodes_by_id = {"theincomparable/robot/6": _episode()}
    log_path = tmp_path / "llm_log.jsonl"

    first = extract_module.ingest_one(conn, packet_dir, episodes_by_id, llm_log_path=log_path)
    # A re-run subagent rewrites output.json after the first ingest succeeded.
    (packet_dir / "output.json").write_text(
        json.dumps([_valid_ruling(subject="a Dalek"), _valid_ruling(subject="a Roomba")])
    )
    conn.execute("UPDATE tasks SET updated_at = '2000-01-01 00:00:00'")  # first is older
    second = extract_module.ingest_one(conn, packet_dir, episodes_by_id, llm_log_path=log_path)

    assert first["status"] == "success"
    assert second["status"] == "success"
    verdicts = extract_module.export_verdicts(conn, tmp_path / "verdicts.jsonl")
    assert [v.subject for v in verdicts] == ["a Dalek", "a Roomba"]  # no duplicate #0


def test_index_rows_falls_back_per_candidate(tmp_path):
    enrolled = tmp_path / "enrolled_index.jsonl"
    enrolled.write_text(
        json.dumps(
            {
                "episode_id": "e1",
                "episode_label": "1",
                "candidate": "A",
                "enrolled_path": "a.json",
                "enrolled_hash": "ha",
            }
        )
        + "\n"
    )
    transcripts = tmp_path / "transcript_index.jsonl"
    transcripts.write_text(
        json.dumps(
            {
                "episode_id": "e1",
                "episode_label": "1",
                "candidate": "B",
                "transcript_path": "b.json",
                "transcript_hash": "hb",
            }
        )
        + "\n"
    )

    assert [r["path"] for r in extract_module._index_rows("A", enrolled, transcripts)] == ["a.json"]
    assert [r["path"] for r in extract_module._index_rows("B", enrolled, transcripts)] == ["b.json"]


@pytest.mark.parametrize(
    "start_s,end_s,duration_s,ok",
    [
        (299.0, 300.9, 300, True),  # measured audio outruns the whole-second feed duration
        (299.0, 303.0, 300, False),
        (-1.0, 1.0, None, False),  # negative start rejected even without a duration
        (1.0, 5000.0, None, True),
    ],
)
def test_validate_timestamps(start_s, end_s, duration_s, ok):
    quotes = [EvidenceQuote(quote="Short quote.", start_s=start_s, end_s=end_s)]
    if ok:
        extract_module._validate_timestamps(quotes, duration_s)
    else:
        with pytest.raises(ValueError):
            extract_module._validate_timestamps(quotes, duration_s)


def test_ingest_bad_packet_meta_is_failure_not_crash(conn, tmp_path):
    packet_dir = _packet(tmp_path, [_valid_ruling()])
    (packet_dir / "packet.json").write_text("{not json")

    result = extract_module.ingest_one(conn, packet_dir, {}, llm_log_path=tmp_path / "log.jsonl")

    assert result["status"] == "failed"
    assert result["reason"] == "bad_packet_meta"


def test_ingest_packet_meta_missing_keys_is_failure_not_crash(conn, tmp_path):
    packet_dir = _packet(tmp_path, [_valid_ruling()])
    (packet_dir / "packet.json").write_text("{}")

    result = extract_module.ingest_one(conn, packet_dir, {}, llm_log_path=tmp_path / "log.jsonl")

    assert result["status"] == "failed"
    assert result["reason"] == "bad_packet_meta"


def test_index_rows_missing_indexes_is_empty(tmp_path):
    assert extract_module._index_rows("A", tmp_path / "enrolled.jsonl", tmp_path / "t.jsonl") == []
