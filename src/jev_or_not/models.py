from typing import Any, Literal

from pydantic import BaseModel, field_validator


class Record(BaseModel):
    """Base class for all dataset artifact records."""

    schema_version: int


class ScorecardRow(Record):
    """One <li> from the robotornot.info scorecard, unchanged from the scrape."""

    source_row_id: str
    row_index: int
    item: str
    episode: int | None
    episode_link_text: str
    verdict: Literal["robot", "not_robot", "unresolved"]
    verdict_raw: str
    reference_url: str
    episode_url: str
    retrieved_at: str
    source_html_hash: str


class ScorecardRuling(Record):
    """One derived (episode, subject) ruling collapsed from scorecard rows."""

    ruling_ref: str
    episode_id: str | None
    episode: int | None
    subject: str
    aliases: list[str] = []
    label: Literal["yes", "no", "unresolved"]
    source_row_ids: list[str]
    review_status: Literal["auto", "quarantined", "excluded"]
    quarantine_reason: str | None = None
    correction_reason: str | None = None


class AdjudicationEntry(Record):
    """One manual decision applied on top of a scorecard row/ruling."""

    source_row_id: str
    field: str
    old: Any = None
    new: Any = None
    reason: str
    evidence: str | None = None
    decided_by: str
    decided_at: str


class PilotReference(Record):
    """One annotated ruling in the pilot reference set.

    See docs/annotation_procedure.md for how these are produced (PLAN.md
    Phase 0 step 4, executed in Phase 1 step 6). Distinct from
    ``ScorecardRuling``: never copy a scorecard verdict into this model.
    """

    episode_id: str
    ruling_id: str
    subject: str
    category: str
    verdict: Literal["yes", "no", "ambiguous", "no_ruling"]
    verdict_strength: Literal["firm", "hedged", "reversed_during_episode"] | None
    ruling_start_s: float
    ruling_end_s: float
    speaker: Literal["JOHN", "JASON", "GUEST"]
    annotator: str
    annotated_at: str
    reference_version: str

    @field_validator("ruling_end_s")
    @classmethod
    def _end_after_start(cls, v: float, info: Any) -> float:
        start = info.data.get("ruling_start_s")
        if start is not None and v <= start:
            raise ValueError("ruling_end_s must be greater than ruling_start_s")
        return v


class Episode(Record):
    """One episode reconciled from the RSS feed and/or the archive index.

    See PLAN.md Phase 1 steps 1-3. ``episode_id`` is the feed guid when the
    episode came from the feed, else a namespaced archive URL for
    archive-only entries.
    """

    episode_id: str
    episode: int | None
    episode_label: str
    title: str
    released_at: str | None
    description: str
    show_notes_url: str
    audio_url: str
    duration_s: int | None
    source: Literal["feed", "archive", "both"]
    retrieved_at: str
    aliases: list[str] = []
    excluded: bool = False
    exclusion_reason: str | None = None
    blocked: bool = False
    review_queue: list[str] = []


class TranscriptIndexEntry(Record):
    """One successful (episode, candidate) transcription (PLAN.md Phase 2 Output).

    Exported from the transcribe ledger's successful tasks only; see
    ``jev_or_not.transcribe.export_index``. No transcript text goes here.
    """

    episode_id: str
    episode_label: str
    candidate: Literal["A", "B"]
    transcript_path: str
    transcript_hash: str
    fingerprint: str
    engine: str
    model: str
    diarization_device: str
    n_segments: int
    n_clusters: int
    runtime_total_s: float
    rtf: float | None
    created_at: str


class EnrolledClusterSummary(BaseModel):
    """One diarization cluster's enrollment outcome, nested inside
    ``EnrolledIndexEntry``."""

    id: str
    label: Literal["JOHN", "JASON", "GUEST", "UNKNOWN"]
    similarity: float | None
    margin: float | None
    speech_s: float


class EnrolledIndexEntry(Record):
    """One successful (episode, candidate) speaker-enrollment application
    (PLAN.md Phase 2 Output items 2-3). No transcript text here -- see
    ``jev_or_not.enroll.export_index``.
    """

    episode_id: str
    episode_label: str
    candidate: Literal["A", "B"]
    enrolled_path: str
    enrolled_hash: str
    transcript_hash: str
    fingerprint: str
    floor: float
    margin: float
    thresholds_status: Literal["provisional", "frozen"]
    clusters: list[EnrolledClusterSummary]
    unknown_speech_s: float
    created_at: str


class AudioManifestEntry(Record):
    """One downloaded episode audio file (PLAN.md Phase 1 step 5).

    Exported from the download ledger's successful tasks only; see
    ``jev_or_not.download.export_manifest``.
    """

    episode_id: str
    episode_label: str
    audio_url: str
    resolved_url: str
    file_path: str
    bytes: int
    sha256: str
    content_type: str
    measured_duration_s: float | None
    feed_duration_s: int | None
    duration_mismatch: bool
    downloaded_at: str
    status: str


class EvidenceQuote(BaseModel):
    """One evidence quote backing a ruling (PLAN.md Phase 3): under 15
    whitespace-delimited words, with its transcript timestamps."""

    quote: str
    start_s: float
    end_s: float

    @field_validator("quote")
    @classmethod
    def _short_quote(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("quote must not be empty")
        if len(v.split()) >= 15:
            raise ValueError("quote must be under 15 whitespace-delimited words")
        return v

    @field_validator("end_s")
    @classmethod
    def _end_after_start(cls, v: float, info: Any) -> float:
        start = info.data.get("start_s")
        if start is not None and v <= start:
            raise ValueError("end_s must be greater than start_s")
        return v


class Ruling(Record):
    """One extracted verdict (PLAN.md Phase 3 output schema, ``data/verdicts.jsonl``).

    Produced only by ``jev_or_not.extract.ingest`` from a Claude Code
    subagent's validated ``output.json``; never written by hand.
    """

    episode_id: str
    episode: int | None
    ruling_id: str
    subject: str
    category: str
    question_as_posed: str
    verdict: Literal["yes", "no", "ambiguous", "no_ruling"]
    verdict_strength: Literal["firm", "hedged", "reversed_during_episode"] | None
    evidence_quotes: list[EvidenceQuote]
    reasoning_summary: str
    extractor_model: str
    prompt_version: str
    extractor_confidence: float
    transcript_hash: str
    speaker_override_hash: str | None
    reference_version: str | None
    review_status: str
