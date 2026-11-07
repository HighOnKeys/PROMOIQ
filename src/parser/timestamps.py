"""Deterministic timestamp handling. No LLM ever does timestamp arithmetic."""
from __future__ import annotations

import re

_TS = re.compile(
    r"^\s*(?:(?P<h>\d{1,2}):)?(?P<m>\d{1,3}):(?P<s>\d{1,2})(?:[.,](?P<f>\d{1,3}))?\s*$"
)
_SECONDS_ONLY = re.compile(r"^\s*(?P<s>\d+)(?:[.,](?P<f>\d{1,3}))?\s*s?\s*$", re.I)


class TimestampError(ValueError):
    pass


def parse_timestamp(raw: str) -> float:
    """Convert a screenplay timestamp to seconds.

    '00:13.50' -> 13.5      (the decimal part is fractions of a SECOND, not minutes)
    '01:25.75' -> 85.75
    '1:02:03.5' -> 3723.5
    '13.5'      -> 13.5
    """
    if raw is None:
        raise TimestampError("timestamp is None")
    text = str(raw).strip().strip("()").strip()
    m = _TS.match(text)
    if m:
        h = int(m.group("h") or 0)
        minutes = int(m.group("m"))
        sec = int(m.group("s"))
        if sec >= 60:
            raise TimestampError(f"seconds field >= 60 in {raw!r}")
        frac = m.group("f")
        value = h * 3600 + minutes * 60 + sec + (int(frac) / (10 ** len(frac)) if frac else 0.0)
        return round(value, 3)
    m = _SECONDS_ONLY.match(text)
    if m:
        frac = m.group("f")
        return round(int(m.group("s")) + (int(frac) / (10 ** len(frac)) if frac else 0.0), 3)
    raise TimestampError(f"unrecognised timestamp {raw!r}")


def format_timestamp(seconds: float) -> str:
    """Inverse of parse_timestamp in the screenplay's own MM:SS.ss style."""
    if seconds < 0:
        raise TimestampError("negative time")
    total_cs = int(round(seconds * 100))
    minutes, cs = divmod(total_cs, 6000)
    return f"{minutes:02d}:{cs // 100:02d}.{cs % 100:02d}"


_RANGE = re.compile(
    r"start\s*:?\s*\(?\s*(?P<a>[^)|]*?)\s*\)?\s*\|\s*end\s*:?\s*\(?\s*(?P<b>[^)]*?)\s*\)?\s*$",
    re.I,
)


def parse_range(line: str) -> tuple[str, str] | None:
    """Parse 'Start: (00:01.00) | End: (00:04.80)' (case/paren tolerant)."""
    m = _RANGE.search(line.strip())
    if not m:
        return None
    return m.group("a").strip(), m.group("b").strip()
