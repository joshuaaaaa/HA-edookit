"""Async client for the Edookit portal (``https://<school>.edookit.net``).

Authentication
--------------
Parents and students sign in through **Plus4U** (OpenID Connect,
``uuidentity.plus4u.net``). We replay the browser flow with plain HTTP:

1. discover the school's OIDC client from its ``/user/login`` page,
2. start ``/oidc/auth`` to get a login ``state``,
3. POST e-mail + password to ``/authPassword/authenticate``,
4. follow the redirects back to ``/user/oidc-login-callback``, which sets the
   portal session cookies.

Older installations still offer Edookit's own login form
(``loginForm``), which is supported as well.

Some schools also issue REST API credentials (HTTP Basic,
``/api/...``); those endpoints are optional extras here.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import date
import html as html_lib
import json
import logging
import re
import secrets
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup
from yarl import URL

from .parsers import is_login_page

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=45)
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"

PLUS4U_HOST = "uuidentity.plus4u.net"
# Plus4U's main OIDC server and the client used by at least one school. Both
# are only fallbacks; the real values are discovered from the login page.
DEFAULT_OIDC_BASE = "https://uuidentity.plus4u.net/uu-oidc-maing02/bb977a99f4cc4c37a2afce3fd599d0a7"
DEFAULT_OIDC_CLIENT_ID = "0fa24fa43e794de89003790253a93cb6"
PASSWORD_REALM = "uuIdentityPasswordAuthNRealm"

_AUTH_URL_RE = re.compile(r"https://uuidentity\.plus4u\.net/[^\s\"'<>\\]+?/oidc/auth[^\s\"'<>\\]*")
_OIDC_BASE_RE = re.compile(r"https://uuidentity\.plus4u\.net/uu-oidc-maing02/[0-9a-f]{32}")
_CLIENT_ID_RE = re.compile(r"client_?id[\"']?\s*[:=]\s*[\"']?([0-9a-f]{32})", re.I)
_REDIRECT_RE = re.compile(r"redirect_?uri[\"']?\s*[:=]\s*[\"'](https?://[^\"']+)", re.I)
_LOGIN_LINK_RE = re.compile(
    r"""(?:href|action|data-href|data-url)\s*=\s*["']([^"']*(?:oidc|plus4u|sso)[^"']*)["']""", re.I
)
_GUESSED_LOGIN_LINKS = ("/user/oidc-login", "/user/login?do=plus4ULogin", "/user/login?do=oidcLogin")


class EdookitError(Exception):
    """Base error."""


class EdookitConnectionError(EdookitError):
    """Network / server problem."""


class EdookitAuthError(EdookitError):
    """Login failed or session cannot be renewed."""


def normalize_school(value: str) -> str:
    """Accept ``zs-abc``, ``zs-abc.edookit.net`` or a full URL; return the subdomain."""
    value = (value or "").strip().lower()
    if "://" in value:
        value = urlparse(value).hostname or ""
    value = value.split("/")[0]
    if value.endswith(".edookit.net"):
        value = value[: -len(".edookit.net")]
    if value.endswith("-login"):
        value = value[: -len("-login")]
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]*", value):
        raise ValueError("invalid_school")
    return value


@dataclass
class Response:
    """Simplified HTTP response."""

    status: int
    url: str
    text: str
    headers: dict[str, str]


@dataclass
class OidcConfig:
    """Plus4U OIDC parameters for one school."""

    base: str
    client_id: str
    redirect_uri: str
    scope: str = "openid"


