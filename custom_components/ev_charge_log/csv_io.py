"""CSV export (stage 6) and import (stage 7) of sessions.

This module must not import Home Assistant so it can be tested on its own.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, tzinfo

from .const import CHARGE_TYPES, SOURCE_MANUAL, SOURCES
from .models import (
    Fee,
    Session,
    Tariff,
    ValidationError,
    Vehicle,
    calculate_cost,
    energy_from_soc,
    round_money,
)

# --- CSV export (stage 6) ---------------------------------------------------

# Column keys of the export, in this order; the header texts come from the
# translations ("panel_export_col_<key>").
EXPORT_COLUMNS = (
    "type",
    "date",
    "time",
    "energy",
    "charge_type",
    "tariff",
    "price",
    "cost",
    "cost_overridden",
    "soc_start",
    "soc_end",
    "location",
    "note",
    "source",
)
# Value texts of the export ("panel_export_<key>").
EXPORT_VALUES = (
    "type_session",
    "type_fee",
    "yes",
    "no",
    "source_manual",
    "source_reminder",
)
# Excel runs cells starting with these as formulas.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_text(value: str | None) -> str:
    """Free text for a CSV cell; formulas are defused with a leading quote."""
    if not value:
        return ""
    return f"'{value}" if value.startswith(_FORMULA_START) else value


def _csv_number(value: float | None, decimal: str) -> str:
    """A number without thousands separator; None is an empty cell, 0 is "0"."""
    if value is None:
        return ""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text.replace(".", decimal)


def _csv_money(value: float, decimal: str) -> str:
    return f"{round_money(value):.2f}".replace(".", decimal)


def export_csv(
    sessions: Iterable[Session],
    fees: Iterable[Fee],
    tariff_names: Mapping[str, str],
    start: date | None,
    end: date | None,
    to_local: Callable[[datetime], datetime],
    texts: Mapping[str, str],
    decimal: str = ".",
) -> str:
    """Sessions and booked monthly fees as CSV for Excel, oldest first.

    A session counts on its local day, a fee on the first of its month;
    `start` and `end` are inclusive, None is open. `texts` holds the header
    texts (`EXPORT_COLUMNS`) and value texts (`EXPORT_VALUES`). With a decimal
    comma the separator is ";", else ",". Starts with a BOM so Excel reads
    UTF-8. Tariffs not in `tariff_names` (deleted) give an empty name.
    """
    if start is not None and end is not None and start > end:
        raise ValidationError("start of the export is after its end", "export_range")

    def inside(day: date) -> bool:
        return (start is None or day >= start) and (end is None or day <= end)

    def tariff_name(tariff_id: str | None) -> str:
        return _csv_text(tariff_names.get(tariff_id, "")) if tariff_id else ""

    rows: list[tuple[tuple[date, int, str], list[str]]] = []
    for fee in fees:
        day = date.fromisoformat(f"{fee.month}-01")
        if not inside(day):
            continue
        row = dict.fromkeys(EXPORT_COLUMNS, "")
        row["type"] = texts["type_fee"]
        row["date"] = day.isoformat()
        row["tariff"] = tariff_name(fee.tariff_id)
        row["cost"] = _csv_money(fee.amount, decimal)
        # A fee counts at 00:00 on the first, before sessions of that day.
        rows.append(((day, 0, ""), list(row.values())))
    for session in sessions:
        local = to_local(session.timestamp)
        day = local.date()
        if not inside(day):
            continue
        clock = local.strftime("%H:%M")
        rows.append(
            (
                (day, 1, local.strftime("%H:%M:%S.%f")),
                [
                    texts["type_session"],
                    day.isoformat(),
                    clock,
                    _csv_number(session.energy_kwh, decimal),
                    session.charge_type.upper(),
                    tariff_name(session.tariff_id),
                    _csv_number(session.price_per_kwh, decimal),
                    _csv_money(session.cost, decimal),
                    texts["yes"] if session.cost_overridden else texts["no"],
                    _csv_number(session.soc_start, decimal),
                    _csv_number(session.soc_end, decimal),
                    _csv_text(session.location),
                    _csv_text(session.note),
                    texts[f"source_{session.source}"],
                ],
            )
        )
    # Stable sort keeps the booking order of sessions at the same time.
    rows.sort(key=lambda item: item[0])

    buffer = io.StringIO()
    writer = csv.writer(
        buffer, delimiter=";" if decimal == "," else ",", lineterminator="\r\n"
    )
    writer.writerow([texts[key] for key in EXPORT_COLUMNS])
    writer.writerows(cells for _, cells in rows)
    return "﻿" + buffer.getvalue()


# --- CSV import (stage 7) ---------------------------------------------------

# Limits of one import file (characters and data rows).
IMPORT_MAX_CHARS = 1_000_000
IMPORT_MAX_ROWS = 5000
# Columns an import file needs; the energy may come from the SoC instead.
_IMPORT_REQUIRED = ("date", "charge_type")
_IMPORT_ENERGY = ("energy", "soc_start", "soc_end")
# Time of a row without a time.
_IMPORT_NOON = time(12, 0)
_BRACKETS = re.compile(r"\s*\([^)]*\)\s*$")
_IMPORT_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
_IMPORT_DATE_DOTTED = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_IMPORT_TIME = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2}(?:\.\d+)?)?$")


@dataclass(frozen=True, slots=True)
class ImportRow:
    """One session row of an import file, parsed but not booked.

    `row` is the row number as a spreadsheet shows it (header = 1).
    `timestamp` is aware, in the time zone of the import.
    """

    row: int
    timestamp: datetime
    energy_kwh: float | None
    charge_type: str
    tariff_name: str | None = None
    price_per_kwh: float | None = None
    cost: float | None = None
    cost_overridden: bool | None = None
    soc_start: float | None = None
    soc_end: float | None = None
    location: str | None = None
    note: str | None = None
    source: str = SOURCE_MANUAL


@dataclass(frozen=True, slots=True)
class ImportProblem:
    """A row that was not imported, with the translation key of the reason."""

    row: int
    key: str
    placeholders: dict[str, str]


@dataclass(slots=True)
class ParsedImport:
    """Result of reading an import file."""

    rows: list[ImportRow]
    fees_skipped: int = 0
    problems: list[ImportProblem] = field(default_factory=list)


@dataclass(slots=True)
class ImportResult:
    """Result of booking the rows of an import file."""

    sessions: list[Session]
    duplicates: int = 0
    problems: list[ImportProblem] = field(default_factory=list)


def _header_key(text: str) -> str:
    """Header text without unit in brackets, for comparing ("Kosten (EUR)")."""
    return _BRACKETS.sub("", text.strip()).strip().casefold()


def _import_text(value: str) -> str | None:
    """Free text of a cell; the export's formula protection is removed."""
    value = value.strip()
    if value.startswith("'") and value[1:2] and value[1] in "=+-@\t\r":
        value = value[1:]
    return value or None


