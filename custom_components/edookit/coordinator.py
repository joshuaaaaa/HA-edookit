"""Data update coordinators for Edookit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import parsers
from .api import Child, EdookitAuthError, EdookitClient, EdookitConnectionError, EdookitError
from .const import (
    CONF_API_STUDENT_ID,
    CONF_FIRE_EVENTS,
    CONF_ICAL_URL,
    CONF_PUBLIC_API,
    CONF_SCAN_INTERVAL,
    CONF_TIMETABLE_SOURCE,
    CONF_WEEKS,
    DEFAULT_FIRE_EVENTS,
    DEFAULT_PUBLIC_API,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WEEKS,
    DOMAIN,
    EVENT_NEW_ITEM,
    ITEM_TYPES,
    SOURCE_API,
    SOURCE_AUTO,
    SOURCE_ICAL,
    SOURCE_PORTAL,
    WEEKDAYS_CS,
    WEEKDAYS_CS_SHORT,
)
from .ical import ical_to_lessons, parse_ical

_LOGGER = logging.getLogger(__name__)

SEEN_RETENTION = timedelta(days=120)


@dataclass
class ChildRuntime:
    """Coordinators of one student."""

    child: Child
    timetable: TimetableCoordinator
    data: EdookitDataCoordinator

    @property
    def name(self) -> str | None:
        """Student name for device / entity naming."""
        return parsers.display_name(self.child.name or self.data.student)


@dataclass
class EdookitRuntimeData:
    """Objects shared by the platforms of one config entry."""

    client: EdookitClient
    store: Store
    stored: dict[str, Any]
    children: list[ChildRuntime]


type EdookitConfigEntry = ConfigEntry[EdookitRuntimeData]


def _opt(entry: ConfigEntry, key: str, default: Any = None) -> Any:
    return entry.options.get(key, entry.data.get(key, default))


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


async def _as_child(client: EdookitClient, child: Child, fetch: Any) -> dict[str, Any]:
    """Run ``fetch()`` with the child selected in the portal session."""
    try:
        async with client.as_child(child):
            return await fetch()
    except EdookitAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except EdookitError as err:
        raise UpdateFailed(str(err)) from err


def pick_child(blocks: list[dict[str, Any]], child: Child) -> dict[str, Any] | None:
    """The block of ``child`` in a page that lists every student of the account."""
    if child.id is not None:
        return next((b for b in blocks if b["id"] == child.id), None)
    return blocks[0] if blocks else None


async def _save(store: Store, stored: dict[str, Any], client: EdookitClient) -> None:
    stored["cookies"] = client.export_cookies()
    store.async_delay_save(lambda: stored, 5)


# ----------------------------------------------------------------- timetable


class TimetableCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Timetable, refreshed once a day at the configured time (and on demand)."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: EdookitClient,
        store: Store,
        stored: dict,
        child: Child,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} timetable {client.school} {child.key}".strip(),
            update_interval=None,
        )
        self.client = client
        self.child = child
        self._store = store
        self._stored = stored

    async def _async_update_data(self) -> dict[str, Any]:
        return await _as_child(self.client, self.child, self._fetch)

    async def _fetch(self) -> dict[str, Any]:
        entry = self.config_entry
        weeks = int(_opt(entry, CONF_WEEKS, DEFAULT_WEEKS))
        today = dt_util.now().date()
        start = _week_start(today)
        # On a weekend the current week is over; show the next one(s).
        if today.weekday() >= 5:
            start += timedelta(days=7)
        end = start + timedelta(days=7 * weeks)

        source = _opt(entry, CONF_TIMETABLE_SOURCE, SOURCE_AUTO)
        ical_url = _opt(entry, CONF_ICAL_URL)
        student_id = _opt(entry, CONF_API_STUDENT_ID)
        if source == SOURCE_AUTO:
            if self.child.id is not None:
                # iCal / API options describe one student; with a child switcher use the portal.
                source = SOURCE_PORTAL
            elif self.client.has_api and student_id:
                source = SOURCE_API
            elif ical_url:
                source = SOURCE_ICAL
            else:
                source = SOURCE_PORTAL

        try:
            if source == SOURCE_API:
                lessons = await self._lessons_from_api(start, end, student_id)
            elif source == SOURCE_ICAL:
                lessons = await self._lessons_from_ical(ical_url, start, end)
            else:
                lessons = await self._lessons_from_portal(weeks, today)
        except EdookitAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EdookitError as err:
            raise UpdateFailed(f"Timetable update failed: {err}") from err

        lessons = [ls for ls in lessons if start.isoformat() <= ls["date"] < end.isoformat()] or lessons
        lessons.sort(key=lambda ls: (ls["date"], ls.get("start") or "99:99"))
        _fill_order(lessons)
        parsers.assign_periods(lessons)
        await _save(self._store, self._stored, self.client)

        return {
            "source": source,
            "lessons": lessons,
            "days": build_days(lessons, start, end),
            "bell": parsers.bell_schedule(lessons),
            "week_start": start.isoformat(),
            "updated": dt_util.now().isoformat(),
        }

    async def _lessons_from_portal(self, weeks: int, today: date) -> list[dict[str, Any]]:
        pages = await self.client.async_get_timetable_pages(weeks)
        lessons: list[dict[str, Any]] = []
        seen: set[tuple] = set()

        def add(items: list[dict[str, Any]]) -> None:
            for lesson in items:
                key = (lesson["date"], lesson.get("start"), lesson["subject"], lesson.get("kind"))
                if key not in seen:
                    seen.add(key)
                    lessons.append(lesson)

        for html in pages:
            block = pick_child(parsers.parse_family_timetables(html), self.child)
            add(block["lessons"] if block else [])

        # The dashboard widget has this week's lessons incl. topics ("Učivo").
        dashboard = pick_child(
            parsers.parse_dashboard_children(await self.client.async_get_page("/"), today), self.child
        )
        if dashboard:
            topics = {(ls["date"], ls.get("start")): ls.get("topic") for ls in dashboard["lessons"] if ls.get("topic")}
            for lesson in lessons:
                lesson["topic"] = lesson.get("topic") or topics.get((lesson["date"], lesson.get("start")))
            covered = {ls["date"] for ls in lessons}
            add([ls for ls in dashboard["lessons"] if ls["date"] not in covered])

        if not lessons:
            # Other layouts (student accounts, older portals).
            for html in pages:
                add(parsers.parse_timetable(html, today))
        if not lessons:
            _LOGGER.warning(
                "No lessons found on the Edookit timetable page. If the school's "
                "timetable is not empty, call the edookit.dump_pages service and "
                "open an issue with the (anonymised) timetable HTML"
            )
        return lessons

    async def _lessons_from_ical(self, url: str, start: date, end: date) -> list[dict[str, Any]]:
        text = await self.client.async_get_url(url)
        tz = str(dt_util.get_default_time_zone())
        lessons = ical_to_lessons(parse_ical(text, tz), tz)
        return [ls for ls in lessons if start.isoformat() <= ls["date"] < end.isoformat()]

    async def _lessons_from_api(self, start: date, end: date, student_id: Any) -> list[dict[str, Any]]:
        lessons: list[dict[str, Any]] = []
        day = start
        while day < end:
            if day.weekday() < 5:
                payload = await self.client.async_api_lessons(day, student_id)
                lessons.extend(normalize_api_lessons(payload))
            day += timedelta(days=1)
        return lessons


def _names(value: Any) -> str | None:
    """Turn API name containers (str, list, dict of dicts) into ``a, b``."""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        if any(k in value for k in ("name", "full_name", "code", "label")):
            return _names(value.get("name") or value.get("full_name") or value.get("label") or value.get("code"))
        value = list(value.values())
    if isinstance(value, list):
        names = [n for n in (_names(v) for v in value) if n]
        return ", ".join(dict.fromkeys(names)) or None
    return str(value)


def normalize_api_lessons(payload: Any) -> list[dict[str, Any]]:
    """Map ``/api/lesson/v2/list-lessons`` items to lesson dicts."""
    items = payload
    if isinstance(payload, dict):
        items = payload.get("lessons") or payload.get("data") or payload.get("items") or list(payload.values())
    result = []
    for item in items or []:
        if not isinstance(item, dict) or not item.get("datetime_from"):
            continue
        start = dt_util.parse_datetime(str(item["datetime_from"]).replace(" ", "T"))
        end = (
            dt_util.parse_datetime(str(item.get("datetime_to", "")).replace(" ", "T"))
            if item.get("datetime_to")
            else None
        )
        if start is None:
            continue
        courses = item.get("courses") or {}
        course = next(iter(courses.values()), {}) if isinstance(courses, dict) else (courses[0] if courses else {})
        if not isinstance(course, dict):
            course = {}
        subject = _names(course.get("name")) or item.get("name") or "?"
        flags = " ".join(str(item.get(k, "")) for k in ("status", "state", "note")).lower()
        cancelled = bool(item.get("cancelled") or item.get("canceled") or "zruš" in flags or "cancel" in flags)
        result.append(
            {
                "date": start.date().isoformat(),
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M") if end else None,
                "subject": subject,
                "subject_short": item.get("name") or course.get("code"),
                "teacher": _names(item.get("teachers") or course.get("teachers") or course.get("teacher")),
                "room": _names(item.get("rooms") or item.get("room") or course.get("rooms")),
                "group": _names(course.get("class") or course.get("classes") or course.get("group")),
                "topic": item.get("topic") or item.get("content"),
                "period": item.get("lesson_number") or item.get("period"),
                "changed": cancelled or bool(item.get("changed") or item.get("substitution")),
                "cancelled": cancelled,
                "note": item.get("note"),
                "kind": "lesson",
                "url": None,
                "id": item.get("lesson_id"),
            }
        )
    return result


def _fill_order(lessons: list[dict[str, Any]]) -> None:
    """Number lessons without a start time by their order within the day."""
    by_day: dict[str, int] = {}
    for lesson in lessons:
        if lesson.get("start") or lesson.get("period") or lesson.get("kind") == "event":
            continue
        by_day[lesson["date"]] = by_day.get(lesson["date"], 0) + 1
        lesson["period"] = by_day[lesson["date"]]


def build_days(lessons: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    """Group lessons per day (Mon-Fri always present, weekend only with lessons)."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for lesson in lessons:
        grouped.setdefault(lesson["date"], []).append(lesson)
    days = []
    day = start
    while day < end:
        key = day.isoformat()
        if day.weekday() < 5 or grouped.get(key):
            days.append(
                {
                    "date": key,
                    "weekday": WEEKDAYS_CS[day.weekday()],
                    "weekday_short": WEEKDAYS_CS_SHORT[day.weekday()],
                    "lessons": sorted(
                        grouped.get(key, []), key=lambda ls: (ls.get("period") or 99, ls.get("start") or "")
                    ),
                }
            )
        day += timedelta(days=1)
    return days


