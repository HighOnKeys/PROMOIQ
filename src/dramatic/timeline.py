"""Per-episode timeline with tension curve, hook strength at every legal start point and
cliffhanger strength at every legal end point. Everything here is deterministic."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.dramatic.features import EventSignal, tension
from src.schemas import Beat, BeatAnnotation, Episode, Event

_CONJ = re.compile(r"^(and|but|so|then|because|which|also|anyway|well)\b", re.I)


@dataclass
class EpisodeTimeline:
    episode: Episode
    beats: list[Beat]
    ann: dict[str, BeatAnnotation]
    events: dict[str, Event]
    signals: dict[str, EventSignal]
    weights: object
    hook_window: float = 3.0
    cliff_window: float = 5.0
    T: list[float] = field(default_factory=list)
    T_smooth: list[float] = field(default_factory=list)
    hook_at: list[float] = field(default_factory=list)    # hook strength (0-10) if a promo starts at beat i
    cliff_at: list[float] = field(default_factory=list)   # cliff strength (0-10) if a promo ends after beat i
    hook_note: list[str] = field(default_factory=list)
    cliff_note: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.T = [tension(self.ann[b.beat_id].scores, self.weights) for b in self.beats]
        n = len(self.T)
        self.T_smooth = [sum(self.T[max(0, i - 1):i + 2]) / len(self.T[max(0, i - 1):i + 2]) for i in range(n)]
        self.ep_events = sorted((self.events[e] for b in self.beats for e in b.event_ids), key=lambda e: e.start)
        self.hook_at, self.hook_note = zip(*[self._hook(i) for i in range(n)]) if n else ([], [])
        self.cliff_at, self.cliff_note = zip(*[self._cliff(i) for i in range(n)]) if n else ([], [])
        self.hook_at, self.cliff_at = list(self.hook_at), list(self.cliff_at)
        self.hook_note, self.cliff_note = list(self.hook_note), list(self.cliff_note)

    # ------------------------------------------------------------------ hooks
    def window_events(self, a: float, b: float) -> list[Event]:
        return [e for e in self.ep_events if a - 1e-6 <= e.start < b]

    def beat_at(self, t: float) -> int:
        """Index of the beat containing time t (used to score arbitrary, non-snapped clips)."""
        for i, b in enumerate(self.beats):
            if b.start - 1e-6 <= t < b.end - 1e-6:
                return i
        return len(self.beats) - 1 if self.beats and t >= self.beats[-1].start else 0

    def hook_at_time(self, t: float) -> tuple[float, str]:
        return self._hook(self.beat_at(t), t)

    def cliff_at_time(self, t: float) -> tuple[float, str]:
        i = self.beat_at(max(0.0, t - 1e-3))
        return self._cliff(i, t)

    def _hook(self, i: int, t: float | None = None) -> tuple[float, str]:
        b = self.beats[i]
        t0 = b.start if t is None else t
        win = self.window_events(t0, t0 + self.hook_window)
        if not win:
            return 0.0, "no source event in first 3s"
        punches = sorted((self.signals[e.event_id].punch for e in win), reverse=True)
        event_level = punches[0] + 0.3 * (punches[1] if len(punches) > 1 else 0.0)
        beat_level = self.ann[b.beat_id].hook_signal
        raw = 0.6 * min(5.0, event_level) + 0.4 * beat_level
        substantive = [e for e in win if e.event_type in ("dialogue", "action", "sound")]
        note_parts = []
        if not substantive:
            raw *= 0.6
            note_parts.append("camera-only opening")
        first_line = next((e for e in win if e.event_type == "dialogue"), None)
        if first_line and _CONJ.match(first_line.text):
            raw *= 0.7
            note_parts.append("opens mid-conversation")
        # Added after blind review round 1: a bare reaction question ("What?", "What is it?") only
        # works if you saw what it reacts to. Penalise unless something physical/loud happens too.
        if (first_line and first_line.text.strip().endswith("?") and len(first_line.text.split()) <= 3
                and not any(e.event_type in ("action", "sound") and self.signals[e.event_id].punch >= 2 for e in win)
                and not ({"threat", "confrontation", "revelation", "betrayal"} & self.signals[first_line.event_id].categories)):
            raw *= 0.7
            note_parts.append("opens on a bare reaction line")
        if any(self.signals[e.event_id].establishing for e in win) and not substantive:
            raw *= 0.7
            note_parts.append("establishing shot")
        best = max(win, key=lambda e: self.signals[e.event_id].punch)
        cue = f" ({', '.join(best.delivery)})" if best.delivery else ""
        lead = f"{best.speaker}{cue}: \"{best.text}\"" if best.event_type == "dialogue" else f"[{best.event_type}] {best.text}"
        note = lead[:220] + (f" [{'; '.join(note_parts)}]" if note_parts else "")
        return round(2.0 * min(5.0, raw), 2), note

    # ----------------------------------------------------------- cliffhangers
    def _cliff(self, i: int, t: float | None = None) -> tuple[float, str]:
        b = self.beats[i]
        e_end = b.end if t is None else t
        tail = [e for e in self.ep_events if e_end - self.cliff_window - 1e-6 <= e.end <= e_end + 1e-6
                or (e.start < e_end <= e.end + 1e-6)]
        tail = [e for e in tail if e.start < e_end]
        dlg = [e for e in tail if e.event_type == "dialogue"]
        last_line = max(dlg, key=lambda e: e.end) if dlg else None
        a = self.ann[b.beat_id]
        raw = 0.45 * a.cliff_signal
        notes = []
        if last_line:
            s = self.signals[last_line.event_id]
            if s.question:
                raw += 1.0
                notes.append("ends on an unanswered question")
            if s.trailing:
                raw += 0.7
                notes.append("line is cut off")
            if "threat" in s.categories:
                raw += 0.8
                notes.append("ends on a threat")
            if "revelation" in s.categories:
                raw += 0.6
                notes.append("reveal lands at the cut")
        if any(self.signals[e.event_id].surprise >= 2.5 for e in tail):
            raw += 0.5
            notes.append("interruption/shock in final seconds")
        # Unresolved: does the story stay hot right after the cut?
        nxt = self.T[i + 1:i + 3]
        if nxt and max(nxt) >= 0.8 * self.T[i] and self.T[i] >= 1.2:
            raw += 0.6
            notes.append("tension continues after the cut")
        raw += 0.35 * self.T[i]
        raw -= 0.5 * a.resolution_signal
        if a.resolution_signal >= 2.5:
            notes.append("moment resolves (weak cut)")
        at_episode_end = abs(e_end - self.episode.duration) <= 0.6 or i == len(self.beats) - 1
        if at_episode_end:
            raw += 0.8
            notes.append("episode-ending cliffhanger chosen by the writers")
        lead = ""
        if last_line:
            cue = f" ({', '.join(last_line.delivery)})" if last_line.delivery else ""
            lead = f"{last_line.speaker}{cue}: \"{last_line.text}\""
        else:
            last = max(tail, key=lambda e: e.end) if tail else None
            lead = f"[{last.event_type}] {last.text}" if last else ""
        note = lead[:220] + (f" [{'; '.join(notes)}]" if notes else "")
        return round(2.0 * min(5.0, max(0.0, raw)), 2), note

    # ---------------------------------------------------------------- helpers
    def index_of_start(self, t: float) -> int:
        for i, b in enumerate(self.beats):
            if abs(b.start - t) < 1e-6:
                return i
        raise KeyError(t)

    def index_of_end(self, t: float) -> int:
        for i, b in enumerate(self.beats):
            if abs(b.end - t) < 1e-6:
                return i
        raise KeyError(t)

    def peaks(self) -> list[int]:
        if not self.T_smooth:
            return []
        srt = sorted(self.T_smooth)
        thr = srt[int(0.7 * (len(srt) - 1))]
        n = len(self.T_smooth)
        return [i for i in range(n) if self.T_smooth[i] >= thr
                and self.T_smooth[i] >= (self.T_smooth[i - 1] if i else -1)
                and self.T_smooth[i] >= (self.T_smooth[i + 1] if i + 1 < n else -1)]

    def profile(self) -> dict:
        return {
            "episode": self.episode.episode,
            "duration": self.episode.duration,
            "points": [
                {"beat_id": b.beat_id, "start": b.start, "end": b.end, "T": round(self.T[i], 3),
                 "T_smooth": round(self.T_smooth[i], 3), "hook": self.hook_at[i], "cliff": self.cliff_at[i]}
                for i, b in enumerate(self.beats)
            ],
            "peaks": [self.beats[i].beat_id for i in self.peaks()],
        }
