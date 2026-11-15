"""Tests for the config flow and the vehicle/tariff subentry flows."""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    ConfigSubentryData,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
)

from custom_components.ev_charge_log.const import DOMAIN


def _tariff(title: str, price_ac: float, price_dc: float | None = None):
    return ConfigSubentryData(
        data={"price_ac": price_ac, "price_dc": price_dc},
        subentry_type="tariff",
        title=title,
        unique_id=None,
    )


def _vehicle(title: str, **data):
    return ConfigSubentryData(
        data={"battery_capacity_kwh": None, "default_tariff_id": None, **data},
        subentry_type="vehicle",
        title=title,
        unique_id=None,
    )


@pytest.fixture
async def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A set-up entry with one tariff and one vehicle."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EV Charge Log",
        data={},
        subentries_data=[_tariff("EnBW", 0.59, 0.69), _vehicle("Seal U")],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _sub(entry: MockConfigEntry, title: str):
    return next(s for s in entry.subentries.values() if s.title == title)


async def _start(hass: HomeAssistant, entry, subentry_type: str, subentry_id=None):
    context = {"source": SOURCE_USER}
    if subentry_id is not None:
        context = {"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id}
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, subentry_type), context=context
    )


# --- main entry -----------------------------------------------------------


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "EV Charge Log"


async def test_only_one_entry_allowed(hass: HomeAssistant, entry) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


# --- tariffs --------------------------------------------------------------


async def test_add_tariff(hass: HomeAssistant, entry) -> None:
    result = await _start(hass, entry, "tariff")
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": " Ionity ", "price_ac": 0.69, "price_dc": 0.79}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = _sub(entry, "Ionity")
    assert dict(sub.data) == {"price_ac": 0.69, "price_dc": 0.79}


async def test_add_free_tariff_without_dc_price(hass: HomeAssistant, entry) -> None:
    result = await _start(hass, entry, "tariff")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Arbeitgeber", "price_ac": 0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(_sub(entry, "Arbeitgeber").data) == {"price_ac": 0.0, "price_dc": None}


async def test_negative_price_is_rejected(hass: HomeAssistant, entry) -> None:
    result = await _start(hass, entry, "tariff")
    with pytest.raises(InvalidData):
        await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"name": "Bad", "price_ac": -0.1}
        )


async def test_duplicate_tariff_name_is_rejected(hass: HomeAssistant, entry) -> None:
    result = await _start(hass, entry, "tariff")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "enbw", "price_ac": 0.5}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"name": "name_exists"}


async def test_reconfigure_tariff(hass: HomeAssistant, entry) -> None:
    enbw = _sub(entry, "EnBW")
    result = await _start(hass, entry, "tariff", enbw.subentry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "EnBW", "price_ac": 0.62}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    updated = entry.subentries[enbw.subentry_id]
    assert dict(updated.data) == {"price_ac": 0.62, "price_dc": None}


# --- vehicles -------------------------------------------------------------


async def test_add_vehicle_with_default_tariff(hass: HomeAssistant, entry) -> None:
    enbw = _sub(entry, "EnBW")
    result = await _start(hass, entry, "vehicle")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Dolphin",
            "battery_capacity_kwh": 60.4,
            "default_tariff_id": enbw.subentry_id,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(_sub(entry, "Dolphin").data) == {
        "battery_capacity_kwh": 60.4,
        "default_tariff_id": enbw.subentry_id,
    }


async def test_add_vehicle_minimal(hass: HomeAssistant, entry) -> None:
    result = await _start(hass, entry, "vehicle")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Dolphin"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(_sub(entry, "Dolphin").data) == {
        "battery_capacity_kwh": None,
        "default_tariff_id": None,
    }


async def test_blank_vehicle_name_is_rejected(hass: HomeAssistant, entry) -> None:
    result = await _start(hass, entry, "vehicle")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "   "}
    )
    assert result["errors"] == {"name": "name_required"}


async def test_reconfigure_vehicle_keeps_own_name(hass: HomeAssistant, entry) -> None:
    seal = _sub(entry, "Seal U")
    result = await _start(hass, entry, "vehicle", seal.subentry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Seal U", "battery_capacity_kwh": 87.0}
    )
    assert result["type"] is FlowResultType.ABORT
    assert entry.subentries[seal.subentry_id].data["battery_capacity_kwh"] == 87.0


async def test_rename_vehicle(hass: HomeAssistant, entry) -> None:
    seal = _sub(entry, "Seal U")
    result = await _start(hass, entry, "vehicle", seal.subentry_id)
    await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Seal U AWD"}
    )
    assert entry.subentries[seal.subentry_id].title == "Seal U AWD"
