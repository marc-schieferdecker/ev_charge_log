"""Sidebar panel "EV Charge Log" with one card per vehicle (stage 4).

The panel is a small JavaScript module in `frontend/`. It asks the backend
via the websocket command `ev_charge_log/panel` which vehicles exist and
which entities belong to them, and builds Home Assistant cards from that.
"""

from __future__ import annotations

import base64
import logging
from datetime import date
from pathlib import Path
from typing import Any

import voluptuous as vol  # resolves to probatio on HA >= 2026.9
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify
from homeassistant.util.hass_dict import HassKey

from .compare import (
    WhatIfResult,
    WhatIfTariff,
    compare_tariffs,
)
from .const import DOMAIN, NOTE_MAX_LENGTH, PANEL_URL_PATH
from .coordinator import COORDINATOR_KEY, EvChargeLogCoordinator
from .csv_io import (
    EXPORT_COLUMNS,
    EXPORT_VALUES,
    export_csv,
    parse_import_csv,
)
from .models import (
    Session,
    ValidationError,
    month_of,
)
from .report_pdf import (
    MONTHS_EN,
    REPORT_TEXT_KEYS,
    PdfFormat,
    ReportContext,
    report_filename,
    report_pdf,
)
from .reports import (
    Report,
    ReportRow,
    TariffMonthRow,
    month_totals,
    report,
    tariff_month_stats,
)

_LOGGER = logging.getLogger(__name__)

PANEL_COMPONENT = "ev-charge-log-panel"
PANEL_ICON = "mdi:ev-station"
STATIC_URL = "/ev_charge_log_static"
WS_TYPE = f"{DOMAIN}/panel"
WS_HISTORY_TYPE = f"{DOMAIN}/panel_history"
WS_TARIFF_STATS_TYPE = f"{DOMAIN}/panel_tariff_stats"
WS_EXPORT_TYPE = f"{DOMAIN}/panel_export"
WS_IMPORT_TYPE = f"{DOMAIN}/panel_import"
# Header texts of these languages are always understood by the import.
IMPORT_LANGUAGES = ("en", "de")
WS_COMPARE_TYPE = f"{DOMAIN}/panel_compare"
_PRICE = vol.Any(None, vol.Coerce(float))
_WHAT_IF = vol.Schema(
    {
        vol.Optional("price_ac"): _PRICE,
        vol.Optional("price_dc"): _PRICE,
        vol.Optional("monthly_fee"): _PRICE,
    }
)
_MONTH = vol.Match(r"^\d{4}-(0[1-9]|1[0-2])$")

WS_REPORT_TYPE = f"{DOMAIN}/panel_report"
WS_REPORT_PDF_TYPE = f"{DOMAIN}/panel_report_pdf"
# Parameters of both report commands (stage 9): a year or a month and filters.
_REPORT_PARAMS = {
    vol.Required("period"): vol.Match(r"^\d{4}(-(0[1-9]|1[0-2]))?$"),
    vol.Optional("vehicle_id"): vol.Any(None, str),
    vol.Optional("tariff_id"): vol.Any(None, str),
    vol.Optional("note"): vol.Any(None, vol.All(str, vol.Length(max=NOTE_MAX_LENGTH))),
    vol.Optional("sessions", default=False): bool,
}
# Number format, month names and currency symbol of the page, for the PDF.
_PDF_FORMAT = vol.Schema(
    {
        vol.Optional("decimal", default="."): vol.All(str, vol.Length(min=1, max=1)),
        vol.Optional("group", default=""): vol.All(str, vol.Length(max=1)),
        vol.Optional("months"): vol.All([str], vol.Length(min=12, max=12)),
        vol.Optional("currency"): vol.Any(None, vol.All(str, vol.Length(max=8))),
    }
)

SIDEBAR_TITLES = {"de": "Ladungen", "en": "Charging log"}

WS_SET_SOC_TYPE = f"{DOMAIN}/panel_set_soc"
# Pseudo platform: the panel shows input_soc_start and input_soc_end in one row.
SOC_ROW = "soc"

