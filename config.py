"""Central configuration for PROMOIQ.

Every tunable number lives here so that weights, search parameters and model
choices can be changed without touching pipeline code. Values can be overridden
with environment variables (see .env.example).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional: load a local .env if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env")
except Exception:  # pragma: no cover - dotenv is optional
    pass

ROOT = Path(__file__).resolve().parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class PromoConstraints:
    min_duration: float = 20.0          # seconds, from the brief
    max_duration: float = 60.0          # seconds, from the brief
    hook_window: float = 3.0            # first N seconds must hook (brief)
    cliff_window: float = 5.0           # last N seconds evaluated for the cliffhanger
    min_segment: float = 4.0            # a segment shorter than this is not watchable
    max_segments: int = 3               # multi-segment promos join at most 3 moments
    max_episode_gap: int = 8            # max episode distance between joined segments


@dataclass
class ScoreWeights:
    """Q(P) = 0.20H + 0.20K + 0.20C + 0.15E + 0.15G + 0.10N - lam*R - mu*S.

    Initial hypotheses, not tuned values. All positive components are 0-10.
    """
    hook: float = 0.20
    cliffhanger: float = 0.20
    clarity: float = 0.20
    emotion: float = 0.15
    info_gap: float = 0.15
    coherence: float = 0.10
    redundancy_lambda: float = 0.15
    spoiler_mu: float = 0.20
    # tension curve T(t) = w1 E + w2 C + w3 S + w4 U
    tension_emotion: float = 0.30
    tension_conflict: float = 0.30
    tension_stakes: float = 0.25
    tension_uncertainty: float = 0.15


@dataclass
class SearchConfig:
    beam_width: int = 40
    segment_library_size: int = 450     # top moments considered for multi-segment
    seg_min: float = 4.0
    seg_max: float = 30.0
    max_continuous_per_episode: int = 60
    seed: int = 7


@dataclass
class GateConfig:
    """Editorial gates. A candidate below any gate is rejected with a reason."""
    hook_pass: float = 5.0
    cliffhanger_pass: float = 5.0
    clarity_pass: float = 5.0
    max_spoiler: float = 6.0
    multi_coherence_pass: float = 5.0   # joined moments must read as one story


@dataclass
class RankingConfig:
    shortlist_size: int = 10
    mmr_lambda: float = 0.70
    duplicate_threshold: float = 0.60   # similarity above this = flagged as near-duplicate
    min_quality_ratio: float = 0.75     # never pick below 75% of the best promo's score
    embedding_model: str = "all-MiniLM-L6-v2"


@dataclass
class ModelConfig:
    """Swappable model layer. 'heuristic' runs fully offline and free."""
    provider: str = field(default_factory=lambda: _env("PROMOIQ_PROVIDER", "heuristic"))
    model: str = field(default_factory=lambda: _env("PROMOIQ_MODEL", ""))
    judge_provider: str = field(default_factory=lambda: _env("PROMOIQ_JUDGE_PROVIDER", ""))
    judge_model: str = field(default_factory=lambda: _env("PROMOIQ_JUDGE_MODEL", ""))
    temperature: float = 0.2
    max_retries: int = 2
    # LLM judges are spent only on this many DISTINCT strong candidates (2 calls each)
    judge_budget: int = field(default_factory=lambda: int(_env("PROMOIQ_JUDGE_BUDGET", "30")))
    # >1 batches several episodes into one compact annotation call (free tiers allow ~20 requests/day/model)
    episodes_per_call: int = field(default_factory=lambda: int(_env("PROMOIQ_LLM_EPISODES_PER_CALL", "1")))
    cache_dir: Path = ROOT / "outputs" / "raw" / "llm_cache"


@dataclass
class Paths:
    input_dir: Path = field(default_factory=lambda: Path(_env("PROMOIQ_INPUT", str(ROOT / "data" / "input"))))
    outputs: Path = ROOT / "outputs"

    @property
    def raw(self) -> Path:
        return self.outputs / "raw"

    @property
    def processed(self) -> Path:
        return self.outputs / "processed"

    @property
    def final(self) -> Path:
        return self.outputs / "final"


@dataclass
class Settings:
    constraints: PromoConstraints = field(default_factory=PromoConstraints)
    weights: ScoreWeights = field(default_factory=ScoreWeights)
    search: SearchConfig = field(default_factory=SearchConfig)
    gates: GateConfig = field(default_factory=GateConfig)
    ranking: RankingConfig = field(default_factory=RankingConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    paths: Paths = field(default_factory=Paths)


def get_settings() -> Settings:
    return Settings()
