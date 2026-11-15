"""Tests for the sidebar panel registration and its websocket command."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components import frontend
from homeassistant.core import HomeAssistant

from custom_components.ev_charge_log.const import DOMAIN
from custom_components.ev_charge_log.panel import PANEL_COMPONENT, PANEL_URL_PATH

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
    assert len(inputs) == 7
    assert inputs[0].startswith("date.")
    assert inputs[-2:] == [e for e in inputs if e.startswith("button.")]
    # Short names without the vehicle name.
    assert [row["name"] for row in seal["input"]] == [
        "Datum (optional)",
        "Energie",
        "Betrag (optional)",
        "Tarif",
        "Ladeart",
        "Ladung buchen",
        "Eingabe zurücksetzen",
    ]
    assert seal["stats"][0]["entity"].startswith("sensor.")
    assert seal["stats"][0]["name"] == "Gesamtkosten"
    assert seal["stats"][4]["name"] == "Letzte Ladung"
    assert len(seal["stats"]) == 8
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
