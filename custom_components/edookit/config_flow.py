"""Config flow for Edookit."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TimeSelector,
)
import voluptuous as vol

from .api import EdookitAuthError, EdookitClient, EdookitConnectionError, normalize_school
from .const import (
    CONF_AFTER_LESSON_DELAY,
    CONF_API_PASSWORD,
    CONF_API_STUDENT_ID,
    CONF_API_USERNAME,
    CONF_FIRE_EVENTS,
    CONF_ICAL_URL,
    CONF_LOGIN_METHOD,
    CONF_OFF_SCHOOL_INTERVAL,
    CONF_OIDC_CLIENT_ID,
    CONF_PUBLIC_API,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_SCAN_INTERVAL,
    CONF_SCHOOL,
    CONF_SMART_REFRESH,
    CONF_TIMETABLE_SOURCE,
    CONF_TIMETABLE_TIME,
    CONF_TRAVEL_TIME,
    CONF_WEEKS,
    DEFAULT_AFTER_LESSON_DELAY,
    DEFAULT_FIRE_EVENTS,
    DEFAULT_OFF_SCHOOL_INTERVAL,
    DEFAULT_PUBLIC_API,
    DEFAULT_QUIET_END,
    DEFAULT_QUIET_START,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SMART_REFRESH,
    DEFAULT_TIMETABLE_TIME,
    DEFAULT_TRAVEL_TIME,
    DEFAULT_WEEKS,
    DOMAIN,
    LOGIN_AUTO,
    LOGIN_METHODS,
    SOURCE_AUTO,
    TIMETABLE_SOURCES,
)
from .parsers import parse_student_name

_LOGGER = logging.getLogger(__name__)


async def validate_login(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Log in once; return the school subdomain and the student's name."""
    session = async_create_clientsession(hass, auto_cleanup=False, cookie_jar=aiohttp.CookieJar())
    try:
        client = EdookitClient(
            session,
            data[CONF_SCHOOL],
            data[CONF_USERNAME],
            data[CONF_PASSWORD],
            data.get(CONF_LOGIN_METHOD, LOGIN_AUTO),
            oidc_client_id=data.get(CONF_OIDC_CLIENT_ID),
        )
        await client.async_login()
        name = parse_student_name(await client.async_get_page("/"))
        return {"school": client.school, "student": name, "method": client.used_login_method}
    finally:
        await session.close()


def _user_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_SCHOOL, default=defaults.get(CONF_SCHOOL, "")): TextSelector(),
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): TextSelector(
                TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
            ),
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
            ),
            vol.Required(CONF_LOGIN_METHOD, default=defaults.get(CONF_LOGIN_METHOD, LOGIN_AUTO)): SelectSelector(
                SelectSelectorConfig(
                    options=LOGIN_METHODS, translation_key="login_method", mode=SelectSelectorMode.DROPDOWN
                )
            ),
            vol.Optional(
                CONF_OIDC_CLIENT_ID, description={"suggested_value": defaults.get(CONF_OIDC_CLIENT_ID, "")}
            ): TextSelector(),
        }
    )


class EdookitConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Edookit."""

    VERSION = 1

    async def _try_login(self, data: dict[str, Any], errors: dict[str, str]) -> dict[str, Any] | None:
        try:
            normalize_school(data[CONF_SCHOOL])
        except ValueError:
            errors[CONF_SCHOOL] = "invalid_school"
            return None
        try:
            return await validate_login(self.hass, data)
        except EdookitAuthError as err:
            _LOGGER.info("Edookit login failed: %s", err)
            errors["base"] = "invalid_auth"
        except EdookitConnectionError as err:
            _LOGGER.info("Edookit connection failed: %s", err)
            errors["base"] = "school_not_found" if "school_not_found" in str(err) else "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error during Edookit login")
            errors["base"] = "unknown"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """First step: school + credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            info = await self._try_login(user_input, errors)
            if info:
                await self.async_set_unique_id(f"{info['school']}:{user_input[CONF_USERNAME].lower()}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=info["student"] or f"{info['school']} ({user_input[CONF_USERNAME]})",
                    data={**user_input, CONF_SCHOOL: info["school"]},
                )
        return self.async_show_form(step_id="user", data_schema=_user_schema(user_input or {}), errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Password changed or session refused."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            if await self._try_login(data, errors):
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
            ),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change school, username or login method."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            info = await self._try_login(user_input, errors)
            if info:
                return self.async_update_reload_and_abort(entry, data={**user_input, CONF_SCHOOL: info["school"]})
        return self.async_show_form(
            step_id="reconfigure", data_schema=_user_schema(user_input or entry.data), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Options."""
        return EdookitOptionsFlow()


class EdookitOptionsFlow(OptionsFlow):
    """Update time, intervals and optional data sources."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Single options page."""
        if user_input is not None:
            # Empty optional text fields are dropped by the frontend; keep them cleared.
            for key in (CONF_ICAL_URL, CONF_API_USERNAME, CONF_API_PASSWORD, CONF_API_STUDENT_ID, CONF_OIDC_CLIENT_ID):
                user_input.setdefault(key, "")
            return self.async_create_entry(data=user_input)

        opts = self.config_entry.options

        def text(key: str, kind: TextSelectorType = TextSelectorType.TEXT) -> tuple:
            return (
                vol.Optional(key, description={"suggested_value": opts.get(key, "")}),
                TextSelector(TextSelectorConfig(type=kind)),
            )

        schema: dict[Any, Any] = {
            vol.Required(
                CONF_TIMETABLE_TIME, default=opts.get(CONF_TIMETABLE_TIME, DEFAULT_TIMETABLE_TIME)
            ): TimeSelector(),
            vol.Required(
                CONF_SCAN_INTERVAL, default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ): NumberSelector(
                NumberSelectorConfig(min=0, max=1440, step=5, mode=NumberSelectorMode.BOX, unit_of_measurement="min")
            ),
            vol.Required(CONF_WEEKS, default=opts.get(CONF_WEEKS, DEFAULT_WEEKS)): NumberSelector(
                NumberSelectorConfig(min=1, max=4, step=1, mode=NumberSelectorMode.SLIDER)
            ),
            vol.Required(CONF_TIMETABLE_SOURCE, default=opts.get(CONF_TIMETABLE_SOURCE, SOURCE_AUTO)): SelectSelector(
                SelectSelectorConfig(
                    options=TIMETABLE_SOURCES,
                    translation_key="timetable_source",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_SMART_REFRESH, default=opts.get(CONF_SMART_REFRESH, DEFAULT_SMART_REFRESH)
            ): BooleanSelector(),
            vol.Required(
                CONF_AFTER_LESSON_DELAY, default=opts.get(CONF_AFTER_LESSON_DELAY, DEFAULT_AFTER_LESSON_DELAY)
            ): NumberSelector(
                NumberSelectorConfig(min=0, max=60, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="min")
            ),
            vol.Required(
                CONF_OFF_SCHOOL_INTERVAL, default=opts.get(CONF_OFF_SCHOOL_INTERVAL, DEFAULT_OFF_SCHOOL_INTERVAL)
            ): NumberSelector(
                NumberSelectorConfig(min=15, max=1440, step=15, mode=NumberSelectorMode.BOX, unit_of_measurement="min")
            ),
            vol.Required(CONF_QUIET_START, default=opts.get(CONF_QUIET_START, DEFAULT_QUIET_START)): TimeSelector(),
            vol.Required(CONF_QUIET_END, default=opts.get(CONF_QUIET_END, DEFAULT_QUIET_END)): TimeSelector(),
            vol.Required(CONF_TRAVEL_TIME, default=opts.get(CONF_TRAVEL_TIME, DEFAULT_TRAVEL_TIME)): NumberSelector(
                NumberSelectorConfig(min=0, max=180, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="min")
            ),
            vol.Required(CONF_FIRE_EVENTS, default=opts.get(CONF_FIRE_EVENTS, DEFAULT_FIRE_EVENTS)): BooleanSelector(),
            vol.Required(CONF_PUBLIC_API, default=opts.get(CONF_PUBLIC_API, DEFAULT_PUBLIC_API)): BooleanSelector(),
        }
        for key, kind in (
            (CONF_ICAL_URL, TextSelectorType.URL),
            (CONF_API_USERNAME, TextSelectorType.TEXT),
            (CONF_API_PASSWORD, TextSelectorType.PASSWORD),
            (CONF_API_STUDENT_ID, TextSelectorType.TEXT),
            (CONF_OIDC_CLIENT_ID, TextSelectorType.TEXT),
        ):
            marker, selector = text(key, kind)
            schema[marker] = selector
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))