def _import_value_error(column: str, value: str) -> ValidationError:
    return ValidationError(
        f"invalid value {value!r} in column {column}",
        "import_value",
        column=column,
        value=value,
    )


def _import_number(value: str, column: str, comma: bool) -> float | None:
    text = value.strip()
    if not text:
        return None
    if comma:
        text = text.replace(",", ".")
    if not _IMPORT_NUMBER.match(text):
        raise _import_value_error(column, value)
    return float(text)


def _import_date(value: str, column: str) -> date:
    text = value.strip()
    dotted = _IMPORT_DATE_DOTTED.match(text)
    try:
        if dotted:
            day, month, year = (int(part) for part in dotted.groups())
            return date(year, month, day)
        return date.fromisoformat(text)
    except ValueError:
        raise _import_value_error(column, value) from None


def _import_time(value: str, column: str) -> time:
    text = value.strip()
    if not text:
        return _IMPORT_NOON
    match = _IMPORT_TIME.match(text)
    try:
        if match is None:
            raise ValueError
        return time(int(match[1]), int(match[2]))
    except ValueError:
        raise _import_value_error(column, value) from None


def parse_import_csv(
    text: str, texts: Iterable[Mapping[str, str]], tz: tzinfo
) -> ParsedImport:
    """Read a CSV file in the format of `export_csv` (any of its languages).

    `texts` holds the header and value texts of each language, like the
    `texts` of `export_csv`. Columns are found by their header text (without
    the unit in brackets, case-insensitive) or their key, in any order;
    unknown columns are ignored. With ";" as separator a decimal comma is
    allowed. Dates as ISO or "D.M.YYYY", a missing time is 12:00 in `tz`.
    Monthly fee rows are skipped (fees are booked automatically). A broken
    file raises `ValidationError`; broken rows become problems.
    """
    if len(text) > IMPORT_MAX_CHARS:
        raise ValidationError("the import file is too large", "import_too_large")
    text = text.removeprefix("﻿")
    first_line = text.split("\n", 1)[0]
    delimiter = ";" if first_line.count(";") >= first_line.count(",") else ","
    comma = delimiter == ";"
    records = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if len(records) - 1 > IMPORT_MAX_ROWS:
        raise ValidationError("the import file is too large", "import_too_large")

    texts = list(texts)
    names: dict[str, str] = {}
    for language in texts:
        for key in EXPORT_COLUMNS:
            names.setdefault(_header_key(language.get(key, key)), key)
    for key in EXPORT_COLUMNS:
        names.setdefault(key, key)
        names.setdefault(key.replace("_", " "), key)

    header = records[0] if records else []
    columns: dict[str, int] = {}
    captions: dict[str, str] = {}
    for index, caption in enumerate(header):
        key = names.get(_header_key(caption.removeprefix("﻿")))
        if key is not None and key not in columns:
            columns[key] = index
            captions[key] = caption.strip()
    if not all(key in columns for key in _IMPORT_REQUIRED) or not (
        "energy" in columns or {"soc_start", "soc_end"} <= columns.keys()
    ):
        raise ValidationError(
            "the import file has no date, charge type or energy column",
            "import_header",
        )

    def values(*keys: str) -> set[str]:
        return {
            language[key].strip().casefold()
            for language in texts
            for key in keys
            if language.get(key)
        }

    fee_texts = values("type_fee")
    yes_texts = values("yes") | {"yes", "true", "1"}
    no_texts = values("no") | {"no", "false", "0"}
    sources = {SOURCE_MANUAL: SOURCE_MANUAL, "reminder": "reminder"}
    for name in SOURCES:
        sources |= dict.fromkeys(values(f"source_{name}"), name)

    result = ParsedImport(rows=[])
    for number, record in enumerate(records[1:], start=2):
        if not any(cell.strip() for cell in record):
            continue

        def cell(key: str, record: list[str] = record) -> str:
            index = columns.get(key)
            return record[index] if index is not None and index < len(record) else ""

        if cell("type").strip().casefold() in fee_texts:
            result.fees_skipped += 1
            continue
        try:
            numbers = {
                key: _import_number(cell(key), captions.get(key, key), comma)
                for key in ("energy", "price", "cost", "soc_start", "soc_end")
            }
            day = _import_date(cell("date"), captions["date"])
            moment = _import_time(cell("time"), captions.get("time", "time"))
            charge_type = cell("charge_type").strip().lower()
            if charge_type not in CHARGE_TYPES:
                raise ValidationError(
                    f"invalid charge_type: {charge_type}",
                    "charge_type",
                    charge_type=cell("charge_type").strip(),
                )
            overridden_text = cell("cost_overridden").strip().casefold()
            if not overridden_text:
                overridden = None
            elif overridden_text in yes_texts:
                overridden = True
            elif overridden_text in no_texts:
                overridden = False
            else:
                raise _import_value_error(
                    captions["cost_overridden"], cell("cost_overridden")
                )
        except ValidationError as err:
            result.problems.append(ImportProblem(number, err.key, err.placeholders))
            continue
        result.rows.append(
            ImportRow(
                row=number,
                timestamp=datetime.combine(day, moment, tzinfo=tz),
                energy_kwh=numbers["energy"],
                charge_type=charge_type,
                tariff_name=_import_text(cell("tariff")),
                price_per_kwh=numbers["price"],
                cost=numbers["cost"],
                cost_overridden=overridden,
                soc_start=numbers["soc_start"],
                soc_end=numbers["soc_end"],
                location=_import_text(cell("location")),
                note=_import_text(cell("note")),
                source=sources.get(cell("source").strip().casefold(), SOURCE_MANUAL),
            )
        )
    return result


