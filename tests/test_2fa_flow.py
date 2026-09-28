"""Regression tests for EVC-net 2FA login flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.evcnet.api import EvcNetApiClient, InvalidOtp, TwoFactorRequired
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
