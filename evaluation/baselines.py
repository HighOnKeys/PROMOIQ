"""Baselines to compare PROMOIQ against.

Baseline A  (LLM-only): one prompt per episode with the raw screenplay asking for promos.
            Implemented; it runs only when an LLM provider is configured.
Baseline A0 (keyword window, no AI): score each dialogue line by dramatic keywords and cut a
            fixed 30 s window centred on the best lines. This is the simplest script a team
            would write first. It does no boundary snapping and no sequence reasoning.
Baseline B  (independent event ranking): rank beats by PROMOIQ's own tension score T and
            take each top beat plus the following beats until >= 30 s. Same features as
            PROMOIQ, but no hook/cliffhanger/clarity optimisation, no multi-segment search,
            no judges and no diversity pass.
"""
from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, Field

from src.dramatic import lexicon as L
from src.llm.client import LenientModel
from src.parser.timestamps import format_timestamp, parse_timestamp

log = logging.getLogger(__name__)


class Clip(BaseModel):
    """System-agnostic promo: list of (episode, start_s, end_s)."""
    system: str
    clip_id: str
    segments: list[tuple[int, float, float]]
    hook: str = ""
    cliffhanger: str = ""
    why: str = ""

    @property
    def duration(self) -> float:
        return round(sum(b - a for _, a, b in self.segments), 3)


def baseline_a0_keyword(show, k: int = 10, window: float = 30.0) -> list[Clip]:
    scored = []
    for ev in show.events.values():
        if ev.event_type != "dialogue":
            continue
        s = sum(L.hits(c, ev.text) for c in ("threat", "betrayal", "reveal", "confront", "physical")) + ev.text.count("!")
        scored.append((s, ev))
    scored.sort(key=lambda x: (-x[0], x[1].episode, x[1].start))
    out, used = [], []
    for s, ev in scored:
        if len(out) >= k:
            break
        ep_dur = show.episodes[ev.episode].duration
        mid = (ev.start + ev.end) / 2
        a = max(0.0, mid - window / 2)
        b = min(ep_dur, a + window)
        a = max(0.0, b - window)
        if any(e == ev.episode and a < y and b > x for e, x, y in used):   # avoid exact repeats only
            continue
        used.append((ev.episode, a, b))
        out.append(Clip(system="A0_keyword_window", clip_id=f"A0-{len(out) + 1:02d}",
                        segments=[(ev.episode, round(a, 2), round(b, 2))],
                        hook="", cliffhanger="", why=f"keyword score {s} on: {ev.text[:80]}"))
    return out


def baseline_b_event_rank(show, k: int = 10, target: float = 30.0) -> list[Clip]:
    beats = [(tl.T[i], ep, i) for ep, tl in show.timelines.items() for i in range(len(tl.beats))]
    beats.sort(key=lambda x: -x[0])
    out = []
    for t, ep, i in beats:
        if len(out) >= k:
            break
        tl = show.timelines[ep]
        j = i
        while j + 1 < len(tl.beats) and tl.beats[j].end - tl.beats[i].start < target:
            j += 1
        a, b = tl.beats[i].start, tl.beats[j].end
        out.append(Clip(system="B_event_rank", clip_id=f"B-{len(out) + 1:02d}", segments=[(ep, a, b)],
                        why=f"top tension beat T={t:.2f}"))
    return out


class _LLMPromo(LenientModel):
    episode: int
    segments: list[dict]
    hook: str = ""
    cliffhanger: str = ""
    why: str = ""
    score: float = 0.0          # the model's own 0-10 rating, used to rank across episodes


class _LLMPromos(LenientModel):
    promos: list[_LLMPromo] = Field(default_factory=list)


def baseline_a_llm(show, gateway, files_text: dict[int, str], k_per_ep: int = 1) -> tuple[list[Clip], dict]:
    """Straightforward LLM extraction: the raw episode script and the brief's promo definition, one
    call per episode, no parsing help, no validation loop. Returns (clips ranked by the model's own
    score, stats). Requires a provider."""
    stats = {"episodes": len(files_text), "calls_failed": 0, "unparseable_timestamps": 0}
    if gateway is None:
        return [], stats
    out: list[tuple[float, Clip]] = []
    sys_p = ("You are a promo editor for short vertical microdramas. A promo is a 20-60 second clip from the show "
             "(one continuous stretch or a few joined moments) that opens on a hook in the first 3 seconds, ends on a "
             "cliffhanger, and makes sense to someone who has never seen the show. Use the screenplay's own "
             "timestamps. Return JSON only.")
    for ep, text in sorted(files_text.items()):
        prompt = (text[:30000] + f"\n\nFind the best promo in this episode. Return {{\"promos\": [{{\"episode\": {ep}, "
                  "\"segments\": [{\"start\": \"MM:SS.ss\", \"end\": \"MM:SS.ss\"}], \"hook\": str, "
                  "\"cliffhanger\": str, \"why\": str, \"score\": 0-10 how strong this promo is}]} "
                  f"with {k_per_ep} promo(s).")
        try:
            res = gateway.generate_json(f"baselineA:ep{ep}", sys_p, prompt, _LLMPromos)
        except Exception as exc:
            log.error("baseline A failed on episode %s: %s", ep, exc)
            stats["calls_failed"] += 1
            continue
        for p in res.promos[:k_per_ep]:
            segs = []
            for s in p.segments:
                try:
                    segs.append((ep, parse_timestamp(s.get("start", "")), parse_timestamp(s.get("end", ""))))
                except Exception:
                    stats["unparseable_timestamps"] += 1
                    segs.append((ep, -1.0, -1.0))   # keep it so the validator counts the failure
            if not segs:
                segs = [(ep, -1.0, -1.0)]
            out.append((p.score, Clip(system="A_llm_only", clip_id=f"A-{ep:02d}", segments=segs, hook=p.hook,
                                      cliffhanger=p.cliffhanger, why=p.why)))
    out.sort(key=lambda x: -x[0])
    return [c for _, c in out], stats
