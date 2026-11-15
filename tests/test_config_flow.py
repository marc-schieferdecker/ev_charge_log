"""Tests for the config flow, the entry migration and the subentry flows."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData, UnknownHandler
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_charge_log.const import DOMAIN

from .conftest import make_subentry, setup_entries

NO_DETECTION = {
    "charging_entity_id": None,
    "soc_entity_id": None,
    "location_entity_id": None,
    "reminder_excluded_zones": [],
}


@pytest.fixture
async def entries(hass: HomeAssistant) -> tuple[MockConfigEntry, MockConfigEntry]:
    """Vehicles entry with one vehicle, tariffs entry with one tariff."""
    return await setup_entries(
        hass,
        [
            make_subentry("tariff", "enbw", "EnBW", price_ac=0.59, price_dc=0.69),
            make_subentry(
                "vehicle",
                "seal",
                "Seal U",
                battery_capacity_kwh=None,
                default_tariff_id=None,
            ),
        ],
    )


@pytest.fixture
def vehicles(entries) -> MockConfigEntry:
    return entries[0]


@pytest.fixture
def tariffs(entries) -> MockConfigEntry:
    return entries[1]


def _sub(entry: MockConfigEntry, title: str):
    return next(s for s in entry.subentries.values() if s.title == title)


async def _start(hass: HomeAssistant, entry, subentry_type: str, subentry_id=None):
    context: dict[str, Any] = {"source": SOURCE_USER}
    if subentry_id is not None:
        context = {"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id}
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, subentry_type), context=context
    )


async def _menu(hass: HomeAssistant, entry, subentry_id: str, choice: str):
    """Open the edit menu of a subentry and pick an option."""
    result = await _start(hass, entry, "tariff", subentry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": choice}
    )


# --- main entries -------------------------------------------------------------


async def test_user_flow_creates_both_entries(hass: HomeAssistant) -> None:
    hass.config.language = "de"
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries(DOMAIN)
    assert sorted((e.unique_id, e.title, e.data["kind"]) for e in entries) == [
        ("tariffs", "Ladetarife", "tariffs"),
        ("vehicles", "Fahrzeuge", "vehicles"),
    ]


async def test_english_titles(hass: HomeAssistant) -> None:
    hass.config.language = "en"
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    titles = sorted(e.title for e in hass.config_entries.async_entries(DOMAIN))
    assert titles == ["Tariffs", "Vehicles"]


async def test_no_third_entry(hass: HomeAssistant, entries) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_missing_entry_is_recreated(hass: HomeAssistant, entries) -> None:
    vehicles, tariffs = entries
    assert await hass.config_entries.async_remove(tariffs.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(vehicles.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, "tariffs")


async def test_subentry_types_per_entry(hass: HomeAssistant, vehicles, tariffs) -> None:
    with pytest.raises(UnknownHandler):
        await _start(hass, vehicles, "tariff")
    with pytest.raises(UnknownHandler):
        await _start(hass, tariffs, "vehicle")


# --- migration ----------------------------------------------------------------


async def test_migrate_single_entry_to_two(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.config.language = "de"
    hass_storage["ev_charge_log.sessions"] = {
        "version": 1,
        "minor_version": 1,
        "key": "ev_charge_log.sessions",
        "data": {
            "sessions": [
                {
                    "id": "s1",
                    "vehicle_id": "seal",
                    "timestamp": "2026-11-01T12:00:00+00:00",
                    "energy_kwh": 40.0,
                    "tariff_id": "enbw",
                    "charge_type": "ac",
                    "price_per_kwh": 0.59,
                    "cost": 23.6,
                    "cost_overridden": False,
                    "source": "manual",
                }
            ]
        },
    }
    old = MockConfigEntry(
        domain=DOMAIN,
        title="EV Charge Log",
        data={},
        version=1,
        subentries_data=[
            make_subentry("tariff", "enbw", "EnBW", price_ac=0.59, price_dc=0.69),
            make_subentry("vehicle", "seal", "Seal U", default_tariff_id="enbw"),
        ],
    )
    old.add_to_hass(hass)
    assert await hass.config_entries.async_setup(old.entry_id)
    await hass.async_block_till_done()

    assert old.version == 2
    assert old.title == "Fahrzeuge"
    assert old.unique_id == "vehicles"
    assert old.data == {"kind": "vehicles"}
    assert [s.subentry_id for s in old.subentries.values()] == ["seal"]

    new = hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, "tariffs")
    assert new is not None
    assert new.title == "Ladetarife"
    assert [s.subentry_id for s in new.subentries.values()] == ["enbw"]
    assert dict(new.subentries["enbw"].data) == {"price_ac": 0.59, "price_dc": 0.69}

    # The booked session survives and still finds its tariff and vehicle.
    coord = old.runtime_data
    assert [s.id for s in coord.log.sessions()] == ["s1"]
    assert coord.tariffs["enbw"].name == "EnBW"
    assert coord.inputs["seal"].tariff_id == "enbw"


# --- tariffs ------------------------------------------------------------------


async def test_add_tariff(hass: HomeAssistant, tariffs) -> None:
    result = await _start(hass, tariffs, "tariff")
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": " Ionity ", "price_ac": 0.69, "price_dc": 0.79}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = _sub(tariffs, "Ionity")
    assert dict(sub.data) == {
        "price_ac": 0.69,
        "price_dc": 0.79,
        "zones": [],
        "versions": [],
    }


async def test_add_free_tariff_without_dc_price(hass: HomeAssistant, tariffs) -> None:
    result = await _start(hass, tariffs, "tariff")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Arbeitgeber", "price_ac": 0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = dict(_sub(tariffs, "Arbeitgeber").data)
    assert data == {"price_ac": 0.0, "price_dc": None, "zones": [], "versions": []}


async def test_add_dc_only_tariff(hass: HomeAssistant, tariffs) -> None:
    result = await _start(hass, tariffs, "tariff")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "EWE Go", "price_dc": 0.62}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(_sub(tariffs, "EWE Go").data) == {
        "price_ac": None,
        "price_dc": 0.62,
        "zones": [],
        "versions": [],
    }


async def test_tariff_without_price_is_rejected(hass: HomeAssistant, tariffs) -> None:
    result = await _start(hass, tariffs, "tariff")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Nothing"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "price_required"}


async def test_negative_price_is_rejected(hass: HomeAssistant, tariffs) -> None:
    result = await _start(hass, tariffs, "tariff")
    with pytest.raises(InvalidData):
        await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"name": "Bad", "price_ac": -0.1}
        )


async def test_duplicate_tariff_name_is_rejected(hass: HomeAssistant, tariffs) -> None:
    result = await _start(hass, tariffs, "tariff")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "enbw", "price_ac": 0.5}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"name": "name_exists"}


async def test_reconfigure_tariff(hass: HomeAssistant, tariffs) -> None:
    result = await _menu(hass, tariffs, "enbw", "edit")
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "EnBW", "price_ac": 0.62}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert dict(tariffs.subentries["enbw"].data) == {
        "price_ac": 0.62,
        "price_dc": None,
        "zones": [],
        "versions": [],
    }


# --- vehicles -----------------------------------------------------------------


async def test_add_vehicle_with_default_tariff(hass: HomeAssistant, vehicles) -> None:
    result = await _start(hass, vehicles, "vehicle")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Dolphin", "battery_capacity_kwh": 60.4, "default_tariff_id": "enbw"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(_sub(vehicles, "Dolphin").data) == {
        "battery_capacity_kwh": 60.4,
        "default_tariff_id": "enbw",
        **NO_DETECTION,
    }


async def test_vehicle_form_offers_tariffs_of_other_entry(
    hass: HomeAssistant, vehicles
) -> None:
    result = await _start(hass, vehicles, "vehicle")
    schema = result["data_schema"].schema
    key = next(k for k in schema if k == "default_tariff_id")
    options = schema[key].config["options"]
    assert options == [{"value": "enbw", "label": "EnBW"}]


async def test_add_vehicle_minimal(hass: HomeAssistant, vehicles) -> None:
    result = await _start(hass, vehicles, "vehicle")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Dolphin"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(_sub(vehicles, "Dolphin").data) == {
        "battery_capacity_kwh": None,
        "default_tariff_id": None,
        **NO_DETECTION,
    }


async def test_add_vehicle_with_detection_entities(
    hass: HomeAssistant, vehicles
) -> None:
    result = await _start(hass, vehicles, "vehicle")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Dolphin",
            "charging_entity_id": "binary_sensor.byd_dolphin_laden",
            "soc_entity_id": "sensor.byd_dolphin_ladezustand",
            "location_entity_id": "device_tracker.byd_dolphin_standort",
            "reminder_excluded_zones": ["zone.home"],
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = dict(_sub(vehicles, "Dolphin").data)
    assert data["charging_entity_id"] == "binary_sensor.byd_dolphin_laden"
    assert data["soc_entity_id"] == "sensor.byd_dolphin_ladezustand"
    assert data["location_entity_id"] == "device_tracker.byd_dolphin_standort"
    assert data["reminder_excluded_zones"] == ["zone.home"]


async def test_charging_entity_must_be_binary_sensor(
    hass: HomeAssistant, vehicles
) -> None:
    result = await _start(hass, vehicles, "vehicle")
    with pytest.raises(InvalidData):
        await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {"name": "Dolphin", "charging_entity_id": "switch.charging"},
        )


# --- tariff zones ---------------------------------------------------------------


async def test_tariff_with_several_zones(hass: HomeAssistant, tariffs) -> None:
    result = await _start(hass, tariffs, "tariff")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Zuhause", "price_ac": 0, "zones": ["zone.home", "zone.ferienhaus"]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert _sub(tariffs, "Zuhause").data["zones"] == ["zone.home", "zone.ferienhaus"]


async def test_zone_of_other_tariff_is_rejected(hass: HomeAssistant, tariffs) -> None:
    hass.config_entries.async_update_subentry(
        tariffs,
        tariffs.subentries["enbw"],
        data={**tariffs.subentries["enbw"].data, "zones": ["zone.work"]},
    )
    await hass.async_block_till_done()
    result = await _start(hass, tariffs, "tariff")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Arbeitgeber", "price_ac": 0, "zones": ["zone.work"]},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"zones": "zone_taken"}
    assert result["description_placeholders"]["tariff"] == "EnBW"


async def test_reconfigure_keeps_own_zones(hass: HomeAssistant, tariffs) -> None:
    result = await _start(hass, tariffs, "tariff")
    await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Zuhause", "price_ac": 0, "zones": ["zone.home"]}
    )
    own = _sub(tariffs, "Zuhause")
    result = await _menu(hass, tariffs, own.subentry_id, "edit")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Zuhause", "price_ac": 0, "zones": ["zone.home"]}
    )
    assert result["type"] is FlowResultType.ABORT


async def test_blank_vehicle_name_is_rejected(hass: HomeAssistant, vehicles) -> None:
    result = await _start(hass, vehicles, "vehicle")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "   "}
    )
    assert result["errors"] == {"name": "name_required"}


async def test_reconfigure_vehicle_keeps_own_name(
    hass: HomeAssistant, vehicles
) -> None:
    result = await _start(hass, vehicles, "vehicle", "seal")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Seal U", "battery_capacity_kwh": 87.0}
    )
    assert result["type"] is FlowResultType.ABORT
    assert vehicles.subentries["seal"].data["battery_capacity_kwh"] == 87.0


async def test_rename_vehicle(hass: HomeAssistant, vehicles) -> None:
    result = await _start(hass, vehicles, "vehicle", "seal")
    await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Seal U AWD"}
    )
    assert vehicles.subentries["seal"].title == "Seal U AWD"


# --- price versions (stage 3) ---------------------------------------------------


async def _add_price(hass: HomeAssistant, tariffs, **values):
    result = await _menu(hass, tariffs, "enbw", "add_price")
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], values
    )


async def test_edit_menu_lists_prices(hass: HomeAssistant, tariffs) -> None:
    result = await _start(hass, tariffs, "tariff", "enbw")
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["edit", "add_price"]  # nothing to delete
    assert result["description_placeholders"]["prices"] == "—: AC 0.59 / DC 0.69"


async def test_add_price_version(hass: HomeAssistant, tariffs) -> None:
    result = await _add_price(
        hass, tariffs, valid_from="2027-01-01", price_ac=0.62, price_dc=0.72
    )
    assert result["type"] is FlowResultType.ABORT
    data = tariffs.subentries["enbw"].data
    assert (data["price_ac"], data["price_dc"]) == (0.59, 0.69)  # base unchanged
    assert data["versions"] == [
        {"valid_from": "2027-01-01", "price_ac": 0.62, "price_dc": 0.72}
    ]
    coord = tariffs.runtime_data
    tariff = coord.tariffs["enbw"]
    assert len(tariff.versions) == 1  # reloaded with the new version


async def test_add_price_version_duplicate_date(hass: HomeAssistant, tariffs) -> None:
    await _add_price(hass, tariffs, valid_from="2027-01-01", price_ac=0.62)
    result = await _add_price(hass, tariffs, valid_from="2027-01-01", price_ac=0.7)
    assert result["errors"] == {"valid_from": "date_exists"}


async def test_add_price_version_needs_a_price(hass: HomeAssistant, tariffs) -> None:
    result = await _add_price(hass, tariffs, valid_from="2027-01-01")
    assert result["errors"] == {"base": "price_required"}


async def test_delete_price_version(hass: HomeAssistant, tariffs) -> None:
    await _add_price(hass, tariffs, valid_from="2027-01-01", price_ac=0.62)
    await _add_price(hass, tariffs, valid_from="2027-07-01", price_ac=0.65)

    result = await _start(hass, tariffs, "tariff", "enbw")
    assert result["menu_options"] == ["edit", "add_price", "delete_price"]
    result = await _menu(hass, tariffs, "enbw", "delete_price")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"valid_from": "2027-01-01"}
    )
    assert result["type"] is FlowResultType.ABORT
    days = [v["valid_from"] for v in tariffs.subentries["enbw"].data["versions"]]
    assert days == ["2027-07-01"]


async def test_edit_corrects_the_version_valid_today(
    hass: HomeAssistant, tariffs, freezer
) -> None:
    freezer.move_to("2027-03-01 12:00:00+00:00")
    await _add_price(hass, tariffs, valid_from="2027-01-01", price_ac=0.62)
    await _add_price(hass, tariffs, valid_from="2027-07-01", price_ac=0.65)

    result = await _menu(hass, tariffs, "enbw", "edit")
    schema = result["data_schema"].schema
    suggested = {
        str(k): k.description["suggested_value"]
        for k in schema
        if k.description and "suggested_value" in k.description
    }
    assert suggested["price_ac"] == 0.62

    await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "EnBW", "price_ac": 0.63}
    )
    data = tariffs.subentries["enbw"].data
    assert data["price_ac"] == 0.59  # base untouched
    assert [v["price_ac"] for v in data["versions"]] == [0.63, 0.65]
