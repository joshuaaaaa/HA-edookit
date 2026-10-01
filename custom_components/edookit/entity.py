"""Base entity for Edookit."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import DOMAIN
from .coordinator import EdookitConfigEntry


class EdookitEntity(CoordinatorEntity[DataUpdateCoordinator]):
    """Common device info / naming for all Edookit entities."""

    _attr_has_entity_name = True

    def __init__(self, entry: EdookitConfigEntry, coordinator: DataUpdateCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self.entry = entry
        runtime = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        student = runtime.data.student or entry.title
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Edookit {student}",
            manufacturer="Edookit",
            model=f"{runtime.client.school}.edookit.net",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=runtime.client.base_url,
        )

    @property
    def runtime(self):
        """Shortcut to the entry's runtime data."""
        return self.entry.runtime_data
