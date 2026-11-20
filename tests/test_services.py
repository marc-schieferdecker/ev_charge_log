"""Tests for the actions and the dashboard input entities."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_charge_log.const import DOMAIN

from .conftest import make_subentry as _sub
from .conftest import setup_entries

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
async def entry(hass: HomeAssistant) -> MockConfigEntry:
    """The vehicles entry (the tariffs entry is set up as well)."""
    vehicles, _ = await setup_entries(
        hass,
        [
            _sub("tariff", "enbw", "EnBW", price_ac=0.59, price_dc=0.69),
            _sub("tariff", "free", "Arbeitgeber", price_ac=0.0, price_dc=None),
            _sub("tariff", "ewe", "EWE Go", price_ac=None, price_dc=0.62),
            _sub(
                "vehicle",
                "seal",
                "Seal U",
                default_tariff_id="enbw",
                battery_capacity_kwh=75.0,
                charging_loss_dc=1.0,
            ),
            _sub("vehicle", "dolphin", "Dolphin", default_tariff_id="free"),
        ],
    )
    return vehicles


def _device(hass: HomeAssistant, subentry_id: str) -> str:
    registry = dr.async_get(hass)
    for entry in hass.config_entries.async_entries(DOMAIN):
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
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
    assert err.value.translation_key == "error_cost_or_tariff"


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


async def test_charge_type_without_tariff_price_is_rejected(
    hass: HomeAssistant, entry
) -> None:
    # "Arbeitgeber" has only an AC price.
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass,
            "add_session",
            vehicle=_device(hass, "dolphin"),
            tariff=_device(hass, "free"),
            energy_kwh=10,
            charge_type="dc",
        )
    assert err.value.translation_key == "error_no_price"
    assert err.value.translation_placeholders == {
        "tariff": "Arbeitgeber",
        "charge_type": "DC",
    }


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


async def test_update_sets_and_clears_soc(hass: HomeAssistant, entry) -> None:
    await _book(hass, "seal", "enbw")
    result = await _call(
        hass, "update_session", vehicle=_device(hass, "seal"), soc_start=33, soc_end=55
    )
    assert (result["session"]["soc_start"], result["session"]["soc_end"]) == (
        33.0,
        55.0,
    )
    result = await _call(
        hass, "update_session", vehicle=_device(hass, "seal"), clear_soc_start=True
    )
    assert (result["session"]["soc_start"], result["session"]["soc_end"]) == (
        None,
        55.0,
    )
    result = await _call(
        hass, "update_session", vehicle=_device(hass, "seal"), clear_soc_end=True
    )
    assert (result["session"]["soc_start"], result["session"]["soc_end"]) == (
        None,
        None,
    )
    assert result["session"]["cost"] == 23.60  # costs untouched


@pytest.mark.parametrize("name", ["soc_start", "soc_end"])
async def test_update_soc_and_clear_soc_conflict(
    hass: HomeAssistant, entry, name: str
) -> None:
    await _book(hass, "seal", "enbw")
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass,
            "update_session",
            vehicle=_device(hass, "seal"),
            **{name: 40, f"clear_{name}": True},
        )
    assert err.value.translation_key == f"{name}_conflict"


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


async def test_services_removed_when_both_entries_unloaded(
    hass: HomeAssistant, entry
) -> None:
    assert hass.services.has_service(DOMAIN, "add_session")
    assert await hass.config_entries.async_unload(entry.entry_id)
    # Tariffs entry still loaded: actions stay (list_sessions still works).
    assert hass.services.has_service(DOMAIN, "add_session")
    tariffs = hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, "tariffs")
    assert await hass.config_entries.async_unload(tariffs.entry_id)
    assert not hass.services.has_service(DOMAIN, "add_session")


# --- dashboard input ----------------------------------------------------------


async def test_input_preselects_default_tariff(hass: HomeAssistant, entry) -> None:
    seal = hass.states.get(_entity(hass, "select", "seal", "input_tariff"))
    dolphin = hass.states.get(_entity(hass, "select", "dolphin", "input_tariff"))
    assert seal.state == "EnBW"
    assert dolphin.state == "Arbeitgeber"
    assert seal.attributes["options"] == ["Arbeitgeber", "EnBW", "EWE Go"]
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
    assert err.value.translation_key == "error_energy_required"
    assert entry.runtime_data.log.sessions() == []


async def test_reset_button_clears_input(hass: HomeAssistant, entry) -> None:
    cost = _entity(hass, "number", "seal", "input_cost")
    tariff = _entity(hass, "select", "seal", "input_tariff")
    await _set(hass, "number", cost, value=12.34)
    await _set(hass, "select", tariff, option="Arbeitgeber")
    await _press(hass, _entity(hass, "button", "seal", "reset_input"))
    assert hass.states.get(cost).state == "unknown"
    assert hass.states.get(tariff).state == "EnBW"


async def test_tariff_choice_aligns_charge_type(hass: HomeAssistant, entry) -> None:
    tariff = _entity(hass, "select", "seal", "input_tariff")
    charge = _entity(hass, "select", "seal", "input_charge_type")

    await _set(hass, "select", tariff, option="EWE Go")  # DC only
    assert hass.states.get(charge).state == "dc"

    await _set(hass, "select", tariff, option="EnBW")  # AC and DC: keep DC
    assert hass.states.get(charge).state == "dc"

    await _set(hass, "select", tariff, option="Arbeitgeber")  # AC only
    assert hass.states.get(charge).state == "ac"


async def test_dc_only_default_tariff_presets_dc(hass: HomeAssistant, entry) -> None:
    hass.config_entries.async_update_subentry(
        entry,
        entry.subentries["dolphin"],
        data={**entry.subentries["dolphin"].data, "default_tariff_id": "ewe"},
    )
    await hass.async_block_till_done()  # reloads the vehicles entry
    charge = _entity(hass, "select", "dolphin", "input_charge_type")
    assert hass.states.get(charge).state == "dc"

    await _set(hass, "select", charge, option="ac")  # explicit choice is kept
    assert hass.states.get(charge).state == "ac"
    await _press(hass, _entity(hass, "button", "dolphin", "reset_input"))
    assert hass.states.get(charge).state == "dc"


async def test_book_dc_only_tariff_from_dashboard(hass: HomeAssistant, entry) -> None:
    await _set(
        hass, "number", _entity(hass, "number", "seal", "input_energy"), value=30
    )
    await _set(
        hass, "select", _entity(hass, "select", "seal", "input_tariff"), option="EWE Go"
    )
    await _press(hass, _entity(hass, "button", "seal", "book"))
    session = entry.runtime_data.log.last_session("seal")
    assert session.charge_type == "dc"
    assert session.cost == 18.60


async def _set_date(hass: HomeAssistant, entity_id: str, day: str) -> None:
    await hass.services.async_call(
        "date", "set_value", {"entity_id": entity_id, "date": day}, blocking=True
    )


async def test_book_with_entered_date_keeps_time_of_day(
    hass: HomeAssistant, entry, freezer
) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2026-11-15 17:30:00+00:00")  # 18:30 in Berlin
    day = _entity(hass, "date", "seal", "input_date")
    assert hass.states.get(day).state == "unknown"

    await _set_date(hass, day, "2026-11-14")
    await _set(
        hass, "number", _entity(hass, "number", "seal", "input_energy"), value=20
    )
    await _press(hass, _entity(hass, "button", "seal", "book"))

    session = entry.runtime_data.log.last_session("seal")
    assert session.timestamp.isoformat() == "2026-11-14T18:30:00+01:00"
    assert hass.states.get(day).state == "unknown"  # cleared after booking


async def test_book_without_date_uses_now(hass: HomeAssistant, entry) -> None:
    before = dt_util.utcnow()
    await _set(
        hass, "number", _entity(hass, "number", "seal", "input_energy"), value=20
    )
    await _press(hass, _entity(hass, "button", "seal", "book"))
    session = entry.runtime_data.log.last_session("seal")
    assert before <= session.timestamp <= dt_util.utcnow()


async def test_reset_clears_date(hass: HomeAssistant, entry) -> None:
    day = _entity(hass, "date", "seal", "input_date")
    await _set_date(hass, day, "2026-11-14")
    assert hass.states.get(day).state == "2026-11-14"
    await _press(hass, _entity(hass, "button", "seal", "reset_input"))
    assert hass.states.get(day).state == "unknown"


async def test_legacy_time_input_is_removed(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    legacy = registry.async_get_or_create("datetime", DOMAIN, "seal_input_time")
    await setup_entries(
        hass,
        [
            _sub("tariff", "enbw", "EnBW", price_ac=0.59, price_dc=0.69),
            _sub(
                "vehicle",
                "seal",
                "Seal U",
                default_tariff_id="enbw",
                battery_capacity_kwh=75.0,
                charging_loss_dc=1.0,
            ),
        ],
    )
    assert registry.async_get(legacy.entity_id) is None


async def test_input_is_per_vehicle(hass: HomeAssistant, entry) -> None:
    await _set(
        hass, "number", _entity(hass, "number", "seal", "input_energy"), value=40
    )
    dolphin = hass.states.get(_entity(hass, "number", "dolphin", "input_energy"))
    assert dolphin.state == "unknown"


async def test_cost_input_uses_ha_currency(hass: HomeAssistant, entry) -> None:
    cost = hass.states.get(_entity(hass, "number", "seal", "input_cost"))
    assert cost.attributes["unit_of_measurement"] == hass.config.currency


async def test_add_session_uses_price_version_of_its_day(
    hass: HomeAssistant, entry
) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    tariffs = hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, "tariffs")
    enbw = tariffs.subentries["enbw"]
    hass.config_entries.async_update_subentry(
        tariffs,
        enbw,
        data={
            **enbw.data,
            "versions": [
                {"valid_from": "2027-01-01", "price_ac": 0.62, "price_dc": 0.72}
            ],
        },
    )
    await hass.async_block_till_done()

    async def book(timestamp: str) -> dict[str, Any]:
        result = await _call(
            hass,
            "add_session",
            vehicle=_device(hass, "seal"),
            tariff=_device(hass, "enbw"),
            energy_kwh=40,
            timestamp=timestamp,
        )
        return result["session"]

    before = await book("2026-12-31T20:00:00+01:00")
    after = await book("2027-01-01T08:00:00+01:00")
    assert (before["price_per_kwh"], before["cost"]) == (0.59, 23.60)
    assert (after["price_per_kwh"], after["cost"]) == (0.62, 24.80)


async def test_price_version_uses_local_day(hass: HomeAssistant, entry) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    tariffs = hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, "tariffs")
    enbw = tariffs.subentries["enbw"]
    hass.config_entries.async_update_subentry(
        tariffs,
        enbw,
        data={
            **enbw.data,
            "versions": [{"valid_from": "2027-01-01", "price_ac": 0.62}],
        },
    )
    await hass.async_block_till_done()
    result = await _call(
        hass,
        "add_session",
        vehicle=_device(hass, "seal"),
        tariff=_device(hass, "enbw"),
        energy_kwh=40,
        timestamp="2026-12-31T23:30:00+00:00",  # 00:30 on Jan 1 in Berlin
    )
    assert result["session"]["price_per_kwh"] == 0.62
    assert result["session"]["timestamp"] == "2027-01-01T00:30:00+01:00"


# --- energy from SoC ----------------------------------------------------------


async def test_add_session_without_energy_uses_soc(hass: HomeAssistant, entry) -> None:
    result = await _call(
        hass,
        "add_session",
        vehicle=_device(hass, "seal"),
        tariff=_device(hass, "enbw"),
        soc_start=20,
        soc_end=80,
        timestamp=T0,
    )
    session = result["session"]
    assert session["energy_kwh"] == 45.0  # 60 % of 75 kWh
    assert session["cost"] == 26.55  # 45 kWh x 0.59
    assert session["cost_overridden"] is False
    state = hass.states.get(_entity(hass, "sensor", "seal", "total_energy"))
    assert float(state.state) == 45.0


async def test_entered_energy_wins_over_soc(hass: HomeAssistant, entry) -> None:
    session = await _book(hass, "seal", "enbw", soc_start=20, soc_end=80)
    assert session["energy_kwh"] == 40.0


async def test_energy_from_soc_with_manual_cost(hass: HomeAssistant, entry) -> None:
    result = await _call(
        hass,
        "add_session",
        vehicle=_device(hass, "seal"),
        soc_start=50,
        soc_end=70,
        cost=0,
    )
    assert result["session"]["energy_kwh"] == 15.0
    assert result["session"]["cost"] == 0.0
    assert result["session"]["cost_overridden"] is True


@pytest.mark.parametrize(
    ("vehicle", "soc", "key"),
    [
        ("seal", {}, "error_energy_required"),
        ("seal", {"soc_end": 80}, "error_energy_required"),
        ("seal", {"soc_start": 80, "soc_end": 40}, "error_energy_soc_order"),
        ("dolphin", {"soc_start": 20, "soc_end": 80}, "error_energy_soc_capacity"),
    ],
)
async def test_add_session_without_energy_is_rejected(
    hass: HomeAssistant, entry, vehicle: str, soc: dict[str, float], key: str
) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "add_session", vehicle=_device(hass, vehicle), cost=5, **soc)
    assert err.value.translation_key == key
    assert entry.runtime_data.log.sessions() == []


async def test_update_energy_from_soc(hass: HomeAssistant, entry) -> None:
    await _book(hass, "seal", "enbw", soc_start=20, soc_end=80)
    result = await _call(
        hass, "update_session", vehicle=_device(hass, "seal"), energy_from_soc=True
    )
    assert result["session"]["energy_kwh"] == 45.0
    assert result["session"]["cost"] == 26.55


async def test_update_energy_from_changed_soc(hass: HomeAssistant, entry) -> None:
    await _book(hass, "seal", "enbw", soc_start=20, soc_end=80)
    result = await _call(
        hass,
        "update_session",
        vehicle=_device(hass, "seal"),
        energy_from_soc=True,
        soc_end=60,
    )
    assert result["session"]["energy_kwh"] == 30.0  # 40 % of 75 kWh
    assert result["session"]["soc_end"] == 60.0
    assert result["session"]["cost"] == 17.70


async def test_update_energy_from_soc_keeps_manual_cost(
    hass: HomeAssistant, entry
) -> None:
    await _book(hass, "seal", "enbw", soc_start=20, soc_end=80, cost=9.99)
    result = await _call(
        hass, "update_session", vehicle=_device(hass, "seal"), energy_from_soc=True
    )
    assert result["session"]["energy_kwh"] == 45.0
    assert result["session"]["cost"] == 9.99


async def test_update_energy_from_cleared_soc_is_rejected(
    hass: HomeAssistant, entry
) -> None:
    await _book(hass, "seal", "enbw", soc_start=20, soc_end=80)
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass,
            "update_session",
            vehicle=_device(hass, "seal"),
            energy_from_soc=True,
            clear_soc_start=True,
        )
    assert err.value.translation_key == "error_energy_required"
    assert entry.runtime_data.log.last_session("seal").energy_kwh == 40.0


async def test_update_energy_and_energy_from_soc_conflict(
    hass: HomeAssistant, entry
) -> None:
    await _book(hass, "seal", "enbw", soc_start=20, soc_end=80)
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass,
            "update_session",
            vehicle=_device(hass, "seal"),
            energy_kwh=10,
            energy_from_soc=True,
        )
    assert err.value.translation_key == "energy_conflict"


async def test_book_from_dashboard_with_soc_only(hass: HomeAssistant, entry) -> None:
    await _set(
        hass, "number", _entity(hass, "number", "seal", "input_soc_start"), value=10
    )
    await _set(
        hass, "number", _entity(hass, "number", "seal", "input_soc_end"), value=50
    )
    await _press(hass, _entity(hass, "button", "seal", "book"))
    session = entry.runtime_data.log.last_session("seal")
    assert session.energy_kwh == 30.0  # 40 % of 75 kWh
    assert session.cost == 17.70


async def test_energy_from_soc_adds_charging_loss_of_charge_type(
    hass: HomeAssistant, entry
) -> None:
    result = await _call(
        hass,
        "add_session",
        vehicle=_device(hass, "seal"),
        tariff=_device(hass, "enbw"),
        charge_type="dc",
        soc_start=20,
        soc_end=80,
    )
    assert result["session"]["energy_kwh"] == 45.45  # 45 kWh + 1 % DC loss
    assert result["session"]["cost"] == 31.36  # 45.45 kWh x 0.69


async def test_update_energy_from_soc_uses_loss_of_new_charge_type(
    hass: HomeAssistant, entry
) -> None:
    await _book(hass, "seal", "enbw", soc_start=20, soc_end=80)  # AC, no loss
    result = await _call(
        hass,
        "update_session",
        vehicle=_device(hass, "seal"),
        charge_type="dc",
        energy_from_soc=True,
    )
    assert result["session"]["energy_kwh"] == 45.45
    assert result["session"]["price_per_kwh"] == 0.69
    assert result["session"]["cost"] == 31.36
