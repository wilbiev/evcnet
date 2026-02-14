"""Select platform for EVC-net."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EvcNetConfigEntry
from .coordinator import EvcNetCoordinator, EvcSpotData
from .entity import EvcNetEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvcNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EVC-net select entities."""
    coordinator = entry.runtime_data.coordinator

    async_add_entities(
        EvcNetSelect(coordinator, spot_id) for spot_id in coordinator.data
    )


class EvcNetSelect(EvcNetEntity, SelectEntity):
    """Representation of a EVC-net card selector."""

    def __init__(
        self,
        coordinator: EvcNetCoordinator,
        spot_id: str,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, spot_id)
        self._attr_unique_id = f"{spot_id}_active_card_selector"
        self._attr_translation_key = "active_card"

    @property
    def options(self) -> list[str]:
        """Return a list of available card names."""
        spot_data: EvcSpotData | None = self.coordinator.data.get(self._spot_id)
        if not spot_data:
            return []
        return list(spot_data.available_cards.keys())

    @property
    def current_option(self) -> str | None:
        """Return the name of the currently selected card."""
        spot_data: EvcSpotData | None = self.coordinator.data.get(self._spot_id)
        if not spot_data or not spot_data.selected_card_id:
            return None

        # Find the name (key) that corresponds to the selected card ID (value)
        for name, card_id in spot_data.available_cards.items():
            if card_id == spot_data.selected_card_id:
                return name
        return None

    async def async_select_option(self, option: str) -> None:
        """Update the selected card ID in the coordinator data."""
        spot_data: EvcSpotData | None = self.coordinator.data.get(self._spot_id)

        if spot_data and (selected_id := spot_data.available_cards.get(option)):
            spot_data.selected_card_id = selected_id
            self.async_write_ha_state()
