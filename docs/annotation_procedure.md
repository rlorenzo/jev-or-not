# Pilot reference annotation procedure

Defines how `data/gold/pilot_reference.jsonl` gets built (PLAN.md Phase 0 step 4,
executed in Phase 1 step 6 once pilot audio exists). This is a **definition
only** — no annotation runs yet.

## Who and unit of annotation

Rex annotates by listening to each pilot episode directly (not from a
transcript, to avoid transcription errors biasing the reference). The unit is
**every distinct ruling in the episode** — every time John states a yes/no
(or unresolved) verdict on whether something is a robot, including
non-robot subjects the show digresses into (e.g. "Is a raft a boat?"). One
episode typically yields multiple `PilotReference` rows.

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
reference (fan-scraped, robot-subjects-only). Never copy a scorecard verdict
into a `PilotReference` row, and never let the scorecard influence the
annotation while listening. If a pilot annotation disagrees with the
scorecard for the same (episode, subject), record the disagreement as an
entry in the adjudication file — do not silently reconcile them into one
label.

## Freeze rule

`data/gold/pilot_reference.jsonl` is annotated once pilot audio exists
(Phase 1 step 6) and must be **frozen before the Phase 2 bake-off gates and
the Phase 3 model comparison run** against it. After freeze, any correction
goes through the adjudication file and a `reference_version` bump, not a
silent edit.
