"""Button platform for EVC-net."""

import asyncio
from dataclasses import dataclass
import logging

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EvcNetConfigEntry, EvcNetException
from .coordinator import EvcNetCoordinator
from .entity import EvcNetEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class EvcNetButtonEntityDescription(ButtonEntityDescription):
    """Describes EVC-net sensor entity."""

    command: str


BUTTON_TYPES: tuple[EvcNetButtonEntityDescription, ...] = (
    EvcNetButtonEntityDescription(
        key="soft_reset",
        translation_key="soft_reset",
        command="soft_reset",
    ),
    EvcNetButtonEntityDescription(
        key="hard_reset",
        translation_key="hard_reset",
        command="hard_reset",
    ),
    EvcNetButtonEntityDescription(
        key="unlock_connector",
        translation_key="unlock_connector",
        command="unlock_connector",
    ),
    EvcNetButtonEntityDescription(
        key="block_charging",
        translation_key="block_charging",
        command="block",
    ),
    EvcNetButtonEntityDescription(
        key="unblock_charging",
        translation_key="unblock_charging",
        command="unblock",
    ),
    EvcNetButtonEntityDescription(
        key="poll_now",
        translation_key="poll_now",
        command="get_spot_overview",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvcNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EVC-net sensors."""
    coordinator = entry.runtime_data.coordinator

    async_add_entities(
        EvcNetButton(
            coordinator,
            description,
            spot_id,
        )
        for spot_id in coordinator.data
        for description in BUTTON_TYPES
    )


class EvcNetButton(EvcNetEntity, ButtonEntity):
    """Base class for EVC-net button entities."""

    entity_description: EvcNetButtonEntityDescription

    def __init__(
        self,
        coordinator: EvcNetCoordinator,
        description: EvcNetButtonEntityDescription,
        spot_id: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, spot_id)
        self.entity_description = description
        self._attr_unique_id = f"{spot_id}_{description.key}_button"

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            super().available
            and self.coordinator.data is not None
            and self._spot_id in self.coordinator.data
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        spot_data = self.coordinator.data.get(self._spot_id)
        if not spot_data:
            _LOGGER.error("Action failed: no data for spot %s", self._spot_id)
            return

        channel = str(spot_data.info.get("CHANNEL", "1"))
        api_method_name = self.entity_description.command

        # Get the method dynamically from the client
        api_method = getattr(self.coordinator.client, api_method_name)

        try:
            _LOGGER.info(
                "Executing action %s on spot %s", api_method_name, self._spot_id
            )
            await api_method(self._spot_id, channel)

            # Wait for the action to take effect
            await asyncio.sleep(3)

            # Force a refresh to get the new state
            await self.coordinator.async_request_refresh()

        except EvcNetException as err:
            _LOGGER.error("Error executing action %s: %s", api_method_name, err)
            raise
