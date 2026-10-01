"""Tests for the Edookit HTTP client (login flows) using a fake HTTP session."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import aiohttp
import pytest

from custom_components.edookit.api import (
    DEFAULT_OIDC_BASE,
    EdookitAuthError,
    EdookitClient,
    normalize_school,
)

from .conftest import load

BASE = "https://skola.edookit.net"


class FakeResponse:
    def __init__(self, status: int, url: str, headers: dict[str, str], body: str) -> None:
        self.status = status
        self.url = url
        self.headers = headers
        self._body = body

    async def text(self, errors: str = "strict") -> str:
        return self._body

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


class FakeSession:
    """Routes requests to a handler; follows redirects when asked to."""

    def __init__(self, handler: Callable[..., tuple[int, dict[str, str], str]]) -> None:
        self.handler = handler
        self.cookie_jar = aiohttp.CookieJar()
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, *, allow_redirects: bool = True, **kwargs: Any) -> FakeResponse:
        for _ in range(10):
            self.calls.append((method, url, kwargs))
            status, headers, body = self.handler(method, url, **kwargs)
            if allow_redirects and status in (301, 302, 303) and "Location" in headers:
                url = urljoin(url, headers["Location"])
                method = "GET"
                kwargs = {}
                continue
            return FakeResponse(status, url, headers, body)
        raise AssertionError("redirect loop")


def test_normalize_school():
    assert normalize_school("zs-abc") == "zs-abc"
    assert normalize_school("https://ZS-abc.edookit.net/user/login") == "zs-abc"
    assert normalize_school("zs-abc-login.edookit.net") == "zs-abc"
    with pytest.raises(ValueError):
        normalize_school("not a school!")


def make_plus4u_server(
    password: str = "secret", codes: tuple[str, str] = ("1234", "abcdefgh9"), codes_endpoint: bool = True
) -> tuple[FakeSession, dict[str, Any]]:
    state: dict[str, Any] = {"logged_in": False, "auth_body": None, "codes_posts": [], "grant": None}

    def handler(method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, str], str]:
        parsed = urlparse(url)
        if url.startswith(BASE):
            if parsed.path == "/user/login":
                if state["logged_in"]:
                    return 302, {"Location": "/"}, ""
                return 200, {}, load("login.html")
            if parsed.path == "/user/oidc-login-callback":
                assert parse_qs(parsed.query)["code"] == ["abc"]
                state["logged_in"] = True
                return 302, {"Location": "/"}, ""
            if parsed.path == "/":
                if not state["logged_in"]:
                    return 302, {"Location": "/user/login"}, ""
                return 200, {}, load("dashboard.html")
            return 404, {}, "not found"
        if url.startswith(f"{DEFAULT_OIDC_BASE}/oidc/auth"):
            qs = parse_qs(parsed.query)
            if "resume" in qs:
                return 302, {"Location": f"{BASE}/user/oidc-login-callback?code=abc&state=x"}, ""
            state["auth_qs"] = qs
            return 302, {"Location": f"{DEFAULT_OIDC_BASE}/login?state=ST1&clientId={qs['client_id'][0]}"}, ""
        if url == f"{DEFAULT_OIDC_BASE}/oidc/grantToken":
            state["grant"] = kwargs["data"]
            if (kwargs["data"]["accessCode1"], kwargs["data"]["accessCode2"]) != codes:
                return 401, {}, json.dumps({"uuAppErrorMap": {"uu-oidc-main/grantToken/identityNotAuthenticated": {}}})
            return 200, {}, json.dumps({"id_token": "x.y.z"})
        if url == f"{DEFAULT_OIDC_BASE}/authAccessCodes/authenticate":
            if not codes_endpoint:
                return 404, {}, ""
            state["codes_posts"].append(kwargs["json"])
            body = kwargs["json"]
            if (body.get("accessCode1"), body.get("accessCode2")) != codes:
                return 401, {}, "{}"
            return 302, {"Location": f"{DEFAULT_OIDC_BASE}/oidc/auth?resume=1"}, ""
        if url == f"{DEFAULT_OIDC_BASE}/authPassword/authenticate":
            state["auth_body"] = kwargs["json"]
            if kwargs["json"]["password"] not in (password, codes[1]):
                return 401, {}, json.dumps({"uuAppErrorMap": {"invalidCredentials": {}}})
            return 302, {"Location": f"{DEFAULT_OIDC_BASE}/oidc/auth?resume=1"}, ""
        return 404, {}, ""

    return FakeSession(handler), state


async def test_plus4u_login_flow():
    session, state = make_plus4u_server()
    client = EdookitClient(session, "skola", "rodic@example.com", "secret")
    await client.async_login()
    assert client.logged_in
    assert client.used_login_method == "plus4u"
    # Client id and redirect were discovered from the (JSON-escaped) login page.
    assert state["auth_qs"]["client_id"] == ["93b2c166a7aa436baa0278b8f5c736db"]
    assert state["auth_qs"]["redirect_uri"] == [f"{BASE}/user/oidc-login-callback"]
    assert state["auth_body"] == {
        "clientId": "93b2c166a7aa436baa0278b8f5c736db",
        "state": "ST1",
        "rememberMe": True,
        "realmCode": "uuIdentityPasswordAuthNRealm",
        "username": "rodic@example.com",
        "password": "secret",
    }
    html = await client.async_get_page("/")
    assert "Jan" in html


async def test_plus4u_wrong_password():
    session, _ = make_plus4u_server()
    client = EdookitClient(session, "skola", "rodic@example.com", "wrong", login_method="plus4u")
    with pytest.raises(EdookitAuthError):
        await client.async_login()
    assert not client.logged_in


async def test_configured_client_id_wins():
    session, state = make_plus4u_server()
    client = EdookitClient(session, "skola", "a@b.cz", "secret", oidc_client_id="f" * 32)
    await client.async_login()
    assert state["auth_qs"]["client_id"] == ["f" * 32]


async def test_classic_form_login():
    state = {"logged_in": False, "posted": None}
    form = """<html><head><title>Přihlašovací stránka</title></head><body>
      <form id="frm-loginForm-form" action="/user/login" method="post">
        <input type="text" name="username"><input type="password" name="password">
        <input type="hidden" name="_token_" value="tok123">
        <input type="submit" name="send" value="Přihlásit">
      </form></body></html>"""

    def handler(method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, str], str]:
        path = urlparse(url).path
        if path == "/user/login" and method == "POST":
            state["posted"] = kwargs["data"]
            if kwargs["data"]["password"] == "pw":
                state["logged_in"] = True
                return 302, {"Location": "/"}, ""
            return 200, {}, form
        if path == "/user/login":
            return 200, {}, form
        if path == "/":
            return (200, {}, load("dashboard.html")) if state["logged_in"] else (302, {"Location": "/user/login"}, "")
        return 404, {}, ""

    client = EdookitClient(FakeSession(handler), "skola", "jan", "pw")
    await client.async_login()
    assert client.used_login_method == "edookit"
    assert state["posted"]["username"] == "jan"
    assert state["posted"]["_token_"] == "tok123"
    assert state["posted"]["_do"] == "loginForm-form-submit"


async def test_session_renewal_on_expired_page():
    session, state = make_plus4u_server()
    client = EdookitClient(session, "skola", "a@b.cz", "secret")
    await client.async_login()
    state["logged_in"] = False  # server dropped the session
    html = await client.async_get_page("/")
    assert "requires-action-container" in html
    assert state["logged_in"]


async def test_access_codes_login():
    session, state = make_plus4u_server()
    # No "@" in the user name -> automatic mode uses the +4U Access codes.
    client = EdookitClient(session, "skola", "1234", "abcdefgh9")
    await client.async_login()
    assert client.logged_in
    assert client.used_login_method == "plus4u_codes"
    assert state["grant"] == {
        "grant_type": "password",
        "accessCode1": "1234",
        "accessCode2": "abcdefgh9",
        "scope": "openid",
    }
    assert state["codes_posts"] == [
        {
            "clientId": "93b2c166a7aa436baa0278b8f5c736db",
            "state": "ST1",
            "rememberMe": True,
            "accessCode1": "1234",
            "accessCode2": "abcdefgh9",
        }
    ]
    assert state["auth_body"] is None  # password endpoint not used


async def test_access_codes_wrong():
    session, state = make_plus4u_server()
    client = EdookitClient(session, "skola", "1234", "wrong", login_method="plus4u_codes")
    with pytest.raises(EdookitAuthError, match="access codes"):
        await client.async_login()
    # Rejected by the pre-check; no browser login attempts were made.
    assert state["codes_posts"] == []


async def test_access_codes_fall_back_to_password_endpoint():
    session, state = make_plus4u_server(codes_endpoint=False)
    client = EdookitClient(session, "skola", "1234", "abcdefgh9", login_method="plus4u_codes")
    await client.async_login()
    assert client.logged_in
    assert state["auth_body"]["username"] == "1234"
