# ruff: noqa: SLF001
"""Tests for EVC-net coordinator refresh behavior."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.evcnet.api import AuthenticationError
from custom_components.evcnet.const import CONF_SELECTED_CARD_IDS
from custom_components.evcnet.coordinator import EvcNetCoordinator
from custom_components.evcnet.select import EvcNetSelect
from homeassistant.exceptions import ConfigEntryAuthFailed


def _bare_coordinator() -> EvcNetCoordinator:
    """Create a coordinator without Home Assistant runtime setup."""
    coordinator = object.__new__(EvcNetCoordinator)
    coordinator.data = {}
    coordinator.client = MagicMock()
    coordinator._selected_card_ids = {"spot-1": "card-1"}
    coordinator._selected_channel_ids = {"spot-1": "2"}
    coordinator._card_cache = {}
    coordinator._energy_cache = {}
    coordinator._logging_cache = {}
    return coordinator


def test_old_selections_include_persisted_values() -> None:
    """Coordinator startup should restore options before its first update."""
    coordinator = _bare_coordinator()

    assert coordinator._get_old_card_selections() == {"spot-1": "card-1"}
    assert coordinator._get_old_channel_selections() == {"spot-1": "2"}


@pytest.mark.asyncio
async def test_auxiliary_data_is_cached_but_force_refresh_bypasses_cache() -> None:
    """Live overview refreshes each time, while auxiliary calls are throttled."""
    coordinator = _bare_coordinator()
    coordinator.client.get_spot_overview = AsyncMock(
        return_value=[[{"CHANNEL": "1", "CUSTOMERS_IDX": "customer-1"}]]
    )
    coordinator.client.get_card_id = AsyncMock(
        return_value=[[{"text": "Home card", "id": "card-1"}]]
    )
    coordinator._async_get_total_energy_usage = AsyncMock(return_value=12.5)
    coordinator._async_get_logging = AsyncMock(return_value=[])
    args = ({"IDX": "spot-1"}, "spot-1", {}, {"spot-1": "1"})

    await coordinator._async_process_spot(*args)
    await coordinator._async_process_spot(*args)
    await coordinator._async_process_spot(*args, force_auxiliary_refresh=True)

    assert coordinator.client.get_spot_overview.await_count == 3
    assert coordinator.client.get_card_id.await_count == 2
    assert coordinator._async_get_total_energy_usage.await_count == 2
    assert coordinator._async_get_logging.await_count == 2


def test_select_persists_spot_selection_and_preserves_other_options() -> None:
    """Changing a card should save its ID without dropping unrelated options."""
    entry = MagicMock()
    entry.options = {
        "unrelated": "preserved",
        CONF_SELECTED_CARD_IDS: {"spot-0": "card-0"},
    }
    hass = MagicMock()
    select = object.__new__(EvcNetSelect)
    select.coordinator = MagicMock(hass=hass)
    select._entry = entry
    select._spot_id = "spot-1"

    select._persist_selection(CONF_SELECTED_CARD_IDS, "card-1")

    hass.config_entries.async_update_entry.assert_called_once_with(
        entry,
        options={
            "unrelated": "preserved",
            CONF_SELECTED_CARD_IDS: {"spot-0": "card-0", "spot-1": "card-1"},
        },
    )


@pytest.mark.asyncio
async def test_coordinator_auth_failure_requests_reauthentication() -> None:
    """Authentication failures should be surfaced as a config-entry auth failure."""
    coordinator = _bare_coordinator()
    coordinator.charge_spots = []
    coordinator.client.get_charge_spots = AsyncMock(
        side_effect=AuthenticationError("Expired session")
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()
