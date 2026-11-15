"""Data model and pure calculation logic for EV Charge Log.

This module must not import Home Assistant so it can be tested on its own.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .const import CHARGE_TYPE_DC, CHARGE_TYPES, SOURCE_MANUAL, SOURCES


class ValidationError(ValueError):
    """Raised when input data violates the data model rules."""


_ENERGY_TEXT = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(?:kwh)?\s*$", re.IGNORECASE)


def parse_energy(text: str | None) -> float | None:
    """kWh from a typed reply ("12,4", "12.4", "12 kWh"); None if invalid."""
    if text is None or (match := _ENERGY_TEXT.match(text)) is None:
        return None
    value = float(match.group(1).replace(",", "."))
    return value if value > 0 else None


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
    # Stage 4: notify services of the Companion app (e.g. "mobile_app_pixel")
    notify_services: tuple[str, ...] = ()

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
            notify_services=tuple(data.get("notify_services") or ()),
        )


def _validate_prices(price_ac: float | None, price_dc: float | None) -> None:
    if price_ac is None and price_dc is None:
        raise ValidationError("a price version needs an AC or a DC price")
    _require_non_negative("price_ac", price_ac)
    _require_non_negative("price_dc", price_dc)


@dataclass(frozen=True, slots=True)
class PriceVersion:
    """Prices valid from a date on (stage 3)."""

    valid_from: date
    price_ac: float | None = None
    price_dc: float | None = None
    # Stage 4: None means "the previous monthly fee still applies".
    monthly_fee: float | None = None

    def __post_init__(self) -> None:
        """Validate the prices."""
        _validate_prices(self.price_ac, self.price_dc)
        _require_non_negative("monthly_fee", self.monthly_fee)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for subentry data."""
        return {
            "valid_from": self.valid_from.isoformat(),
            "price_ac": self.price_ac,
            "price_dc": self.price_dc,
            "monthly_fee": self.monthly_fee,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PriceVersion:
        """Load from subentry data."""
        return cls(
            valid_from=date.fromisoformat(data["valid_from"]),
            price_ac=_optional_float(data.get("price_ac")),
            price_dc=_optional_float(data.get("price_dc")),
            monthly_fee=_optional_float(data.get("monthly_fee")),
        )


def month_of(day: date) -> str:
    """Month key "YYYY-MM"."""
    return f"{day.year:04d}-{day.month:02d}"


def _months(first: date, last: date) -> list[date]:
    """First days of all months from `first` to `last` (inclusive)."""
    months = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append(date(year, month, 1))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


@dataclass(frozen=True, slots=True)
class Tariff:
    """A charging tariff. Prices of 0 are valid (e.g. free charging).

    `price_ac`/`price_dc` are the base prices, valid from the beginning.
    `versions` (stage 3) are later prices, each valid from its date on. For a
    session the newest version whose date is on or before the session's day
    applies, otherwise the base prices.

    Per version, AC and DC prices are each optional, but at least one is
    required. A tariff only accepts sessions of the charge types it has a
    price for on that day; there is no fallback from one price to the other.

    Monthly fee (stage 4): `monthly_fee` is charged to `fee_vehicle_id` for
    every month from `fee_start` to `fee_end` (both inclusive, open end if
    None). A version may change the fee from its date on.
    """

    id: str
    name: str
    price_ac: float | None = None
    price_dc: float | None = None
    zones: tuple[str, ...] = ()
    versions: tuple[PriceVersion, ...] = ()
    monthly_fee: float | None = None
    fee_vehicle_id: str | None = None
    fee_start: date | None = None
    fee_end: date | None = None

    def __post_init__(self) -> None:
        """Validate the tariff and sort the versions by date."""
        _validate_prices(self.price_ac, self.price_dc)
        _require_non_negative("monthly_fee", self.monthly_fee)
        if self.fee_start and self.fee_end and self.fee_end < self.fee_start:
            raise ValidationError("fee_end is before fee_start")
        days = [v.valid_from for v in self.versions]
        if len(days) != len(set(days)):
            raise ValidationError("two price versions with the same date")
        object.__setattr__(
            self, "versions", tuple(sorted(self.versions, key=lambda v: v.valid_from))
        )

    def prices_on(self, day: date | None) -> tuple[float | None, float | None]:
        """(AC, DC) price valid on a day; None means the newest version."""
        if day is None and self.versions:
            newest = self.versions[-1]
            return newest.price_ac, newest.price_dc
        for version in reversed(self.versions):
            if day is not None and version.valid_from <= day:
                return version.price_ac, version.price_dc
        return self.price_ac, self.price_dc

    def supports(self, charge_type: str, day: date | None = None) -> bool:
        """Whether the tariff has a price for this charge type on that day."""
        return self._price(charge_type, day) is not None

    def price_for(self, charge_type: str, day: date | None = None) -> float:
        """Return the price per kWh on a day; reject charge types without one."""
        price = self._price(charge_type, day)
        if price is None:
            raise ValidationError(
                f"tariff {self.name} has no {charge_type.upper()} price"
            )
        return price

    def _price(self, charge_type: str, day: date | None) -> float | None:
        price_ac, price_dc = self.prices_on(day)
        return price_dc if charge_type == CHARGE_TYPE_DC else price_ac

    def fee_on(self, day: date) -> float | None:
        """Monthly fee valid on a day (versions without a fee keep the last)."""
        fee = self.monthly_fee
        for version in self.versions:
            if version.valid_from > day:
                break
            if version.monthly_fee is not None:
                fee = version.monthly_fee
        return fee

    def due_fees(self, today: date, booked: set[str]) -> list[tuple[str, float]]:
        """(month, amount) of fee months up to today that are not booked yet.

        A month is due from its first day on; the amount is the fee valid on
        that day. Months without a fee (None) are skipped.
        """
        if self.fee_vehicle_id is None or self.fee_start is None:
            return []
        last = min(today, self.fee_end) if self.fee_end else today
        due = []
        for first in _months(self.fee_start, last):
            key = month_of(first)
            fee = self.fee_on(first)
            if key not in booked and fee is not None:
                due.append((key, round_money(fee)))
        return due

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Tariff:
        """Create a tariff from stored data."""
        return cls(
            id=data["id"],
            name=data["name"],
            price_ac=_optional_float(data.get("price_ac")),
            price_dc=_optional_float(data.get("price_dc")),
            zones=tuple(data.get("zones") or ()),
            versions=tuple(
                PriceVersion.from_dict(v) for v in data.get("versions") or ()
            ),
            monthly_fee=_optional_float(data.get("monthly_fee")),
            fee_vehicle_id=data.get("fee_vehicle_id"),
            fee_start=_optional_date(data.get("fee_start")),
            fee_end=_optional_date(data.get("fee_end")),
        )


def _optional_date(value: Any) -> date | None:
    return date.fromisoformat(value) if value else None


@dataclass(frozen=True, slots=True)
class Fee:
    """A booked monthly fee. Immutable snapshot like a session."""

    tariff_id: str
    vehicle_id: str
    month: str  # "YYYY-MM"
    amount: float

    def __post_init__(self) -> None:
        """Normalize and validate."""
        object.__setattr__(self, "amount", float(self.amount))
        _require_non_negative("amount", self.amount)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {
            "tariff_id": self.tariff_id,
            "vehicle_id": self.vehicle_id,
            "month": self.month,
            "amount": self.amount,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fee:
        """Load from storage."""
        return cls(
            tariff_id=data["tariff_id"],
            vehicle_id=data["vehicle_id"],
            month=data["month"],
            amount=data["amount"],
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
    day: date | None = None,
) -> CostResult:
    """Calculate the cost of a session at booking time.

    A given amount (including 0) always wins. Otherwise energy x tariff price
    of the version valid on `day` (the session's day). `None` means "not
    given"; 0 is a valid value. A tariff without a price for the charge type
    is rejected, also with a given amount: the tariff does not offer that
    charge type.
    """
    if charge_type not in CHARGE_TYPES:
        raise ValidationError(f"invalid charge_type: {charge_type}")
    if energy_kwh <= 0:
        raise ValidationError("energy_kwh must be positive")
    _require_non_negative("cost", cost)

    price = tariff.price_for(charge_type, day) if tariff is not None else None

    if cost is not None:
        return CostResult(round_money(cost), price, cost_overridden=True)
    if price is None:
        raise ValidationError("either a cost or a tariff is required")
    return CostResult(round_money(energy_kwh * price), price, cost_overridden=False)


@dataclass(frozen=True, slots=True)
class Summary:
    """Aggregated values over sessions and monthly fees.

    `total_cost` includes the fees; `fee_total` shows them separately.
    """

    total_cost: float
    total_energy_kwh: float
    session_count: int
    fee_total: float = 0.0

    @property
    def average_price_per_kwh(self) -> float | None:
        """Total cost (incl. fees) / total energy, including free sessions."""
        if self.total_energy_kwh == 0:
            return None
        return round(self.total_cost / self.total_energy_kwh, 4)


def summarize(sessions: Iterable[Session], fees: Iterable[Fee] = ()) -> Summary:
    """Sum up cost, energy and count; fees add to the cost only."""
    cost = Decimal(0)
    energy = Decimal(0)
    count = 0
    for session in sessions:
        cost += Decimal(str(session.cost))
        energy += Decimal(str(session.energy_kwh))
        count += 1
    fee_total = sum((Decimal(str(fee.amount)) for fee in fees), Decimal(0))
    return Summary(float(cost + fee_total), float(energy), count, float(fee_total))


@dataclass(frozen=True, slots=True)
class DailyTotals:
    """Energy and session cost per day (fees are not included)."""

    days: list[date]
    energy_kwh: list[float]
    cost: list[float]

    @property
    def total_energy_kwh(self) -> float:
        """Sum over all days."""
        return float(sum(Decimal(str(v)) for v in self.energy_kwh))

    @property
    def total_cost(self) -> float:
        """Sum over all days."""
        return float(sum(Decimal(str(v)) for v in self.cost))


def daily_totals(
    sessions: Iterable[Session],
    last_day: date,
    days: int,
    day_of: Callable[[Session], date],
) -> DailyTotals:
    """Totals per day for `days` days up to `last_day` (inclusive).

    `day_of` gives the (local) day of a session. Monthly fees are not part
    of this: they belong to a month, not to a charging day.
    """
    first_day = last_day - timedelta(days=days - 1)
    span = [first_day + timedelta(days=i) for i in range(days)]
    energy = {day: Decimal(0) for day in span}
    cost = {day: Decimal(0) for day in span}
    for session in sessions:
        day = day_of(session)
        if day in energy:
            energy[day] += Decimal(str(session.energy_kwh))
            cost[day] += Decimal(str(session.cost))
    return DailyTotals(
        days=span,
        energy_kwh=[float(energy[d]) for d in span],
        cost=[float(cost[d]) for d in span],
    )


def month_totals(
    sessions: Iterable[Session], month: str, day_of: Callable[[Session], date]
) -> DailyTotals:
    """Totals per day for all days of a calendar month ("YYYY-MM")."""
    year, number = (int(part) for part in month.split("-"))
    following = date(year + 1, 1, 1) if number == 12 else date(year, number + 1, 1)
    last = following - timedelta(days=1)
    return daily_totals(sessions, last, last.day, day_of)


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
    result = calculate_cost(
        data.energy_kwh, data.charge_type, tariff, data.cost, data.timestamp.date()
    )
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
