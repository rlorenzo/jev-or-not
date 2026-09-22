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
    verdict: str  # "robot" | "not_robot" | "unresolved"
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
    label: str  # "yes" | "no" | "unresolved"
    source_row_ids: list[str]
    review_status: str  # "auto" | "quarantined" | "excluded"
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
