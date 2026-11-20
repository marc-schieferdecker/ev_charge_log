"""Tests for the pure calculation logic in models.py."""

from __future__ import annotations

import csv
import io
from dataclasses import replace
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.ev_charge_log.models import (
    Fee,
    OdometerBaseline,
    PriceVersion,
    Reply,
    Session,
    SessionInput,
    Tariff,
    ValidationError,
    Vehicle,
    build_session,
    calculate_cost,
    cost_per_100,
    daily_totals,
    energy_from_soc,
    export_csv,
    month_totals,
    parse_reply,
    round_money,
    soc_metrics,
    summarize,
    tariff_month_stats,
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


# --- energy from SoC ------------------------------------------------------


def test_energy_from_soc() -> None:
    # 60 % of 75 kWh
    assert energy_from_soc(20, 80, 75.0) == 45.0
    # 0 is a valid start SoC
    assert energy_from_soc(0, 100, 60.48) == 60.48


def test_energy_from_soc_is_rounded() -> None:
    # 33 % of 87 kWh = 28.71; 1 % of 60.48 = 0.6048 -> 0.60
    assert energy_from_soc(31, 64, 87.0) == 28.71
    assert energy_from_soc(50, 51, 60.48) == 0.6
    assert energy_from_soc(10.5, 20, 50.0) == 4.75


@pytest.mark.parametrize(("start", "end"), [(None, 80), (20, None), (None, None)])
def test_energy_from_soc_needs_both_soc(start, end) -> None:
    with pytest.raises(ValidationError) as err:
        energy_from_soc(start, end, 75.0)
    assert err.value.key == "error_energy_required"


@pytest.mark.parametrize("capacity", [None, 0.0])
def test_energy_from_soc_needs_capacity(capacity) -> None:
    with pytest.raises(ValidationError) as err:
        energy_from_soc(20, 80, capacity)
    assert err.value.key == "error_energy_soc_capacity"


@pytest.mark.parametrize(("start", "end"), [(80, 80), (80, 20), (50, 50.001)])
def test_energy_from_soc_needs_rising_soc(start, end) -> None:
    with pytest.raises(ValidationError) as err:
        energy_from_soc(start, end, 75.0)
    assert err.value.key == "error_energy_soc_order"


def test_energy_from_soc_adds_charging_loss() -> None:
    # 60 % of 75 kWh = 45 kWh in the battery, plus 1 % -> 45.45 kWh
    assert energy_from_soc(20, 80, 75.0, 1.0) == 45.45
    # 0 % is the same as no loss
    assert energy_from_soc(20, 80, 75.0, 0.0) == 45.0
    # Rounded once at the end: 33 % of 87 kWh = 28.71, x 1.085 = 31.15035
    assert energy_from_soc(31, 64, 87.0, 8.5) == 31.15


def test_vehicle_charging_loss_per_charge_type() -> None:
    vehicle = Vehicle(id="v", name="Surf", charging_loss_ac=10, charging_loss_dc=1)
    assert vehicle.charging_loss("ac") == 10
    assert vehicle.charging_loss("dc") == 1
    assert Vehicle(id="v", name="Surf").charging_loss("dc") == 0.0
    loaded = Vehicle.from_dict(
        {"id": "v", "name": "Surf", "charging_loss_ac": None, "charging_loss_dc": 1}
    )
    assert (loaded.charging_loss("ac"), loaded.charging_loss("dc")) == (0.0, 1.0)


@pytest.mark.parametrize("loss", [-1, 100])
def test_vehicle_rejects_invalid_charging_loss(loss: float) -> None:
    with pytest.raises(ValidationError) as err:
        Vehicle(id="v", name="Surf", charging_loss_dc=loss)
    assert err.value.key == "error_charging_loss"


def test_energy_from_soc_rejects_soc_out_of_range() -> None:
    with pytest.raises(ValidationError) as err:
        energy_from_soc(20, 120, 75.0)
    assert err.value.key == "error_soc_end_range"


def test_build_session_without_energy_is_rejected() -> None:
    with pytest.raises(ValidationError) as err:
        build_session("s1", _input(energy_kwh=None, tariff_id="enbw"), ENBW)
    assert err.value.key == "error_energy_required"


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
        "reference_price_ac": None,
        "reference_price_dc": None,
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


# --- typed reply: kWh and optional cost (stage 4 push) --------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12", Reply(12.0)),
        ("12,4", Reply(12.4)),
        ("12.4", Reply(12.4)),
        (" 12,4 kWh ", Reply(12.4)),
        ("40KWH", Reply(40.0)),
        ("12,4 7,32", Reply(12.4, 7.32)),
        ("12,4 kWh 7,32 €", Reply(12.4, 7.32)),
        ("12,4kWh 7,32€", Reply(12.4, 7.32)),
        ("12,4;7,32", Reply(12.4, 7.32)),
        ("12,4 / 7,32", Reply(12.4, 7.32)),
        ("12.4 7.32 EUR", Reply(12.4, 7.32)),
        ("30 0", Reply(30.0, 0.0)),  # cost 0 is valid
        ("0", None),  # kWh must be positive
        ("0 5", None),
        ("", None),
        ("zwölf", None),
        ("12,4,5", None),
        ("12,4 7,32 1", None),
        ("-3", None),
        ("12,4 -1", None),
        (None, None),
    ],
)
def test_parse_reply(text: str | None, expected: Reply | None) -> None:
    assert parse_reply(text) == expected


