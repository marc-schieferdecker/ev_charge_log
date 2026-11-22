"""Hourly statistics in the recorder (stage 5).

The sum sensors land in the long-term statistics at the time of booking, so
a session entered later for an earlier day counts on the wrong day. This
module writes external statistics in which every session counts in the hour
of its timestamp. They are always rewritten completely per vehicle: cheap
for a few hundred sessions and correct for corrections and deletions without
special cases.

The statistic ID is made from the displayed name ("BYD Seal U Ladekosten"
-> `ev_charge_log:byd_seal_u_ladekosten`): the statistic picker of the HA
frontend searches external statistics only by ID and integration, not by
name. Renaming a vehicle therefore changes the ID; statistics under IDs that
are no longer used are deleted.
"""

from __future__ import annotations

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
from homeassistant.util import slugify
from homeassistant.util.unit_conversion import EnergyConverter

from .const import DOMAIN
from .reports import (
    HourlyTotal,
)

KIND_ENERGY = "energy"
KIND_COST = "cost"

# `unit_class` is part of the metadata from HA 2025.11 on and required from
# 2026.11 on; older versions reject the unknown key.
_HAS_UNIT_CLASS = "unit_class" in (
    StatisticMetaData.__required_keys__ | StatisticMetaData.__optional_keys__
)


def statistic_ids[K](names: Mapping[K, str]) -> dict[K, str]:
    """A unique statistic ID per name, made from the name.

    Keys are processed in sorted order, so equal names always get the same
    suffixes (`_2`, `_3`, ...). `slugify` gives `[a-z0-9_]` without `__`
    and never an empty string, as the recorder requires.
    """
    result: dict[K, str] = {}
    used: set[str] = set()
    for key in sorted(names):
        base = f"{DOMAIN}:{slugify(names[key])}"
        stat_id = base
        number = 2
        while stat_id in used:
            stat_id = f"{base}_{number}"
            number += 1
        used.add(stat_id)
        result[key] = stat_id
    return result


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
    ids: Mapping[str, str],
    names: Mapping[str, str],
    rows: list[HourlyTotal],
) -> None:
    """Replace the energy and cost statistics of a vehicle.

    Clearing and importing are queued in the recorder and run in order.
    `ids` and `names` map KIND_ENERGY / KIND_COST to the statistic IDs and
    the display names.
    """
    energy_id = ids[KIND_ENERGY]
    cost_id = ids[KIND_COST]
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


async def async_remove_unused(hass: HomeAssistant, used_ids: Iterable[str]) -> None:
    """Delete our statistics under IDs no longer in use.

    These belong to deleted or renamed vehicles (or to the language before
    a change of the HA language).
    """
    instance = get_instance(hass)
    known = set(used_ids)
    existing = await instance.async_add_executor_job(
        partial(get_metadata, hass, statistic_source=DOMAIN)
    )
    if stale := [stat_id for stat_id in existing if stat_id not in known]:
        instance.async_clear_statistics(stale)
