"""Sidebar panel "EV Charge Log" with one card per vehicle (stage 4).

The panel is a small JavaScript module in `frontend/`. It asks the backend
via the websocket command `ev_charge_log/panel` which vehicles exist and
which entities belong to them, and builds Home Assistant cards from that.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

import voluptuous as vol  # resolves to probatio on HA >= 2026.9
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN, PANEL_URL_PATH
from .coordinator import COORDINATOR_KEY, EvChargeLogCoordinator
from .models import (
    Session,
    TariffMonthRow,
    month_of,
    month_totals,
    tariff_month_stats,
)

_LOGGER = logging.getLogger(__name__)

PANEL_COMPONENT = "ev-charge-log-panel"
PANEL_ICON = "mdi:ev-station"
STATIC_URL = "/ev_charge_log_static"
WS_TYPE = f"{DOMAIN}/panel"
WS_HISTORY_TYPE = f"{DOMAIN}/panel_history"
WS_TARIFF_STATS_TYPE = f"{DOMAIN}/panel_tariff_stats"
_MONTH = vol.Match(r"^\d{4}-(0[1-9]|1[0-2])$")

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


def _local_day(session: Session) -> date:
    return dt_util.as_local(session.timestamp).date()


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
                    "stats": entities(vehicle.id, STAT_ENTITIES),
                    "last": entities(vehicle.id, LAST_ENTITIES),
                    "history": vehicle_history(coord, vehicle.id, current_month),
                    "sessions": sessions(vehicle.id),
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
        for tariff_id in coord.sorted_tariffs():
            tariffs.append(
                {
                    "id": tariff_id,
                    "name": coord.tariffs[tariff_id].name,
                    "device_id": device_of("sensor", f"{tariff_id}_total_cost"),
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
