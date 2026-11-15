"""Tests for session management and the storage format in storage.py."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ev_charge_log.models import (
    SessionInput,
    Tariff,
    ValidationError,
)
from custom_components.ev_charge_log.storage import (
    STORAGE_MINOR_VERSION,
    STORAGE_VERSION,
    SessionLog,
    SessionNotFoundError,
    migrate,
)

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

ENBW = Tariff(id="enbw", name="EnBW", price_ac=0.59, price_dc=0.69)
IONITY = Tariff(id="ionity", name="Ionity", price_ac=0.69, price_dc=0.79)
FREE = Tariff(id="free", name="Employer", price_ac=0.0)
TARIFFS = {t.id: t for t in (ENBW, IONITY, FREE)}


def _log() -> SessionLog:
    counter = itertools.count(1)
    return SessionLog(id_factory=lambda: f"s{next(counter)}")


def _input(**kwargs) -> SessionInput:
    defaults = {
        "vehicle_id": "seal_u",
        "energy_kwh": 40.0,
        "charge_type": "ac",
        "timestamp": T0,
        "tariff_id": "enbw",
    }
    defaults.update(kwargs)
    return SessionInput(**defaults)


# --- add / query ----------------------------------------------------------


def test_add_books_with_current_price() -> None:
    log = _log()
    session = log.add(_input(), TARIFFS)
    assert session.id == "s1"
    assert session.cost == 23.60
    assert log.get("s1") == session


def test_add_unknown_tariff_is_rejected() -> None:
    log = _log()
    with pytest.raises(ValidationError):
        log.add(_input(tariff_id="missing"), TARIFFS)
    assert log.sessions() == []


def test_sessions_sorted_and_filtered() -> None:
    log = _log()
    log.add(_input(timestamp=T0 + timedelta(days=2)), TARIFFS)
    log.add(_input(timestamp=T0), TARIFFS)
    log.add(_input(vehicle_id="dolphin", tariff_id="free"), TARIFFS)

    assert [s.id for s in log.sessions(vehicle_id="seal_u")] == ["s2", "s1"]
    assert [s.id for s in log.sessions(start=T0 + timedelta(days=1))] == ["s1"]
    assert len(log.sessions(end=T0)) == 2


def test_last_session_uses_timestamp_not_booking_order() -> None:
    log = _log()
    log.add(_input(timestamp=T0 + timedelta(days=1)), TARIFFS)
    log.add(_input(timestamp=T0), TARIFFS)  # booked later, but older
    last = log.last_session("seal_u")
    assert last is not None
    assert last.id == "s1"


def test_summary_per_vehicle_and_tariff() -> None:
    log = _log()
    log.add(_input(), TARIFFS)
    log.add(_input(energy_kwh=20.0, charge_type="dc"), TARIFFS)
    log.add(_input(vehicle_id="dolphin", tariff_id="free"), TARIFFS)

    seal = log.summary_for_vehicle("seal_u")
    assert seal.total_cost == 37.40
    assert seal.session_count == 2
    assert log.summary_for_tariff("free").total_energy_kwh == 40.0
    assert log.summary_for_tariff("free").total_cost == 0


# --- tariff change does not affect booked sessions ------------------------


def test_tariff_price_change_keeps_old_sessions() -> None:
    log = _log()
    log.add(_input(), TARIFFS)
    raised = {**TARIFFS, "enbw": Tariff("enbw", "EnBW", price_ac=0.79)}
    log.add(_input(), raised)

    first, second = log.sessions()
    assert first.cost == 23.60
    assert second.cost == 31.60
    assert log.summary_for_tariff("enbw").total_cost == 55.20


# --- update ---------------------------------------------------------------


def test_update_energy_keeps_stored_price() -> None:
    log = _log()
    log.add(_input(), TARIFFS)
    raised = {**TARIFFS, "enbw": Tariff("enbw", "EnBW", price_ac=0.99)}
    updated = log.update("seal_u", raised, energy_kwh=50.0)
    assert updated.price_per_kwh == 0.59
    assert updated.cost == 29.50


def test_update_tariff_uses_new_tariff_current_price() -> None:
    log = _log()
    log.add(_input(), TARIFFS)
    updated = log.update("seal_u", TARIFFS, tariff_id="ionity")
    assert updated.tariff_id == "ionity"
    assert updated.price_per_kwh == 0.69
    assert updated.cost == 27.60


def test_update_charge_type_uses_matching_current_price() -> None:
    log = _log()
    log.add(_input(), TARIFFS)
    updated = log.update("seal_u", TARIFFS, charge_type="dc")
    assert updated.price_per_kwh == 0.69
    assert updated.cost == 27.60


def test_update_same_charge_type_keeps_stored_price() -> None:
    log = _log()
    log.add(_input(), TARIFFS)
    raised = {**TARIFFS, "enbw": Tariff("enbw", "EnBW", price_ac=0.99)}
    updated = log.update("seal_u", raised, charge_type="ac", tariff_id="enbw")
    assert updated.price_per_kwh == 0.59


def test_update_charge_type_keeps_manual_cost() -> None:
    log = _log()
    log.add(_input(cost=19.99), TARIFFS)
    updated = log.update("seal_u", TARIFFS, charge_type="dc")
    assert updated.cost == 19.99
    assert updated.price_per_kwh == 0.69


def test_update_tariff_to_free() -> None:
    log = _log()
    log.add(_input(), TARIFFS)
    updated = log.update("seal_u", TARIFFS, tariff_id="free")
    assert updated.cost == 0.0
    assert updated.cost_overridden is False


def test_update_keeps_manual_cost() -> None:
    log = _log()
    log.add(_input(cost=19.99), TARIFFS)
    updated = log.update("seal_u", TARIFFS, energy_kwh=45.0)
    assert updated.cost == 19.99
    assert updated.cost_overridden is True


def test_update_sets_manual_cost_zero() -> None:
    log = _log()
    log.add(_input(), TARIFFS)
    updated = log.update("seal_u", TARIFFS, cost=0)
    assert updated.cost == 0.0
    assert updated.cost_overridden is True


def test_update_clearing_manual_cost_recalculates() -> None:
    log = _log()
    log.add(_input(cost=19.99), TARIFFS)
    updated = log.update("seal_u", TARIFFS, cost=None)
    assert updated.cost == 23.60
    assert updated.cost_overridden is False


def test_update_clearing_cost_without_tariff_is_rejected() -> None:
    log = _log()
    log.add(_input(tariff_id=None, cost=10.0), TARIFFS)
    with pytest.raises(ValidationError):
        log.update("seal_u", TARIFFS, cost=None)
    assert log.get("s1").cost == 10.0  # unchanged after failed update


def test_update_removing_tariff_without_cost_is_rejected() -> None:
    log = _log()
    log.add(_input(), TARIFFS)
    with pytest.raises(ValidationError):
        log.update("seal_u", TARIFFS, tariff_id=None)


def test_update_invalid_value_leaves_session_unchanged() -> None:
    log = _log()
    original = log.add(_input(), TARIFFS)
    with pytest.raises(ValidationError):
        log.update("seal_u", TARIFFS, energy_kwh=-5)
    assert log.get("s1") == original


def test_update_without_id_targets_last_session() -> None:
    log = _log()
    log.add(_input(timestamp=T0), TARIFFS)
    log.add(_input(timestamp=T0 + timedelta(hours=1)), TARIFFS)
    updated = log.update("seal_u", TARIFFS, note="corrected")
    assert updated.id == "s2"
    assert log.get("s1").note is None


def test_update_other_vehicles_session_is_rejected() -> None:
    log = _log()
    log.add(_input(), TARIFFS)
    with pytest.raises(SessionNotFoundError):
        log.update("dolphin", TARIFFS, "s1", note="x")


def test_update_changes_sums() -> None:
    log = _log()
    log.add(_input(), TARIFFS)
    log.add(_input(), TARIFFS)
    assert log.summary_for_vehicle("seal_u").total_cost == 47.20
    log.update("seal_u", TARIFFS, "s1", energy_kwh=10.0)
    summary = log.summary_for_vehicle("seal_u")
    assert summary.total_cost == 29.50
    assert summary.total_energy_kwh == 50.0


# --- delete ---------------------------------------------------------------


def test_delete_by_id_changes_sums() -> None:
    log = _log()
    log.add(_input(), TARIFFS)
    log.add(_input(energy_kwh=10.0), TARIFFS)
    log.delete("seal_u", "s1")
    summary = log.summary_for_vehicle("seal_u")
    assert summary.session_count == 1
    assert summary.total_cost == 5.90
    assert log.summary_for_tariff("enbw").total_energy_kwh == 10.0


def test_delete_without_id_removes_last_session() -> None:
    log = _log()
    log.add(_input(timestamp=T0), TARIFFS)
    log.add(_input(timestamp=T0 + timedelta(hours=1)), TARIFFS)
    deleted = log.delete("seal_u")
    assert deleted.id == "s2"
    assert [s.id for s in log.sessions()] == ["s1"]


def test_delete_without_sessions_raises() -> None:
    with pytest.raises(SessionNotFoundError):
        _log().delete("seal_u")


def test_delete_unknown_id_raises() -> None:
    log = _log()
    log.add(_input(), TARIFFS)
    with pytest.raises(SessionNotFoundError):
        log.delete("seal_u", "nope")


def test_remove_other_vehicles() -> None:
    log = _log()
    log.add(_input(), TARIFFS)
    log.add(_input(vehicle_id="dolphin"), TARIFFS)
    log.add(_input(vehicle_id="dolphin"), TARIFFS)
    assert log.remove_other_vehicles(["seal_u"]) == 2
    assert [s.vehicle_id for s in log.sessions()] == ["seal_u"]
    assert log.remove_other_vehicles(["seal_u"]) == 0


# --- persistence / migration ----------------------------------------------


def test_data_roundtrip() -> None:
    log = _log()
    log.add(_input(soc_start=20, soc_end=80, note="A7"), TARIFFS)
    log.add(_input(vehicle_id="dolphin", tariff_id="free"), TARIFFS)
    restored = SessionLog.from_data(log.to_data())
    assert restored.sessions() == log.sessions()


def test_from_empty_store() -> None:
    assert SessionLog.from_data(None).sessions() == []


def test_migrate_current_version_is_noop() -> None:
    data = {"sessions": [], "detection": {}}
    assert migrate(STORAGE_VERSION, STORAGE_MINOR_VERSION, data) == data


def test_migrate_1_1_adds_detection() -> None:
    session = {"id": "s1"}
    data = migrate(1, 1, {"sessions": [session]})
    assert data == {"sessions": [session], "detection": {}}


def test_migrate_future_version_is_rejected() -> None:
    with pytest.raises(ValueError):
        migrate(STORAGE_VERSION + 1, 1, {"sessions": []})
