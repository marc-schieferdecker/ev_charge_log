"""Hourly statistics in the recorder (stage 5).

The sum sensors land in the long-term statistics at the time of booking, so
a session entered later for an earlier day counts on the wrong day. This
module writes external statistics in which every session counts in the hour
of its timestamp. They are always rewritten completely per vehicle: cheap
for a few hundred sessions and correct for corrections and deletions without
special cases.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from functools import partial
from typing import cast

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_metadata,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.util.unit_conversion import EnergyConverter

from .const import DOMAIN
from .models import HourlyTotal

KIND_ENERGY = "energy"
KIND_COST = "cost"

# `unit_class` is part of the metadata from HA 2025.11 on and required from
# 2026.11 on; older versions reject the unknown key.
_HAS_UNIT_CLASS = "unit_class" in (
    StatisticMetaData.__required_keys__ | StatisticMetaData.__optional_keys__
)
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def statistic_id(vehicle_id: str, kind: str) -> str:
    """Statistic ID of a vehicle: lowercase, `[a-z0-9_]`, no `__`."""
    slug = _NOT_SLUG.sub("_", vehicle_id.lower()).strip("_")
    return f"{DOMAIN}:{slug}_{kind}"


def recorder_available(hass: HomeAssistant) -> bool:
    """True if the recorder is set up (it is optional)."""
    return "recorder" in hass.config.components


def _metadata(
    stat_id: str, name: str, unit: str | None, unit_class: str | None
) -> StatisticMetaData:
    metadata: dict[str, object] = {
        "mean_type": StatisticMeanType.NONE,
        "has_sum": True,
        "name": name,
        "source": DOMAIN,
        "statistic_id": stat_id,
        "unit_of_measurement": unit,
    }
    if _HAS_UNIT_CLASS:
        metadata["unit_class"] = unit_class
    return cast(StatisticMetaData, metadata)


@callback
def async_write_vehicle(
    hass: HomeAssistant,
    vehicle_id: str,
    names: Mapping[str, str],
    rows: list[HourlyTotal],
) -> None:
    """Replace the energy and cost statistics of a vehicle.

    Clearing and importing are queued in the recorder and run in order.
    `names` maps KIND_ENERGY / KIND_COST to the display names.
    """
    energy_id = statistic_id(vehicle_id, KIND_ENERGY)
    cost_id = statistic_id(vehicle_id, KIND_COST)
    get_instance(hass).async_clear_statistics([energy_id, cost_id])
    if not rows:
        return
    async_add_external_statistics(
        hass,
        _metadata(
            energy_id,
            names[KIND_ENERGY],
            UnitOfEnergy.KILO_WATT_HOUR,
            EnergyConverter.UNIT_CLASS,
        ),
        [
            StatisticData(start=row.start, state=row.energy_kwh, sum=row.energy_kwh)
            for row in rows
        ],
    )
    async_add_external_statistics(
        hass,
        # No unit converter exists for currencies.
        _metadata(cost_id, names[KIND_COST], hass.config.currency, None),
        [StatisticData(start=row.start, state=row.cost, sum=row.cost) for row in rows],
    )


async def async_remove_other_vehicles(
    hass: HomeAssistant, vehicle_ids: Iterable[str]
) -> None:
    """Delete the statistics of vehicles that no longer exist."""
    instance = get_instance(hass)
    known = {
        statistic_id(vehicle_id, kind)
        for vehicle_id in vehicle_ids
        for kind in (KIND_ENERGY, KIND_COST)
    }
    existing = await instance.async_add_executor_job(
        partial(get_metadata, hass, statistic_source=DOMAIN)
    )
    if stale := [stat_id for stat_id in existing if stat_id not in known]:
        instance.async_clear_statistics(stale)
