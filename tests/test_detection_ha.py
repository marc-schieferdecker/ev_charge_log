"""Charge detection in Home Assistant: state tracking, reminder, booking."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.ev_charge_log.const import DOMAIN, EVENT_CHARGING_FINISHED
from custom_components.ev_charge_log.coordinator import STORAGE_KEY
from custom_components.ev_charge_log.storage import STORAGE_MINOR_VERSION

from .conftest import make_subentry as _sub
from .conftest import setup_entries

CHARGING = "binary_sensor.seal_laden"
SOC = "sensor.seal_ladezustand"
TRACKER = "device_tracker.seal_standort"
D_CHARGING = "binary_sensor.dolphin_laden"
D_TRACKER = "device_tracker.dolphin_standort"


@pytest.fixture
async def entry(hass: HomeAssistant) -> MockConfigEntry:
    """Vehicles entry; Seal U with detection, Dolphin excludes the home zone."""
    hass.states.async_set("zone.work", "0", {"friendly_name": "Arbeit"})
    hass.states.async_set(CHARGING, "off")
    hass.states.async_set(SOC, "30")
    hass.states.async_set(TRACKER, "home")
    hass.states.async_set(D_CHARGING, "off")
    hass.states.async_set(D_TRACKER, "home")
    vehicles, _ = await setup_entries(
        hass,
        [
            _sub("tariff", "enbw", "EnBW", price_ac=0.59, price_dc=0.69, zones=[]),
            _sub("tariff", "home", "Zuhause", price_ac=0.0, zones=["zone.home"]),
            _sub("tariff", "work", "Arbeitgeber", price_ac=0.0, zones=["zone.work"]),
            _sub(
                "vehicle",
                "seal",
                "Seal U",
                default_tariff_id="enbw",
                battery_capacity_kwh=87.0,
                charging_entity_id=CHARGING,
                soc_entity_id=SOC,
                location_entity_id=TRACKER,
                reminder_excluded_zones=[],
            ),
            _sub(
                "vehicle",
                "dolphin",
                "Dolphin",
                default_tariff_id="enbw",
                charging_entity_id=D_CHARGING,
                location_entity_id=D_TRACKER,
                reminder_excluded_zones=["zone.home"],
            ),
        ],
    )
    return vehicles


async def _states(hass: HomeAssistant, *steps: tuple[str, str]) -> None:
    for entity_id, state in steps:
        hass.states.async_set(entity_id, state)
        await hass.async_block_till_done()


def _entity(hass: HomeAssistant, platform: str, subentry_id: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{subentry_id}_{key}"
    )
    assert entity_id
    return entity_id


async def _book_dashboard(hass: HomeAssistant, vehicle: str, kwh: float) -> None:
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _entity(hass, "number", vehicle, "input_energy"), "value": kwh},
        blocking=True,
    )
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": _entity(hass, "button", vehicle, "book")},
        blocking=True,
    )


async def _charge(hass: HomeAssistant, start_soc: str, end_soc: str) -> None:
    await _states(hass, (SOC, start_soc), (CHARGING, "on"))
    await _states(hass, (SOC, end_soc), (CHARGING, "off"))


# --- reminder event -----------------------------------------------------------


async def test_charge_end_fires_reminder_with_zone_tariff(
    hass: HomeAssistant, entry
) -> None:
    events = async_capture_events(hass, EVENT_CHARGING_FINISHED)
    await _charge(hass, "31", "80")

    assert len(events) == 1
    data: dict[str, Any] = events[0].data
    assert data["vehicle_id"] == "seal"
    assert data["vehicle_name"] == "Seal U"
    assert data["device_id"]
    assert (data["soc_start"], data["soc_end"]) == (31.0, 80.0)
    assert data["zone"] == "zone.home"
    assert (data["tariff_id"], data["tariff_name"]) == ("home", "Zuhause")
    # Dashboard input preselects the zone's tariff.
    tariff = hass.states.get(_entity(hass, "select", "seal", "input_tariff"))
    assert tariff.state == "Zuhause"


async def test_zone_by_name(hass: HomeAssistant, entry) -> None:
    events = async_capture_events(hass, EVENT_CHARGING_FINISHED)
    await _states(hass, (TRACKER, "Arbeit"))
    await _charge(hass, "40", "60")
    assert events[0].data["zone"] == "zone.work"
    assert events[0].data["tariff_name"] == "Arbeitgeber"


async def test_unknown_location_reminds_without_tariff(
    hass: HomeAssistant, entry
) -> None:
    events = async_capture_events(hass, EVENT_CHARGING_FINISHED)
    await _states(hass, (TRACKER, "unavailable"))
    await _charge(hass, "40", "60")
    assert len(events) == 1
    assert events[0].data["zone"] is None
    assert events[0].data["tariff_id"] is None


async def test_outside_any_zone_reminds_without_tariff(
    hass: HomeAssistant, entry
) -> None:
    events = async_capture_events(hass, EVENT_CHARGING_FINISHED)
    await _states(hass, (TRACKER, "not_home"))
    await _charge(hass, "40", "60")
    assert events[0].data["zone"] is None


async def test_excluded_zone_sends_no_reminder(hass: HomeAssistant, entry) -> None:
    events = async_capture_events(hass, EVENT_CHARGING_FINISHED)
    await _states(hass, (D_CHARGING, "on"), (D_CHARGING, "off"))
    assert events == []
    # The charge is still remembered for the next booking.
    assert entry.runtime_data.detection["dolphin"].pending is not None


async def test_unavailable_does_not_start_a_new_charge(
    hass: HomeAssistant, entry
) -> None:
    events = async_capture_events(hass, EVENT_CHARGING_FINISHED)
    await _states(hass, (SOC, "31"), (CHARGING, "on"))
    await _states(hass, (SOC, "50"), (CHARGING, "unavailable"), (CHARGING, "on"))
    await _states(hass, (SOC, "80"), (CHARGING, "off"))
    assert len(events) == 1
    assert events[0].data["soc_start"] == 31.0


# --- booking ------------------------------------------------------------------


async def test_booking_takes_detected_values_once(hass: HomeAssistant, entry) -> None:
    await _charge(hass, "31", "80")
    await _states(hass, (SOC, "75"))  # driven since the charge ended
    ended = entry.runtime_data.detection["seal"].pending.ended_at

    await _book_dashboard(hass, "seal", 40)
    session = entry.runtime_data.log.last_session("seal")
    assert (session.soc_start, session.soc_end) == (31.0, 80.0)
    assert session.timestamp == ended
    assert session.source == "reminder"
    assert session.tariff_id == "home"  # zone tariff was preselected
    assert entry.runtime_data.detection["seal"].pending is None

    # The next booking without detection: only the current SoC, manual.
    await _book_dashboard(hass, "seal", 10)
    second = max(entry.runtime_data.log.sessions(), key=lambda s: s.timestamp)
    assert (second.soc_start, second.soc_end) == (None, 75.0)
    assert second.source == "manual"


async def test_manual_soc_overrides_detected(hass: HomeAssistant, entry) -> None:
    await _charge(hass, "31", "80")
    device = (
        er.async_get(hass).async_get(_entity(hass, "button", "seal", "book")).device_id
    )
    result = await hass.services.async_call(
        DOMAIN,
        "add_session",
        {"vehicle": device, "energy_kwh": 40, "cost": 0, "soc_start": 25},
        blocking=True,
        return_response=True,
    )
    session = result["session"]
    assert (session["soc_start"], session["soc_end"]) == (25.0, 80.0)
    assert session["source"] == "reminder"


async def test_explicit_time_wins_over_detected_end(hass: HomeAssistant, entry) -> None:
    await _charge(hass, "31", "80")
    device = (
        er.async_get(hass).async_get(_entity(hass, "button", "seal", "book")).device_id
    )
    result = await hass.services.async_call(
        DOMAIN,
        "add_session",
        {
            "vehicle": device,
            "energy_kwh": 40,
            "cost": 0,
            "timestamp": "2026-11-01T10:00:00+00:00",
        },
        blocking=True,
        return_response=True,
    )
    booked = datetime.fromisoformat(result["session"]["timestamp"])
    assert booked == datetime(2026, 11, 1, 10, 0, tzinfo=UTC)


async def test_booking_while_charging_suppresses_reminder(
    hass: HomeAssistant, entry
) -> None:
    events = async_capture_events(hass, EVENT_CHARGING_FINISHED)
    await _states(hass, (SOC, "31"), (CHARGING, "on"), (SOC, "60"))
    await _book_dashboard(hass, "seal", 25)
    session = entry.runtime_data.log.last_session("seal")
    assert (session.soc_start, session.soc_end) == (31.0, 60.0)

    await _states(hass, (CHARGING, "off"))
    assert events == []
    assert entry.runtime_data.detection["seal"].pending is None


async def test_manual_booking_works_without_detection_entities(
    hass: HomeAssistant, entry
) -> None:
    """Dolphin has no SoC entity: booking works, SoC stays empty."""
    await _book_dashboard(hass, "dolphin", 12)
    session = entry.runtime_data.log.last_session("dolphin")
    assert (session.soc_start, session.soc_end) == (None, None)
    assert session.source == "manual"


# --- persistence --------------------------------------------------------------


async def _restart(hass: HomeAssistant) -> None:
    for e in hass.config_entries.async_entries(DOMAIN):
        assert await hass.config_entries.async_unload(e.entry_id)
    for e in hass.config_entries.async_entries(DOMAIN):
        assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()


async def test_restart_while_charging_keeps_start(
    hass: HomeAssistant, entry, hass_storage: dict[str, Any]
) -> None:
    events = async_capture_events(hass, EVENT_CHARGING_FINISHED)
    await _states(hass, (SOC, "31"), (CHARGING, "on"))
    stored = hass_storage[STORAGE_KEY]
    assert stored["minor_version"] == STORAGE_MINOR_VERSION
    assert stored["data"]["detection"]["seal"]["soc_start"] == 31.0

    await _states(hass, (SOC, "55"))
    await _restart(hass)
    coord = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert coord.detection["seal"].soc_start == 31.0

    await _states(hass, (SOC, "80"), (CHARGING, "off"))
    assert len(events) == 1
    assert events[0].data["soc_start"] == 31.0


async def test_charge_ended_while_ha_was_down(hass: HomeAssistant, entry) -> None:
    events = async_capture_events(hass, EVENT_CHARGING_FINISHED)
    await _states(hass, (SOC, "31"), (CHARGING, "on"))
    for e in hass.config_entries.async_entries(DOMAIN):
        assert await hass.config_entries.async_unload(e.entry_id)
    hass.states.async_set(SOC, "90")
    hass.states.async_set(CHARGING, "off")
    for e in hass.config_entries.async_entries(DOMAIN):
        assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert len(events) == 1
    assert (events[0].data["soc_start"], events[0].data["soc_end"]) == (31.0, 90.0)


async def test_deleting_vehicle_drops_its_detection_state(
    hass: HomeAssistant, entry, hass_storage: dict[str, Any]
) -> None:
    await _states(hass, (D_CHARGING, "on"))
    hass.config_entries.async_remove_subentry(entry, "dolphin")
    await hass.async_block_till_done()
    assert "dolphin" not in hass_storage[STORAGE_KEY]["data"]["detection"]


# --- notification without automation (stage 4) ---------------------------------


def _notifications(hass: HomeAssistant) -> dict[str, Any]:
    from homeassistant.components import persistent_notification

    return persistent_notification._async_get_or_create_notifications(hass)


async def test_charge_end_creates_notification(hass: HomeAssistant, entry) -> None:
    await _charge(hass, "31", "80")
    await hass.async_block_till_done()
    notes = _notifications(hass)
    note = notes["ev_charge_log_charge_seal"]
    assert "Seal U" in note["title"]
    assert "31 % → 80 %" in note["message"]
    assert "Zuhause" in note["message"]
    assert "/config/devices/device/" in note["message"]


async def test_booking_dismisses_notification(hass: HomeAssistant, entry) -> None:
    await _charge(hass, "31", "80")
    await hass.async_block_till_done()
    assert "ev_charge_log_charge_seal" in _notifications(hass)
    await _book_dashboard(hass, "seal", 40)
    assert "ev_charge_log_charge_seal" not in _notifications(hass)


async def test_excluded_zone_creates_no_notification(
    hass: HomeAssistant, entry
) -> None:
    await _states(hass, (D_CHARGING, "on"), (D_CHARGING, "off"))
    await hass.async_block_till_done()
    assert "ev_charge_log_charge_dolphin" not in _notifications(hass)


async def test_notification_in_german(hass: HomeAssistant, entry) -> None:
    hass.config.language = "de"
    await _charge(hass, "31", "80")
    await hass.async_block_till_done()
    note = _notifications(hass)["ev_charge_log_charge_seal"]
    assert note["title"] == "Seal U hat fertig geladen"
    assert "Ladung eintragen" in note["message"]
