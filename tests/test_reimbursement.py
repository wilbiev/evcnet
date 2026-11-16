# ruff: noqa: SLF001
"""Tests for the optional reimbursement tariff source."""

from datetime import timedelta
from typing import Any, Self
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.evcnet.coordinator import EvcNetCoordinator
from custom_components.evcnet.reimbursement import (
    MobileReimbursementClient,
    ReimbursementData,
    ReimbursementError,
    tariff_number,
    valid_endpoint,
)
from homeassistant.util import dt as dt_util

ENDPOINT = "https://api.example.com/graphql"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (ENDPOINT, True),
        ("http://api.example.com/graphql", False),
        ("https://user:pw@api.example.com/graphql", False),
        ("https://api.example.com/graphql?x=1", False),
        ("https://api.example.com/graphql#frag", False),
        ("https://api.example.com/other", False),
        ("", False),
    ],
)
def test_valid_endpoint(value: str, expected: bool) -> None:
    """Only plain HTTPS GraphQL endpoints are accepted."""
    assert valid_endpoint(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        (True, None),
        (-0.1, None),
        (float("nan"), None),
        (0, 0.0),
        (0.2345, 0.2345),
        ("0,2345 EUR", 0.2345),
    ],
)
def test_tariff_number(value: Any, expected: float | None) -> None:
    """Missing or invalid tariffs stay None instead of becoming zero."""
    assert tariff_number(value) == expected


class _Response:
    """Minimal async context manager mimicking an aiohttp response."""

    def __init__(self, payload: Any, status: int = 200) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def json(self) -> Any:
        return self._payload


def _client(*responses: _Response) -> tuple[MobileReimbursementClient, MagicMock]:
    session = MagicMock()
    session.post.side_effect = list(responses)
    return MobileReimbursementClient(ENDPOINT, "user", "secret", session), session


def _login() -> _Response:
    return _Response({"data": {"loginFiftyFive": {"access_token": "tok"}}})


def _hcc(enabled: Any, tariff: Any) -> _Response:
    return _Response(
        {
            "data": {
                "getChargeStationById": {
                    "homeChargingCompensation": {
                        "hccEnabled": enabled,
                        "hccTariff": tariff,
                    }
                }
            }
        }
    )


@pytest.mark.asyncio
async def test_get_tariff_logs_in_once_and_reuses_token() -> None:
    """The token is cached between tariff queries and redirects are refused."""
    client, session = _client(_login(), _hcc(True, "0.25"), _hcc(True, 0.3))

    assert await client.get_tariff("spot-1") == 0.25
    assert await client.get_tariff("spot-1") == 0.3

    assert session.post.call_count == 3
    query_call = session.post.call_args_list[1]
    assert query_call.kwargs["headers"]["Authorization"] == "Bearer tok"
    assert query_call.kwargs["allow_redirects"] is False


@pytest.mark.asyncio
async def test_get_tariff_disabled_compensation_is_zero() -> None:
    """An explicitly disabled compensation reports a zero tariff."""
    client, _ = _client(_login(), _hcc(False, None))
    assert await client.get_tariff("spot-1") == 0.0


@pytest.mark.asyncio
async def test_get_tariff_failure_enters_cooldown() -> None:
    """Auth failures raise and suppress further requests during the cooldown."""
    client, session = _client(_Response({"errors": [{"message": "bad"}]}))

    with pytest.raises(ReimbursementError):
        await client.get_tariff("spot-1")
    with pytest.raises(ReimbursementError, match="cooldown"):
        await client.get_tariff("spot-1")

    assert session.post.call_count == 1


@pytest.mark.asyncio
async def test_get_tariff_http_error_clears_token() -> None:
    """A 401 drops the cached token so the next attempt logs in again."""
    client, _ = _client(_login(), _Response({}, status=401))

    with pytest.raises(ReimbursementError):
        await client.get_tariff("spot-1")
    assert client._token is None


def _coordinator(mobile: Any = None) -> EvcNetCoordinator:
    coordinator = object.__new__(EvcNetCoordinator)
    coordinator._reimbursement_client = mobile
    coordinator._reimbursement_vat_percent = None
    coordinator._reimbursement_cache = {}
    coordinator._spots_updated = dt_util.utcnow()
    return coordinator


@pytest.mark.asyncio
async def test_coordinator_prefers_mobile_tariff() -> None:
    """The mobile HCC tariff wins over the portal reimbursement field."""
    mobile = MagicMock()
    mobile.get_tariff = AsyncMock(return_value=0.21)
    coordinator = _coordinator(mobile)

    result = await coordinator._async_get_reimbursement(
        {"REIMBURSEMENT_TARIFF": "0.30"}, "spot-1"
    )

    assert (result.tariff, result.source) == (0.21, "mobile_hcc")


@pytest.mark.asyncio
async def test_coordinator_falls_back_to_portal_and_ignores_generic_tariff() -> None:
    """Mobile failures fall back to REIMBURSEMENT_TARIFF, never to TARIFF."""
    mobile = MagicMock()
    mobile.get_tariff = AsyncMock(side_effect=ReimbursementError("down"))
    coordinator = _coordinator(mobile)

    result = await coordinator._async_get_reimbursement(
        {"REIMBURSEMENT_TARIFF": "0,30", "TARIFF": "0.50"}, "spot-1"
    )
    assert (result.tariff, result.source) == (0.30, "portal_reimbursement")

    other = await _coordinator()._async_get_reimbursement({"TARIFF": "0.50"}, "s2")
    assert other == ReimbursementData()


@pytest.mark.asyncio
async def test_coordinator_caches_reimbursement() -> None:
    """Results are cached per spot for the refresh window."""
    mobile = MagicMock()
    mobile.get_tariff = AsyncMock(return_value=0.21)
    coordinator = _coordinator(mobile)

    await coordinator._async_get_reimbursement({}, "spot-1")
    await coordinator._async_get_reimbursement({}, "spot-1")
    assert mobile.get_tariff.await_count == 1

    stamp, data = coordinator._reimbursement_cache["spot-1"]
    coordinator._reimbursement_cache["spot-1"] = (stamp - timedelta(hours=1), data)
    await coordinator._async_get_reimbursement({}, "spot-1")
    assert mobile.get_tariff.await_count == 2