# (platform, key) of the entities shown per vehicle, in this order.
INPUT_ENTITIES = (
    ("date", "input_date"),
    ("number", "input_energy"),
    ("number", "input_cost"),
    # One own row with both SoC fields side by side (input_soc_start/_end).
    (SOC_ROW, "input_soc"),
    ("select", "input_tariff"),
    ("select", "input_charge_type"),
    ("text", "input_note"),
    # "book" is sent separately and shown as an own button below the inputs;
    # "reset_input" is left out on purpose (user request), the entity stays
    # available for own dashboards.
)
BOOK_ENTITY = ("button", "book")
STAT_ENTITIES = (
    ("sensor", "total_cost"),
    ("sensor", "total_energy"),
    ("sensor", "fee_total"),
    ("sensor", "average_price"),
    # Instead of the number of sessions (user request); that sensor stays.
    ("sensor", "cost_per_100"),
)
# Shown in an own section "Last session" of the statistics card, with the
# short `panel_label_*` names.
LAST_ENTITIES = (
    ("sensor", "last_session_time"),
    ("sensor", "last_session_cost"),
    ("sensor", "last_session_energy"),
    ("sensor", "last_session_tariff"),
)
TEXT_KEYS = (
    "panel_empty",
    "panel_add",
    "panel_input",
    "panel_booked",
    "panel_reload",
    "panel_stats",
    "panel_last",
    "panel_chart_energy",
    "panel_chart_cost",
    "panel_sessions",
    "panel_no_sessions",
    "panel_soc",
    "panel_soc_start",
    "panel_soc_end",
    "panel_older",
    "panel_older_month",
    "panel_newer_month",
    "panel_newer",
    "panel_edit_title",
    "panel_field_date",
    "panel_field_energy",
    "panel_field_charge_type",
    "panel_field_tariff",
    "panel_field_cost",
    "panel_field_cost_hint",
    "panel_field_note",
    "panel_no_tariff",
    "panel_save",
    "panel_delete",
    "panel_delete_confirm",
    "panel_cancel",
    "panel_tariff_stats",
    "panel_tariff_col_tariff",
    "panel_tariff_col_sessions",
    "panel_tariff_col_energy",
    "panel_tariff_col_cost",
    "panel_tariff_col_fee",
    "panel_tariff_col_saving",
    "panel_tariff_without",
    "panel_tariff_total",
    "panel_tariff_current_month",
    "panel_tariff_empty",
    "panel_export",
    "panel_export_title",
    "panel_export_from",
    "panel_export_to",
    "panel_export_download",
    "panel_import",
    "panel_import_title",
    "panel_import_file",
    "panel_import_start",
    "panel_import_result",
    "panel_import_row",
    "panel_close",
    *(
        f"panel_compare{suffix}"
        for suffix in (
            "",
            "_title",
            "_from",
            "_to",
            "_vehicle",
            "_vehicle_all",
            "_tariff",
            "_tariff_all",
            "_prefill",
            "_without_subscription",
            "_tariff_a",
            "_tariff_b",
            "_name",
            "_price_ac",
            "_price_dc",
            "_fee",
            "_start",
            "_summary",
            "_row_cost",
            "_row_fees",
            "_row_total",
            "_row_average",
            "_cheaper",
            "_dearer",
            "_equal",
            "_missing",
            "_actual",
        )
    ),
    *REPORT_TEXT_KEYS,
    "panel_report",
    "panel_report_period",
    "panel_report_year",
    "panel_report_month",
    "panel_report_note",
    "panel_report_list",
    "panel_report_show",
    "panel_report_pdf",
)
SESSION_LIST_LIMIT = 100
# Any booking, correction or deletion changes at least one of these.
WATCH_ENTITIES = (
    ("sensor", "session_count"),
    ("sensor", "total_energy"),
    ("sensor", "total_cost"),
    ("sensor", "last_session_time"),
)