# ---------------------------------------------------------------- other data

PAGES = {
    "dashboard": "/",
    "inbox": "/overview/updates",
    "evaluations": "/evaluation/listing",
    "assignments": "/assignments/",
    "exams": "/exams/",
    "events": "/timetable/upcoming",
    "attendance": "/attendance/",
    "payments": "/payments/",
}


class EdookitDataCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Inbox, grades, homework, exams, events, attendance, payments."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: EdookitClient,
        store: Store,
        stored: dict,
        child: Child,
    ) -> None:
        minutes = int(_opt(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} data {client.school} {child.key}".strip(),
            update_interval=timedelta(minutes=minutes) if minutes > 0 else None,
        )
        self.client = client
        self._store = store
        self._stored = stored
        self._missing_pages: set[str] = set()
        self.child = child
        self.student: str | None = child.name or (stored.get("student") if child.id is None else None)

    async def _page(self, key: str, errors: dict[str, str]) -> str | None:
        if key in self._missing_pages:
            return None
        try:
            return await self.client.async_get_page(PAGES[key])
        except EdookitAuthError:
            raise
        except EdookitConnectionError as err:
            if "HTTP 404" in str(err) or "HTTP 403" in str(err):
                # Module not enabled at this school; don't ask again until reload.
                self._missing_pages.add(key)
            errors[key] = str(err)
        return None

    async def _async_update_data(self) -> dict[str, Any]:
        return await _as_child(self.client, self.child, self._fetch)

    async def _fetch(self) -> dict[str, Any]:
        entry = self.config_entry
        today = dt_util.now().date()
        errors: dict[str, str] = {}
        data: dict[str, Any] = {"errors": errors}

        try:
            dashboard = await self._page("dashboard", errors)
            student_widgets: dict[str, Any] | None = None
            if dashboard:
                student_widgets = pick_child(parsers.parse_dashboard_children(dashboard, today), self.child)
                if self.child.id is None:
                    self.student = (
                        (student_widgets or {}).get("name")
                        or parsers.display_name(parsers.parse_student_name(dashboard))
                        or self.student
                    )
                    self._stored["student"] = self.student
                data["action_items"] = parsers.parse_action_items(dashboard)
                data["school_year"] = parsers.parse_school_year(dashboard)

            parsed: dict[str, Any] = {}
            for key, parser in (
                ("inbox", parsers.parse_inbox),
                ("evaluations", parsers.parse_evaluations),
                ("assignments", parsers.parse_assignments),
                ("exams", parsers.parse_exams),
                ("events", parsers.parse_upcoming_events),
                ("attendance", parsers.parse_attendance),
            ):
                html = await self._page(key, errors)
                if html is None:
                    continue
                try:
                    parsed[key] = parser(html, today)
                except Exception as err:
                    _LOGGER.debug("Parsing %s failed", key, exc_info=True)
                    errors[key] = f"parse error: {err}"
            html = await self._page("payments", errors)
            if html is not None:
                try:
                    parsed["payments"] = parsers.parse_payments(html)
                except Exception as err:
                    errors["payments"] = f"parse error: {err}"
        except EdookitAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EdookitConnectionError as err:
            raise UpdateFailed(str(err)) from err

        inbox = parsed.get("inbox", [])
        data["inbox"] = inbox
        data["unread"] = sum(1 for i in inbox if i["unread"])
        data["messages"] = [i for i in inbox if i["type"] == "inboxMessage"]

        widget_grades = (student_widgets or {}).get("grades") or []
        subjects = (student_widgets or {}).get("subjects") or {}
        for grade in widget_grades:
            grade["subject_short"] = grade["subject"]
            grade["subject"] = subjects.get(grade["subject"], grade["subject"])
        # The grade list pages mix all children; the dashboard widget is per child.
        grades = widget_grades if self.child.id is not None else parsed.get("evaluations") or widget_grades
        grades = grades or [
            {
                "subject": i["title"].split(" - ")[0],
                "topic": " - ".join(i["title"].split(" - ")[1:]),
                "grade": i["grade"],
                "value": parsers.parse_grade_value(i["grade"]),
                "weight": None,
                "date": i["timestamp"],
                "url": i["url"],
            }
            for i in inbox
            if i["type"] == "evaluation" and i["grade"]
        ]
        grades.sort(key=lambda g: g.get("date") or "", reverse=True)
        data["grades"] = grades
        data["averages"] = parsers.grade_averages(grades)

        now_iso = datetime.combine(today, datetime.min.time()).isoformat()
        assignments = parsed.get("assignments") or [
            {"title": i["title"], "subject": "", "due": None, "description": i["description"], "url": i["url"]}
            for i in inbox
            if i["type"] == "assignment"
        ]
        data["assignments"] = sorted(
            (a for a in assignments if not a.get("done") and (a.get("due") is None or a["due"] >= now_iso)),
            key=lambda a: a.get("due") or "9999",
        )
        data["exams"] = sorted(
            (e for e in parsed.get("exams", []) if e.get("date") is None or e["date"] >= now_iso),
            key=lambda e: e.get("date") or "9999",
        )
        data["events"] = sorted(
            (e for e in parsed.get("events", []) if (e.get("end") or e.get("start") or "9999") >= today.isoformat()),
            key=lambda e: e.get("start") or "9999",
        )
        data["attendance"] = parsed.get("attendance", {"records": [], "stats": {}, "unexcused": 0})
        if student_widgets:
            data["attendance"]["summary"] = student_widgets["absences"]
            data["class_name"] = student_widgets["class_name"]
            data["class_teacher"] = student_widgets["class_teacher"]
        data["payments"] = parsed.get("payments", {"payments": [], "outstanding": 0.0})
        data.setdefault("action_items", [])

        if _opt(entry, CONF_PUBLIC_API, DEFAULT_PUBLIC_API):
            await self._public_data(data, today, errors)

        data["student"] = self.student
        data["updated"] = dt_util.now().isoformat()

        if _opt(entry, CONF_FIRE_EVENTS, DEFAULT_FIRE_EVENTS):
            self._fire_new_items(inbox)
        await _save(self._store, self._stored, self.client)
        return data

    async def _public_data(self, data: dict[str, Any], today: date, errors: dict[str, str]) -> None:
        tz = str(dt_util.get_default_time_zone())
        try:
            text = await self.client.async_public_events_ical(today, today + timedelta(days=60))
            data["public_events"] = [
                {
                    "title": ev.get("summary", ""),
                    "description": ev.get("description", ""),
                    "location": ev.get("location"),
                    "start": ev["start"].isoformat(),
                    "end": ev["end"].isoformat() if ev.get("end") else None,
                    "all_day": not isinstance(ev["start"], datetime),
                }
                for ev in parse_ical(text, tz)
            ]
        except EdookitError as err:
            errors["public_events"] = str(err)
        try:
            data["substitutions"] = await self.client.async_substitutions(today, today + timedelta(days=7))
        except EdookitError as err:
            errors["substitutions"] = str(err)

    def _fire_new_items(self, inbox: list[dict[str, Any]]) -> None:
        # One "seen" ledger per account: an item shown for both children is announced once.
        seen: dict[str, str] = self._stored.setdefault("seen", {})
        seeded: list[str] = self._stored.setdefault("seeded", [""] if seen else [])
        first_run = self.child.key not in seeded
        if first_run:
            seeded.append(self.child.key)
        now = dt_util.now()
        for item in inbox:
            identity = item.get("url") or f"{item['type']}|{item['title']}|{item['time']}"
            if identity in seen:
                continue
            seen[identity] = now.isoformat()
            if first_run:
                continue
            self.hass.bus.async_fire(
                EVENT_NEW_ITEM,
                {
                    "entry_id": self.config_entry.entry_id,
                    "student": parsers.display_name(self.student),
                    "child_id": self.child.id,
                    "type": item["type"],
                    "type_label": ITEM_TYPES.get(item["type"], item["type"]),
                    "title": item["title"],
                    "creator": item["creator"],
                    "time": item["time"],
                    "grade": item["grade"],
                    "description": item["description"],
                    "url": f"{self.client.base_url}{item['url']}"
                    if item.get("url", "") and item["url"].startswith("/")
                    else item.get("url"),
                },
            )
        cutoff = (now - SEEN_RETENTION).isoformat()
        for key in [k for k, v in seen.items() if v < cutoff]:
            seen.pop(key)
