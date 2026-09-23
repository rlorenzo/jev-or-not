# Pilot reference annotation procedure

Defines how `data/gold/pilot_reference.jsonl` gets built (PLAN.md Phase 0 step 4,
executed in Phase 1 step 6 once pilot audio exists). Built with the pooled
review below.

## Who and unit of annotation

The unit is **every distinct ruling in the episode**: every time John states a
yes/no (or unresolved) verdict on whether something is a robot, including
non-robot subjects the show digresses into (e.g. "Is a raft a boat?"). One
episode typically yields several `PilotReference` rows.

## Pooled review (method, from 2026-09-23)

Listening to every episode end to end and timing each ruling proved too
costly, so Rex builds the reference by **reviewing pooled findings** in
`uv run jev-or-not annotate`. This is the TREC-style pooling used when full
annotation is too expensive.

1. **Pool.** For each episode, every extraction run's rulings (candidates A,
   A2 and B × sonnet and haiku, from `data/verdicts.jsonl`) and the episode's
   scorecard subjects are merged into one list of findings, deduplicated by
   subject (`src/jev_or_not/review_pool.py`). The reviewer never sees which
   model or run found a finding; the tool shows only how many runs found it
   and whether the scorecard lists it.
2. **Review.** For each finding, Rex plays its span (the median of each run's
   latest evidence quote, ±2 s) and then chooses one of:
   - **approve**: enter it as proposed, speaker JOHN;
   - **fix**: correct the span, verdict, strength, subject or speaker, then
     enter it;
   - **reject**: not a ruling, a duplicate, or wrong.

   Scorecard-only findings have no span or proposed verdict and can only be
   fixed or rejected.
3. **Add.** Rex adds any ruling no source found (marked with `[` `]`), using
   the B transcript to skim for them.
4. **Close.** A session closes only when no finding is pending.

Every decision is logged to `data/gold/pilot_review.jsonl` (approved, fixed,
rejected or added, with the finding's run count and scorecard flag). That log
is the provenance of the pooled reference.

**What this costs.** The reference is no longer independent of the
extractors. Recall can only be measured against rulings that some run or the
scorecard found, plus the ones Rex added. The scoring report must say the
reference is pooled and report precision and recall with that caveat. An
approved finding also anchors Rex toward the proposed verdict, so he should
listen to its span before approving.

## Timestamps

`ruling_start_s` / `ruling_end_s` bound the span containing John's *final*
ruling statement on that subject — the sentence(s) where he actually says
yes/no, not the whole discussion leading up to it. If John reverses mid-episode
(see below), each distinct verdict gets its own span and its own row.

## Fields

- `speaker`: `JOHN` | `JASON` | `GUEST` — who delivers the ruling. John is the
  usual judge; mark `JASON` or `GUEST` if someone else renders the verdict
  (e.g. the episode 297 live show with Casey Liss).
- `verdict`: `yes` | `no` | `ambiguous` | `no_ruling`. `ambiguous` = a verdict
  was given but it's genuinely hedged/unclear which way it lands. `no_ruling`
  = the subject was discussed but no verdict was ever rendered.
- `verdict_strength`: `firm` | `hedged` | `reversed_during_episode` | `null`.
  `null` only when `verdict` is `no_ruling`. `reversed_during_episode` marks
  every row for a subject that gets re-judged later in the same episode
  (both the original and the reversing row get this strength).

## Reversals and multiple rulings per episode

If John changes his mind on the same subject later in the same episode,
annotate it as two (or more) separate `PilotReference` rows, each with its
own `ruling_id`, span, and verdict — do not overwrite or merge them. Both
carry `verdict_strength: reversed_during_episode` so downstream scoring can
choose to score only the final one or both, as needed.

## `ruling_id` format

`<episode_id>#<k>`, where `k` is a 1-based integer assigned in the order
rulings occur in the episode (e.g. `theincomparable/robot/6#1`,
`...#2`). Once assigned, a `ruling_id` is **frozen** — it never gets
renumbered, even if a later re-listen adds, removes, or reorders rulings.
If a ruling is later found to be wrong or split into two, record the change
as a **supersession** (new `ruling_id` referencing the old one via the
adjudication file), not a renumbering of existing IDs.

## `reference_version`

A semver string (e.g. `1.0.0`) on every row, identifying the annotation pass
that produced it. Bump it on any change to that row after the file is frozen
(correction, supersession, added row) — never edit a frozen row in place
without bumping its version.

## Distinct from scorecard labels

`data/gold/scorecard_verified.jsonl` is a separate, independently-sourced
reference (fan-scraped, robot-subjects-only). Its *subjects* join the review
pool so that rulings no run found still get reviewed, but its *verdicts* are
never shown during review and are never copied into a `PilotReference` row. If a pilot annotation disagrees with the
scorecard for the same (episode, subject), record the disagreement as an
entry in the adjudication file — do not silently reconcile them into one
label.

## Freeze rule

`data/gold/pilot_reference.jsonl` is annotated once pilot audio exists
(Phase 1 step 6) and must be **frozen before the Phase 2 bake-off gates and
the Phase 3 model comparison run** against it. After freeze, any correction
goes through the adjudication file and a `reference_version` bump, not a
silent edit.
