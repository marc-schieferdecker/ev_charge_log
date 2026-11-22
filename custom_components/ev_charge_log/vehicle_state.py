"""Read the optional entities of a vehicle: SoC, location and odometer.

All readers return None if the entity is not configured or its state is not
usable.
"""

from __future__ import annotations

from homeassistant.const import STATE_HOME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_conversion import DistanceConverter

from .const import ZONE_HOME
from .models import Vehicle


def read_soc(hass: HomeAssistant, vehicle: Vehicle) -> float | None:
    """Current SoC in percent, None if not configured or not available."""
    if not vehicle.soc_entity_id:
        return None
    state = hass.states.get(vehicle.soc_entity_id)
    try:
        value = float(state.state) if state else None
    except ValueError:
        return None
    return value if value is not None and 0 <= value <= 100 else None


def read_zone(hass: HomeAssistant, vehicle: Vehicle) -> str | None:
    """Zone entity the vehicle is in, None if unknown or in no zone.

    Trackers and persons report the zone name as their state ("home" for
    the home zone).
    """
    if not vehicle.location_entity_id:
        return None
    state = hass.states.get(vehicle.location_entity_id)
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    if state.state == STATE_HOME:
        return ZONE_HOME
    for zone in hass.states.async_all("zone"):
        if zone.name == state.state:
            return zone.entity_id
    return None


def read_odometer(hass: HomeAssistant, vehicle: Vehicle) -> float | None:
    """Current odometer in the HA length unit; None if not available."""
    if not vehicle.odometer_entity_id:
        return None
    state = hass.states.get(vehicle.odometer_entity_id)
    if state is None:
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    unit = state.attributes.get("unit_of_measurement")
    target = hass.config.units.length_unit
    if unit and unit != target and unit in DistanceConverter.VALID_UNITS:
        value = DistanceConverter.convert(value, unit, target)
    return value
