"""Binary sensors for Edookit."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .coordinator import ChildRuntime, EdookitConfigEntry
from .entity import EdookitEntity
from .timeutil import events_on, lesson_end, lesson_start, lessons_on


async def async_setup_entry(
    hass: HomeAssistant, entry: EdookitConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up binary sensors."""
    async_add_entities(
        entity
        for child in entry.runtime_data.children
        for entity in (
            SchoolDayBinarySensor(entry, child, "school_today", 0),
            SchoolDayBinarySensor(entry, child, "school_tomorrow", 1),
            InLessonBinarySensor(entry, child),
            UnreadBinarySensor(entry, child),
        )
    )


class _TimeBinary(EdookitEntity, BinarySensorEntity):
    """Timetable-based binary sensor re-evaluated every minute."""

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime, key: str) -> None:
        super().__init__(entry, child, child.timetable, key)

    @property
    def lessons(self) -> list[dict[str, Any]]:
        return (self.coordinator.data or {}).get("lessons", [])

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_track_time_change(self.hass, self._tick, second=0))

    @callback
    def _tick(self, now: datetime) -> None:
        self.async_write_ha_state()


class SchoolDayBinarySensor(_TimeBinary):
    """On when there are lessons on that day."""

    _attr_icon = "mdi:bag-personal-outline"

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime, key: str, offset: int) -> None:
        super().__init__(entry, child, key)
        self._offset = offset

    @property
    def is_on(self) -> bool:
        day = dt_util.now().date() + timedelta(days=self._offset)
        # A trip / project day counts as school; a whole-day holiday without lessons does not.
        timed_events = [ev for ev in events_on(self.lessons, day) if not ev.get("all_day")]
        return bool(lessons_on(self.lessons, day) or timed_events)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        day = dt_util.now().date() + timedelta(days=self._offset)
        todays = lessons_on(self.lessons, day)
        starts = [s for ls in todays if (s := lesson_start(ls))]
        ends = [e for ls in todays if (e := lesson_end(ls))]
        return {
            "date": day.isoformat(),
            "lessons": len(todays),
            "events": [ev["subject"] for ev in events_on(self.lessons, day)],
            "first_lesson": min(starts).isoformat() if starts else None,
            "last_lesson_end": max(ends).isoformat() if ends else None,
        }


class InLessonBinarySensor(_TimeBinary):
    """On while a lesson is in progress."""

    _attr_icon = "mdi:human-male-board"

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, "in_lesson")

    @property
    def is_on(self) -> bool:
        now = dt_util.now()
        for lesson in lessons_on(self.lessons, now.date()):
            start, end = lesson_start(lesson), lesson_end(lesson)
            if start and end and start <= now < end:
                return True
        return False


class UnreadBinarySensor(EdookitEntity, BinarySensorEntity):
    """On when there is something unread in the inbox."""

    _attr_icon = "mdi:email-alert-outline"

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, child.data, "has_unread")

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("unread"))
