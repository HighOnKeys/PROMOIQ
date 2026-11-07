"""Editorial judges.

ColdViewerJudge     sees ONLY the promo's own text (no story bible, no scores).
IntegrityJudge      sees the promo + targeted story context (conflict timelines, later
                    developments) and checks cliffhanger, hook-ending link, coherence,
                    spoilers and faithfulness of quotes.

Each has a deterministic implementation and an LLM implementation behind the same
output schema. When an LLM is configured, the judges can use a different
provider/model from the generator (PROMOIQ_JUDGE_PROVIDER / PROMOIQ_JUDGE_MODEL).
"""
from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field

from src.construction.scoring import clarity, info_gap, spoilers
from src.construction.show import Show
from src.llm.client import LenientModel
from src.narrative.story import retrieve_context
from src.schemas import PromoCandidate

log = logging.getLogger(__name__)


def promo_script(show: Show, p: PromoCandidate) -> str:
    """Exactly what a viewer would see/hear in the promo, in order. No external context."""
    out = []
    for s in p.segments:
        out.append(f"--- SEGMENT {s.order} ---")
        for bid in s.beat_ids:
            tl = show.timelines[s.episode]
            b = next(x for x in tl.beats if x.beat_id == bid)
            out.append(f"[{b.start - s.start + sum(x.end - x.start for x in p.segments if x.order < s.order):05.1f}s] {b.text}")
    return "\n".join(out)


class ColdViewerResult(LenientModel):
    main_characters: list[str] = Field(default_factory=list)
    what_is_happening: str = ""
    central_conflict: str = ""
    why_it_matters: str = ""
    what_remains_unknown: str = ""
    standalone: bool = False
    clarity_score: float = 0.0
    missing_information: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    judge: str = "heuristic"


class IntegrityResult(LenientModel):
    ending_leaves_question: bool = False
    question: str = ""
    answered_inside_promo: bool = False
    answered_later_in_series: bool = False
    hook_connected_to_ending: bool = False
    coherent: bool = False
    spoiler_types: list[str] = Field(default_factory=list)
    spoiler_risk: float = 0.0
    faithful_to_source: bool = True
    cliffhanger_score: float = 0.0
    coherence_score: float = 0.0
    notes: list[str] = Field(default_factory=list)
    judge: str = "heuristic"


def _segs(show: Show, p: PromoCandidate):
    out = []
    for s in p.segments:
        tl = show.timelines[s.episode]
        i = tl.index_of_start(s.start)
        j = tl.index_of_end(s.end)
        out.append((s.episode, i, j))
    return tuple(out)


class ColdViewerJudge:
    def __init__(self, show: Show, gateway=None):
        self.show, self.gw = show, gateway

    def judge(self, p: PromoCandidate) -> ColdViewerResult:
        if self.gw is not None:
            try:
                return self._llm(p)
            except Exception as exc:
                log.error("cold-viewer LLM judge failed for %s: %s; using heuristic", p.promo_id, exc)
        return self._heuristic(p)

    def _heuristic(self, p: PromoCandidate) -> ColdViewerResult:
        show = self.show
        segs = _segs(show, p)
        evs = [show.events[e] for s in p.segments for b in s.beat_ids
               for e in next(x for x in show.timelines[s.episode].beats if x.beat_id == b).event_ids]
        dlg = [e for e in evs if e.event_type == "dialogue"]
        from collections import Counter
        speakers = [show.characters.get(c, {}).get("name", c) for c, _ in Counter(e.speaker for e in dlg).most_common(4)]
        sig = lambda e: show.timelines[e.episode].signals[e.event_id]
        # a viewer sees action and hears dialogue/sound; camera directions are production notes, not story
        seen = [e for e in evs if e.event_type != "camera"]
        top = max(seen, key=lambda e: sig(e).C + sig(e).S + sig(e).E) if seen else None
        # stakes count only when a character says them
        stake = max(dlg, key=lambda e: sig(e).S) if dlg else None
        qs = [e for e in dlg if "?" in e.text]
        c_score, missing = clarity(show, segs)
        # name a conflict only if it is a known antagonistic pair (hostility > warmth) and both speak in the promo
        spk = {e.speaker for e in dlg}
        in_promo = {b for s in p.segments for b in s.beat_ids}
        known = [cf for cf in show.conflicts.values() if set(cf["characters"]) <= spk]
        conflict_pair = ""
        if known:
            cf = max(known, key=lambda cf: (sum(st["beat_id"] in in_promo for st in cf["events"]), len(cf["events"])))
            conflict_pair = " vs ".join(show.characters.get(c, {}).get("name", c) for c in cf["characters"])
        return ColdViewerResult(
            main_characters=speakers,
            what_is_happening=(top.text[:160] if top else ""),
            central_conflict=conflict_pair or "not explicit on screen",
            why_it_matters=(stake.text[:160] if stake and sig(stake).S > 0.5 else "stakes not stated on screen"),
            what_remains_unknown=(qs[-1].text[:160] if qs else "no explicit open question"),
            standalone=c_score >= 5.0,
            clarity_score=round(c_score, 2),
            missing_information=missing,
            evidence=[e.event_id for e in ([top] if top else []) + ([qs[-1]] if qs else [])],
            judge="heuristic (not independent of the generator's clarity proxy)",
        )

    def _llm(self, p: PromoCandidate) -> ColdViewerResult:
        system = ("You are a viewer scrolling social media who has NEVER seen this show. You only know what is "
                  "in the promo below. Do not invent context. Return JSON only.")
        prompt = (promo_script(self.show, p) + "\n\nAnswer as JSON with keys: main_characters (list), "
                  "what_is_happening, central_conflict, why_it_matters, what_remains_unknown, standalone (bool: "
                  "could you follow this without previous episodes?), clarity_score (0-10), missing_information "
                  "(list of things you could not understand), evidence (list of short exact quotes).")
        r = self.gw.generate_json(f"cold_viewer:{p.promo_id}", system, prompt, ColdViewerResult)
        r.judge = self.gw.name
        return r


