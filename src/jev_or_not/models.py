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
