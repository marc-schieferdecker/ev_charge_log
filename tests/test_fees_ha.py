"""Monthly fees in Home Assistant: form, automatic booking, sensors."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.ev_charge_log.const import DOMAIN
from custom_components.ev_charge_log.coordinator import STORAGE_KEY

from .conftest import make_subentry as _sub
from .conftest import setup_entries

FEE = {
    "monthly_fee": 11.99,
    "fee_vehicle_id": "seal",
    "fee_start": "2026-10-01",
    "fee_end": None,
}


@pytest.fixture(autouse=True)
async def berlin(hass: HomeAssistant, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2026-11-15 12:00:00+01:00")


async def _setup(hass: HomeAssistant, **fee: Any):
    return await setup_entries(
        hass,
        [
            _sub("tariff", "enbw", "EnBW", price_dc=0.69, **{**FEE, **fee}),
            _sub("tariff", "free", "Arbeitgeber", price_ac=0.0),
            _sub("vehicle", "seal", "Seal U"),
            _sub("vehicle", "dolphin", "Dolphin"),
        ],
    )


def _state(hass: HomeAssistant, subentry_id: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{subentry_id}_{key}"
    )
    assert entity_id
    return hass.states.get(entity_id).state


async def test_fees_booked_on_setup(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    vehicles, _ = await _setup(hass)
    fees = vehicles.runtime_data.log.fees()
    assert [(f.month, f.amount, f.vehicle_id) for f in fees] == [
        ("2026-10", 11.99, "seal"),
        ("2026-11", 11.99, "seal"),
    ]
    assert len(hass_storage[STORAGE_KEY]["data"]["fees"]) == 2
    assert _state(hass, "seal", "fee_total") == "23.98"
    assert _state(hass, "seal", "total_cost") == "23.98"
    assert _state(hass, "seal", "session_count") == "0"
    assert _state(hass, "dolphin", "fee_total") == "0.0"
    assert _state(hass, "enbw", "total_cost") == "23.98"


async def test_fee_raises_average_price(hass: HomeAssistant) -> None:
    await _setup(hass)
    devices = list(dr.async_get(hass).devices)
    device = next(d for d in devices if (DOMAIN, "seal") in d.identifiers).id
    tariff_device = next(d for d in devices if (DOMAIN, "enbw") in d.identifiers).id
    await hass.services.async_call(
        DOMAIN,
        "add_session",
        {
            "vehicle": device,
            "tariff": tariff_device,
            "energy_kwh": 40,
            "charge_type": "dc",
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    # 40 kWh x 0.69 = 27.60 + 2 x 11.99 fees = 51.58
    assert _state(hass, "seal", "total_cost") == "51.58"
    assert _state(hass, "seal", "average_price") == str(round(51.58 / 40, 4))


async def test_new_month_is_booked_at_midnight(hass: HomeAssistant, freezer) -> None:
    vehicles, _ = await _setup(hass)
    assert len(vehicles.runtime_data.log.fees()) == 2

    freezer.move_to("2026-12-01 00:00:30+01:00")
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    months = [f.month for f in vehicles.runtime_data.log.fees()]
    assert months == ["2026-10", "2026-11", "2026-12"]


async def test_restart_does_not_book_twice(hass: HomeAssistant) -> None:
    vehicles, tariffs = await _setup(hass)
    for entry in (vehicles, tariffs):
        assert await hass.config_entries.async_unload(entry.entry_id)
    for entry in (vehicles, tariffs):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(vehicles.runtime_data.log.fees()) == 2


async def test_fee_end_stops_booking(hass: HomeAssistant) -> None:
    vehicles, _ = await _setup(hass, fee_end="2026-10-31")
    assert [f.month for f in vehicles.runtime_data.log.fees()] == ["2026-10"]


async def test_fee_without_vehicle_entry_books_nothing(hass: HomeAssistant) -> None:
    vehicles, _ = await _setup(hass, fee_vehicle_id="unknown")
    assert vehicles.runtime_data.log.fees() == []


async def test_deleting_fee_vehicle_removes_its_fees(hass: HomeAssistant) -> None:
    vehicles, _ = await _setup(hass)
    hass.config_entries.async_remove_subentry(vehicles, "seal")
    await hass.async_block_till_done()
    assert vehicles.runtime_data.log.fees() == []


# --- form -----------------------------------------------------------------------


async def test_add_tariff_with_fee(hass: HomeAssistant) -> None:
    _, tariffs = await _setup(hass)
    result = await hass.config_entries.subentries.async_init(
        (tariffs.entry_id, "tariff"), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Ionity Passport",
            "price_dc": 0.39,
            "monthly_fee": 5.99,
            "fee_vehicle_id": "dolphin",
            "fee_start": "2026-11-20",
        },
    )
    # A tariff with a fee asks for the reference prices (optional).
    assert result["step_id"] == "reference"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    coord = tariffs.runtime_data
    fees = coord.log.fees(vehicle_id="dolphin")
    assert [(f.month, f.amount) for f in fees] == [("2026-11", 5.99)]


async def test_fee_needs_vehicle_and_start(hass: HomeAssistant) -> None:
    _, tariffs = await _setup(hass)
    result = await hass.config_entries.subentries.async_init(
        (tariffs.entry_id, "tariff"), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "X", "price_ac": 0.4, "monthly_fee": 5}
    )
    assert result["errors"] == {"base": "fee_incomplete"}


async def test_fee_end_before_start_is_rejected(hass: HomeAssistant) -> None:
    _, tariffs = await _setup(hass)
    result = await hass.config_entries.subentries.async_init(
        (tariffs.entry_id, "tariff"), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "X",
            "price_ac": 0.4,
            "monthly_fee": 5,
            "fee_vehicle_id": "seal",
            "fee_start": "2026-10-01",
            "fee_end": "2026-09-01",
        },
    )
    assert result["errors"] == {"fee_end": "fee_end_before_start"}


async def test_new_fee_version_applies_from_next_month(
    hass: HomeAssistant, freezer
) -> None:
    vehicles, tariffs = await _setup(hass)
    result = await hass.config_entries.subentries.async_init(
        (tariffs.entry_id, "tariff"),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": "enbw"},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "add_price"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"valid_from": "2026-12-01", "price_dc": 0.72, "monthly_fee": 12.99},
    )
    assert result["step_id"] == "reference"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()

    freezer.move_to(
        datetime(2026, 12, 1, 0, 0, 30, tzinfo=dt_util.get_default_time_zone())
    )
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    coord = vehicles.runtime_data
    assert [(f.month, f.amount) for f in coord.log.fees()] == [
        ("2026-10", 11.99),
        ("2026-11", 11.99),
        ("2026-12", 12.99),
    ]
