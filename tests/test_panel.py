"""Tests for the sidebar panel registration and its websocket command."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components import frontend
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.ev_charge_log.const import DOMAIN
from custom_components.ev_charge_log.panel import (
    PANEL_COMPONENT,
    PANEL_URL_PATH,
    async_panel_data,
    vehicle_history,
)

from .conftest import make_subentry as _sub
from .conftest import setup_entries

SUBENTRIES = [
    _sub("tariff", "enbw", "EnBW", price_ac=0.59, price_dc=0.69),
    _sub("vehicle", "seal", "Seal U", default_tariff_id="enbw"),
    _sub("vehicle", "dolphin", "Dolphin"),
]


@pytest.fixture(autouse=True)
def frontend_loaded(hass: HomeAssistant) -> None:
    """Pretend the frontend is loaded (its package is not installed in tests)."""
    hass.config.components.add("frontend")


def _panels(hass: HomeAssistant) -> dict:
    return hass.data.get(frontend.DATA_PANELS, {})


async def test_no_panel_without_frontend(hass: HomeAssistant) -> None:
    hass.config.components.remove("frontend")
    await setup_entries(hass, SUBENTRIES)
    assert PANEL_URL_PATH not in _panels(hass)


async def test_panel_is_registered(hass: HomeAssistant) -> None:
    hass.config.language = "de"
    await setup_entries(hass, SUBENTRIES)
    panel = _panels(hass)[PANEL_URL_PATH]
    assert panel.sidebar_title == "Ladungen"
    assert panel.sidebar_icon == "mdi:ev-station"
    assert panel.config["_panel_custom"]["name"] == PANEL_COMPONENT
    assert panel.config["_panel_custom"]["module_url"].startswith(
        "/ev_charge_log_static/ev-charge-log-panel.js?v="
    )


async def test_panel_websocket_lists_vehicles(
    hass: HomeAssistant, hass_ws_client
) -> None:
    hass.config.language = "de"
    await setup_entries(hass, SUBENTRIES)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "ev_charge_log/panel"})
    response = await client.receive_json()
    assert response["success"]
    result = response["result"]

    assert [v["name"] for v in result["vehicles"]] == ["Dolphin", "Seal U"]
    seal = result["vehicles"][1]
    assert seal["id"] == "seal"
    inputs = [row.get("entity") for row in seal["input"]]
    assert len(inputs) == 7
    assert inputs[0].startswith("date.")
    assert inputs[6].startswith("text.")
    # Both SoC fields in one own row.
    soc = seal["input"][3]
    assert soc["type"] == "soc"
    assert soc["vehicle_id"] == "seal"
    assert soc["start"].startswith("number.")
    assert soc["end"].startswith("number.")
    assert (result["texts"]["panel_soc_start"], result["texts"]["panel_soc_end"]) == (
        "Beginn",
        "Ende",
    )
    inputs = [e for e in inputs if e]
    # No buttons among the inputs: "book" is sent separately.
    assert not any(e.startswith("button.") for e in inputs)
    assert seal["book"]["entity"].startswith("button.")
    assert seal["book"]["name"] == "Ladung buchen"
    # Short names without the vehicle name.
    assert [row["name"] for row in seal["input"]] == [
        "Datum",
        "Energie",
        "Betrag",
        "SoC",
        "Tarif",
        "Ladeart",
        "Notiz",
    ]
    assert seal["stats"][0]["entity"].startswith("sensor.")
    assert seal["stats"][0]["name"] == "Gesamtkosten"
    assert seal["stats"][2]["name"] == "Grundgebühren"
    assert len(seal["stats"]) == 5
    assert [row["name"] for row in seal["last"]] == [
        "Zeitpunkt",
        "Kosten",
        "Energie",
        "Tarif",
    ]
    assert result["texts"]["panel_last"] == "Letzte Ladung"
    assert result["config_url"] == "/config/integrations/integration/ev_charge_log"
    assert result["texts"]["panel_input"] == "Ladung eintragen"


async def test_panel_set_soc_websocket(hass: HomeAssistant, hass_ws_client) -> None:
    vehicles, _ = await setup_entries(hass, SUBENTRIES)
    coord = vehicles.runtime_data
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "ev_charge_log/panel_set_soc",
            "vehicle_id": "seal",
            "soc_start": 33,
            "soc_end": 0,
        }
    )
    assert (await client.receive_json())["success"]
    assert (coord.inputs["seal"].soc_start, coord.inputs["seal"].soc_end) == (33, 0)

    # null empties a field (number.set_value cannot), the other one stays.
    await client.send_json_auto_id(
        {"type": "ev_charge_log/panel_set_soc", "vehicle_id": "seal", "soc_start": None}
    )
    assert (await client.receive_json())["success"]
    assert (coord.inputs["seal"].soc_start, coord.inputs["seal"].soc_end) == (None, 0)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    start = registry.async_get_entity_id("number", DOMAIN, "seal_input_soc_start")
    assert hass.states.get(start).state == "unknown"

    for bad in (
        {"vehicle_id": "nope", "soc_end": 5},
        {"vehicle_id": "seal", "soc_end": 101},
    ):
        await client.send_json_auto_id({"type": "ev_charge_log/panel_set_soc", **bad})
        assert not (await client.receive_json())["success"]


async def test_panel_without_vehicles(hass: HomeAssistant, hass_ws_client) -> None:
    await setup_entries(hass, [_sub("tariff", "enbw", "EnBW", price_ac=0.59)])
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "ev_charge_log/panel"})
    result = (await client.receive_json())["result"]
    assert result["vehicles"] == []
    assert result["texts"]["panel_empty"].startswith("No vehicles")


async def test_panel_removed_when_both_entries_unloaded(hass: HomeAssistant) -> None:
    vehicles, tariffs = await setup_entries(hass, SUBENTRIES)
    assert await hass.config_entries.async_unload(vehicles.entry_id)
    assert PANEL_URL_PATH in _panels(hass)  # tariffs entry still loaded
    assert await hass.config_entries.async_unload(tariffs.entry_id)
    assert PANEL_URL_PATH not in _panels(hass)

    # Loading again registers it again.
    for entry in hass.config_entries.async_entries(DOMAIN):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert PANEL_URL_PATH in _panels(hass)


def test_panel_module_exists() -> None:
    module = (
        Path(__file__).parent.parent
        / "custom_components/ev_charge_log/frontend/ev-charge-log-panel.js"
    )
    source = module.read_text(encoding="utf-8")
    assert f'customElements.define("{PANEL_COMPONENT}"' in source
    assert '"ev_charge_log/panel"' in source


def test_panel_parts_exist() -> None:
    """Every part the panel module loads is shipped next to it."""
    frontend_dir = (
        Path(__file__).parent.parent / "custom_components/ev_charge_log/frontend"
    )
    source = (frontend_dir / "ev-charge-log-panel.js").read_text(encoding="utf-8")
    parts = re.findall(r'load\("([\w-]+)"\)', source)
    assert parts
    for part in parts:
        assert (frontend_dir / f"{part}.js").is_file(), part


async def test_notification_links_to_panel(hass: HomeAssistant) -> None:
    from homeassistant.components import persistent_notification

    hass.states.async_set("binary_sensor.seal_laden", "off")
    await setup_entries(
        hass,
        [
            _sub("tariff", "enbw", "EnBW", price_ac=0.59),
            _sub(
                "vehicle",
                "seal",
                "Seal U",
                charging_entity_id="binary_sensor.seal_laden",
            ),
        ],
    )
    hass.states.async_set("binary_sensor.seal_laden", "on")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.seal_laden", "off")
    await hass.async_block_till_done()

    notes = persistent_notification._async_get_or_create_notifications(hass)
    assert "(/ev-charge-log)" in notes["ev_charge_log_charge_seal"]["message"]


async def test_panel_history_by_month(hass: HomeAssistant, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2026-11-15 12:00:00+01:00")
    vehicles, _ = await setup_entries(hass, SUBENTRIES)
    coord = vehicles.runtime_data
    from datetime import datetime

    from custom_components.ev_charge_log.models import SessionInput

    tz = dt_util.get_default_time_zone()
    for day, kwh in ((15, 20.0), (15, 5.0), (1, 10.0), (1, 2.0)):
        await coord.async_add_session(
            SessionInput(
                vehicle_id="seal",
                energy_kwh=kwh,
                charge_type="ac",
                timestamp=datetime(2026, 11, day, 8, tzinfo=tz),
                tariff_id="enbw",
            )
        )
    # Previous month: not in the current month's chart.
    await coord.async_add_session(
        SessionInput(
            vehicle_id="seal",
            energy_kwh=99.0,
            charge_type="ac",
            timestamp=datetime(2026, 10, 1, 8, tzinfo=tz),
            tariff_id="enbw",
        )
    )

    # Direct call: a frozen clock would invalidate the websocket auth token.
    result = await async_panel_data(hass)
    seal = next(v for v in result["vehicles"] if v["id"] == "seal")
    history = seal["history"]
    # The whole current month, future days included (empty).
    assert history["month"] == "2026-11"
    assert history["current_month"] == "2026-11"
    assert history["first_month"] == "2026-10"
    assert len(history["days"]) == 30
    assert history["days"][-1] == "2026-11-30"
    assert history["energy"][history["days"].index("2026-11-15")] == 25.0
    assert history["cost"][history["days"].index("2026-11-15")] == 14.75
    assert history["energy"][0] == 12.0
    assert history["total_energy"] == 37.0
    assert result["currency"] == hass.config.currency
    assert len(seal["watch"]) == 4

    october = vehicle_history(coord, "seal", "2026-10")
    assert len(october["days"]) == 31
    assert october["total_energy"] == 99.0

    dolphin = next(v for v in result["vehicles"] if v["id"] == "dolphin")
    assert dolphin["history"]["total_energy"] == 0
    assert dolphin["history"]["first_month"] == "2026-11"  # no sessions


async def test_panel_lists_sessions_newest_first(hass: HomeAssistant) -> None:
    from datetime import datetime

    from custom_components.ev_charge_log.models import SessionInput

    await hass.config.async_set_time_zone("Europe/Berlin")
    vehicles, _ = await setup_entries(hass, SUBENTRIES)
    coord = vehicles.runtime_data
    tz = dt_util.get_default_time_zone()
    for day in range(1, 13):
        await coord.async_add_session(
            SessionInput(
                vehicle_id="seal",
                energy_kwh=float(day),
                charge_type="ac",
                timestamp=datetime(2026, 11, day, 18, 30, tzinfo=tz),
                tariff_id="enbw",
                cost=1.5 if day == 12 else None,
                note="A7" if day == 12 else None,
                soc_start=33.0 if day == 11 else None,
                soc_end=77.0 if day in (11, 12) else None,
            )
        )

    result = await async_panel_data(hass)
    seal = next(v for v in result["vehicles"] if v["id"] == "seal")
    assert seal["device_id"]
    assert len(seal["sessions"]) == 12
    newest = seal["sessions"][0]
    assert newest["timestamp"] == "2026-11-12T18:30:00+01:00"
    assert newest["energy_kwh"] == 12.0
    assert newest["tariff_id"] == "enbw"
    assert newest["tariff_name"] == "EnBW"
    assert newest["cost"] == 1.5
    assert newest["cost_overridden"] is True
    assert newest["note"] == "A7"
    assert newest["soc_start"] is None
    assert newest["soc_end"] == 77.0
    assert seal["sessions"][1]["soc_start"] == 33.0
    assert seal["sessions"][1]["soc_end"] == 77.0
    assert seal["sessions"][-1]["energy_kwh"] == 1.0
    assert seal["sessions"][-1]["soc_start"] is None
    assert seal["sessions"][-1]["soc_end"] is None

    assert result["tariffs"] == [
        {
            "id": "enbw",
            "name": "EnBW",
            "device_id": result["tariffs"][0]["device_id"],
            "price_ac": 0.59,
            "price_dc": 0.69,
            "monthly_fee": None,
            "reference_price_ac": None,
            "reference_price_dc": None,
        }
    ]
    assert result["tariffs"][0]["device_id"]
    assert result["texts"]["panel_save"] == "Save"


async def test_panel_list_is_limited(hass: HomeAssistant) -> None:
    from datetime import datetime, timedelta

    from custom_components.ev_charge_log.models import SessionInput
    from custom_components.ev_charge_log.panel import SESSION_LIST_LIMIT

    vehicles, _ = await setup_entries(hass, SUBENTRIES)
    coord = vehicles.runtime_data
    start = datetime(2026, 1, 1, tzinfo=dt_util.get_default_time_zone())
    for i in range(SESSION_LIST_LIMIT + 5):
        await coord.async_add_session(
            SessionInput(
                vehicle_id="dolphin",
                energy_kwh=1.0,
                charge_type="ac",
                timestamp=start + timedelta(hours=i),
                cost=0,
            )
        )
    result = await async_panel_data(hass)
    dolphin = next(v for v in result["vehicles"] if v["id"] == "dolphin")
    assert len(dolphin["sessions"]) == SESSION_LIST_LIMIT


async def test_panel_history_websocket(hass: HomeAssistant, hass_ws_client) -> None:
    await setup_entries(hass, SUBENTRIES)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "ev_charge_log/panel_history",
            "vehicle_id": "seal",
            "month": "2026-02",
        }
    )
    response = await client.receive_json()
    assert response["success"]
    assert response["result"]["month"] == "2026-02"
    assert len(response["result"]["days"]) == 28

    await client.send_json_auto_id(
        {
            "type": "ev_charge_log/panel_history",
            "vehicle_id": "nope",
            "month": "2026-02",
        }
    )
    assert not (await client.receive_json())["success"]

    await client.send_json_auto_id(
        {
            "type": "ev_charge_log/panel_history",
            "vehicle_id": "seal",
            "month": "2026-13",
        }
    )
    assert not (await client.receive_json())["success"]


async def test_panel_data_has_the_integration_version(hass: HomeAssistant) -> None:
    import json
    from pathlib import Path

    await setup_entries(hass, SUBENTRIES)
    manifest = json.loads(
        (
            Path(__file__).parent.parent
            / "custom_components/ev_charge_log/manifest.json"
        ).read_text(encoding="utf-8")
    )
    result = await async_panel_data(hass)
    assert result["version"] == manifest["version"]
    module_url = _panels(hass)[PANEL_URL_PATH].config["_panel_custom"]["module_url"]
    assert module_url.endswith(f"?v={manifest['version']}")


async def test_panel_sends_translated_error_messages(hass: HomeAssistant) -> None:
    hass.config.language = "de"
    await setup_entries(hass, SUBENTRIES)
    result = await async_panel_data(hass)
    errors = result["errors"]
    assert (
        errors["error_energy_required"]
        == "Bitte die geladene Energie oder Start- und End-SoC eingeben."
    )
    # Placeholders are filled by the panel.
    assert (
        errors["error_no_price"] == "Der Tarif {tariff} hat keinen {charge_type}-Preis."
    )


async def test_panel_tariff_stats(hass: HomeAssistant, freezer) -> None:
    from datetime import datetime

    from custom_components.ev_charge_log.models import SessionInput
    from custom_components.ev_charge_log.panel import tariff_stats

    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2026-11-15 12:00:00+01:00")
    vehicles, _ = await setup_entries(
        hass,
        [
            *SUBENTRIES,
            _sub(
                "tariff",
                "abo",
                "EnBW L",
                price_dc=0.49,
                monthly_fee=11.99,
                fee_vehicle_id="seal",
                fee_start="2026-10-01",
                reference_price_dc=0.79,
            ),
        ],
    )
    coord = vehicles.runtime_data
    tz = dt_util.get_default_time_zone()
    for vehicle_id, tariff_id, kwh in (
        ("seal", "abo", 40.0),
        ("dolphin", "abo", 10.0),
        ("seal", "enbw", 10.0),
    ):
        await coord.async_add_session(
            SessionInput(
                vehicle_id=vehicle_id,
                energy_kwh=kwh,
                charge_type="dc",
                timestamp=datetime(2026, 11, 3, 8, tzinfo=tz),
                tariff_id=tariff_id,
            )
        )

    result = await async_panel_data(hass)
    stats = result["tariff_stats"]
    assert (stats["month"], stats["current_month"]) == ("2026-11", "2026-11")
    assert stats["first_month"] == "2026-10"  # first fee
    enbw, abo = stats["rows"]  # sorted by name
    assert (abo["name"], abo["sessions"], abo["energy"]) == ("EnBW L", 2, 50.0)
    assert abo["fee"] == 11.99
    # Both vehicles: 50 x (0.79 - 0.49) - 11.99
    assert abo["saving"] == 3.01
    assert (enbw["name"], enbw["fee"], enbw["saving"]) == ("EnBW", None, None)
    assert stats["total"]["sessions"] == 3
    assert stats["total"]["saving"] == 3.01
    assert result["texts"]["panel_tariff_without"] == "No tariff"

    october = tariff_stats(coord, "2026-10")
    (row,) = october["rows"]
    assert (row["sessions"], row["saving"]) == (0, -11.99)


async def test_panel_tariff_stats_websocket(
    hass: HomeAssistant, hass_ws_client
) -> None:
    await setup_entries(hass, SUBENTRIES)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "ev_charge_log/panel_tariff_stats", "month": "2026-02"}
    )
    response = await client.receive_json()
    assert response["success"]
    assert response["result"]["month"] == "2026-02"
    assert response["result"]["rows"] == []

    await client.send_json_auto_id(
        {"type": "ev_charge_log/panel_tariff_stats", "month": "2026-13"}
    )
    assert not (await client.receive_json())["success"]


async def test_panel_marks_vehicle_with_pending_charge(hass: HomeAssistant) -> None:
    from datetime import UTC, datetime

    from custom_components.ev_charge_log.detection import (
        DetectionState,
        PendingCharge,
    )

    vehicles, _ = await setup_entries(hass, SUBENTRIES)
    coord = vehicles.runtime_data
    ended = datetime(2026, 11, 18, 7, 30, tzinfo=UTC)
    coord.detection["dolphin"] = DetectionState(
        last_state="off",
        pending=PendingCharge(
            started_at=None, ended_at=ended, soc_start=20.0, soc_end=80.0
        ),
    )
    result = await async_panel_data(hass)
    pending = {v["id"]: v["pending_ended_at"] for v in result["vehicles"]}
    assert pending == {"dolphin": ended.isoformat(), "seal": None}


async def test_panel_export_websocket(hass: HomeAssistant, hass_ws_client) -> None:
    from datetime import datetime

    from custom_components.ev_charge_log.models import SessionInput

    hass.config.language = "de"
    hass.config.currency = "EUR"
    await hass.config.async_set_time_zone("Europe/Berlin")
    vehicles, _ = await setup_entries(hass, SUBENTRIES)
    coord = vehicles.runtime_data
    tz = dt_util.get_default_time_zone()
    for day in (3, 10):
        await coord.async_add_session(
            SessionInput(
                vehicle_id="seal",
                energy_kwh=10.0,
                charge_type="ac",
                timestamp=datetime(2026, 11, day, 18, 30, tzinfo=tz),
                tariff_id="enbw",
            )
        )

    result = await async_panel_data(hass)
    seal = next(v for v in result["vehicles"] if v["id"] == "seal")
    dolphin = next(v for v in result["vehicles"] if v["id"] == "dolphin")
    assert seal["first_day"] == "2026-11-03"
    assert dolphin["first_day"] == dt_util.now().date().isoformat()
    assert "panel_export_title" in result["texts"]

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "ev_charge_log/panel_export",
            "vehicle_id": "seal",
            "start": "2026-11-05",
            "end": "2026-11-19",
            "decimal": ",",
        }
    )
    response = await client.receive_json()
    assert response["success"], response
    export = response["result"]
    assert export["filename"] == "ev-charge-log_seal-u_2026-11-05_2026-11-19.csv"
    lines = export["csv"].removeprefix("﻿").split("\r\n")
    assert lines[0].startswith("Typ;Datum;Uhrzeit;Energie (kWh);")
    assert "Kosten (EUR)" in lines[0]
    assert lines[1] == "Ladung;2026-11-10;18:30;10;AC;EnBW;0,59;5,90;nein;;;;;manuell"
    assert lines[2:] == [""]

    # Open range: everything, file name without dates.
    await client.send_json_auto_id(
        {"type": "ev_charge_log/panel_export", "vehicle_id": "seal"}
    )
    export = (await client.receive_json())["result"]
    assert export["filename"] == "ev-charge-log_seal-u.csv"
    assert export["csv"].count("\r\n") == 3
    assert export["csv"].split("\r\n")[0].count(",") == 13

    await client.send_json_auto_id(
        {
            "type": "ev_charge_log/panel_export",
            "vehicle_id": "seal",
            "start": "2026-11-19",
            "end": "2026-11-01",
        }
    )
    response = await client.receive_json()
    assert not response["success"]
    assert response["error"]["code"] == "error_export_range"

    await client.send_json_auto_id(
        {"type": "ev_charge_log/panel_export", "vehicle_id": "nope"}
    )
    response = await client.receive_json()
    assert response["error"]["code"] == "not_found"


async def test_panel_import_websocket(hass: HomeAssistant, hass_ws_client) -> None:
    hass.config.language = "de"
    hass.config.currency = "EUR"
    await hass.config.async_set_time_zone("Europe/Berlin")
    vehicles, _ = await setup_entries(hass, SUBENTRIES)
    coord = vehicles.runtime_data
    result = await async_panel_data(hass)
    assert "panel_import_title" in result["texts"]
    assert "error_import_value" in result["errors"]

    # English header although HA runs in German; one fee, one broken row.
    text = (
        "﻿Type,Date,Time,Energy (kWh),Charge type,Tariff,Cost (USD)\r\n"
        "Monthly fee,2026-11-01,,,,EnBW,11.99\r\n"
        "Session,2026-11-03,18:30,10,AC,EnBW,\r\n"
        "Session,2026-11-04,18:30,10,DC,Ionity,4.5\r\n"
        "Session,2026-11-05,18:30,ten,AC,EnBW,\r\n"
    )
    client = await hass_ws_client(hass)
    message = {"type": "ev_charge_log/panel_import", "vehicle_id": "seal", "csv": text}
    await client.send_json_auto_id(message)
    response = await client.receive_json()
    assert response["success"], response
    assert response["result"] == {
        "imported": 2,
        "duplicates": 0,
        "fees_skipped": 1,
        "errors": [
            {
                "row": 5,
                "key": "error_import_value",
                "placeholders": {"column": "Energy (kWh)", "value": "ten"},
            }
        ],
    }
    sessions = coord.log.sessions(vehicle_id="seal")
    assert [(s.tariff_id, s.cost) for s in sessions] == [("enbw", 5.9), (None, 4.5)]
    assert dt_util.as_local(sessions[0].timestamp).hour == 18
    await hass.async_block_till_done()
    count = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, "seal_session_count"
    )
    assert hass.states.get(count).state == "2"

    # The same file again: only duplicates.
    await client.send_json_auto_id(message)
    response = await client.receive_json()
    assert response["result"]["imported"] == 0
    assert response["result"]["duplicates"] == 2

    await client.send_json_auto_id({**message, "csv": "Foo;Bar\n1;2\n"})
    response = await client.receive_json()
    assert response["error"]["code"] == "error_import_header"

    await client.send_json_auto_id({**message, "vehicle_id": "nope"})
    response = await client.receive_json()
    assert response["error"]["code"] == "not_found"


async def test_panel_compare_websocket(hass: HomeAssistant, hass_ws_client) -> None:
    from datetime import datetime

    from custom_components.ev_charge_log.models import SessionInput

    await hass.config.async_set_time_zone("Europe/Berlin")
    vehicles, _ = await setup_entries(hass, SUBENTRIES)
    coord = vehicles.runtime_data
    tz = dt_util.get_default_time_zone()
    for vehicle_id, charge_type in (("seal", "ac"), ("seal", "dc"), ("dolphin", "ac")):
        await coord.async_add_session(
            SessionInput(
                vehicle_id=vehicle_id,
                energy_kwh=10.0,
                charge_type=charge_type,
                timestamp=datetime(2026, 8, 10, 18, 30, tzinfo=tz),
                tariff_id="enbw",
            )
        )

    result = await async_panel_data(hass)
    enbw = next(t for t in result["tariffs"] if t["id"] == "enbw")
    assert (enbw["price_ac"], enbw["price_dc"], enbw["monthly_fee"]) == (
        0.59,
        0.69,
        None,
    )
    assert "panel_compare_title" in result["texts"]

    client = await hass_ws_client(hass)
    message = {
        "type": "ev_charge_log/panel_compare",
        "start": "2026-08-01",
        "end": "2026-08-31",
        "vehicle_id": "seal",
        "tariff_id": None,
        "a": {"price_ac": 0.59, "price_dc": 0.69, "monthly_fee": None},
        "b": {"price_ac": 0.39, "price_dc": None, "monthly_fee": 5.99},
    }
    await client.send_json_auto_id(message)
    response = await client.receive_json()
    assert response["success"], response
    compare = response["result"]
    assert (compare["session_count"], compare["months"]) == (2, 1)
    assert compare["a"]["total"] == 12.8
    # AC 10 x 0.39, DC without price: its actual cost 6.90, plus the fee.
    assert compare["b"]["total"] == 16.79
    assert compare["b"]["missing_count"] == 1
    assert compare["difference"] == 3.99
    assert compare["actual_total"] == 12.8

    await client.send_json_auto_id({**message, "b": {"monthly_fee": 5.0}})
    response = await client.receive_json()
    assert response["error"]["code"] == "error_price_required"

    await client.send_json_auto_id({**message, "start": "2026-09-01"})
    response = await client.receive_json()
    assert response["error"]["code"] == "error_compare_range"

    for key in ("vehicle_id", "tariff_id"):
        await client.send_json_auto_id({**message, key: "nope"})
        response = await client.receive_json()
        assert response["error"]["code"] == "not_found"


async def test_panel_shows_odometer_below_total_energy(hass: HomeAssistant) -> None:
    hass.config.language = "de"
    hass.states.async_set(
        "sensor.seal_odometer", "12345", {"unit_of_measurement": "km"}
    )
    await setup_entries(
        hass,
        [
            _sub("tariff", "enbw", "EnBW", price_ac=0.59, price_dc=0.69),
            _sub(
                "vehicle",
                "seal",
                "Seal U",
                default_tariff_id="enbw",
                odometer_entity_id="sensor.seal_odometer",
            ),
            _sub("vehicle", "dolphin", "Dolphin"),
        ],
    )
    result = await async_panel_data(hass)
    seal, dolphin = (
        next(v for v in result["vehicles"] if v["id"] == key)
        for key in ("seal", "dolphin")
    )
    names = [row["name"] for row in seal["stats"]]
    assert names[1:3] == ["Gesamtenergie", "Kilometerstand"]
    assert seal["stats"][2]["entity"] == "sensor.seal_odometer"
    # Without an odometer sensor: no row.
    assert "Kilometerstand" not in [row["name"] for row in dolphin["stats"]]


async def _report_sessions(hass: HomeAssistant):
    """Three sessions in August and September 2026."""
    from datetime import datetime

    from custom_components.ev_charge_log.models import SessionInput

    hass.config.language = "de"
    hass.config.currency = "EUR"
    await hass.config.async_set_time_zone("Europe/Berlin")
    vehicles, _ = await setup_entries(hass, SUBENTRIES)
    coord = vehicles.runtime_data
    tz = dt_util.get_default_time_zone()
    for vehicle_id, charge_type, day, note in (
        ("seal", "ac", datetime(2026, 8, 10, 18, 30, tzinfo=tz), "Dienstlich"),
        ("seal", "dc", datetime(2026, 8, 11, 9, 0, tzinfo=tz), None),
        ("dolphin", "ac", datetime(2026, 9, 1, 7, 0, tzinfo=tz), None),
    ):
        await coord.async_add_session(
            SessionInput(
                vehicle_id=vehicle_id,
                energy_kwh=10.0,
                charge_type=charge_type,
                timestamp=day,
                tariff_id="enbw",
                note=note,
            )
        )
    return coord


async def test_panel_report_websocket(hass: HomeAssistant, hass_ws_client) -> None:
    await _report_sessions(hass)
    today = dt_util.now().date()
    # 2026 up to the current month while it runs, all 12 months afterwards.
    last_month = today.month if today.year == 2026 else 12
    data = await async_panel_data(hass)
    assert data["texts"]["panel_report"] == "Bericht"
    assert data["texts"]["panel_report_title_year"] == "Ladebericht {year}"

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "ev_charge_log/panel_report", "period": "2026"}
    )
    response = await client.receive_json()
    assert response["success"], response
    result = response["result"]
    assert result["current"] == (today.year == 2026)
    assert result["summary"]["sessions"] == 3
    assert result["summary"]["total"] == 18.7  # 5.90 + 6.90 + 5.90
    assert [row["key"] for row in result["periods"]] == [
        f"2026-{m:02d}" for m in range(1, last_month + 1)
    ]
    assert [row["name"] for row in result["vehicles"]] == ["Dolphin", "Seal U"]
    assert [row["key"] for row in result["charge_types"]] == ["ac", "dc"]
    assert result["sessions"] is None

    await client.send_json_auto_id(
        {
            "type": "ev_charge_log/panel_report",
            "period": "2026-08",
            "vehicle_id": "seal",
            "tariff_id": "enbw",
            "note": "dienst",
            "sessions": True,
        }
    )
    result = (await client.receive_json())["result"]
    assert result["fees_excluded"]
    assert result["vehicles"] is None
    assert [row["key"] for row in result["periods"]] == ["2026-08-10"]
    assert result["sessions"] == [
        {
            "id": result["sessions"][0]["id"],
            "date": "2026-08-10",
            "time": "18:30",
            "vehicle": "Seal U",
            "tariff": "EnBW",
            "charge_type": "ac",
            "energy": 10.0,
            "cost": 5.9,
            "soc_start": None,
            "soc_end": None,
            "note": "Dienstlich",
        }
    ]

    for message, code in (
        ({"period": str(today.year + 1)}, "error_report_period"),
        ({"period": "2026", "vehicle_id": "nope"}, "error_unknown_vehicle"),
        ({"period": "2026", "tariff_id": "nope"}, "error_unknown_tariff"),
        ({"period": "2026-13"}, "invalid_format"),
    ):
        await client.send_json_auto_id(
            {"type": "ev_charge_log/panel_report", **message}
        )
        response = await client.receive_json()
        assert not response["success"]
        assert response["error"]["code"] == code, message


async def test_panel_report_pdf_websocket(hass: HomeAssistant, hass_ws_client) -> None:
    import base64
    import io

    from pypdf import PdfReader

    await _report_sessions(hass)
    months = [
        "Januar",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    ]
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "ev_charge_log/panel_report_pdf",
            "period": "2026-08",
            "vehicle_id": "seal",
            "sessions": True,
            "format": {"decimal": ",", "group": ".", "months": months, "currency": "€"},
        }
    )
    response = await client.receive_json()
    assert response["success"], response
    assert response["result"]["filename"] == "ev-charge-log-2026-08-seal-u.pdf"
    pdf = base64.b64decode(response["result"]["pdf"])
    assert pdf.startswith(b"%PDF")
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "Ladebericht August 2026" in text
    assert "Seal U" in text
    assert "Kosten (€)" in text
    assert "12,80 €" in text  # 5.90 + 6.90 with the decimal comma

    # Without a format: English month names, decimal point, currency code.
    await client.send_json_auto_id(
        {"type": "ev_charge_log/panel_report_pdf", "period": "2026-08"}
    )
    response = await client.receive_json()
    assert response["success"], response
    assert response["result"]["filename"] == "ev-charge-log-2026-08.pdf"
    pdf = base64.b64decode(response["result"]["pdf"])
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "Ladebericht August 2026" in text
    assert "12.80 EUR" in text

    await client.send_json_auto_id(
        {
            "type": "ev_charge_log/panel_report_pdf",
            "period": str(dt_util.now().year + 1),
        }
    )
    response = await client.receive_json()
    assert response["error"]["code"] == "error_report_period"
