"""The EVC-net integration."""

from dataclasses import dataclass, field
import logging
from typing import Any

import aiohttp

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import (
    CONF_BASE_URL,
    CONF_MOBILE_ENDPOINT,
    CONF_MOBILE_REIMBURSEMENT,
    CONF_SELECTED_CARD_IDS,
    CONF_SELECTED_CHANNEL_IDS,
    DOMAIN,
    REIMBURSEMENT_OPTIONS,
    EvcNetException,
)
from .coordinator import EvcNetCoordinator
from .reimbursement import MobileReimbursementClient, valid_endpoint
from .session import create_client

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BUTTON,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


@dataclass
class EvcNetData:
    """Define an object to hold EVC-net data."""

    coordinator: EvcNetCoordinator
    reimbursement_options: dict[str, Any] = field(default_factory=dict)
    mobile_session: aiohttp.ClientSession | None = None


type EvcNetConfigEntry = ConfigEntry[EvcNetData]

# This integration can only be set up from config entries, not from YAML
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: EvcNetConfigEntry) -> bool:
    """Set up EVC-net from a config entry."""

    # Validate required configuration
    for key in [CONF_BASE_URL, CONF_USERNAME, CONF_PASSWORD]:
        if key not in entry.data:
            _LOGGER.error(
                "Missing required configuration keys: %s. "
                "Please delete and re-add the integration: "
                "Settings > Devices & Services > EVC-net > Delete",
                key,
            )
            return False

    # Initialize API Client & Coordinator
    client, store, _ = create_client(hass, dict(entry.data))

    try:
        saved = await store.async_load()
        if saved:
            client.restore_cookies(saved.get("cookies", []))
        if not client.is_authenticated:
            raise ConfigEntryAuthFailed("EVC-net authentication required")
    except EvcNetException as err:
        raise ConfigEntryNotReady(f"Connection with EVC-net failed: {err}") from err

    selected_card_ids = entry.options.get(CONF_SELECTED_CARD_IDS, {})
    selected_channel_ids = entry.options.get(CONF_SELECTED_CHANNEL_IDS, {})
    mobile_client = None
    mobile_session = None
    endpoint = entry.options.get(CONF_MOBILE_ENDPOINT, "")
    if entry.options.get(CONF_MOBILE_REIMBURSEMENT) and valid_endpoint(endpoint):
        mobile_session = async_create_clientsession(
            hass, cookie_jar=aiohttp.DummyCookieJar()
        )
        mobile_client = MobileReimbursementClient(
            endpoint,
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            mobile_session,
        )
    coordinator = EvcNetCoordinator(
        hass,
        client,
        selected_card_ids=(
            selected_card_ids if isinstance(selected_card_ids, dict) else {}
        ),
        selected_channel_ids=(
            selected_channel_ids if isinstance(selected_channel_ids, dict) else {}
        ),
        reimbursement_client=mobile_client,
    )

    refresh_succeeded = False
    try:
        await coordinator.async_config_entry_first_refresh()
        refresh_succeeded = True
    except EvcNetException as err:
        raise ConfigEntryNotReady(f"Connection with EVC-net failed: {err}") from err
    finally:
        if not refresh_succeeded and mobile_session is not None:
            await mobile_session.close()

    entry.runtime_data = EvcNetData(
        coordinator=coordinator,
        reimbursement_options={
            key: entry.options.get(key) for key in REIMBURSEMENT_OPTIONS
        },
        mobile_session=mobile_session,
    )
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    # Register the services (only the first time the integration is set up)
    setup_services(hass)

    # Start platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_update_options(hass: HomeAssistant, entry: EvcNetConfigEntry) -> None:
    """Reload tariff options only, not every persisted RFID/channel selection."""
    current = {key: entry.options.get(key) for key in REIMBURSEMENT_OPTIONS}
    if current != entry.runtime_data.reimbursement_options:
        await hass.config_entries.async_reload(entry.entry_id)


def setup_services(hass: HomeAssistant) -> None:
    """Register EVC-net services."""
    if hass.services.has_service(DOMAIN, "start_charging"):
        return

    async def handle_start_charging(call: ServiceCall) -> None:
        """Service to start a charging session (supports an optional RFID card ID)."""
        entity_ids = call.data.get("entity_id", [])
        card_id_override = call.data.get("card_id")
        channel_id_override = call.data.get("channel_id")
        allow_missing_customer_id = call.data.get("allow_missing_customer_id") is True

        registry = er.async_get(hass)
        for entity_id in entity_ids:
            entity_entry = registry.async_get(entity_id)
            if not entity_entry or not entity_entry.config_entry_id:
                continue

            # Get the entry from the registry
            entry: EvcNetConfigEntry | None = hass.config_entries.async_get_entry(
                entity_entry.config_entry_id
            )
            if not entry or not hasattr(entry, "runtime_data"):
                continue

            coordinator = entry.runtime_data.coordinator
            # Get spot_id from unique_id (e.g. "12345_charging_switch")
            spot_id = entity_entry.unique_id.split("_")[0]
            spot_data = coordinator.data.get(spot_id)

            if spot_data:
                # Priority: 1. Service call card_id, 2. Selected card in UI
                card_id = card_id_override or spot_data.selected_card_id
                customer_id = spot_data.customer_id or ""
                channel_id = channel_id_override or spot_data.selected_channel_id

                if (
                    card_id
                    and channel_id
                    and (customer_id or allow_missing_customer_id)
                ):
                    try:
                        await coordinator.client.start_charging(
                            spot_id, customer_id, card_id, channel_id
                        )
                    except Exception:
                        _LOGGER.exception(
                            "Failed to start charging for spot %s", spot_id
                        )
                        persistent_notification.async_create(
                            hass,
                            "The charging start command failed. Check the integration logs.",
                            title="EVC-net charging",
                            notification_id=f"evcnet_{spot_id}_service_start_failed",
                        )
                        continue
                    await coordinator.async_request_refresh()
                else:
                    persistent_notification.async_create(
                        hass,
                        "Select a valid card and channel before starting charging.",
                        title="EVC-net charging",
                        notification_id=f"evcnet_{spot_id}_service_start_failed",
                    )

    hass.services.async_register(DOMAIN, "start_charging", handle_start_charging)


async def async_unload_entry(hass: HomeAssistant, entry: EvcNetConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and entry.runtime_data.mobile_session is not None:
        await entry.runtime_data.mobile_session.close()
    return unloaded
