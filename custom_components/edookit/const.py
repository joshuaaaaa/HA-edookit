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
LOGIN_PLUS4U_CODES: Final = "plus4u_codes"
LOGIN_EDOOKIT: Final = "edookit"
LOGIN_METHODS: Final = [LOGIN_AUTO, LOGIN_PLUS4U, LOGIN_PLUS4U_CODES, LOGIN_EDOOKIT]

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
CONF_SMART_REFRESH: Final = "smart_refresh"
CONF_AFTER_LESSON_DELAY: Final = "after_lesson_delay"
CONF_OFF_SCHOOL_INTERVAL: Final = "off_school_interval"
CONF_QUIET_START: Final = "quiet_start"
CONF_QUIET_END: Final = "quiet_end"
CONF_TRAVEL_TIME: Final = "travel_time"

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
DEFAULT_SMART_REFRESH: Final = True
DEFAULT_AFTER_LESSON_DELAY: Final = 5  # minutes after a lesson ends
DEFAULT_OFF_SCHOOL_INTERVAL: Final = 180  # minutes outside school hours
DEFAULT_QUIET_START: Final = "22:00:00"
DEFAULT_QUIET_END: Final = "06:00:00"
DEFAULT_TRAVEL_TIME: Final = 15  # minutes from school to home

# Events fired on the HA bus
EVENT_NEW_ITEM: Final = "edookit_new_item"

# Services
SERVICE_REFRESH: Final = "refresh"
SERVICE_DUMP_PAGES: Final = "dump_pages"
SERVICE_GET_TIMETABLE: Final = "get_timetable"


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
