"""Transparent promo objective.

Q(P) = 0.20H + 0.20K + 0.20C + 0.15E + 0.15G + 0.10N - lambda*R - mu*S   (components 0-10)

A promo is P = ((ep, i, j), ...): ordered beat ranges, each inclusive.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.construction.show import Show
from src.schemas import PromoScores

Seg = tuple  # (episode, i, j)

_FATE = re.compile(r"(?i)\b(is dead|was killed|died|has died|executed|hanged|perished|buried)\b")
_IDENT = re.compile(r"(?i)\b(true identity|who you really are|is the duke|rightful duke|your son|alive|crest|"
                    r"presumed[- ]dead|in disguise|real name|heir)\b")
_ROM_RES = re.compile(r"(?i)\b(i love you|kiss\w*|proclaim\w* (her|him)|my duchess|now pronounce|wed\b)")


@dataclass
class Breakdown:
    scores: PromoScores
    duration: float
    notes: dict = field(default_factory=dict)
    spoilers: list = field(default_factory=list)


def seg_duration(show: Show, s: Seg) -> float:
    tl = show.timelines[s[0]]
    return round(tl.beats[s[2]].end - tl.beats[s[1]].start, 3)


def promo_duration(show: Show, segs) -> float:
    return round(sum(seg_duration(show, s) for s in segs), 3)


def beats_of(show: Show, segs):
    for ep, i, j in segs:
        tl = show.timelines[ep]
        for k in range(i, j + 1):
            yield ep, k, tl.beats[k]


def clarity(show: Show, segs) -> tuple[float, list[str]]:
    feats = [show.beat_feat[b.beat_id] for _, _, b in beats_of(show, segs)]
    speakers = set().union(*(f.speakers for f in feats)) if feats else set()
    visible = set().union(*(f.visible for f in feats)) if feats else set()
    mentioned = set().union(*(f.dialogue_mentions for f in feats)) if feats else set()
    missing = []
    c = 6.0
    n = len(speakers)
    if 2 <= n <= 4:
        c += 1.0
    elif n >= 6:
        c -= 1.0
        missing.append(f"{n} speaking characters is a lot for a cold viewer")
    elif n == 0:
        c -= 1.5
        missing.append("no dialogue: situation must be read from visuals alone")
    elif n == 1:
        c -= 0.5
    if sum(f.context_words for f in feats) > 0:
        c += 1.0
    else:
        missing.append("no line establishes who these people are to each other")
    if max((f.conflict for f in feats), default=0) >= 3.0:
        c += 1.0
    else:
        missing.append("central conflict is not stated on screen")
    br = sum(f.backrefs for f in feats)
    if br:
        c -= min(2.0, 0.6 * br)
        missing.append(f"{br} reference(s) to earlier events (e.g. 'again', 'remember')")
    unseen = mentioned - visible
    if unseen:
        c -= min(1.6, 0.4 * len(unseen))
        missing.append("names off-screen characters: " + ", ".join(sorted(show.characters[u]["name"] for u in unseen if u in show.characters)))
    if feats and feats[0].first_line_conj:
        c -= 1.0
        missing.append("first line starts mid-conversation")
    eps = {s[0] for s in segs}
    c -= 0.8 * (len(eps) - 1) + 0.3 * (len(segs) - len(eps))
    if len(eps) > 1:
        missing.append(f"jumps across {len(eps)} episodes")
    return max(0.0, min(10.0, c)), missing


def emotion(show: Show, segs) -> float:
    Ts, ws = [], []
    for ep, k, b in beats_of(show, segs):
        Ts.append(show.timelines[ep].T[k])
        ws.append(max(0.1, b.duration))
    if not Ts:
        return 0.0
    n = len(Ts)
    tot = sum(ws)
    mean = sum(t * w for t, w in zip(Ts, ws)) / tot
    third = max(1, n // 3)
    mid = Ts[third:n - third] or Ts
    last = Ts[n - third:]
    rise = max(0.0, min(1.0, (sum(last) / len(last) - sum(mid) / len(mid)) / 2 + 0.5))
    peak = Ts.index(max(Ts)) / max(1, n - 1)
    late = 1.0 if peak >= 0.6 else (0.5 if peak >= 0.3 else 0.25)
    return round(10 * (0.5 * min(1.0, mean / 2.5) + 0.3 * rise + 0.2 * late), 3)


def info_gap(show: Show, segs) -> tuple[float, str]:
    ep, i, j = segs[-1]
    tl = show.timelines[ep]
    last = tl.beats[j]
    a = tl.ann[last.beat_id]
    g = 0.6 * a.scores.get("curiosity", 0)               # up to 3
    stakes = max(tl_.ann[b.beat_id].scores.get("stakes", 0) for e_, _, b in beats_of(show, segs) for tl_ in [show.timelines[e_]])
    g += 0.5 * stakes                                    # up to 2.5
    note = ""
    last_chars = set().union(*(show.beat_feat[tl.beats[k].beat_id].characters for k in range(i, j + 1)))
    open_conf, closed = [], []
    in_promo = {b.beat_id for _, _, b in beats_of(show, segs)}
    for cf in show.conflicts.values():
        if not set(cf["characters"]) <= last_chars:
            continue
        stages_in = [st for st in cf["events"] if st["beat_id"] in in_promo]
        later = [st for st in cf["events"] if (st["episode"], st["start"]) > (ep, last.end)]
        if any(st["stage"] == "RESOLVED" for st in stages_in):
            closed.append(cf["id"])
        elif later:
            open_conf.append(cf["id"])
    if open_conf:
        g += 2.5
        note = f"conflict {open_conf[0].replace('|', ' vs ')} continues after the cut"
    if closed:
        g -= 3.0
        note = f"conflict {closed[0].replace('|', ' vs ')} is resolved inside the promo"
    if a.info_gap.question or (j > i and tl.ann[tl.beats[j - 1].beat_id].info_gap.question):
        g += 1.0
    g -= 0.5 * a.resolution_signal
    return max(0.0, min(10.0, g)), note


def coherence(show: Show, segs, sim_fn=None) -> tuple[float, float]:
    """Returns (N, R)."""
    if len(segs) == 1:
        ep, i, j = segs[0]
        locs = []
        n = 9.0
        for k in range(i, j + 1):
            f = show.beat_feat[show.timelines[ep].beats[k].beat_id]
            if not locs or f.location != locs[-1][0]:
                locs.append((f.location, f.continuous_heading))
        jumps = sum(1 for loc, cont in locs[1:] if not cont)
        n -= 1.5 * jumps
        return max(0.0, min(10.0, n)), 0.0
    n = 6.0
    sims = []
    for a, b in zip(segs, segs[1:]):
        ca = set().union(*(show.beat_feat[show.timelines[a[0]].beats[k].beat_id].characters for k in range(a[1], a[2] + 1)))
        cb = set().union(*(show.beat_feat[show.timelines[b[0]].beats[k].beat_id].characters for k in range(b[1], b[2] + 1)))
        jac = len(ca & cb) / max(1, len(ca | cb))
        n += 2.0 * jac / (len(segs) - 1)
        ba = {cid for k in range(a[1], a[2] + 1) for cid, _ in show.beat_conflicts.get(show.timelines[a[0]].beats[k].beat_id, [])}
        bb = {cid for k in range(b[1], b[2] + 1) for cid, _ in show.beat_conflicts.get(show.timelines[b[0]].beats[k].beat_id, [])}
        if ba & bb:
            n += 1.5 / (len(segs) - 1)
        n -= 0.3 * abs(b[0] - a[0])
        if show.beat_feat[show.timelines[b[0]].beats[b[1]].beat_id].first_line_conj:
            n -= 1.5                                     # joined segment starts mid-sentence
        ta = max(show.timelines[a[0]].T[a[1]:a[2] + 1])
        tb = max(show.timelines[b[0]].T[b[1]:b[2] + 1])
        if tb >= ta:
            n += 0.5 / (len(segs) - 1)
        if sim_fn is not None:
            sims.append(sim_fn(a, b))
    r = 0.0
    if sims:
        worst = max(sims)
        r = 10.0 * max(0.0, worst - 0.5) / 0.5
        if worst > 0.75:
            n -= 3.0
    return max(0.0, min(10.0, n)), round(r, 3)


def spoilers(show: Show, segs) -> tuple[float, list[dict]]:
    """Spoiler taxonomy. Late != spoiler: we look at what the moment gives away."""
    found = []
    n_eps = max(show.episodes)
    for ep, k, b in beats_of(show, segs):
        a = show.timelines[ep].ann[b.beat_id]
        txt = b.text
        for cid, stage in show.beat_conflicts.get(b.beat_id, []):
            if stage == "RESOLVED":
                found.append({"type": "resolution", "severity": 6, "beat_id": b.beat_id,
                              "why": f"resolves conflict {cid.replace('|', ' vs ')}"})
        m = _IDENT.search(txt)
        if m and ("identity_reveal" in a.categories or "revelation" in a.categories):
            premise = any(w in m.group(0).lower() for w in show.premise_terms)
            found.append({"type": "identity" if not premise else "premise", "severity": 1 if premise else 5,
                          "beat_id": b.beat_id,
                          "why": f"'{m.group(0)}'" + (" is part of the show's own title premise" if premise else " exposes an identity")})
        if _FATE.search(txt):
            found.append({"type": "character_fate", "severity": 6, "beat_id": b.beat_id, "why": f"'{_FATE.search(txt).group(0)}'"})
        if ep > 0.7 * n_eps and a.scores.get("reveal_value", 0) >= 3.5:
            found.append({"type": "major_plot_twist", "severity": 4, "beat_id": b.beat_id,
                          "why": f"high-value reveal in episode {ep} of {n_eps}"})
        if ep > 0.6 * n_eps and _ROM_RES.search(txt):
            found.append({"type": "relationship", "severity": 2, "beat_id": b.beat_id,
                          "why": f"late romantic payoff '{_ROM_RES.search(txt).group(0)}'"})
    if not found:
        return 0.0, []
    sev = sorted((f["severity"] for f in found), reverse=True)
    return min(10.0, sev[0] + 0.5 * (len(sev) - 1)), found


def score(show: Show, segs, weights, sim_fn=None) -> Breakdown:
    first, last = segs[0], segs[-1]
    H = show.timelines[first[0]].hook_at[first[1]]
    K = show.timelines[last[0]].cliff_at[last[2]]
    C, missing = clarity(show, segs)
    E = emotion(show, segs)
    G, gap_note = info_gap(show, segs)
    N, R = coherence(show, segs, sim_fn)
    S, spoil = spoilers(show, segs)
    w = weights
    Q = (w.hook * H + w.cliffhanger * K + w.clarity * C + w.emotion * E + w.info_gap * G + w.coherence * N
         - w.redundancy_lambda * R - w.spoiler_mu * S)
    sc = PromoScores(hook=round(H, 2), cliffhanger=round(K, 2), clarity=round(C, 2), emotion=round(E, 2),
                     info_gap=round(G, 2), coherence=round(N, 2), redundancy=round(R, 2), spoiler=round(S, 2),
                     quality=round(Q, 3))
    return Breakdown(sc, promo_duration(show, segs),
                     notes={"missing_context": missing, "gap": gap_note,
                            "hook": show.timelines[first[0]].hook_note[first[1]],
                            "cliff": show.timelines[last[0]].cliff_note[last[2]]},
                     spoilers=spoil)
