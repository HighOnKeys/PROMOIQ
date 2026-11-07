"""Turn overlapping events into cuttable beats + a parser validation report.

Camera, action and dialogue blocks overlap in time; they are layers of the same
footage, not separate clips. We therefore cut only at camera-shot starts (or scene
starts), and drop any cut point that would slice through a line of dialogue or a
sound cue. Every remaining cut point is an actual source timestamp.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from src.parser.screenplay import canonical_name
from src.schemas import Beat, Episode, Event

TOL = 0.05
GENERIC = {
    "THE", "MAN", "WOMAN", "NOBLEMAN", "NOBLEWOMAN", "THUG", "GUARD", "MAID", "PRIEST",
    "MATRIARCH", "STRANGER", "OFFICIAL", "SENTRY", "CONSTABLE", "JUDGE", "INFORMANT",
    "VILLAGER", "TOWNSMAN", "SCOUT", "FIGHTER", "DRIVER", "BUTLER", "HOUSEKEEPER",
    "MOTHER", "FATHER", "LORD", "LADY", "SOUND", "VOICE", "OFF-SCREEN", "ELDER", "OLD",
    "HIGH", "ROYAL", "CROWN", "POLICE", "LOYALIST", "PEASANT", "ARISTOCRATIC", "BLOND",
    "MYSTERIOUS", "BEARDED", "SCARRED", "TALL", "BALD", "THIRD", "CART", "DOWAGER",
}


def named_characters(events: list[Event]) -> dict[str, str]:
    """Canonical speakers that are proper names -> display name used for text mentions.
    'JULIANA' -> 'Juliana'. Descriptive labels ('LADY IN GOLD GOWN') are excluded from
    mention matching because a viewer sees a face, not a label."""
    names: dict[str, str] = {}
    for ev in events:
        if ev.event_type != "dialogue" or not ev.speaker:
            continue
        toks = ev.speaker.replace("'S", "").split()
        if not toks or toks[0] in GENERIC or any(t in GENERIC for t in toks) or len(toks) > 2:
            continue
        if any(not re.match(r"^[A-Z][A-Z\-']+$", t) for t in toks):
            continue
        display = " ".join(t.capitalize() for t in toks)
        names[ev.speaker] = display
    return names


def canonicalise(events: list[Event], alias_map: dict[str, str] | None = None) -> None:
    """In-place: fold honorific aliases, apply an optional (LLM/editor supplied) alias map,
    and attach mentioned character names to every event."""
    known = {e.speaker for e in events if e.speaker}
    alias_map = alias_map or {}
    for ev in events:
        if ev.speaker:
            sp = canonical_name(ev.speaker, known)
            ev.speaker = alias_map.get(sp, sp)
    names = named_characters(events)
    patterns = {cid: re.compile(rf"\b{re.escape(disp)}\b") for cid, disp in names.items()}
    for ev in events:
        chars = set()
        if ev.speaker and ev.event_type == "dialogue":
            chars.add(ev.speaker)
        for cid, pat in patterns.items():
            if pat.search(ev.text):
                chars.add(alias_map.get(cid, cid))
        ev.characters = sorted(chars)


def _compact(ev: Event) -> str:
    if ev.event_type == "dialogue":
        cue = f" ({', '.join(ev.delivery)})" if ev.delivery else ""
        mod = f" [{' '.join(ev.modifiers)}]" if ev.modifiers else ""
        return f'{ev.speaker}{mod}{cue}: "{ev.text}"'
    tag = {"camera": "CAM", "action": "ACT", "sound": "SFX", "scene_transition": "TRANS"}[ev.event_type]
    return f"[{tag}] {ev.text}"


def build_beats(episode: Episode, events: list[Event]) -> list[Beat]:
    evs = sorted(events, key=lambda e: (e.start, e.end))
    if not evs:
        return []
    end_time = max(episode.duration, max(e.end for e in evs))
    raw_of: dict[float, str] = {}
    for e in evs:
        raw_of.setdefault(e.start, e.start_raw)
        raw_of.setdefault(e.end, e.end_raw)
    for sc in episode.scenes:
        if sc.start is not None:
            raw_of.setdefault(sc.start, sc.start_raw)
        if sc.end is not None:
            raw_of.setdefault(sc.end, sc.end_raw)

    cams = [e for e in evs if e.event_type == "camera"]
    bounds = {min(e.start for e in evs), end_time}
    bounds.update(c.start for c in cams)
    bounds.update(sc.start for sc in episode.scenes if sc.start is not None)
    # a gap after a camera shot ends is also a natural cut point
    for c in cams:
        if not any(o.start - TOL <= c.end < o.end - TOL for o in cams if o is not c):
            bounds.add(c.end)
    blockers = [e for e in evs if e.event_type in ("dialogue", "sound")]
    legal = sorted(
        b for b in bounds
        if not any(e.start < b - TOL and e.end > b + TOL for e in blockers)
    )
    beats: list[Beat] = []
    for k in range(len(legal) - 1):
        a, b = legal[k], legal[k + 1]
        if b - a < TOL:
            continue
        members = [e for e in evs if a - TOL <= e.start < b - TOL]
        if not members:
            continue
        scene_no = Counter(e.scene for e in members).most_common(1)[0][0]
        chars = sorted({c for e in members for c in e.characters})
        speakers = sorted({e.speaker for e in members if e.event_type == "dialogue" and e.speaker})
        beats.append(Beat(
            beat_id=f"B{episode.episode:03d}-{len(beats):03d}",
            episode=episode.episode,
            scene=scene_no,
            start=a,
            end=b,
            start_raw=raw_of.get(a) or "",
            end_raw=raw_of.get(b) or "",
            event_ids=[e.event_id for e in members],
            characters=chars,
            speakers=speakers,
            text=" | ".join(_compact(e) for e in members),
        ))
    return beats


def validation_report(episodes: list[Episode], events: list[Event], beats: list[Beat], issues: list[dict]) -> dict:
    by_ep: dict[int, list[Event]] = defaultdict(list)
    for e in events:
        by_ep[e.episode].append(e)
    beats_by_ep: dict[int, list[Beat]] = defaultdict(list)
    for b in beats:
        beats_by_ep[b.episode].append(b)
    per_episode = []
    totals = Counter()
    for ep in episodes:
        evs = by_ep[ep.episode]
        types = Counter(e.event_type for e in evs)
        totals.update(types)
        problems = []
        scenes = sorted(ep.scenes, key=lambda s: s.scene)
        for s1, s2 in zip(scenes, scenes[1:]):
            if s1.end is not None and s2.start is not None and abs(s1.end - s2.start) > 0.5:
                problems.append(f"scene {s1.scene} ends {s1.end_raw} but scene {s2.scene} starts {s2.start_raw}")
        scene_rng = {s.scene: (s.start, s.end) for s in scenes}
        outside = 0
        for e in evs:
            a, b = scene_rng.get(e.scene, (None, None))
            if a is not None and b is not None and (e.start < a - 0.5 or e.end > b + 0.5):
                outside += 1
        cams = sorted((e for e in evs if e.event_type == "camera"), key=lambda e: e.start)
        cam_overlaps = sum(1 for c1, c2 in zip(cams, cams[1:]) if c2.start < c1.end - TOL)
        covered = sum(c.duration for c in cams)
        dlg = sorted((e for e in evs if e.event_type == "dialogue"), key=lambda e: e.start)
        dlg_overlaps = sum(1 for d1, d2 in zip(dlg, dlg[1:]) if d2.start < d1.end - TOL)
        zero = sum(1 for e in evs if e.duration <= 0)
        inferred = sum(1 for e in evs if e.timing_status != "exact")
        if outside:
            problems.append(f"{outside} events fall outside their scene's time range")
        per_episode.append({
            "episode": ep.episode,
            "file": ep.file,
            "scenes": len(ep.scenes),
            "duration_s": ep.duration,
            "events": len(evs),
            "by_type": dict(types),
            "beats": len(beats_by_ep[ep.episode]),
            "camera_coverage_ratio": round(min(1.0, covered / ep.duration), 3) if ep.duration else 0,
            "overlapping_camera_shots": cam_overlaps,
            "overlapping_dialogue_lines": dlg_overlaps,
            "zero_length_events": zero,
            "inferred_timestamps": inferred,
            "problems": problems,
        })
    return {
        "episodes_parsed": len(episodes),
        "events_total": len(events),
        "events_by_type": dict(totals),
        "beats_total": len(beats),
        "total_runtime_s": round(sum(e.duration for e in episodes), 2),
        "parse_issues": issues,
        "episodes_with_problems": [p["episode"] for p in per_episode if p["problems"]],
        "per_episode": per_episode,
    }
