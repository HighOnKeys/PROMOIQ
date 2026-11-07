"""Narrative-constrained promo search.

Mode A (continuous): every legal window of 20-60 s inside an episode is enumerated
    exactly. With ~33 cut points per episode this is ~16k windows for 50 episodes, which
    is cheap and guarantees the best continuous cut for the objective is not missed.
Mode B (multi-segment): combinations explode, so we run a constrained beam search over
    a library of strong short moments (4-30 s). Partial sequences are ranked with an
    optimistic completion estimate (best reachable cliffhanger), not with the final score.
"""
from __future__ import annotations

import hashlib
import logging
from collections import Counter

import numpy as np

from src.construction.scoring import Breakdown, promo_duration, score, seg_duration
from src.construction.show import Show
from src.schemas import PromoCandidate, Segment

log = logging.getLogger(__name__)

THEMES = {
    "romance": {"romance": 1.0, "proposal": 1.5},
    "betrayal": {"betrayal": 1.5, "rejection": 0.5},
    "mystery": {"mystery": 1.2, "identity_reveal": 1.5, "revelation": 0.8},
    "revenge": {"unexpected_decision": 0.3},          # needs explicit revenge language, see theme_of
    "action": {"physical_action": 1.2, "danger": 1.2, "rescue": 1.0},
    "confrontation": {"confrontation": 0.7, "threat": 0.5},
    "family conflict": {},
}


class SegmentSimilarity:
    """Cosine similarity of segment dialogue/action text (sentence-transformers; TF-IDF fallback)."""

    def __init__(self, show: Show, model_name: str = "all-MiniLM-L6-v2"):
        self.show = show
        self.model_name = model_name
        self._vec: dict[tuple, np.ndarray] = {}
        self._model = None
        self.backend = "uninitialised"

    def text(self, seg) -> str:
        ep, i, j = seg
        tl = self.show.timelines[ep]
        parts = []
        for k in range(i, j + 1):
            for eid in tl.beats[k].event_ids:
                e = self.show.events[eid]
                if e.event_type in ("dialogue", "action"):
                    parts.append(e.text)
        return " ".join(parts) or " ".join(tl.beats[k].text for k in range(i, j + 1))

    def _load(self):
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
            self.backend = f"sentence-transformers:{self.model_name}"
        except Exception as exc:  # offline or not installed
            log.warning("sentence-transformers unavailable (%s); using TF-IDF", exc)
            self._model = "tfidf"
            self.backend = "tfidf"

    def prime(self, segs) -> None:
        self._load()
        todo = [s for s in segs if tuple(s) not in self._vec]
        if not todo:
            return
        texts = [self.text(s) for s in todo]
        if self._model == "tfidf":
            from sklearn.feature_extraction.text import TfidfVectorizer

            allsegs = list(self._vec) + todo
            vec = TfidfVectorizer(stop_words="english").fit([self.text(s) for s in allsegs])
            for s in allsegs:
                v = vec.transform([self.text(s)]).toarray()[0]
                self._vec[tuple(s)] = v / (np.linalg.norm(v) or 1.0)
        else:
            embs = self._model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
            for s, v in zip(todo, embs):
                self._vec[tuple(s)] = np.asarray(v)

    def __call__(self, a, b) -> float:
        a, b = tuple(a), tuple(b)
        if a not in self._vec or b not in self._vec:
            self.prime([a, b])
        return float(np.dot(self._vec[a], self._vec[b]))

    def promo_vec(self, segs) -> np.ndarray:
        self.prime([tuple(s) for s in segs])
        v = np.sum([self._vec[tuple(s)] for s in segs], axis=0)
        return v / (np.linalg.norm(v) or 1.0)


def theme_of(categories: Counter, text: str) -> str:
    best, best_v = "family conflict", 0.0
    for theme, w in THEMES.items():
        v = sum(categories.get(c, 0) * x for c, x in w.items())
        if theme == "family conflict":
            v = 0.6 * sum(text.lower().count(k) for k in ("mother", "sister", "father", "family", "stepmother", "daughter", "disown"))
            v += 0.3 * categories.get("confrontation", 0) if v else 0
        if theme == "revenge":
            v += 1.5 * sum(text.lower().count(k) for k in ("vengeance", "revenge", "pay for", "reclaim", "retribution",
                                                           "avenge", "make them pay", "you will pay"))
        if v > best_v:
            best, best_v = theme, v
    return best


def _cid(mode: str, segs) -> str:
    key = "|".join(f"{e}:{i}:{j}" for e, i, j in segs)
    return f"{'C' if mode == 'continuous' else 'M'}-" + hashlib.md5(key.encode()).hexdigest()[:8]


