"""Integration tests: config flow, setup, entities, services and events."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.edookit.api import EdookitAuthError
from custom_components.edookit.const import DOMAIN, EVENT_NEW_ITEM
from custom_components.edookit.diagnostics import async_get_config_entry_diagnostics

from .conftest import load

PAGES = {
    "/": "dashboard.html",
    "/overview/updates": "inbox.html",
    "/evaluation/listing": "evaluation_list.html",
    "/timetable/upcoming": "upcoming.html",
    "/payments/": "payments.html",
}

ENTRY_DATA = {"school": "skola", "username": "rodic@example.com", "password": "secret", "login_method": "auto"}


@pytest.fixture
def pages() -> dict[str, str]:
    return {path: load(name) for path, name in PAGES.items()}


@pytest.fixture
def mock_client(pages):
    async def get_page(self, path: str) -> str:
        return pages.get(path, "<html><body></body></html>")

    async def login(self) -> None:
        self.logged_in = True
        self.used_login_method = "plus4u"

    with (
        patch("custom_components.edookit.api.EdookitClient.async_login", login),
        patch("custom_components.edookit.api.EdookitClient.async_get_page", get_page),
        patch(
            "custom_components.edookit.api.EdookitClient.async_get_timetable_pages",
            AsyncMock(return_value=[load("timetable_grid.html")]),
        ),
    ):
        yield


async def _setup(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> MockConfigEntry:
    await hass.config.async_set_time_zone("Europe/Prague")
    freezer.move_to("2026-09-28 08:10:00+02:00")
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, title="Jan Novák", unique_id="skola:rodic@example.com")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with (
        patch(
            "custom_components.edookit.config_flow.validate_login",
            AsyncMock(return_value={"school": "skola", "student": "Jan Novák", "method": "plus4u"}),
        ),
        patch("custom_components.edookit.async_setup_entry", AsyncMock(return_value=True)),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**ENTRY_DATA, "school": "https://skola.edookit.net/"},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Jan Novák"
    assert result["data"]["school"] == "skola"


async def test_config_flow_errors(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**ENTRY_DATA, "school": "bad school!"})
    assert result["errors"] == {"school": "invalid_school"}
    with patch("custom_components.edookit.config_flow.validate_login", AsyncMock(side_effect=EdookitAuthError("no"))):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["errors"] == {"base": "invalid_auth"}


async def test_setup_entities(hass: HomeAssistant, freezer: FrozenDateTimeFactory, mock_client) -> None:
    entry = await _setup(hass, freezer)
    assert entry.state is ConfigEntryState.LOADED

    timetable = hass.states.get("sensor.edookit_jan_novak_timetable")
    assert timetable.state == "2"
    assert timetable.attributes["student"] == "Jan Novák"
    assert timetable.attributes["source"] == "portal"
    days = timetable.attributes["days"]
    assert [d["date"] for d in days[:5]] == ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]
    assert days[0]["weekday_short"] == "Po"
    assert [ls["subject"] for ls in days[0]["lessons"]] == ["M", "ČJ"]
    assert timetable.attributes["bell"][0] == {"period": 1, "start": "08:00", "end": "08:45"}

    assert hass.states.get("sensor.edookit_jan_novak_current_lesson").state == "M"
    assert hass.states.get("sensor.edookit_jan_novak_next_lesson").state == "ČJ"
    assert hass.states.get("binary_sensor.edookit_jan_novak_school_today").state == "on"
    assert hass.states.get("binary_sensor.edookit_jan_novak_in_lesson").state == "on"
    # Tuesday: AJ is cancelled, only PŘ remains.
    assert hass.states.get("sensor.edookit_jan_novak_lessons_tomorrow").state == "1"
    assert hass.states.get("sensor.edookit_jan_novak_timetable_changes").state == "2"
    assert hass.states.get("sensor.edookit_jan_novak_school_starts_today").state == "2026-09-28T06:00:00+00:00"

    assert hass.states.get("sensor.edookit_jan_novak_unread_notifications").state == "2"
    assert hass.states.get("sensor.edookit_jan_novak_last_message").state == "Třídní schůzky"
    assert hass.states.get("sensor.edookit_jan_novak_last_grade").state == "3"  # "Včera" is the newest
    assert float(hass.states.get("sensor.edookit_jan_novak_grade_average").state) == pytest.approx(2.08, 0.01)
    assert hass.states.get("sensor.edookit_jan_novak_school_events").state == "2"
    assert hass.states.get("sensor.edookit_jan_novak_requires_action").state == "2"
    assert float(hass.states.get("sensor.edookit_jan_novak_payments_due").state) == 1250.0

    cal = hass.states.get("calendar.edookit_jan_novak_timetable")
    assert cal.attributes["message"] == "M"

    response = await hass.services.async_call(
        DOMAIN,
        "get_timetable",
        {"start_date": "2026-09-29", "end_date": "2026-09-29"},
        blocking=True,
        return_response=True,
    )
    lessons = response[entry.entry_id][0]["lessons"]
    assert [ls["subject"] for ls in lessons] == ["AJ", "PŘ"]

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"]["password"] == "**REDACTED**"
    assert diag["children"][0]["timetable"]["lessons"] == 4

    # Minutes later the current lesson changes without any download.
    freezer.move_to("2026-09-28 09:00:00+02:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.edookit_jan_novak_current_lesson").state == "ČJ"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_new_item_event(hass: HomeAssistant, freezer: FrozenDateTimeFactory, mock_client, pages) -> None:
    entry = await _setup(hass, freezer)
    events = async_capture_events(hass, EVENT_NEW_ITEM)

    # Nothing is announced for items that were already there at setup.
    await entry.runtime_data.children[0].data.async_refresh()
    await hass.async_block_till_done()
    assert events == []

    pages["/overview/updates"] = pages["/overview/updates"].replace(
        '<div class="items">',
        '<div class="items"><div class="item exam unread" onclick=\'window.location.href="/exams/detail?exam=5"\'>'
        '<div class="object-name">Písemka z matematiky</div><div class="creator">Dvořák</div>'
        '<div class="time">Dnes, 9:00</div></div>',
    )
    await entry.runtime_data.children[0].data.async_refresh()
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data["type"] == "exam"
    assert events[0].data["title"] == "Písemka z matematiky"
    assert events[0].data["url"] == "https://skola.edookit.net/exams/detail?exam=5"


async def test_auth_failure_starts_reauth(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    await hass.config.async_set_time_zone("Europe/Prague")
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, title="Jan Novák")
    entry.add_to_hass(hass)
    with patch(
        "custom_components.edookit.api.EdookitClient.async_get_page",
        AsyncMock(side_effect=EdookitAuthError("bad password")),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert flows and flows[0]["context"]["source"] == "reauth"


async def test_options_flow(hass: HomeAssistant, freezer: FrozenDateTimeFactory, mock_client) -> None:
    entry = await _setup(hass, freezer)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "timetable_update_time": "06:30:00",
            "scan_interval": 30,
            "timetable_weeks": 1,
            "timetable_source": "portal",
            "fire_events": False,
            "public_api": False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["timetable_update_time"] == "06:30:00"
    assert entry.options["ical_url"] == ""
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.children[0].data.update_interval.total_seconds() == 1800


async def test_reauth_flow(hass: HomeAssistant, freezer: FrozenDateTimeFactory, mock_client) -> None:
    entry = await _setup(hass, freezer)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    with patch(
        "custom_components.edookit.config_flow.validate_login",
        AsyncMock(return_value={"school": "skola", "student": "Jan Novák", "method": "plus4u"}),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "new"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new"


FAMILY_DASHBOARD = """<html><head><title>Edookit</title></head><body>
<span class="fullname">Jakub Hrubý (Jarmila Šuláková, 9549-3740-1)</span>
<div class="students">
  <a class="selected" href="/?mainMenu-studentSelector-new=101&amp;do=mainMenu-studentSelector-change">Anna Hrubá</a>
  <a href="/?mainMenu-studentSelector-new=102&amp;do=mainMenu-studentSelector-change">Petr Hrubý</a>
