"""Tests for starting charging sessions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.evcnet import setup_services
from custom_components.evcnet.api import EvcNetApiClient


@pytest.mark.asyncio
@pytest.mark.parametrize("customer_id", ["", "customer-1"])
async def test_start_charging_sends_customer_id(customer_id: str) -> None:
    """The API request includes the provided customer ID string."""
    client = EvcNetApiClient(
        "https://example.com", "user@example.com", "secret", MagicMock()
    )
    with patch.object(
        client, "_make_ajax_request", new_callable=AsyncMock
    ) as make_request:
        await client.start_charging("spot-1", customer_id, "card-1", "2")

    payload = make_request.await_args.args[0]
    assert payload["0"]["params"]["customer"] == customer_id


def _start_charging_handler(
    customer_id: str, card_id: str | None = "card-1"
) -> tuple[object, AsyncMock, MagicMock]:
    """Register the service with a mocked charging spot and return its handler."""
    coordinator = MagicMock()
    coordinator.data = {
        "spot-1": SimpleNamespace(
            customer_id=customer_id,
            selected_card_id=card_id,
            selected_channel_id="1",
        )
    }
    coordinator.client.start_charging = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))
    entity_entry = SimpleNamespace(
        config_entry_id="entry-1", unique_id="spot-1_charging_switch"
    )
    registry = MagicMock()
    registry.async_get.return_value = entity_entry
    hass = MagicMock()
    hass.services.has_service.return_value = False
    hass.config_entries.async_get_entry.return_value = entry

    with patch("custom_components.evcnet.er.async_get", return_value=registry):
        setup_services(hass)

    handler = hass.services.async_register.call_args.args[2]
    return handler, coordinator.client.start_charging, registry


@pytest.mark.asyncio
async def test_service_requires_customer_id_by_default() -> None:
    """The opt-in service option does not change the default validation."""
    handler, start_charging, registry = _start_charging_handler("")
    with (
        patch("custom_components.evcnet.er.async_get", return_value=registry),
        patch("custom_components.evcnet.persistent_notification.async_create"),
    ):
        await handler(SimpleNamespace(data={"entity_id": ["switch.spot-1"]}))

    start_charging.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_can_allow_missing_customer_id() -> None:
    """The service forwards a missing customer ID only when explicitly allowed."""
    handler, start_charging, registry = _start_charging_handler("")
    with patch("custom_components.evcnet.er.async_get", return_value=registry):
        await handler(
            SimpleNamespace(
                data={
                    "entity_id": ["switch.spot-1"],
                    "allow_missing_customer_id": True,
                }
            )
        )

    start_charging.assert_awaited_once_with("spot-1", "", "card-1", "1")


@pytest.mark.asyncio
async def test_service_uses_customer_id_when_available() -> None:
    """The service keeps the spot's customer ID when one is available."""
    handler, start_charging, registry = _start_charging_handler("customer-1")
    with patch("custom_components.evcnet.er.async_get", return_value=registry):
        await handler(
            SimpleNamespace(
                data={
                    "entity_id": ["switch.spot-1"],
                    "allow_missing_customer_id": True,
                }
            )
        )

    start_charging.assert_awaited_once_with("spot-1", "customer-1", "card-1", "1")


@pytest.mark.asyncio
async def test_service_still_requires_card_without_customer_id() -> None:
    """Allowing a missing customer ID does not bypass the card requirement."""
    handler, start_charging, registry = _start_charging_handler("", card_id=None)
    with (
        patch("custom_components.evcnet.er.async_get", return_value=registry),
        patch("custom_components.evcnet.persistent_notification.async_create"),
    ):
        await handler(
            SimpleNamespace(
                data={
                    "entity_id": ["switch.spot-1"],
                    "allow_missing_customer_id": True,
                }
            )
        )

    start_charging.assert_not_awaited()
