"""Tests for the tariff comparison "what if" (stage 8), without Home Assistant."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.ev_charge_log.models import (
    Fee,
    Session,
    ValidationError,
    WhatIfTariff,
    compare_tariffs,
)

BERLIN = ZoneInfo("Europe/Berlin")


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
    _session("a", datetime(2026, 8, 10, 10, 0, tzinfo=UTC)),
    _session(
        "b",
        datetime(2026, 8, 12, 10, 0, tzinfo=UTC),
        charge_type="dc",
        energy_kwh=40.0,
        cost=27.6,
        price_per_kwh=0.69,
    ),
    _session("c", datetime(2026, 8, 20, 10, 0, tzinfo=UTC), vehicle_id="dolphin"),
    _session(
        "d",
        datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
        tariff_id="free",
        cost=0.0,
        price_per_kwh=0.0,
    ),
    _session("gone", datetime(2026, 8, 22, 10, 0, tzinfo=UTC), tariff_id="deleted"),
]
FEES = [
    Fee("enbw", "seal", "2026-07", 4.99),
    Fee("enbw", "seal", "2026-08", 4.99),
    Fee("other", "dolphin", "2026-08", 2.0),
]
A = WhatIfTariff(price_ac=0.59, price_dc=0.69)
B = WhatIfTariff(price_ac=0.39, price_dc=0.49, monthly_fee=5.99)


def _compare(start=date(2026, 8, 1), end=date(2026, 8, 31), a=A, b=B, **kwargs):
    return compare_tariffs(SESSIONS, FEES, start, end, _day, a, b, **kwargs)


def test_prices_per_charge_type_and_fee_per_month() -> None:
    result = _compare(vehicle_id="seal", tariff_id="enbw")
    assert result.session_count == 2
    assert result.energy_kwh == 50.0
    assert result.months == 1
    # 10 x 0.59 + 40 x 0.69
    assert (result.a.cost, result.a.fees, result.a.total) == (33.5, 0.0, 33.5)
    # 10 x 0.39 + 40 x 0.49 + 5.99
    assert (result.b.cost, result.b.fees, result.b.total) == (23.5, 5.99, 29.49)
    assert result.difference == -4.01
    assert result.a.average == 0.67
    assert result.b.average == 0.5898
    # Really paid: stored costs and the booked fee of August.
    assert (result.actual_cost, result.actual_fees, result.actual_total) == (
        33.5,
        4.99,
        38.49,
    )


def test_all_sessions_include_deleted_tariff_and_all_fees() -> None:
    result = _compare()
    assert result.session_count == 5
    assert result.actual_fees == 6.99
    assert result.actual_cost == 45.3


def test_filter_by_vehicle_and_by_tariff() -> None:
    assert _compare(vehicle_id="dolphin").session_count == 1
    assert _compare(vehicle_id="dolphin").actual_fees == 2.0
    assert _compare(tariff_id="enbw").session_count == 3
    assert _compare(tariff_id="enbw").actual_fees == 4.99
    assert _compare(tariff_id="free", vehicle_id="dolphin").session_count == 0


def test_charge_type_without_price_keeps_the_actual_cost() -> None:
    dc_only = WhatIfTariff(price_dc=0.49)
    result = _compare(a=dc_only, vehicle_id="seal", tariff_id="enbw")
    # AC session with its stored 5.90, DC session 40 x 0.49.
    assert result.a.cost == 25.5
    assert result.a.missing_count == 1
    assert result.b.missing_count == 0


def test_price_zero_and_fee_zero_are_valid() -> None:
    free = WhatIfTariff(price_ac=0.0, price_dc=0.0, monthly_fee=0.0)
    result = _compare(a=free)
    assert (result.a.cost, result.a.fees, result.a.total) == (0.0, 0.0, 0.0)
    assert result.a.missing_count == 0
    assert WhatIfTariff(price_ac=0.3).monthly_fee is None


@pytest.mark.parametrize(
    ("kwargs", "key"),
    [
        ({}, "error_price_required"),
        ({"monthly_fee": 5.0}, "error_price_required"),
        ({"price_ac": -0.1}, "error_negative_price_ac"),
        ({"price_dc": -0.1}, "error_negative_price_dc"),
        ({"price_ac": 0.3, "monthly_fee": -1.0}, "error_negative_monthly_fee"),
    ],
)
def test_invalid_tariff_is_rejected(kwargs, key) -> None:
    with pytest.raises(ValidationError) as err:
        WhatIfTariff(**kwargs)
    assert err.value.key == key


def test_start_after_end_is_rejected() -> None:
    with pytest.raises(ValidationError) as err:
        _compare(start=date(2026, 8, 31), end=date(2026, 8, 1))
    assert err.value.key == "error_compare_range"


def test_period_is_inclusive_and_uses_the_local_day() -> None:
    late = _session("late", datetime(2026, 8, 9, 22, 30, tzinfo=UTC))  # 10.8. local
    result = compare_tariffs(
        [late, *SESSIONS], [], date(2026, 8, 10), date(2026, 8, 10), _day, A, B
    )
    assert result.session_count == 2


def test_fee_only_for_months_with_sessions() -> None:
    sessions = [
        _session("jul", datetime(2026, 7, 31, 10, 0, tzinfo=UTC)),
        _session("aug1", datetime(2026, 8, 1, 10, 0, tzinfo=UTC)),
        _session("aug2", datetime(2026, 8, 20, 10, 0, tzinfo=UTC)),
        # October, but another vehicle: not selected, no fee month.
        _session("oct", datetime(2026, 10, 5, 10, 0, tzinfo=UTC), vehicle_id="x"),
        _session("jan", datetime(2027, 1, 2, 10, 0, tzinfo=UTC)),
    ]
    result = compare_tariffs(
        sessions,
        [],
        date(2026, 7, 1),
        date(2027, 1, 31),
        _day,
        A,
        B,
        vehicle_id="seal",
    )
    # July, August and January; September to December without sessions.
    assert result.months == 3
    assert result.b.fees == 17.97
    assert result.a.fees == 0.0


def test_no_sessions_no_fee() -> None:
    result = compare_tariffs(
        [], FEES, date(2025, 12, 1), date(2026, 11, 21), _day, A, B
    )
    assert result.months == 0
    assert result.b.fees == 0.0
    assert result.session_count == 0
    assert result.a.average is None
    # What was really paid still contains the booked fees.
    assert result.actual_fees == 11.98


def test_actual_fees_of_the_started_months() -> None:
    result = _compare(start=date(2026, 7, 31), end=date(2026, 8, 1), vehicle_id="seal")
    assert result.actual_fees == 9.98
