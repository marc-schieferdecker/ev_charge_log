"""Push reminders to phones (Companion app) with kWh reply."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.ev_charge_log.const import DOMAIN

from .conftest import make_subentry as _sub
from .conftest import setup_entries

SEAL_CHARGING = "binary_sensor.seal_laden"
DOLPHIN_CHARGING = "binary_sensor.dolphin_laden"


@pytest.fixture
async def phones(hass: HomeAssistant) -> dict[str, list]:
    return {
        "marc": async_mock_service(hass, "notify", "mobile_app_pixel_marc"),
        "frau": async_mock_service(hass, "notify", "mobile_app_pixel_frau"),
    }


@pytest.fixture
async def entries(hass: HomeAssistant, phones):
    hass.states.async_set(SEAL_CHARGING, "off")
    hass.states.async_set(DOLPHIN_CHARGING, "off")
    return await setup_entries(
        hass,
        [
            _sub("tariff", "enbw", "EnBW", price_ac=0.59, price_dc=0.69),
            _sub(
                "vehicle",
                "seal",
                "Seal U",
                default_tariff_id="enbw",
                charging_entity_id=SEAL_CHARGING,
                notify_services=["mobile_app_pixel_marc"],
            ),
            _sub(
                "vehicle",
                "dolphin",
                "Dolphin",
                default_tariff_id="enbw",
                charging_entity_id=DOLPHIN_CHARGING,
                notify_services=["mobile_app_pixel_frau"],
            ),
        ],
    )


async def _charge(hass: HomeAssistant, entity_id: str) -> None:
    hass.states.async_set(entity_id, "on")
    await hass.async_block_till_done()
    hass.states.async_set(entity_id, "off")
    await hass.async_block_till_done()


async def _reply(hass: HomeAssistant, vehicle_id: str, text: str) -> None:
    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": f"EV_CHARGE_LOG_KWH_{vehicle_id}", "reply_text": text},
    )
    await hass.async_block_till_done()


def _last(calls: list) -> dict[str, Any]:
    return dict(calls[-1].data)


async def test_charge_end_pushes_to_the_vehicles_phone_only(
    hass: HomeAssistant, entries, phones
) -> None:
    await _charge(hass, SEAL_CHARGING)
    assert len(phones["marc"]) == 1
    assert phones["frau"] == []

    push = _last(phones["marc"])
    assert push["title"] == "Seal U has finished charging"
    # Short hint that kWh and optionally the cost can be entered.
    assert "kWh, optionally the cost, e.g. 12.4 or 12.4 7.32" in push["message"]
    data = push["data"]
    assert data["tag"] == "ev_charge_log_seal"
    # Tapping the notification itself opens the panel (Android and iOS).
    assert data["clickAction"] == "/ev-charge-log"
    assert data["url"] == "/ev-charge-log"
    reply, open_panel = data["actions"]
    assert reply["action"] == "EV_CHARGE_LOG_KWH_seal"
    assert reply["behavior"] == "textInput"
    assert open_panel == {"action": "URI", "title": "Open", "uri": "/ev-charge-log"}


async def test_dolphin_goes_to_the_other_phone(
    hass: HomeAssistant, entries, phones
) -> None:
    await _charge(hass, DOLPHIN_CHARGING)
    assert phones["marc"] == []
    assert len(phones["frau"]) == 1


async def test_reply_books_the_charge(hass: HomeAssistant, entries, phones) -> None:
    vehicles, _ = entries
    await _charge(hass, SEAL_CHARGING)
    await _reply(hass, "seal", "12,4")

    session = vehicles.runtime_data.log.last_session("seal")
    assert session.energy_kwh == 12.4
    assert session.tariff_id == "enbw"
    assert session.cost == 7.32
    assert session.source == "reminder"

    confirmation = _last(phones["marc"])
    assert confirmation["message"] == "Booked: 12.4 kWh, 7.32 EUR (EnBW)."
    assert "actions" not in confirmation["data"]
    assert confirmation["data"]["tag"] == "ev_charge_log_seal"


async def test_reply_takes_the_note_of_the_input(
    hass: HomeAssistant, entries, phones
) -> None:
    vehicles, _ = entries
    note = er.async_get(hass).async_get_entity_id("text", DOMAIN, "seal_input_note")
    await hass.services.async_call(
        "text", "set_value", {"entity_id": note, "value": "dienstlich"}, blocking=True
    )
    await _charge(hass, SEAL_CHARGING)
    await _reply(hass, "seal", "12,4")

    assert vehicles.runtime_data.log.last_session("seal").note == "dienstlich"
    assert hass.states.get(note).state == ""  # input reset after booking


async def test_reply_twice_books_once(hass: HomeAssistant, entries, phones) -> None:
    vehicles, _ = entries
    await _charge(hass, SEAL_CHARGING)
    await _reply(hass, "seal", "12,4")
    await _reply(hass, "seal", "12,4")
    assert len(vehicles.runtime_data.log.sessions(vehicle_id="seal")) == 1
    assert _last(phones["marc"])["message"] == "This charge has already been booked."


async def test_invalid_reply_asks_again(hass: HomeAssistant, entries, phones) -> None:
    vehicles, _ = entries
    await _charge(hass, SEAL_CHARGING)
    await _reply(hass, "seal", "zwölf")
    assert vehicles.runtime_data.log.sessions() == []
    push = _last(phones["marc"])
    assert "zwölf" in push["message"]
    assert len(push["data"]["actions"]) == 2  # can answer again
    assert vehicles.runtime_data.detection["seal"].pending is not None


async def test_foreign_actions_are_ignored(
    hass: HomeAssistant, entries, phones
) -> None:
    vehicles, _ = entries
    await _charge(hass, SEAL_CHARGING)
    hass.bus.async_fire(
        "mobile_app_notification_action", {"action": "ALARM", "reply_text": "1"}
    )
    await _reply(hass, "unknown_vehicle", "12")
    assert vehicles.runtime_data.log.sessions() == []


async def test_booking_in_dashboard_clears_the_push(
    hass: HomeAssistant, entries, phones
) -> None:
    await _charge(hass, SEAL_CHARGING)
    registry = er.async_get(hass)
    await hass.services.async_call(
        "number",
        "set_value",
        {
            "entity_id": registry.async_get_entity_id(
                "number", DOMAIN, "seal_input_energy"
            ),
            "value": 20,
        },
        blocking=True,
    )
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": registry.async_get_entity_id("button", DOMAIN, "seal_book")},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert _last(phones["marc"]) == {
        "message": "clear_notification",
        "data": {"tag": "ev_charge_log_seal"},
    }


async def test_missing_phone_does_not_break_the_reminder(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set(SEAL_CHARGING, "off")
    vehicles, _ = await setup_entries(
        hass,
        [
            _sub("tariff", "enbw", "EnBW", price_ac=0.59),
            _sub(
                "vehicle",
                "seal",
                "Seal U",
                charging_entity_id=SEAL_CHARGING,
                notify_services=["mobile_app_gone"],
            ),
        ],
    )
    await _charge(hass, SEAL_CHARGING)
    assert vehicles.runtime_data.detection["seal"].pending is not None


async def test_vehicle_form_offers_phones(hass: HomeAssistant, entries, phones) -> None:
    vehicles, _ = entries
    result = await hass.config_entries.subentries.async_init(
        (vehicles.entry_id, "vehicle"), context={"source": SOURCE_USER}
    )
    schema = result["data_schema"].schema
    key = next(k for k in schema if k == "notify_services")
    assert schema[key].config["options"] == [
        "mobile_app_pixel_frau",
        "mobile_app_pixel_marc",
    ]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Zoe", "notify_services": ["mobile_app_pixel_frau"]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    zoe = next(s for s in vehicles.subentries.values() if s.title == "Zoe")
    assert zoe.data["notify_services"] == ["mobile_app_pixel_frau"]


async def test_reply_with_cost_overrides_tariff(
    hass: HomeAssistant, entries, phones
) -> None:
    vehicles, _ = entries
    await _charge(hass, SEAL_CHARGING)
    await _reply(hass, "seal", "12,4 kWh 9,99 €")

    session = vehicles.runtime_data.log.last_session("seal")
    assert session.energy_kwh == 12.4
    assert session.cost == 9.99
    assert session.cost_overridden is True
    assert session.tariff_id == "enbw"
    assert _last(phones["marc"])["message"] == "Booked: 12.4 kWh, 9.99 EUR (EnBW)."


async def test_reply_with_cost_zero(hass: HomeAssistant, entries, phones) -> None:
    vehicles, _ = entries
    await _charge(hass, SEAL_CHARGING)
    await _reply(hass, "seal", "20 0")
    session = vehicles.runtime_data.log.last_session("seal")
    assert (session.energy_kwh, session.cost) == (20.0, 0.0)
    assert session.cost_overridden is True


async def test_confirmation_also_opens_panel(
    hass: HomeAssistant, entries, phones
) -> None:
    await _charge(hass, SEAL_CHARGING)
    await _reply(hass, "seal", "12,4")
    assert _last(phones["marc"])["data"]["clickAction"] == "/ev-charge-log"


async def test_german_texts(hass: HomeAssistant, entries, phones) -> None:
    hass.config.language = "de"
    await _charge(hass, SEAL_CHARGING)
    push = _last(phones["marc"])
    assert "„Eintragen“: kWh, optional mit Betrag" in push["message"]
    assert push["data"]["actions"][0]["title"] == "Eintragen"