class EdookitClient:
    """Edookit portal client bound to one account."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        school: str,
        username: str,
        password: str,
        login_method: str = "auto",
        oidc_client_id: str | None = None,
        api_username: str | None = None,
        api_password: str | None = None,
    ) -> None:
        self._session = session
        self.school = normalize_school(school)
        self.base_url = f"https://{self.school}.edookit.net"
        self.legacy_url = f"https://{self.school}-login.edookit.net"
        self._username = username
        self._password = password
        self._login_method = login_method
        self._oidc_client_id = oidc_client_id or None
        self._api_auth = aiohttp.BasicAuth(api_username, api_password or "") if api_username else None
        self._login_lock = asyncio.Lock()
        self.logged_in = False
        self.used_login_method: str | None = None

    # ------------------------------------------------------------------ http

    async def _request(
        self,
        method: str,
        url: str,
        *,
        allow_redirects: bool = True,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Response:
        hdrs = {"User-Agent": USER_AGENT, "Accept-Language": "cs,en;q=0.8"}
        if headers:
            hdrs.update(headers)
        try:
            async with self._session.request(
                method,
                url,
                allow_redirects=allow_redirects,
                headers=hdrs,
                timeout=TIMEOUT,
                **kwargs,
            ) as resp:
                text = await resp.text(errors="replace")
                return Response(resp.status, str(resp.url), text, dict(resp.headers))
        except (TimeoutError, aiohttp.ClientError) as err:
            raise EdookitConnectionError(f"{method} {url}: {err}") from err

    async def _follow(self, url: str, max_hops: int = 15) -> Response:
        """Follow redirects manually so every hop's cookies are stored."""
        resp = await self._request("GET", url, allow_redirects=False)
        for _ in range(max_hops):
            location = resp.headers.get("Location")
            if resp.status not in (301, 302, 303, 307, 308) or not location:
                return resp
            url = urljoin(resp.url, location)
            resp = await self._request("GET", url, allow_redirects=False)
        return resp

    # ----------------------------------------------------------------- login

    async def async_login(self) -> None:
        """Log in using the configured method (``auto`` tries both)."""
        async with self._login_lock:
            self.logged_in = False
            method = self._login_method
            login_page = await self._request("GET", f"{self.base_url}/user/login")
            if login_page.status >= 500:
                raise EdookitConnectionError(f"Login page returned HTTP {login_page.status}")
            if login_page.status == 404 and "edookit" not in login_page.text.lower():
                raise EdookitConnectionError("school_not_found")
            if (
                not is_login_page(login_page.text, login_page.url)
                and urlparse(login_page.url).hostname == urlparse(self.base_url).hostname
            ):
                # Still have a valid session from stored cookies.
                self.logged_in = True
                return

            has_form = self._find_login_form(login_page.text) is not None
            order: list[str]
            if method == "plus4u":
                order = ["plus4u"]
            elif method == "edookit":
                order = ["edookit"]
            else:
                order = ["edookit", "plus4u"] if has_form else ["plus4u", "edookit"]

            last_err: Exception | None = None
            for attempt in order:
                try:
                    if attempt == "plus4u":
                        await self._login_plus4u(login_page)
                    else:
                        await self._login_form(login_page)
                    if await self._check_session():
                        self.logged_in = True
                        self.used_login_method = attempt
                        _LOGGER.debug("Edookit login via %s succeeded", attempt)
                        return
                    last_err = EdookitAuthError(f"{attempt}: still on the login page after login")
                except EdookitAuthError as err:
                    _LOGGER.debug("Edookit login via %s failed: %s", attempt, err)
                    last_err = err
            raise last_err or EdookitAuthError("login failed")

    async def _check_session(self) -> bool:
        resp = await self._request("GET", f"{self.base_url}/")
        return resp.status < 400 and not is_login_page(resp.text, resp.url)

    # Plus4U ---------------------------------------------------------------

    async def _discover_oidc(self, login_page: Response) -> OidcConfig:
        text = html_lib.unescape(login_page.text).replace("\\/", "/")

        def from_auth_url(url: str) -> OidcConfig | None:
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            client_id = self._oidc_client_id or (qs.get("client_id") or [None])[0]
            if not client_id:
                return None
            base = url.split("/oidc/auth")[0]
            redirect = (qs.get("redirect_uri") or [f"{self.base_url}/user/oidc-login-callback"])[0]
            scope = (qs.get("scope") or ["openid"])[0]
            return OidcConfig(base, client_id, redirect, scope)

        # 1) The login page already redirected to Plus4U, or embeds the auth URL.
        for candidate in [login_page.url, *_AUTH_URL_RE.findall(text)]:
            if "/oidc/auth" in candidate:
                cfg = from_auth_url(candidate)
                if cfg:
                    return cfg

        # 2) A login button/link on the portal that redirects to Plus4U.
        links = [m for m in _LOGIN_LINK_RE.findall(text) if PLUS4U_HOST not in m]
        for link in [*links, *_GUESSED_LOGIN_LINKS]:
            if link.startswith(("javascript:", "#")):
                continue
            try:
                resp = await self._request("GET", urljoin(self.base_url, link), allow_redirects=False)
            except EdookitConnectionError:
                continue
            location = resp.headers.get("Location", "")
            if "/oidc/auth" in location:
                cfg = from_auth_url(urljoin(resp.url, location))
                if cfg:
                    return cfg

        # 3) Values embedded in inline JS configuration.
        base_match = _OIDC_BASE_RE.search(text)
        client_match = _CLIENT_ID_RE.search(text)
        redirect_match = _REDIRECT_RE.search(text)
        client_id = self._oidc_client_id or (client_match.group(1) if client_match else None)
        if not client_id:
            _LOGGER.warning(
                "Could not discover the Plus4U OIDC client of %s; falling back to a "
                "default client id. If login fails, set 'OIDC client id' in the "
                "integration options",
                self.base_url,
            )
            client_id = DEFAULT_OIDC_CLIENT_ID
        return OidcConfig(
            base=base_match.group(0) if base_match else DEFAULT_OIDC_BASE,
            client_id=client_id,
            redirect_uri=redirect_match.group(1)
            if redirect_match and self.school in redirect_match.group(1)
            else f"{self.base_url}/user/oidc-login-callback",
        )

    async def _login_plus4u(self, login_page: Response) -> None:
        cfg = await self._discover_oidc(login_page)
        _LOGGER.debug("Plus4U OIDC: base=%s client=%s redirect=%s", cfg.base, cfg.client_id, cfg.redirect_uri)
        params = {
            "response_type": "code",
            "client_id": cfg.client_id,
            "redirect_uri": cfg.redirect_uri,
            "scope": cfg.scope,
            "state": secrets.token_urlsafe(16),
        }
        auth_url = f"{cfg.base}/oidc/auth?{urlencode(params)}"
        resp = await self._request("GET", auth_url, allow_redirects=False)
        location = urljoin(auth_url, resp.headers.get("Location", ""))

        if "code=" in location:
            # Plus4U session cookies are still valid; finish directly.
            await self._follow(location)
            return
        if "/login" not in location:
            raise EdookitAuthError(
                f"Plus4U did not redirect to its login page (HTTP {resp.status}); the OIDC client id is probably wrong"
            )
        qs = parse_qs(urlparse(location).query)
        state = (qs.get("state") or [None])[0]
        client_id = (qs.get("clientId") or qs.get("client_id") or [cfg.client_id])[0]
        if not state:
            raise EdookitAuthError("Plus4U login redirect has no state parameter")

        body = {
            "clientId": client_id,
            "state": state,
            "rememberMe": True,
            "realmCode": PASSWORD_REALM,
            "username": self._username,
            "password": self._password,
        }
        resp = await self._request(
            "POST",
            f"{cfg.base}/authPassword/authenticate",
            json=body,
            allow_redirects=False,
        )
        location = resp.headers.get("Location")
        if not location and resp.text.strip().startswith("{"):
            try:
                data = json.loads(resp.text)
                location = data.get("redirectUri") or data.get("location") or data.get("uri")
            except ValueError:
                pass
        if resp.status in (400, 401, 403) or not location:
            hint = ""
            if "captcha" in resp.text.lower():
                hint = " (Plus4U requires a reCAPTCHA; log in once in a browser and retry later)"
            raise EdookitAuthError(f"Plus4U rejected the credentials (HTTP {resp.status}){hint}")
        await self._follow(urljoin(resp.url, location))

    # Classic Edookit form ------------------------------------------------

    @staticmethod
    def _find_login_form(html: str) -> Any:
        soup = BeautifulSoup(html, "html.parser")
        for form in soup.find_all("form"):
            if form.find("input", attrs={"type": "password"}):
                return form
        return None

    async def _login_form(self, login_page: Response) -> None:
        form = self._find_login_form(login_page.text)
        if form is None:
            raise EdookitAuthError("This school has no Edookit login form (use Plus4U)")
        data: dict[str, str] = {}
        user_field = pass_field = None
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            itype = (inp.get("type") or "text").lower()
            if itype == "password":
                pass_field = name
            elif itype in ("text", "email") and user_field is None:
                user_field = name
            elif itype in ("hidden",):
                data[name] = inp.get("value", "")
            elif itype == "checkbox" and "remember" in name.lower():
                data[name] = inp.get("value") or "1"
            elif itype == "submit" and name not in data:
                data[name] = inp.get("value", "")
        data[user_field or "username"] = self._username
        data[pass_field or "password"] = self._password
        data.setdefault("_do", "loginForm-form-submit")

        login_url = f"{self.base_url}/user/login"
        # The form asks the server whether a captcha is needed for this user.
        with contextlib.suppress(EdookitConnectionError):
            await self._request(
                "GET",
                f"{login_url}?do=loginForm-checkCaptcha&loginForm-username={quote(self._username)}",
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        action = urljoin(login_page.url, form.get("action") or login_url)
        resp = await self._request("POST", action, data=data, headers={"Referer": login_page.url})
        if is_login_page(resp.text, resp.url):
            raise EdookitAuthError("Edookit login form rejected the credentials")

    # ----------------------------------------------------------- portal pages

    async def async_get_page(self, path: str) -> str:
        """GET a portal page, re-authenticating once if the session expired."""
        url = urljoin(self.base_url, path)
        if not self.logged_in:
            await self.async_login()
        resp = await self._request("GET", url)
        if is_login_page(resp.text, resp.url):
            _LOGGER.debug("Edookit session expired, logging in again")
            await self.async_login()
            resp = await self._request("GET", url)
            if is_login_page(resp.text, resp.url):
                raise EdookitAuthError("Still redirected to login after re-authentication")
        if resp.status >= 400:
            raise EdookitConnectionError(f"GET {path}: HTTP {resp.status}")
        return resp.text

    def _csrf(self) -> str:
        cookies = self._session.cookie_jar.filter_cookies(URL(self.base_url))
        morsel = cookies.get("uu.app.csrf")
        return morsel.value if morsel else ""

    async def async_ajax(self, path: str) -> str:
        """Send a Nette AJAX signal (used for switching timetable weeks)."""
        if not self.logged_in:
            await self.async_login()
        resp = await self._request(
            "GET",
            urljoin(self.base_url, path),
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "X-CSRF-Token": self._csrf(),
                "Accept": "application/json, text/javascript, */*",
                "Referer": f"{self.base_url}/timetable/",
            },
        )
        return resp.text

    async def async_get_timetable_pages(self, weeks: int) -> list[str]:
        """Return the timetable page for the current week and ``weeks - 1`` following ones."""
        pages = [await self.async_get_page("/timetable/")]
        if weeks <= 1:
            return pages
        try:
            for week in range(1, weeks):
                await self.async_ajax(f"/timetable/?familyTimetable-value={7 * week}&do=familyTimetable-changeFilter")
                pages.append(await self.async_get_page("/timetable/"))
        except EdookitConnectionError as err:
            _LOGGER.debug("Could not load following timetable weeks: %s", err)
        finally:
            with contextlib.suppress(EdookitError):
                await self.async_ajax("/timetable/?familyTimetable-value=0&do=familyTimetable-changeFilter")
        return pages

    # -------------------------------------------------------------- REST API

    @property
    def has_api(self) -> bool:
        """True when school-issued REST API credentials are configured."""
        return self._api_auth is not None

    async def async_api(
        self, path: str, params: dict[str, Any] | None = None, *, legacy: bool = False, auth: bool = True
    ) -> Any:
        """Call the Edookit REST API (JSON)."""
        base = self.legacy_url if legacy else self.base_url
        query = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        kwargs: dict[str, Any] = {"params": query}
        if auth and self._api_auth:
            kwargs["auth"] = self._api_auth
        resp = await self._request("GET", f"{base}{path}", headers={"Accept": "application/json"}, **kwargs)
        if resp.status in (401, 403):
            raise EdookitAuthError(f"API {path}: HTTP {resp.status}")
        if resp.status >= 400:
            raise EdookitConnectionError(f"API {path}: HTTP {resp.status}")
        try:
            return json.loads(resp.text)
        except ValueError as err:
            raise EdookitError(f"API {path}: not JSON") from err

    async def async_api_lessons(self, day: date, student_id: str | int) -> Any:
        """``/api/lesson/v2/list-lessons`` for one student and day."""
        return await self.async_api(
            "/api/lesson/v2/list-lessons",
            {"date": day.isoformat(), "student_person_id": student_id},
        )

    async def async_public_events_ical(self, start: date, end: date) -> str:
        """Public school events as iCalendar (no authentication needed)."""
        resp = await self._request(
            "GET",
            f"{self.legacy_url}/api/public/v1/events",
            params={
                "dateFrom": start.isoformat(),
                "dateTo": end.isoformat(),
                "from": start.isoformat(),
                "to": end.isoformat(),
                "ical": "1",
            },
        )
        if resp.status >= 400:
            raise EdookitConnectionError(f"public events: HTTP {resp.status}")
        return resp.text

    async def async_substitutions(self, start: date, end: date) -> Any:
        """Timetable changes (substitutions) published for the school website."""
        return await self.async_api(
            "/api/scheduler/v1/change",
            {
                "dateFrom": start.isoformat(),
                "dateTo": end.isoformat(),
                "from": start.isoformat(),
                "to": end.isoformat(),
                "teacherNameFormat": "name",
            },
            legacy=True,
            auth=self.has_api,
        )

    async def async_get_url(self, url: str) -> str:
        """GET an absolute URL (e.g. an iCal feed) with the portal session."""
        resp = await self._request("GET", url)
        if resp.status >= 400:
            raise EdookitConnectionError(f"GET {url}: HTTP {resp.status}")
        return resp.text

    # --------------------------------------------------------------- cookies

    def export_cookies(self) -> list[dict[str, str]]:
        """Serialize the cookie jar (to keep the session across restarts)."""
        result = []
        for morsel in self._session.cookie_jar:
            domain = morsel["domain"] or ""
            if not domain.endswith(("edookit.net", "plus4u.net")):
                continue
            result.append({"name": morsel.key, "value": morsel.value, "domain": domain, "path": morsel["path"] or "/"})
        return result

    def import_cookies(self, cookies: list[dict[str, str]] | None) -> None:
        """Restore cookies saved by :meth:`export_cookies`."""
        for cookie in cookies or []:
            domain = cookie.get("domain", "").lstrip(".")
            if not domain:
                continue
            self._session.cookie_jar.update_cookies(
                {cookie["name"]: cookie["value"]}, response_url=URL(f"https://{domain}/")
            )
