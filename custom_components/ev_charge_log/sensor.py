"""Sensors per vehicle and per tariff. All values come from the session log."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import EvChargeLogConfigEntry, EvChargeLogCoordinator
from .models import Session, soc_metrics

# Unit placeholders, replaced with the HA currency at runtime.
_CURRENCY = "{currency}"
_CURRENCY_PER_KWH = "{currency}/kWh"
_CURRENCY_PER_PERCENT = "{currency}/%"

type ValueFn = Callable[[EvChargeLogCoordinator, str], Any]


@dataclass(frozen=True, kw_only=True)
class EvChargeLogSensorDescription(SensorEntityDescription):
    """Sensor description with a value function (coordinator, subentry_id)."""

    value_fn: ValueFn
    attrs_fn: Callable[[EvChargeLogCoordinator, str], dict[str, Any]] | None = None


def _last(coord: EvChargeLogCoordinator, vehicle_id: str) -> Session | None:
    return coord.log.last_session(vehicle_id)


def _last_attr(fn: Callable[[Session], Any]) -> ValueFn:
    def value(coord: EvChargeLogCoordinator, vehicle_id: str) -> Any:
        session = _last(coord, vehicle_id)
        return fn(session) if session else None

    return value


def _last_soc(attr: str) -> ValueFn:
    def value(coord: EvChargeLogCoordinator, vehicle_id: str) -> Any:
        session = _last(coord, vehicle_id)
        if session is None:
            return None
        metrics = soc_metrics(session, coord.vehicles[vehicle_id].battery_capacity_kwh)
        return getattr(metrics, attr) if metrics else None

    return value


def _last_tariff_name(coord: EvChargeLogCoordinator, vehicle_id: str) -> str | None:
    session = _last(coord, vehicle_id)
    if session is None or session.tariff_id is None:
        return None
    tariff = coord.tariffs.get(session.tariff_id)
    return tariff.name if tariff else None


def _last_session_attrs(
    coord: EvChargeLogCoordinator, vehicle_id: str
) -> dict[str, Any]:
    session = _last(coord, vehicle_id)
    if session is None:
        return {}
    return {
        "session_id": session.id,
        "charge_type": session.charge_type,
        "price_per_kwh": session.price_per_kwh,
        "cost_overridden": session.cost_overridden,
        "soc_start": session.soc_start,
        "soc_end": session.soc_end,
        "location": session.location,
        "note": session.note,
        "source": session.source,
    }


def _last_timestamp(session: Session) -> datetime:
    return session.timestamp


VEHICLE_SENSORS: tuple[EvChargeLogSensorDescription, ...] = (
    EvChargeLogSensorDescription(
        key="total_cost",
        translation_key="total_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=_CURRENCY,
        suggested_display_precision=2,
        value_fn=lambda c, v: c.log.summary_for_vehicle(v).total_cost,
    ),
    EvChargeLogSensorDescription(
        key="total_energy",
        translation_key="total_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda c, v: c.log.summary_for_vehicle(v).total_energy_kwh,
    ),
    EvChargeLogSensorDescription(
        key="average_price",
        translation_key="average_price",
        native_unit_of_measurement=_CURRENCY_PER_KWH,
        suggested_display_precision=3,
        value_fn=lambda c, v: c.log.summary_for_vehicle(v).average_price_per_kwh,
    ),
    EvChargeLogSensorDescription(
        key="session_count",
        translation_key="session_count",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda c, v: c.log.summary_for_vehicle(v).session_count,
    ),
    EvChargeLogSensorDescription(
        key="last_session_cost",
        translation_key="last_session_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=_CURRENCY,
        suggested_display_precision=2,
        value_fn=_last_attr(lambda s: s.cost),
    ),
    EvChargeLogSensorDescription(
        key="last_session_energy",
        translation_key="last_session_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=_last_attr(lambda s: s.energy_kwh),
    ),
    EvChargeLogSensorDescription(
        key="last_session_time",
        translation_key="last_session_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_attr(_last_timestamp),
        attrs_fn=_last_session_attrs,
    ),
    EvChargeLogSensorDescription(
        key="last_session_tariff",
        translation_key="last_session_tariff",
        value_fn=_last_tariff_name,
    ),
    # Display-only SoC values (never used for cost or energy).
    EvChargeLogSensorDescription(
        key="last_session_soc_points",
        translation_key="last_session_soc_points",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=_last_soc("soc_points"),
    ),
    EvChargeLogSensorDescription(
        key="last_session_charging_loss",
        translation_key="last_session_charging_loss",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        value_fn=_last_soc("charging_loss_percent"),
    ),
    EvChargeLogSensorDescription(
        key="last_session_cost_per_soc_point",
        translation_key="last_session_cost_per_soc_point",
        native_unit_of_measurement=_CURRENCY_PER_PERCENT,
        suggested_display_precision=3,
        value_fn=_last_soc("cost_per_soc_point"),
    ),
)

TARIFF_SENSORS: tuple[EvChargeLogSensorDescription, ...] = (
    EvChargeLogSensorDescription(
        key="total_cost",
        translation_key="tariff_total_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=_CURRENCY,
        suggested_display_precision=2,
        value_fn=lambda c, t: c.log.summary_for_tariff(t).total_cost,
    ),
    EvChargeLogSensorDescription(
        key="total_energy",
        translation_key="tariff_total_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda c, t: c.log.summary_for_tariff(t).total_energy_kwh,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvChargeLogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors for every vehicle and tariff subentry."""
    coord = entry.runtime_data
    for vehicle in coord.vehicles.values():
        device = DeviceInfo(identifiers={(DOMAIN, vehicle.id)}, name=vehicle.name)
        async_add_entities(
            [EvChargeLogSensor(coord, vehicle.id, device, d) for d in VEHICLE_SENSORS],
            config_subentry_id=vehicle.id,
        )
    for tariff in coord.tariffs.values():
        device = DeviceInfo(
            identifiers={(DOMAIN, tariff.id)},
            name=tariff.name,
            entry_type=DeviceEntryType.SERVICE,
        )
        async_add_entities(
            [EvChargeLogSensor(coord, tariff.id, device, d) for d in TARIFF_SENSORS],
            config_subentry_id=tariff.id,
        )


class EvChargeLogSensor(SensorEntity):
    """A sensor computed from the session log."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description: EvChargeLogSensorDescription

    def __init__(
        self,
        coord: EvChargeLogCoordinator,
        subentry_id: str,
        device: DeviceInfo,
        description: EvChargeLogSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        self.entity_description = description
        self._coord = coord
        self._subentry_id = subentry_id
        self._attr_unique_id = f"{subentry_id}_{description.key}"
        self._attr_device_info = device
        unit = description.native_unit_of_measurement
        if unit is not None and "{currency}" in unit:
            self._attr_native_unit_of_measurement = unit.format(
                currency=coord.hass.config.currency
            )

    async def async_added_to_hass(self) -> None:
        """Follow session log changes."""
        self.async_on_remove(self._coord.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> Any:
        """Return the current value."""
        return self.entity_description.value_fn(self._coord, self._subentry_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return session details for the last-session sensor."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self._coord, self._subentry_id)
