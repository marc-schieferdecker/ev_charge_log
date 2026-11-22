"""Tests for the coordinator, persistence and sensors."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_charge_log.const import DOMAIN
from custom_components.ev_charge_log.coordinator import (
    STORAGE_KEY,
    EvChargeLogCoordinator,
)
from custom_components.ev_charge_log.models import (
    SessionInput,
    ValidationError,
)

from .conftest import make_subentry as _sub
from .conftest import setup_entries

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


SUBENTRIES = [
    _sub("tariff", "enbw", "EnBW", price_ac=0.59, price_dc=0.69),
    _sub("tariff", "free", "Arbeitgeber", price_ac=0.0, price_dc=None),
    _sub(
        "vehicle",
        "seal",
        "Seal U",
        battery_capacity_kwh=87.0,
        default_tariff_id="enbw",
    ),
    _sub("vehicle", "dolphin", "Dolphin", battery_capacity_kwh=None),
]


@pytest.fixture
async def entries(hass: HomeAssistant) -> tuple[MockConfigEntry, MockConfigEntry]:
    return await setup_entries(hass, SUBENTRIES)


@pytest.fixture
def entry(entries) -> MockConfigEntry:
    """The vehicles entry."""
    return entries[0]


@pytest.fixture
def tariffs_entry(entries) -> MockConfigEntry:
    return entries[1]


def _coord(entry: MockConfigEntry) -> EvChargeLogCoordinator:
    return entry.runtime_data


def _state(hass: HomeAssistant, subentry_id: str, key: str):
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{subentry_id}_{key}"
    )
    assert entity_id, f"no entity for {subentry_id}_{key}"
    return hass.states.get(entity_id)


def _input(**kwargs: Any) -> SessionInput:
    defaults: dict[str, Any] = {
        "vehicle_id": "seal",
        "energy_kwh": 40.0,
        "charge_type": "ac",
        "timestamp": T0,
        "tariff_id": "enbw",
    }
    defaults.update(kwargs)
    return SessionInput(**defaults)


async def test_entities_are_created_per_subentry(
    hass: HomeAssistant, entry, tariffs_entry
) -> None:
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(
        registry, entry.entry_id
    ) + er.async_entries_for_config_entry(registry, tariffs_entry.entry_id)
    by_subentry: dict[str | None, int] = {}
    for ent in entities:
        by_subentry[ent.config_subentry_id] = (
            by_subentry.get(ent.config_subentry_id, 0) + 1
        )
    # Vehicle: 14 sensors + 4 numbers + 2 selects + 2 buttons + 1 date + 1 text;
    # tariff: 2 sensors
    assert by_subentry == {"seal": 24, "dolphin": 24, "enbw": 2, "free": 2}


async def test_initial_states(hass: HomeAssistant, entry) -> None:
    assert _state(hass, "seal", "total_cost").state == "0.0"
    assert _state(hass, "seal", "session_count").state == "0"
    assert _state(hass, "seal", "average_price").state == STATE_UNKNOWN
    assert _state(hass, "seal", "last_session_time").state == STATE_UNKNOWN
    cost = _state(hass, "seal", "total_cost")
    assert cost.attributes["unit_of_measurement"] == hass.config.currency


async def test_booking_updates_sensors(hass: HomeAssistant, entry) -> None:
    await _coord(entry).async_add_session(_input(soc_start=20, soc_end=60, note="A7"))
    await hass.async_block_till_done()

    assert _state(hass, "seal", "total_cost").state == "23.6"
    assert _state(hass, "seal", "total_energy").state == "40.0"
    assert _state(hass, "seal", "session_count").state == "1"
    assert _state(hass, "seal", "average_price").state == "0.59"
    assert _state(hass, "seal", "last_session_cost").state == "23.6"
    assert _state(hass, "seal", "last_session_tariff").state == "EnBW"
    last = _state(hass, "seal", "last_session_time")
    assert last.state == T0.isoformat()
    assert last.attributes["note"] == "A7"
    assert last.attributes["charge_type"] == "ac"
    # 40 % of 87 kWh = 34.8 kWh stored, 40 kWh charged -> 13 % loss
    assert _state(hass, "seal", "last_session_soc_points").state == "40.0"
    assert _state(hass, "seal", "last_session_charging_loss").state == "13.0"
    assert _state(hass, "enbw", "total_cost").state == "23.6"
    # Other vehicle untouched
    assert _state(hass, "dolphin", "session_count").state == "0"


async def test_soc_metrics_without_capacity_stay_unknown(
    hass: HomeAssistant, entry
) -> None:
    await _coord(entry).async_add_session(
        _input(vehicle_id="dolphin", tariff_id="free", soc_start=30, soc_end=80)
    )
    await hass.async_block_till_done()
    assert _state(hass, "dolphin", "last_session_soc_points").state == "50.0"
    assert _state(hass, "dolphin", "last_session_charging_loss").state == STATE_UNKNOWN
    # Free charge: cost 0 -> cost per point 0
    assert _state(hass, "dolphin", "last_session_cost_per_soc_point").state == "0.0"


async def test_free_sessions_lower_vehicle_average(hass: HomeAssistant, entry) -> None:
    coord = _coord(entry)
    await coord.async_add_session(_input(vehicle_id="dolphin", tariff_id="free"))
    await coord.async_add_session(_input(vehicle_id="dolphin"))
    await hass.async_block_till_done()
    assert _state(hass, "dolphin", "total_cost").state == "23.6"
    assert _state(hass, "dolphin", "average_price").state == "0.295"
    assert _state(hass, "free", "total_energy").state == "40.0"


async def test_delete_and_update_refresh_sensors(hass: HomeAssistant, entry) -> None:
    coord = _coord(entry)
    await coord.async_add_session(_input())
    await coord.async_add_session(
        _input(energy_kwh=10.0, timestamp=T0.replace(hour=13))
    )
    await hass.async_block_till_done()
    assert _state(hass, "seal", "total_cost").state == "29.5"

    await coord.async_update_session("seal", energy_kwh=20.0)  # last session
    await hass.async_block_till_done()
    assert _state(hass, "seal", "total_cost").state == "35.4"

    await coord.async_delete_session("seal")  # last session
    await hass.async_block_till_done()
    assert _state(hass, "seal", "total_cost").state == "23.6"
    assert _state(hass, "seal", "session_count").state == "1"


async def test_unknown_vehicle_is_rejected(hass: HomeAssistant, entry) -> None:
    with pytest.raises(ValidationError):
        await _coord(entry).async_add_session(_input(vehicle_id="nope"))


async def test_sessions_are_persisted(
    hass: HomeAssistant, entry, hass_storage: dict[str, Any]
) -> None:
    await _coord(entry).async_add_session(_input())
    stored = hass_storage[STORAGE_KEY]
    assert stored["version"] == 1
    assert stored["data"]["sessions"][0]["cost"] == 23.60

    # Unload both entries (drops the shared state) and load again from storage.
    for e in hass.config_entries.async_entries(DOMAIN):
        assert await hass.config_entries.async_unload(e.entry_id)
    assert DOMAIN not in hass.data
    for e in hass.config_entries.async_entries(DOMAIN):
        assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert _state(hass, "seal", "total_cost").state == "23.6"
    assert _state(hass, "enbw", "total_cost").state == "23.6"


async def test_tariffs_follow_after_vehicle_entry(
    hass: HomeAssistant, entry, tariffs_entry
) -> None:
    """Vehicle entities pick up tariff changes of the other entry live."""
    select = er.async_get(hass).async_get_entity_id(
        "select", DOMAIN, "seal_input_tariff"
    )
    assert await hass.config_entries.async_unload(tariffs_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(select).state == "unavailable"

    assert await hass.config_entries.async_setup(tariffs_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(select).state == "EnBW"


async def test_tariff_change_keeps_booked_sessions(
    hass: HomeAssistant, entry, tariffs_entry
) -> None:
    await _coord(entry).async_add_session(_input())
    enbw = tariffs_entry.subentries["enbw"]
    hass.config_entries.async_update_subentry(
        tariffs_entry, enbw, data={"price_ac": 0.99, "price_dc": 0.99}
    )
    await hass.async_block_till_done()  # update listener reloads the entry

    assert _coord(entry).tariffs["enbw"].price_ac == 0.99
    assert _state(hass, "seal", "total_cost").state == "23.6"
    session = await _coord(entry).async_add_session(_input())
    assert session.cost == 39.60


async def test_removing_vehicle_removes_its_entities_and_sessions(
    hass: HomeAssistant, entry, hass_storage: dict[str, Any]
) -> None:
    coord = _coord(entry)
    await coord.async_add_session(_input())
    await coord.async_add_session(_input(vehicle_id="dolphin", tariff_id="free"))

    hass.config_entries.async_remove_subentry(entry, "dolphin")
    await hass.async_block_till_done()  # update listener reloads the entry

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, "dolphin_total_cost") is None
    coord = _coord(entry)
    assert "dolphin" not in coord.vehicles
    assert [s.vehicle_id for s in coord.log.sessions()] == ["seal"]
    stored = hass_storage[STORAGE_KEY]["data"]["sessions"]
    assert [s["vehicle_id"] for s in stored] == ["seal"]
    # Sessions of the deleted vehicle no longer count for the tariff
    assert _state(hass, "free", "total_energy").state == "0.0"