def test_parse_reply_with_currency_code() -> None:
    assert parse_reply("10 3,50 CHF", "CHF") == Reply(10.0, 3.5)
    assert parse_reply("10 3,50 SEK", "SEK") == Reply(10.0, 3.5)


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


# --- cost per 100 km --------------------------------------------------------


def _session_at(session_id: str, ts: datetime, kwh: float) -> Session:
    return build_session(
        session_id, _input(energy_kwh=kwh, tariff_id="enbw", timestamp=ts), ENBW
    )


BASELINE = OdometerBaseline(
    "sensor.seal_odometer", 12000.0, datetime(2026, 11, 1, tzinfo=UTC)
)


def test_cost_per_100_counts_sessions_since_baseline() -> None:
    sessions = [
        _session_at("old", datetime(2026, 10, 31, tzinfo=UTC), 40.0),  # 23.60
        _session_at("a", datetime(2026, 11, 2, tzinfo=UTC), 40.0),  # 23.60
        _session_at("b", datetime(2026, 11, 9, tzinfo=UTC), 20.0),  # 11.80
    ]
    # 35.40 over 400 km -> 8.85 per 100 km
    assert cost_per_100(sessions, BASELINE, 12400.0) == 8.85


def test_cost_per_100_needs_some_distance() -> None:
    sessions = [_session_at("a", datetime(2026, 11, 2, tzinfo=UTC), 40.0)]
    assert cost_per_100(sessions, BASELINE, 12000.0) is None
    assert cost_per_100(sessions, BASELINE, 11990.0) is None


def test_cost_per_100_free_charging_is_zero() -> None:
    free = build_session(
        "f",
        _input(
            energy_kwh=30.0,
            tariff_id="free",
            timestamp=datetime(2026, 11, 2, tzinfo=UTC),
        ),
        FREE,
    )
    assert cost_per_100([free], BASELINE, 12200.0) == 0.0


def test_odometer_baseline_roundtrip() -> None:
    assert OdometerBaseline.from_dict(BASELINE.to_dict()) == BASELINE


# --- tariff statistics (stage 4) -------------------------------------------

ABO = Tariff(
    id="abo",
    name="EnBW L",
    price_ac=0.39,
    price_dc=0.49,
    reference_price_dc=0.79,
    monthly_fee=11.99,
    fee_vehicle_id="seal",
    fee_start=date(2026, 1, 1),
)
HOME = Tariff(id="home", name="Zuhause", price_ac=0.0)


def _charge(
    session_id: str,
    day: date,
    kwh: float,
    tariff: Tariff | None,
    charge_type: str = "dc",
    cost: float | None = None,
    tariff_id: str | None = None,
) -> Session:
    ts = datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
    data = _input(
        energy_kwh=kwh,
        charge_type=charge_type,
        timestamp=ts,
        cost=cost,
        tariff_id=tariff.id if tariff else None,
    )
    session = build_session(session_id, data, tariff)
    return replace(session, tariff_id=tariff_id) if tariff_id else session


def _fee(tariff_id: str, month: str = "2026-11", amount: float = 11.99) -> Fee:
    return Fee(tariff_id=tariff_id, vehicle_id="seal", month=month, amount=amount)


