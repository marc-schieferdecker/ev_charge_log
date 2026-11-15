"""Tests for the pure calculation logic in models.py."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from custom_components.ev_charge_log.models import (
    Fee,
    PriceVersion,
    Session,
    SessionInput,
    Tariff,
    ValidationError,
    build_session,
    calculate_cost,
    daily_totals,
    month_totals,
    parse_energy,
    round_money,
    soc_metrics,
    summarize,
)

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)

ENBW = Tariff(id="enbw", name="EnBW", price_ac=0.59, price_dc=0.69)
AC_ONLY = Tariff(id="ac_only", name="AC only", price_ac=0.39)
DC_ONLY = Tariff(id="ewe", name="EWE Go", price_dc=0.62)
FREE = Tariff(id="free", name="Employer", price_ac=0.0)


def _input(**kwargs) -> SessionInput:
    defaults = {
        "vehicle_id": "seal_u",
        "energy_kwh": 40.0,
        "charge_type": "ac",
        "timestamp": NOW,
    }
    defaults.update(kwargs)
    return SessionInput(**defaults)


# --- calculate_cost -------------------------------------------------------


def test_cost_from_tariff_ac() -> None:
    result = calculate_cost(40.0, "ac", ENBW)
    assert result.cost == 23.60
    assert result.price_per_kwh == 0.59
    assert result.cost_overridden is False


def test_cost_from_tariff_dc() -> None:
    result = calculate_cost(40.0, "dc", ENBW)
    assert result.cost == 27.60
    assert result.price_per_kwh == 0.69


def test_dc_price_missing_is_rejected() -> None:
    with pytest.raises(ValidationError):
        calculate_cost(10.0, "dc", AC_ONLY)


def test_ac_price_missing_is_rejected() -> None:
    with pytest.raises(ValidationError):
        calculate_cost(10.0, "ac", DC_ONLY)


def test_dc_only_tariff_books_dc() -> None:
    result = calculate_cost(30.0, "dc", DC_ONLY)
    assert result.cost == 18.60
    assert result.price_per_kwh == 0.62


def test_missing_price_is_rejected_even_with_cost() -> None:
    with pytest.raises(ValidationError):
        calculate_cost(10.0, "ac", DC_ONLY, cost=5.0)


def test_given_cost_overrides_tariff() -> None:
    result = calculate_cost(40.0, "ac", ENBW, cost=19.99)
    assert result.cost == 19.99
    assert result.cost_overridden is True
    assert result.price_per_kwh == 0.59


def test_given_cost_without_tariff() -> None:
    result = calculate_cost(12.5, "dc", None, cost=8.0)
    assert result.cost == 8.0
    assert result.cost_overridden is True
    assert result.price_per_kwh is None


def test_tariff_price_zero_books_zero() -> None:
    result = calculate_cost(30.0, "ac", FREE)
    assert result.cost == 0.0
    assert result.cost_overridden is False
    assert result.price_per_kwh == 0.0


def test_cost_zero_is_a_given_value() -> None:
    # 0 must not be treated as "not given": no tariff needed.
    result = calculate_cost(30.0, "ac", None, cost=0)
    assert result.cost == 0.0
    assert result.cost_overridden is True


def test_cost_zero_overrides_paid_tariff() -> None:
    result = calculate_cost(30.0, "ac", ENBW, cost=0)
    assert result.cost == 0.0
    assert result.cost_overridden is True


def test_neither_cost_nor_tariff_is_rejected() -> None:
    with pytest.raises(ValidationError):
        calculate_cost(30.0, "ac", None, None)


@pytest.mark.parametrize("energy", [0, -1.0])
def test_non_positive_energy_is_rejected(energy: float) -> None:
    with pytest.raises(ValidationError):
        calculate_cost(energy, "ac", ENBW)


def test_negative_cost_is_rejected() -> None:
    with pytest.raises(ValidationError):
        calculate_cost(10.0, "ac", ENBW, cost=-1)


def test_invalid_charge_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        calculate_cost(10.0, "hpc", ENBW)


def test_rounding_half_up() -> None:
    # 0.125 would round to 0.12 with banker's rounding.
    assert round_money(0.125) == 0.13
    assert calculate_cost(1.0, "ac", Tariff("t", "t", price_ac=0.125)).cost == 0.13


# --- Tariff ---------------------------------------------------------------


def test_negative_tariff_price_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Tariff(id="x", name="x", price_ac=-0.1)


def test_tariff_without_any_price_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Tariff(id="x", name="x")


def test_tariff_supports() -> None:
    assert AC_ONLY.supports("ac")
    assert not AC_ONLY.supports("dc")
    assert DC_ONLY.supports("dc")
    assert not DC_ONLY.supports("ac")
    assert FREE.supports("ac")


def test_dc_only_tariff_from_dict() -> None:
    tariff = Tariff.from_dict(
        {"id": "e", "name": "EWE", "price_ac": None, "price_dc": 0.62}
    )
    assert tariff.price_ac is None
    assert tariff.price_dc == 0.62


def test_tariff_from_dict_roundtrip_fields() -> None:
    tariff = Tariff.from_dict(
        {"id": "home", "name": "Zuhause", "price_ac": 0, "zones": ["zone.home"]}
    )
    assert tariff.price_ac == 0.0
    assert tariff.price_dc is None
    assert tariff.zones == ("zone.home",)


# --- build_session / immutability -----------------------------------------


def test_build_session_freezes_cost() -> None:
    session = build_session("s1", _input(tariff_id="enbw"), ENBW)
    assert session.cost == 23.60
    assert session.price_per_kwh == 0.59
    assert session.tariff_id == "enbw"


def test_tariff_change_does_not_affect_booked_session() -> None:
    session = build_session("s1", _input(tariff_id="enbw"), ENBW)
    raised = replace(ENBW, price_ac=0.79)
    assert raised.price_ac == 0.79
    stored = Session.from_dict(session.to_dict())
    assert stored.cost == 23.60
    assert stored.price_per_kwh == 0.59


def test_build_session_rejects_mismatched_tariff() -> None:
    with pytest.raises(ValidationError):
        build_session("s1", _input(tariff_id="other"), ENBW)


def test_build_session_rejects_unknown_tariff() -> None:
    with pytest.raises(ValidationError):
        build_session("s1", _input(tariff_id="missing"), None)


def test_session_requires_aware_timestamp() -> None:
    with pytest.raises(ValidationError):
        build_session("s1", _input(timestamp=datetime(2026, 11, 15)), ENBW)


@pytest.mark.parametrize("soc", [-1, 101])
def test_soc_out_of_range_is_rejected(soc: float) -> None:
    with pytest.raises(ValidationError):
        build_session("s1", _input(tariff_id="enbw", soc_start=soc), ENBW)


def test_session_normalizes_numbers_to_float() -> None:
    session = build_session(
        "s1", _input(energy_kwh=40, tariff_id="enbw", soc_start=20, soc_end=60), ENBW
    )
    assert type(session.energy_kwh) is float
    assert type(session.soc_start) is float
    assert type(session.soc_end) is float


def test_session_dict_roundtrip() -> None:
    session = build_session(
        "s1",
        _input(
            tariff_id="enbw", soc_start=20, soc_end=80, note="A7", source="reminder"
        ),
        ENBW,
    )
    assert Session.from_dict(session.to_dict()) == session


# --- summarize ------------------------------------------------------------


def test_summary_includes_free_sessions_in_average() -> None:
    sessions = [
        build_session("s1", _input(energy_kwh=160.0, tariff_id="free"), FREE),
        build_session("s2", _input(energy_kwh=30.0, cost=0), None),
        build_session("s3", _input(energy_kwh=40.0, tariff_id="enbw"), ENBW),
    ]
    summary = summarize(sessions)
    assert summary.total_cost == 23.60
    assert summary.total_energy_kwh == 230.0
    assert summary.session_count == 3
    assert summary.average_price_per_kwh == round(23.60 / 230.0, 4)


def test_summary_without_sessions() -> None:
    summary = summarize([])
    assert summary.total_cost == 0
    assert summary.session_count == 0
    assert summary.average_price_per_kwh is None


def test_summary_has_no_float_drift() -> None:
    sessions = [
        build_session(f"s{i}", _input(energy_kwh=1.0, cost=0.1), None) for i in range(3)
    ]
    assert summarize(sessions).total_cost == 0.3


# --- soc_metrics ----------------------------------------------------------


def test_soc_metrics_with_capacity() -> None:
    session = build_session(
        "s1", _input(energy_kwh=50.0, tariff_id="enbw", soc_start=20, soc_end=80), ENBW
    )
    metrics = soc_metrics(session, battery_capacity_kwh=75.0)
    assert metrics is not None
    assert metrics.soc_points == 60
    # 60 % of 75 kWh = 45 kWh stored, 50 kWh charged -> 10 % loss
    assert metrics.charging_loss_percent == 10.0
    assert metrics.cost_per_soc_point == round(29.50 / 60, 4)


def test_soc_metrics_without_capacity_has_no_loss() -> None:
    session = build_session(
        "s1", _input(tariff_id="enbw", soc_start=20, soc_end=80), ENBW
    )
    metrics = soc_metrics(session, None)
    assert metrics is not None
    assert metrics.charging_loss_percent is None


def test_soc_metrics_missing_soc() -> None:
    session = build_session("s1", _input(tariff_id="enbw", soc_start=20), ENBW)
    assert soc_metrics(session, 75.0) is None


# --- price versions (stage 3) ---------------------------------------------


def _versioned() -> Tariff:
    return Tariff(
        id="enbw",
        name="EnBW",
        price_ac=0.59,
        price_dc=0.69,
        versions=(
            PriceVersion(date(2027, 7, 1), price_ac=0.65, price_dc=0.75),
            PriceVersion(date(2027, 1, 1), price_ac=0.62, price_dc=0.72),
        ),
    )


def test_versions_are_sorted() -> None:
    days = [v.valid_from for v in _versioned().versions]
    assert days == [date(2027, 1, 1), date(2027, 7, 1)]


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 12, 31), 0.59),  # before the first version: base prices
        (date(2027, 1, 1), 0.62),  # valid from its date on
        (date(2027, 6, 30), 0.62),
        (date(2027, 7, 1), 0.65),
        (date(2030, 1, 1), 0.65),
    ],
)
def test_price_version_by_day(day: date, expected: float) -> None:
    assert _versioned().price_for("ac", day) == expected


def test_booking_uses_version_of_session_day() -> None:
    tariff = _versioned()
    old = build_session(
        "s1", _input(timestamp=datetime(2026, 12, 31, 20, tzinfo=UTC)), tariff
    )
    new = build_session(
        "s2", _input(timestamp=datetime(2027, 1, 2, 8, tzinfo=UTC)), tariff
    )
    assert (old.price_per_kwh, old.cost) == (0.59, 23.60)
    assert (new.price_per_kwh, new.cost) == (0.62, 24.80)


def test_version_without_dc_price_rejects_dc_from_its_date() -> None:
    tariff = Tariff(
        id="t",
        name="T",
        price_ac=0.5,
        price_dc=0.6,
        versions=(PriceVersion(date(2027, 1, 1), price_ac=0.55),),
    )
    assert tariff.supports("dc", date(2026, 12, 31))
    assert not tariff.supports("dc", date(2027, 1, 1))
    with pytest.raises(ValidationError):
        calculate_cost(10.0, "dc", tariff, day=date(2027, 2, 1))


def test_duplicate_version_dates_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Tariff(
            id="t",
            name="T",
            price_ac=0.5,
            versions=(
                PriceVersion(date(2027, 1, 1), price_ac=0.6),
                PriceVersion(date(2027, 1, 1), price_ac=0.7),
            ),
        )


def test_version_without_any_price_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PriceVersion(date(2027, 1, 1))


def test_versions_from_dict() -> None:
    tariff = Tariff.from_dict(
        {
            "id": "enbw",
            "name": "EnBW",
            "price_ac": 0.59,
            "versions": [
                {"valid_from": "2027-01-01", "price_ac": 0.62, "price_dc": None}
            ],
        }
    )
    assert tariff.versions == (PriceVersion(date(2027, 1, 1), price_ac=0.62),)
    assert tariff.versions[0].to_dict() == {
        "valid_from": "2027-01-01",
        "price_ac": 0.62,
        "price_dc": None,
        "monthly_fee": None,
    }


def test_tariff_without_versions_from_dict() -> None:
    """Tariffs stored before stage 3 have no "versions" key."""
    tariff = Tariff.from_dict({"id": "x", "name": "X", "price_ac": 0.4})
    assert tariff.versions == ()
    assert tariff.price_for("ac", date(2030, 1, 1)) == 0.4


# --- monthly fee (stage 4) ------------------------------------------------


def _fee_tariff(**kwargs) -> Tariff:
    defaults = {
        "id": "enbw",
        "name": "EnBW",
        "price_dc": 0.69,
        "monthly_fee": 11.99,
        "fee_vehicle_id": "seal",
        "fee_start": date(2026, 10, 15),
    }
    defaults.update(kwargs)
    return Tariff(**defaults)


def test_due_fees_every_started_month() -> None:
    due = _fee_tariff().due_fees(date(2026, 12, 1), set())
    assert due == [("2026-10", 11.99), ("2026-11", 11.99), ("2026-12", 11.99)]


def test_due_fees_skip_booked_months() -> None:
    due = _fee_tariff().due_fees(date(2026, 12, 31), {"2026-10", "2026-11"})
    assert due == [("2026-12", 11.99)]


def test_due_fees_until_end_month() -> None:
    tariff = _fee_tariff(fee_end=date(2026, 11, 1))
    assert [m for m, _ in tariff.due_fees(date(2027, 3, 1), set())] == [
        "2026-10",
        "2026-11",
    ]


def test_due_fees_need_vehicle_and_start() -> None:
    assert _fee_tariff(fee_vehicle_id=None).due_fees(date(2027, 1, 1), set()) == []
    assert _fee_tariff(fee_start=None).due_fees(date(2027, 1, 1), set()) == []


def test_no_fee_amount_books_nothing() -> None:
    assert _fee_tariff(monthly_fee=None).due_fees(date(2027, 1, 1), set()) == []


def test_fee_zero_is_booked() -> None:
    assert _fee_tariff(monthly_fee=0).due_fees(date(2026, 10, 20), set()) == [
        ("2026-10", 0.0)
    ]


def test_fee_follows_price_versions() -> None:
    tariff = _fee_tariff(
        versions=(
            PriceVersion(date(2026, 12, 1), price_dc=0.72, monthly_fee=12.99),
            PriceVersion(date(2027, 2, 15), price_dc=0.75),  # fee unchanged
        )
    )
    due = dict(tariff.due_fees(date(2027, 3, 1), set()))
    assert due == {
        "2026-10": 11.99,
        "2026-11": 11.99,
        "2026-12": 12.99,
        "2027-01": 12.99,
        "2027-02": 12.99,  # version from Feb 15 does not change the fee
        "2027-03": 12.99,
    }


def test_fee_end_before_start_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _fee_tariff(fee_end=date(2026, 1, 1))


def test_summary_includes_fees() -> None:
    sessions = [build_session("s1", _input(energy_kwh=40.0, tariff_id="enbw"), ENBW)]
    fees = [Fee("enbw", "seal_u", "2026-11", 11.99)]
    summary = summarize(sessions, fees)
    assert summary.total_cost == 35.59
    assert summary.fee_total == 11.99
    assert summary.session_count == 1
    assert summary.total_energy_kwh == 40.0
    assert summary.average_price_per_kwh == round(35.59 / 40.0, 4)


def test_fee_tariff_from_dict() -> None:
    tariff = Tariff.from_dict(
        {
            "id": "enbw",
            "name": "EnBW",
            "price_dc": 0.69,
            "monthly_fee": 11.99,
            "fee_vehicle_id": "seal",
            "fee_start": "2026-10-01",
            "fee_end": None,
        }
    )
    assert tariff.fee_start == date(2026, 10, 1)
    assert tariff.fee_end is None


# --- typed kWh reply (stage 4 push) ---------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12", 12.0),
        ("12,4", 12.4),
        ("12.4", 12.4),
        (" 12,4 kWh ", 12.4),
        ("40KWH", 40.0),
        ("0", None),
        ("", None),
        ("zwölf", None),
        ("12,4,5", None),
        ("-3", None),
        (None, None),
    ],
)
def test_parse_energy(text: str | None, expected: float | None) -> None:
    assert parse_energy(text) == expected


# --- daily totals for the panel chart -------------------------------------


def test_daily_totals_last_30_days() -> None:
    def at(day: int, kwh: float, hour: int = 12) -> Session:
        return build_session(
            f"s{day}{hour}",
            _input(
                energy_kwh=kwh,
                tariff_id="enbw",
                timestamp=datetime(2026, 11, day, hour, tzinfo=UTC),
            ),
            ENBW,
        )

    sessions = [
        at(1, 10.0),  # 15 days before the last day -> inside
        at(15, 20.0, 8),
        at(15, 5.0, 20),  # two sessions on one day
        at(16, 99.0),  # after the last day -> outside
    ]
    totals = daily_totals(
        sessions, date(2026, 11, 15), 30, lambda s: s.timestamp.date()
    )
    assert len(totals.days) == 30
    assert totals.days[0] == date(2026, 10, 17)
    assert totals.days[-1] == date(2026, 11, 15)
    assert totals.energy_kwh[-1] == 25.0
    assert totals.cost[-1] == 14.75
    assert totals.energy_kwh[totals.days.index(date(2026, 11, 1))] == 10.0
    assert totals.total_energy_kwh == 35.0
    assert totals.total_cost == 20.65


def test_daily_totals_without_sessions() -> None:
    totals = daily_totals([], date(2026, 11, 15), 7, lambda s: s.timestamp.date())
    assert totals.energy_kwh == [0.0] * 7
    assert totals.total_cost == 0


@pytest.mark.parametrize(
    ("month", "days", "first", "last"),
    [
        ("2026-11", 30, date(2026, 11, 1), date(2026, 11, 30)),
        ("2026-12", 31, date(2026, 12, 1), date(2026, 12, 31)),
        ("2028-02", 29, date(2028, 2, 1), date(2028, 2, 29)),
    ],
)
def test_month_totals_cover_the_calendar_month(
    month: str, days: int, first: date, last: date
) -> None:
    totals = month_totals([], month, lambda s: s.timestamp.date())
    assert len(totals.days) == days
    assert (totals.days[0], totals.days[-1]) == (first, last)


def test_month_totals_count_only_that_month() -> None:
    sessions = [
        build_session(
            f"s{i}",
            _input(energy_kwh=kwh, tariff_id="enbw", timestamp=ts),
            ENBW,
        )
        for i, (kwh, ts) in enumerate(
            [
                (10.0, datetime(2026, 10, 31, 23, tzinfo=UTC)),
                (20.0, datetime(2026, 11, 1, 8, tzinfo=UTC)),
                (5.0, datetime(2026, 11, 30, 20, tzinfo=UTC)),
                (7.0, datetime(2026, 12, 1, 0, tzinfo=UTC)),
            ]
        )
    ]
    totals = month_totals(sessions, "2026-11", lambda s: s.timestamp.date())
    assert totals.total_energy_kwh == 25.0
    assert totals.energy_kwh[0] == 20.0
    assert totals.energy_kwh[-1] == 5.0
