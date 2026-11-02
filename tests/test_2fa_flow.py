"""Regression tests for EVC-net 2FA login flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.evcnet.api import (
    AuthenticationError,
    EvcNetApiClient,
    InvalidOtp,
    TwoFactorRequired,
)
from custom_components.evcnet.config_flow import EvcNetConfigFlow


class DummyResponse:
    """Simple async response stub used in auth tests."""

    def __init__(
        self, *, status: int, headers: dict[str, str] | None = None, body: str = ""
    ) -> None:
        """Initialize the stub response."""
        self.status = status
        self.headers = headers or {}
        self._body = body

    async def __aenter__(self):
        """Return the response object when used as a context manager."""
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """Suppress exceptions from the fake async context manager."""
        return False

    async def text(self) -> str:
        """Return the response body text."""
        return self._body


@pytest.mark.asyncio
async def test_authenticate_raises_two_factor_when_server_requests_email_code() -> None:
    """The login flow should pause on a /2fa redirect and keep the CSRF token."""
    session = MagicMock()
    session.post = MagicMock(
        side_effect=[
            DummyResponse(status=302, headers={"Location": "/2fa"}),
            DummyResponse(
                status=200,
                body='<input type="hidden" name="_token" value="token-123">',
            ),
        ]
    )
    session.get = MagicMock(
        return_value=DummyResponse(
            status=200,
            body='<input type="hidden" name="_token" value="token-123">',
        )
    )

    client = EvcNetApiClient(
        "https://example.com", "user@example.com", "secret", session
    )

    with pytest.raises(TwoFactorRequired):
        await client.authenticate()

    assert object.__getattribute__(client, "_token") == "token-123"


@pytest.mark.asyncio
async def test_authenticate_detects_otp_in_html_response() -> None:
    """A challenge page without a redirect must still start the OTP flow."""
    session = MagicMock()
    session.post = MagicMock(
        return_value=DummyResponse(
            status=200,
            body=(
                '<html><body><form action="/2fa_check">'
                '<input type="hidden" name="_token" value="token-123">'
                "</form></body></html>"
            ),
        )
    )
    session.get = MagicMock(
        return_value=DummyResponse(
            status=200,
            body='<input type="hidden" name="_token" value="token-123">',
        )
    )

    client = EvcNetApiClient(
        "https://example.com", "user@example.com", "secret", session
    )

    with pytest.raises(TwoFactorRequired):
        await client.authenticate()

    assert object.__getattribute__(client, "_token") == "token-123"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    ["https://capbornes.evc-net.com", "https://50five-sde.evc-net.com"],
)
@pytest.mark.parametrize("login_value", ["Log in", "Se connecter"])
async def test_browser_emulation_uses_portal_login_button_value(
    base_url: str, login_value: str
) -> None:
    """The fallback must submit the localized value expected by the portal."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.get = MagicMock(
        return_value=DummyResponse(
            status=200,
            body=(f'<input type="submit" name="Login" value="{login_value}">'),
        )
    )
    session.post = MagicMock(
        return_value=DummyResponse(status=302, headers={"Location": "/Overview"})
    )
    session.cookie_jar.filter_cookies.return_value = {
        "PHPSESSID": MagicMock(value="sess-123"),
        "SERVERID": MagicMock(value="server-456"),
    }
    api_session = MagicMock()
    api_session.get = MagicMock(return_value=DummyResponse(status=200))

    client = EvcNetApiClient(
        base_url,
        "user@example.com",
        "secret",
        api_session,
    )
    setattr(client, "_standard_login", AsyncMock(return_value=False))

    with patch(
        "custom_components.evcnet.api.aiohttp.ClientSession", return_value=session
    ):
        assert await client.authenticate() is True
    assert client.is_authenticated is True

    form_fields = session.post.call_args.kwargs["data"]._fields
    login_field = next(
        field for field in form_fields if field[0].get("name") == "Login"
    )
    assert login_field[2] == login_value


