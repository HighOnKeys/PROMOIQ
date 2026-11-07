"""Event- and beat-level dramatic features.

Two interchangeable annotators produce the same BeatAnnotation schema:
  * HeuristicAnnotator - deterministic, offline, free (cue lexicons + delivery cues +
    camera language + the episode logline written by the show's own writers)
  * LLMAnnotator       - one batched call per episode via the ModelGateway; every quote
    the model cites is checked against the source text, unverifiable claims are flagged.
Event-level signals (used to time hooks to the exact first 3 seconds) are always
computed deterministically.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from src.dramatic import lexicon as L
from src.schemas import CATEGORIES, FEATURES, Beat, BeatAnnotation, Episode, Event, InfoGap

log = logging.getLogger(__name__)

STOP = set("""about above after again against their there these those which while would could should
being other under where whose with within without from into onto over through between before during
because every until only very also when then than that this have has had will shall them they your
yours ours hers his her him she he it its was were are is be been an a the of to in on for and or
but not no by at as so if do does did can may might must just even ever upon""".split())


def sat(x: float, k: float) -> float:
    """Saturating map of a non-negative evidence count onto 0..5."""
    return 5.0 * (1.0 - math.exp(-max(0.0, x) / k))


@dataclass
class EventSignal:
    event_id: str
    event_type: str
    start: float
    end: float
    E: float = 0.0          # emotional intensity 0-5
    C: float = 0.0          # conflict intensity 0-5
    S: float = 0.0          # stakes 0-5
    U: float = 0.0          # uncertainty / curiosity 0-5
    reveal: float = 0.0
    visual: float = 0.0
    surprise: float = 0.0
    punch: float = 0.0      # immediate attention grab 0-5 (hook material)
    resolution: float = 0.0  # closes tension 0-5
    question: bool = False
    trailing: bool = False
    establishing: bool = False
    categories: set = field(default_factory=set)


def logline_terms(ep: Episode) -> set[str]:
    words = re.findall(r"[A-Za-z][A-Za-z'\-]{4,}", f"{ep.logline} {ep.continuity}")
    return {w.lower() for w in words if w.lower() not in STOP}


def event_signal(ev: Event) -> EventSignal:
    t = ev.text
    sig = EventSignal(ev.event_id, ev.event_type, ev.start, ev.end)
    h = {k: L.hits(k, t) for k in L.LEX}
    cue = L.cue_intensity(ev.delivery) if ev.event_type == "dialogue" else 0.0
    excl = min(t.count("!"), 2) if ev.event_type == "dialogue" else 0
    q = ev.event_type == "dialogue" and "?" in t
    trailing = ev.event_type == "dialogue" and bool(re.search(r"(\.\.\.|…|—|--|-)\W*$", t))
    cam = L.camera_drama(t) if ev.event_type == "camera" else 0
    est = ev.event_type == "camera" and L.establishing(t)
    is_sound = ev.event_type == "sound"

    sig.E = sat(1.0 * cue + 0.6 * excl + 0.8 * h["emotion"] + 0.3 * h["shock"], 2.5)
    sig.C = sat(1.0 * h["confront"] + 1.0 * h["betrayal"] + 0.6 * h["threat"] + 0.8 * h["physical"]
                + (0.6 * cue if cue >= 2 else 0.0), 2.2)
    sig.S = sat(1.2 * h["threat"] + 0.4 * h["physical"], 2.0)
    sig.U = sat(1.0 * q + 1.0 * h["mystery"] + 1.0 * trailing + 0.7 * h["interrupt"] + 0.5 * h["shock"], 2.0)
    sig.reveal = sat(1.2 * h["reveal"] + 0.8 * h["identity"], 2.0)
    sig.visual = sat(1.0 * h["physical"] + 0.7 * cam + (1.2 if is_sound else 0.0), 2.0)
    sig.surprise = sat(1.0 * h["shock"] + 0.9 * h["reveal"] + 0.8 * h["interrupt"] + (0.8 if is_sound else 0), 2.2)
    if ev.event_type == "dialogue":
        punch = (0.8 * cue + 0.6 * excl + 0.8 * (h["confront"] + h["threat"] + h["reveal"] + h["betrayal"])
                 + 0.5 * q + 0.4 * h["romance"])
    elif ev.event_type == "action":
        punch = 1.2 * h["physical"] + 0.8 * h["shock"] + 0.5 * h["emotion"] + 0.6 * h["interrupt"]
    elif is_sound:
        punch = 1.5 + 1.0 * h["physical"] + 0.5 * h["shock"]
    else:
        punch = 0.5 * cam + 0.8 * h["physical"] + 0.5 * h["shock"] + 0.3 * h["emotion"] - (1.5 if est else 0.0)
    sig.punch = sat(punch, 2.0)
    sig.resolution = sat(1.0 * h["resolution"], 1.5)
    sig.question, sig.trailing, sig.establishing = q, trailing, est

    cats = sig.categories
    # camera text describes props ("chains", "iron") even in purely verbal scenes, so only
    # action/sound blocks count as physical action
    if h["physical"] and ev.event_type in ("action", "sound"):
        cats.add("physical_action")
    if h["threat"]:
        cats.add("threat")
        if h["physical"]:
            cats.add("danger")
    if h["betrayal"]:
        cats.add("betrayal")
    if h["confront"] or (cue >= 2 and excl):
        cats.add("confrontation")
    if h["reveal"]:
        cats.add("revelation")
        if h["identity"]:
            cats.add("identity_reveal")
    if h["romance"]:
        cats.add("romance")
    if h["proposal"]:
        cats.add("proposal")
    if h["rejection"] and ev.event_type == "dialogue":
        cats.add("rejection")
    if h["mystery"]:
        cats.add("mystery")
    if h["rescue"]:
        cats.add("rescue")
    if re.search(r"(?i)\b(i accept|i will marry|i choose|i'll go with|i do\b)", t):
        cats.add("unexpected_decision")
    return sig


def tension(sig_or_scores, w) -> float:
    """T = w1 E + w2 C + w3 S + w4 U (0-5)."""
    if isinstance(sig_or_scores, dict):
        E, C, S, U = (sig_or_scores.get(k, 0.0) for k in ("emotional_intensity", "conflict_intensity", "stakes", "curiosity"))
    else:
        E, C, S, U = sig_or_scores.E, sig_or_scores.C, sig_or_scores.S, sig_or_scores.U
    return w.tension_emotion * E + w.tension_conflict * C + w.tension_stakes * S + w.tension_uncertainty * U


class HeuristicAnnotator:
    name = "heuristic"

    def annotate_episode(self, ep: Episode, beats: list[Beat], events: dict[str, Event],
                         signals: dict[str, EventSignal]) -> list[BeatAnnotation]:
        terms = logline_terms(ep)
        out = []
        for b in beats:
            sigs = [signals[e] for e in b.event_ids]
            def agg(attr):
                vals = [getattr(s, attr) for s in sigs]
                return 0.7 * max(vals) + 0.3 * (sum(vals) / len(vals)) if vals else 0.0
            text_l = b.text.lower()
            overlap = {t for t in terms if t in text_l}
            scores = {
                "surprise": agg("surprise"),
                "emotional_intensity": agg("E"),
                "conflict_intensity": agg("C"),
                "stakes": agg("S"),
                "reveal_value": agg("reveal"),
                "visual_potential": agg("visual"),
                "curiosity": agg("U"),
                "narrative_importance": sat(0.9 * len(overlap) + 0.4 * (agg("reveal") + agg("S") + agg("C")) / 5, 2.5),
            }
            opening = [s for s in sigs if s.start <= b.start + 3.0]
            hook = max((s.punch for s in opening), default=0.0)
            tail = sorted(sigs, key=lambda s: s.end)[-3:]
            last_dlg = [s for s in sigs if s.event_type == "dialogue"]
            last_dlg = max(last_dlg, key=lambda s: s.end) if last_dlg else None
            cliff = sat(
                1.4 * bool(last_dlg and (last_dlg.question or last_dlg.trailing))
                + 0.25 * max((s.S + s.U + s.C for s in tail), default=0.0)
                + 0.6 * sum(1 for s in tail if "threat" in s.categories or "revelation" in s.categories),
                2.2,
            )
            resolution = max((s.resolution for s in tail), default=0.0)
            cats = sorted({c for s in sigs for c in s.categories})
            top = sorted(sigs, key=lambda s: s.punch + s.C + s.S, reverse=True)[:2]
            ev_q = [events[e] for e in b.event_ids if events[e].event_type == "dialogue" and "?" in events[e].text]
            learn = max((events[s.event_id] for s in sigs), key=lambda e: (signals[e.event_id].reveal + signals[e.event_id].S))
            out.append(BeatAnnotation(
                beat_id=b.beat_id,
                categories=cats,
                scores=scores,
                hook_signal=hook,
                cliff_signal=cliff,
                resolution_signal=resolution,
                info_gap=InfoGap(
                    viewer_learns=learn.text[:160],
                    unknown="",
                    question=ev_q[-1].text[:160] if ev_q else "",
                ),
                evidence=[s.event_id for s in top],
                reasoning=_reason(cats, top, events, overlap),
                annotator="heuristic",
            ))
        return out


def _reason(cats, top, events, overlap) -> str:
    bits = []
    if cats:
        bits.append("labels: " + ", ".join(cats[:4]))
    for s in top[:1]:
        ev = events[s.event_id]
        cue = f" ({', '.join(ev.delivery)})" if ev.delivery else ""
        bits.append(f'strongest: {ev.event_type}{cue} "{ev.text[:80]}"')
    if overlap:
        bits.append("matches logline: " + ", ".join(sorted(overlap)[:4]))
    return "; ".join(bits)


# ---------------------------------------------------------------- LLM annotator
class _LLMBeat(BaseModel):
    beat_id: str
    categories: list[str] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict)
    hook_signal: float = 0
    cliff_signal: float = 0
    resolution_signal: float = 0
    viewer_learns: str = ""
    unknown: str = ""
    question: str = ""
    evidence_quote: str = ""
    reasoning: str = ""


class _LLMEpisode(BaseModel):
    beats: list[_LLMBeat]


ANNOTATE_SYSTEM = (
    "You are a microdrama promo editor. You score screenplay beats for promotional potential. "
    "Use ONLY the text given. Never invent events. Scores are 0-5 integers on this rubric: "
    "0 none, 1 faint, 2 mild, 3 clear, 4 strong, 5 exceptional. Return JSON only."
)


def annotate_prompt(ep: Episode, beats: list[Beat], prev_logline: str) -> str:
    lines = [
        f"SHOW CONTEXT (previous episode logline): {prev_logline or 'n/a'}",
        f"THIS EPISODE LOGLINE: {ep.logline}",
        f"CONTINUITY NOTES: {ep.continuity}",
        "",
        "BEATS (id | start-end seconds | content):",
    ]
    for b in beats:
        lines.append(f"{b.beat_id} | {b.start:.2f}-{b.end:.2f} | {b.text[:600]}")
    lines += [
        "",
        f"For EVERY beat return an object with: beat_id; categories (subset of {list(CATEGORIES)}); "
        f"scores with keys {list(FEATURES)}; hook_signal (would its first 3 seconds stop a scroller?); "
        "cliff_signal (if a promo cut right after this beat, how strong is the unanswered question?); "
        "resolution_signal (does it close tension?); viewer_learns; unknown; question (the question a "
        "viewer is left with); evidence_quote (an exact substring of the beat content); reasoning (<=25 words).",
        'Return {"beats": [...]} only.',
    ]
    return "\n".join(lines)


class LLMAnnotator:
    def __init__(self, gateway, fallback: HeuristicAnnotator):
        self.gw = gateway
        self.fallback = fallback
        self.name = gateway.name
        self.unverified_quotes: list[dict] = []

    def annotate_episode(self, ep, beats, events, signals, prev_logline: str = "") -> list[BeatAnnotation]:
        heur = {a.beat_id: a for a in self.fallback.annotate_episode(ep, beats, events, signals)}
        try:
            res = self.gw.generate_json(f"annotate:ep{ep.episode}", ANNOTATE_SYSTEM,
                                        annotate_prompt(ep, beats, prev_logline), _LLMEpisode)
        except Exception as exc:
            log.error("LLM annotation failed for episode %s, using heuristic: %s", ep.episode, exc)
            for a in heur.values():
                a.annotator = "heuristic(fallback)"
            return list(heur.values())
        by_id = {b.beat_id: b for b in beats}
        out = []
        got = {r.beat_id: r for r in res.beats if r.beat_id in by_id}
        for b in beats:
            r = got.get(b.beat_id)
            if r is None:
                a = heur[b.beat_id]
                a.annotator = "heuristic(missing-from-llm)"
                out.append(a)
                continue
            quote_ok = bool(r.evidence_quote) and r.evidence_quote.strip('"').lower()[:60] in b.text.lower()
            if not quote_ok:
                self.unverified_quotes.append({"beat_id": b.beat_id, "quote": r.evidence_quote})
            out.append(BeatAnnotation(
                beat_id=b.beat_id,
                categories=r.categories,
                scores={k: float(r.scores.get(k, heur[b.beat_id].scores.get(k, 0))) for k in FEATURES},
                hook_signal=r.hook_signal,
                cliff_signal=r.cliff_signal,
                resolution_signal=r.resolution_signal,
                info_gap=InfoGap(viewer_learns=r.viewer_learns, unknown=r.unknown, question=r.question),
                evidence=heur[b.beat_id].evidence,
                reasoning=r.reasoning + ("" if quote_ok else " [UNVERIFIED QUOTE]"),
                annotator=self.name,
            ))
        return out
