# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Python 3.14 CLI (`uv run jev-or-not …`, typer + pydantic, no web framework). Browser tools are served by a stdlib HTTP server on localhost from a CLI subcommand, with a single-page UI and no new dependencies (Rex's choice, 2026-09-22).

## Users

One person: Rex, the project owner, working alone at a Mac with headphones. The annotation tool's job is to build the pilot reference for 10 pilot episodes of *Robot or Not?* by reviewing pooled findings. Rex approves, fixes or rejects each candidate ruling against its audio span and adds any ruling the pool missed. Listening to all 51 minutes was too costly (Rex, 2026-09-23). The same person later uses the tool to adjudicate disagreements and review extraction results.

## Product Purpose

jev-or-not is a reproducible evaluation of TypeSafe AI's Jev against John Siracusa's rulings on *Robot or Not?*. The pipeline transcribes episodes (bake-off candidates A, A2, B), labels speakers, extracts rulings with LLM subagents, and scores the extractions against a hand-built reference. The reference (`data/gold/pilot_reference.jsonl`) is the ground truth everything else is measured against, so it has to be careful, provenanced, and frozen before scoring. It is a pooled reference (see the annotation procedure), and scores must say so.

## Operating Context

- The procedure is fixed in `docs/annotation_procedure.md` (pooled review). Findings are blind: which model or run found a finding is never shown, only how many runs did. Scorecard verdicts stay hidden until the seal. Every decision is logged in `data/gold/pilot_review.jsonl`.
- A reference row is a `PilotReference` (`src/jev_or_not/models.py`): `ruling_id` `<episode_id>#k` (frozen once assigned), subject, category, verdict (yes/no/ambiguous/no_ruling), verdict_strength (firm/hedged/reversed_during_episode/null), `ruling_start_s`/`ruling_end_s` around John's *final* verdict sentence, speaker (JOHN/JASON/GUEST), annotator, annotated_at, reference_version.
- Scorecard (`data/gold/scorecard_verified.jsonl`) subjects join the review pool, but its verdicts stay hidden until the seal. Once the reference is frozen, disagreements with the scorecard go to `data/gold/adjudication.jsonl`. They are never silently reconciled.
- After freezing, any change is a supersession plus a `reference_version` bump. Rows are never edited in place.
- Audio lives in `local/audio/<n>.mp3` (gitignored, copyrighted). Transcripts and extraction outputs are under `local/`.

## Capabilities and Constraints

- Local-only, single user, no auth, localhost-bound. Nothing is published, since the audio is copyrighted.
- Writes go through pydantic validation, and files are written atomically (`write_lines_atomic`).
- Scope: pooled review (approve / fix / reject / add), freeze, scorecard-disagreement adjudication after freeze, and extraction review comparing candidates A/A2/B × sonnet/haiku against the reference.

## Evidence on Hand

10 pilot episodes (`data/gold/pilot_episodes.json`), 4–7 minutes each. The shortest is 257 s, the longest about 11 minutes. Episodes carry 1–5 rulings typically, and some have none on robots at all (non-robot subjects such as boats and sauces). Real transcripts exist for three candidates. There is no annotation data yet.

## Product Principles

- Blind pooling: no finding is favoured by who found it, and no scorecard verdict is shown before the seal.
- Provenance over convenience: every saved fact is validated, versioned, and traceable, and frozen data is never mutated.
- Keyboard-first and fast: review time is the cost, so approving, fixing or rejecting a finding is one key after hearing its span.
