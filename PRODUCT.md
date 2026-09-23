# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Python 3.14 CLI (`uv run jev-or-not …`, typer + pydantic, no web framework). Browser tools are served by a stdlib HTTP server on localhost from a CLI subcommand, with a single-page UI and no new dependencies (Rex's choice, 2026-09-22).

## Users

One person: Rex, the project owner, working alone at a Mac with headphones. The annotation tool's job is to build the pilot reference by listening to about 51 minutes of *Robot or Not?* audio across 10 pilot episodes and marking every distinct ruling John Siracusa gives. The same person later uses the tool to adjudicate disagreements and review extraction results.

## Product Purpose

jev-or-not is a reproducible evaluation of TypeSafe AI's Jev against John Siracusa's rulings on *Robot or Not?*. The pipeline transcribes episodes (bake-off candidates A, A2, B), labels speakers, extracts rulings with LLM subagents, and scores the extractions against a hand-built reference. The reference (`data/gold/pilot_reference.jsonl`) is the ground truth everything else is measured against, so it has to be independent, careful, and frozen before scoring.

## Operating Context

- The procedure is fixed in `docs/annotation_procedure.md`. Rex annotates by listening, not reading, so transcription errors can't bias the reference. Transcripts may be shown only after an episode's rulings are marked.
- A reference row is a `PilotReference` (`src/jev_or_not/models.py`): `ruling_id` `<episode_id>#k` (frozen once assigned), subject, category, verdict (yes/no/ambiguous/no_ruling), verdict_strength (firm/hedged/reversed_during_episode/null), `ruling_start_s`/`ruling_end_s` around John's *final* verdict sentence, speaker (JOHN/JASON/GUEST), annotator, annotated_at, reference_version.
- The scorecard (`data/gold/scorecard_verified.jsonl`) must stay hidden while Rex annotates. Once the reference is frozen, disagreements with the scorecard go to `data/gold/adjudication.jsonl`. They are never silently reconciled.
- After freezing, any change is a supersession plus a `reference_version` bump. Rows are never edited in place.
- Audio lives in `local/audio/<n>.mp3` (gitignored, copyrighted). Transcripts and extraction outputs are under `local/`.

## Capabilities and Constraints

- Local-only, single user, no auth, localhost-bound. Nothing is published, since the audio is copyrighted.
- Writes go through pydantic validation, and files are written atomically (`write_lines_atomic`).
- Planned scope: annotation (audio-first), freeze, scorecard-disagreement adjudication after freeze, and extraction review comparing candidates A/A2/B × sonnet/haiku against the reference.

## Evidence on Hand

10 pilot episodes (`data/gold/pilot_episodes.json`), 4–7 minutes each. The shortest is 257 s, the longest about 11 minutes. Episodes carry 1–5 rulings typically, and some have none on robots at all (non-robot subjects such as boats and sauces). Real transcripts exist for three candidates. There is no annotation data yet.

## Product Principles

- The reference is independent: nothing the tool shows while Rex listens may leak a transcript, the scorecard, or an extraction.
- Provenance over convenience: every saved fact is validated, versioned, and traceable, and frozen data is never mutated.
- Keyboard-first and fast: listening time is the cost, so marking must never pull attention away from the audio.
