"""Hard-constraint validator (pure Python). A failure here can never be outweighed by a
high subjective score."""
from __future__ import annotations

from src.construction.show import Show
from src.schemas import PromoCandidate

EPS = 1e-6


def source_times(show: Show) -> dict[int, set]:
    out: dict[int, set] = {}
    for ep, e in show.episodes.items():
        s = set()
        for sc in e.scenes:
            if sc.start is not None:
                s.add(round(sc.start, 3))
            if sc.end is not None:
                s.add(round(sc.end, 3))
        out[ep] = s
    for ev in show.events.values():
        out.setdefault(ev.episode, set()).update({round(ev.start, 3), round(ev.end, 3)})
    return out


class HardValidator:
    def __init__(self, show: Show, constraints):
        self.show = show
        self.c = constraints
        self.times = source_times(show)
        self.blockers: dict[int, list] = {}
        for ev in show.events.values():
            if ev.event_type in ("dialogue", "sound"):
                self.blockers.setdefault(ev.episode, []).append(ev)

    def validate(self, p: PromoCandidate) -> dict:
        checks = []

        def check(name, ok, detail=""):
            checks.append({"check": name, "pass": bool(ok), "detail": detail})

        segs = p.segments
        check("has_segments", len(segs) >= 1)
        total = round(sum(s.end - s.start for s in segs), 3)
        check("duration_20_60", self.c.min_duration - EPS <= total <= self.c.max_duration + EPS, f"{total:.2f}s")
        check("duration_field_matches", abs(total - p.duration) < 0.01, f"field {p.duration} vs computed {total}")
        for s in segs:
            tag = f"seg{s.order}"
            ep = self.show.episodes.get(s.episode)
            check(f"{tag}_episode_exists", ep is not None, f"episode {s.episode}")
            if ep is None:
                continue
            check(f"{tag}_positive_length", s.end - s.start >= self.c.min_segment - EPS, f"{s.end - s.start:.2f}s")
            check(f"{tag}_within_episode", -EPS <= s.start and s.end <= ep.duration + EPS,
                  f"{s.start_raw}-{s.end_raw} in 0-{ep.duration}")
            times = self.times.get(s.episode, set())
            check(f"{tag}_start_is_source_timestamp", round(s.start, 3) in times, s.start_raw)
            check(f"{tag}_end_is_source_timestamp", round(s.end, 3) in times, s.end_raw)
            cut = [ev.event_id for ev in self.blockers.get(s.episode, [])
                   for t in (s.start, s.end) if ev.start < t - 0.05 and ev.end > t + 0.05]
            check(f"{tag}_no_line_cut_midway", not cut, ", ".join(cut[:3]))
        order_ok = all((a.episode, a.end) <= (b.episode, b.start) for a, b in zip(segs, segs[1:]))
        check("segments_ordered_no_overlap", order_ok)
        check("segment_order_field", [s.order for s in segs] == list(range(1, len(segs) + 1)))
        # hook evidence: something substantive happens in the first 3 seconds
        f = segs[0] if segs else None
        hook_ok = False
        if f is not None and f.episode in self.show.timelines:
            tl = self.show.timelines[f.episode]
            win = tl.window_events(f.start, f.start + self.c.hook_window)
            hook_ok = any(tl.signals[e.event_id].punch >= 1.0 for e in win)
        check("hook_evidence_first_3s", hook_ok)
        ids = {x["event_id"] for x in p.evidence}
        check("evidence_traceable", bool(ids) and all(i in self.show.events for i in ids), f"{len(ids)} events")
        for fld in ("promo_id", "hook", "cliffhanger", "why"):
            check(f"field_{fld}", bool(getattr(p, fld)))
        failed = [c for c in checks if not c["pass"]]
        return {"promo_id": p.promo_id, "valid": not failed, "failed": [c["check"] for c in failed], "checks": checks}
