"""Tests for the CSV import (stage 7), without Home Assistant."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from zoneinfo import ZoneInfo

import pytest

from custom_components.ev_charge_log.models import (
    Fee,
    PriceVersion,
    Session,
    Tariff,
    ValidationError,
    Vehicle,
    export_csv,
    parse_import_csv,
)
from custom_components.ev_charge_log.storage import SessionLog

BERLIN = ZoneInfo("Europe/Berlin")
DE = {
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
EN = {
    "type": "Type",
    "date": "Date",
    "time": "Time",
    "energy": "Energy (kWh)",
    "charge_type": "Charge type",
    "tariff": "Tariff",
    "price": "Price (EUR/kWh)",
    "cost": "Cost (EUR)",
    "cost_overridden": "Amount entered",
    "soc_start": "SoC start (%)",
    "soc_end": "SoC end (%)",
    "location": "Location",
    "note": "Note",
    "source": "Source",
    "type_session": "Session",
    "type_fee": "Monthly fee",
    "yes": "yes",
    "no": "no",
    "source_manual": "manual",
    "source_reminder": "reminder",
}
TEXTS = (DE, EN)

ENBW = Tariff(
    id="enbw",
    name="EnBW",
    price_ac=0.59,
    price_dc=0.69,
    versions=(PriceVersion(datetime(2026, 7, 1).date(), price_ac=0.49, price_dc=0.59),),
)
EWE = Tariff(id="ewe", name="EWE Go", price_dc=0.62)
FREE = Tariff(id="free", name="Employer", price_ac=0.0)
TARIFFS = {t.id: t for t in (ENBW, EWE, FREE)}
CAR = Vehicle(id="car", name="Seal U", battery_capacity_kwh=80.0, charging_loss_ac=10)


def _local(moment: datetime) -> datetime:
    return moment.astimezone(BERLIN)


def _log(sessions=()) -> SessionLog:
    ids = count(1)
    return SessionLog(sessions, id_factory=lambda: f"new{next(ids)}")


def _import(text: str, log: SessionLog | None = None, vehicle: Vehicle = CAR):
    log = log if log is not None else _log()
    parsed = parse_import_csv(text, TEXTS, BERLIN)
    result = log.import_rows(vehicle, parsed.rows, TARIFFS, _local)
    return parsed, result, log


def _session(session_id: str, ts: datetime, **kwargs) -> Session:
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


SESSIONS = [
    _session(
        "a",
        datetime(2026, 3, 29, 0, 30, tzinfo=UTC),  # day of the DST change
        soc_start=20.0,
        soc_end=55.5,
        location="=Zuhause",
        note='Säule "3"; defekt\nzweite Zeile',
        source="reminder",
    ),
    _session(
        "b",
        datetime(2026, 11, 3, 17, 5, tzinfo=UTC),
        tariff_id="gone",
        cost=0.0,
        price_per_kwh=0.0,
        cost_overridden=True,
        charge_type="dc",
    ),
    _session(
        "c",
        datetime(2026, 11, 4, 8, 0, tzinfo=UTC),
        tariff_id=None,
        price_per_kwh=None,
        cost=5.5,
        cost_overridden=True,
    ),
]
FEES = [Fee("enbw", "car", "2026-11", 11.99)]


def _key(session: Session) -> tuple:
    return (
        _local(session.timestamp).replace(second=0, microsecond=0),
        session.energy_kwh,
        session.charge_type,
        session.price_per_kwh,
        session.cost,
        session.cost_overridden,
        session.soc_start,
        session.soc_end,
        session.location,
        session.note,
        session.source,
    )


@pytest.mark.parametrize(("texts", "decimal"), [(DE, ","), (EN, ".")])
def test_round_trip_of_the_export(texts, decimal) -> None:
    text = export_csv(
        SESSIONS,
        FEES,
        {"enbw": "EnBW"},
        None,
        None,
        _local,
        texts,
        decimal,
    )
    parsed, result, _ = _import(text)
    assert parsed.fees_skipped == 1
    assert not parsed.problems and not result.problems
    assert [_key(s) for s in result.sessions] == [_key(s) for s in SESSIONS]
    # Tariff by name; a deleted tariff has no name in the file.
    assert [s.tariff_id for s in result.sessions] == ["enbw", None, None]
    assert all(s.vehicle_id == "car" for s in result.sessions)


def test_second_import_only_finds_duplicates() -> None:
    text = export_csv(SESSIONS, [], {"enbw": "EnBW"}, None, None, _local, DE, ",")
    _, first, log = _import(text)
    _, second, _ = _import(text, log)
    assert len(first.sessions) == 3
    assert second.sessions == [] and second.duplicates == 3
    assert len(log.sessions(vehicle_id="car")) == 3


def test_duplicate_within_the_file_and_other_vehicle() -> None:
    other = _session("x", datetime(2026, 11, 3, 9, 0, tzinfo=UTC), vehicle_id="dolphin")
    text = (
        "Datum;Uhrzeit;Energie (kWh);Ladeart;Kosten (EUR)\n"
        "2026-11-03;10:00;12,4;AC;5\n"
        "2026-11-03;10:00;12,40;AC;6\n"
        "2026-11-03;10:00;12,5;AC;6\n"
    )
    _, result, _ = _import(text, _log([other]))
    assert len(result.sessions) == 2
    assert result.duplicates == 1


def test_columns_in_any_order_optional_missing_and_unknown_ignored() -> None:
    text = "Extra;Ladeart;kosten (USD);DATUM;Energie\nx;dc;9,99;3.11.2026;20\n"
    _, result, _ = _import(text)
    session = result.sessions[0]
    assert session.charge_type == "dc"
    assert session.cost == 9.99 and session.cost_overridden is True
    assert session.price_per_kwh is None and session.tariff_id is None
    # Missing time: 12:00 local time.
    assert _local(session.timestamp) == datetime(2026, 11, 3, 12, 0, tzinfo=BERLIN)
    assert session.source == "manual"


def test_english_comma_separated_file() -> None:
    text = (
        "Date,Time,Energy (kWh),Charge type,Tariff,Cost (EUR)\n"
        "2026-11-03,9:05,10.5,AC,enbw,\n"
    )
    _, result, _ = _import(text)
    session = result.sessions[0]
    # Without cost: from the tariff version valid on the day.
    assert session.tariff_id == "enbw"
    assert session.price_per_kwh == 0.49
    assert session.cost == 5.15 and session.cost_overridden is False


def test_cost_and_price_zero_stay_zero() -> None:
    text = (
        "Datum;Energie;Ladeart;Tarif;Preis;Kosten;Betrag von Hand\n"
        "2026-11-03;15;AC;Employer;0;0;nein\n"
        "2026-11-04;15;AC;Employer;;;\n"
    )
    _, result, _ = _import(text)
    first, second = result.sessions
    assert (first.price_per_kwh, first.cost, first.cost_overridden) == (0.0, 0.0, False)
    assert (second.price_per_kwh, second.cost, second.cost_overridden) == (
        0.0,
        0.0,
        False,
    )


def test_cost_of_the_file_is_the_snapshot() -> None:
    # The tariff price changed since, the file keeps its values.
    text = (
        "Datum;Energie;Ladeart;Tarif;Preis;Kosten;Betrag von Hand\n"
        "2026-11-03;10;AC;enbw;0,79;7,90;nein\n"
    )
    _, result, _ = _import(text)
    session = result.sessions[0]
    assert (session.price_per_kwh, session.cost, session.cost_overridden) == (
        0.79,
        7.9,
        False,
    )


def test_unknown_tariff_with_cost_books_without_tariff() -> None:
    text = "Datum;Energie;Ladeart;Tarif;Kosten\n2026-11-03;10;AC;Ionity;8\n"
    _, result, _ = _import(text)
    assert result.sessions[0].tariff_id is None
    assert result.sessions[0].cost == 8.0


def test_unknown_tariff_without_cost_is_a_problem() -> None:
    text = "Datum;Energie;Ladeart;Tarif;Kosten\n2026-11-03;10;AC;Ionity;\n"
    _, result, _ = _import(text)
    assert result.sessions == []
    assert [(p.row, p.key) for p in result.problems] == [(2, "error_cost_or_tariff")]


@pytest.mark.parametrize(
    ("tariff", "charge_type"), [("EWE Go", "AC"), ("Employer", "DC")]
)
def test_charge_type_without_tariff_price_is_rejected(tariff, charge_type) -> None:
    text = (
        f"Datum;Energie;Ladeart;Tarif;Kosten\n2026-11-03;10;{charge_type};{tariff};5\n"
    )
    _, result, _ = _import(text)
    assert result.sessions == []
    assert result.problems[0].key == "error_no_price"


def test_energy_from_soc_with_charging_loss() -> None:
    text = "Datum;Energie;Ladeart;Kosten;SoC Beginn;SoC Ende\n2026-11-03;;AC;5;20;70\n"
    _, result, _ = _import(text)
    # 50 % of 80 kWh plus 10 % charging loss.
    assert result.sessions[0].energy_kwh == 44.0


def test_energy_from_soc_needs_the_capacity() -> None:
    text = "Datum;Ladeart;Kosten;SoC Beginn;SoC Ende\n2026-11-03;AC;5;20;70\n"
    _, result, _ = _import(text, vehicle=Vehicle(id="car", name="Car"))
    assert result.problems[0].key == "error_energy_soc_capacity"


def test_invalid_rows_are_problems_valid_rows_are_booked() -> None:
    text = (
        "Datum;Uhrzeit;Energie;Ladeart;Kosten;Betrag von Hand;SoC Beginn\n"
        "2026-11-03;10:00;10;AC;5;;\n"
        "03/11/2026;10:00;10;AC;5;;\n"
        "2026-11-04;25:00;10;AC;5;;\n"
        "2026-11-05;10:00;zehn;AC;5;;\n"
        "2026-11-06;10:00;10;HPC;5;;\n"
        "2026-11-07;10:00;10;AC;-1;;\n"
        "2026-11-08;10:00;0;AC;5;;\n"
        "2026-11-09;10:00;10;AC;5;vielleicht;\n"
        ";;;;;;\n"
        "2026-11-10;10:00;10;AC;5;;120\n"
    )
    _, result, _ = _import(text)
    parsed = parse_import_csv(text, TEXTS, BERLIN)
    assert len(result.sessions) == 1
    problems = [(p.row, p.key) for p in [*parsed.problems, *result.problems]]
    assert problems == [
        (3, "error_import_value"),
        (4, "error_import_value"),
        (5, "error_import_value"),
        (6, "error_charge_type"),
        (9, "error_import_value"),
        (7, "error_negative_cost"),
        (8, "error_energy"),
        (11, "error_soc_start_range"),
    ]
    assert parsed.problems[0].placeholders == {"column": "Datum", "value": "03/11/2026"}


def test_decimal_comma_only_with_semicolon() -> None:
    text = 'Date,Energy,Charge type,Cost\n2026-11-03,"10,5",AC,5\n'
    parsed = parse_import_csv(text, TEXTS, BERLIN)
    assert parsed.rows == []
    assert parsed.problems[0].key == "error_import_value"


def test_formula_protection_is_removed_and_bom_ignored() -> None:
    text = (
        "﻿Datum;Energie;Ladeart;Kosten;Ort;Notiz\n2026-11-03;10;AC;5;'=Zuhause;'Hallo\n"
    )
    _, result, _ = _import(text)
    assert result.sessions[0].location == "=Zuhause"
    # Only the quote of the formula protection is removed.
    assert result.sessions[0].note == "'Hallo"


def test_missing_required_column_rejects_the_file() -> None:
    for header in ("Energie;Ladeart", "Datum;Energie", "Datum;Ladeart;SoC Beginn"):
        with pytest.raises(ValidationError) as err:
            parse_import_csv(f"{header}\n", TEXTS, BERLIN)
        assert err.value.key == "error_import_header"
    with pytest.raises(ValidationError) as err:
        parse_import_csv("", TEXTS, BERLIN)
    assert err.value.key == "error_import_header"


def test_too_large_file_is_rejected() -> None:
    rows = "".join("2026-11-03;10;AC;5\n" for _ in range(5001))
    with pytest.raises(ValidationError) as err:
        parse_import_csv("Datum;Energie;Ladeart;Kosten\n" + rows, TEXTS, BERLIN)
    assert err.value.key == "error_import_too_large"


def test_source_and_yes_no_in_both_languages() -> None:
    text = (
        "Datum;Uhrzeit;Energie;Ladeart;Kosten;Betrag von Hand;Quelle\n"
        "2026-11-03;10:00;10;AC;5;yes;reminder\n"
        "2026-11-04;10:00;10;AC;5;ja;Erinnerung\n"
        "2026-11-05;10:00;10;AC;5;no;manuell\n"
    )
    _, result, _ = _import(text)
    assert [(s.cost_overridden, s.source) for s in result.sessions] == [
        (True, "reminder"),
        (True, "reminder"),
        (False, "manual"),
    ]
