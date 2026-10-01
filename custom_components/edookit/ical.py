"""Minimal iCalendar (RFC 5545) reader for timetable / school event feeds.

Only what Edookit-style feeds need: VEVENTs with DTSTART/DTEND (UTC, TZID
or all-day dates), SUMMARY, LOCATION, DESCRIPTION, UID, STATUS. Recurrence
rules are not expanded; timetable feeds list every lesson explicitly.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "Europe/Prague"


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    return value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _parse_dt(value: str, params: dict[str, str], default_tz: str) -> datetime | date | None:
    value = value.strip()
    try:
        if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
            return datetime.strptime(value, "%Y%m%d").date()
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        naive = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    tz_name = params.get("TZID", default_tz).strip('"')
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo(default_tz)
    return naive.replace(tzinfo=tz)


def parse_ical(text: str, default_tz: str = DEFAULT_TZ) -> list[dict[str, Any]]:
    """Return VEVENTs as dicts with ``start``/``end`` (date or aware datetime)."""
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None and current.get("start") is not None:
                if current.get("end") is None:
                    start = current["start"]
                    current["end"] = (
                        start + timedelta(days=1) if not isinstance(start, datetime) else start + timedelta(minutes=45)
                    )
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        name, *raw_params = head.split(";")
        params = {}
        for raw in raw_params:
            if "=" in raw:
                key, val = raw.split("=", 1)
                params[key.upper()] = val
        name = name.upper()
        if name == "DTSTART":
            current["start"] = _parse_dt(value, params, default_tz)
        elif name == "DTEND":
            current["end"] = _parse_dt(value, params, default_tz)
        elif name in ("SUMMARY", "LOCATION", "DESCRIPTION", "UID", "STATUS", "URL", "CATEGORIES"):
            current[name.lower()] = _unescape(value).strip()
        elif name == "ORGANIZER":
            current["organizer"] = params.get("CN", value).strip('"')
    events.sort(key=lambda ev: _sort_key(ev["start"]))
    return events


def _sort_key(value: datetime | date) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC).replace(tzinfo=None)
    return datetime.combine(value, datetime.min.time())


def ical_to_lessons(events: list[dict[str, Any]], local_tz: str = DEFAULT_TZ) -> list[dict[str, Any]]:
    """Convert timed VEVENTs to the integration's lesson dicts."""
    tz = ZoneInfo(local_tz)
    lessons = []
    for ev in events:
        start, end = ev["start"], ev.get("end")
        if not isinstance(start, datetime):
            continue  # all-day items are events, not lessons
        start = start.astimezone(tz)
        end = end.astimezone(tz) if isinstance(end, datetime) else None
        summary = ev.get("summary", "")
        description = ev.get("description", "")
        teacher = None
        match = re.search(r"(?:vyučující|učitel|teacher)\s*:?\s*([^\n,;]+)", description, re.I)
        if match:
            teacher = match.group(1).strip()
        blob = f"{summary} {description} {ev.get('status', '')}"
        cancelled = bool(re.search(r"zrušen|odpadá|cancel", blob, re.I))
        lessons.append(
            {
                "date": start.date().isoformat(),
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M") if end else None,
                "subject": summary,
                "subject_short": None,
                "teacher": teacher,
                "room": ev.get("location") or None,
                "group": None,
                "topic": None,
                "period": None,
                "changed": cancelled or bool(re.search(r"suplov|změn|substitut", blob, re.I)),
                "cancelled": cancelled,
                "note": description or None,
                "kind": "lesson",
                "url": ev.get("url"),
            }
        )
    return lessons