def materialise(show: Show, segs, bd: Breakdown, mode: str, generator: str = "promoiq") -> PromoCandidate:
    segments = []
    cats: Counter = Counter()
    chars: Counter = Counter()
    evidence = []
    alltext = []
    for order, (ep, i, j) in enumerate(segs, 1):
        tl = show.timelines[ep]
        bi, bj = tl.beats[i], tl.beats[j]
        segments.append(Segment(order=order, episode=ep, start=bi.start, end=bj.end, start_raw=bi.start_raw,
                                end_raw=bj.end_raw, beat_ids=[tl.beats[k].beat_id for k in range(i, j + 1)]))
        for k in range(i, j + 1):
            b = tl.beats[k]
            cats.update(tl.ann[b.beat_id].categories)
            alltext.append(b.text)
            for eid in b.event_ids:
                e = show.events[eid]
                if e.speaker:
                    chars[e.speaker] += 1
    # evidence: hook event, cliff event, peak-tension event (all real source events)
    f_ep, f_i, _ = segs[0]
    tl0 = show.timelines[f_ep]
    win = tl0.window_events(tl0.beats[f_i].start, tl0.beats[f_i].start + 3.0)
    if win:
        hook_ev = max(win, key=lambda e: tl0.signals[e.event_id].punch)
        evidence.append({"role": "hook", **_ev(hook_ev)})
    l_ep, _, l_j = segs[-1]
    tlL = show.timelines[l_ep]
    lastb = tlL.beats[l_j]
    tail = [show.events[e] for e in lastb.event_ids]
    cliff_ev = max(tail, key=lambda e: (e.event_type == "dialogue", e.end))
    evidence.append({"role": "cliffhanger", **_ev(cliff_ev)})
    peak = max(((ep, k) for ep, i, j in segs for k in range(i, j + 1)), key=lambda x: show.timelines[x[0]].T[x[1]])
    pb = show.timelines[peak[0]].beats[peak[1]]
    pe = max((show.events[e] for e in pb.event_ids), key=lambda e: show.timelines[peak[0]].signals[e.event_id].C + show.timelines[peak[0]].signals[e.event_id].S)
    if pe.event_id not in {x["event_id"] for x in evidence}:
        evidence.append({"role": "peak_tension", **_ev(pe)})
    theme = theme_of(cats, " ".join(alltext))
    sc = bd.scores
    cand = PromoCandidate(
        promo_id=_cid(mode, segs), mode=mode, segments=segments, duration=bd.duration, scores=sc,
        hook=bd.notes.get("hook", ""), cliffhanger=bd.notes.get("cliff", ""), theme=theme,
        categories=[c for c, _ in cats.most_common(6)],
        characters=[show.characters.get(c, {}).get("name", c) for c, _ in chars.most_common(5)],
        evidence=evidence, generator=generator,
    )
    cand.why = why_line(show, cand, bd)
    cand.flags = [f"spoiler:{s['type']}" for s in bd.spoilers if s["severity"] >= 4]
    return cand


def _ev(e) -> dict:
    return {"episode": e.episode, "scene": e.scene, "event_id": e.event_id, "type": e.event_type,
            "start": e.start_raw, "end": e.end_raw, "speaker": e.speaker, "text": e.text[:240],
            "source": f"{e.source.file}:{e.source.line}"}


def why_line(show: Show, cand: PromoCandidate, bd: Breakdown) -> str:
    s = bd.scores
    who = " and ".join(cand.characters[:2]) if cand.characters else "the leads"
    strengths = sorted([("a hard-hitting first 3 seconds", s.hook), ("an unresolved ending", s.cliffhanger),
                        ("a conflict a stranger can follow", s.clarity), ("rising emotion", s.emotion),
                        ("an open question the series answers later", s.info_gap)], key=lambda x: -x[1])
    top = [t for t, v in strengths[:2]]
    gap = f"; {bd.notes['gap']}" if bd.notes.get("gap") else ""
    return f"{cand.theme.capitalize()} angle with {who}: {top[0]} and {top[1]}{gap}."


# ---------------------------------------------------------------- Mode A
def continuous_candidates(show: Show, settings, sim) -> list[tuple]:
    cons, w, sc = settings.constraints, settings.weights, settings.search
    out = []
    for ep, tl in show.timelines.items():
        n = len(tl.beats)
        local = []
        for i in range(n):
            if tl.hook_at[i] < 2.0:          # cheap deterministic pre-filter
                continue
            for j in range(i, n):
                d = tl.beats[j].end - tl.beats[i].start
                if d > cons.max_duration + 1e-6:
                    break
                if d < cons.min_duration - 1e-6:
                    continue
                segs = ((ep, i, j),)
                local.append((score(show, segs, w).scores.quality, segs))
        local.sort(key=lambda x: -x[0])
        out.extend(local[: sc.max_continuous_per_episode])
    log.info("continuous: %d candidate windows kept", len(out))
    return out