@pytest.mark.asyncio
async def test_authenticate_detects_otp_after_dashboard_redirect() -> None:
    """A dashboard redirect can still be the moment the server asks for OTP."""
    session = MagicMock()
    session.post = MagicMock(
        return_value=DummyResponse(
            status=302,
            headers={"Location": "/Overview"},
        )
    )
    session.cookie_jar.filter_cookies.return_value = {
        "PHPSESSID": MagicMock(key="PHPSESSID", value="sess-123"),
        "SERVERID": MagicMock(key="SERVERID", value="server-456"),
    }
    session.get = MagicMock(
        return_value=DummyResponse(
            status=302,
            headers={"Location": "/2fa"},
            body='<input type="hidden" name="_token" value="token-123">',
        )
    )

    client = EvcNetApiClient(
        "https://example.com", "user@example.com", "secret", session
    )

    with pytest.raises(TwoFactorRequired):
        await client.authenticate()

    assert object.__getattribute__(client, "_token") == "token-123"


@pytest.mark.asyncio
async def test_config_flow_shows_otp_step_when_2fa_required() -> None:
    """The config flow should pause on the OTP step instead of creating the entry."""
    flow = EvcNetConfigFlow()
    flow.hass = MagicMock()
    setattr(flow, "_client", MagicMock())
    object.__getattribute__(flow, "_client").authenticate = AsyncMock(
        side_effect=TwoFactorRequired("Email verification required")
    )
    setattr(
        flow,
        "_pending_user_input",
        {
            "base_url": "https://example.com",
            "username": "user@example.com",
            "password": "secret",
        },
    )

    result = await flow.async_step_otp()

    assert result.get("type") == "form"
    assert result.get("step_id") == "otp"


@pytest.mark.asyncio
async def test_verify_otp_accepts_valid_code() -> None:
    """Submitting a six-digit code should complete the challenge."""
    session = MagicMock()
    session.post = MagicMock(
        return_value=DummyResponse(status=302, headers={"Location": "/Overview"})
    )
    session.cookie_jar.filter_cookies.return_value = {
        "PHPSESSID": MagicMock(key="PHPSESSID", value="sess-123"),
        "SERVERID": MagicMock(key="SERVERID", value="server-456"),
    }

    client = EvcNetApiClient(
        "https://example.com", "user@example.com", "secret", session
    )
    setattr(client, "_token", "token-123")

    assert await client.verify_otp("123456") is True
    assert object.__getattribute__(client, "_token") is None
    assert object.__getattribute__(client, "_is_authenticated") is True
    assert object.__getattribute__(client, "_phpsessid") == "sess-123"
    assert object.__getattribute__(client, "_serverid") == "server-456"


@pytest.mark.asyncio
async def test_verify_otp_persists_session_after_success() -> None:
    """A successful OTP challenge should save the authenticated session for setup."""
    flow = EvcNetConfigFlow()
    flow.hass = MagicMock()
    flow.hass.config_entries.async_entry_for_domain_unique_id.return_value = None
    flow.context = {"source": "user"}

    client = MagicMock()
    client.verify_otp = AsyncMock(return_value=True)
    client.export_cookies.return_value = [
        {"name": "PHPSESSID", "value": "sess-123"},
        {"name": "SERVERID", "value": "server-456"},
    ]
    setattr(flow, "_client", client)
    setattr(
        flow,
        "_pending_user_input",
        {
            "base_url": "https://example.com",
            "username": "user@example.com",
            "password": "secret",
        },
    )
    save = AsyncMock()
    setattr(flow, "_save", save)

    result = await flow.async_step_otp({"otp": "123456"})

    assert result.get("type") == "create_entry"
    save.assert_awaited_once_with(client.export_cookies.return_value)


@pytest.mark.asyncio
async def test_verify_otp_rejects_invalid_format() -> None:
    """The verification code must be six numeric digits."""
    session = MagicMock()
    client = EvcNetApiClient(
        "https://example.com", "user@example.com", "secret", session
    )
    setattr(client, "_token", "token-123")

    with pytest.raises(InvalidOtp):
        await client.verify_otp("abc")


