"""PDF of the annual or monthly report (stage 9), made with fpdf2.

This module must not import Home Assistant so it can be tested on its own.
Building the PDF reads the font files and blocks; Home Assistant runs it in
the executor.

The fonts (Noto Sans, SIL Open Font License 1.1) are shipped in `fonts/`:
the built-in PDF fonts only cover Latin-1, which is not enough for every
currency and language. fpdf2 embeds only the characters used.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, tzinfo
from pathlib import Path

from fpdf import FPDF, FontFace

from .models import Session
from .reports import Report, ReportRow

FONT_DIR = Path(__file__).parent / "fonts"
FONT = "NotoSans"
# Between start and end SoC ("33", en dash, "55"); the panel shows an arrow,
# which Noto Sans does not have.
EN_DASH = "\N{EN DASH}"

MONTHS_EN = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

# Translation keys ("exceptions" category, without the component prefix)
# the PDF needs; the panel shows the report with the same texts.
REPORT_TEXT_KEYS = (
    "panel_report_title_year",
    "panel_report_title_month",
    "panel_report_all_vehicles",
    "panel_report_filter_tariff",
    "panel_report_filter_note",
    "panel_report_created",
    "panel_report_current_year",
    "panel_tariff_current_month",
    "panel_report_summary",
    "panel_report_cost_total",
    "panel_report_fees",
    "panel_report_average",
    "panel_report_free",
    "panel_report_per_month",
    "panel_report_per_day",
    "panel_report_per_tariff",
    "panel_report_charge_types",
    "panel_report_per_vehicle",
    "panel_report_col_month",
    "panel_report_col_day",
    "panel_report_col_vehicle",
    "panel_report_col_time",
    "panel_report_col_average",
    "panel_report_empty",
    "panel_report_no_fees_note",
    "panel_report_page",
    "panel_sessions",
    "panel_soc",
    "panel_chart_energy",
    "panel_field_date",
    "panel_field_note",
    "panel_field_charge_type",
    "panel_tariff_col_tariff",
    "panel_tariff_col_sessions",
    "panel_tariff_col_energy",
    "panel_tariff_col_cost",
    "panel_tariff_col_fee",
    "panel_tariff_without",
    "panel_tariff_total",
)

_BOLD = FontFace(emphasis="BOLD")
_HEADING = FontFace(emphasis="BOLD", fill_color=(235, 235, 235))


@dataclass(frozen=True, slots=True)
class PdfFormat:
    """Number format and month names of the page the PDF is made from."""

    decimal: str = "."
    group: str = ""
    months: tuple[str, ...] = MONTHS_EN
    currency: str | None = None  # symbol, e.g. "€"; None: the currency code


@dataclass(frozen=True, slots=True)
class ReportContext:
    """What the report is about, for the head of the PDF and the names."""

    currency: str  # currency code of HA, e.g. "EUR"
    created: date
    tz: tzinfo
    vehicle_name: str | None = None  # None: all vehicles
    tariff_name: str | None = None  # tariff filter
    note: str | None = None  # note filter
    vehicle_names: Mapping[str, str] = field(default_factory=dict)
    tariff_names: Mapping[str, str] = field(default_factory=dict)


def report_filename(period: str, vehicle_name: str | None = None) -> str:
    """`ev-charge-log-<year>[-<month>][-<vehicle>].pdf`."""
    name = f"ev-charge-log-{period}"
    if vehicle_name:
        slug = re.sub(r"[^a-z0-9]+", "-", vehicle_name.casefold()).strip("-")
        if slug:
            name += f"-{slug}"
    return f"{name}.pdf"


def _number(value: float, digits: int, fmt: PdfFormat) -> str:
    text = f"{value:,.{digits}f}"
    return text.replace(",", "\0").replace(".", fmt.decimal).replace("\0", fmt.group)


def _energy(value: float, fmt: PdfFormat) -> str:
    """kWh with up to two decimals, without trailing zeros."""
    text = _number(value, 2, fmt)
    if fmt.decimal in text:
        text = text.rstrip("0").rstrip(fmt.decimal)
    return text


def _month_name(month: str, fmt: PdfFormat) -> str:
    year, number = month.split("-")
    return f"{fmt.months[int(number) - 1]} {year}"


def _soc(session: Session) -> str:
    """SoC range like "33 - 55" with an en dash (Noto Sans has no arrow)."""
    if session.soc_start is None and session.soc_end is None:
        return ""
    start = "" if session.soc_start is None else f"{session.soc_start:g}"
    end = "" if session.soc_end is None else f"{session.soc_end:g}"
    return f"{start} {EN_DASH} {end}".strip()


class _ReportPdf(FPDF):
    def __init__(self, footer_text: str) -> None:
        super().__init__(orientation="portrait", unit="mm", format="A4")
        self._footer_text = footer_text
        self.add_font(FONT, "", str(FONT_DIR / "NotoSans-Regular.ttf"))
        self.add_font(FONT, "B", str(FONT_DIR / "NotoSans-Bold.ttf"))
        self.set_margins(15, 15, 15)
        self.set_auto_page_break(auto=True, margin=18)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font(FONT, "", 8)
        self.set_text_color(110, 110, 110)
        self.cell(0, 5, "EV Charge Log", align="L")
        self.set_x(self.l_margin)
        page = self._footer_text.format(page=self.page_no(), pages="{nb}")
        self.cell(0, 5, page, align="R")
        self.set_text_color(0, 0, 0)


def report_pdf(
    result: Report,
    texts: Mapping[str, str],
    fmt: PdfFormat,
    context: ReportContext,
) -> bytes:
    """The report as a PDF (A4 portrait), in the order of the panel.

    `texts` maps the keys in REPORT_TEXT_KEYS to the translated texts.
    """

    def text(key: str, **placeholders: str) -> str:
        return texts.get(key, key).format(**placeholders)

    currency = fmt.currency or context.currency

    def money(value: float) -> str:
        return _number(value, 2, fmt)

    def average(row: ReportRow) -> str:
        value = row.average_price
        return "" if value is None else _number(value, 2, fmt)

    pdf = _ReportPdf(text("panel_report_page", page="{page}", pages="{pages}"))
    pdf.add_page()

    # --- head ---------------------------------------------------------------
    monthly = len(result.period) == 7
    title = (
        text("panel_report_title_month", month=_month_name(result.period, fmt))
        if monthly
        else text("panel_report_title_year", year=result.period)
    )
    pdf.set_font(FONT, "B", 16)
    pdf.multi_cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(FONT, "", 10)
    lines = [context.vehicle_name or text("panel_report_all_vehicles")]
    if context.tariff_name:
        lines.append(text("panel_report_filter_tariff", tariff=context.tariff_name))
    if context.note and context.note.strip():
        lines.append(text("panel_report_filter_note", note=context.note.strip()))
    lines.append(text("panel_report_created", date=context.created.isoformat()))
    if result.current:
        lines.append(
            text(
                "panel_tariff_current_month" if monthly else "panel_report_current_year"
            )
        )
    for line in lines:
        pdf.multi_cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    if result.fees_excluded:
        pdf.set_text_color(110, 110, 110)
        pdf.multi_cell(
            0, 5, text("panel_report_no_fees_note"), new_x="LMARGIN", new_y="NEXT"
        )
        pdf.set_text_color(0, 0, 0)

    def heading(key: str) -> None:
        # Never a heading with only its table head below it at the page end.
        if pdf.will_page_break(40):
            pdf.add_page()
        else:
            pdf.ln(4)
        pdf.set_font(FONT, "B", 12)
        pdf.cell(0, 7, text(key), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(FONT, "", 9)

    # --- summary ------------------------------------------------------------
    summary = result.summary
    heading("panel_report_summary")
    with pdf.table(
        width=110,
        align="LEFT",
        col_widths=(60, 50),
        text_align=("LEFT", "RIGHT"),
        first_row_as_headings=False,
        borders_layout="HORIZONTAL_LINES",
    ) as table:
        for label, value in (
            (text("panel_tariff_col_sessions"), str(summary.session_count)),
            (text("panel_chart_energy"), f"{_energy(summary.energy_kwh, fmt)} kWh"),
            (text("panel_report_cost_total"), f"{money(summary.total)} {currency}"),
            (text("panel_report_fees"), f"{money(summary.fee)} {currency}"),
            (
                text("panel_report_average"),
                f"{average(summary)} {currency}" if summary.average_price else "",
            ),
            (text("panel_report_free"), f"{_energy(result.free_energy_kwh, fmt)} kWh"),
        ):
            row = table.row()
            row.cell(label)
            row.cell(value)

    if not result.periods and not result.tariffs:
        pdf.ln(4)
        pdf.multi_cell(0, 5, text("panel_report_empty"), new_x="LMARGIN", new_y="NEXT")
        return bytes(pdf.output())

    cost_head = f"{text('panel_tariff_col_cost')} ({currency})"
    fee_head = f"{text('panel_tariff_col_fee')} ({currency})"
    average_head = f"{text('panel_report_col_average')} ({currency})"
    numbers = ("RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT")

    def totals_table(
        first_head: str,
        rows: list[tuple[str, ReportRow]],
        total: ReportRow | None,
        *,
        with_fee: bool = True,
    ) -> None:
        head = [
            first_head,
            text("panel_tariff_col_sessions"),
            text("panel_tariff_col_energy"),
            cost_head,
        ]
        if with_fee:
            head.append(fee_head)
        head.append(average_head)
        with pdf.table(
            text_align=("LEFT", *numbers[: len(head) - 1]),
            headings_style=_HEADING,
            borders_layout="HORIZONTAL_LINES",
        ) as table:
            table.row(head)

            def add(label: str, row: ReportRow, style: FontFace | None) -> None:
                cells = [
                    label,
                    str(row.session_count),
                    _energy(row.energy_kwh, fmt),
                    money(row.cost),
                ]
                if with_fee:
                    cells.append(money(row.fee))
                cells.append(average(row))
                table.row(cells, style=style)

            for label, row in rows:
                add(label, row, None)
            if total is not None:
                add(text("panel_tariff_total"), total, _BOLD)

    # --- per month / per day ------------------------------------------------
    if monthly:
        heading("panel_report_per_day")
        totals_table(
            text("panel_report_col_day"),
            [(row.key or "", row) for row in result.periods],
            summary,
        )
    else:
        heading("panel_report_per_month")
        totals_table(
            text("panel_report_col_month"),
            [(fmt.months[int(str(row.key)[5:]) - 1], row) for row in result.periods],
            summary,
        )

    # --- per tariff ---------------------------------------------------------
    heading("panel_report_per_tariff")
    totals_table(
        text("panel_tariff_col_tariff"),
        [(row.name or text("panel_tariff_without"), row) for row in result.tariffs],
        summary,
    )

    # --- AC / DC ------------------------------------------------------------
    if result.charge_types:
        heading("panel_report_charge_types")
        totals_table(
            text("panel_field_charge_type"),
            [((row.key or "").upper(), row) for row in result.charge_types],
            None,
            with_fee=False,
        )

    # --- per vehicle --------------------------------------------------------
    if result.vehicles:
        heading("panel_report_per_vehicle")
        totals_table(
            text("panel_report_col_vehicle"),
            [(row.name or "", row) for row in result.vehicles],
            summary,
        )

    # --- sessions -----------------------------------------------------------
    if result.sessions:
        heading("panel_sessions")
        all_vehicles = result.vehicles is not None
        head = [text("panel_field_date"), text("panel_report_col_time")]
        widths = [20, 14]
        if all_vehicles:
            head.append(text("panel_report_col_vehicle"))
            widths.append(24)
        head += [
            text("panel_tariff_col_tariff"),
            text("panel_field_charge_type"),
            text("panel_tariff_col_energy"),
            cost_head,
            text("panel_soc"),
            text("panel_field_note"),
        ]
        widths += [28, 16, 13, 18, 16, 31 if all_vehicles else 55]
        align = ["LEFT"] * len(head)
        for column in (-5, -4, -3, -2):
            align[column] = "RIGHT"
        align[-5] = "CENTER"
        pdf.set_font(FONT, "", 8)
        with pdf.table(
            col_widths=widths,
            text_align=align,
            headings_style=_HEADING,
            borders_layout="HORIZONTAL_LINES",
        ) as table:
            table.row(head)
            for session in result.sessions:
                local = session.timestamp.astimezone(context.tz)
                cells = [local.date().isoformat(), local.strftime("%H:%M")]
                if all_vehicles:
                    cells.append(context.vehicle_names.get(session.vehicle_id, ""))
                cells += [
                    context.tariff_names.get(session.tariff_id or "", "")
                    if session.tariff_id
                    else "",
                    session.charge_type.upper(),
                    _energy(session.energy_kwh, fmt),
                    money(session.cost),
                    _soc(session),
                    session.note or "",
                ]
                table.row(cells)

    return bytes(pdf.output())