def _stats(sessions, fees, tariffs=(ABO, HOME), month="2026-11"):
    return tariff_month_stats(
        sessions, fees, {t.id: t for t in tariffs}, month, lambda s: s.timestamp.date()
    )


def test_tariff_stats_saving_with_fee() -> None:
    sessions = [
        _charge("a", date(2026, 11, 3), 40.0, ABO),
        _charge("b", date(2026, 11, 9), 40.0, ABO),
    ]
    (row,) = _stats(sessions, [_fee("abo")]).rows
    assert (row.tariff_id, row.name, row.session_count) == ("abo", "EnBW L", 2)
    assert row.energy_kwh == 80.0
    assert row.cost == 39.2
    assert row.fee == 11.99
    # 2 x (40 x 0.79 - 19.60) - 11.99
    assert row.saving == 12.01


def test_tariff_stats_saving_uses_stored_cost_of_overridden_amount() -> None:
    sessions = [_charge("a", date(2026, 11, 3), 40.0, ABO, cost=10.0)]
    (row,) = _stats(sessions, [_fee("abo")]).rows
    assert row.cost == 10.0
    assert row.saving == 9.61  # 31.60 - 10.00 - 11.99


def test_tariff_stats_reference_price_zero() -> None:
    tariff = replace(ABO, reference_price_ac=0.0)
    sessions = [_charge("a", date(2026, 11, 3), 10.0, tariff, charge_type="ac")]
    (row,) = _stats(sessions, [_fee("abo")], tariffs=(tariff,)).rows
    assert row.saving == -15.89  # 0 - 3.90 - 11.99


def test_tariff_stats_charge_type_without_reference_counts_zero() -> None:
    sessions = [_charge("a", date(2026, 11, 3), 10.0, ABO, charge_type="ac")]
    (row,) = _stats(sessions, [_fee("abo", amount=10.0)]).rows
    assert row.cost == 3.9
    assert row.saving == -10.0


def test_tariff_stats_month_with_fee_but_no_session() -> None:
    (row,) = _stats([], [_fee("abo")]).rows
    assert (row.session_count, row.energy_kwh, row.cost) == (0, 0.0, 0.0)
    assert row.saving == -11.99


def test_tariff_stats_reference_price_version_within_month() -> None:
    tariff = replace(
        ABO,
        versions=(
            PriceVersion(
                valid_from=date(2026, 11, 15), price_dc=0.49, reference_price_dc=0.89
            ),
        ),
    )
    sessions = [
        _charge("a", date(2026, 11, 10), 10.0, tariff),  # 7.90 - 4.90 = 3.00
        _charge("b", date(2026, 11, 20), 10.0, tariff),  # 8.90 - 4.90 = 4.00
    ]
    (row,) = _stats(sessions, [_fee("abo", amount=5.0)], tariffs=(tariff,)).rows
    assert row.saving == 2.0


def test_reference_price_version_without_value_keeps_previous() -> None:
    tariff = replace(
        ABO, versions=(PriceVersion(valid_from=date(2026, 11, 15), price_dc=0.59),)
    )
    assert tariff.reference_price_on("dc", date(2026, 11, 20)) == 0.79
    assert tariff.reference_price_on("ac", date(2026, 11, 20)) is None


def test_tariff_stats_without_fee_has_no_saving() -> None:
    sessions = [_charge("a", date(2026, 11, 3), 12.0, HOME, charge_type="ac")]
    (row,) = _stats(sessions, []).rows
    assert (row.tariff_id, row.cost, row.fee, row.saving) == ("home", 0.0, None, None)


def test_tariff_stats_deleted_tariff_goes_to_without_tariff() -> None:
    sessions = [
        _charge("a", date(2026, 11, 3), 10.0, ABO, tariff_id="gone"),
        _charge("b", date(2026, 11, 4), 5.0, None, charge_type="ac", cost=3.0),
    ]
    stats = _stats(sessions, [_fee("gone", amount=4.99)])
    (row,) = stats.rows
    assert (row.tariff_id, row.name) == (None, None)
    assert (row.session_count, row.energy_kwh, row.cost) == (2, 15.0, 7.9)
    assert (row.fee, row.saving) == (4.99, None)


