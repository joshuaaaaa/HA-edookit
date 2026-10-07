"""Edookit (Czech school information system) integration for Home Assistant."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
import json
import logging
from pathlib import Path
import re
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.event import async_track_point_in_time, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import EdookitAuthError, EdookitClient, EdookitError
from .const import (
    CONF_AFTER_LESSON_DELAY,
    CONF_API_PASSWORD,
    CONF_API_USERNAME,
    CONF_LOGIN_METHOD,
    CONF_OFF_SCHOOL_INTERVAL,
    CONF_OIDC_CLIENT_ID,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_SCHOOL,
    CONF_SMART_REFRESH,
    CONF_TIMETABLE_TIME,
    DEFAULT_AFTER_LESSON_DELAY,
    DEFAULT_OFF_SCHOOL_INTERVAL,
    DEFAULT_QUIET_END,
    DEFAULT_QUIET_START,
    DEFAULT_SMART_REFRESH,
    DEFAULT_TIMETABLE_TIME,
    DOMAIN,
    LOGIN_AUTO,
    SERVICE_DUMP_PAGES,
    SERVICE_GET_TIMETABLE,
    SERVICE_REFRESH,
    STORAGE_VERSION,
)
from .coordinator import (
    PAGES,
    ChildRuntime,
    EdookitConfigEntry,
    EdookitDataCoordinator,
    EdookitRuntimeData,
    TimetableCoordinator,
)
from .scheduler import RefreshPlan, plan_next_refresh
from .timeutil import lesson_end

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ATTR_ENTRY_ID = "config_entry_id"
ATTR_WHAT = "what"
ATTR_START = "start_date"
ATTR_END = "end_date"


def parse_time_option(value: str | None, default: str = DEFAULT_TIMETABLE_TIME) -> time:
    """Parse ``HH:MM[:SS]`` (falls back to ``default``)."""
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", (value or "").strip())
    if not match:
        return parse_time_option(default)
    return time(int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services."""
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EdookitConfigEntry) -> bool:
    """Set up Edookit from a config entry."""
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    client = EdookitClient(
        session,
        entry.data[CONF_SCHOOL],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_LOGIN_METHOD, LOGIN_AUTO),
        oidc_client_id=entry.options.get(CONF_OIDC_CLIENT_ID) or entry.data.get(CONF_OIDC_CLIENT_ID),
        api_username=entry.options.get(CONF_API_USERNAME),
        api_password=entry.options.get(CONF_API_PASSWORD),
    )
    store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}", private=True)
    stored: dict[str, Any] = await store.async_load() or {}
    client.import_cookies(stored.get("cookies"))

    # A parent account can see several children (switcher at the top of the portal).
    try:
        found = await client.async_get_children()
    except EdookitAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except EdookitError as err:
        raise ConfigEntryNotReady(str(err)) from err

    children: list[ChildRuntime] = []
    for child in found:
        data = EdookitDataCoordinator(hass, entry, client, store, stored, child)
        timetable = TimetableCoordinator(hass, entry, client, store, stored, child)
        # Data first: it learns the student's name.
        await data.async_config_entry_first_refresh()
        await timetable.async_config_entry_first_refresh()
        children.append(ChildRuntime(child, timetable, data))

    entry.runtime_data = EdookitRuntimeData(client, store, stored, children)
    _remove_stale_devices(hass, entry, children)

    when = parse_time_option(entry.options.get(CONF_TIMETABLE_TIME, DEFAULT_TIMETABLE_TIME))

    @callback
    def _daily_refresh(now: datetime) -> None:
        _LOGGER.debug("Scheduled daily Edookit refresh")
        for child in children:
            hass.async_create_task(child.timetable.async_request_refresh())
            if child.data.update_interval is None:
                hass.async_create_task(child.data.async_request_refresh())

    entry.async_on_unload(
        async_track_time_change(hass, _daily_refresh, hour=when.hour, minute=when.minute, second=when.second)
    )
    if entry.options.get(CONF_SMART_REFRESH, DEFAULT_SMART_REFRESH):
        entry.runtime_data.smart = SmartRefresh(hass, entry, children)
        entry.runtime_data.smart.start()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


