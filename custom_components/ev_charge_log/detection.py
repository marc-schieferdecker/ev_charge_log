"""Charge detection as a reminder (stage 2). Pure Python, no HA imports.

The detection never calculates or books anything. It only remembers when a
vehicle started and stopped charging and the SoC at those moments, so the
next booking can be prefilled and a reminder can be sent.

Rules:
- Only `on` and `off` count. Other states (`unavailable`, `unknown`, ...)
  are skipped; a new state is compared with the last *valid* state.
- off -> on: charge started, remember start time and SoC.
- on -> off: charge ended, the charge becomes "pending" (detected, not yet
  booked) with start/end time and SoC.
- The next booking of the vehicle takes the pending values; afterwards they
  are dropped. A booking while charging marks that charge as booked, so its
  end creates no pending charge and no reminder.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

STATE_ON = "on"
STATE_OFF = "off"


@dataclass(frozen=True, slots=True)
class PendingCharge:
    """A detected charge that has not been booked yet."""

    started_at: datetime | None
    ended_at: datetime
    soc_start: float | None
    soc_end: float | None
    zone: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat(),
            "soc_start": self.soc_start,
            "soc_end": self.soc_end,
            "zone": self.zone,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingCharge:
        """Load from storage."""
        started = data.get("started_at")
        return cls(
            started_at=datetime.fromisoformat(started) if started else None,
            ended_at=datetime.fromisoformat(data["ended_at"]),
            soc_start=data.get("soc_start"),
            soc_end=data.get("soc_end"),
            zone=data.get("zone"),
        )


@dataclass(frozen=True, slots=True)
class DetectionState:
    """Remembered detection state of one vehicle (persisted)."""

    last_state: str | None = None  # last valid state: "on", "off" or None
    started_at: datetime | None = None
    soc_start: float | None = None
    booked_while_charging: bool = False
    pending: PendingCharge | None = None

    @property
    def charging(self) -> bool:
        """Whether the vehicle is charging according to the last valid state."""
        return self.last_state == STATE_ON

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {
            "last_state": self.last_state,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "soc_start": self.soc_start,
            "booked_while_charging": self.booked_while_charging,
            "pending": self.pending.to_dict() if self.pending else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DetectionState:
        """Load from storage."""
        started = data.get("started_at")
        pending = data.get("pending")
        return cls(
            last_state=data.get("last_state"),
            started_at=datetime.fromisoformat(started) if started else None,
            soc_start=data.get("soc_start"),
            booked_while_charging=bool(data.get("booked_while_charging", False)),
            pending=PendingCharge.from_dict(pending) if pending else None,
        )


@dataclass(frozen=True, slots=True)
class Transition:
    """Result of a state change."""

    state: DetectionState
    started: bool = False
    ended: PendingCharge | None = None


def on_charging_state(
    current: DetectionState,
    new_state: str | None,
    now: datetime,
    soc: float | None,
    zone: str | None = None,
) -> Transition:
    """Apply a new charging state.

    `soc` is the vehicle SoC at this moment (None if unavailable), `zone` the
    zone the vehicle is in (only used when a charge ends).
    """
    if new_state not in (STATE_ON, STATE_OFF):
        return Transition(current)  # unavailable/unknown: skip
    if new_state == current.last_state:
        return Transition(current)

    if new_state == STATE_ON:
        # Charge started (also when the previous state is not known yet).
        return Transition(
            replace(
                current,
                last_state=STATE_ON,
                started_at=now,
                soc_start=soc,
                booked_while_charging=False,
            ),
            started=True,
        )

    # new_state == off
    if current.last_state is None:
        return Transition(replace(current, last_state=STATE_OFF))
    if current.booked_while_charging:
        return Transition(
            replace(
                current,
                last_state=STATE_OFF,
                started_at=None,
                soc_start=None,
                booked_while_charging=False,
            )
        )
    pending = PendingCharge(
        started_at=current.started_at,
        ended_at=now,
        soc_start=current.soc_start,
        soc_end=soc,
        zone=zone,
    )
    return Transition(
        replace(
            current,
            last_state=STATE_OFF,
            started_at=None,
            soc_start=None,
            pending=pending,
        ),
        ended=pending,
    )


@dataclass(frozen=True, slots=True)
class BookingDefaults:
    """Values a booking takes from the detection unless given by hand."""

    timestamp: datetime | None
    soc_start: float | None
    soc_end: float | None
    from_detection: bool


def booking_defaults(current: DetectionState, soc_now: float | None) -> BookingDefaults:
    """Defaults for the next booking.

    - Pending charge: its end time and both SoC values.
    - Charging right now: the remembered start SoC and the current SoC.
    - Otherwise: only the current SoC as end SoC.
    """
    if current.pending is not None:
        return BookingDefaults(
            timestamp=current.pending.ended_at,
            soc_start=current.pending.soc_start,
            soc_end=current.pending.soc_end,
            from_detection=True,
        )
    if current.charging and not current.booked_while_charging:
        return BookingDefaults(
            timestamp=None,
            soc_start=current.soc_start,
            soc_end=soc_now,
            from_detection=True,
        )
    return BookingDefaults(
        timestamp=None, soc_start=None, soc_end=soc_now, from_detection=False
    )


def after_booking(current: DetectionState) -> DetectionState:
    """Drop the values a booking has taken over."""
    if current.pending is not None:
        return replace(current, pending=None)
    if current.charging:
        return replace(current, booked_while_charging=True)
    return current
