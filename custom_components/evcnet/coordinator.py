"""DataUpdateCoordinator for EVC-net."""

from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any, cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EvcNetApiClient
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    KEY_CARDID,
    KEY_CARDS_IDX,
    KEY_CUSTOMER_NAME,
    KEY_CUSTOMERS_IDX,
    KEY_ID,
    KEY_TEXT,
    LOG_ROW_LIMIT,
    EvcNetException,
)
from .utils import get_total_energy_usage_kwh

_LOGGER = logging.getLogger(__name__)


@dataclass
class EvcSpotData:
    """Model for an individual charging station."""

    info: dict[str, Any]
    status: dict[str, Any]
    total_energy_usage: float = 0.0
    customer_id: str | None = None
    available_cards: dict[str, str] = field(default_factory=dict)
    selected_card_id: str | None = None
    selected_channel_id: str = "1"
    available_channels: dict[int, str] = field(default_factory=dict)
    logging: list[dict[str, Any]] = field(default_factory=list)


class EvcNetCoordinator(DataUpdateCoordinator[dict[str, EvcSpotData]]):
    """Class to manage fetching EVC-net data."""

    def __init__(self, hass: HomeAssistant, client: EvcNetApiClient) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        self.charge_spots: list[dict[str, Any]] = []

    def get_device_info(self, spot_id: str) -> dict[str, Any]:
        """Generate generic device info for a charge spot."""
        spot_data: EvcSpotData | None = self.data.get(spot_id)
        sw_version = spot_data.info.get("SOFTWARE_VERSION") if spot_data else None
        return {
            "identifiers": {(DOMAIN, spot_id)},
            "name": f"Charge Spot {spot_id}",
            "manufacturer": "Last Mile Solutions",
            "model": "EVC-net Charging Station",
            "sw_version": sw_version,
        }

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API."""
        try:
            await self._async_fetch_charge_spots()
            if not self.charge_spots:
                _LOGGER.warning("No charging spots found in response")
                return {}

            old_card_selections = self._get_old_card_selections()
            old_channel_selections = self._get_old_channel_selections()
            data: dict[str, EvcSpotData] = {}

            for spot in self.charge_spots:
                spot_id = spot.get("IDX")
                if spot_id:
                    spot_data = await self._async_process_spot(
                        spot, spot_id, old_card_selections, old_channel_selections
                    )
                    data[spot_id] = spot_data

        except EvcNetException as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        else:
            return data

    async def _async_fetch_charge_spots(self) -> None:
        """Fetch and store the list of charging spots."""
        if self.charge_spots:
            return

        spots_response = cast(
            list[list[dict[str, Any]]], await self.client.get_charge_spots()
        )
        _LOGGER.debug("Raw charge spots response: %s", spots_response)

        if isinstance(spots_response, list) and len(spots_response) > 0:
            first_item = spots_response[0]
            if isinstance(first_item, list) and len(first_item) > 0:
                self.charge_spots = first_item
            else:
                _LOGGER.warning(
                    "Unexpected charge spots data structure: %s", spots_response
                )
        else:
            _LOGGER.warning(
                "No charge spots data received or invalid format: %s",
                spots_response,
            )

        _LOGGER.info("Found %d charging spot(s)", len(self.charge_spots))
        _LOGGER.debug("Charging spots: %s", self.charge_spots)

    def _get_old_card_selections(self) -> dict[str, Any]:
        """Get previous card selections to avoid overwriting them."""
        old_selections = {}
        if self.data:
            for sid, sdata in self.data.items():
                if sdata.selected_card_id:
                    old_selections[sid] = sdata.selected_card_id
        return old_selections

    def _get_old_channel_selections(self) -> dict[str, Any]:
        """Get previous channel selections to avoid overwriting them."""
        old_selections = {}
        if self.data:
            for sid, sdata in self.data.items():
                if sdata.selected_channel_id:
                    old_selections[sid] = sdata.selected_channel_id
        return old_selections

    async def _async_process_spot(
        self,
        spot: dict[str, Any],
        spot_id: str,
        old_card_selections: dict[str, Any],
        old_channel_selections: dict[str, Any],
    ) -> EvcSpotData:
        """Process a single charging spot."""
        try:
            status = {}
            customer_idx = None
            available_cards = {}
            selected_card_id = None
            available_channels = {}
            selected_channel_id = "1"
            logging_data = []
            status_response = cast(
                list[list[dict[str, Any]]],
                await self.client.get_spot_overview(str(spot_id)),
            )
            if isinstance(status_response, list) and len(status_response) > 0:
                available_channels = {}
                for index, channel_info in enumerate(status_response[0]):
                    channel_name = str(channel_info.get("CHANNEL", index + 1))
                    available_channels[index] = channel_name
                if not available_channels:
                    available_channels = {0: "1"}
                selected_channel_id = old_channel_selections.get(spot_id)
                if selected_channel_id not in available_channels.values():
                    selected_channel_id = list(available_channels.values())[0]
                    _LOGGER.info(
                        "Selected channel for spot %s was not valid or new. Default selected",
                        spot_id,
                    )
                target_index = next(
                    (
                        idx
                        for idx, name in available_channels.items()
                        if name == selected_channel_id
                    ),
                    0,
                )
                try:
                    status = status_response[0][target_index]
                except IndexError:
                    status = status_response[0][0] if status_response[0] else {}
                if status:
                    (
                        customer_idx,
                        available_cards,
                        selected_card_id,
                    ) = await self._async_process_customer_and_cards(
                        spot_id, status, old_card_selections
                    )
                    _LOGGER.debug("Status for spot %s: %s", spot_id, status)
            total_energy_usage = await self._async_get_total_energy_usage(spot_id)
            _LOGGER.debug(
                "Total energy usage for spot %s: %s",
                spot_id,
                total_energy_usage,
            )
            if selected_channel_id:
                logging_data = await self._async_get_logging(
                    spot_id, selected_channel_id
                )
                _LOGGER.debug(
                    "Logging data for spot %s channel %s: %s",
                    spot_id,
                    selected_channel_id,
                    logging_data,
                )

            return EvcSpotData(
                info=spot,
                status=status,
                total_energy_usage=total_energy_usage,
                customer_id=customer_idx,
                available_cards=available_cards,
                selected_card_id=selected_card_id,
                available_channels=available_channels,
                selected_channel_id=str(selected_channel_id),
                logging=logging_data,
            )

        except EvcNetException as err:
            _LOGGER.debug(
                "Failed to fetch data for spot %s: %s (will retry next update)",
                spot_id,
                err,
            )
            if spot_id in self.data:
                return self.data[spot_id]
            return EvcSpotData(
                info=spot,
                status={},
                total_energy_usage=0.0,
                available_cards={},
                selected_card_id=None,
                available_channels={},
                selected_channel_id="",
                logging=[],
            )

    async def _async_process_customer_and_cards(
        self,
        spot_id: str,
        status: dict[str, Any],
        old_selections: dict[str, Any],
    ) -> tuple[str | None, dict[str, str], str | None]:
        """Process customer and available cards for a spot."""
        customer_idx = status.get(KEY_CUSTOMERS_IDX)
        available_cards = {}
        selected_card_id = None

        if not customer_idx:
            customer_data = cast(
                list[list[dict[str, Any]]],
                await self.client.get_customer_id(spot_id),
            )
            if isinstance(customer_data, list) and len(customer_data) > 0:
                customer_idx = customer_data[0][0].get(KEY_ID)
                customer_text = customer_data[0][0].get(KEY_TEXT)
                status[KEY_CUSTOMERS_IDX] = customer_idx
                status[KEY_CUSTOMER_NAME] = customer_text

        if customer_idx:
            card_data = cast(
                list[list[dict[str, Any]]],
                await self.client.get_card_id(spot_id, customer_idx),
            )
            if (
                isinstance(card_data, list)
                and len(card_data) > 0
                and isinstance(card_data[0], list)
            ):
                available_cards = {card["text"]: card["id"] for card in card_data[0]}
                selected_card_id = old_selections.get(spot_id)

                if not selected_card_id and available_cards:
                    selected_card_id = list(available_cards.values())[0]
                    status[KEY_CARDS_IDX] = selected_card_id
                    status[KEY_CARDID] = list(available_cards.keys())[0]
                elif selected_card_id and available_cards:
                    if selected_card_id not in available_cards.values():
                        selected_card_id = list(available_cards.values())[0]
                        status[KEY_CARDS_IDX] = selected_card_id
                        status[KEY_CARDID] = list(available_cards.keys())[0]
                        _LOGGER.info(
                            "Selected card for spot %s was not valid or new. Default selected",
                            spot_id,
                        )
                    else:
                        for name, card_id in available_cards.items():
                            if card_id == selected_card_id:
                                status[KEY_CARDS_IDX] = selected_card_id
                                status[KEY_CARDID] = name
                                break
            else:
                status[KEY_CARDS_IDX] = ""
                status[KEY_CARDID] = ""

        return customer_idx, available_cards, selected_card_id

    async def _async_get_total_energy_usage(self, spot_id: str) -> float:
        """Get total energy usage for a spot."""
        total_energy_list = cast(
            list[dict[str, Any]],
            await self.client.get_spot_total_energy_usage(str(spot_id)),
        )
        if total_energy_list and isinstance(total_energy_list, list):
            if len(total_energy_list) > 0 and isinstance(total_energy_list[0], dict):
                return get_total_energy_usage_kwh(total_energy_list[0])
        return 0.0

    async def _async_get_logging(
        self, spot_id: str, channel_id: str
    ) -> list[dict[str, Any]]:
        """Fetch logs and return a cleaned list of unique dictionaries."""
        try:
            logging_response = cast(
                list[list[Any]],
                await self.client.get_spot_log(str(spot_id), channel_id),
            )

            raw_entries = []
            if isinstance(logging_response, list) and len(logging_response) > 0:
                inner = logging_response[0]
                raw_entries = inner if isinstance(inner, list) else []

            seen = set()
            unique_entries = []
            for item in raw_entries:
                if not isinstance(item, dict):
                    continue

                date_id = item.get("LOG_DATE", "")[:-3]
                identifier = f"{date_id}|{item.get('NOTIFICATION')}"

                if identifier not in seen:
                    seen.add(identifier)
                    unique_entries.append(item)

            return unique_entries[:LOG_ROW_LIMIT]

        except EvcNetException as err:
            _LOGGER.debug("Could not fetch logging for spot %s: %s", spot_id, err)

            return []

    async def async_poll_spot(self, spot_id: str) -> None:
        """Update only the data for a specific charging spot."""
        if spot_id not in self.data:
            _LOGGER.error("Spot %s not found in current data", spot_id)
            return

        _LOGGER.debug("Manual update trigger for spot %s", spot_id)

        current_spot_data = self.data[spot_id]
        raw_spot = next(
            (s for s in self.charge_spots if str(s.get("IDX")) == spot_id), None
        )
        if not raw_spot:
            return

        new_spot_data = await self._async_process_spot(
            spot=raw_spot,
            spot_id=spot_id,
            old_card_selections={spot_id: current_spot_data.selected_card_id},
            old_channel_selections={spot_id: current_spot_data.selected_channel_id},
        )

        new_data = {**self.data}
        new_data[spot_id] = new_spot_data
        self.async_set_updated_data(new_data)
