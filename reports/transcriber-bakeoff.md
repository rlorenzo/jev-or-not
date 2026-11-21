# Transcriber bake-off (Phase 2 pilot)

Reference: `data/gold/pilot_reference.jsonl` v1.0.0, 30 live rulings, sha256 `1023d60875ca6fa7…`. Matches frozen in `data/gold/pilot_matches.jsonl` (see `jev_or_not.bakeoff`).

> The reference is **pooled**: it holds only rulings that a run, the scorecard, or Rex found while reviewing. Recall is measured against that inventory, not against every ruling in the audio, so it can overstate true recall.

## Gates (extractor: Sonnet)

| Candidate / prompt | Verdict agreement | Subject recall | Raw speaker attribution | Gates |
| --- | --- | --- | --- | --- |
| A / extract-v1 | 15/21 (71.4%) | 21/30 (70.0%) | 26/30 (86.7%) | FAIL / FAIL / FAIL |
| A2 / extract-v1 | 18/23 (78.3%) | 23/30 (76.7%) | 27/30 (90.0%) | FAIL / FAIL / FAIL |
| B / extract-v1 | 22/28 (78.6%) | 28/30 (93.3%) | 27/30 (90.0%) | FAIL / FAIL / FAIL |

Gates are 95% each (PLAN.md Phase 2). Verdict agreement is over matched binary reference rulings; an `ambiguous` or `no_ruling` call counts as a disagreement.

## Speaker attribution misses

Ruling spans whose dominant enrolled speaker (by word time) is not the reference speaker.

- **A:** 298 Chewbacca: JASON; 298 Boba Fett: JASON; 300 UK takeaway pre-made sandwiches (e.g. a Marks & Spencer boxed sandwich): None; 300 Shake Shack: JASON
- **A2:** 298 Chewbacca: JASON; 298 Boba Fett: JASON; 300 Shake Shack: JASON
- **B:** 298 Chewbacca: JASON; 298 Boba Fett: JASON; 300 Shake Shack: JASON

## Proper nouns

| Candidate | Expected terms found | Missed |
| --- | --- | --- |
| A | 24/30 (80.0%) | 1: Styx, 1: Kilroy, 300: Incomparable, 300: Burger King, 300: Starbucks, 300: Subway |
| A2 | 30/30 (100.0%) | - |
| B | 26/30 (86.7%) | 298: Saruman, 298: Uruk-hai, 300: In-N-Out, 350: Incomparable |

## Speed (as recorded in each transcript)

| Candidate | Wall clock | Audio | RTF |
| --- | --- | --- | --- |
| A | 43.6 min | 50.8 min | 0.86 |
| A2 | 18.8 min | 50.8 min | 0.37 |
| B | 11.7 min | 50.8 min | 0.23 |

## Per-run detail (Sonnet)

- **A / extract-v1:** missed 2 ATM (no), 298 a small retinue of orcs directly serving Saruman (yes), 300 Burger King's numbered-ticket ordering system (order, get a slip, wait for a number to be called) (yes), 300 a fancy, expensive tofu pop served instantly at a fancy cafe (no), 300 Starbucks and similar upscale cafes (e.g. a "frufu" cafe/bookshop serving scones) (no), 300 UK takeaway pre-made sandwiches (e.g. a Marks & Spencer boxed sandwich) (no), 300 pizza (yes), 300 Subway (yes), 350 canned "tomato sauce" (i.e. crushed or pureed tomatoes sold in a can) (no); wrong verdict 50 the squid/spider creatures in The Matrix (yes) -> ambiguous, 298 orcs (e.g. the Uruk-hai) (no) -> ambiguous, 298 Boba Fett (no) -> no_ruling, 300 Five Guys (yes) -> ambiguous, 300 Shake Shack (no) -> yes, 350 cheese sauce (as on mac and cheese) (yes) -> no_ruling; 2 unmatched extra rulings.
- **A2 / extract-v1:** missed 2 ATM (no), 298 a small retinue of orcs directly serving Saruman (yes), 298 Boba Fett (no), 300 Burger King's numbered-ticket ordering system (order, get a slip, wait for a number to be called) (yes), 300 Starbucks and similar upscale cafes (e.g. a "frufu" cafe/bookshop serving scones) (no), 350 cheese sauce (as on mac and cheese) (yes), 350 canned "tomato sauce" (i.e. crushed or pureed tomatoes sold in a can) (no); wrong verdict 50 the squid/spider creatures in The Matrix (yes) -> no_ruling, 298 orcs (e.g. the Uruk-hai) (no) -> ambiguous, 300 Subway (yes) -> ambiguous, 300 Five Guys (yes) -> ambiguous, 300 Shake Shack (no) -> yes; 0 unmatched extra rulings.
- **B / extract-v1:** missed 2 ATM (no), 350 traditional/broad sauces served on pasta (marinara, alfredo, pesto, pomodoro, cream-based, etc.) (yes); wrong verdict 50 the squid/spider creatures in The Matrix (yes) -> no_ruling, 298 a small retinue of orcs directly serving Saruman (yes) -> ambiguous, 298 Boba Fett (no) -> no_ruling, 300 Burger King's numbered-ticket ordering system (order, get a slip, wait for a number to be called) (yes) -> no_ruling, 300 Subway (yes) -> ambiguous, 300 Five Guys (yes) -> ambiguous; 0 unmatched extra rulings.

Review sheets: `local/review/{episode}-{candidate}.md` (local only).