def duplicate_key(timestamp: datetime, energy_kwh: float) -> tuple[str, float]:
    """Sessions with the same local minute and energy count as the same."""
    return timestamp.strftime("%Y-%m-%dT%H:%M"), round(energy_kwh, 2)


def build_imported_session(
    session_id: str,
    vehicle: Vehicle,
    row: ImportRow,
    tariff: Tariff | None,
) -> Session:
    """Book an imported row: the cost of the file is the snapshot.

    Without cost in the row, it is calculated from the tariff like a
    booking. A tariff without a price for the charge type rejects the row,
    also with a cost. Without energy, it comes from the row's SoC, the
    battery capacity and the charging loss (`energy_from_soc`).
    """
    energy = row.energy_kwh
    if energy is None:
        energy = energy_from_soc(
            row.soc_start,
            row.soc_end,
            vehicle.battery_capacity_kwh,
            vehicle.charging_loss(row.charge_type),
        )
    day = row.timestamp.date()
    result = calculate_cost(energy, row.charge_type, tariff, row.cost, day)
    if row.cost is None:
        price, overridden = result.price_per_kwh, False
    else:
        price = row.price_per_kwh
        overridden = True if row.cost_overridden is None else row.cost_overridden
    return Session(
        id=session_id,
        vehicle_id=vehicle.id,
        timestamp=row.timestamp,
        energy_kwh=energy,
        tariff_id=tariff.id if tariff is not None else None,
        charge_type=row.charge_type,
        price_per_kwh=price,
        cost=result.cost,
        cost_overridden=overridden,
        soc_start=row.soc_start,
        soc_end=row.soc_end,
        location=row.location,
        note=row.note,
        source=row.source,
    )
