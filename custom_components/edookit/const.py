"""Constants for the Edookit integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "edookit"
VERSION: Final = "0.1.0"

# Config entry data
CONF_SCHOOL: Final = "school"
CONF_LOGIN_METHOD: Final = "login_method"

LOGIN_AUTO: Final = "auto"
LOGIN_PLUS4U: Final = "plus4u"
LOGIN_EDOOKIT: Final = "edookit"
LOGIN_METHODS: Final = [LOGIN_AUTO, LOGIN_PLUS4U, LOGIN_EDOOKIT]

# Options
CONF_TIMETABLE_TIME: Final = "timetable_update_time"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_WEEKS: Final = "timetable_weeks"
CONF_TIMETABLE_SOURCE: Final = "timetable_source"
CONF_ICAL_URL: Final = "ical_url"
CONF_API_USERNAME: Final = "api_username"
CONF_API_PASSWORD: Final = "api_password"
CONF_API_STUDENT_ID: Final = "api_student_id"
CONF_PUBLIC_API: Final = "public_api"
CONF_FIRE_EVENTS: Final = "fire_events"
CONF_OIDC_CLIENT_ID: Final = "oidc_client_id"

SOURCE_AUTO: Final = "auto"
SOURCE_PORTAL: Final = "portal"
SOURCE_ICAL: Final = "ical"
SOURCE_API: Final = "api"
TIMETABLE_SOURCES: Final = [SOURCE_AUTO, SOURCE_PORTAL, SOURCE_ICAL, SOURCE_API]

DEFAULT_TIMETABLE_TIME: Final = "05:00:00"
DEFAULT_SCAN_INTERVAL: Final = 60  # minutes, 0 = only once a day
DEFAULT_WEEKS: Final = 2
DEFAULT_PUBLIC_API: Final = False
DEFAULT_FIRE_EVENTS: Final = True

# Events fired on the HA bus
EVENT_NEW_ITEM: Final = "edookit_new_item"

# Services
SERVICE_REFRESH: Final = "refresh"
SERVICE_DUMP_PAGES: Final = "dump_pages"
SERVICE_GET_TIMETABLE: Final = "get_timetable"

# Frontend
CARD_FILENAME: Final = "edookit-timetable-card.js"
CARD_URL: Final = f"/{DOMAIN}/{CARD_FILENAME}"

STORAGE_VERSION: Final = 1

# Inbox item types (CSS class used by the portal inbox -> human label)
ITEM_TYPES: Final = {
    "assignment": "Úkol",
    "actionRequired": "Vyžaduje akci",
    "inboxMessage": "Zpráva",
    "message": "Zpráva",
    "exam": "Písemka",
    "poll": "Anketa",
    "event": "Událost",
    "evaluation": "Hodnocení",
    "material": "Materiál",
}

WEEKDAYS_CS: Final = ["Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek", "Sobota", "Neděle"]
WEEKDAYS_CS_SHORT: Final = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]
