"""Calendars for Edookit: timetable and school agenda (events, exams, homework)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import ChildRuntime, EdookitConfigEntry
from .entity import EdookitEntity
from .timeutil import lesson_end, lesson_start


async def async_setup_entry(
    hass: HomeAssistant, entry: EdookitConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up calendars."""
    async_add_entities(
        entity
        for child in entry.runtime_data.children
        for entity in (TimetableCalendar(entry, child), AgendaCalendar(entry, child))
    )


def _in_range(event: CalendarEvent, start: datetime, end: datetime) -> bool:
    ev_start, ev_end = event.start_datetime_local, event.end_datetime_local
    return ev_start < end and ev_end > start


class _BaseCalendar(EdookitEntity, CalendarEntity):
    def _events(self) -> list[CalendarEvent]:
        raise NotImplementedError

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        upcoming = [ev for ev in self._events() if ev.end_datetime_local > now]
        return min(upcoming, key=lambda ev: ev.start_datetime_local) if upcoming else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [ev for ev in self._events() if _in_range(ev, start_date, end_date)]


class TimetableCalendar(_BaseCalendar):
    """Every lesson as a calendar event."""

    _attr_icon = "mdi:timetable"

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, child.timetable, "timetable_calendar")

    def _events(self) -> list[CalendarEvent]:
        events = []
        for lesson in (self.coordinator.data or {}).get("lessons", []):
            if lesson.get("kind") == "event" and lesson.get("all_day"):
                day = date.fromisoformat(lesson["date"])
                events.append(
                    CalendarEvent(
                        start=day,
                        end=day + timedelta(days=1),
                        summary=lesson["subject"],
                        uid=f"{lesson['date']}-event-{lesson['subject']}",
                    )
                )
                continue
            start, end = lesson_start(lesson), lesson_end(lesson)
            if start is None or end is None:
                continue
            summary = lesson["subject"]
            if lesson.get("cancelled"):
                summary = f"❌ {summary} (zrušeno)"
            elif lesson.get("changed"):
                summary = f"⚠️ {summary} (změna)"
            details = [
                f"{lesson['period']}. hodina" if lesson.get("period") else None,
                f"Vyučující: {lesson['teacher']}" if lesson.get("teacher") else None,
                f"Skupina: {lesson['group']}" if lesson.get("group") else None,
                f"Téma: {lesson['topic']}" if lesson.get("topic") else None,
                lesson.get("note"),
            ]
            events.append(
                CalendarEvent(
                    start=start,
                    end=end,
                    summary=summary,
                    location=lesson.get("room"),
                    description="\n".join(d for d in details if d) or None,
                    uid=f"{lesson['date']}-{lesson.get('start')}-{lesson['subject']}",
                )
            )
        return events


def _parse_any(value: str | None) -> datetime | date | None:
    if not value:
        return None
    if len(value) == 10:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    # Midnight timestamps in the portal mean "date without time".
    if parsed.hour == 0 and parsed.minute == 0:
        return parsed.date()
    return parsed


def _event(summary: str, start: datetime | date, end: datetime | date | None, **kw: Any) -> CalendarEvent:
    if isinstance(start, datetime):
        if not isinstance(end, datetime) or end <= start:
            end = start + timedelta(hours=1)
    else:
        last = end if isinstance(end, date) and not isinstance(end, datetime) and end >= start else start
        end = last + timedelta(days=1)
    return CalendarEvent(start=start, end=end, summary=summary, **kw)


class AgendaCalendar(_BaseCalendar):
    """School events, written tests and homework deadlines."""

    _attr_icon = "mdi:calendar-school"

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, child.data, "agenda_calendar")

    def _events(self) -> list[CalendarEvent]:
        data = self.coordinator.data or {}
        tz = dt_util.get_default_time_zone()
        events: list[CalendarEvent] = []
        for ev in data.get("events", []):
            start = _parse_any(ev.get("start"))
            if start is None:
                continue
            end = _parse_any(ev.get("end"))
            if isinstance(start, date) and ev.get("start_time"):
                hour, minute = map(int, ev["start_time"].split(":"))
                start = datetime(start.year, start.month, start.day, hour, minute, tzinfo=tz)
                if ev.get("end_time"):
                    hour, minute = map(int, ev["end_time"].split(":"))
                    end = start.replace(hour=hour, minute=minute)
            events.append(_event(ev["title"], start, end, description=ev.get("description") or None))
        for exam in data.get("exams", []):
            start = _parse_any(exam.get("date"))
            if start is not None:
                title = f"📝 {exam['title']}" + (f" ({exam['subject']})" if exam.get("subject") else "")
                events.append(_event(title, start, None, description=exam.get("description") or None))
        for hw in data.get("assignments", []):
            start = _parse_any(hw.get("due"))
            if start is not None:
                title = f"📚 {hw['title']}" + (f" ({hw['subject']})" if hw.get("subject") else "")
                events.append(_event(title, start, None, description=hw.get("description") or None))
        for ev in data.get("public_events", []):
            start = _parse_any(ev.get("start"))
            if start is None:
                continue
            end = _parse_any(ev.get("end"))
            if not isinstance(start, datetime) and isinstance(end, date) and not isinstance(end, datetime):
                end -= timedelta(days=1)  # iCal all-day end dates are exclusive
            events.append(
                _event(
                    ev["title"],
                    start,
                    end,
                    description=ev.get("description") or None,
                    location=ev.get("location"),
                )
            )
        return events
