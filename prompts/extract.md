<!-- markdownlint-disable-next-line MD041 -->
prompt_version: extract-v2

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
- **Prefer a yes/no verdict.** John usually lands somewhere even when he
  grumbles, qualifies, or calls it a close call. If he states or clearly
  accepts a lean ("I guess it's a robot", "fine, not a robot", "I'll allow
  it", "probably yes"), record that lean as `"yes"` or `"no"` and set
  `verdict_strength` to `"hedged"`. Hedging, reluctance, and caveats are
  strength, not a different verdict.
- A ruling can be delivered as agreement. If Jason (or a guest) states a
  conclusion and John plainly assents ("Yeah", "Right", "Exactly") or builds
  on it without objecting, that is John's ruling, attributed to John's
  assenting line. If John only lets it pass in silence, it is not.
- Use `"ambiguous"` only when John's *final* position is explicitly
  conditional or indeterminate ("it depends on whether X", "could be either,
  I can't say") and he never commits to a side. This should be rare.
- Use `"no_ruling"` only when John explicitly declines to rule, defers the
  question to a later episode, or the subject is mentioned without any lean
  from him. Do not use it for a subject he discussed and leaned on.
- `verdict_strength` is `"firm"` for a plain, undecorated ruling, `"hedged"`
  for a ruling he qualifies or expresses low confidence in,
  `"reversed_during_episode"` as above, and `null` when `verdict` is
  `"no_ruling"`.
- `subject` is the specific thing being judged (e.g. "a Roomba", "Johnny
  Cab"). `category` is the general concept it's judged against (e.g.
  "robot", "boat", "sauce", "video game").
- `question_as_posed` is a short, neutral paraphrase of how the question was
  originally asked (by a listener or by Jason), before John's deliberation.
- Up to 3 `evidence_quotes` per ruling, each a direct quote of at most 30
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
