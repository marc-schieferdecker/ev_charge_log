"""Shared base entity: device per vehicle/tariff, updates from the coordinator."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription

from .const import DOMAIN, MODEL_ID_TARIFF, MODEL_ID_VEHICLE
from .coordinator import EvChargeLogCoordinator


def vehicle_device(coord: EvChargeLogCoordinator, vehicle_id: str) -> DeviceInfo:
    """Device info of a vehicle."""
    return DeviceInfo(
        identifiers={(DOMAIN, vehicle_id)},
        name=coord.vehicles[vehicle_id].name,
        model_id=MODEL_ID_VEHICLE,
    )


def tariff_device(coord: EvChargeLogCoordinator, tariff_id: str) -> DeviceInfo:
    """Device info of a tariff."""
    return DeviceInfo(
        identifiers={(DOMAIN, tariff_id)},
        name=coord.tariffs[tariff_id].name,
        model_id=MODEL_ID_TARIFF,
        entry_type=DeviceEntryType.SERVICE,
    )


class EvChargeLogEntity(Entity):
    """Entity bound to one vehicle or tariff subentry."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coord: EvChargeLogCoordinator,
        subentry_id: str,
        device: DeviceInfo,
        description: EntityDescription,
    ) -> None:
        """Initialize the entity."""
        self.entity_description = description
        self._coord = coord
        self._subentry_id = subentry_id
        self._attr_unique_id = f"{subentry_id}_{description.key}"
        self._attr_device_info = device

    async def async_added_to_hass(self) -> None:
        """Follow coordinator changes."""
        self.async_on_remove(self._coord.async_add_listener(self.async_write_ha_state))
