"""Dashboard input fields: energy and optional cost per vehicle."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EvChargeLogConfigEntry
from .entity import EvChargeLogEntity, vehicle_device

ENERGY = NumberEntityDescription(
    key="input_energy",
    translation_key="input_energy",
    device_class=NumberDeviceClass.ENERGY,
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    native_min_value=0,
    native_max_value=1000,
    native_step=0.01,
    mode=NumberMode.BOX,
)
COST = NumberEntityDescription(
    key="input_cost",
    translation_key="input_cost",
    device_class=NumberDeviceClass.MONETARY,
    native_min_value=0,
    native_max_value=10000,
    native_step=0.01,
    mode=NumberMode.BOX,
)
_FIELDS = {ENERGY.key: "energy_kwh", COST.key: "cost"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvChargeLogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the input numbers for every vehicle."""
    coord = entry.runtime_data
    for vehicle_id in coord.vehicles:
        device = vehicle_device(coord, vehicle_id)
        async_add_entities(
            [InputNumber(coord, vehicle_id, device, d) for d in (ENERGY, COST)],
            config_subentry_id=vehicle_id,
        )


class InputNumber(EvChargeLogEntity, NumberEntity):
    """Input field; empty (unknown) until set, cleared after booking."""

    @property
    def native_unit_of_measurement(self) -> str | None:
        """kWh for energy, the HA currency for cost."""
        if self.entity_description.key == COST.key:
            return self.hass.config.currency
        return self.entity_description.native_unit_of_measurement

    @property
    def native_value(self) -> float | None:
        """Return the current input."""
        field = _FIELDS[self.entity_description.key]
        return getattr(self._coord.inputs[self._subentry_id], field)

    async def async_set_native_value(self, value: float) -> None:
        """Store the input."""
        field = _FIELDS[self.entity_description.key]
        self._coord.async_set_input(self._subentry_id, **{field: value})
