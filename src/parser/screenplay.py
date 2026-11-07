"""Robust parser for the Kuku screenplay text format.

Format (observed across all 50 files, but parsed defensively):

    EPISODE NUMBER: Episode 1
    EPISODE LOGLINE: ...
    CONTINUITY NOTES: ...
    --- SCRIPT START ---
    SCENE 01
    Scene heading: INT. CATHEDRAL – DAY
    Scene start: 00:00.00
    Scene end: 02:18.00
    Actions and dialogue:
    **CAMERA:** | **ACTION:** | **SOUND FX:** | **SPEAKER (O.S.) (Cue, Cue):**
    Start: (00:00.00) | End: (00:04.80)
    content line(s)
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from src.parser.timestamps import TimestampError, parse_range, parse_timestamp
from src.schemas import Episode, Event, Scene, SourceRef

log = logging.getLogger(__name__)

_HEADER = re.compile(r"^\s*(EPISODE NUMBER|EPISODE LOGLINE|CONTINUITY NOTES)\s*:\s*(.*)$", re.I)
_SCENE = re.compile(r"^\s*SCENE\s+(\S+)\s*$", re.I)
_SCENE_FIELD = re.compile(r"^\s*Scene\s+(heading|start|end)\s*:\s*(.*)$", re.I)
_BLOCK = re.compile(r"^\s*\*\*\s*(?P<head>.+?)\s*:?\s*\*\*\s*:?\s*$")
_PAREN = re.compile(r"\(([^)]*)\)")
_EP_FROM_FILE = re.compile(r"episode[-_ ]?(\d+)", re.I)
_MODIFIERS = {"O.S.", "V.O.", "O.C.", "OFF-SCREEN", "CONT'D", "CONTINUING", "OS", "VO"}
_HONORIFICS = ("LADY ", "LORD ", "SIR ", "MISS ", "MRS. ", "MR. ", "DAME ")


def _speaker_parts(head: str) -> tuple[str, list[str], list[str]]:
    """'THE BEARDED STRANGER (O.S.) (Deep, Resonant)' -> name, modifiers, delivery cues."""
    name = _PAREN.sub("", head).strip().rstrip(":").strip()
    modifiers: list[str] = []
    delivery: list[str] = []
    for group in _PAREN.findall(head):
        parts = [p.strip() for p in group.split(",") if p.strip()]
        for p in parts:
            if p.upper() in _MODIFIERS:
                modifiers.append(p.upper())
            else:
                delivery.append(p)
    return re.sub(r"\s+", " ", name.upper()), modifiers, delivery


def _classify(head_name: str) -> str:
    h = head_name.upper()
    if h == "CAMERA":
        return "camera"
    if h == "ACTION":
        return "action"
    if h in ("SOUND FX", "SFX", "SOUND", "MUSIC"):
        return "sound"
    if h in ("TRANSITION", "CUT TO", "FADE OUT", "FADE IN"):
        return "scene_transition"
    return "dialogue"


def episode_number_from_name(path: Path, fallback: int) -> int:
    m = _EP_FROM_FILE.search(path.name)
    return int(m.group(1)) if m else fallback


def parse_episode(path: Path, fallback_number: int = 0) -> tuple[Episode, list[Event], list[dict]]:
    """Parse one screenplay file. Returns (episode, events, issues)."""
    issues: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
        issues.append({"file": path.name, "line": 0, "issue": "non-UTF-8 bytes replaced"})
    lines = text.splitlines()
    ep_no = episode_number_from_name(path, fallback_number)
    episode = Episode(episode=ep_no, label=f"Episode {ep_no}", file=path.name)
    events: list[Event] = []
    scene: Scene | None = None
    scene_counter = 0
    i = 0
    in_script = False
    last_end = 0.0

    def finish_scene():
        if scene is not None:
            episode.scenes.append(scene)

    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        m = _HEADER.match(s)
        if m and not in_script:
            key = m.group(1).upper()
            if key == "EPISODE NUMBER":
                episode.label = m.group(2).strip() or episode.label
            elif key == "EPISODE LOGLINE":
                episode.logline = m.group(2).strip()
            else:
                episode.continuity = m.group(2).strip()
            i += 1
            continue
        if s.startswith("--- SCRIPT START"):
            in_script = True
            i += 1
            continue
        m = _SCENE.match(s)
        if m:
            in_script = True
            finish_scene()
            scene_counter += 1
            try:
                sc_no = int(re.sub(r"\D", "", m.group(1)) or scene_counter)
            except ValueError:
                sc_no = scene_counter
            scene = Scene(episode=ep_no, scene=sc_no, heading="", start=None, end=None)
            i += 1
            continue
        m = _SCENE_FIELD.match(s)
        if m and scene is not None:
            field_name, value = m.group(1).lower(), m.group(2).strip()
            if field_name == "heading":
                scene.heading = value
            else:
                try:
                    t = parse_timestamp(value)
                    if field_name == "start":
                        scene.start, scene.start_raw = t, value
                    else:
                        scene.end, scene.end_raw = t, value
                except TimestampError as exc:
                    issues.append({"file": path.name, "line": i + 1, "issue": f"bad scene {field_name}: {exc}"})
            i += 1
            continue
        m = _BLOCK.match(s)
        if m and in_script:
            head = m.group("head").strip()
            name, modifiers, delivery = _speaker_parts(head)
            etype = _classify(name)
            block_line = i + 1
            i += 1
            # timestamp line (allow blank lines before it)
            while i < len(lines) and not lines[i].strip():
                i += 1
            start_raw = end_raw = ""
            timing = "missing"
            if i < len(lines):
                rng = parse_range(lines[i])
                if rng:
                    start_raw, end_raw = rng
                    i += 1
            content: list[str] = []
            while i < len(lines):
                nxt = lines[i].strip()
                if not nxt and content:
                    break
                if _BLOCK.match(nxt) or _SCENE.match(nxt) or nxt == "---":
                    break
                if nxt:
                    content.append(nxt)
                i += 1
            body = " ".join(content).strip()
            if etype == "dialogue":
                body = body.strip().strip('"').strip("“”").strip()
            ev_issues: list[str] = []
            start = end = None
            if start_raw:
                try:
                    start, end = parse_timestamp(start_raw), parse_timestamp(end_raw)
                    timing = "exact"
                except TimestampError as exc:
                    ev_issues.append(f"malformed timestamp: {exc}")
            if start is None:
                # Never invent precision: inherit the previous end and mark as inferred.
                start = end = last_end
                timing = "inferred"
                ev_issues.append("missing/invalid timestamp; position inferred, needs human check")
                start_raw = end_raw = start_raw or ""
            if end < start:
                ev_issues.append(f"end {end} before start {start}; swapped")
                start, end = end, start
            if scene is None:
                scene_counter += 1
                scene = Scene(episode=ep_no, scene=scene_counter, heading="(no scene header)", start=None, end=None)
                ev_issues.append("event outside any SCENE block")
            ev = Event(
                event_id=f"E{ep_no:03d}-S{scene.scene:02d}-{len(events):03d}",
                episode=ep_no,
                scene=scene.scene,
                event_type=etype,  # type: ignore[arg-type]
                speaker_raw=head if etype == "dialogue" else None,
                speaker=name if etype == "dialogue" else None,
                modifiers=modifiers,
                delivery=delivery if etype == "dialogue" else [],
                start=start,
                end=end,
                start_raw=start_raw,
                end_raw=end_raw,
                text=body,
                source=SourceRef(file=path.name, line=block_line),
                timing_status=timing,  # type: ignore[arg-type]
                confidence=1.0 if timing == "exact" else 0.3,
                issues=ev_issues,
            )
            for msg in ev_issues:
                issues.append({"file": path.name, "line": block_line, "issue": msg})
            if not body:
                issues.append({"file": path.name, "line": block_line, "issue": "empty block content"})
            events.append(ev)
            scene.event_ids.append(ev.event_id)
            last_end = max(last_end, end)
            continue
        i += 1
    finish_scene()
    ends = [sc.end for sc in episode.scenes if sc.end is not None] + [e.end for e in events]
    episode.duration = round(max(ends), 3) if ends else 0.0
    if not episode.logline:
        issues.append({"file": path.name, "line": 0, "issue": "missing EPISODE LOGLINE"})
    if not events:
        issues.append({"file": path.name, "line": 0, "issue": "no events parsed"})
    return episode, events, issues


def find_episode_files(input_dir: Path) -> list[Path]:
    files = sorted(p for p in Path(input_dir).rglob("*.txt") if not p.name.startswith("."))
    return sorted(files, key=lambda p: (episode_number_from_name(p, 10**6), p.name))


def canonical_name(name: str, known: set[str]) -> str:
    """Deterministic alias folding: strip an honorific only if the bare name also speaks.
    'LADY JULIANA' -> 'JULIANA' (because JULIANA exists); 'LORD ASHCROFT' stays."""
    for h in _HONORIFICS:
        if name.startswith(h) and name[len(h):] in known:
            return name[len(h):]
    return name
