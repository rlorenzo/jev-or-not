"""Tests for jev_or_not.extract. No subagent, no network: output.json files
are written by hand, exactly like a Claude Code subagent would write them."""

import hashlib
import json

import pytest

from jev_or_not import extract as extract_module
from jev_or_not.ledger import open_ledger
from jev_or_not.models import Episode


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


@pytest.mark.parametrize(
    "reason,overrides",
    [
        (
            "16-word quote",
            {"evidence_quotes": [{"quote": " ".join(["word"] * 16), "start_s": 1.0, "end_s": 2.0}]},
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