# ---------------------------------------------------------------- Mode B
def segment_library(show: Show, settings) -> list[tuple]:
    sc = settings.search
    lib = []
    for ep, tl in show.timelines.items():
        n = len(tl.beats)
        for i in range(n):
            for j in range(i, n):
                d = tl.beats[j].end - tl.beats[i].start
                if d > sc.seg_max:
                    break
                if d < sc.seg_min:
                    continue
                strength = max(tl.T[i:j + 1]) + 0.25 * tl.hook_at[i] / 2 + 0.25 * tl.cliff_at[j] / 2
                lib.append((strength, (ep, i, j)))
    lib.sort(key=lambda x: -x[0])
    # keep diverse: at most 12 library moments per episode
    per_ep: Counter = Counter()
    kept = []
    for st, s in lib:
        if per_ep[s[0]] >= 12:
            continue
        if any(s[0] == k[0] and not (s[2] < k[1] or s[1] > k[2]) and abs(s[1] - k[1]) + abs(s[2] - k[2]) < 2 for _, k in kept):
            continue
        per_ep[s[0]] += 1
        kept.append((st, s))
        if len(kept) >= sc.segment_library_size:
            break
    return kept


def _chars(show: Show, seg) -> set:
    ep, i, j = seg
    tl = show.timelines[ep]
    return set().union(*(show.beat_feat[tl.beats[k].beat_id].speakers for k in range(i, j + 1)))


def beam_search(show: Show, settings, sim) -> list[tuple]:
    cons, w, sc = settings.constraints, settings.weights, settings.search
    lib = [s for _, s in segment_library(show, settings)]
    sim.prime(lib)
    protagonists = {c for c, v in show.characters.items() if v.get("role") == "protagonist"}
    # the protagonist is in almost every scene, so sharing them is NOT a narrative connection
    chars = {s: _chars(show, s) - protagonists for s in lib}
    confl = {s: {cid for k in range(s[1], s[2] + 1)
                 for cid, _ in show.beat_conflicts.get(show.timelines[s[0]].beats[k].beat_id, [])} for s in lib}
    pos = {s: (s[0], show.timelines[s[0]].beats[s[1]].start) for s in lib}
    cliff = {s: show.timelines[s[0]].cliff_at[s[2]] for s in lib}
    dur = {s: seg_duration(show, s) for s in lib}
    order = sorted(lib, key=lambda s: pos[s])
    # suffix max of cliffhanger strength: optimistic completion bound
    suffix_best = {}
    best = 0.0
    for s in reversed(order):
        best = max(best, cliff[s])
        suffix_best[s] = best

    def compatible(seq, s) -> bool:
        last = seq[-1]
        if pos[s] <= (last[0], show.timelines[last[0]].beats[last[2]].end + 2.9):
            return False                                  # must move forward in story time
        if s[0] - last[0] > cons.max_episode_gap:
            return False
        if s[0] == last[0] and s[1] <= last[2] + 1:
            return False                                  # contiguous = Mode A territory
        if not (chars[s] & chars[last]) and not (confl[s] & confl[last]):
            return False                                  # narrative connection: shared rival/ally or conflict
        if sum(dur[x] for x in seq) + dur[s] > cons.max_duration:
            return False
        if any(sim(x, s) > 0.80 for x in seq):
            return False                                  # retelling of the same moment
        return True

    def partial(seq) -> float:
        bd = score(show, tuple(seq), w, sim)
        s = bd.scores
        k_opt = max(s.cliffhanger, 0.85 * suffix_best.get(seq[-1], 0.0))
        return (w.hook * s.hook + w.cliffhanger * k_opt + w.clarity * s.clarity + w.emotion * s.emotion
                + w.info_gap * s.info_gap + w.coherence * s.coherence - w.redundancy_lambda * s.redundancy
                - w.spoiler_mu * s.spoiler)

    starts = sorted(lib, key=lambda s: -(show.timelines[s[0]].hook_at[s[1]] + max(show.timelines[s[0]].T[s[1]:s[2] + 1])))
    beam = [((s,), partial((s,))) for s in starts[: sc.beam_width] if dur[s] < cons.max_duration - cons.min_segment]
    complete: dict[tuple, float] = {}
    expansions = 0
    for depth in range(1, cons.max_segments):
        nxt = []
        for seq, _ in beam:
            for s in order:
                if not compatible(seq, s):
                    continue
                cand = seq + (s,)
                expansions += 1
                p = partial(cand)
                nxt.append((cand, p))
                if promo_duration(show, cand) >= cons.min_duration:
                    complete[cand] = score(show, cand, w, sim).scores.quality
        nxt.sort(key=lambda x: -x[1])
        seen, beam = set(), []
        per_start: Counter = Counter()
        for seq, p in nxt:
            key = tuple(sorted(seq))
            if key in seen or per_start[seq[0]] >= 3:      # keep the beam from collapsing onto one opener
                continue
            seen.add(key)
            per_start[seq[0]] += 1
            beam.append((seq, p))
            if len(beam) >= sc.beam_width:
                break
        if not beam:
            break
    log.info("beam search: library=%d expansions=%d complete=%d", len(lib), expansions, len(complete))
    return sorted(((q, segs) for segs, q in complete.items()), key=lambda x: -x[0])
