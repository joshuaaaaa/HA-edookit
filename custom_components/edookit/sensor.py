"""Sensors for Edookit."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import CONF_PUBLIC_API, DEFAULT_PUBLIC_API
from .coordinator import ChildRuntime, EdookitConfigEntry
from .entity import EdookitEntity
from .timeutil import compact, lesson_end, lesson_start, lessons_on, next_school_day

LIST_LIMIT = 20


async def async_setup_entry(
    hass: HomeAssistant, entry: EdookitConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensors."""
    entities: list[SensorEntity] = []
    for child in entry.runtime_data.children:
        entities += [
            TimetableSensor(entry, child),
            LessonsTomorrowSensor(entry, child),
            CurrentLessonSensor(entry, child),
            NextLessonSensor(entry, child),
            SchoolStartSensor(entry, child, "school_start_today", tomorrow=False),
            SchoolEndSensor(entry, child),
            SchoolStartSensor(entry, child, "school_start_next", tomorrow=True),
            TimetableChangesSensor(entry, child),
            UnreadSensor(entry, child),
            MessagesSensor(entry, child),
            LastGradeSensor(entry, child),
            GradeAverageSensor(entry, child),
            HomeworkSensor(entry, child),
            ExamsSensor(entry, child),
            EventsSensor(entry, child),
            ActionRequiredSensor(entry, child),
            AbsencesSensor(entry, child),
            PaymentsSensor(entry, child),
            LastUpdateSensor(entry, child),
        ]
        if entry.options.get(CONF_PUBLIC_API, DEFAULT_PUBLIC_API):
            entities.append(PublicEventsSensor(entry, child))
            entities.append(SubstitutionsSensor(entry, child))
    async_add_entities(entities)


# ----------------------------------------------------------------- timetable


class _TimetableBase(EdookitEntity, SensorEntity):
    """Sensor based on the timetable coordinator."""

    _per_minute = False

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime, key: str) -> None:
        super().__init__(entry, child, child.timetable, key)

    @property
    def lessons(self) -> list[dict[str, Any]]:
        return (self.coordinator.data or {}).get("lessons", [])

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._per_minute:
            self.async_on_remove(async_track_time_change(self.hass, self._tick, second=0))
        else:
            # Day-dependent values must flip at midnight.
            self.async_on_remove(async_track_time_change(self.hass, self._tick, hour=0, minute=0, second=1))

    @callback
    def _tick(self, now: datetime) -> None:
        self.async_write_ha_state()


class TimetableSensor(_TimetableBase):
    """Number of lessons today; the full timetable is in the attributes (used by the card)."""

    _attr_icon = "mdi:timetable"
    _attr_native_unit_of_measurement = "hodin"
    _unrecorded_attributes = frozenset({"days", "bell", "today"})

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "timetable")

    @property
    def native_value(self) -> int:
        return len(lessons_on(self.lessons, dt_util.now().date()))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "student": self.child.name,
            "source": data.get("source"),
            "week_start": data.get("week_start"),
            "last_update": data.get("updated"),
            "today": [compact(ls) for ls in lessons_on(self.lessons, dt_util.now().date(), True)],
            "bell": data.get("bell", []),
            "days": data.get("days", []),
        }


class LessonsTomorrowSensor(_TimetableBase):
    """Lessons on the next school day."""

    _attr_icon = "mdi:calendar-arrow-right"
    _attr_native_unit_of_measurement = "hodin"

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "lessons_tomorrow")

    @property
    def native_value(self) -> int:
        return len(lessons_on(self.lessons, dt_util.now().date() + timedelta(days=1)))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        tomorrow = dt_util.now().date() + timedelta(days=1)
        nxt = next_school_day(self.lessons, dt_util.now().date())
        return {
            "date": tomorrow.isoformat(),
            "lessons": [compact(ls) for ls in lessons_on(self.lessons, tomorrow, True)],
            "next_school_day": nxt.isoformat() if nxt else None,
            "subjects": sorted({ls["subject"] for ls in lessons_on(self.lessons, tomorrow)}),
        }


