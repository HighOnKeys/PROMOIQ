"""Show: everything the search and evaluators need, precomputed once."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.dramatic import lexicon as L
from src.dramatic.timeline import EpisodeTimeline
from src.schemas import Beat, Episode, Event

_TITLE_STOP = {"i", "a", "he", "she", "was", "married", "the", "an", "of"}


@dataclass
class BeatFeat:
    speakers: set
    characters: set
    visible: set            # speakers + characters named in action/camera text
    dialogue_mentions: set  # named characters mentioned in dialogue
    backrefs: int
    context_words: int
    conflict: float
    first_line_conj: bool
    location: str
    continuous_heading: bool
    spoiler_terms: list = field(default_factory=list)


@dataclass
class Show:
    title: str
    episodes: dict[int, Episode]
    events: dict[str, Event]
    timelines: dict[int, EpisodeTimeline]
    characters: dict[str, dict]
    conflicts: dict[str, dict]
    beat_feat: dict[str, BeatFeat] = field(default_factory=dict)
    beat_conflicts: dict[str, list] = field(default_factory=dict)   # beat_id -> [(conflict_id, stage)]
    premise_terms: set = field(default_factory=set)

    def __post_init__(self):
        self.premise_terms = {w.lower() for w in re.findall(r"[A-Za-z]+", self.title)} - _TITLE_STOP
        named = {c for c, v in self.characters.items() if v.get("is_proper_name")}
        name_of = {c: self.characters[c]["name"] for c in named}
        for cf in self.conflicts.values():
            for st in cf["events"]:
                self.beat_conflicts.setdefault(st["beat_id"], []).append((cf["id"], st["stage"]))
        scene_heading = {(e.episode, s.scene): s.heading for e in self.episodes.values() for s in e.scenes}
        for tl in self.timelines.values():
            for b in tl.beats:
                evs = [self.events[e] for e in b.event_ids]
                dlg = [e for e in evs if e.event_type == "dialogue"]
                desc_text = " ".join(e.text for e in evs if e.event_type != "dialogue")
                dlg_text = " ".join(e.text for e in dlg)
                visible = set(b.speakers) | {c for c in named if re.search(rf"\b{re.escape(name_of[c])}\b", desc_text)}
                mentions = {c for c in named if re.search(rf"\b{re.escape(name_of[c])}\b", dlg_text)}
                heading = scene_heading.get((b.episode, b.scene), "")
                loc = re.sub(r"\s*[–-]\s*(DAY|NIGHT|CONTINUOUS|LATER|DAWN|DUSK|MOMENTS LATER|EVENING|MORNING).*$", "", heading, flags=re.I).strip()
                self.beat_feat[b.beat_id] = BeatFeat(
                    speakers=set(b.speakers),
                    characters=set(b.characters),
                    visible=visible,
                    dialogue_mentions=mentions,
                    backrefs=L.hits("backref", dlg_text),
                    context_words=L.hits("context", b.text),
                    conflict=tl.ann[b.beat_id].scores.get("conflict_intensity", 0.0),
                    first_line_conj=bool(dlg and (re.match(r"^(and|but|so|then|because|which|also)\b", dlg[0].text, re.I)
                                                  or re.match(r"^(\.\.\.|…|[a-z])", dlg[0].text))),
                    location=loc.upper(),
                    continuous_heading="CONTINUOUS" in heading.upper(),
                )

    @property
    def n_episodes(self) -> int:
        return len(self.episodes)

    def beat(self, ep: int, i: int) -> Beat:
        return self.timelines[ep].beats[i]
