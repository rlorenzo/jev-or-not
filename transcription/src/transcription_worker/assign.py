"""Pure functions: assign diarization clusters to words, then group words
into segments. No I/O, no third-party deps — easy to unit test in isolation.

PLAN.md Phase 2 Output section: each word gets the diarization cluster with
maximum temporal overlap; a word with no overlap gets the nearest cluster
within 0.5 s, else null. Segments group consecutive words with the same
cluster, splitting on gaps > 1.0 s.
"""

from __future__ import annotations

NEAREST_CLUSTER_MAX_GAP_S = 0.5
SEGMENT_SPLIT_GAP_S = 1.0


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def assign_word_cluster(
    word_start: float, word_end: float, diar_segments: list[tuple[float, float, str]]
) -> str | None:
    """diar_segments: list of (start, end, label). Returns the label with the
    largest overlap, or the nearest label within 0.5 s if none overlap, or
    None."""
    best_label, best_overlap = None, 0.0
    for start, end, label in diar_segments:
        ov = _overlap(word_start, word_end, start, end)
        if ov > best_overlap:
            best_overlap, best_label = ov, label
    if best_label is not None:
        return best_label

    best_label, best_dist = None, None
    for start, end, label in diar_segments:
        if word_end <= start:
            dist = start - word_end
        elif word_start >= end:
            dist = word_start - end
        else:
            dist = 0.0
        if dist <= NEAREST_CLUSTER_MAX_GAP_S and (best_dist is None or dist < best_dist):
            best_dist, best_label = dist, label
    return best_label


def build_segments(words: list[dict]) -> list[dict]:
    """words: [{"start","end","text","cluster"}], in time order. Groups
    consecutive words that share a cluster, splitting whenever the gap to the
    previous word exceeds 1.0 s (or the cluster changes)."""
    if not words:
        return []

    segments: list[dict] = []
    first = words[0]
    current = {
        "start": first["start"],
        "end": first["end"],
        "cluster": first["cluster"],
        "words": [first],
    }
    for w in words[1:]:
        gap = w["start"] - current["end"]
        if w["cluster"] == current["cluster"] and gap <= SEGMENT_SPLIT_GAP_S:
            current["end"] = max(current["end"], w["end"])  # overlapping tokens must not shorten it
            current["words"].append(w)
        else:
            segments.append(current)
            current = {"start": w["start"], "end": w["end"], "cluster": w["cluster"], "words": [w]}
    segments.append(current)

    for seg in segments:
        seg["text"] = "".join(w["text"] for w in seg["words"]).strip()
    return segments
