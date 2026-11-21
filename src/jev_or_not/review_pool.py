"""Pool extracted rulings into blind review candidates (pooled reference).

Rex builds the pilot reference by reviewing findings instead of listening
to every episode end to end (docs/annotation_procedure.md, "Pooled
review"). Every extraction run's rulings for an episode (A, A2, B x sonnet,
haiku), plus the episode's scorecard subjects, are merged into one list of
candidates, deduplicated by subject. Model and run names never reach the
reviewer, so no candidate is favoured by who found it; only *how many* runs
found it is shown.

Everything here is pure: rows in, candidates out.
"""

import hashlib
import re
from collections import Counter
from statistics import median

# Words that carry no identity for a subject ("the robot dance" and "dance
# the robot" are one subject). "robot" is here because most subjects are
# judged against it and runs vary on whether they repeat it.
_STOPWORDS = frozenset(
    {
        *("a", "an", "the", "of", "and", "or", "to", "in", "on", "for", "is", "it", "its"),
        *("as", "s", "by", "with", "his", "her", "their", "this", "that", "e", "g"),
        *("robot", "robots", "called", "being"),
    }
)
_MATCH_JACCARD = 0.5
# Runs phrase one ruling differently ("microscopic dust-sized robots", "the
# microscopic, dust-sized machines"). Two phrasings sharing a content word
# are one ruling when they start near each other or quote the same moment.
_NEAR_START_S = 20.0
_SAME_QUOTE_S = 3.0
_MIN_SPAN_S = 4.0


def subject_tokens(subject: str) -> frozenset[str]:
    words = re.sub(r"[^a-z0-9]+", " ", subject.lower()).split()
    kept = frozenset(w for w in words if w not in _STOPWORDS)
    return kept or frozenset(words)


def same_subject(a: frozenset[str], b: frozenset[str]) -> bool:
    if not a or not b:
        return False
    if a <= b or b <= a:
        return True
    return len(a & b) / len(a | b) >= _MATCH_JACCARD


def _quote_starts(v: dict) -> list[float]:
    return [q["start_s"] for q in v.get("evidence_quotes") or []]


def _same_moment(a: list[float], b: list[float]) -> bool:
    if not a or not b:
        return False
    if abs(min(a) - min(b)) <= _NEAR_START_S:
        return True
    return any(abs(x - y) <= _SAME_QUOTE_S for x in a for y in b)


def _belongs(tokens: frozenset[str], starts: list[float], group: dict) -> bool:
    for t, st in zip(group["tokens"], group["starts"], strict=True):
        if same_subject(tokens, t):
            return True
        if tokens & t and _same_moment(starts, st):
            return True
    return False


def _majority(values: list, default=None):
    """Most common value; ties go to the one seen first."""
    # most_common orders equal counts by first appearance
    return Counter(values).most_common(1)[0][0] if values else default


def candidate_id(episode_id: str, subject: str) -> str:
    key = episode_id + "|" + " ".join(sorted(subject_tokens(subject)))
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def pool(
    episode_id: str,
    duration_s: float,
    verdicts: list[dict],
    scorecard: list[dict],
    runs_total: int,
    *,
    with_members: bool = False,
) -> list[dict]:
    """Candidates for one episode, in audio order (scorecard-only last).

    ``verdicts`` are this episode's ``data/verdicts.jsonl`` rows, each with an
    added ``run`` key naming its (candidate, model) run; ``scorecard`` are
    its ``scorecard_verified`` rulings.

    ``with_members`` adds which run's rulings and which scorecard rows landed
    in each candidate, for scoring after the reference is sealed. It names
    runs, so the review desk never asks for it.
    """
    groups: list[dict] = []

    def place(tokens: frozenset[str], starts: list[float]) -> dict:
        group = next((g for g in groups if _belongs(tokens, starts, g)), None)
        if group is None:
            group = {"tokens": [], "starts": [], "members": [], "scorecard": []}
            groups.append(group)
        group["tokens"].append(tokens)
        group["starts"].append(starts)
        return group

    for v in sorted(verdicts, key=lambda v: min(_quote_starts(v), default=float("inf"))):
        place(subject_tokens(v["subject"]), _quote_starts(v))["members"].append(v)
    for sc in scorecard:
        # scorecard rows carry no timestamps, so they join on the subject alone
        place(subject_tokens(sc["subject"]), [])["scorecard"].append(sc)

    candidates = []
    for g in groups:
        members = g["members"]
        subjects = [m["subject"] for m in members] or [s["subject"] for s in g["scorecard"]]
        counts = Counter(subjects)
        # the most common phrasing; ties go to the shortest, then the first seen
        subject = min(counts, key=lambda s: (-counts[s], len(s)))
        verdict = _majority([m["verdict"] for m in members])
        strengths = [m["verdict_strength"] for m in members if m["verdict"] == verdict]
        # The reference span bounds John's *final* ruling statement, and a
        # run's latest quote is the closest thing it has to that, so the span
        # is the median of each run's latest quote rather than the union of
        # every quote (which covers the whole discussion).
        ranges = [
            (last["start_s"], last["end_s"])
            for m in members
            if m.get("evidence_quotes")
            for last in [max(m["evidence_quotes"], key=lambda q: q["start_s"])]
        ]
        span = None
        if ranges:
            start = median(r[0] for r in ranges)
            end = min(float(duration_s), max(median(r[1] for r in ranges), start + _MIN_SPAN_S))
            # a quote timed at or past the audio's end would clamp to an
            # empty span, so pull the start back instead
            start = max(0.0, min(start, end - _MIN_SPAN_S))
            span = [round(start, 1), round(end, 1)]
        quotes: list[dict] = []
        seen: set[str] = set()
        for m in sorted(members, key=lambda m: -m.get("extractor_confidence", 0)):
            for q in m.get("evidence_quotes") or []:
                if (norm := q["quote"].strip().lower()) not in seen:
                    seen.add(norm)
                    quotes.append({"quote": q["quote"], "start_s": q["start_s"]})
        candidates.append(
            {
                "candidate_id": candidate_id(episode_id, subject),
                "subject": subject,
                "category": _majority([m["category"] for m in members], "robot"),
                "verdict": verdict,
                "verdict_strength": None if verdict == "no_ruling" else _majority(strengths),
                "verdict_split": dict(Counter(m["verdict"] for m in members)),
                "span": span,
                "quotes": sorted(quotes[:3], key=lambda q: q["start_s"]),
                "found_by": len({m["run"] for m in members}),
                "runs_total": runs_total,
                "on_scorecard": bool(g["scorecard"]),
            }
        )
        if with_members:
            candidates[-1]["members"] = [
                {
                    "run": m["run"],
                    "ruling_id": m["ruling_id"],
                    "verdict": m["verdict"],
                    "subject": m["subject"],
                    "prompt_version": m.get("prompt_version"),
                }
                for m in members
            ]
            candidates[-1]["scorecard_refs"] = [
                {"ruling_ref": sc["ruling_ref"], "label": sc["label"]} for sc in g["scorecard"]
            ]
    candidates.sort(key=lambda c: (c["span"] is None, c["span"][0] if c["span"] else 0))
    return candidates
