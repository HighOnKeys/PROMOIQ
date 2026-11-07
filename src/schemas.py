"""Pydantic data models shared across the pipeline."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

EventType = Literal["dialogue", "action", "camera", "sound", "scene_transition"]
TimingStatus = Literal["exact", "inferred", "missing"]


class SourceRef(BaseModel):
    file: str
    line: int


class Event(BaseModel):
    """One timestamped screenplay block (a camera shot, an action, a line, a sound)."""
    event_id: str
    episode: int
    scene: int
    event_type: EventType
    speaker_raw: Optional[str] = None
    speaker: Optional[str] = None            # canonical character id
    modifiers: list[str] = Field(default_factory=list)   # O.S., V.O. ...
    delivery: list[str] = Field(default_factory=list)    # performance cues
    characters: list[str] = Field(default_factory=list)  # speaker + names mentioned
    start: float
    end: float
    start_raw: str
    end_raw: str
    text: str
    source: SourceRef
    timing_status: TimingStatus = "exact"
    confidence: float = 1.0
    issues: list[str] = Field(default_factory=list)

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)


class Scene(BaseModel):
    episode: int
    scene: int
    heading: str
    start: Optional[float]
    end: Optional[float]
    start_raw: str = ""
    end_raw: str = ""
    event_ids: list[str] = Field(default_factory=list)


class Episode(BaseModel):
    episode: int                 # 1-based order from file name
    label: str                   # "Episode 1"
    file: str
    logline: str = ""
    continuity: str = ""
    scenes: list[Scene] = Field(default_factory=list)
    duration: float = 0.0


class Beat(BaseModel):
    """A cuttable unit of footage: one or more consecutive camera shots, merged so that
    no line of dialogue or sound cue straddles a beat boundary. Beat boundaries are the
    only legal promo cut points, and they always equal a source timestamp."""
    beat_id: str
    episode: int
    scene: int
    start: float
    end: float
    start_raw: str
    end_raw: str
    event_ids: list[str]
    characters: list[str]
    speakers: list[str]
    text: str                    # compact text used for scoring and judging

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)


FEATURES = (
    "surprise", "emotional_intensity", "conflict_intensity", "stakes",
    "reveal_value", "visual_potential", "curiosity", "narrative_importance",
)
CATEGORIES = (
    "betrayal", "confrontation", "revelation", "physical_action", "rescue", "romance",
    "proposal", "rejection", "mystery", "danger", "threat", "emotional_reversal",
    "unexpected_decision", "identity_reveal",
)


class InfoGap(BaseModel):
    viewer_learns: str = ""
    unknown: str = ""
    question: str = ""


class BeatAnnotation(BaseModel):
    beat_id: str
    categories: list[str] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict)
    hook_signal: float = 0.0     # 0-5: how strongly this beat's opening grabs attention
    cliff_signal: float = 0.0    # 0-5: how unresolved the moment at this beat's end is
    resolution_signal: float = 0.0  # 0-5: how much this beat closes a question
    info_gap: InfoGap = Field(default_factory=InfoGap)
    evidence: list[str] = Field(default_factory=list)   # event ids
    reasoning: str = ""
    annotator: str = "heuristic"

    @field_validator("scores")
    @classmethod
    def _clip(cls, v: dict[str, float]) -> dict[str, float]:
        return {k: float(min(5.0, max(0.0, x))) for k, x in v.items()}

    @field_validator("categories")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        return [c for c in v if c in CATEGORIES]


class Segment(BaseModel):
    order: int
    episode: int
    start: float
    end: float
    start_raw: str
    end_raw: str
    beat_ids: list[str]

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)


class PromoScores(BaseModel):
    hook: float = 0.0
    cliffhanger: float = 0.0
    clarity: float = 0.0
    emotion: float = 0.0
    info_gap: float = 0.0
    coherence: float = 0.0
    redundancy: float = 0.0
    spoiler: float = 0.0
    quality: float = 0.0


class PromoCandidate(BaseModel):
    promo_id: str
    mode: Literal["continuous", "multi_segment"]
    segments: list[Segment]
    duration: float
    scores: PromoScores = Field(default_factory=PromoScores)
    hook: str = ""
    cliffhanger: str = ""
    why: str = ""
    theme: str = ""
    categories: list[str] = Field(default_factory=list)
    characters: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    generator: str = "promoiq"