def test_tariff_stats_rows_sorted_and_total() -> None:
    sessions = [
        _charge("a", date(2026, 11, 3), 40.0, ABO),
        _charge("b", date(2026, 11, 4), 12.0, HOME, charge_type="ac"),
        _charge("c", date(2026, 11, 5), 5.0, None, charge_type="ac", cost=3.0),
        _charge("old", date(2026, 10, 30), 40.0, ABO),
    ]
    stats = _stats(sessions, [_fee("abo"), _fee("abo", month="2026-10")])
    assert [r.tariff_id for r in stats.rows] == ["abo", "home", None]
    total = stats.total
    assert (total.session_count, total.energy_kwh) == (3, 57.0)
    assert total.cost == 22.6
    assert total.fee == 11.99
    assert total.saving == 0.01  # 40 x 0.79 - 19.60 - 11.99


def test_tariff_stats_empty_month() -> None:
    stats = _stats([], [])
    assert stats.rows == []
    total = stats.total
    assert (total.session_count, total.fee, total.saving) == (0, None, None)


def test_negative_reference_price_is_rejected() -> None:
    with pytest.raises(ValidationError):
        replace(ABO, reference_price_dc=-0.1)
    with pytest.raises(ValidationError):
        PriceVersion(valid_from=date(2026, 1, 1), price_ac=0.4, reference_price_ac=-1)


def test_reference_prices_from_dict_and_has_fee() -> None:
    tariff = Tariff.from_dict(
        {
            "id": "x",
            "name": "X",
            "price_dc": 0.49,
            "reference_price_dc": 0.79,
            "versions": [
                {
                    "valid_from": "2027-01-01",
                    "price_dc": 0.52,
                    "monthly_fee": 9.99,
                    "reference_price_dc": 0.85,
                }
            ],
        }
    )
    assert tariff.reference_price_dc == 0.79
    assert tariff.versions[0].reference_price_dc == 0.85
    assert tariff.versions[0].to_dict()["reference_price_dc"] == 0.85
    assert tariff.has_fee
    assert not HOME.has_fee


# --- CSV export -------------------------------------------------------------

BERLIN = ZoneInfo("Europe/Berlin")
EXPORT_TEXTS = {
    "type": "Typ",
    "date": "Datum",
    "time": "Uhrzeit",
    "energy": "Energie (kWh)",
    "charge_type": "Ladeart",
    "tariff": "Tarif",
    "price": "Preis (EUR/kWh)",
    "cost": "Kosten (EUR)",
    "cost_overridden": "Betrag von Hand",
    "soc_start": "SoC Beginn (%)",
    "soc_end": "SoC Ende (%)",
    "location": "Ort",
    "note": "Notiz",
    "source": "Quelle",
    "type_session": "Ladung",
    "type_fee": "Grundgebühr",
    "yes": "ja",
    "no": "nein",
    "source_manual": "manuell",
    "source_reminder": "Erinnerung",
}


def _export_session(session_id: str, ts: datetime, **kwargs) -> Session:
    defaults = {
        "vehicle_id": "car",
        "energy_kwh": 12.4,
        "charge_type": "ac",
        "cost": 7.32,
        "cost_overridden": False,
        "tariff_id": "enbw",
        "price_per_kwh": 0.59,
    }
    return Session(id=session_id, timestamp=ts, **{**defaults, **kwargs})


def _export(sessions=(), fees=(), start=None, end=None, decimal=","):
    text = export_csv(
        sessions,
        fees,
        {"enbw": "EnBW"},
        start,
        end,
        lambda moment: moment.astimezone(BERLIN),
        EXPORT_TEXTS,
        decimal,
    )
    assert text.startswith("﻿")
    return text[1:].split("\r\n")[:-1]


def test_export_decimal_comma_uses_semicolon() -> None:
    lines = _export([_export_session("a", datetime(2026, 11, 3, 9, 5, tzinfo=UTC))])
    assert lines[0].split(";")[:3] == ["Typ", "Datum", "Uhrzeit"]
    assert lines[1] == (
        "Ladung;2026-11-03;10:05;12,4;AC;EnBW;0,59;7,32;nein;;;;;manuell"
    )


def test_export_decimal_point_uses_comma() -> None:
    lines = _export(
        [_export_session("a", datetime(2026, 11, 3, 9, 5, tzinfo=UTC))], decimal="."
    )
    assert lines[1] == "Ladung,2026-11-03,10:05,12.4,AC,EnBW,0.59,7.32,nein,,,,,manuell"


