"""Runtime state for EV Charge Log: sessions, vehicles, tariffs, listeners.

All calculation lives in models.py / storage.py. This module only wires
them to Home Assistant (storage, config subentries, entity updates).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN, SUBENTRY_TYPE_TARIFF, SUBENTRY_TYPE_VEHICLE
from .models import Session, SessionInput, Tariff, ValidationError, Vehicle
from .storage import STORAGE_MINOR_VERSION, STORAGE_VERSION, SessionLog, migrate

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = f"{DOMAIN}.sessions"

type EvChargeLogConfigEntry = ConfigEntry[EvChargeLogCoordinator]


class _SessionStore(Store[dict[str, Any]]):
    """Store that delegates migration to the pure `storage.migrate`."""

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        return migrate(old_major_version, old_minor_version, old_data)


class EvChargeLogCoordinator:
    """Holds the session log and notifies entities after every change."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize from the config entry's subentries."""
        self.hass = hass
        self._store = _SessionStore(
            hass, STORAGE_VERSION, STORAGE_KEY, minor_version=STORAGE_MINOR_VERSION
        )
        self.log = SessionLog()
        self.vehicles: dict[str, Vehicle] = {}
        self.tariffs: dict[str, Tariff] = {}
        self._listeners: list[CALLBACK_TYPE] = []
        self._load_subentries(entry)

    def _load_subentries(self, entry: ConfigEntry) -> None:
        for sub in entry.get_subentries_of_type(SUBENTRY_TYPE_VEHICLE):
            self.vehicles[sub.subentry_id] = Vehicle.from_dict(
                {**sub.data, "id": sub.subentry_id, "name": sub.title}
            )
        for sub in entry.get_subentries_of_type(SUBENTRY_TYPE_TARIFF):
            self.tariffs[sub.subentry_id] = Tariff.from_dict(
                {**sub.data, "id": sub.subentry_id, "name": sub.title}
            )

    async def async_load(self) -> None:
        """Load stored sessions; drop those of deleted vehicles."""
        self.log = SessionLog.from_data(await self._store.async_load())
        removed = self.log.remove_other_vehicles(self.vehicles)
        if removed:
            _LOGGER.info("Removed %s sessions of deleted vehicles", removed)
            await self._store.async_save(self.log.to_data())

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

    def _require_vehicle(self, vehicle_id: str) -> None:
        if vehicle_id not in self.vehicles:
            raise ValidationError(f"unknown vehicle: {vehicle_id}")
