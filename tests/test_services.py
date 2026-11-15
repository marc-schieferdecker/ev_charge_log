"""Tests for the actions and the dashboard input entities."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
)

from custom_components.ev_charge_log.const import DOMAIN

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _sub(subentry_type: str, subentry_id: str, title: str, **data: Any):
    return ConfigSubentryData(
        data=data,
        subentry_id=subentry_id,
        subentry_type=subentry_type,
        title=title,
        unique_id=None,
    )


@pytest.fixture
async def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EV Charge Log",
        data={},
        subentries_data=[
            _sub("tariff", "enbw", "EnBW", price_ac=0.59, price_dc=0.69),
            _sub("tariff", "free", "Arbeitgeber", price_ac=0.0, price_dc=None),
            _sub("vehicle", "seal", "Seal U", default_tariff_id="enbw"),
            _sub("vehicle", "dolphin", "Dolphin", default_tariff_id="free"),
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _device(hass: HomeAssistant, subentry_id: str) -> str:
    registry = dr.async_get(hass)
    entry_id = hass.config_entries.async_entries(DOMAIN)[0].entry_id
    for device in dr.async_entries_for_config_entry(registry, entry_id):
        if (DOMAIN, subentry_id) in device.identifiers:
            return device.id
    raise AssertionError(f"no device for {subentry_id}")


def _entity(hass: HomeAssistant, platform: str, subentry_id: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{subentry_id}_{key}"
    )
    assert entity_id
    return entity_id


async def _call(hass: HomeAssistant, service: str, **data: Any) -> dict[str, Any]:
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=True
    )


# --- devices ----------------------------------------------------------------


async def test_devices_carry_model_ids(hass: HomeAssistant, entry) -> None:
    registry = dr.async_get(hass)
    seal = registry.async_get(_device(hass, "seal"))
    enbw = registry.async_get(_device(hass, "enbw"))
    assert seal.model_id == "vehicle"
    assert enbw.model_id == "tariff"


# --- add_session ------------------------------------------------------------


async def test_add_session_with_tariff(hass: HomeAssistant, entry) -> None:
    result = await _call(
        hass,
        "add_session",
        vehicle=_device(hass, "seal"),
        tariff=_device(hass, "enbw"),
        energy_kwh=40,
        charge_type="dc",
        timestamp=T0,
        note="A7",
    )
    session = result["session"]
    assert session["cost"] == 27.60
    assert session["price_per_kwh"] == 0.69
    assert session["vehicle_name"] == "Seal U"
    assert session["tariff_name"] == "EnBW"
    assert session["source"] == "manual"
    state = hass.states.get(_entity(hass, "sensor", "seal", "total_cost"))
    assert state.state == "27.6"


async def test_add_session_with_cost_only(hass: HomeAssistant, entry) -> None:
    result = await _call(
        hass, "add_session", vehicle=_device(hass, "seal"), energy_kwh=12.5, cost=0
    )
    assert result["session"]["cost"] == 0.0
    assert result["session"]["cost_overridden"] is True
    assert result["session"]["tariff_id"] is None


async def test_add_session_without_tariff_and_cost_is_rejected(
    hass: HomeAssistant, entry
) -> None:
    # The vehicle has a default tariff, but actions do not use it.
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "add_session", vehicle=_device(hass, "seal"), energy_kwh=10)
    assert err.value.translation_key == "invalid_session"


async def test_add_session_naive_time_is_local(hass: HomeAssistant, entry) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    result = await _call(
        hass,
        "add_session",
        vehicle=_device(hass, "seal"),
        tariff=_device(hass, "enbw"),
        energy_kwh=10,
        timestamp="2026-11-01 12:00:00",
    )
    assert result["session"]["timestamp"] == "2026-11-01T12:00:00+01:00"


async def test_tariff_device_as_vehicle_is_rejected(hass: HomeAssistant, entry) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass, "add_session", vehicle=_device(hass, "enbw"), energy_kwh=10, cost=5
        )
    assert err.value.translation_key == "unknown_vehicle"


async def test_vehicle_device_as_tariff_is_rejected(hass: HomeAssistant, entry) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass,
            "add_session",
            vehicle=_device(hass, "seal"),
            tariff=_device(hass, "dolphin"),
            energy_kwh=10,
        )
    assert err.value.translation_key == "unknown_tariff"


# --- update / delete / list ---------------------------------------------------


async def _book(hass: HomeAssistant, vehicle: str, tariff: str, **data: Any):
    return (
        await _call(
            hass,
            "add_session",
            vehicle=_device(hass, vehicle),
            tariff=_device(hass, tariff),
            **{"energy_kwh": 40, "timestamp": T0, **data},
        )
    )["session"]


async def test_update_last_session(hass: HomeAssistant, entry) -> None:
    await _book(hass, "seal", "enbw")
    result = await _call(
        hass, "update_session", vehicle=_device(hass, "seal"), energy_kwh=50
    )
    assert result["session"]["cost"] == 29.50


async def test_update_by_id_with_tariff_change(hass: HomeAssistant, entry) -> None:
    first = await _book(hass, "seal", "enbw")
    await _book(hass, "seal", "enbw", timestamp=T0.replace(hour=13))
    result = await _call(
        hass,
        "update_session",
        vehicle=_device(hass, "seal"),
        session_id=first["id"],
        tariff=_device(hass, "free"),
    )
    assert result["session"]["id"] == first["id"]
    assert result["session"]["cost"] == 0.0


async def test_update_clear_cost(hass: HomeAssistant, entry) -> None:
    await _book(hass, "seal", "enbw", cost=19.99)
    result = await _call(
        hass, "update_session", vehicle=_device(hass, "seal"), clear_cost=True
    )
    assert result["session"]["cost"] == 23.60
    assert result["session"]["cost_overridden"] is False


async def test_update_cost_and_clear_cost_conflict(hass: HomeAssistant, entry) -> None:
    await _book(hass, "seal", "enbw")
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass,
            "update_session",
            vehicle=_device(hass, "seal"),
            cost=5,
            clear_cost=True,
        )
    assert err.value.translation_key == "cost_conflict"


async def test_update_without_sessions(hass: HomeAssistant, entry) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "update_session", vehicle=_device(hass, "seal"), note="x")
    assert err.value.translation_key == "session_not_found"


async def test_delete_last_session(hass: HomeAssistant, entry) -> None:
    await _book(hass, "seal", "enbw")
    last = await _book(hass, "seal", "enbw", timestamp=T0.replace(hour=13))
    result = await _call(hass, "delete_session", vehicle=_device(hass, "seal"))
    assert result["session"]["id"] == last["id"]
    count = hass.states.get(_entity(hass, "sensor", "seal", "session_count"))
    assert count.state == "1"


async def test_delete_session_of_other_vehicle_is_rejected(
    hass: HomeAssistant, entry
) -> None:
    session = await _book(hass, "seal", "enbw")
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            "delete_session",
            vehicle=_device(hass, "dolphin"),
            session_id=session["id"],
        )


async def test_list_sessions_filtered(hass: HomeAssistant, entry) -> None:
    await _book(hass, "seal", "enbw", timestamp=T0)
    await _book(hass, "seal", "enbw", timestamp=T0.replace(day=10))
    await _book(hass, "dolphin", "free", timestamp=T0.replace(day=5))

    everything = await _call(hass, "list_sessions")
    assert [s["vehicle_name"] for s in everything["sessions"]] == [
        "Seal U",
        "Dolphin",
        "Seal U",
    ]
    seal_late = await _call(
        hass,
        "list_sessions",
        vehicle=_device(hass, "seal"),
        start=T0.replace(day=2),
    )
    assert len(seal_late["sessions"]) == 1


async def test_services_removed_on_unload(hass: HomeAssistant, entry) -> None:
    assert hass.services.has_service(DOMAIN, "add_session")
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not hass.services.has_service(DOMAIN, "add_session")


# --- dashboard input ----------------------------------------------------------


async def test_input_preselects_default_tariff(hass: HomeAssistant, entry) -> None:
    seal = hass.states.get(_entity(hass, "select", "seal", "input_tariff"))
    dolphin = hass.states.get(_entity(hass, "select", "dolphin", "input_tariff"))
    assert seal.state == "EnBW"
    assert dolphin.state == "Arbeitgeber"
    assert seal.attributes["options"] == ["Arbeitgeber", "EnBW"]
    charge = hass.states.get(_entity(hass, "select", "seal", "input_charge_type"))
    assert charge.state == "ac"


async def _set(hass: HomeAssistant, domain: str, entity_id: str, **data: Any) -> None:
    service = "set_value" if domain == "number" else "select_option"
    await hass.services.async_call(
        domain, service, {"entity_id": entity_id, **data}, blocking=True
    )


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )


async def test_book_from_dashboard_and_reset(hass: HomeAssistant, entry) -> None:
    energy = _entity(hass, "number", "seal", "input_energy")
    charge = _entity(hass, "select", "seal", "input_charge_type")
    await _set(hass, "number", energy, value=40)
    await _set(hass, "select", charge, option="dc")
    await _press(hass, _entity(hass, "button", "seal", "book"))

    total = hass.states.get(_entity(hass, "sensor", "seal", "total_cost"))
    assert total.state == "27.6"
    # Inputs are reset after booking
    assert hass.states.get(energy).state == "unknown"
    assert hass.states.get(charge).state == "ac"


async def test_book_from_dashboard_with_cost_and_other_tariff(
    hass: HomeAssistant, entry
) -> None:
    await _set(
        hass, "number", _entity(hass, "number", "seal", "input_energy"), value=30
    )
    await _set(hass, "number", _entity(hass, "number", "seal", "input_cost"), value=0)
    tariff = _entity(hass, "select", "seal", "input_tariff")
    await _set(hass, "select", tariff, option="Arbeitgeber")
    await _press(hass, _entity(hass, "button", "seal", "book"))

    coord = entry.runtime_data
    session = coord.log.last_session("seal")
    assert session.cost == 0.0
    assert session.cost_overridden is True
    assert session.tariff_id == "free"
    assert hass.states.get(tariff).state == "EnBW"  # back to default


async def test_book_without_energy_is_rejected(hass: HomeAssistant, entry) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await _press(hass, _entity(hass, "button", "seal", "book"))
    assert err.value.translation_key == "energy_required"
    assert entry.runtime_data.log.sessions() == []


async def test_reset_button_clears_input(hass: HomeAssistant, entry) -> None:
    cost = _entity(hass, "number", "seal", "input_cost")
    tariff = _entity(hass, "select", "seal", "input_tariff")
    await _set(hass, "number", cost, value=12.34)
    await _set(hass, "select", tariff, option="Arbeitgeber")
    await _press(hass, _entity(hass, "button", "seal", "reset_input"))
    assert hass.states.get(cost).state == "unknown"
    assert hass.states.get(tariff).state == "EnBW"


async def test_input_is_per_vehicle(hass: HomeAssistant, entry) -> None:
    await _set(
        hass, "number", _entity(hass, "number", "seal", "input_energy"), value=40
    )
    dolphin = hass.states.get(_entity(hass, "number", "dolphin", "input_energy"))
    assert dolphin.state == "unknown"


async def test_cost_input_uses_ha_currency(hass: HomeAssistant, entry) -> None:
    cost = hass.states.get(_entity(hass, "number", "seal", "input_cost"))
    assert cost.attributes["unit_of_measurement"] == hass.config.currency
