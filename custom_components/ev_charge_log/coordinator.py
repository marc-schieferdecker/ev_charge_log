"""Runtime state for EV Charge Log: sessions, vehicles, tariffs, listeners.

There is one coordinator per Home Assistant instance, shared by the two
config entries ("vehicles" and "tariffs"). Each entry registers its
subentries here on setup and removes them on unload.

All calculation lives in models.py / storage.py. This module only wires
them to Home Assistant (storage, config subentries, entity updates).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util.hass_dict import HassKey

from .const import (
    CHARGE_TYPE_AC,
    CHARGE_TYPES,
    DOMAIN,
    SUBENTRY_TYPE_TARIFF,
    SUBENTRY_TYPE_VEHICLE,
)
from .models import Session, SessionInput, Tariff, ValidationError, Vehicle
from .storage import STORAGE_MINOR_VERSION, STORAGE_VERSION, SessionLog, migrate

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = f"{DOMAIN}.sessions"

type EvChargeLogConfigEntry = ConfigEntry[EvChargeLogCoordinator]

COORDINATOR_KEY: HassKey[EvChargeLogCoordinator] = HassKey(DOMAIN)
_LOCK_KEY: HassKey[asyncio.Lock] = HassKey(f"{DOMAIN}_lock")


async def async_get_coordinator(hass: HomeAssistant) -> EvChargeLogCoordinator:
    """Return the shared coordinator, loading the session store once."""
    if (coord := hass.data.get(COORDINATOR_KEY)) is not None:
        return coord
    async with hass.data.setdefault(_LOCK_KEY, asyncio.Lock()):
        if (coord := hass.data.get(COORDINATOR_KEY)) is None:
            coord = EvChargeLogCoordinator(hass)
            await coord.async_load()
            hass.data[COORDINATOR_KEY] = coord
    return coord


@callback
def async_release_coordinator(hass: HomeAssistant) -> None:
    """Drop the shared coordinator once no entry uses it any more."""
    hass.data.pop(COORDINATOR_KEY, None)


class _SessionStore(Store[dict[str, Any]]):
    """Store that delegates migration to the pure `storage.migrate`."""

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        return migrate(old_major_version, old_minor_version, old_data)


@dataclass(slots=True)
class VehicleInput:
    """Dashboard input fields of one vehicle (not persisted)."""

    energy_kwh: float | None = None
    cost: float | None = None
    tariff_id: str | None = None
    charge_type: str = CHARGE_TYPE_AC


class EvChargeLogCoordinator:
    """Holds the session log and notifies entities after every change."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize empty; entries register vehicles and tariffs."""
        self.hass = hass
        self._store = _SessionStore(
            hass, STORAGE_VERSION, STORAGE_KEY, minor_version=STORAGE_MINOR_VERSION
        )
        self.log = SessionLog()
        self.vehicles: dict[str, Vehicle] = {}
        self.tariffs: dict[str, Tariff] = {}
        self.inputs: dict[str, VehicleInput] = {}
        self._listeners: list[CALLBACK_TYPE] = []

    async def async_load(self) -> None:
        """Load stored sessions."""
        self.log = SessionLog.from_data(await self._store.async_load())

    # --- registration by the config entries ---------------------------------

    async def async_set_vehicles(self, entry: ConfigEntry) -> None:
        """Take the vehicles from the vehicles entry; drop orphaned sessions."""
        self.vehicles = {
            sub.subentry_id: Vehicle.from_dict(
                {**sub.data, "id": sub.subentry_id, "name": sub.title}
            )
            for sub in entry.get_subentries_of_type(SUBENTRY_TYPE_VEHICLE)
        }
        self.inputs = {}
        for vehicle_id in self.vehicles:
            self._reset_input(vehicle_id)
        # The vehicles entry is the complete list: sessions of vehicles that
        # are not in it belong to deleted vehicles and are removed with them.
        removed = self.log.remove_other_vehicles(self.vehicles)
        if removed:
            _LOGGER.info("Removed %s sessions of deleted vehicles", removed)
            await self._store.async_save(self.log.to_data())
        self._async_notify()

    @callback
    def async_clear_vehicles(self) -> None:
        """Forget the vehicles (vehicles entry unloaded)."""
        self.vehicles = {}
        self.inputs = {}
        self._async_notify()

    @callback
    def async_set_tariffs(self, entry: ConfigEntry) -> None:
        """Take the tariffs from the tariffs entry."""
        self.tariffs = {
            sub.subentry_id: Tariff.from_dict(
                {**sub.data, "id": sub.subentry_id, "name": sub.title}
            )
            for sub in entry.get_subentries_of_type(SUBENTRY_TYPE_TARIFF)
        }
        self._async_fix_input_tariffs()
        self._async_notify()

    @callback
    def async_clear_tariffs(self) -> None:
        """Forget the tariffs (tariffs entry unloaded)."""
        self.tariffs = {}
        self._async_fix_input_tariffs()
        self._async_notify()

    @callback
    def _async_fix_input_tariffs(self) -> None:
        """Replace input tariffs that no longer exist by the default."""
        for vehicle_id, current in self.inputs.items():
            if current.tariff_id not in self.tariffs:
                current.tariff_id = self.default_tariff_id(vehicle_id)
            self._align_charge_type(current)

    def _align_charge_type(self, current: VehicleInput) -> None:
        """Switch AC/DC if the selected tariff only has the other price."""
        tariff = self.tariffs.get(current.tariff_id) if current.tariff_id else None
        if tariff is None or tariff.supports(current.charge_type):
            return
        for charge_type in CHARGE_TYPES:
            if tariff.supports(charge_type):
                current.charge_type = charge_type
                return

    # --- listeners --------------------------------------------------------

    @callback
    def async_add_listener(self, update: CALLBACK_TYPE) -> Callable[[], None]:
        """Register an entity update callback; returns the unsubscribe."""
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    @callback
    def _async_notify(self) -> None:
        for update in list(self._listeners):
            update()

    async def _async_commit(self) -> None:
        await self._store.async_save(self.log.to_data())
        self._async_notify()

    # --- changes ----------------------------------------------------------

    async def async_add_session(self, data: SessionInput) -> Session:
        """Book a session, persist it and refresh entities."""
        self._require_vehicle(data.vehicle_id)
        session = self.log.add(data, self.tariffs)
        await self._async_commit()
        return session

    async def async_update_session(
        self, vehicle_id: str, session_id: str | None = None, **changes: Any
    ) -> Session:
        """Correct a session (last one of the vehicle without ID)."""
        self._require_vehicle(vehicle_id)
        session = self.log.update(vehicle_id, self.tariffs, session_id, **changes)
        await self._async_commit()
        return session

    async def async_delete_session(
        self, vehicle_id: str, session_id: str | None = None
    ) -> Session:
        """Delete a session (last one of the vehicle without ID)."""
        self._require_vehicle(vehicle_id)
        session = self.log.delete(vehicle_id, session_id)
        await self._async_commit()
        return session

    # --- dashboard input ----------------------------------------------------

    def default_tariff_id(self, vehicle_id: str) -> str | None:
        """Vehicle default tariff if it still exists, else the first tariff."""
        default = self.vehicles[vehicle_id].default_tariff_id
        if default in self.tariffs:
            return default
        return next(iter(self.sorted_tariffs()), None)

    def sorted_tariffs(self) -> list[str]:
        """Tariff IDs sorted by name."""
        return sorted(self.tariffs, key=lambda t: self.tariffs[t].name.casefold())

    def _reset_input(self, vehicle_id: str) -> None:
        current = VehicleInput(tariff_id=self.default_tariff_id(vehicle_id))
        self._align_charge_type(current)
        self.inputs[vehicle_id] = current

    @callback
    def async_reset_input(self, vehicle_id: str) -> None:
        """Clear the dashboard input of a vehicle."""
        self._reset_input(vehicle_id)
        self._async_notify()

    @callback
    def async_set_input(self, vehicle_id: str, **values: Any) -> None:
        """Change dashboard input fields of a vehicle.

        Choosing a tariff that only has an AC or only a DC price switches the
        charge type accordingly. Choosing a charge type is never overridden.
        """
        current = self.inputs[vehicle_id]
        for name, value in values.items():
            setattr(current, name, value)
        if "tariff_id" in values:
            self._align_charge_type(current)
        self._async_notify()

    async def async_book_input(self, vehicle_id: str, now: datetime) -> Session:
        """Book the dashboard input as a session, then reset the input."""
        current = self.inputs[vehicle_id]
        if current.energy_kwh is None:
            raise ValidationError("energy_kwh is required")
        session = await self.async_add_session(
            SessionInput(
                vehicle_id=vehicle_id,
                energy_kwh=current.energy_kwh,
                charge_type=current.charge_type,
                timestamp=now,
                tariff_id=current.tariff_id,
                cost=current.cost,
            )
        )
        self.async_reset_input(vehicle_id)
        return session

    def _require_vehicle(self, vehicle_id: str) -> None:
        if vehicle_id not in self.vehicles:
            raise ValidationError(f"unknown vehicle: {vehicle_id}")
