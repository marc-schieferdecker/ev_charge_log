"""Hourly statistics in the recorder (stage 5), with Home Assistant."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components.recorder import Recorder, get_instance
from homeassistant.components.recorder.statistics import (
    get_metadata,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.ev_charge_log.const import DOMAIN, STATISTICS_DELAY
from custom_components.ev_charge_log.coordinator import STORAGE_KEY
from custom_components.ev_charge_log.models import SessionInput
from custom_components.ev_charge_log.storage import (
    STORAGE_MINOR_VERSION,
    STORAGE_VERSION,
)

from .conftest import make_subentry as _sub
from .conftest import setup_entries

SEAL_ENERGY = f"{DOMAIN}:seal_energy"
SEAL_COST = f"{DOMAIN}:seal_cost"
DOLPHIN_COST = f"{DOMAIN}:dolphin_cost"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: Recorder, enable_custom_integrations: None
) -> None:
    """The recorder has to be set up before hass (overrides conftest)."""
    return


@pytest.fixture(autouse=True)
async def berlin(hass: HomeAssistant, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    hass.config.currency = "EUR"
    freezer.move_to("2026-11-15 12:00:00+01:00")


async def _setup(hass: HomeAssistant):
    return await setup_entries(
        hass,
        [
            _sub("tariff", "enbw", "EnBW", price_ac=0.59, price_dc=0.69),
            _sub("vehicle", "seal", "Seal U"),
            _sub("vehicle", "dolphin", "Dolphin"),
        ],
    )


async def _flush(hass: HomeAssistant, freezer) -> None:
    """Let the debounce run out and the recorder write."""
    freezer.tick(timedelta(seconds=STATISTICS_DELAY + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)


async def _rows(hass: HomeAssistant, stat_id: str) -> list[tuple[datetime, float]]:
    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {stat_id},
        "hour",
        None,
        {"sum"},
    )
    return [
        (dt_util.utc_from_timestamp(row["start"]), row["sum"])
        for row in result.get(stat_id, [])
    ]


async def _metadata(hass: HomeAssistant) -> dict[str, Any]:
    result = await get_instance(hass).async_add_executor_job(
        lambda: get_metadata(hass, statistic_source=DOMAIN)
    )
    return {stat_id: meta for stat_id, (_, meta) in result.items()}


def _local(*args: int) -> datetime:
    return datetime(*args, tzinfo=dt_util.get_default_time_zone())


async def test_backdated_session_counts_in_its_hour(
    hass: HomeAssistant, freezer
) -> None:
    vehicles, _ = await _setup(hass)
    await vehicles.runtime_data.async_add_session(
        SessionInput(
            vehicle_id="seal",
            energy_kwh=20.0,
            charge_type="ac",
            timestamp=_local(2026, 11, 8, 18, 42),
            tariff_id="enbw",
        )
    )
    await _flush(hass, freezer)

    hour = dt_util.as_utc(_local(2026, 11, 8, 18))
    assert await _rows(hass, SEAL_ENERGY) == [(hour, 20.0)]
    assert await _rows(hass, SEAL_COST) == [(hour, 11.8)]
    metadata = await _metadata(hass)
    assert metadata[SEAL_ENERGY]["name"] == "Seal U charging energy"
    assert metadata[SEAL_ENERGY]["unit_of_measurement"] == "kWh"
    assert metadata[SEAL_COST]["name"] == "Seal U charging cost"
    assert metadata[SEAL_COST]["unit_of_measurement"] == "EUR"
    # Nothing booked for the Dolphin: no statistics.
    assert DOLPHIN_COST not in metadata


async def test_delete_and_update_rewrite_later_sums(
    hass: HomeAssistant, freezer
) -> None:
    vehicles, _ = await _setup(hass)
    coord = vehicles.runtime_data
    for day, kwh in ((1, 10.0), (2, 20.0), (3, 30.0)):
        await coord.async_add_session(
            SessionInput(
                vehicle_id="seal",
                energy_kwh=kwh,
                charge_type="ac",
                timestamp=_local(2026, 11, day, 9),
                cost=kwh / 10,
            )
        )
    await _flush(hass, freezer)
    assert [s for _, s in await _rows(hass, SEAL_COST)] == [1.0, 3.0, 6.0]

    second = coord.log.sessions(vehicle_id="seal")[1]
    await coord.async_delete_session("seal", second.id)
    await coord.async_update_session("seal", cost=0.0)  # last session
    await _flush(hass, freezer)
    rows = await _rows(hass, SEAL_COST)
    assert [(t.day, s) for t, s in rows] == [(1, 1.0), (3, 1.0)]
    assert [s for _, s in await _rows(hass, SEAL_ENERGY)] == [10.0, 40.0]


async def test_existing_sessions_filled_on_load(
    hass: HomeAssistant, freezer, hass_storage: dict[str, Any]
) -> None:
    """Sessions booked before stage 5 appear without booking anything."""
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "minor_version": STORAGE_MINOR_VERSION,
        "key": STORAGE_KEY,
        "data": {
            "sessions": [
                {
                    "id": "old",
                    "vehicle_id": "dolphin",
                    "timestamp": "2026-03-04T07:15:00+01:00",
                    "energy_kwh": 25.0,
                    "tariff_id": None,
                    "charge_type": "ac",
                    "price_per_kwh": None,
                    "cost": 0.0,
                    "cost_overridden": True,
                    "source": "manual",
                }
            ]
        },
    }
    await _setup(hass)
    await _flush(hass, freezer)
    hour = dt_util.as_utc(_local(2026, 3, 4, 7))
    assert await _rows(hass, f"{DOMAIN}:dolphin_energy") == [(hour, 25.0)]
    assert await _rows(hass, DOLPHIN_COST) == [(hour, 0.0)]


async def test_removing_vehicle_removes_its_statistics(
    hass: HomeAssistant, freezer
) -> None:
    vehicles, _ = await _setup(hass)
    coord = vehicles.runtime_data
    for vehicle_id in ("seal", "dolphin"):
        await coord.async_add_session(
            SessionInput(
                vehicle_id=vehicle_id,
                energy_kwh=10.0,
                charge_type="ac",
                timestamp=_local(2026, 11, 14, 9),
                cost=5.0,
            )
        )
    await _flush(hass, freezer)
    assert DOLPHIN_COST in await _metadata(hass)

    hass.config_entries.async_remove_subentry(vehicles, "dolphin")
    await hass.async_block_till_done()  # update listener reloads the entry
    await _flush(hass, freezer)
    metadata = await _metadata(hass)
    assert DOLPHIN_COST not in metadata
    assert f"{DOMAIN}:dolphin_energy" not in metadata
    assert SEAL_COST in metadata


async def test_renaming_vehicle_renames_statistics(
    hass: HomeAssistant, freezer
) -> None:
    vehicles, _ = await _setup(hass)
    await vehicles.runtime_data.async_add_session(
        SessionInput(
            vehicle_id="seal",
            energy_kwh=10.0,
            charge_type="ac",
            timestamp=_local(2026, 11, 14, 9),
            cost=5.0,
        )
    )
    await _flush(hass, freezer)
    hass.config_entries.async_update_subentry(
        vehicles, vehicles.subentries["seal"], title="Seal"
    )
    await hass.async_block_till_done()
    await _flush(hass, freezer)
    assert (await _metadata(hass))[SEAL_COST]["name"] == "Seal charging cost"
