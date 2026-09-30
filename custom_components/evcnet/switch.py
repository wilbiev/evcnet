"""Switch platform for EVC-net."""

import asyncio
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EvcNetConfigEntry
from .const import CHARGESPOT_STATUS2_FLAGS, PREPARE_STATUS_LIST
from .coordinator import EvcNetCoordinator, EvcSpotData
from .entity import EvcNetEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvcNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EVC-net switches."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        EvcNetChargingSwitch(coordinator, spot_id) for spot_id in coordinator.data
    )


class EvcNetChargingSwitch(EvcNetEntity, SwitchEntity):
    """Representation of a EVC-net charging switch."""

    def __init__(self, coordinator: EvcNetCoordinator, spot_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, spot_id)
        self._attr_unique_id = f"{spot_id}_charging_switch"
        self._attr_translation_key = "charging"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool:
        """Return true if charging is active (OCCUPIED bit is set)."""
        spot_data: EvcSpotData | None = self.coordinator.data.get(self._spot_id)
        if not spot_data or not spot_data.status:
            return False

        status_value = spot_data.status.get("STATUS")
        if not status_value or not isinstance(status_value, str):
            return False

        if status_value != "0":
            # Parse the last 32 bits (status2) to check for OCCUPIED flag
            try:
                hex_status = str(status_value).zfill(16)
                status2 = int(hex_status[8:], 16)
                return bool(status2 & CHARGESPOT_STATUS2_FLAGS["OCCUPIED"])
            except (ValueError, IndexError):
                return False

        # Check if the spot is preparing the transaction (light on)
        last_notify = str(spot_data.status.get("NOTIFICATION", "")).lower()
        return any(key in last_notify for key in PREPARE_STATUS_LIST)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start charging met de geselecteerde pas."""
        spot_data: EvcSpotData | None = self.coordinator.data.get(self._spot_id)
        if not spot_data:
            return

        # Get the context from the model (set by the select entities!)
        card_id = spot_data.selected_card_id
        customer_id = spot_data.customer_id
        channel_id = spot_data.selected_channel_id

        if not card_id or not customer_id or not channel_id:
            _LOGGER.error(
                "Unable to start charging: No card selected for spot %s",
                self._spot_id,
            )
            self._notify_action_failure(
                "start", "Select a valid card and channel before starting charging."
            )
            return

        try:
            await self.coordinator.client.start_charging(
                self._spot_id, customer_id, card_id, channel_id
            )
            await self._async_wait_for_state(expected_on=True)
        except Exception as err:
            _LOGGER.error("Error when starting charging: %s", err)
            self._notify_action_failure(
                "start",
                "The charging start command failed. Check the integration logs.",
            )
            raise

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop charging."""
        spot_data: EvcSpotData | None = self.coordinator.data.get(self._spot_id)
        if not spot_data or not spot_data.status:
            return
        channel_id = spot_data.selected_channel_id
        last_notify = str(spot_data.status.get("NOTIFICATION", "")).lower()

        try:
            if any(key in last_notify for key in PREPARE_STATUS_LIST):
                await self.coordinator.client.soft_reset(self._spot_id, channel_id)
            else:
                await self.coordinator.client.stop_charging(self._spot_id, channel_id)
            await self._async_wait_for_state(expected_on=False)
        except Exception as err:
            _LOGGER.error("Error when stopping charging: %s", err)
            self._notify_action_failure(
                "stop", "The charging stop command failed. Check the integration logs."
            )
            raise

    async def _async_wait_for_state(self, *, expected_on: bool) -> None:
        """Poll briefly for the charger to reflect a requested state change."""
        for _ in range(3):
            await asyncio.sleep(3)
            await self.coordinator.async_poll_spot(self._spot_id)
            if self.is_on is expected_on:
                return

        _LOGGER.warning(
            "Charging state for spot %s did not change to %s after the command",
            self._spot_id,
            expected_on,
        )

    def _notify_action_failure(self, action: str, message: str) -> None:
        """Show an actionable notification when a charging command fails."""
        persistent_notification.async_create(
            self.hass,
            message,
            title="EVC-net charging",
            notification_id=f"evcnet_{self._spot_id}_{action}_failed",
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return attributes uit het object model."""
        spot_data: EvcSpotData | None = self.coordinator.data.get(self._spot_id)
        if not spot_data:
            return {}

        return {
            "spot_id": self._spot_id,
            "channel": spot_data.selected_channel_id,
            "customer_id": spot_data.customer_id,
            "selected_card_id": spot_data.selected_card_id,
        }
