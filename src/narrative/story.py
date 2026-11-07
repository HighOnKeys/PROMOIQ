"""Story Bible, narrative graph, conflict state tracking and context retrieval.

The bible is built deterministically from the screenplay (who speaks, who is named,
who clashes with whom, what the writers' own loglines say). When an LLM is configured
it is asked once, over loglines + continuity notes only (~15 KB, not 50 scripts), to
resolve descriptive aliases and name relationships; its claims are kept separate and
marked as model-asserted.
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict

import networkx as nx
from pydantic import BaseModel, Field

from src.llm.client import LenientModel
from src.parser.beats import named_characters
from src.schemas import Beat, BeatAnnotation, Episode, Event

log = logging.getLogger(__name__)

REL_WORDS = ("sister", "brother", "mother", "father", "stepmother", "husband", "wife", "fiancé", "fiance",
             "son", "daughter", "betrothed", "bride", "groom", "maid", "lover", "ally")
_REL_LOG = re.compile(
    r"\b(?:her|his)\s+(?:(?:late|new|cruel|rugged|own|elderly|lost|stoic|loyal|spiteful|treacherous|"
    r"presumed-dead)\s+)*(" + "|".join(REL_WORDS) + r")\s*,?\s+(?:Lady\s+|Lord\s+)?([A-Z][a-z]+)"
)
_REL_DLG = re.compile(r"(?i)\bmy\s+(" + "|".join(REL_WORDS) + r")\b")
RESOLVE_TERMS = re.compile(
    r"(?i)\b(apprehend\w*|arrested|proclaim\w*|exonerat\w*|freed|frees|unchain\w*|cured|reclaim\w*|"
    r"rightful duke|guards? salute|it'?s over|forgive\w*|reunit\w*|pardon\w*)\b"
)
STAGES = ("INTRODUCED", "DISCOVERED", "CONFRONTED", "ESCALATED", "RESOLVED")


def display(cid: str) -> str:
    return " ".join(w.capitalize() for w in cid.split())


def build_characters(episodes: list[Episode], events: list[Event]) -> dict[str, dict]:
    lines = Counter(e.speaker for e in events if e.event_type == "dialogue" and e.speaker)
    eps_speak: dict[str, set] = defaultdict(set)
    eps_any: dict[str, set] = defaultdict(set)
    raw_aliases: dict[str, set] = defaultdict(set)
    for e in events:
        if e.speaker:
            eps_speak[e.speaker].add(e.episode)
            raw_aliases[e.speaker].add(re.sub(r"\s*\(.*$", "", e.speaker_raw or e.speaker).strip())
        for c in e.characters:
            eps_any[c].add(e.episode)
    named = named_characters(events)
    ranked = [c for c, _ in lines.most_common()]
    chars = {}
    for rank, cid in enumerate(ranked):
        eps = sorted(eps_any[cid] | eps_speak[cid])
        is_named = cid in named
        if rank == 0:
            role = "protagonist"
        elif is_named and rank < 8:
            role = "principal"
        elif lines[cid] >= 10:
            role = "supporting"
        else:
            role = "minor"
        chars[cid] = {
            "id": cid,
            "name": display(cid),
            "is_proper_name": is_named,
            "aliases": sorted(raw_aliases[cid] - {cid}),
            "role": role,
            "dialogue_lines": lines[cid],
            "episodes": eps,
            "first_episode": eps[0] if eps else None,
            "last_episode": eps[-1] if eps else None,
            "alias_status": "named" if is_named else "descriptive label (possible alias, unresolved offline)",
        }
    return chars


def extract_relationships(episodes: list[Episode], beats: list[Beat], events: dict[str, Event],
                          characters: dict[str, dict]) -> list[dict]:
    by_name = {c["name"].split()[-1].lower(): cid for cid, c in characters.items() if c["is_proper_name"]}
    rels: dict[tuple, dict] = {}

    def add(a, rel, b, evidence, conf, source):
        if not a or not b or a == b:
            return
        rel = "fiancé" if rel == "fiance" else rel
        key = (a, rel, b)
        r = rels.setdefault(key, {"from": a, "relation": rel, "to": b, "confidence": 0.0,
                                  "evidence": [], "source": source})
        r["confidence"] = round(min(0.95, r["confidence"] + conf), 2)
        if len(r["evidence"]) < 4:
            r["evidence"].append(evidence)

    for ep in episodes:
        for sent in re.split(r"(?<=[.;])\s+", f"{ep.logline} {ep.continuity}"):
            subj_m = [by_name[w.lower()] for w in re.findall(r"\b[A-Z][a-z]+\b", sent) if w.lower() in by_name]
            for m in _REL_LOG.finditer(sent):
                rel, obj = m.group(1), by_name.get(m.group(2).lower())
                subj = next((s for s in subj_m if s != obj), None)
                add(obj, rel, subj, {"episode": ep.episode, "source": "logline/continuity", "text": sent[:200]}, 0.5,
                    "writer loglines")
    for b in beats:
        others = [s for s in b.speakers if characters.get(s, {}).get("is_proper_name")]
        for eid in b.event_ids:
            ev = events[eid]
            if ev.event_type != "dialogue" or not ev.speaker:
                continue
            for m in _REL_DLG.finditer(ev.text):
                cands = [o for o in others if o != ev.speaker]
                if len(cands) == 1:
                    add(cands[0], m.group(1).lower(), ev.speaker,
                        {"episode": ev.episode, "event_id": eid, "text": ev.text[:160]}, 0.25, "dialogue")
    return sorted((r for r in rels.values() if r["confidence"] >= 0.45),
                  key=lambda r: -r["confidence"])


_HOSTILE_CUES = set("mocking venomous cruel cold icy accusing threatening sneering gloating spiteful contemptuous "
                    "taunting scornful smug dismissive menacing vicious condescending disdainful hostile".split())
_WARM_CUES = set("gentle soft warm tender loving concerned reassuring affectionate grateful relieved "
                 "sincere earnest kind protective soothing comforting".split())
_FRIENDLY = re.compile(r"(?i)\b(thank you|please|help|my lady|forgive|trust|together|safe|darling|my love)\b")


def pair_stance(b: Beat, p: str, q: str, events: dict[str, Event]) -> tuple[float, float]:
    """(hostility, warmth) expressed between p and q in one beat, from their own lines."""
    from src.dramatic import lexicon as L

    host = warm = 0.0
    for eid in b.event_ids:
        e = events[eid]
        if e.event_type != "dialogue" or e.speaker not in (p, q):
            continue
        cues = {w for c in e.delivery for w in re.findall(r"[a-z]+", c.lower())}
        host += L.hits("confront", e.text) + L.hits("betrayal", e.text) + L.hits("threat", e.text) + len(cues & _HOSTILE_CUES)
        warm += L.hits("romance", e.text) + L.hits("rescue", e.text) + len(_FRIENDLY.findall(e.text)) + len(cues & _WARM_CUES)
    return host, warm


def build_conflicts(beats: list[Beat], ann: dict[str, BeatAnnotation], T: dict[str, float],
                    characters: dict[str, dict], events: dict[str, Event] | None = None,
                    min_events: int = 3) -> dict[str, dict]:
    """Antagonistic character pairs. Being together in a tense scene is NOT enough: allies
    share tense scenes too (an early version labelled husband and wife as a 'conflict').
    A pair counts only if their own lines towards each other are more hostile than warm."""
    named = {cid for cid, c in characters.items() if c["is_proper_name"]}
    pairs: dict[tuple, list] = defaultdict(list)
    stance: dict[tuple, list] = defaultdict(lambda: [0.0, 0.0])
    for b in beats:
        a = ann[b.beat_id]
        people = sorted(set(b.characters) & named)
        for i in range(len(people)):
            for j in range(i + 1, len(people)):
                key = (people[i], people[j])
                if events is not None:
                    h, w = pair_stance(b, *key, events)
                    stance[key][0] += h
                    stance[key][1] += w
                if a.scores.get("conflict_intensity", 0) >= 2.5 or "threat" in a.categories:
                    pairs[key].append(b)
    conflicts = {}
    for (p, q), bl in pairs.items():
        if len(bl) < min_events:
            continue
        host, warm = stance[(p, q)]
        if events is not None and not (host >= 3 and host >= 1.5 * warm):
            continue
        bl = sorted(bl, key=lambda b: (b.episode, b.start))
        stages = []
        peak = 0.0
        for k, b in enumerate(bl):
            a = ann[b.beat_id]
            t = T[b.beat_id]
            if RESOLVE_TERMS.search(b.text) and k >= len(bl) // 2:
                st = "RESOLVED"
            elif k == 0:
                st = "INTRODUCED"
            elif "revelation" in a.categories or "identity_reveal" in a.categories:
                st = "DISCOVERED"
            elif t > peak * 1.05 and k > 1:
                st = "ESCALATED"
            else:
                st = "CONFRONTED"
            peak = max(peak, t)
            stages.append({"beat_id": b.beat_id, "episode": b.episode, "start": b.start, "end": b.end,
                           "stage": st, "tension": round(t, 2), "categories": a.categories,
                           "evidence": b.text[:180]})
        cid = f"{p}|{q}"
        conflicts[cid] = {
            "id": cid, "characters": [p, q], "events": stages, "size": len(stages),
            "first_episode": stages[0]["episode"], "last_episode": stages[-1]["episode"],
            "resolved_in_episode": next((s["episode"] for s in stages if s["stage"] == "RESOLVED"), None),
            "hostility": round(host, 1), "warmth": round(warm, 1),
            "note": "Stages are heuristic labels; storylines need not follow the canonical order.",
        }
    return dict(sorted(conflicts.items(), key=lambda kv: -kv[1]["size"]))


def build_graph(characters: dict, relationships: list[dict], conflicts: dict, beats: list[Beat],
                ann: dict[str, BeatAnnotation], T: dict[str, float], top_events: int = 150) -> nx.MultiDiGraph:
    G = nx.MultiDiGraph()
    for cid, c in characters.items():
        G.add_node(f"char:{cid}", kind="character", name=c["name"], role=c["role"])
    for r in relationships:
        G.add_edge(f"char:{r['from']}", f"char:{r['to']}", key="RELATED_TO", type="RELATED_TO",
                   relation=r["relation"], confidence=r["confidence"], evidence=r["evidence"][:2])
    ranked = sorted(beats, key=lambda b: -(T[b.beat_id] + 0.5 * ann[b.beat_id].scores.get("narrative_importance", 0)))
    keep = {b.beat_id for b in ranked[:top_events]}
    for cf in conflicts.values():
        keep.update(e["beat_id"] for e in cf["events"])
    bmap = {b.beat_id: b for b in beats}
    for bid in keep:
        b, a = bmap[bid], ann[bid]
        G.add_node(f"event:{bid}", kind="event", episode=b.episode, start=b.start, end=b.end,
                   categories=a.categories, tension=round(T[bid], 2), evidence=b.text[:200])
        for c in b.characters:
            G.add_edge(f"char:{c}", f"event:{bid}", key="PARTICIPATES_IN", type="PARTICIPATES_IN")
        speakers = b.speakers
        for s in speakers:
            for o in b.characters:
                if o == s:
                    continue
                for cat, etype in (("confrontation", "CONFRONTS"), ("betrayal", "BETRAYS"), ("rescue", "RESCUES"),
                                   ("revelation", "REVEALS")):
                    if cat in a.categories:
                        G.add_edge(f"char:{s}", f"char:{o}", key=f"{etype}:{bid}", type=etype, event=bid,
                                   episode=b.episode, start=b.start)
    for cid, cf in conflicts.items():
        G.add_node(f"conflict:{cid}", kind="conflict", characters=cf["characters"], size=cf["size"])
        prev = None
        for st in cf["events"]:
            ev = f"event:{st['beat_id']}"
            G.add_edge(ev, f"conflict:{cid}", key=st["stage"], type=st["stage"] if st["stage"] in ("RESOLVED",) else "PART_OF",
                       stage=st["stage"])
            if st["stage"] == "RESOLVED":
                G.add_edge(ev, f"conflict:{cid}", key="RESOLVES", type="RESOLVES")
            if prev is not None:
                etype = "ESCALATES" if st["tension"] > prev["tension"] else "FOLLOWS"
                G.add_edge(f"event:{prev['beat_id']}", ev, key=f"{etype}:{cid}", type=etype, conflict=cid,
                           note="temporal succession within a conflict; not proven causation")
            prev = st
    return G


def graph_to_json(G: nx.MultiDiGraph) -> dict:
    return nx.node_link_data(G, edges="edges")


class StoryBibleLLM(LenientModel):
    aliases: dict[str, str] = Field(default_factory=dict)
    relationships: list[dict] = Field(default_factory=list)
    premise: str = ""


BIBLE_SYSTEM = ("You build a story bible for a TV microdrama. Use only the text given. "
                "If unsure, omit. Return JSON only.")


def llm_enrich(gateway, episodes: list[Episode], characters: dict) -> StoryBibleLLM | None:
    if gateway is None:
        return None
    lines = [f"E{e.episode}: {e.logline} || {e.continuity}" for e in episodes]
    speakers = ", ".join(sorted(characters))
    prompt = ("EPISODE LOGLINES || CONTINUITY NOTES:\n" + "\n".join(lines) +
              f"\n\nSPEAKER LABELS USED IN SCRIPTS: {speakers}\n\n"
              "Return {\"aliases\": {DESCRIPTIVE_LABEL: CANONICAL_NAMED_LABEL} only where the loglines make it clear, "
              "\"relationships\": [{\"from\":LABEL,\"relation\":str,\"to\":LABEL,\"evidence_episode\":int}], "
              "\"premise\": one sentence}.")
    try:
        return gateway.generate_json("story_bible", BIBLE_SYSTEM, prompt, StoryBibleLLM)
    except Exception as exc:
        log.error("story bible LLM enrichment failed: %s", exc)
        return None


def retrieve_context(promo_episodes: list[int], promo_chars: list[str], episodes: list[Episode],
                     conflicts: dict, characters: dict, k_prev: int = 3) -> dict:
    """Targeted cross-episode context for one promo (instead of sending all 50 scripts)."""
    first = min(promo_episodes)
    last = max(promo_episodes)
    names = [characters[c]["name"] for c in promo_chars if c in characters]
    def mentions(ep):
        txt = f"{ep.logline} {ep.continuity}"
        return any(n.split()[-1] in txt for n in names)
    prior = [e for e in episodes if e.episode < first and mentions(e)][-k_prev:]
    rel_conf = [cf for cf in conflicts.values() if set(cf["characters"]) <= set(promo_chars) or
                (set(cf["characters"]) & set(promo_chars) and len(promo_chars) <= 2)]
    later = []
    for cf in rel_conf:
        for st in cf["events"]:
            if st["episode"] > last:
                later.append({"conflict": cf["id"], **st})
    return {
        "prior_episodes": [{"episode": e.episode, "logline": e.logline} for e in prior],
        "this_episode": [{"episode": e.episode, "logline": e.logline, "continuity": e.continuity}
                         for e in episodes if first <= e.episode <= last],
        "conflicts": [{"id": cf["id"], "first_episode": cf["first_episode"],
                       "resolved_in_episode": cf["resolved_in_episode"]} for cf in rel_conf],
        "later_developments": later[:12],
    }
