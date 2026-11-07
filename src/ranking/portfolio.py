"""Duplicate detection, diversity-aware shortlist (MMR) and boundary counterfactuals."""
from __future__ import annotations

import numpy as np

from src.construction.scoring import score
from src.construction.show import Show
from src.schemas import PromoCandidate


# Measured on the full run: unrelated promos from different episodes have median raw cosine 0.38,
# p90 0.58, p99 0.71; time-overlapping duplicates median 0.80; the E1/E4 altar retelling 0.745.
SEM_BASELINE = 0.55


def _intervals(p: PromoCandidate):
    return [(s.episode, s.start, s.end) for s in p.segments]


def time_overlap(a: PromoCandidate, b: PromoCandidate) -> float:
    """Overlapping seconds / shorter promo duration."""
    ov = 0.0
    for ea, sa, ta in _intervals(a):
        for eb, sb, tb in _intervals(b):
            if ea == eb:
                ov += max(0.0, min(ta, tb) - max(sa, sb))
    return ov / max(1e-6, min(a.duration, b.duration))


def beat_jaccard(a: PromoCandidate, b: PromoCandidate) -> float:
    A = {x for s in a.segments for x in s.beat_ids}
    B = {x for s in b.segments for x in s.beat_ids}
    return len(A & B) / max(1, len(A | B))


class PromoSimilarity:
    def __init__(self, show: Show, seg_sim, segs_of):
        self.show, self.seg_sim, self.segs_of = show, seg_sim, segs_of
        self._vec = {}

    def vec(self, p: PromoCandidate):
        if p.promo_id not in self._vec:
            self._vec[p.promo_id] = self.seg_sim.promo_vec(self.segs_of(p))
        return self._vec[p.promo_id]

    def scenes(self, p: PromoCandidate) -> set:
        out = set()
        for s in p.segments:
            for bid in s.beat_ids:
                tl = self.show.timelines[s.episode]
                out.add((s.episode, next(b.scene for b in tl.beats if b.beat_id == bid)))
        return out

    def detail(self, a: PromoCandidate, b: PromoCandidate) -> dict:
        sem = float(np.dot(self.vec(a), self.vec(b)))
        to, bj = time_overlap(a, b), beat_jaccard(a, b)
        sa, sb = self.scenes(a), self.scenes(b)
        same_scene = len(sa & sb) / max(1, min(len(sa), len(sb)))
        # Scenes from one show share vocabulary, so unrelated promos already sit around 0.4-0.6 raw
        # cosine. Rescale so only genuinely similar text counts (see SEM_BASELINE).
        sem_cal = max(0.0, (sem - SEM_BASELINE) / (1 - SEM_BASELINE))
        return {"time_overlap": round(to, 3), "shared_beats": round(bj, 3), "semantic": round(sem, 3),
                "semantic_calibrated": round(sem_cal, 3), "same_scene": round(same_scene, 3),
                "combined": round(max(to, bj, sem_cal, 0.6 * same_scene), 3)}

    def __call__(self, a, b) -> float:
        return self.detail(a, b)["combined"]


def mmr_shortlist(cands: list[PromoCandidate], quality: dict[str, float], sim: PromoSimilarity, k: int,
                  lam: float, min_quality_ratio: float, dup_threshold: float) -> tuple[list, list]:
    """MMR(p) = lam*Q(p) - (1-lam)*max_s Sim(p, s). Q normalised to 0-1. Returns (shortlist, dup_log)."""
    if not cands:
        return [], []
    best = max(quality[c.promo_id] for c in cands)
    pool = [c for c in sorted(cands, key=lambda c: -quality[c.promo_id])
            if quality[c.promo_id] >= min_quality_ratio * best][:250]
    selected: list[PromoCandidate] = []
    dup_log = []
    themes_used: set = set()
    modes_used: set = set()
    while pool and len(selected) < k:
        best_c, best_v, best_sim = None, -1e9, None
        for c in pool:
            q = quality[c.promo_id] / 10.0
            sims = [(sim(c, s), s) for s in selected]
            mx, who = max(sims, key=lambda x: x[0]) if sims else (0.0, None)
            # small novelty bonuses for an unseen promo angle / construction mode
            v = (lam * q - (1 - lam) * mx + (0.02 if c.theme not in themes_used else 0.0)
                 + (0.05 if c.mode not in modes_used else 0.0))   # editors should see both formats
            if v > best_v:
                best_c, best_v, best_sim = c, v, (mx, who)
        selected.append(best_c)
        themes_used.add(best_c.theme)
        modes_used.add(best_c.mode)
        pool.remove(best_c)
        # flag (do not auto-reject) near-duplicates of what was just chosen
        for c in pool:
            d = sim.detail(c, best_c)
            if d["combined"] >= dup_threshold:
                dup_log.append({"candidate": c.promo_id, "similar_to": best_c.promo_id, **d})
    return selected, dup_log


def counterfactuals(show: Show, segs, weights, sim_fn, constraints) -> list[dict]:
    """Earlier/later opening, earlier/later ending: re-score each valid variant."""
    base = score(show, segs, weights, sim_fn).scores.quality
    out = []
    first, last = segs[0], segs[-1]
    variants = {
        "earlier_opening": ((first[0], first[1] - 1, first[2]),) + tuple(segs[1:]),
        "later_opening": ((first[0], first[1] + 1, first[2]),) + tuple(segs[1:]),
        "earlier_ending": tuple(segs[:-1]) + ((last[0], last[1], last[2] - 1),),
        "later_ending": tuple(segs[:-1]) + ((last[0], last[1], last[2] + 1),),
    }
    for name, v in variants.items():
        ok = all(0 <= i <= j < len(show.timelines[e].beats) for e, i, j in v)
        if not ok:
            out.append({"variant": name, "valid": False, "reason": "no source cut point"})
            continue
        if len(segs) == 1:
            pass
        dur = sum(show.timelines[e].beats[j].end - show.timelines[e].beats[i].start for e, i, j in v)
        if not (constraints.min_duration <= dur <= constraints.max_duration):
            out.append({"variant": name, "valid": False, "reason": f"duration {dur:.1f}s out of 20-60s"})
            continue
        sc = score(show, v, weights, sim_fn).scores
        tl0, tlL = show.timelines[v[0][0]], show.timelines[v[-1][0]]
        out.append({"variant": name, "valid": True, "duration": round(dur, 2), "quality": sc.quality,
                    "delta": round(sc.quality - base, 3), "hook": sc.hook, "cliffhanger": sc.cliffhanger,
                    "start": tl0.beats[v[0][1]].start_raw, "end": tlL.beats[v[-1][2]].end_raw,
                    "preferred": "variant" if sc.quality > base + 1e-9 else "original"})
    return out
