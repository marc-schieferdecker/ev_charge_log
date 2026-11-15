"""Tests for the sidebar panel registration and its websocket command."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components import frontend
from homeassistant.core import HomeAssistant
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
    inputs = [row["entity"] for row in seal["input"]]
    assert len(inputs) == 6
    assert inputs[0].startswith("date.")
    assert [e for e in inputs if e.startswith("button.")] == [inputs[-1]]
    assert not any(e.endswith("reset_input") for e in inputs)
    # Short names without the vehicle name.
    assert [row["name"] for row in seal["input"]] == [
        "Datum (optional)",
        "Energie",
        "Betrag (optional)",
        "Tarif",
        "Ladeart",
        "Ladung buchen",
    ]
    assert seal["stats"][0]["entity"].startswith("sensor.")
    assert seal["stats"][0]["name"] == "Gesamtkosten"
    assert seal["stats"][2]["name"] == "Grundgebühren"
    assert seal["stats"][5]["name"] == "Letzte Ladung"
    assert len(seal["stats"]) == 9
    assert result["config_url"] == "/config/integrations/integration/ev_charge_log"
    assert result["texts"]["panel_input"] == "Ladung eintragen"


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
    assert seal["sessions"][-1]["energy_kwh"] == 1.0

    assert result["tariffs"] == [
        {"id": "enbw", "name": "EnBW", "device_id": result["tariffs"][0]["device_id"]}
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