_SETUP_DONE: HassKey[bool] = HassKey(f"{DOMAIN}_panel_setup")


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register the static files, the websocket command and the panel."""
    if not hass.data.get(_SETUP_DONE):
        # Static paths and websocket commands cannot be unregistered;
        # register them once per Home Assistant run.
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    STATIC_URL, str(Path(__file__).parent / "frontend"), False
                )
            ]
        )
        websocket_api.async_register_command(hass, websocket_panel)
        websocket_api.async_register_command(hass, websocket_history)
        websocket_api.async_register_command(hass, websocket_tariff_stats)
        websocket_api.async_register_command(hass, websocket_set_soc)
        websocket_api.async_register_command(hass, websocket_export)
        websocket_api.async_register_command(hass, websocket_import)
        websocket_api.async_register_command(hass, websocket_compare)
        websocket_api.async_register_command(hass, websocket_report)
        websocket_api.async_register_command(hass, websocket_report_pdf)
        hass.data[_SETUP_DONE] = True

    # The frontend is a soft dependency (after_dependencies): always loaded in
    # a normal installation, but not in headless setups or tests.
    if "frontend" not in hass.config.components:
        _LOGGER.debug("Frontend not loaded, no sidebar panel")
        return
    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        return
    integration = await async_get_integration(hass, DOMAIN)
    language = hass.config.language.split("-")[0]
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title=SIDEBAR_TITLES.get(language, SIDEBAR_TITLES["en"]),
        sidebar_icon=PANEL_ICON,
        # The version busts the browser cache after an update.
        module_url=f"{STATIC_URL}/{PANEL_COMPONENT}.js?v={integration.version}",
        require_admin=False,
    )


@callback
def async_remove_panel(hass: HomeAssistant) -> None:
    """Remove the sidebar entry (last config entry unloaded)."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)


