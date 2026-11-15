"""Data model and pure calculation logic for EV Charge Log.

This module must not import Home Assistant so it can be tested on its own.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .const import CHARGE_TYPE_DC, CHARGE_TYPES, SOURCE_MANUAL, SOURCES


class ValidationError(ValueError):
    """Raised when input data violates the data model rules."""


def round_money(value: float) -> float:
    """Round a monetary amount to two decimals, half up."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _require_non_negative(name: str, value: float | None) -> None:
    if value is not None and value < 0:
        raise ValidationError(f"{name} must not be negative")


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


@dataclass(frozen=True, slots=True)
class Vehicle:
    """A vehicle. SoC and capacity are display values only."""

    id: str
    name: str
    battery_capacity_kwh: float | None = None
    default_tariff_id: str | None = None
    soc_entity_id: str | None = None
    charging_entity_id: str | None = None
    location_entity_id: str | None = None
    reminder_excluded_zones: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate the vehicle."""
        if self.battery_capacity_kwh is not None and self.battery_capacity_kwh <= 0:
            raise ValidationError("battery_capacity_kwh must be positive")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Vehicle:
        """Create a vehicle from stored data."""
        return cls(
            id=data["id"],
            name=data["name"],
            battery_capacity_kwh=_optional_float(data.get("battery_capacity_kwh")),
            default_tariff_id=data.get("default_tariff_id"),
            soc_entity_id=data.get("soc_entity_id"),
            charging_entity_id=data.get("charging_entity_id"),
            location_entity_id=data.get("location_entity_id"),
            reminder_excluded_zones=tuple(data.get("reminder_excluded_zones") or ()),
        )


@dataclass(frozen=True, slots=True)
class Tariff:
    """A charging tariff. Prices of 0 are valid (e.g. free charging).

    AC and DC prices are each optional, but at least one is required. A tariff
    only accepts sessions of the charge types it has a price for; there is no
    fallback from one price to the other.
    """

    id: str
    name: str
    price_ac: float | None = None
    price_dc: float | None = None
    zones: tuple[str, ...] = ()
    valid_from: date | None = None
    monthly_fee: float | None = None

    def __post_init__(self) -> None:
        """Validate the tariff."""
        if self.price_ac is None and self.price_dc is None:
            raise ValidationError("a tariff needs an AC or a DC price")
        _require_non_negative("price_ac", self.price_ac)
        _require_non_negative("price_dc", self.price_dc)
        _require_non_negative("monthly_fee", self.monthly_fee)

    def supports(self, charge_type: str) -> bool:
        """Whether the tariff has a price for this charge type."""
        return self._price(charge_type) is not None

    def price_for(self, charge_type: str) -> float:
        """Return the price per kWh; reject charge types without a price."""
        price = self._price(charge_type)
        if price is None:
            raise ValidationError(
                f"tariff {self.name} has no {charge_type.upper()} price"
            )
        return price

    def _price(self, charge_type: str) -> float | None:
        return self.price_dc if charge_type == CHARGE_TYPE_DC else self.price_ac

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Tariff:
        """Create a tariff from stored data."""
        valid_from = data.get("valid_from")
        return cls(
            id=data["id"],
            name=data["name"],
            price_ac=_optional_float(data.get("price_ac")),
            price_dc=_optional_float(data.get("price_dc")),
            zones=tuple(data.get("zones") or ()),
            valid_from=date.fromisoformat(valid_from) if valid_from else None,
            monthly_fee=_optional_float(data.get("monthly_fee")),
        )


@dataclass(frozen=True, slots=True)
class Session:
    """A booked charging session. Immutable snapshot, cost is stored."""

    id: str
    vehicle_id: str
    timestamp: datetime
    energy_kwh: float
    charge_type: str
    cost: float
    cost_overridden: bool
    tariff_id: str | None = None
    price_per_kwh: float | None = None
    soc_start: float | None = None
    soc_end: float | None = None
    location: str | None = None
    note: str | None = None
    source: str = SOURCE_MANUAL

    def __post_init__(self) -> None:
        """Normalize numbers to float and validate the session."""
        # Same types whether built from input (may be int) or loaded from storage.
        for name in ("energy_kwh", "cost", "price_per_kwh", "soc_start", "soc_end"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, float(value))
        if self.timestamp.tzinfo is None:
            raise ValidationError("timestamp must be timezone-aware")
        if self.energy_kwh <= 0:
            raise ValidationError("energy_kwh must be positive")
        if self.charge_type not in CHARGE_TYPES:
            raise ValidationError(f"invalid charge_type: {self.charge_type}")
        if self.source not in SOURCES:
            raise ValidationError(f"invalid source: {self.source}")
        _require_non_negative("cost", self.cost)
        _require_non_negative("price_per_kwh", self.price_per_kwh)
        for name in ("soc_start", "soc_end"):
            value = getattr(self, name)
            if value is not None and not 0 <= value <= 100:
                raise ValidationError(f"{name} must be between 0 and 100")

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {
            "id": self.id,
            "vehicle_id": self.vehicle_id,
            "timestamp": self.timestamp.isoformat(),
            "energy_kwh": self.energy_kwh,
            "tariff_id": self.tariff_id,
            "charge_type": self.charge_type,
            "price_per_kwh": self.price_per_kwh,
            "cost": self.cost,
            "cost_overridden": self.cost_overridden,
            "soc_start": self.soc_start,
            "soc_end": self.soc_end,
            "location": self.location,
            "note": self.note,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        """Create a session from stored data."""
        return cls(
            id=data["id"],
            vehicle_id=data["vehicle_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            energy_kwh=float(data["energy_kwh"]),
            tariff_id=data.get("tariff_id"),
            charge_type=data["charge_type"],
            price_per_kwh=_optional_float(data.get("price_per_kwh")),
            cost=float(data["cost"]),
            cost_overridden=bool(data["cost_overridden"]),
            soc_start=_optional_float(data.get("soc_start")),
            soc_end=_optional_float(data.get("soc_end")),
            location=data.get("location"),
            note=data.get("note"),
            source=data.get("source", SOURCE_MANUAL),
        )


@dataclass(frozen=True, slots=True)
class CostResult:
    """Result of the cost calculation at booking time."""

    cost: float
    price_per_kwh: float | None
    cost_overridden: bool


def calculate_cost(
    energy_kwh: float,
    charge_type: str,
    tariff: Tariff | None = None,
    cost: float | None = None,
) -> CostResult:
    """Calculate the cost of a session at booking time.

    A given amount (including 0) always wins. Otherwise energy x tariff price.
    `None` means "not given"; 0 is a valid value. A tariff without a price
    for the charge type is rejected, also with a given amount: the tariff
    does not offer that charge type.
    """
    if charge_type not in CHARGE_TYPES:
        raise ValidationError(f"invalid charge_type: {charge_type}")
    if energy_kwh <= 0:
        raise ValidationError("energy_kwh must be positive")
    _require_non_negative("cost", cost)

    price = tariff.price_for(charge_type) if tariff is not None else None

    if cost is not None:
        return CostResult(round_money(cost), price, cost_overridden=True)
    if price is None:
        raise ValidationError("either a cost or a tariff is required")
    return CostResult(round_money(energy_kwh * price), price, cost_overridden=False)


@dataclass(frozen=True, slots=True)
class Summary:
    """Aggregated values over a set of sessions."""

    total_cost: float
    total_energy_kwh: float
    session_count: int

    @property
    def average_price_per_kwh(self) -> float | None:
        """Total cost / total energy over all sessions, including free ones."""
        if self.total_energy_kwh == 0:
            return None
        return round(self.total_cost / self.total_energy_kwh, 4)


def summarize(sessions: Iterable[Session]) -> Summary:
    """Sum up cost, energy and count."""
    cost = Decimal(0)
    energy = Decimal(0)
    count = 0
    for session in sessions:
        cost += Decimal(str(session.cost))
        energy += Decimal(str(session.energy_kwh))
        count += 1
    return Summary(float(cost), float(energy), count)


@dataclass(frozen=True, slots=True)
class SocMetrics:
    """Display-only values derived from SoC. Never used for cost or energy."""

    soc_points: float
    charging_loss_percent: float | None
    cost_per_soc_point: float | None


def soc_metrics(
    session: Session, battery_capacity_kwh: float | None
) -> SocMetrics | None:
    """Return SoC-based display values, or None if SoC data is missing."""
    if session.soc_start is None or session.soc_end is None:
        return None
    points = session.soc_end - session.soc_start
    loss = None
    if battery_capacity_kwh:
        stored_kwh = points / 100 * battery_capacity_kwh
        loss = round((session.energy_kwh - stored_kwh) / session.energy_kwh * 100, 1)
    cost_per_point = round(session.cost / points, 4) if points > 0 else None
    return SocMetrics(points, loss, cost_per_point)


@dataclass(slots=True)
class SessionInput:
    """Raw input for booking a session, before cost calculation."""

    vehicle_id: str
    energy_kwh: float
    charge_type: str
    timestamp: datetime
    tariff_id: str | None = None
    cost: float | None = None
    soc_start: float | None = None
    soc_end: float | None = None
    location: str | None = None
    note: str | None = None
    source: str = SOURCE_MANUAL


def build_session(
    session_id: str, data: SessionInput, tariff: Tariff | None
) -> Session:
    """Book a session: calculate and freeze the cost."""
    if data.tariff_id is not None and (tariff is None or tariff.id != data.tariff_id):
        raise ValidationError(f"unknown tariff: {data.tariff_id}")
    result = calculate_cost(data.energy_kwh, data.charge_type, tariff, data.cost)
    return Session(
        id=session_id,
        vehicle_id=data.vehicle_id,
        timestamp=data.timestamp,
        energy_kwh=data.energy_kwh,
        tariff_id=data.tariff_id,
        charge_type=data.charge_type,
        price_per_kwh=result.price_per_kwh,
        cost=result.cost,
        cost_overridden=result.cost_overridden,
        soc_start=data.soc_start,
        soc_end=data.soc_end,
        location=data.location,
        note=data.note,
        source=data.source,
    )
