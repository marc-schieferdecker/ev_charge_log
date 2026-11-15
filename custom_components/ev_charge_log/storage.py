"""Session management and storage format for EV Charge Log.

Pure Python: no Home Assistant imports, so it can be tested on its own.
The Home Assistant `Store` wrapper lives in coordinator.py and delegates
migration to `migrate()`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime
from enum import Enum
from typing import Any, Final

from .models import (
    Session,
    SessionInput,
    Summary,
    Tariff,
    ValidationError,
    build_session,
    round_money,
    summarize,
)

STORAGE_VERSION: Final = 1
STORAGE_MINOR_VERSION: Final = 1


class SessionNotFoundError(LookupError):
    """Raised when a session (or a vehicle's last session) does not exist."""


class _Unset(Enum):
    UNSET = "UNSET"


UNSET: Final = _Unset.UNSET
"""Marks a field as "not changed" in `update`. `None` means "clear"."""


def _new_id() -> str:
    return uuid.uuid4().hex


class SessionLog:
    """In-memory list of booked sessions with booking, correction and deletion."""

    def __init__(
        self,
        sessions: Iterable[Session] = (),
        id_factory: Callable[[], str] = _new_id,
    ) -> None:
        """Initialize the log."""
        self._sessions: dict[str, Session] = {s.id: s for s in sessions}
        self._id_factory = id_factory

    # --- queries ----------------------------------------------------------

    def get(self, session_id: str) -> Session:
        """Return a session by ID."""
        try:
            return self._sessions[session_id]
        except KeyError:
            raise SessionNotFoundError(session_id) from None

    def last_session(self, vehicle_id: str) -> Session | None:
        """Return the vehicle's session with the latest timestamp."""
        sessions = self.sessions(vehicle_id=vehicle_id)
        return sessions[-1] if sessions else None

    def sessions(
        self,
        vehicle_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Session]:
        """Return sessions sorted by timestamp, optionally filtered (inclusive)."""
        result = [
            s
            for s in self._sessions.values()
            if (vehicle_id is None or s.vehicle_id == vehicle_id)
            and (start is None or s.timestamp >= start)
            and (end is None or s.timestamp <= end)
        ]
        # Stable sort keeps booking order for equal timestamps.
        return sorted(result, key=lambda s: s.timestamp)

    def summary_for_vehicle(self, vehicle_id: str) -> Summary:
        """Totals over all sessions of a vehicle."""
        return summarize(self.sessions(vehicle_id=vehicle_id))

    def summary_for_tariff(self, tariff_id: str) -> Summary:
        """Totals over all sessions booked with a tariff."""
        return summarize(s for s in self.sessions() if s.tariff_id == tariff_id)

    # --- changes ----------------------------------------------------------

    def add(self, data: SessionInput, tariffs: Mapping[str, Tariff]) -> Session:
        """Book a new session with the tariff's current price."""
        tariff = _lookup_tariff(data.tariff_id, tariffs)
        session = build_session(self._id_factory(), data, tariff)
        self._sessions[session.id] = session
        return session

    def update(
        self,
        vehicle_id: str,
        tariffs: Mapping[str, Tariff],
        session_id: str | None = None,
        *,
        timestamp: datetime | _Unset = UNSET,
        energy_kwh: float | _Unset = UNSET,
        charge_type: str | _Unset = UNSET,
        tariff_id: str | _Unset | None = UNSET,
        cost: float | _Unset | None = UNSET,
        soc_start: float | _Unset | None = UNSET,
        soc_end: float | _Unset | None = UNSET,
        location: str | _Unset | None = UNSET,
        note: str | _Unset | None = UNSET,
    ) -> Session:
        """Correct a session. Without ID the vehicle's last session.

        Price rules:
        - Same tariff and charge type: the stored price_per_kwh stays.
        - Tariff or charge type changed: the tariff's current price for the
          (new) charge type applies.
        - A manual cost stays until it is changed or cleared (cost=None).
        """
        old = self._resolve(vehicle_id, session_id)

        changes: dict[str, Any] = {
            name: value
            for name, value in {
                "timestamp": timestamp,
                "energy_kwh": energy_kwh,
                "charge_type": charge_type,
                "soc_start": soc_start,
                "soc_end": soc_end,
                "location": location,
                "note": note,
            }.items()
            if value is not UNSET
        }
        new = replace(old, **changes)

        if tariff_id is not UNSET:
            new = replace(new, tariff_id=tariff_id)
        if new.tariff_id != old.tariff_id or new.charge_type != old.charge_type:
            tariff = _lookup_tariff(new.tariff_id, tariffs)
            price = tariff.price_for(new.charge_type) if tariff else None
            new = replace(new, price_per_kwh=price)

        if cost is not UNSET and cost is not None:
            new = replace(new, cost=round_money(cost), cost_overridden=True)
        elif cost is None or not new.cost_overridden:
            if new.price_per_kwh is None:
                raise ValidationError("either a cost or a tariff is required")
            new = replace(
                new,
                cost=round_money(new.energy_kwh * new.price_per_kwh),
                cost_overridden=False,
            )

        self._sessions[new.id] = new
        return new

    def delete(self, vehicle_id: str, session_id: str | None = None) -> Session:
        """Delete a session. Without ID the vehicle's last session."""
        session = self._resolve(vehicle_id, session_id)
        del self._sessions[session.id]
        return session

    def _resolve(self, vehicle_id: str, session_id: str | None) -> Session:
        if session_id is None:
            session = self.last_session(vehicle_id)
            if session is None:
                raise SessionNotFoundError(f"no sessions for vehicle {vehicle_id}")
            return session
        session = self.get(session_id)
        if session.vehicle_id != vehicle_id:
            raise SessionNotFoundError(session_id)
        return session

    # --- persistence ------------------------------------------------------

    def to_data(self) -> dict[str, Any]:
        """Serialize for the Home Assistant store."""
        return {"sessions": [s.to_dict() for s in self.sessions()]}

    @classmethod
    def from_data(
        cls,
        data: Mapping[str, Any] | None,
        id_factory: Callable[[], str] = _new_id,
    ) -> SessionLog:
        """Load from store data (already migrated). None means empty store."""
        sessions = (data or {}).get("sessions", [])
        return cls((Session.from_dict(s) for s in sessions), id_factory)


def _lookup_tariff(
    tariff_id: str | None, tariffs: Mapping[str, Tariff]
) -> Tariff | None:
    if tariff_id is None:
        return None
    try:
        return tariffs[tariff_id]
    except KeyError:
        raise ValidationError(f"unknown tariff: {tariff_id}") from None


def migrate(old_major: int, old_minor: int, data: dict[str, Any]) -> dict[str, Any]:
    """Migrate store data to the current version.

    Called by the Home Assistant Store. Add one step per version bump and
    never change existing steps.
    """
    if old_major > STORAGE_VERSION:
        raise ValueError(f"unsupported storage version {old_major}")
    # No migrations yet (version 1.1 is the first format).
    return data
