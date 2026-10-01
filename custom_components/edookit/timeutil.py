"""Helpers turning timetable lesson dicts into datetimes."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

DEFAULT_LESSON = timedelta(minutes=45)


def lesson_start(lesson: dict[str, Any]) -> datetime | None:
    """Aware start datetime of a lesson (None when the time is unknown)."""
    if not lesson.get("start"):
        return None
    hour, minute = (int(x) for x in lesson["start"].split(":")[:2])
    return datetime.combine(
        date.fromisoformat(lesson["date"]), time(hour, minute), tzinfo=dt_util.get_default_time_zone()
    )


def lesson_end(lesson: dict[str, Any]) -> datetime | None:
    """Aware end datetime (start + 45 min when the end is unknown)."""
    start = lesson_start(lesson)
    if start is None:
        return None
    if lesson.get("end"):
        hour, minute = (int(x) for x in lesson["end"].split(":")[:2])
        end = start.replace(hour=hour, minute=minute)
        if end > start:
            return end
    return start + DEFAULT_LESSON


def lessons_on(lessons: list[dict[str, Any]], day: date, include_cancelled: bool = False) -> list[dict[str, Any]]:
    """Lessons (not school events such as holidays or trips) on a given date."""
    key = day.isoformat()
    return [
        ls
        for ls in lessons
        if ls["date"] == key and ls.get("kind", "lesson") == "lesson" and (include_cancelled or not ls.get("cancelled"))
    ]


def events_on(lessons: list[dict[str, Any]], day: date) -> list[dict[str, Any]]:
    """Timetable events (holiday, trip, project day) on a given date."""
    key = day.isoformat()
    return [ls for ls in lessons if ls["date"] == key and ls.get("kind") == "event"]


def next_school_day(lessons: list[dict[str, Any]], after: date) -> date | None:
    """First date after ``after`` that has a (not cancelled) lesson."""
    days = sorted(
        {
            ls["date"]
            for ls in lessons
            if ls["date"] > after.isoformat() and not ls.get("cancelled") and ls.get("kind", "lesson") == "lesson"
        }
    )
    return date.fromisoformat(days[0]) if days else None


COMPACT_KEYS = (
    "period",
    "start",
    "end",
    "subject",
    "subject_short",
    "room",
    "teacher",
    "topic",
    "kind",
    "changed",
    "cancelled",
)


def compact(lesson: dict[str, Any]) -> dict[str, Any]:
    """Short lesson representation for attributes."""
    return {
        k: lesson.get(k)
        for k in (
            "period",
            "start",
            "end",
            "subject",
            "subject_short",
            "room",
            "teacher",
            "topic",
            "kind",
            "changed",
            "cancelled",
        )
        if lesson.get(k) not in (None, "", False)
    }
