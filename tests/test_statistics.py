"""Hourly statistics for the recorder (stage 5): pure calculation."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from custom_components.ev_charge_log.models import (
    Fee,
    Session,
    summarize,
)
from custom_components.ev_charge_log.reports import (
    HourlyTotal,
    hourly_statistics,
)

BERLIN = ZoneInfo("Europe/Berlin")


def _session(
    session_id: str,
    ts: datetime,
    kwh: float,
    cost: float,
    vehicle_id: str = "seal",
) -> Session:
    return Session(
        id=session_id,
        vehicle_id=vehicle_id,
        timestamp=ts,
        energy_kwh=kwh,
        charge_type="ac",
        cost=cost,
        cost_overridden=True,
    )


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_session_counts_in_the_hour_of_its_timestamp() -> None:
    # Booked today, but charged a week ago at 18:42 local time.
    session = _session("a", datetime(2026, 11, 8, 18, 42, tzinfo=BERLIN), 20.0, 11.8)
    assert hourly_statistics([session], [], "seal", BERLIN) == [
        HourlyTotal(_utc(2026, 11, 8, 17), 20.0, 11.8)
    ]


def test_running_sums_and_same_hour_added() -> None:
    sessions = [
        _session("c", _utc(2026, 11, 10, 9, 5), 30.0, 20.7),
        _session("a", _utc(2026, 11, 1, 7, 10), 10.1, 5.9),
        _session("b", _utc(2026, 11, 1, 7, 50), 0.2, 0.1),
    ]
    assert hourly_statistics(sessions, [], "seal", BERLIN) == [
        HourlyTotal(_utc(2026, 11, 1, 7), 10.3, 6.0),
        HourlyTotal(_utc(2026, 11, 10, 9), 40.3, 26.7),
    ]


def test_deleting_or_correcting_changes_all_later_sums() -> None:
    first = _session("a", _utc(2026, 11, 1, 7), 10.0, 6.0)
    second = _session("b", _utc(2026, 11, 2, 7), 20.0, 12.0)
    third = _session("c", _utc(2026, 11, 3, 7), 30.0, 18.0)
    rows = hourly_statistics([first, second, third], [], "seal", BERLIN)
    assert [r.cost for r in rows] == [6.0, 18.0, 36.0]

    # Deleted: its hour disappears, later sums drop.
    rows = hourly_statistics([first, third], [], "seal", BERLIN)
    assert [(r.start.day, r.energy_kwh, r.cost) for r in rows] == [
        (1, 10.0, 6.0),
        (3, 40.0, 24.0),
    ]

    # Corrected to an earlier day and a lower amount.
    moved = _session("b", _utc(2026, 10, 30, 7), 20.0, 2.0)
    rows = hourly_statistics([first, moved, third], [], "seal", BERLIN)
    assert [(r.start.day, r.cost) for r in rows] == [(30, 2.0), (1, 8.0), (3, 26.0)]


def test_free_session_creates_a_row() -> None:
    session = _session("a", _utc(2026, 11, 1, 7), 12.0, 0.0)
    assert hourly_statistics([session], [], "seal", BERLIN) == [
        HourlyTotal(_utc(2026, 11, 1, 7), 12.0, 0.0)
    ]


def test_fee_counts_on_first_of_month_local_midnight() -> None:
    fees = [
        Fee("enbw", "seal", "2026-11", 11.99),
        Fee("enbw", "seal", "2026-07", 11.99),
    ]
    rows = hourly_statistics([], fees, "seal", BERLIN)
    # 00:00 local: 23:00 UTC in winter, 22:00 UTC in summer (day before).
    assert rows == [
        HourlyTotal(_utc(2026, 6, 30, 22), 0.0, 11.99),
        HourlyTotal(_utc(2026, 10, 31, 23), 0.0, 23.98),
    ]


def test_only_the_vehicles_own_sessions_and_fees() -> None:
    sessions = [
        _session("a", _utc(2026, 11, 1, 7), 10.0, 6.0),
        _session("b", _utc(2026, 11, 1, 8), 5.0, 0.0, vehicle_id="dolphin"),
    ]
    fees = [Fee("enbw", "dolphin", "2026-11", 9.99)]
    assert hourly_statistics(sessions, fees, "seal", BERLIN) == [
        HourlyTotal(_utc(2026, 11, 1, 7), 10.0, 6.0)
    ]
    assert hourly_statistics([], [], "seal", BERLIN) == []


def test_daylight_saving_change() -> None:
    # 2026-10-25: 02:00-03:00 local happens twice (CEST, then CET).
    first = datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=0)  # 00:30 UTC
    second = datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=1)  # 01:30 UTC
    rows = hourly_statistics(
        [_session("a", first, 10.0, 5.0), _session("b", second, 10.0, 5.0)],
        [],
        "seal",
        BERLIN,
    )
    assert [r.start for r in rows] == [_utc(2026, 10, 25, 0), _utc(2026, 10, 25, 1)]
    assert [r.energy_kwh for r in rows] == [10.0, 20.0]


def test_last_row_equals_vehicle_totals() -> None:
    sessions = [
        _session("a", _utc(2026, 9, 3, 7), 10.1, 6.07),
        _session("b", _utc(2026, 10, 9, 18), 33.3, 22.98),
        _session("c", _utc(2026, 11, 2, 12), 0.7, 0.0),
    ]
    fees = [Fee("enbw", "seal", m, 11.99) for m in ("2026-10", "2026-11")]
    last = hourly_statistics(sessions, fees, "seal", BERLIN)[-1]
    summary = summarize(sessions, fees)
    assert last.energy_kwh == summary.total_energy_kwh
    assert last.cost == summary.total_cost