class CurrentLessonSensor(_TimetableBase):
    """Lesson in progress right now."""

    _attr_icon = "mdi:school"
    _per_minute = True

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "current_lesson")

    def _current(self) -> dict[str, Any] | None:
        now = dt_util.now()
        for lesson in lessons_on(self.lessons, now.date()):
            start, end = lesson_start(lesson), lesson_end(lesson)
            if start and end and start <= now < end:
                return lesson
        return None

    @property
    def native_value(self) -> str | None:
        lesson = self._current()
        return lesson["subject"] if lesson else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        lesson = self._current()
        if not lesson:
            return {}
        end = lesson_end(lesson)
        return {
            **compact(lesson),
            "minutes_left": int((end - dt_util.now()).total_seconds() // 60) if end else None,
        }


class NextLessonSensor(_TimetableBase):
    """Next lesson that has not started yet."""

    _attr_icon = "mdi:school-outline"
    _per_minute = True

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "next_lesson")

    def _next(self) -> dict[str, Any] | None:
        now = dt_util.now()
        upcoming = [
            ls
            for ls in self.lessons
            if not ls.get("cancelled") and (start := lesson_start(ls)) is not None and start > now
        ]
        return min(upcoming, key=lesson_start) if upcoming else None

    @property
    def native_value(self) -> str | None:
        lesson = self._next()
        return lesson["subject"] if lesson else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        lesson = self._next()
        if not lesson:
            return {}
        start = lesson_start(lesson)
        return {
            **compact(lesson),
            "date": lesson["date"],
            "starts_at": start.isoformat() if start else None,
            "minutes_until": int((start - dt_util.now()).total_seconds() // 60) if start else None,
        }


class SchoolStartSensor(_TimetableBase):
    """Start of the first lesson today / on the next school day."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:alarm"

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime, key: str, tomorrow: bool) -> None:
        super().__init__(entry, child, key)
        self._tomorrow = tomorrow

    @property
    def native_value(self) -> datetime | None:
        today = dt_util.now().date()
        day = next_school_day(self.lessons, today) if self._tomorrow else today
        if day is None:
            return None
        starts = [s for ls in lessons_on(self.lessons, day) if (s := lesson_start(ls))]
        return min(starts) if starts else None


class SchoolEndSensor(_TimetableBase):
    """End of the last lesson today."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:home-import-outline"

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "school_end_today")

    @property
    def native_value(self) -> datetime | None:
        ends = [e for ls in lessons_on(self.lessons, dt_util.now().date()) if (e := lesson_end(ls))]
        return max(ends) if ends else None


class TimetableChangesSensor(_TimetableBase):
    """Changed or cancelled lessons in the loaded weeks (from today on)."""

    _attr_icon = "mdi:calendar-alert"

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "timetable_changes")

    def _changes(self) -> list[dict[str, Any]]:
        today = dt_util.now().date().isoformat()
        return [ls for ls in self.lessons if ls["date"] >= today and (ls.get("changed") or ls.get("cancelled"))]

    @property
    def native_value(self) -> int:
        return len(self._changes())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"changes": [{"date": ls["date"], **compact(ls), "note": ls.get("note")} for ls in self._changes()]}


# ---------------------------------------------------------------- portal data


class _DataBase(EdookitEntity, SensorEntity):
    """Sensor based on the data coordinator."""

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime, key: str) -> None:
        super().__init__(entry, child, child.data, key)

    @property
    def data(self) -> dict[str, Any]:
        return self.coordinator.data or {}

    def _url(self, url: str | None) -> str | None:
        if url and url.startswith("/"):
            return f"{self.runtime.client.base_url}{url}"
        return url


class UnreadSensor(_DataBase):
    """Unread notifications in the Edookit inbox."""

    _attr_icon = "mdi:bell-badge-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "unread")

    @property
    def native_value(self) -> int:
        return self.data.get("unread", 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        unread = [i for i in self.data.get("inbox", []) if i["unread"]]
        return {
            "items": [
                {k: i[k] for k in ("type", "title", "creator", "time", "grade") if i.get(k)}
                | {"url": self._url(i["url"])}
                for i in unread[:LIST_LIMIT]
            ]
        }


class MessagesSensor(_DataBase):
    """Latest message (state = subject)."""

    _attr_icon = "mdi:email-outline"

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "last_message")

    @property
    def native_value(self) -> str | None:
        messages = self.data.get("messages", [])
        return messages[0]["title"][:255] if messages else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        messages = self.data.get("messages", [])
        latest = messages[0] if messages else {}
        return {
            "from": latest.get("creator"),
            "time": latest.get("time"),
            "preview": latest.get("description"),
            "unread": latest.get("unread"),
            "url": self._url(latest.get("url")),
            "unread_messages": sum(1 for m in messages if m["unread"]),
            "messages": [
                {"title": m["title"], "from": m["creator"], "time": m["time"], "unread": m["unread"]}
                for m in messages[:LIST_LIMIT]
            ],
        }


class LastGradeSensor(_DataBase):
    """Most recent grade."""

    _attr_icon = "mdi:numeric-1-box-multiple-outline"

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "last_grade")

    @property
    def native_value(self) -> str | None:
        grades = self.data.get("grades", [])
        return grades[0]["grade"][:255] if grades else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        grades = self.data.get("grades", [])
        latest = grades[0] if grades else {}
        return {
            "subject": latest.get("subject"),
            "topic": latest.get("topic"),
            "weight": latest.get("weight"),
            "date": latest.get("date"),
            "grades": [
                {k: g.get(k) for k in ("subject", "topic", "grade", "weight", "date")} for g in grades[: LIST_LIMIT * 2]
            ],
        }


