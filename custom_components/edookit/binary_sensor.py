"""Binary sensors for Edookit."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .coordinator import EdookitConfigEntry
from .entity import EdookitEntity
from .timeutil import lesson_end, lesson_start, lessons_on


async def async_setup_entry(
    hass: HomeAssistant, entry: EdookitConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up binary sensors."""
    async_add_entities(
        [
            SchoolDayBinarySensor(entry, "school_today", 0),
            SchoolDayBinarySensor(entry, "school_tomorrow", 1),
            InLessonBinarySensor(entry),
            UnreadBinarySensor(entry),
        ]
    )


class _TimeBinary(EdookitEntity, BinarySensorEntity):
    """Timetable-based binary sensor re-evaluated every minute."""

    def __init__(self, entry: EdookitConfigEntry, key: str) -> None:
        super().__init__(entry, entry.runtime_data.timetable, key)

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

    def __init__(self, entry: EdookitConfigEntry, key: str, offset: int) -> None:
        super().__init__(entry, key)
        self._offset = offset

    @property
    def is_on(self) -> bool:
        day = dt_util.now().date() + timedelta(days=self._offset)
        return bool(lessons_on(self.lessons, day))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        day = dt_util.now().date() + timedelta(days=self._offset)
        todays = lessons_on(self.lessons, day)
        starts = [s for ls in todays if (s := lesson_start(ls))]
        ends = [e for ls in todays if (e := lesson_end(ls))]
        return {
            "date": day.isoformat(),
            "lessons": len(todays),
            "first_lesson": min(starts).isoformat() if starts else None,
            "last_lesson_end": max(ends).isoformat() if ends else None,
        }


class InLessonBinarySensor(_TimeBinary):
    """On while a lesson is in progress."""

    _attr_icon = "mdi:human-male-board"

    def __init__(self, entry: EdookitConfigEntry) -> None:
        super().__init__(entry, "in_lesson")

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

    def __init__(self, entry: EdookitConfigEntry) -> None:
        super().__init__(entry, entry.runtime_data.data, "has_unread")

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("unread"))
