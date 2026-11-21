# Extraction pilot (Phase 3 model choice)

Reference: `data/gold/pilot_reference.jsonl` v1.0.0, 30 rulings, sha256 `1023d60875ca6fa7…`. Matches frozen in `data/gold/pilot_matches.jsonl` (pooled review, see `jev_or_not.bakeoff`).

> The reference is **pooled**: it holds only rulings that a run, the scorecard, or Rex found while reviewing. Recall is measured against that inventory, not against every ruling in the audio, so it can overstate true recall.

| Run | Episodes | Rows | Verdict agreement | End-to-end correct | Subject recall | ambiguous / no_ruling | Unmatched extras |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A / sonnet | 10 | 26 | 15/21 (71.4%) | 15/30 (50.0%) | 21/30 (70.0%) | 3 / 4 | 2 |
| A / haiku | 10 | 21 | 16/18 (88.9%) | 16/30 (53.3%) | 18/30 (60.0%) | 1 / 1 | 0 |
| A2 / sonnet | 10 | 26 | 18/23 (78.3%) | 18/30 (60.0%) | 23/30 (76.7%) | 3 / 1 | 0 |
| A2 / haiku | 9 | 17 | 14/16 (87.5%) | 14/30 (46.7%) | 16/30 (53.3%) | 1 / 0 | 0 |
| B / sonnet | 10 | 30 | 22/28 (78.6%) | 22/30 (73.3%) | 28/30 (93.3%) | 3 / 3 | 0 |
| B / haiku | 10 | 20 | 16/18 (88.9%) | 16/30 (53.3%) | 18/30 (60.0%) | 2 / 0 | 0 |

## Model choice per transcriber

- **A:** sonnet
- **A2:** sonnet
- **B:** sonnet

Rule: Sonnet unless Haiku is within 2% on both verdict agreement and subject recall. With 30 rulings one ruling is 3.3 points, so the margin is below the resolution of this pilot.

## Haiku vs Sonnet disagreements

- A: 50 the squid/spider creatures in The Matrix (yes): sonnet ambiguous, haiku no_ruling
- A: 298 orcs (e.g. the Uruk-hai) (no): sonnet ambiguous, haiku missed
- A: 298 Boba Fett (no): sonnet no_ruling, haiku no
- A: 300 Shake Shack (no): sonnet yes, haiku no
- A: 350 traditional/broad sauces served on pasta (marinara, alfredo, pesto, pomodoro, cream-based, etc.) (yes): sonnet yes, haiku missed
- A: 350 cheese sauce (as on mac and cheese) (yes): sonnet no_ruling, haiku missed
- A2: 1 Mr. Roboto (no): sonnet no, haiku missed
- A2: 50 the squid/spider creatures in The Matrix (yes): sonnet no_ruling, haiku ambiguous
- A2: 298 orcs (e.g. the Uruk-hai) (no): sonnet ambiguous, haiku missed
- A2: 300 Shake Shack (no): sonnet yes, haiku no
- A2: 300 a fancy, expensive tofu pop served instantly at a fancy cafe (no): sonnet no, haiku missed
- A2: 300 UK takeaway pre-made sandwiches (e.g. a Marks & Spencer boxed sandwich) (no): sonnet no, haiku missed
- A2: 300 pizza (yes): sonnet yes, haiku no
- A2: 300 Subway (yes): sonnet ambiguous, haiku missed
- A2: 300 Five Guys (yes): sonnet ambiguous, haiku missed
- A2: 350 traditional/broad sauces served on pasta (marinara, alfredo, pesto, pomodoro, cream-based, etc.) (yes): sonnet yes, haiku missed
- B: 2 ATM (no): sonnet missed, haiku no
- B: 50 the squid/spider creatures in The Matrix (yes): sonnet no_ruling, haiku missed
- B: 298 orcs (e.g. the Uruk-hai) (no): sonnet no, haiku missed
- B: 298 a small retinue of orcs directly serving Saruman (yes): sonnet ambiguous, haiku missed
- B: 298 Boba Fett (no): sonnet no_ruling, haiku missed
- B: 300 Burger King's numbered-ticket ordering system (order, get a slip, wait for a number to be called) (yes): sonnet no_ruling, haiku missed
- B: 300 a fancy, expensive tofu pop served instantly at a fancy cafe (no): sonnet no, haiku missed
- B: 300 Starbucks and similar upscale cafes (e.g. a "frufu" cafe/bookshop serving scones) (no): sonnet no, haiku missed
- B: 300 UK takeaway pre-made sandwiches (e.g. a Marks & Spencer boxed sandwich) (no): sonnet no, haiku missed
- B: 300 pizza (yes): sonnet yes, haiku missed
- B: 350 cheese sauce (as on mac and cheese) (yes): sonnet yes, haiku missed
- B: 350 canned "tomato sauce" (i.e. crushed or pureed tomatoes sold in a can) (no): sonnet no, haiku missed

## Scorecard vs reference

- 1 The Robot (dance): scorecard no, not a ruling in the reference (rejected)
- 6 Auto-pilot: scorecard no, not a ruling in the reference (rejected)

Cost and latency: not measured. Extraction ran as Claude Code subagents under a subscription (decision 2026-09-22), so there is no per-call usage to report.
