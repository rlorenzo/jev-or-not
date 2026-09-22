<!-- markdownlint-disable-next-line MD041 -->
prompt_version: extract-v1

# Verdict extraction: Robot or Not?

You are extracting John Siracusa's rulings from a transcript of one episode
of *Robot or Not?* (The Incomparable, hosted by John Siracusa and Jason
Snell). Segments are labeled `JOHN`, `JASON`, `GUEST`, or `UNKNOWN` (speaker
identity not yet resolved) and carry `[mm:ss-mm:ss]` timestamps.

An episode can contain several distinct rulings (e.g. self-driving cars,
Johnny Cab, drones, cruise missiles, and autopilot all in one episode). Not
every episode is about robots: some judge whether something is a boat, a
sauce, a video game, etc. Extract every distinct ruling you find, whatever
the category.

## Rules

- **Only John's final ruling counts.** Jason's opinions and jokes are not
  verdicts. Ignore anything said only by `JASON` or `GUEST` when deciding the
  verdict; use it only as context for what was asked.
- If John changes his mind mid-episode, record his *final* position as the
  verdict, and set `verdict_strength` to `"reversed_during_episode"`.
- Use `"ambiguous"` only when John states an explicit conditional or
  genuinely indeterminate judgment ("it depends on X"), not when he is just
  hedging on a firm lean.
- Use `"no_ruling"` when John declines to rule or the episode ends before he
  reaches a judgment. Do not force a yes/no onto a subject he never decided.
- `verdict_strength` is `"firm"` for a plain, undecorated ruling, `"hedged"`
  for a ruling he immediately qualifies or expresses low confidence in, and
  `null` when `verdict` is `"no_ruling"`.
- `subject` is the specific thing being judged (e.g. "a Roomba", "Johnny
  Cab"). `category` is the general concept it's judged against (e.g.
  "robot", "boat", "sauce", "video game").
- `question_as_posed` is a short, neutral paraphrase of how the question was
  originally asked (by a listener or by Jason), before John's deliberation.
- Up to 3 `evidence_quotes` per ruling, each a direct quote of under 15
  whitespace-delimited words from the transcript, with its start/end
  timestamps in seconds. Prefer the quote(s) that most directly state the
  verdict and John's reasoning. Zero quotes is acceptable if none is short
  enough to lift verbatim.
- `reasoning_summary`: 1-2 sentences, in your own words (not a quote),
  describing John's stated criteria for this ruling.
- `extractor_confidence`: your own confidence, 0 to 1, that you identified
  the correct final verdict and speaker attribution for this ruling.
- A single episode may yield multiple rulings; output one array element per
  distinct ruling, in the order John rules on them.

## Output format

Write a single JSON array to the exact output path given in this packet
(below the transcript). No other text, no markdown fences. Each element:

```json
{
  "subject": "a Roomba",
  "category": "robot",
  "question_as_posed": "Is a Roomba a robot?",
  "verdict": "yes",
  "verdict_strength": "firm",
  "evidence_quotes": [
    {"quote": "It senses, it decides, it acts. That's a robot.", "start_s": 132.4, "end_s": 136.1}
  ],
  "reasoning_summary": "John's criteria are sensing the environment, deciding, and acting without direct control, all of which a Roomba does.",
  "extractor_confidence": 0.95
}
```

`verdict` is one of `"yes" | "no" | "ambiguous" | "no_ruling"`.
`verdict_strength` is one of `"firm" | "hedged" | "reversed_during_episode" | null`.