class GradeAverageSensor(_DataBase):
    """Average of the per-subject weighted averages."""

    _attr_icon = "mdi:chart-line"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "grade_average")

    @property
    def native_value(self) -> float | None:
        averages = self.data.get("averages", {})
        return round(sum(averages.values()) / len(averages), 2) if averages else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"subjects": self.data.get("averages", {})}


class _ListSensor(_DataBase):
    """Count of items in a data list, items in attributes."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _list_key = ""
    _fields: tuple[str, ...] = ()

    @property
    def items(self) -> list[dict[str, Any]]:
        return self.data.get(self._list_key, [])

    @property
    def native_value(self) -> int:
        return len(self.items)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        items = [
            {k: i.get(k) for k in self._fields if i.get(k) not in (None, "")} | {"url": self._url(i.get("url"))}
            for i in self.items[:LIST_LIMIT]
        ]
        return {"items": items, "next": items[0] if items else None}


class HomeworkSensor(_ListSensor):
    """Homework not yet due."""

    _attr_icon = "mdi:book-edit-outline"
    _list_key = "assignments"
    _fields = ("title", "subject", "due", "description", "teacher")

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "homework")


class ExamsSensor(_ListSensor):
    """Upcoming written tests."""

    _attr_icon = "mdi:file-document-edit-outline"
    _list_key = "exams"
    _fields = ("title", "subject", "date", "description")

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "exams")


class EventsSensor(_ListSensor):
    """Upcoming school events (trips, holidays, parent meetings)."""

    _attr_icon = "mdi:calendar-star"
    _list_key = "events"
    _fields = ("title", "start", "end", "start_time", "end_time", "description", "creator")

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "events")


class ActionRequiredSensor(_ListSensor):
    """Items waiting for the parent (payments, polls, consents)."""

    _attr_icon = "mdi:alert-circle-outline"
    _list_key = "action_items"
    _fields = ("name", "info", "kind")

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "action_required")


class PublicEventsSensor(_ListSensor):
    """Public school events from the school's website API."""

    _attr_icon = "mdi:calendar-globe"
    _list_key = "public_events"
    _fields = ("title", "start", "end", "location", "description")

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "public_events")


class SubstitutionsSensor(_DataBase):
    """Raw substitution plan published by the school (next 7 days)."""

    _attr_icon = "mdi:account-switch-outline"
    _unrecorded_attributes = frozenset({"data"})

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "substitutions")

    @property
    def native_value(self) -> int | None:
        subs = self.data.get("substitutions")
        if isinstance(subs, (list, dict)):
            return len(subs)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"data": self.data.get("substitutions")}


class AbsencesSensor(_DataBase):
    """Absence records."""

    _attr_icon = "mdi:account-off-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "absences")

    @property
    def native_value(self) -> int:
        return len(self.data.get("attendance", {}).get("records", []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attendance = self.data.get("attendance", {})
        return {
            "unexcused": attendance.get("unexcused", 0),
            "stats": attendance.get("stats", {}),
            "records": [
                {k: r.get(k) for k in ("date", "lesson", "subject", "status", "excuse") if r.get(k)}
                for r in attendance.get("records", [])[:LIST_LIMIT]
            ],
        }


class PaymentsSensor(_DataBase):
    """Outstanding payments (CZK)."""

    _attr_icon = "mdi:cash-clock"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "CZK"

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "payments_due")

    @property
    def native_value(self) -> float:
        return self.data.get("payments", {}).get("outstanding", 0.0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        payments = self.data.get("payments", {}).get("payments", [])
        return {
            "unpaid": [
                {k: p.get(k) for k in ("title", "amount", "due", "variable_symbol", "status") if p.get(k)}
                for p in payments
                if not p.get("paid")
            ][:LIST_LIMIT]
        }


class LastUpdateSensor(_DataBase):
    """When the data was last loaded; errors per section in attributes."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "last_update")

    @property
    def native_value(self) -> datetime | None:
        updated = self.data.get("updated")
        return dt_util.parse_datetime(updated) if updated else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        timetable = self.child.timetable.data or {}
        return {
            "timetable_updated": timetable.get("updated"),
            "timetable_source": timetable.get("source"),
            "login_method": self.runtime.client.used_login_method,
            "child_id": self.child.child.id,
            "children": [c.name for c in self.runtime.children],
            "school_year": self.data.get("school_year"),
            "errors": self.data.get("errors", {}),
        }
