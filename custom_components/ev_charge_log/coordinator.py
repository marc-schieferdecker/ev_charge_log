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
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any

from homeassistant.components import frontend, persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_HOME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .const import (
    CHARGE_TYPE_AC,
    CHARGE_TYPES,
    DOMAIN,
    EVENT_CHARGING_FINISHED,
    PANEL_URL_PATH,
    SOURCE_REMINDER,
    SUBENTRY_TYPE_TARIFF,
    SUBENTRY_TYPE_VEHICLE,
    ZONE_HOME,
)
from .detection import (
    DetectionState,
    PendingCharge,
    after_booking,
    booking_defaults,
    on_charging_state,
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
            coord.async_start_fee_timer()
            hass.data[COORDINATOR_KEY] = coord
    return coord


@callback
def async_release_coordinator(hass: HomeAssistant) -> None:
    """Drop the shared coordinator once no entry uses it any more."""
    if (coord := hass.data.pop(COORDINATOR_KEY, None)) is not None:
        coord.async_shutdown()


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
    day: date | None = None  # None: today


class EvChargeLogCoordinator:
    """Holds the session log and notifies entities after every change."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize empty; entries register vehicles and tariffs."""
        self.hass = hass
        self._store = _SessionStore(
            hass, STORAGE_VERSION, STORAGE_KEY, minor_version=STORAGE_MINOR_VERSION
        )
        self.log = SessionLog()
        self.detection: dict[str, DetectionState] = {}
        self.vehicles: dict[str, Vehicle] = {}
        self.tariffs: dict[str, Tariff] = {}
        self.inputs: dict[str, VehicleInput] = {}
        self._vehicles_entry_id: str | None = None
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsub_tracking: list[CALLBACK_TYPE] = []
        # State changes are applied one after another, never interleaved.
        self._detection_lock = asyncio.Lock()
        self._fee_lock = asyncio.Lock()
        self._unsub_fee_timer: CALLBACK_TYPE | None = None

    async def async_load(self) -> None:
        """Load stored sessions and the detection state."""
        data = await self._store.async_load() or {}
        self.log = SessionLog.from_data(data)
        self.detection = {
            vehicle_id: DetectionState.from_dict(state)
            for vehicle_id, state in (data.get("detection") or {}).items()
        }

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                **self.log.to_data(),
                "detection": {v: s.to_dict() for v, s in self.detection.items()},
            }
        )

    # --- registration by the config entries ---------------------------------

    async def async_set_vehicles(self, entry: ConfigEntry) -> None:
        """Take the vehicles from the vehicles entry; drop orphaned data."""
        self._vehicles_entry_id = entry.entry_id
        self.vehicles = {
            sub.subentry_id: Vehicle.from_dict(
                {**sub.data, "id": sub.subentry_id, "name": sub.title}
            )
            for sub in entry.get_subentries_of_type(SUBENTRY_TYPE_VEHICLE)
        }
        self.inputs = {}
        for vehicle_id in self.vehicles:
            self._reset_input(vehicle_id)
        # The vehicles entry is the complete list: sessions and detection
        # state of vehicles not in it belong to deleted vehicles.
        removed = self.log.remove_other_vehicles(self.vehicles)
        orphans = [v for v in self.detection if v not in self.vehicles]
        for vehicle_id in orphans:
            del self.detection[vehicle_id]
        if removed:
            _LOGGER.info("Removed %s sessions of deleted vehicles", removed)
        if removed or orphans:
            await self._async_save()
        self._async_start_tracking()
        await self.async_book_fees()
        self._async_notify()

    @callback
    def async_clear_vehicles(self) -> None:
        """Forget the vehicles (vehicles entry unloaded)."""
        self._async_stop_tracking()
        self.vehicles = {}
        self.inputs = {}
        self._vehicles_entry_id = None
        self._async_notify()

    # --- monthly fees (stage 4) ----------------------------------------------

    async def async_book_fees(self, *_: Any) -> None:
        """Book all monthly fees due up to today (local date).

        Runs when vehicles or tariffs are loaded and every night. Only
        tariffs whose fee vehicle exists are considered, so nothing is booked
        before the vehicles entry is loaded.
        """
        async with self._fee_lock:
            if not self.vehicles:
                return
            tariffs = [
                t for t in self.tariffs.values() if t.fee_vehicle_id in self.vehicles
            ]
            booked = self.log.book_due_fees(tariffs, dt_util.now().date())
            if booked:
                _LOGGER.debug("Booked %s monthly fees", len(booked))
                await self._async_commit()

    @callback
    def async_start_fee_timer(self) -> None:
        """Book fees shortly after midnight, when a new month may start."""
        if self._unsub_fee_timer is None:
            self._unsub_fee_timer = async_track_time_change(
                self.hass, self.async_book_fees, hour=0, minute=0, second=30
            )

    @callback
    def async_shutdown(self) -> None:
        """Stop timers and state tracking (last entry unloaded)."""
        self._async_stop_tracking()
        if self._unsub_fee_timer is not None:
            self._unsub_fee_timer()
            self._unsub_fee_timer = None

    # --- charge detection (stage 2) -----------------------------------------

    @callback
    def _async_start_tracking(self) -> None:
        """Follow the charging entities; evaluate their current state once."""
        self._async_stop_tracking()
        for vehicle in self.vehicles.values():
            if not vehicle.charging_entity_id:
                continue
            vehicle_id = vehicle.id

            @callback
            def _changed(event: Event[EventStateChangedData], vid=vehicle_id) -> None:
                new_state = event.data["new_state"]
                self.hass.async_create_task(
                    self._async_charging_state(
                        vid, new_state.state if new_state else None
                    )
                )

            self._unsub_tracking.append(
                async_track_state_change_event(
                    self.hass, [vehicle.charging_entity_id], _changed
                )
            )
            # A charge may have started or ended while HA was not running.
            if (state := self.hass.states.get(vehicle.charging_entity_id)) is not None:
                self.hass.async_create_task(
                    self._async_charging_state(vehicle_id, state.state)
                )

    @callback
    def _async_stop_tracking(self) -> None:
        for unsub in self._unsub_tracking:
            unsub()
        self._unsub_tracking = []

    async def _async_charging_state(
        self, vehicle_id: str, new_state: str | None
    ) -> None:
        """Apply a charging state change of a vehicle."""
        async with self._detection_lock:
            vehicle = self.vehicles.get(vehicle_id)
            if vehicle is None:
                return
            current = self.detection.get(vehicle_id, DetectionState())
            result = on_charging_state(
                current,
                new_state,
                dt_util.now(),
                self._soc(vehicle),
                self._zone(vehicle),
            )
            if result.state == current:
                return
            self.detection[vehicle_id] = result.state
            await self._async_save()
            if result.ended is not None:
                self._async_charge_ended(vehicle, result.ended)
            self._async_notify()

    @callback
    def _async_charge_ended(self, vehicle: Vehicle, charge: PendingCharge) -> None:
        """Preselect the zone's tariff and send the reminder event."""
        tariff = self.tariff_for_zone(charge.zone)
        if tariff is not None and vehicle.id in self.inputs:
            current = self.inputs[vehicle.id]
            current.tariff_id = tariff.id
            self._align_charge_type(current)
        if charge.zone is not None and charge.zone in vehicle.reminder_excluded_zones:
            return
        self.hass.async_create_task(
            self._async_notify_charge(vehicle, charge, tariff),
            f"{DOMAIN} reminder {vehicle.id}",
        )
        self.hass.bus.async_fire(
            EVENT_CHARGING_FINISHED,
            {
                "vehicle_id": vehicle.id,
                "vehicle_name": vehicle.name,
                "device_id": self._device_id(vehicle.id),
                "started_at": charge.started_at.isoformat()
                if charge.started_at
                else None,
                "ended_at": charge.ended_at.isoformat(),
                "soc_start": charge.soc_start,
                "soc_end": charge.soc_end,
                "zone": charge.zone,
                "tariff_id": tariff.id if tariff else None,
                "tariff_name": tariff.name if tariff else None,
            },
        )

    async def _async_notify_charge(
        self, vehicle: Vehicle, charge: PendingCharge, tariff: Tariff | None
    ) -> None:
        """Show the reminder in the HA notifications (no automation needed).

        One notification per vehicle; it is dismissed when the charge is
        booked. The texts come from the translations ("exceptions" category,
        the one HA provides for backend messages with placeholders).
        """
        texts = await async_get_translations(
            self.hass, self.hass.config.language, "exceptions", [DOMAIN]
        )
        prefix = f"component.{DOMAIN}.exceptions"

        def number(value: float | None) -> str:
            return "-" if value is None else f"{value:g}"

        device_id = self._device_id(vehicle.id)
        if PANEL_URL_PATH in self.hass.data.get(frontend.DATA_PANELS, {}):
            url = f"/{PANEL_URL_PATH}"
        elif device_id:
            url = f"/config/devices/device/{device_id}"
        else:
            url = "/config"
        placeholders = {
            "vehicle": vehicle.name,
            "time": dt_util.as_local(charge.ended_at).strftime("%Y-%m-%d %H:%M"),
            "soc_start": number(charge.soc_start),
            "soc_end": number(charge.soc_end),
            "tariff": tariff.name if tariff else "-",
            "url": url,
        }
        title = texts.get(f"{prefix}.reminder_title.message", "{vehicle}")
        message = texts.get(f"{prefix}.reminder_message.message", "{time}")
        persistent_notification.async_create(
            self.hass,
            message.format(**placeholders),
            title=title.format(**placeholders),
            notification_id=self._notification_id(vehicle.id),
        )

    @staticmethod
    def _notification_id(vehicle_id: str) -> str:
        return f"{DOMAIN}_charge_{vehicle_id}"

    def tariff_for_zone(self, zone: str | None) -> Tariff | None:
        """The tariff a zone is assigned to (a zone belongs to at most one)."""
        if zone is None:
            return None
        return next((t for t in self.tariffs.values() if zone in t.zones), None)

    def _soc(self, vehicle: Vehicle) -> float | None:
        """Current SoC in percent, None if not configured or not available."""
        if not vehicle.soc_entity_id:
            return None
        state = self.hass.states.get(vehicle.soc_entity_id)
        try:
            value = float(state.state) if state else None
        except ValueError:
            return None
        return value if value is not None and 0 <= value <= 100 else None

    def _zone(self, vehicle: Vehicle) -> str | None:
        """Zone entity the vehicle is in, None if unknown or in no zone.

        Trackers and persons report the zone name as their state ("home" for
        the home zone).
        """
        if not vehicle.location_entity_id:
            return None
        state = self.hass.states.get(vehicle.location_entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        if state.state == STATE_HOME:
            return ZONE_HOME
        for zone in self.hass.states.async_all("zone"):
            if zone.name == state.state:
                return zone.entity_id
        return None

    def _device_id(self, vehicle_id: str) -> str | None:
        if self._vehicles_entry_id is None:
            return None
        registry = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(
            registry, self._vehicles_entry_id
        ):
            if (DOMAIN, vehicle_id) in device.identifiers:
                return device.id
        return None

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
        self.hass.async_create_task(self.async_book_fees(), f"{DOMAIN} fees")
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
        today = dt_util.now().date()
        if tariff is None or tariff.supports(current.charge_type, today):
            return
        for charge_type in CHARGE_TYPES:
            if tariff.supports(charge_type, today):
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
        await self._async_save()
        self._async_notify()

    # --- changes ----------------------------------------------------------

    async def async_add_session(
        self, data: SessionInput, *, timestamp_given: bool = True
    ) -> Session:
        """Book a session, persist it and refresh entities.

        Values from the charge detection fill what was not given by hand:
        the end time of a detected charge (if no time was given) and the
        start/end SoC. A booking that takes over a detected charge gets the
        source "reminder"; the detected values are dropped afterwards.
        """
        self._require_vehicle(data.vehicle_id)
        vehicle = self.vehicles[data.vehicle_id]
        current = self.detection.get(data.vehicle_id, DetectionState())
        defaults = booking_defaults(current, self._soc(vehicle))
        timestamp = (
            defaults.timestamp
            if defaults.timestamp is not None and not timestamp_given
            else data.timestamp
        )
        data = replace(
            data,
            # Local time: the price version is chosen by the calendar day.
            timestamp=dt_util.as_local(timestamp),
            soc_start=data.soc_start
            if data.soc_start is not None
            else defaults.soc_start,
            soc_end=data.soc_end if data.soc_end is not None else defaults.soc_end,
            source=SOURCE_REMINDER if defaults.from_detection else data.source,
        )
        session = self.log.add(data, self.tariffs)
        if defaults.from_detection:
            self.detection[data.vehicle_id] = after_booking(current)
            persistent_notification.async_dismiss(
                self.hass, self._notification_id(data.vehicle_id)
            )
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
        """Clear the input; preselect the tariff of a detected charge's zone."""
        detected = self.detection.get(vehicle_id)
        zone_tariff = (
            self.tariff_for_zone(detected.pending.zone)
            if detected is not None and detected.pending is not None
            else None
        )
        tariff_id = (
            zone_tariff.id if zone_tariff else self.default_tariff_id(vehicle_id)
        )
        current = VehicleInput(tariff_id=tariff_id)
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
        """Book the dashboard input as a session, then reset the input.

        `now` must be local time. Without an entered date the session gets
        the end of a detected charge, else `now`. With a date it gets that
        date at the current time of day, so several sessions of one day keep a
        sensible order.
        """
        current = self.inputs[vehicle_id]
        if current.energy_kwh is None:
            raise ValidationError("energy_kwh is required")
        timestamp = (
            now
            if current.day is None
            else now.replace(
                year=current.day.year, month=current.day.month, day=current.day.day
            )
        )
        session = await self.async_add_session(
            SessionInput(
                vehicle_id=vehicle_id,
                energy_kwh=current.energy_kwh,
                charge_type=current.charge_type,
                timestamp=timestamp,
                tariff_id=current.tariff_id,
                cost=current.cost,
            ),
            timestamp_given=current.day is not None,
        )
        self.async_reset_input(vehicle_id)
        return session

    def _require_vehicle(self, vehicle_id: str) -> None:
        if vehicle_id not in self.vehicles:
            raise ValidationError(f"unknown vehicle: {vehicle_id}")
