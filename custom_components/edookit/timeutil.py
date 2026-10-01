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


def attends(item: dict[str, Any]) -> bool:
    """True for a lesson that takes place or a timed event (trip) - the child is at school."""
    if item.get("kind", "lesson") == "lesson":
        return not item.get("cancelled")
    return bool(item.get("start")) and not item.get("all_day")


def school_day_items(lessons: list[dict[str, Any]], day: date) -> list[dict[str, Any]]:
    """Lessons plus timed events (trip 8:00-13:30) that keep the child at school."""
    return lessons_on(lessons, day) + [
        ev for ev in events_on(lessons, day) if not ev.get("all_day") and ev.get("start")
    ]


def school_day_bounds(lessons: list[dict[str, Any]], day: date) -> tuple[datetime | None, datetime | None]:
    """First start and last end of the school day (None when there is no school)."""
    items = school_day_items(lessons, day)
    starts = [s for ls in items if (s := lesson_start(ls))]
    ends = [e for ls in items if (e := lesson_end(ls))]
    return (min(starts) if starts else None, max(ends) if ends else None)


def next_school_day(lessons: list[dict[str, Any]], after: date) -> date | None:
    """First date after ``after`` that has a (not cancelled) lesson."""
    days = sorted(
        {
            ls["date"]
            for ls in lessons
            if ls["date"] > after.isoformat()
            and not ls.get("cancelled")
            and (ls.get("kind", "lesson") == "lesson" or (ls.get("start") and not ls.get("all_day")))
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
    "teacher_short",
    "topic",
    "exam",
    "status",
    "kind",
    "changed",
    "cancelled",
)


def timetable_exams(lessons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Written tests marked in the timetable ("Pís. - …" badges)."""
    return [
        {
            "title": ls["exam"],
            "subject": ls.get("subject"),
            "date": f"{ls['date']}T{ls['start']}:00" if ls.get("start") else ls["date"],
            "end": f"{ls['date']}T{ls['end']}:00" if ls.get("end") else None,
            "period": ls.get("period"),
            "description": None,
            "url": None,
        }
        for ls in lessons
        if ls.get("exam")
    ]


def compact(lesson: dict[str, Any]) -> dict[str, Any]:
    """Short lesson representation for attributes."""
    return {k: lesson.get(k) for k in COMPACT_KEYS if lesson.get(k) not in (None, "", False)}


def current_or_next(lessons: list[dict[str, Any]], now: datetime) -> tuple[dict[str, Any] | None, str | None]:
    """The lesson in progress, else the next one (today or a later school day).

    Returns the lesson and a state: ``lesson`` (in progress), ``break``
    (between lessons today), ``before_school`` (later today, nothing yet),
    ``next_day`` (a later day) or None.
    """
    today = now.date()
    upcoming = []
    for item in lessons:
        if item["date"] < today.isoformat():
            continue
        if not attends(item):
            continue
        start, end = lesson_start(item), lesson_end(item)
        if start is None or end is None or end <= now:
            continue
        if start <= now:
            return item, "lesson"
        upcoming.append((start, item))
    if not upcoming:
        return None, None
    start, item = min(upcoming, key=lambda pair: pair[0])
    if start.date() != today:
        return item, "next_day"
    first_today, _ = school_day_bounds(lessons, today)
    return item, "break" if first_today and first_today < now else "before_school"
