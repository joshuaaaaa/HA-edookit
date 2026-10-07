"""Sensors for Edookit."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import CONF_PUBLIC_API, CONF_TRAVEL_TIME, DEFAULT_PUBLIC_API, DEFAULT_TRAVEL_TIME
from .coordinator import ChildRuntime, EdookitConfigEntry
from .entity import EdookitEntity
from .timeutil import (
    compact,
    current_or_next,
    events_on,
    lesson_end,
    lesson_start,
    lessons_on,
    next_school_day,
    school_day_bounds,
    school_day_items,
    timetable_exams,
)

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
            SchoolEndSensor(entry, child, "school_end_next", next_day=True),
            ArrivalHomeSensor(entry, child),
            SubjectSensor(entry, child),
            SubjectSensor(entry, child, "subject_start", part="start"),
            SubjectSensor(entry, child, "subject_end", part="end"),
            SchoolStartSensor(entry, child, "school_start_next", tomorrow=True),
            TimetableChangesSensor(entry, child),
            UnreadSensor(entry, child),
            MessagesSensor(entry, child),
            LastMessageTextSensor(entry, child),
            UpcomingTestsSensor(entry, child),
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
            "class_name": (self.child.data.data or {}).get("class_name"),
            "class_teacher": (self.child.data.data or {}).get("class_teacher"),
            "source": data.get("source"),
            "week_start": data.get("week_start"),
            "last_update": data.get("updated"),
            "today": [compact(ls) for ls in lessons_on(self.lessons, dt_util.now().date(), True)],
            "today_events": [ev["subject"] for ev in events_on(self.lessons, dt_util.now().date())],
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
            "events": [ev["subject"] for ev in events_on(self.lessons, tomorrow)],
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
        return school_day_bounds(self.lessons, day)[0] if day else None


class SchoolEndSensor(_TimetableBase):
    """End of school today / on the next school day (lessons and timed events such as trips)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:bell-ring-outline"
    _per_minute = True

    def __init__(
        self, entry: EdookitConfigEntry, child: ChildRuntime, key: str = "school_end_today", next_day: bool = False
    ) -> None:
        super().__init__(entry, child, key)
        self._next_day = next_day

    def _day(self) -> date | None:
        today = dt_util.now().date()
        return next_school_day(self.lessons, today) if self._next_day else today

    @property
    def native_value(self) -> datetime | None:
        day = self._day()
        return school_day_bounds(self.lessons, day)[1] if day else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        day = self._day()
        if day is None:
            return {}
        items = school_day_items(self.lessons, day)
        end = school_day_bounds(self.lessons, day)[1]
        last = max(items, key=lambda ls: lesson_end(ls) or dt_util.now()) if items else None
        attrs: dict[str, Any] = {
            "date": day.isoformat(),
            "last_lesson": last["subject"] if last else None,
            "last_period": last.get("period") if last else None,
        }
        if end and not self._next_day:
            attrs["minutes_left"] = max(0, int((end - dt_util.now()).total_seconds() // 60))
        return attrs


class ArrivalHomeSensor(_TimetableBase):
    """When the child gets home: end of school + travel time (option)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:home-account"
    _per_minute = True

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "arrival_home")
        self._travel = timedelta(minutes=float(entry.options.get(CONF_TRAVEL_TIME, DEFAULT_TRAVEL_TIME)))

    @property
    def native_value(self) -> datetime | None:
        end = school_day_bounds(self.lessons, dt_util.now().date())[1]
        return end + self._travel if end else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        today = dt_util.now().date()
        end = school_day_bounds(self.lessons, today)[1]
        nxt = next_school_day(self.lessons, today)
        next_end = school_day_bounds(self.lessons, nxt)[1] if nxt else None
        return {
            "school_end": end.isoformat() if end else None,
            "travel_minutes": int(self._travel.total_seconds() // 60),
            "at_school": bool(end and dt_util.now() < end),
            "next_school_day": nxt.isoformat() if nxt else None,
            "next_arrival": (next_end + self._travel).isoformat() if next_end else None,
        }


STATE_LABELS = {
    "lesson": "probíhá",
    "break": "přestávka",
    "before_school": "před vyučováním",
    "next_day": "další školní den",
}


class SubjectSensor(_TimetableBase):
    """The subject in progress, or the next one - with its start and end."""

    _attr_icon = "mdi:book-open-variant"
    _per_minute = True

    def __init__(
        self, entry: EdookitConfigEntry, child: ChildRuntime, key: str = "subject", part: str = "name"
    ) -> None:
        super().__init__(entry, child, key)
        self._part = part
        if part in ("start", "end"):
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
            self._attr_icon = "mdi:clock-start" if part == "start" else "mdi:clock-end"

    @property
    def native_value(self) -> Any:
        lesson, _ = current_or_next(self.lessons, dt_util.now())
        if lesson is None:
            return None
        if self._part == "start":
            return lesson_start(lesson)
        if self._part == "end":
            return lesson_end(lesson)
        return lesson["subject"][:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        now = dt_util.now()
        lesson, state = current_or_next(self.lessons, now)
        if lesson is None:
            return {}
        start, end = lesson_start(lesson), lesson_end(lesson)
        attrs = {
            **compact(lesson),
            "date": lesson["date"],
            "state": state,
            "state_label": STATE_LABELS.get(state or ""),
            "starts_at": start.isoformat() if start else None,
            "ends_at": end.isoformat() if end else None,
        }
        if state == "lesson" and end:
            attrs["minutes_left"] = int((end - now).total_seconds() // 60)
        elif start:
            attrs["minutes_until"] = int((start - now).total_seconds() // 60)
        return attrs


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

    @property
    def items(self) -> list[dict[str, Any]]:
        """Tests from the exams page plus the "Pís." badges in the timetable."""
        return upcoming_exams(self.child)

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "exams")


WEEKDAYS_SHORT = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]


def upcoming_exams(child: ChildRuntime) -> list[dict[str, Any]]:
    """Upcoming written tests of a child, nearest first (exams page + timetable badges)."""
    now = dt_util.now()
    today = now.date().isoformat()
    lessons = (child.timetable.data or {}).get("lessons", [])
    items = [dict(e) for e in (child.data.data or {}).get("exams", [])]
    known = {(str(e.get("date"))[:10], e.get("title")) for e in items}
    for exam in timetable_exams(lessons):
        if (exam["date"][:10], exam["title"]) not in known:
            items.append(exam)
            known.add((exam["date"][:10], exam["title"]))
    # Subject abbreviation from the timetable lesson of the same day/time.
    shorts = {(ls["date"], ls.get("start")): ls.get("subject_short") for ls in lessons}
    result = []
    for exam in items:
        when = str(exam.get("date") or "")
        if not when or when[:10] < today:
            continue
        if len(when) > 10:
            parsed = dt_util.parse_datetime(when)
            if parsed is not None and parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
            # A test at 8:00 today is still listed until the end of its lesson.
            end = dt_util.parse_datetime(str(exam.get("end") or "")) if exam.get("end") else None
            if end is not None and end.tzinfo is None:
                end = end.replace(tzinfo=dt_util.get_default_time_zone())
            if (end or parsed) and (end or parsed) < now:
                continue
        day = date.fromisoformat(when[:10])
        time_str = when[11:16] if len(when) > 10 and when[11:16] != "00:00" else None
        result.append(
            {
                "date": day.isoformat(),
                "weekday": WEEKDAYS_SHORT[day.weekday()],
                "time": time_str,
                "subject": exam.get("subject") or "",
                "subject_short": shorts.get((day.isoformat(), time_str)),
                "title": exam.get("title") or "",
                "period": exam.get("period"),
                "days_until": (day - now.date()).days,
                "description": exam.get("description"),
                "url": exam.get("url"),
            }
        )
    return sorted(result, key=lambda e: (e["date"], e["time"] or "99:99"))


def exam_line(exam: dict[str, Any]) -> str:
    """'Čt 8. 10. 10:55 · Matematika: Geometrické značky'."""
    day = date.fromisoformat(exam["date"])
    when = f"{exam['weekday']} {day.day}. {day.month}." + (f" {exam['time']}" if exam["time"] else "")
    subject = exam["subject"] or exam["subject_short"] or ""
    return f"{when} · {subject}: {exam['title']}" if subject else f"{when} · {exam['title']}"


class UpcomingTestsSensor(_DataBase):
    """All upcoming written tests sorted from the nearest; state = the nearest one."""

    _attr_icon = "mdi:calendar-check-outline"
    _unrecorded_attributes = frozenset({"tests", "text"})

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "upcoming_tests")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Tests also come from the timetable, and "days_until" changes at midnight.
        self.async_on_remove(self.child.timetable.async_add_listener(self.async_write_ha_state))
        self.async_on_remove(
            async_track_time_change(self.hass, lambda _now: self.async_write_ha_state(), hour=0, minute=0, second=5)
        )

    @property
    def native_value(self) -> str | None:
        tests = upcoming_exams(self.child)
        return exam_line(tests[0])[:255] if tests else "Žádné písemky"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        tests = upcoming_exams(self.child)
        return {
            "count": len(tests),
            "next_date": tests[0]["date"] if tests else None,
            "next_days_until": tests[0]["days_until"] if tests else None,
            "tests": tests,
            "text": "\n".join(exam_line(t) for t in tests) or "Žádné nadcházející písemky",
        }


class LastMessageTextSensor(_DataBase):
    """The newest message with its full text (for Telegram / push notifications)."""

    _attr_icon = "mdi:email-open-outline"
    _unrecorded_attributes = frozenset({"text", "notification"})

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "last_message_text")

    @property
    def message(self) -> dict[str, Any]:
        return self.data.get("latest_message") or {}

    @property
    def native_value(self) -> str | None:
        msg = self.message
        if not msg:
            return None
        text = msg.get("text") or msg.get("description") or msg.get("title") or ""
        return " ".join(text.split())[:255] or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        msg = self.message
        if not msg:
            return {}
        text = msg.get("text") or msg.get("description") or ""
        sender = msg.get("creator") or msg.get("from") or ""
        title = msg.get("title") or msg.get("subject") or ""
        attachments = [a["name"] for a in msg.get("attachments", [])]
        lines = [f"✉️ {title}"]
        if sender or msg.get("time"):
            lines.append(" · ".join(x for x in (sender, msg.get("time")) if x))
        if text:
            lines += ["", text]
        if attachments:
            lines += ["", "📎 " + ", ".join(attachments)]
        if msg.get("url"):
            lines += ["", self._url(msg["url"]) or ""]
        return {
            "subject": title,
            "from": sender,
            "time": msg.get("time"),
            "timestamp": msg.get("timestamp"),
            "unread": msg.get("unread"),
            "text": text,
            "attachments": attachments,
            "url": self._url(msg.get("url")),
            "full_text_loaded": bool(msg.get("text")),
            "notification": "\n".join(lines),
        }


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
        attendance = self.data.get("attendance", {})
        # Per-child dashboard summary (e.g. {"Absence omluvená": 5}) beats the shared list.
        if attendance.get("summary"):
            return sum(attendance["summary"].values())
        return len(attendance.get("records", []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attendance = self.data.get("attendance", {})
        return {
            "summary": attendance.get("summary", {}),
            "unexcused": attendance.get("summary", {}).get("Absence neomluvená", attendance.get("unexcused", 0)),
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
            "next_refresh": self.runtime.smart.plan.when.isoformat()
            if self.runtime.smart and self.runtime.smart.plan
            else None,
            "child_id": self.child.child.id,
            "children": [c.name for c in self.runtime.children],
            "school_year": self.data.get("school_year"),
            "errors": self.data.get("errors", {}),
        }