def test_export_and_restore_session_cookies() -> None:
    """Session cookies should round-trip without losing the authenticated state."""
    jar = MagicMock()
    jar.filter_cookies.return_value = {
        "PHPSESSID": MagicMock(key="PHPSESSID", value="sess-123"),
        "SERVERID": MagicMock(key="SERVERID", value="server-456"),
    }
    session = MagicMock()
    session.cookie_jar = jar

    client = EvcNetApiClient(
        "https://example.com", "user@example.com", "secret", session
    )
    session.cookie_jar.filter_cookies.return_value = {
        "PHPSESSID": MagicMock(key="PHPSESSID", value="sess-123"),
        "SERVERID": MagicMock(key="SERVERID", value="server-456"),
    }
    client.restore_cookies(
        [
            {"name": "PHPSESSID", "value": "sess-123"},
            {"name": "SERVERID", "value": "server-456"},
        ]
    )

    exported = client.export_cookies()
    assert {row["name"] for row in exported} == {"PHPSESSID", "SERVERID"}

    restored = EvcNetApiClient(
        "https://example.com", "user@example.com", "secret", MagicMock()
    )
    restored.restore_cookies(exported)
    assert restored.is_authenticated is True


@pytest.mark.asyncio
async def test_expired_session_login_failure_is_authentication_error() -> None:
    """A failed login after session expiry should initiate reauthentication."""
    session = MagicMock()
    session.post = MagicMock(return_value=DummyResponse(status=401))
    client = EvcNetApiClient(
        "https://example.com", "user@example.com", "secret", session
    )
    setattr(client, "_is_authenticated", True)
    client.authenticate = AsyncMock(return_value=False)

    with pytest.raises(AuthenticationError):
        await client.get_charge_spots()


@pytest.mark.asyncio
async def test_reauth_confirm_transitions_to_otp_when_required() -> None:
    """Reauthentication should reuse the OTP step when the portal requests it."""
    flow = EvcNetConfigFlow()
    flow.hass = MagicMock()
    entry = MagicMock()
    entry.data = {
        "base_url": "https://example.com",
        "username": "user@example.com",
        "password": "old-secret",
    }
    setattr(flow, "_reauth_entry", entry)

    client = MagicMock()
    client.authenticate = AsyncMock(
        side_effect=TwoFactorRequired("Email verification required")
    )
    save = AsyncMock()

    with patch(
        "custom_components.evcnet.config_flow.create_client",
        return_value=(client, MagicMock(), save),
    ):
        result = await flow.async_step_reauth_confirm({"password": "new-secret"})

    assert result.get("type") == "form"
    assert result.get("step_id") == "otp"
    assert object.__getattribute__(flow, "_pending_user_input")["password"] == (
        "new-secret"
    )


@pytest.mark.asyncio
async def test_otp_success_updates_reauth_entry() -> None:
    """A successful OTP challenge should update and reload the existing entry."""
    flow = EvcNetConfigFlow()
    flow.hass = MagicMock()
    entry = MagicMock()
    client = MagicMock()
    client.verify_otp = AsyncMock(return_value=True)
    client.export_cookies.return_value = [{"name": "PHPSESSID", "value": "session-123"}]
    setattr(flow, "_reauth_entry", entry)
    setattr(flow, "_client", client)
    setattr(
        flow,
        "_pending_user_input",
        {
            "base_url": "https://example.com",
            "username": "user@example.com",
            "password": "new-secret",
        },
    )
    save = AsyncMock()
    setattr(flow, "_save", save)
    update_result = {"type": "abort", "reason": "reauth_successful"}
    flow.async_update_reload_and_abort = MagicMock(return_value=update_result)

    result = await flow.async_step_otp({"otp": "123456"})

    assert result == update_result
    save.assert_awaited_once_with(client.export_cookies.return_value)
    flow.async_update_reload_and_abort.assert_called_once_with(
        entry,
        data_updates=object.__getattribute__(flow, "_pending_user_input"),
        reason="reauth_successful",
    )


@pytest.mark.asyncio
async def test_initial_flow_rejects_false_authentication_result() -> None:
    """Do not create a config entry when authentication returns false."""
    flow = EvcNetConfigFlow()
    flow.hass = MagicMock()
    client = MagicMock()
    client.authenticate = AsyncMock(return_value=False)
    user_input = {
        "base_url": "https://example.com",
        "username": "user@example.com",
        "password": "secret",
    }

    with patch(
        "custom_components.evcnet.config_flow.create_client",
        return_value=(client, MagicMock(), AsyncMock()),
    ):
        result = await flow.async_step_user(user_input)

    assert result.get("type") == "form"
    assert result.get("errors") == {"base": "invalid_auth"}