def test_export_zero_is_not_empty() -> None:
    session = _export_session(
        "a",
        datetime(2026, 11, 3, 9, 0, tzinfo=UTC),
        cost=0.0,
        price_per_kwh=0.0,
        cost_overridden=True,
        soc_start=0.0,
        soc_end=None,
        source="reminder",
    )
    cells = _export([session])[1].split(";")
    assert cells[6:11] == ["0", "0,00", "ja", "0", ""]
    assert cells[13] == "Erinnerung"


def test_export_range_uses_local_day_inclusive() -> None:
    # 23:30 UTC on Nov 30 is Dec 1 in Berlin.
    late = _export_session("late", datetime(2026, 11, 30, 23, 30, tzinfo=UTC))
    early = _export_session("early", datetime(2026, 11, 1, 0, 0, tzinfo=UTC))
    lines = _export([early, late], start=date(2026, 11, 1), end=date(2026, 11, 30))
    assert [line.split(";")[1] for line in lines[1:]] == ["2026-11-01"]
    lines = _export([early, late], start=date(2026, 12, 1), end=date(2026, 12, 1))
    assert [line.split(";")[1:3] for line in lines[1:]] == [["2026-12-01", "00:30"]]


def test_export_open_range_is_everything() -> None:
    sessions = [
        _export_session("a", datetime(2025, 1, 3, 9, 0, tzinfo=UTC)),
        _export_session("b", datetime(2026, 11, 3, 9, 0, tzinfo=UTC)),
    ]
    assert len(_export(sessions)) == 3
    assert len(_export(sessions, start=date(2026, 1, 1))) == 2
    assert len(_export(sessions, end=date(2025, 12, 31))) == 2


def test_export_start_after_end_is_rejected() -> None:
    with pytest.raises(ValidationError) as err:
        _export(start=date(2026, 11, 2), end=date(2026, 11, 1))
    assert err.value.key == "error_export_range"


def test_export_fees_on_the_first_before_sessions() -> None:
    sessions = [_export_session("a", datetime(2026, 11, 1, 6, 0, tzinfo=UTC))]
    fees = [
        Fee("enbw", "car", "2026-11", 11.99),
        Fee("gone", "car", "2026-10", 5.0),
        Fee("enbw", "car", "2026-12", 11.99),
    ]
    lines = _export(sessions, fees, start=date(2026, 10, 1), end=date(2026, 11, 30))
    assert [line.split(";")[:3] for line in lines[1:]] == [
        ["Grundgebühr", "2026-10-01", ""],
        ["Grundgebühr", "2026-11-01", ""],
        ["Ladung", "2026-11-01", "07:00"],
    ]
    # Deleted tariff: empty name; the amount is in the cost column.
    assert lines[1].split(";")[5:8] == ["", "", "5,00"]
    assert lines[2].split(";")[5:8] == ["EnBW", "", "11,99"]
    # A fee in the range but not the first of its month is outside.
    assert len(_export(fees=fees, start=date(2026, 11, 2))) == 2


def test_export_deleted_tariff_and_no_tariff_are_empty() -> None:
    sessions = [
        _export_session("a", datetime(2026, 11, 3, 9, 0, tzinfo=UTC), tariff_id="gone"),
        _export_session(
            "b",
            datetime(2026, 11, 4, 9, 0, tzinfo=UTC),
            tariff_id=None,
            price_per_kwh=None,
            cost_overridden=True,
        ),
    ]
    lines = _export(sessions)
    assert lines[1].split(";")[5] == ""
    assert lines[2].split(";")[5:7] == ["", ""]


def test_export_quotes_text_and_defuses_formulas() -> None:
    session = _export_session(
        "a",
        datetime(2026, 11, 3, 9, 0, tzinfo=UTC),
        note='Säule "3"; defekt\nzweite Zeile',
        location="=HYPERLINK(1)",
    )
    text = export_csv(
        [session],
        [],
        {"enbw": "-EnBW"},
        None,
        None,
        lambda moment: moment.astimezone(BERLIN),
        EXPORT_TEXTS,
        ",",
    )
    rows = list(csv.reader(io.StringIO(text[1:]), delimiter=";"))
    assert rows[1][5] == "'-EnBW"
    assert rows[1][11] == "'=HYPERLINK(1)"
    assert rows[1][12] == 'Säule "3"; defekt\nzweite Zeile'
