"""Dashboard input fields: tariff and AC/DC per vehicle."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CHARGE_TYPES
from .coordinator import EvChargeLogConfigEntry
from .entity import EvChargeLogEntity, vehicle_device

TARIFF = SelectEntityDescription(key="input_tariff", translation_key="input_tariff")
CHARGE_TYPE = SelectEntityDescription(
    key="input_charge_type",
    translation_key="input_charge_type",
    options=list(CHARGE_TYPES),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvChargeLogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the input selects for every vehicle."""
    coord = entry.runtime_data
    for vehicle_id in coord.vehicles:
        device = vehicle_device(coord, vehicle_id)
        async_add_entities(
            [
                ChargeTypeSelect(coord, vehicle_id, device, CHARGE_TYPE),
                TariffSelect(coord, vehicle_id, device, TARIFF),
            ],
            config_subentry_id=vehicle_id,
        )


class TariffSelect(EvChargeLogEntity, SelectEntity):
    """Tariff for the next booking, preset with the vehicle's default.

    Tariffs live in the other config entry and may load later or change at
    any time; the options are read live. Without tariffs it is unavailable
    and booking needs a cost.
    """

    @property
    def available(self) -> bool:
        """Only available when tariffs exist."""
        return bool(self._coord.tariffs)

    @property
    def options(self) -> list[str]:
        """Tariff names, sorted."""
        return [self._coord.tariffs[t].name for t in self._coord.sorted_tariffs()]

    @property
    def current_option(self) -> str | None:
        """Name of the selected tariff."""
        tariff_id = self._coord.inputs[self._subentry_id].tariff_id
        tariff = self._coord.tariffs.get(tariff_id) if tariff_id else None
        return tariff.name if tariff else None

    async def async_select_option(self, option: str) -> None:
        """Select a tariff by name (names are unique)."""
        tariff_id = next(t.id for t in self._coord.tariffs.values() if t.name == option)
        self._coord.async_set_input(self._subentry_id, tariff_id=tariff_id)


class ChargeTypeSelect(EvChargeLogEntity, SelectEntity):
    """AC or DC for the next booking."""

    @property
    def current_option(self) -> str:
        """Selected charge type."""
        return self._coord.inputs[self._subentry_id].charge_type

    async def async_select_option(self, option: str) -> None:
        """Select AC or DC."""
        self._coord.async_set_input(self._subentry_id, charge_type=option)
