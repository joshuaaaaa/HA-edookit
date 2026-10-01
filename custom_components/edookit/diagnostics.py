"""Diagnostics for Edookit (personal data redacted)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_API_PASSWORD, CONF_API_USERNAME, CONF_ICAL_URL
from .coordinator import EdookitConfigEntry

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME, CONF_API_PASSWORD, CONF_API_USERNAME, CONF_ICAL_URL, "student"}


def _shape(value: Any) -> Any:
    """Keep structure and counts, drop personal texts."""
    if isinstance(value, list):
        return {"count": len(value), "first": _shape(value[0]) if value else None}
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in value.items()}
    if isinstance(value, str):
        return f"<str len={len(value)}>"
    return value


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: EdookitConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    timetable = runtime.timetable.data or {}
    data = runtime.data.data or {}
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "login_method": runtime.client.used_login_method,
        "timetable": {
            "source": timetable.get("source"),
            "updated": timetable.get("updated"),
            "lessons": len(timetable.get("lessons", [])),
            "bell": timetable.get("bell"),
            "sample": _shape(timetable.get("lessons", [])[:1]),
        },
        "data": {
            "errors": data.get("errors"),
            "sections": {k: _shape(v) for k, v in data.items() if k not in ("errors", "student")},
        },
    }
