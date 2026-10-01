"""Refresh button for Edookit."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ChildRuntime, EdookitConfigEntry
from .entity import EdookitEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: EdookitConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the refresh button."""
    async_add_entities(RefreshButton(entry, child) for child in entry.runtime_data.children)


class RefreshButton(EdookitEntity, ButtonEntity):
    """Reload timetable and all data from Edookit now."""

    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: EdookitConfigEntry, child: ChildRuntime) -> None:
        super().__init__(entry, child, child.data, "refresh")

    async def async_press(self) -> None:
        await self.child.timetable.async_refresh()
        await self.child.data.async_refresh()