</div></body></html>"""


async def test_two_children(hass: HomeAssistant, freezer: FrozenDateTimeFactory, pages) -> None:
    """A parent account with a child switcher gets one device per child."""
    selected = {"id": "101"}
    pages["/"] = FAMILY_DASHBOARD

    async def get_page(self, path: str) -> str:
        if "studentSelector-new=" in path:
            selected["id"] = path.split("studentSelector-new=")[1][:3]
            return FAMILY_DASHBOARD.replace('class="selected" ', "").replace(
                f'<a href="/?mainMenu-studentSelector-new={selected["id"]}',
                f'<a class="selected" href="/?mainMenu-studentSelector-new={selected["id"]}',
            )
        return pages.get(path, "<html><body></body></html>")

    async def timetable_pages(self, weeks: int) -> list[str]:
        html = load("timetable_grid.html")
        if selected["id"] == "102":  # Petr has only Monday's first lesson
            html = html.split('<div class="lesson changed"')[0] + "</div></div></body></html>"
        return [html]

    async def login(self) -> None:
        self.logged_in = True

    with (
        patch("custom_components.edookit.api.EdookitClient.async_login", login),
        patch("custom_components.edookit.api.EdookitClient.async_get_page", get_page),
        patch("custom_components.edookit.api.EdookitClient.async_get_timetable_pages", timetable_pages),
    ):
        entry = await _setup(hass, freezer)
        assert entry.state is ConfigEntryState.LOADED
        assert [c.name for c in entry.runtime_data.children] == ["Anna Hrubá", "Petr Hrubý"]
        assert hass.states.get("sensor.edookit_anna_hruba_timetable").state == "2"
        assert hass.states.get("sensor.edookit_petr_hruby_timetable").state == "1"
        assert hass.states.get("sensor.edookit_anna_hruba_timetable").attributes["student"] == "Anna Hrubá"
        assert hass.states.get("binary_sensor.edookit_petr_hruby_school_today").state == "on"

        # An inbox item visible for both children is announced only once.
        events = async_capture_events(hass, EVENT_NEW_ITEM)
        pages["/overview/updates"] = pages["/overview/updates"].replace(
            '<div class="items">',
            '<div class="items"><div class="item poll" onclick=\'window.location.href="/polls/detail?poll=9"\'>'
            '<div class="object-name">Anketa</div><div class="time">Dnes, 9:00</div></div>',
        )
        for child in entry.runtime_data.children:
            await child.data.async_refresh()
        await hass.async_block_till_done()
        assert len(events) == 1
        assert events[0].data["child_id"] in ("101", "102")


async def test_stale_child_devices_removed(hass: HomeAssistant, freezer: FrozenDateTimeFactory, mock_client) -> None:
    """Devices of children that are no longer detected disappear on reload."""
    from homeassistant.helpers import device_registry as dr

    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, title="Jan Novák")
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    stale = registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{entry.entry_id}_999")}, name="Edookit bogus"
    )
    await hass.config.async_set_time_zone("Europe/Prague")
    freezer.move_to("2026-09-28 08:10:00+02:00")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(stale.id) is None
    assert registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)}) is not None


async def test_family_portal_two_children(hass: HomeAssistant, freezer: FrozenDateTimeFactory, pages) -> None:
    """Real parent portal layout: both children on the same pages, one device each."""
    pages["/"] = load("family_dashboard.html")
    requested: list[str] = []

    async def get_page(self, path: str) -> str:
        requested.append(path)
        if path.startswith("/timetable/"):
            return load("family_timetable.html")
        return pages.get(path, "<html><body></body></html>")

    async def login(self) -> None:
        self.logged_in = True

    with (
        patch("custom_components.edookit.api.EdookitClient.async_login", login),
        patch("custom_components.edookit.api.EdookitClient.async_get_page", get_page),
    ):
        await hass.config.async_set_time_zone("Europe/Prague")
        freezer.move_to("2026-10-01 09:00:00+02:00")
        entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, title="Rodič")
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert [c.name for c in entry.runtime_data.children] == ["Anna Nováková", "Petr Novák"]
    anna = hass.states.get("sensor.edookit_anna_novakova_timetable")
    assert anna.state == "6"
    assert anna.attributes["class_name"] == "VI.B"
    assert hass.states.get("sensor.edookit_petr_novak_current_lesson").state == "Český jazyk a literatura"
    assert hass.states.get("sensor.edookit_petr_novak_current_lesson").attributes["topic"] == "Sloh: inzerát."
    assert hass.states.get("sensor.edookit_petr_novak_absences").state == "5"
    assert hass.states.get("sensor.edookit_petr_novak_last_grade").state in {"1", "2", "3", "N", "Pouze komentář"}
    assert hass.states.get("sensor.edookit_petr_novak_grade_average").attributes["subjects"]["Matematika"] == 3.0

    # Written tests from the timetable ("Pís." badges) reach the sensor and the calendars.
    exams = hass.states.get("sensor.edookit_petr_novak_written_tests")
    assert exams.state == "1"
    assert exams.attributes["next"]["title"] == "Geometrické značky"
    from homeassistant.util import dt as dt_util

    agenda = hass.data["entity_components"]["calendar"].get_entity("calendar.edookit_petr_novak_school_agenda")
    start = dt_util.parse_datetime("2026-09-28T00:00:00+02:00")
    events = await agenda.async_get_events(hass, start, start + timedelta(days=7))
    assert {e.summary for e in events} >= {
        "📝 Pravopisné cvičení - září (Český jazyk a literatura)",
        "📝 Geometrické značky (Seminář matematiky)",
    }

    # The week is moved with relative steps and always reset back (2 weeks by default).
    timetable_requests = [p for p in requested if p.startswith("/timetable/?")]
    assert timetable_requests[:3] == [
        "/timetable/?do=familyTimetable-resetFilter",
        "/timetable/?familyTimetable-value=7&do=familyTimetable-changeFilter",
        "/timetable/?do=familyTimetable-resetFilter",
    ]
    # Both children share one download of the timetable pages.
    assert len(timetable_requests) == 3
