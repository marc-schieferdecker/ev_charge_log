"""Tests for the annual and monthly report (stage 9), without Home Assistant."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.ev_charge_log.models import (
    Fee,
    Session,
    Tariff,
    ValidationError,
    summarize,
)
from custom_components.ev_charge_log.reports import report

BERLIN = ZoneInfo("Europe/Berlin")
TODAY = date(2026, 11, 22)
TARIFFS = {
    "enbw": Tariff(id="enbw", name="EnBW", price_ac=0.59, price_dc=0.69),
    "free": Tariff(id="free", name="Arbeitgeber", price_ac=0.0),
    "home": Tariff(id="home", name="zuhause", price_ac=0.0),
}
VEHICLES = {"seal": "Seal U", "dolphin": "Dolphin"}


def _day(session: Session) -> date:
    return session.timestamp.astimezone(BERLIN).date()


def _session(session_id: str, ts: datetime, **kwargs) -> Session:
    defaults = {
        "vehicle_id": "seal",
        "energy_kwh": 10.0,
        "charge_type": "ac",
        "cost": 5.9,
        "cost_overridden": False,
        "tariff_id": "enbw",
        "price_per_kwh": 0.59,
    }
    return Session(id=session_id, timestamp=ts, **{**defaults, **kwargs})


SESSIONS = [
    _session("jan", datetime(2026, 1, 10, 10, 0, tzinfo=UTC), note="Dienstlich"),
    _session(
        "mar_dc",
        datetime(2026, 3, 12, 10, 0, tzinfo=UTC),
        charge_type="dc",
        energy_kwh=40.0,
        cost=27.6,
        price_per_kwh=0.69,
    ),
    _session(
        "mar_free",
        datetime(2026, 3, 12, 15, 0, tzinfo=UTC),
        vehicle_id="dolphin",
        tariff_id="free",
        cost=0.0,
        price_per_kwh=0.0,
        note="dienstlich Kunde X",
    ),
    _session(
        "mar_gone",
        datetime(2026, 3, 20, 10, 0, tzinfo=UTC),
        tariff_id="deleted",
        cost=3.0,
        cost_overridden=True,
        note="privat",
    ),
    _session(
        "nov", datetime(2026, 11, 2, 10, 0, tzinfo=UTC), energy_kwh=20.0, cost=11.8
    ),
]
FEES = [
    Fee("enbw", "seal", "2026-03", 4.99),
    Fee("enbw", "seal", "2026-04", 4.99),  # month without a session
    Fee("deleted", "seal", "2026-03", 1.0),
    Fee("enbw", "seal", "2025-12", 4.99),  # before the year
]


def _report(period: str = "2026", **kwargs):
    return report(SESSIONS, FEES, TARIFFS, VEHICLES, period, _day, TODAY, **kwargs)


def test_annual_report_summary() -> None:
    result = _report()
    s = result.summary
    assert s.session_count == 5
    assert s.energy_kwh == 90.0
    assert s.cost == 48.3  # 5.9 + 27.6 + 0 + 3.0 + 11.8
    assert s.fee == 10.98  # 4.99 + 4.99 + 1.0, not the one of 2025
    assert s.total == 59.28
    assert s.average_price == round(59.28 / 90, 4)
    assert result.free_energy_kwh == 10.0  # cost 0 counts as free
    assert result.current
    assert not result.fees_excluded
    assert result.sessions is None


def test_annual_report_has_every_month_up_to_the_current_one() -> None:
    result = _report()
    assert [r.key for r in result.periods] == [f"2026-{m:02d}" for m in range(1, 12)]
    april = result.periods[3]
    # Fee without a session in the month.
    assert (april.session_count, april.energy_kwh, april.cost, april.fee) == (
        0,
        0.0,
        0.0,
        4.99,
    )
    assert april.average_price is None  # no energy
    assert result.periods[1].session_count == 0  # February: no session, row 0
    march = result.periods[2]
    assert (march.session_count, march.cost, march.fee) == (3, 30.6, 5.99)


def test_past_annual_report_has_twelve_months() -> None:
    result = report(SESSIONS, FEES, TARIFFS, VEHICLES, "2025", _day, TODAY)
    assert len(result.periods) == 12
    assert not result.current
    assert result.summary.session_count == 0
    assert result.summary.fee == 4.99


def test_monthly_report_per_day_with_session_or_fee() -> None:
    result = _report("2026-03")
    assert [r.key for r in result.periods] == ["2026-03-01", "2026-03-12", "2026-03-20"]
    first, twelfth, _ = result.periods
    assert (first.session_count, first.fee) == (0, 5.99)  # fees on the 1st
    assert (twelfth.session_count, twelfth.energy_kwh, twelfth.cost) == (2, 50.0, 27.6)
    assert not result.current
    assert (result.first_day, result.last_day) == (date(2026, 3, 1), date(2026, 3, 31))


def test_tariffs_sorted_by_name_without_tariff_last() -> None:
    result = _report()
    assert [(r.key, r.name) for r in result.tariffs] == [
        ("free", "Arbeitgeber"),
        ("enbw", "EnBW"),
        (None, None),  # deleted tariff
    ]
    enbw = result.tariffs[1]
    assert (enbw.session_count, enbw.cost, enbw.fee) == (3, 45.3, 9.98)
    without = result.tariffs[2]
    assert (without.session_count, without.cost, without.fee) == (1, 3.0, 1.0)


def test_charge_types_without_fees() -> None:
    result = _report()
    assert [
        (r.key, r.session_count, r.energy_kwh, r.cost, r.fee)
        for r in result.charge_types
    ] == [
        ("ac", 4, 50.0, 20.7, 0.0),
        ("dc", 1, 40.0, 27.6, 0.0),
    ]


def test_vehicles_only_for_all_vehicles() -> None:
    result = _report()
    assert [(r.key, r.name, r.session_count, r.total) for r in result.vehicles] == [
        ("dolphin", "Dolphin", 1, 0.0),
        ("seal", "Seal U", 4, 59.28),
    ]
    assert _report(vehicle_id="seal").vehicles is None


def test_vehicle_filter() -> None:
    result = _report(vehicle_id="dolphin")
    assert result.summary.session_count == 1
    assert result.summary.fee == 0.0  # fees belong to the Seal


def test_tariff_filter_leaves_out_deleted_tariff() -> None:
    result = _report(tariff_id="enbw")
    assert result.summary.session_count == 3
    assert result.summary.fee == 9.98  # fee of the deleted tariff not included
    assert [r.key for r in result.tariffs] == ["enbw"]


def test_note_filter_case_insensitive_and_partial() -> None:
    result = _report(note="  DIENSTLICH ")
    assert {
        s.id for s in _report(note="dienstlich", include_sessions=True).sessions
    } == {
        "jan",
        "mar_free",
    }
    assert result.summary.session_count == 2


def test_note_filter_leaves_out_fees_and_sessions_without_note() -> None:
    result = _report(note="dienst")
    assert result.fees_excluded
    assert result.summary.fee == 0.0
    assert all(r.fee == 0.0 for r in result.periods)
    assert "nov" not in {
        s.id for s in _report(note="dienst", include_sessions=True).sessions
    }


def test_blank_note_is_no_filter() -> None:
    result = _report(note="   ")
    assert not result.fees_excluded
    assert result.summary.session_count == 5


def test_filters_combined() -> None:
    result = _report(vehicle_id="seal", tariff_id="enbw", note="dienstlich")
    assert result.summary.session_count == 1  # only "jan"
    assert result.summary.fee == 0.0


def test_sessions_listed_oldest_first() -> None:
    result = _report(include_sessions=True)
    assert [s.id for s in result.sessions] == [
        "jan",
        "mar_dc",
        "mar_free",
        "mar_gone",
        "nov",
    ]


def test_no_sessions_and_no_fees() -> None:
    result = _report("2026-02")
    assert result.periods == []
    assert result.tariffs == []
    assert result.charge_types == []
    assert result.vehicles == []
    assert result.summary.session_count == 0
    assert result.summary.average_price is None


def test_day_in_local_time_at_the_year_boundary() -> None:
    # 23:30 UTC on Dec 31 is already Jan 1 in Berlin.
    late = _session("ny", datetime(2025, 12, 31, 23, 30, tzinfo=UTC))
    result = report([late], [], TARIFFS, VEHICLES, "2026", _day, TODAY)
    assert result.summary.session_count == 1
    previous = report([late], [], TARIFFS, VEHICLES, "2025", _day, TODAY)
    assert previous.summary.session_count == 0


def test_totals_equal_sensor_over_the_whole_time() -> None:
    sessions = [s for s in SESSIONS if s.vehicle_id == "seal"]
    fees = [f for f in FEES if f.vehicle_id == "seal"]
    summary = summarize(sessions, fees)
    total = sum(
        report(
            sessions, fees, TARIFFS, VEHICLES, year, _day, TODAY, vehicle_id="seal"
        ).summary.total
        for year in ("2025", "2026")
    )
    energy = sum(
        report(
            sessions, fees, TARIFFS, VEHICLES, year, _day, TODAY, vehicle_id="seal"
        ).summary.energy_kwh
        for year in ("2025", "2026")
    )
    assert round(total, 2) == summary.total_cost
    assert energy == summary.total_energy_kwh


def test_sessions_of_deleted_vehicles_are_left_out() -> None:
    gone = _session("x", datetime(2026, 5, 1, 10, 0, tzinfo=UTC), vehicle_id="old")
    result = report(
        [gone],
        [Fee("enbw", "old", "2026-05", 1.0)],
        TARIFFS,
        VEHICLES,
        "2026",
        _day,
        TODAY,
    )
    assert result.summary.session_count == 0
    assert result.summary.fee == 0.0


@pytest.mark.parametrize(
    "period", ["26", "2026-13", "2026-1", "abcd", "2026/03", "2027", "2026-12"]
)
def test_invalid_or_future_period_is_rejected(period: str) -> None:
    with pytest.raises(ValidationError) as err:
        _report(period)
    assert err.value.key == "error_report_period"


def test_unknown_vehicle_and_tariff_are_rejected() -> None:
    with pytest.raises(ValidationError) as err:
        _report(vehicle_id="old")
    assert err.value.key == "error_unknown_vehicle"
    with pytest.raises(ValidationError) as err:
        _report(tariff_id="deleted")
    assert err.value.key == "error_unknown_tariff"
