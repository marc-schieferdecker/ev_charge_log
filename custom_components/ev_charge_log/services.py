"""Actions: add, update, delete and list sessions."""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime
from typing import Any

import voluptuous as vol  # resolves to probatio on HA >= 2026.9
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .const import CHARGE_TYPE_AC, CHARGE_TYPES, DOMAIN
from .coordinator import COORDINATOR_KEY, EvChargeLogCoordinator
from .models import Session, SessionInput, ValidationError
from .storage import SessionNotFoundError

SERVICE_ADD = "add_session"
SERVICE_UPDATE = "update_session"
SERVICE_DELETE = "delete_session"
SERVICE_LIST = "list_sessions"

_NUMBER = vol.Coerce(float)
_SESSION_FIELDS = {
    vol.Optional("tariff"): cv.string,
    vol.Optional("cost"): _NUMBER,
    vol.Optional("timestamp"): cv.datetime,
    vol.Optional("soc_start"): _NUMBER,
    vol.Optional("soc_end"): _NUMBER,
    vol.Optional("location"): cv.string,
    vol.Optional("note"): cv.string,
}

ADD_SCHEMA = vol.Schema(
    {
        vol.Required("vehicle"): cv.string,
        vol.Required("energy_kwh"): _NUMBER,
        vol.Optional("charge_type", default=CHARGE_TYPE_AC): vol.In(CHARGE_TYPES),
        **_SESSION_FIELDS,
    }
)
UPDATE_SCHEMA = vol.Schema(
    {
        vol.Required("vehicle"): cv.string,
        vol.Optional("session_id"): cv.string,
        vol.Optional("energy_kwh"): _NUMBER,
        vol.Optional("charge_type"): vol.In(CHARGE_TYPES),
        vol.Optional("clear_cost"): cv.boolean,
        **_SESSION_FIELDS,
    }
)
DELETE_SCHEMA = vol.Schema(
    {
        vol.Required("vehicle"): cv.string,
        vol.Optional("session_id"): cv.string,
    }
)
LIST_SCHEMA = vol.Schema(
    {
        vol.Optional("vehicle"): cv.string,
        vol.Optional("start"): cv.datetime,
        vol.Optional("end"): cv.datetime,
    }
)


def _error(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


def _coordinator(hass: HomeAssistant) -> EvChargeLogCoordinator:
    coord = hass.data.get(COORDINATOR_KEY)
    if coord is None:
        raise _error("not_loaded")
    return coord


def _subentry_of_device(
    hass: HomeAssistant, device_id: str, known: dict[str, Any], kind: str
) -> str:
    """Map a device ID (from the device selector) to the vehicle/tariff ID."""
    device = dr.async_get(hass).async_get(device_id)
    if device is not None:
        for domain, identifier in device.identifiers:
            if domain == DOMAIN and identifier in known:
                return identifier
    raise _error(f"unknown_{kind}", device_id=device_id)


def _aware(value: datetime | None) -> datetime | None:
    """Interpret naive datetimes (from the UI) as local time."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=dt_util.get_default_time_zone())


def _to_response(coord: EvChargeLogCoordinator, session: Session) -> dict[str, Any]:
    vehicle = coord.vehicles.get(session.vehicle_id)
    tariff = coord.tariffs.get(session.tariff_id) if session.tariff_id else None
    return {
        **session.to_dict(),
        "vehicle_name": vehicle.name if vehicle else None,
        "tariff_name": tariff.name if tariff else None,
    }


async def _run(coro: Awaitable[Session]) -> Session:
    """Translate model errors into user-facing validation errors."""
    try:
        return await coro
    except SessionNotFoundError as err:
        raise _error("session_not_found") from err
    except ValidationError as err:
        raise _error("invalid_session", error=str(err)) from err


def async_register_services(hass: HomeAssistant) -> None:
    """Register the actions (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_ADD):
        return

    async def add_session(call: ServiceCall) -> ServiceResponse:
        coord = _coordinator(hass)
        data = call.data
        vehicle_id = _subentry_of_device(
            hass, data["vehicle"], coord.vehicles, "vehicle"
        )
        tariff_id = (
            _subentry_of_device(hass, data["tariff"], coord.tariffs, "tariff")
            if "tariff" in data
            else None
        )
        session_input = SessionInput(
            vehicle_id=vehicle_id,
            energy_kwh=data["energy_kwh"],
            charge_type=data["charge_type"],
            timestamp=_aware(data.get("timestamp")) or dt_util.now(),
            tariff_id=tariff_id,
            cost=data.get("cost"),
            soc_start=data.get("soc_start"),
            soc_end=data.get("soc_end"),
            location=data.get("location"),
            note=data.get("note"),
        )
        session = await _run(
            coord.async_add_session(session_input, timestamp_given="timestamp" in data)
        )
        return {"session": _to_response(coord, session)}

    async def update_session(call: ServiceCall) -> ServiceResponse:
        coord = _coordinator(hass)
        data = call.data
        vehicle_id = _subentry_of_device(
            hass, data["vehicle"], coord.vehicles, "vehicle"
        )
        changes: dict[str, Any] = {
            name: data[name]
            for name in (
                "energy_kwh",
                "charge_type",
                "soc_start",
                "soc_end",
                "location",
                "note",
            )
            if name in data
        }
        if "timestamp" in data:
            changes["timestamp"] = _aware(data["timestamp"])
        if "tariff" in data:
            changes["tariff_id"] = _subentry_of_device(
                hass, data["tariff"], coord.tariffs, "tariff"
            )
        if data.get("clear_cost"):
            if "cost" in data:
                raise _error("cost_conflict")
            changes["cost"] = None
        elif "cost" in data:
            changes["cost"] = data["cost"]
        session = await _run(
            coord.async_update_session(vehicle_id, data.get("session_id"), **changes)
        )
        return {"session": _to_response(coord, session)}

    async def delete_session(call: ServiceCall) -> ServiceResponse:
        coord = _coordinator(hass)
        vehicle_id = _subentry_of_device(
            hass, call.data["vehicle"], coord.vehicles, "vehicle"
        )
        session = await _run(
            coord.async_delete_session(vehicle_id, call.data.get("session_id"))
        )
        return {"session": _to_response(coord, session)}

    async def list_sessions(call: ServiceCall) -> ServiceResponse:
        coord = _coordinator(hass)
        data = call.data
        vehicle_id = (
            _subentry_of_device(hass, data["vehicle"], coord.vehicles, "vehicle")
            if "vehicle" in data
            else None
        )
        sessions = coord.log.sessions(
            vehicle_id=vehicle_id,
            start=_aware(data.get("start")),
            end=_aware(data.get("end")),
        )
        return {"sessions": [_to_response(coord, s) for s in sessions]}

    hass.services.async_register(
        DOMAIN, SERVICE_ADD, add_session, ADD_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_UPDATE, update_session, UPDATE_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE, delete_session, DELETE_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_LIST, list_sessions, LIST_SCHEMA, SupportsResponse.ONLY
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove the actions."""
    for service in (SERVICE_ADD, SERVICE_UPDATE, SERVICE_DELETE, SERVICE_LIST):
        hass.services.async_remove(DOMAIN, service)
