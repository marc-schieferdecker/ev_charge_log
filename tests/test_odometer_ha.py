"""Cost per 100 km from an odometer sensor."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.ev_charge_log.const import DOMAIN
from custom_components.ev_charge_log.coordinator import STORAGE_KEY
from custom_components.ev_charge_log.models import SessionInput
from custom_components.ev_charge_log.panel import async_panel_data

from .conftest import make_subentry as _sub
from .conftest import setup_entries

ODOMETER = "sensor.seal_kilometerstand"


async def _setup(hass: HomeAssistant, odometer: str | None = "12000", unit="km"):
    if odometer is not None:
        hass.states.async_set(ODOMETER, odometer, {"unit_of_measurement": unit})
    vehicles, _ = await setup_entries(
        hass,
        [
            _sub("tariff", "enbw", "EnBW", price_ac=0.59),
            _sub("vehicle", "seal", "Seal U", odometer_entity_id=ODOMETER),
            _sub("vehicle", "dolphin", "Dolphin"),
        ],
    )
    return vehicles


def _entity(hass: HomeAssistant, vehicle: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{vehicle}_cost_per_100"
    )
    assert entity_id
    return entity_id


async def _book(coord, kwh: float, when=None) -> None:
    await coord.async_add_session(
        SessionInput(
            vehicle_id="seal",
            energy_kwh=kwh,
            charge_type="ac",
            timestamp=when or dt_util.now(),
            tariff_id="enbw",
        )
    )


async def test_cost_per_100_km(hass: HomeAssistant) -> None:
    vehicles = await _setup(hass)
    coord = vehicles.runtime_data
    state = hass.states.get(_entity(hass, "seal"))
    assert state.state == "unknown"  # nothing driven yet
    assert state.attributes["unit_of_measurement"] == "EUR/100 km"
    assert state.attributes["friendly_name"] == "Seal U Cost per 100 km"

    await _book(coord, 40.0)  # 23.60
    hass.states.async_set(ODOMETER, "12400", {"unit_of_measurement": "km"})
    await hass.async_block_till_done()
    assert hass.states.get(_entity(hass, "seal")).state == "5.9"


async def test_sessions_before_baseline_do_not_count(hass: HomeAssistant) -> None:
    vehicles = await _setup(hass)
    coord = vehicles.runtime_data
    await _book(coord, 40.0, dt_util.now() - timedelta(days=3))
    await _book(coord, 20.0)  # 11.80
    hass.states.async_set(ODOMETER, "12200", {"unit_of_measurement": "km"})
    await hass.async_block_till_done()
    assert hass.states.get(_entity(hass, "seal")).state == "5.9"


async def test_baseline_waits_for_a_valid_reading(hass: HomeAssistant) -> None:
    vehicles = await _setup(hass, odometer="unavailable")
    coord = vehicles.runtime_data
    assert "seal" not in coord.odometer
    hass.states.async_set(ODOMETER, "15000", {"unit_of_measurement": "km"})
    await hass.async_block_till_done()
    assert coord.odometer["seal"].start == 15000.0


async def test_baseline_is_stored_and_kept(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    vehicles = await _setup(hass)
    stored = hass_storage[STORAGE_KEY]["data"]["odometer"]["seal"]
    assert stored["start"] == 12000.0
    assert stored["entity_id"] == ODOMETER

    hass.states.async_set(ODOMETER, "13000", {"unit_of_measurement": "km"})
    for entry in hass.config_entries.async_entries(DOMAIN):
        assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert vehicles.runtime_data.odometer["seal"].start == 12000.0


async def test_odometer_in_miles_is_converted(hass: HomeAssistant) -> None:
    vehicles = await _setup(hass, odometer="1000", unit="mi")
    coord = vehicles.runtime_data
    assert coord.odometer["seal"].start == pytest.approx(1609.344)


async def test_vehicle_without_odometer(hass: HomeAssistant) -> None:
    await _setup(hass)
    assert hass.states.get(_entity(hass, "dolphin")).state == "unknown"


async def test_panel_shows_cost_per_100_instead_of_count(hass: HomeAssistant) -> None:
    await _setup(hass)
    result = await async_panel_data(hass)
    seal = next(v for v in result["vehicles"] if v["id"] == "seal")
    keys = [row["entity"] for row in seal["stats"]]
    assert _entity(hass, "seal") in keys
    assert not any(e.endswith("_sessions") for e in keys)
    # The placeholder in the name is filled in (no "{length}" in the panel).
    names = [row["name"] for row in seal["stats"]]
    assert "Cost per 100 km" in names
    assert not any("{" in (name or "") for name in names)


async def test_panel_name_in_german(hass: HomeAssistant) -> None:
    hass.config.language = "de"
    await _setup(hass)
    result = await async_panel_data(hass)
    seal = next(v for v in result["vehicles"] if v["id"] == "seal")
    assert "Kosten pro 100 km" in [row["name"] for row in seal["stats"]]