@websocket_api.websocket_command({vol.Required("type"): WS_TYPE})
@websocket_api.async_response
async def websocket_panel(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Vehicles with their entity IDs, and the texts of the panel."""
    connection.send_result(msg["id"], await async_panel_data(hass))


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_HISTORY_TYPE,
        vol.Required("vehicle_id"): str,
        vol.Required("month"): _MONTH,
    }
)
@callback
def websocket_history(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Daily totals of one vehicle in one calendar month (chart paging)."""
    coord = hass.data.get(COORDINATOR_KEY)
    if coord is None or msg["vehicle_id"] not in coord.vehicles:
        connection.send_error(msg["id"], "not_found", "Unknown vehicle")
        return
    connection.send_result(
        msg["id"], vehicle_history(coord, msg["vehicle_id"], msg["month"])
    )


@websocket_api.websocket_command(
    {vol.Required("type"): WS_TARIFF_STATS_TYPE, vol.Required("month"): _MONTH}
)
@callback
def websocket_tariff_stats(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Tariff statistics of all vehicles in one calendar month (paging)."""
    coord = hass.data.get(COORDINATOR_KEY)
    if coord is None:
        connection.send_error(msg["id"], "not_found", "Not loaded")
        return
    connection.send_result(msg["id"], tariff_stats(coord, msg["month"]))


_SOC_VALUE = vol.Any(None, vol.All(vol.Coerce(float), vol.Range(min=0, max=100)))


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_SET_SOC_TYPE,
        vol.Required("vehicle_id"): str,
        vol.Optional("soc_start"): _SOC_VALUE,
        vol.Optional("soc_end"): _SOC_VALUE,
    }
)
@callback
def websocket_set_soc(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Set or clear the SoC input fields of a vehicle.

    A number entity cannot be emptied with number.set_value; the panel's SoC
    row uses this command so a cleared field really means "no SoC".
    """
    coord = hass.data.get(COORDINATOR_KEY)
    if coord is None or msg["vehicle_id"] not in coord.inputs:
        connection.send_error(msg["id"], "not_found", "Unknown vehicle")
        return
    values = {name: msg[name] for name in ("soc_start", "soc_end") if name in msg}
    coord.async_set_input(msg["vehicle_id"], **values)
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_EXPORT_TYPE,
        vol.Required("vehicle_id"): str,
        vol.Optional("start"): vol.Any(None, cv.date),
        vol.Optional("end"): vol.Any(None, cv.date),
        vol.Optional("decimal", default="."): vol.In([",", "."]),
    }
)
@websocket_api.async_response
async def websocket_export(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Sessions and monthly fees of one vehicle as CSV (stage 6).

    The panel turns the text into a download. Errors carry the translation
    key as code, the panel shows the translated message.
    """
    coord = hass.data.get(COORDINATOR_KEY)
    if coord is None or msg["vehicle_id"] not in coord.vehicles:
        connection.send_error(msg["id"], "not_found", "Unknown vehicle")
        return
    try:
        result = await async_export(
            hass,
            coord,
            msg["vehicle_id"],
            msg.get("start"),
            msg.get("end"),
            msg["decimal"],
        )
    except ValidationError as err:
        connection.send_error(msg["id"], err.key, str(err))
        return
    connection.send_result(msg["id"], result)


async def _export_texts(hass: HomeAssistant, language: str) -> dict[str, str]:
    """Header and value texts of the CSV file in a language."""
    translations = await async_get_translations(hass, language, "exceptions", [DOMAIN])
    prefix = f"component.{DOMAIN}.exceptions.panel_export"
    currency = hass.config.currency
    texts = {
        key: translations.get(f"{prefix}_col_{key}.message", key).replace(
            "{currency}", currency
        )
        for key in EXPORT_COLUMNS
    }
    texts |= {
        key: translations.get(f"{prefix}_{key}.message", key) for key in EXPORT_VALUES
    }
    return texts


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_IMPORT_TYPE,
        vol.Required("vehicle_id"): str,
        vol.Required("csv"): str,
    }
)
@websocket_api.async_response
async def websocket_import(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Import sessions of one vehicle from a CSV file of the export (stage 7).

    A broken file is an error with the translation key as code; broken rows
    are listed in the result with row number, key and placeholders.
    """
    coord = hass.data.get(COORDINATOR_KEY)
    if coord is None or msg["vehicle_id"] not in coord.vehicles:
        connection.send_error(msg["id"], "not_found", "Unknown vehicle")
        return
    try:
        result = await async_import(hass, coord, msg["vehicle_id"], msg["csv"])
    except ValidationError as err:
        connection.send_error(msg["id"], err.key, str(err))
        return
    connection.send_result(msg["id"], result)


async def async_import(
    hass: HomeAssistant, coord: EvChargeLogCoordinator, vehicle_id: str, text: str
) -> dict[str, Any]:
    """Read and book an import file; headers in English, German or HA language."""
    languages = dict.fromkeys((hass.config.language, *IMPORT_LANGUAGES))
    texts = [await _export_texts(hass, language) for language in languages]
    parsed = parse_import_csv(text, texts, dt_util.get_default_time_zone())
    result = await coord.async_import_sessions(vehicle_id, parsed.rows)
    problems = sorted([*parsed.problems, *result.problems], key=lambda p: p.row)
    return {
        "imported": len(result.sessions),
        "duplicates": result.duplicates,
        "fees_skipped": parsed.fees_skipped,
        "errors": [
            {"row": p.row, "key": p.key, "placeholders": p.placeholders}
            for p in problems
        ],
    }


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_COMPARE_TYPE,
        vol.Required("start"): cv.date,
        vol.Required("end"): cv.date,
        vol.Optional("vehicle_id"): vol.Any(None, str),
        vol.Optional("tariff_id"): vol.Any(None, str),
        vol.Required("a"): _WHAT_IF,
        vol.Required("b"): _WHAT_IF,
    }
)
@callback
def websocket_compare(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Tariff comparison "what if" (stage 8): nothing is stored."""
    coord = hass.data.get(COORDINATOR_KEY)
    vehicle_id = msg.get("vehicle_id")
    tariff_id = msg.get("tariff_id")
    if (
        coord is None
        or (vehicle_id is not None and vehicle_id not in coord.vehicles)
        or (tariff_id is not None and tariff_id not in coord.tariffs)
    ):
        connection.send_error(msg["id"], "not_found", "Unknown vehicle or tariff")
        return
    try:
        comparison = compare_tariffs(
            coord.log.sessions(),
            coord.log.fees(),
            msg["start"],
            msg["end"],
            _local_day,
            WhatIfTariff(**msg["a"]),
            WhatIfTariff(**msg["b"]),
            vehicle_id=vehicle_id,
            tariff_id=tariff_id,
        )
    except ValidationError as err:
        connection.send_error(msg["id"], err.key, str(err))
        return

    def side(result: WhatIfResult) -> dict[str, Any]:
        return {
            "cost": result.cost,
            "fees": result.fees,
            "total": result.total,
            "average": result.average,
            "missing_count": result.missing_count,
        }

    connection.send_result(
        msg["id"],
        {
            "months": comparison.months,
            "session_count": comparison.session_count,
            "energy_kwh": comparison.energy_kwh,
            "a": side(comparison.a),
            "b": side(comparison.b),
            "difference": comparison.difference,
            "actual_cost": comparison.actual_cost,
            "actual_fees": comparison.actual_fees,
            "actual_total": comparison.actual_total,
        },
    )


def build_report(coord: EvChargeLogCoordinator, msg: dict[str, Any]) -> Report:
    """The report (stage 9) for the parameters of a report command."""
    return report(
        coord.log.sessions(),
        coord.log.fees(),
        coord.tariffs,
        {vehicle_id: vehicle.name for vehicle_id, vehicle in coord.vehicles.items()},
        msg["period"],
        _local_day,
        dt_util.now().date(),
        vehicle_id=msg.get("vehicle_id"),
        tariff_id=msg.get("tariff_id"),
        note=msg.get("note"),
        include_sessions=msg["sessions"],
    )


def _report_row_data(row: ReportRow) -> dict[str, Any]:
    return {
        "key": row.key,
        "name": row.name,
        "sessions": row.session_count,
        "energy": row.energy_kwh,
        "cost": row.cost,
        "fee": row.fee,
        "total": row.total,
        "average": row.average_price,
    }


def report_data(coord: EvChargeLogCoordinator, result: Report) -> dict[str, Any]:
    """The report for the panel; sessions with local date and time and names."""
    sessions = None
    if result.sessions is not None:
        sessions = []
        for session in result.sessions:
            local = dt_util.as_local(session.timestamp)
            vehicle = coord.vehicles.get(session.vehicle_id)
            tariff = coord.tariffs.get(session.tariff_id) if session.tariff_id else None
            sessions.append(
                {
                    "id": session.id,
                    "date": local.date().isoformat(),
                    "time": local.strftime("%H:%M"),
                    "vehicle": vehicle.name if vehicle else None,
                    "tariff": tariff.name if tariff else None,
                    "charge_type": session.charge_type,
                    "energy": session.energy_kwh,
                    "cost": session.cost,
                    "soc_start": session.soc_start,
                    "soc_end": session.soc_end,
                    "note": session.note,
                }
            )
    return {
        "period": result.period,
        "current": result.current,
        "summary": _report_row_data(result.summary),
        "free_energy": result.free_energy_kwh,
        "fees_excluded": result.fees_excluded,
        "periods": [_report_row_data(row) for row in result.periods],
        "tariffs": [_report_row_data(row) for row in result.tariffs],
        "charge_types": [_report_row_data(row) for row in result.charge_types],
        "vehicles": None
        if result.vehicles is None
        else [_report_row_data(row) for row in result.vehicles],
        "sessions": sessions,
    }


@websocket_api.websocket_command(
    {vol.Required("type"): WS_REPORT_TYPE, **_REPORT_PARAMS}
)
@callback
def websocket_report(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Annual or monthly report (stage 9) to show in the panel."""
    coord = hass.data.get(COORDINATOR_KEY)
    if coord is None:
        connection.send_error(msg["id"], "not_found", "Not loaded")
        return
    try:
        result = build_report(coord, msg)
    except ValidationError as err:
        connection.send_error(msg["id"], err.key, str(err))
        return
    connection.send_result(msg["id"], report_data(coord, result))


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_REPORT_PDF_TYPE,
        **_REPORT_PARAMS,
        vol.Optional("format", default={}): _PDF_FORMAT,
    }
)
@websocket_api.async_response
async def websocket_report_pdf(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """The report as a PDF (stage 9): file name and the file in Base64.

    Built in the executor: reading the fonts and making the PDF block.
    """
    coord = hass.data.get(COORDINATOR_KEY)
    if coord is None:
        connection.send_error(msg["id"], "not_found", "Not loaded")
        return
    try:
        result = build_report(coord, msg)
    except ValidationError as err:
        connection.send_error(msg["id"], err.key, str(err))
        return
    filename, pdf = await async_report_pdf(hass, coord, result, msg)
    connection.send_result(
        msg["id"],
        {"filename": filename, "pdf": base64.b64encode(pdf).decode("ascii")},
    )


async def async_report_pdf(
    hass: HomeAssistant,
    coord: EvChargeLogCoordinator,
    result: Report,
    msg: dict[str, Any],
) -> tuple[str, bytes]:
    """File name and content of the report PDF, texts in the HA language."""
    translations = await async_get_translations(
        hass, hass.config.language, "exceptions", [DOMAIN]
    )
    prefix = f"component.{DOMAIN}.exceptions"
    texts = {
        key: translations.get(f"{prefix}.{key}.message", key)
        for key in REPORT_TEXT_KEYS
    }
    fmt_data = msg["format"]
    fmt = PdfFormat(
        decimal=fmt_data["decimal"],
        group=fmt_data["group"],
        months=tuple(fmt_data.get("months") or MONTHS_EN),
        currency=fmt_data.get("currency") or None,
    )
    vehicle = coord.vehicles.get(msg.get("vehicle_id") or "")
    tariff = coord.tariffs.get(msg.get("tariff_id") or "")
    context = ReportContext(
        currency=hass.config.currency,
        created=dt_util.now().date(),
        tz=dt_util.get_default_time_zone(),
        vehicle_name=vehicle.name if vehicle else None,
        tariff_name=tariff.name if tariff else None,
        note=msg.get("note"),
        vehicle_names={v_id: v.name for v_id, v in coord.vehicles.items()},
        tariff_names={t_id: t.name for t_id, t in coord.tariffs.items()},
    )
    pdf = await hass.async_add_executor_job(report_pdf, result, texts, fmt, context)
    return report_filename(result.period, context.vehicle_name), pdf


async def async_export(
    hass: HomeAssistant,
    coord: EvChargeLogCoordinator,
    vehicle_id: str,
    start: date | None,
    end: date | None,
    decimal: str,
) -> dict[str, str]:
    """File name and CSV text of the export, header in the HA language."""
    texts = await _export_texts(hass, hass.config.language)
    vehicle = coord.vehicles[vehicle_id]
    text = export_csv(
        coord.log.sessions(vehicle_id=vehicle_id),
        coord.log.fees(vehicle_id=vehicle_id),
        {tariff_id: tariff.name for tariff_id, tariff in coord.tariffs.items()},
        start,
        end,
        dt_util.as_local,
        texts,
        decimal,
    )
    parts = ["ev-charge-log", slugify(vehicle.name, separator="-") or "vehicle"]
    parts += [day.isoformat() for day in (start, end) if day is not None]
    return {"filename": "_".join(parts) + ".csv", "csv": text}


def _local_day(session: Session) -> date:
    return dt_util.as_local(session.timestamp).date()


def first_day(coord: EvChargeLogCoordinator, vehicle_id: str) -> date:
    """Day of the vehicle's oldest session or fee (export preset), else today."""
    days = [dt_util.now().date()]
    sessions = coord.log.sessions(vehicle_id=vehicle_id)
    if sessions:
        days.append(_local_day(sessions[0]))
    fees = coord.log.fees(vehicle_id=vehicle_id)
    if fees:
        days.append(date.fromisoformat(f"{fees[0].month}-01"))
    return min(days)


def _row_data(row: TariffMonthRow) -> dict[str, Any]:
    return {
        "tariff_id": row.tariff_id,
        "name": row.name,
        "sessions": row.session_count,
        "energy": row.energy_kwh,
        "cost": row.cost,
        "fee": row.fee,
        "saving": row.saving,
    }


def tariff_stats(coord: EvChargeLogCoordinator, month: str) -> dict[str, Any]:
    """Sessions, energy, cost, fee and savings per tariff in a calendar month.

    Over all vehicles. `tariff_id` None is the row "without tariff"; the
    first month (oldest session or fee) and the current month tell the panel
    when to disable "<" and ">".
    """
    sessions = coord.log.sessions()
    fees = coord.log.fees()
    current = month_of(dt_util.now().date())
    firsts = [current]
    if sessions:
        firsts.append(month_of(_local_day(sessions[0])))
    if fees:
        firsts.append(fees[0].month)
    stats = tariff_month_stats(sessions, fees, coord.tariffs, month, _local_day)
    return {
        "month": month,
        "first_month": min(firsts),
        "current_month": current,
        "rows": [_row_data(row) for row in stats.rows],
        "total": _row_data(stats.total),
    }


def vehicle_history(
    coord: EvChargeLogCoordinator, vehicle_id: str, month: str
) -> dict[str, Any]:
    """Energy and session cost per local day of a calendar month.

    Also returns the month of the vehicle's first session and the current
    month, so the panel knows when to disable "<" and ">". Monthly fees are
    not included.
    """
    sessions = coord.log.sessions(vehicle_id=vehicle_id)
    current = month_of(dt_util.now().date())
    first = month_of(_local_day(sessions[0])) if sessions else current
    totals = month_totals(sessions, month, _local_day)
    return {
        "month": month,
        "first_month": min(first, current),
        "current_month": current,
        "days": [d.isoformat() for d in totals.days],
        "energy": totals.energy_kwh,
        "cost": totals.cost,
        "total_energy": totals.total_energy_kwh,
        "total_cost": totals.total_cost,
    }


async def async_panel_data(hass: HomeAssistant) -> dict[str, Any]:
    """Data for the panel (also used by the tests).

    Each row has the entity ID and a short name without the vehicle name
    (the card title already shows it): input rows use the `panel_label_*`
    texts, statistic rows the translated entity name.
    """
    registry = er.async_get(hass)
    coord = hass.data.get(COORDINATOR_KEY)
    language = hass.config.language
    panel_texts = await async_get_translations(hass, language, "exceptions", [DOMAIN])
    entity_texts = await async_get_translations(hass, language, "entity", [DOMAIN])

    # Placeholders in entity names, filled like the entities do it.
    placeholders = {"length": str(hass.config.units.length_unit)}

    def label(platform: str, key: str) -> str | None:
        short = panel_texts.get(
            f"component.{DOMAIN}.exceptions.panel_label_{key}.message"
        )
        name = short or entity_texts.get(
            f"component.{DOMAIN}.entity.{platform}.{key}.name"
        )
        if name is None:
            return None
        for placeholder, value in placeholders.items():
            name = name.replace(f"{{{placeholder}}}", value)
        return name

    def entities(
        vehicle_id: str, spec: tuple[tuple[str, str], ...]
    ) -> list[dict[str, Any]]:
        rows = []
        for platform, key in spec:
            if platform == SOC_ROW:
                start, end = (
                    registry.async_get_entity_id(
                        "number", DOMAIN, f"{vehicle_id}_{key}_{part}"
                    )
                    for part in ("start", "end")
                )
                if start and end:
                    rows.append(
                        {
                            "type": SOC_ROW,
                            "vehicle_id": vehicle_id,
                            "start": start,
                            "end": end,
                            "name": label(platform, key),
                        }
                    )
                continue
            entity_id = registry.async_get_entity_id(
                platform, DOMAIN, f"{vehicle_id}_{key}"
            )
            if entity_id:
                rows.append({"entity": entity_id, "name": label(platform, key)})
        return rows

    current_month = month_of(dt_util.now().date())

    def device_of(platform: str, unique_id: str) -> str | None:
        """Device ID via one of the subentry's entities (for the actions)."""
        entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
        entry = registry.async_get(entity_id) if entity_id else None
        return entry.device_id if entry else None

    def sessions(vehicle_id: str) -> list[dict[str, Any]]:
        """Newest sessions first, for the list with edit dialog."""
        newest = list(reversed(coord.log.sessions(vehicle_id=vehicle_id)))
        rows = []
        for session in newest[:SESSION_LIST_LIMIT]:
            tariff = coord.tariffs.get(session.tariff_id) if session.tariff_id else None
            rows.append(
                {
                    "id": session.id,
                    # Local time; the dialog keeps the time when the date changes.
                    "timestamp": dt_util.as_local(session.timestamp).isoformat(),
                    "energy_kwh": session.energy_kwh,
                    "charge_type": session.charge_type,
                    "tariff_id": session.tariff_id,
                    "tariff_name": tariff.name if tariff else None,
                    "cost": session.cost,
                    "cost_overridden": session.cost_overridden,
                    "note": session.note,
                    "soc_start": session.soc_start,
                    "soc_end": session.soc_end,
                }
            )
        return rows

    def stats(vehicle_id: str) -> list[dict[str, Any]]:
        """Statistic rows.

        With an odometer sensor: the consumption per 100 km in brackets behind
        the total energy (`extra`), and the odometer below it.
        """
        rows = entities(vehicle_id, STAT_ENTITIES)
        odometer = coord.vehicles[vehicle_id].odometer_entity_id
        if odometer:
            energy = registry.async_get_entity_id(
                "sensor", DOMAIN, f"{vehicle_id}_total_energy"
            )
            consumption = registry.async_get_entity_id(
                "sensor", DOMAIN, f"{vehicle_id}_energy_per_100"
            )
            position = len(rows)
            for i, row in enumerate(rows):
                if row["entity"] == energy:
                    position = i + 1
                    if consumption:
                        row["extra"] = consumption
            rows.insert(
                position, {"entity": odometer, "name": label("sensor", "odometer")}
            )
        return rows

    vehicles = []
    tariffs = []
    if coord is not None:
        for vehicle in sorted(coord.vehicles.values(), key=lambda v: v.name.casefold()):
            detected = coord.detection.get(vehicle.id)
            pending = detected.pending if detected else None
            vehicles.append(
                {
                    "id": vehicle.id,
                    "name": vehicle.name,
                    "device_id": device_of("button", f"{vehicle.id}_book"),
                    "input": entities(vehicle.id, INPUT_ENTITIES),
                    "book": next(iter(entities(vehicle.id, (BOOK_ENTITY,))), None),
                    "stats": stats(vehicle.id),
                    "last": entities(vehicle.id, LAST_ENTITIES),
                    "history": vehicle_history(coord, vehicle.id, current_month),
                    "sessions": sessions(vehicle.id),
                    # Preset "from" of the export dialog.
                    "first_day": first_day(coord, vehicle.id).isoformat(),
                    # A detected charge not booked yet: the form preselects
                    # this vehicle (the latest one if several).
                    "pending_ended_at": pending.ended_at.isoformat()
                    if pending
                    else None,
                    # The panel redraws the chart when one of these changes.
                    "watch": [
                        row["entity"] for row in entities(vehicle.id, WATCH_ENTITIES)
                    ],
                }
            )
        today = dt_util.now().date()
        for tariff_id in coord.sorted_tariffs():
            tariff = coord.tariffs[tariff_id]
            price_ac, price_dc = tariff.prices_on(today)
            tariffs.append(
                {
                    "id": tariff_id,
                    "name": tariff.name,
                    "device_id": device_of("sensor", f"{tariff_id}_total_cost"),
                    # Today's values, to prefill the tariff comparison.
                    "price_ac": price_ac,
                    "price_dc": price_dc,
                    "monthly_fee": tariff.fee_on(today),
                    "reference_price_ac": tariff.reference_price_on("ac", today),
                    "reference_price_dc": tariff.reference_price_on("dc", today),
                }
            )

    prefix = f"component.{DOMAIN}.exceptions"
    texts = {key: panel_texts.get(f"{prefix}.{key}.message", key) for key in TEXT_KEYS}
    # Error messages by translation key: HA sends errors of actions in
    # English plus the key; the panel shows them in the HA language.
    errors = {
        key.removeprefix(f"{prefix}.").removesuffix(".message"): message
        for key, message in panel_texts.items()
        if key.startswith(prefix) and key.endswith(".message")
    }
    return {
        "vehicles": vehicles,
        "tariffs": tariffs,
        "tariff_stats": tariff_stats(coord, current_month) if coord else None,
        "config_url": f"/config/integrations/integration/{DOMAIN}",
        "texts": texts,
        "errors": errors,
        "currency": hass.config.currency,
        # The panel compares this with its own module version and asks for a
        # reload if the browser still runs an older panel module.
        "version": (await async_get_integration(hass, DOMAIN)).version,
    }
