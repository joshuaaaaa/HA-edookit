"""Base entity for Edookit."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import DOMAIN
from .coordinator import ChildRuntime, EdookitConfigEntry, EdookitRuntimeData


class EdookitEntity(CoordinatorEntity[DataUpdateCoordinator]):
    """Common device info / naming; one device per student."""

    _attr_has_entity_name = True

    def __init__(
        self, entry: EdookitConfigEntry, child: ChildRuntime, coordinator: DataUpdateCoordinator, key: str
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.child = child
        client = entry.runtime_data.client
        # The single/default student keeps the ids used before multi-child support.
        device_id = f"{entry.entry_id}_{child.child.id}" if child.child.id else entry.entry_id
        self._attr_unique_id = f"{device_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=f"Edookit {child.name or entry.title}",
            manufacturer="Edookit",
            model=f"{client.school}.edookit.net",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=client.base_url,
        )

    @property
    def runtime(self) -> EdookitRuntimeData:
        """Shortcut to the entry's runtime data."""
        return self.entry.runtime_data