class SmartRefresh:
    """Refresh after every lesson, rarely outside school, never at night."""

    def __init__(self, hass: HomeAssistant, entry: EdookitConfigEntry, children: list[ChildRuntime]) -> None:
        self.hass = hass
        self.entry = entry
        self.children = children
        opts = entry.options
        self.delay = timedelta(minutes=float(opts.get(CONF_AFTER_LESSON_DELAY, DEFAULT_AFTER_LESSON_DELAY)))
        self.off_school = timedelta(
            minutes=max(15.0, float(opts.get(CONF_OFF_SCHOOL_INTERVAL, DEFAULT_OFF_SCHOOL_INTERVAL)))
        )
        self.quiet_start = parse_time_option(opts.get(CONF_QUIET_START, DEFAULT_QUIET_START), DEFAULT_QUIET_START)
        self.quiet_end = parse_time_option(opts.get(CONF_QUIET_END, DEFAULT_QUIET_END), DEFAULT_QUIET_END)
        self._unsub: Callable[[], None] | None = None
        self.plan: RefreshPlan | None = None

    def start(self) -> None:
        """Schedule the first refresh and cancel the timer on unload."""
        self.entry.async_on_unload(self._cancel)
        self._schedule()

    @callback
    def _cancel(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    def _lesson_ends(self) -> list[datetime]:
        ends: list[datetime] = []
        for child in self.children:
            for lesson in (child.timetable.data or {}).get("lessons", []):
                if lesson.get("cancelled") or lesson.get("all_day"):
                    continue
                if lesson.get("kind") == "event" and not lesson.get("start"):
                    continue
                if (end := lesson_end(lesson)) is not None:
                    ends.append(end)
        return ends

    @callback
    def _schedule(self) -> None:
        self._cancel()
        self.plan = plan_next_refresh(
            dt_util.now(),
            self._lesson_ends(),
            delay=self.delay,
            off_school=self.off_school,
            quiet_start=self.quiet_start,
            quiet_end=self.quiet_end,
        )
        _LOGGER.debug("Next Edookit refresh at %s (%s)", self.plan.when, self.plan)
        self._unsub = async_track_point_in_time(self.hass, self._fire, self.plan.when)

    async def _fire(self, now: datetime) -> None:
        self._unsub = None
        plan = self.plan
        try:
            for child in self.children:
                await child.data.async_refresh()
                if plan and plan.last_of_day:
                    # After school: catch timetable changes for the next days.
                    await child.timetable.async_refresh()
        finally:
            self._schedule()


def _remove_stale_devices(hass: HomeAssistant, entry: ConfigEntry, children: list[ChildRuntime]) -> None:
    """Drop devices (and their entities) of students that are no longer found."""
    valid = {(DOMAIN, f"{entry.entry_id}_{c.child.id}" if c.child.id else entry.entry_id) for c in children}
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not device.identifiers & valid:
            _LOGGER.info("Removing stale Edookit device %s", device.name)
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EdookitConfigEntry) -> bool:
    """Unload a config entry."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.store.async_save(entry.runtime_data.stored)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete stored cookies when the entry is removed."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()


# ------------------------------------------------------------------ services


def _entries(hass: HomeAssistant, call: ServiceCall) -> list[EdookitConfigEntry]:
    entry_id = call.data.get(ATTR_ENTRY_ID)
    entries = [
        e for e in hass.config_entries.async_loaded_entries(DOMAIN) if entry_id is None or e.entry_id == entry_id
    ]
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_not_found")
    return entries


def _register_services(hass: HomeAssistant) -> None:
    async def refresh(call: ServiceCall) -> None:
        what = call.data[ATTR_WHAT]
        for entry in _entries(hass, call):
            for child in entry.runtime_data.children:
                if what in ("all", "timetable"):
                    await child.timetable.async_refresh()
                if what in ("all", "data"):
                    await child.data.async_refresh()

    async def dump_pages(call: ServiceCall) -> ServiceResponse:
        folder = Path(hass.config.path("edookit_debug"))
        written: list[str] = []

        def _write(target: Path, text: str) -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")

        for entry in _entries(hass, call):
            client = entry.runtime_data.client
            base = folder / client.school
            children = [rt.child for rt in entry.runtime_data.children]
            await hass.async_add_executor_job(
                _write,
                base / "children.json",
                json.dumps([c.__dict__ for c in children], ensure_ascii=False, indent=2),
            )
            for rt in entry.runtime_data.children:
                child = rt.child
                pages = {**PAGES, "timetable": "/timetable/"}
                latest = (rt.data.data or {}).get("latest_message") or {}
                if str(latest.get("url") or "").startswith("/"):
                    pages["message"] = latest["url"]
                async with client.as_child(child):
                    for key, path in pages.items():
                        try:
                            html = await client.async_get_page(path)
                        except Exception as err:
                            html = f"<!-- error: {err} -->"
                        target = base / (child.key or "default") / f"{key}.html"
                        await hass.async_add_executor_job(_write, target, html)
                        written.append(str(target))
        _LOGGER.warning(
            "Edookit pages saved to %s. They contain personal data - anonymise them before sharing",
            folder,
        )
        return {"files": written}

    async def get_timetable(call: ServiceCall) -> ServiceResponse:
        start: date = call.data.get(ATTR_START) or dt_util.now().date()
        end: date = call.data.get(ATTR_END) or start
        result = {}
        for entry in _entries(hass, call):
            result[entry.entry_id] = [
                {
                    "student": child.name,
                    "child_id": child.child.id,
                    "lessons": [
                        ls
                        for ls in (child.timetable.data or {}).get("lessons", [])
                        if start.isoformat() <= ls["date"] <= end.isoformat()
                    ],
                }
                for child in entry.runtime_data.children
            ]
        return result

    entry_schema = {vol.Optional(ATTR_ENTRY_ID): cv.string}
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH,
        refresh,
        schema=vol.Schema(
            {**entry_schema, vol.Optional(ATTR_WHAT, default="all"): vol.In(["all", "timetable", "data"])}
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DUMP_PAGES,
        dump_pages,
        schema=vol.Schema(entry_schema),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_TIMETABLE,
        get_timetable,
        schema=vol.Schema({**entry_schema, vol.Optional(ATTR_START): cv.date, vol.Optional(ATTR_END): cv.date}),
        supports_response=SupportsResponse.ONLY,
    )