class IntegrityJudge:
    def __init__(self, show: Show, gateway=None):
        self.show, self.gw = show, gateway

    def judge(self, p: PromoCandidate) -> IntegrityResult:
        base = self._heuristic(p)
        if self.gw is not None:
            try:
                r = self._llm(p)
                r.spoiler_types = sorted(set(r.spoiler_types) | set(base.spoiler_types))
                return r
            except Exception as exc:
                log.error("integrity LLM judge failed for %s: %s; using heuristic", p.promo_id, exc)
        return base

    def _heuristic(self, p: PromoCandidate) -> IntegrityResult:
        show = self.show
        segs = _segs(show, p)
        last = p.segments[-1]
        tl = show.timelines[last.episode]
        lb = tl.beats[segs[-1][2]]
        la = tl.ann[lb.beat_id]
        last_q = None
        for s in p.segments:
            for bid in s.beat_ids:
                b = next(x for x in show.timelines[s.episode].beats if x.beat_id == bid)
                for eid in b.event_ids:
                    e = show.events[eid]
                    if e.event_type == "dialogue" and "?" in e.text:
                        last_q = (s.order, b.end, e)
        answered_inside = False
        if last_q:
            # a question is "answered inside" if substantial dialogue follows it in the promo
            o, t, e = last_q
            after = [ev for s in p.segments if s.order >= o for bid in s.beat_ids
                     for b in [next(x for x in show.timelines[s.episode].beats if x.beat_id == bid)]
                     if (s.order > o or b.start >= t) for ev in (show.events[i] for i in b.event_ids)
                     if ev.event_type == "dialogue"]
            answered_inside = len(after) >= 2
        g, gap_note = info_gap(show, segs)
        S, spoil = spoilers(show, segs)
        first_chars = show.beat_feat[show.timelines[p.segments[0].episode].beats[segs[0][1]].beat_id].characters
        first_seg_chars = set().union(*(show.beat_feat[show.timelines[segs[0][0]].beats[k].beat_id].characters
                                        for k in range(segs[0][1], segs[0][2] + 1)))
        last_seg_chars = set().union(*(show.beat_feat[tl.beats[k].beat_id].characters for k in range(segs[-1][1], segs[-1][2] + 1)))
        hook_link = bool((first_chars or first_seg_chars) & last_seg_chars)
        later = bool(gap_note and "continues" in gap_note)
        notes = []
        if gap_note:
            notes.append(gap_note)
        if not hook_link:
            notes.append("hook and ending involve different characters")
        if answered_inside:
            notes.append("the last question is answered before the cut")
        ctx = retrieve_context([s.episode for s in p.segments],
                               list(last_seg_chars | first_seg_chars), list(show.episodes.values()),
                               show.conflicts, show.characters)
        if ctx["later_developments"]:
            notes.append(f"{len(ctx['later_developments'])} later developments on these conflicts (answer exists later)")
            later = True
        faithful = all(x["event_id"] in show.events and show.events[x["event_id"]].text.startswith(x["text"][:40])
                       for x in p.evidence)
        return IntegrityResult(
            ending_leaves_question=p.scores.cliffhanger >= 5.0,
            question=(la.info_gap.question or (last_q[2].text[:160] if last_q else "")),
            answered_inside_promo=answered_inside,
            answered_later_in_series=later,
            hook_connected_to_ending=hook_link,
            coherent=p.scores.coherence >= 5.0,
            spoiler_types=sorted({s["type"] for s in spoil if s["severity"] >= 4}),
            spoiler_risk=round(S, 2),
            faithful_to_source=faithful,
            cliffhanger_score=round(p.scores.cliffhanger - (2.0 if answered_inside else 0.0), 2),
            coherence_score=round(p.scores.coherence - (1.5 if not hook_link else 0.0), 2),
            notes=notes,
            judge="heuristic",
        )

    def _llm(self, p: PromoCandidate) -> IntegrityResult:
        segs = _segs(self.show, p)
        chars = list(set().union(*(self.show.beat_feat[self.show.timelines[e].beats[k].beat_id].characters
                                   for e, i, j in segs for k in range(i, j + 1))))
        ctx = retrieve_context([s.episode for s in p.segments], chars, list(self.show.episodes.values()),
                               self.show.conflicts, self.show.characters)
        system = ("You are a senior promo editor checking a proposed promo against the story. "
                  "Use only the given text. Return JSON only.")
        prompt = (f"PROMO:\n{promo_script(self.show, p)}\n\nSTORY CONTEXT:\n{json.dumps(ctx)[:6000]}\n\n"
                  "Return JSON with keys: ending_leaves_question (bool), question, answered_inside_promo (bool), "
                  "answered_later_in_series (bool), hook_connected_to_ending (bool), coherent (bool), spoiler_types "
                  "(subset of premise, relationship, identity, major_plot_twist, resolution, character_fate), "
                  "spoiler_risk (0-10), faithful_to_source (bool), cliffhanger_score (0-10), coherence_score (0-10), notes (list).")
        r = self.gw.generate_json(f"integrity:{p.promo_id}", system, prompt, IntegrityResult)
        r.judge = self.gw.name
        return r
