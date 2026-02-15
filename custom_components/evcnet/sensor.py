"""Sensor platform for EVC-net."""

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EvcNetConfigEntry
from .const import EvcNetException
from .coordinator import EvcNetCoordinator, EvcSpotData
from .entity import EvcNetEntity
from .utils import convert_time_to_minutes, parse_locale_number

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class EvcNetSensorEntityDescription(SensorEntityDescription):
    """Describes EVC-net sensor entity."""

    value_fn: Callable[[EvcSpotData], Any] | None = None
    attributes_fn: Callable[[EvcSpotData], dict[str, Any]] | None = None


SENSOR_TYPES: tuple[EvcNetSensorEntityDescription, ...] = (
    EvcNetSensorEntityDescription(
        key="status",
        translation_key="status",
        value_fn=lambda data: data.status.get("NOTIFICATION", "Unknown"),
    ),
    EvcNetSensorEntityDescription(
        key="status_code",
        translation_key="status_code",
        value_fn=lambda data: data.status.get("STATUS", "Unknown"),
    ),
    EvcNetSensorEntityDescription(
        key="connector",
        translation_key="connector",
        value_fn=lambda data: data.status.get("CONNECTOR", "Unknown"),
        attributes_fn=lambda data: {
            "spot_id": data.info.get("IDX"),
            "channel": data.status.get("CHANNEL"),
            "card_idx": data.status.get("CARDS_IDX"),
            "customer_idx": data.status.get("CUSTOMERS_IDX"),
            "customer_name": data.status.get("CUSTOMER_NAME"),
            "software_version": data.info.get("SOFTWARE_VERSION"),
            "address": data.info.get("ADDRESS"),
            "reference": data.info.get("REFERENCE"),
            "cost_center": data.info.get("COST_CENTER_NUMBER"),
            "network_type": data.info.get("NETWORK_TYPE"),
        },
    ),
    EvcNetSensorEntityDescription(
        key="current_power",
        translation_key="current_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.status.get("MOM_POWER_KW", 0),
    ),
    EvcNetSensorEntityDescription(
        key="total_energy_usage",
        translation_key="total_energy_usage",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.total_energy_usage,
    ),
    EvcNetSensorEntityDescription(
        key="session_energy",
        translation_key="session_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.status.get("TRANS_ENERGY_DELIVERED_KWH", 0),
    ),
    EvcNetSensorEntityDescription(
        key="session_time",
        translation_key="session_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: convert_time_to_minutes(
            data.status.get("TRANSACTION_TIME_H_M", "")
        ),
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
        EvcNetSensor(
            coordinator,
            description,
            spot_id,
        )
        for spot_id in coordinator.data
        for description in SENSOR_TYPES
    )


class EvcNetSensor(EvcNetEntity, SensorEntity):
    """Representation of a EVC-net sensor."""

    entity_description: EvcNetSensorEntityDescription

    def __init__(
        self,
        coordinator: EvcNetCoordinator,
        description: EvcNetSensorEntityDescription,
        spot_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, spot_id)
        self.entity_description = description
        self._attr_unique_id = f"{spot_id}_{description.key}_sensor"

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        spot_data: EvcSpotData | None = self.coordinator.data.get(self._spot_id)
        if spot_data is None or self.entity_description.value_fn is None:
            return None

        try:
            value = self.entity_description.value_fn(spot_data)
        except (KeyError, TypeError, AttributeError) as err:
            _LOGGER.warning(
                "Error getting value for %s at spot %s: %s",
                self.entity_description.key,
                self._spot_id,
                err,
            )
            return None

        # Filter out empty strings
        if value is None or value == "":
            return None

        # Parse locale-aware numbers for numeric sensors
        # Sensors with device_class and state_class expect numeric values
        if (
            self.entity_description.device_class is not None
            or self.entity_description.state_class is not None
        ) and isinstance(value, str):
            # This is a numeric sensor with a string value, parse it
            return parse_locale_number(value, default=0.0)

        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return entity specific state attributes."""
        spot_data: EvcSpotData | None = self.coordinator.data.get(self._spot_id)

        if spot_data is None or self.entity_description.attributes_fn is None:
            return None

        try:
            return self.entity_description.attributes_fn(spot_data)
        except EvcNetException as err:
            _LOGGER.debug(
                "Error getting attributes for %s: %s", self.entity_description.key, err
            )
            return None
