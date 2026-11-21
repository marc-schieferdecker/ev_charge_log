"""Data model and pure calculation logic for EV Charge Log.

This module must not import Home Assistant so it can be tested on its own.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .const import CHARGE_TYPE_DC, CHARGE_TYPES, SOURCE_MANUAL, SOURCES


class ValidationError(ValueError):
    """Raised when input data violates the data model rules.

    `key` is the translation key ("error_<key>" in the "exceptions" category
    of the translations), `placeholders` fill its message. The English
    message stays for logs.
    """

    def __init__(self, message: str, key: str, **placeholders: Any) -> None:
        """Create the error."""
        super().__init__(message)
        self.key = f"error_{key}"
        self.placeholders = {name: str(value) for name, value in placeholders.items()}


_NUMBER = re.compile(r"^\d+(?:[.,]\d+)?$")
_REPLY_SEPARATORS = re.compile(r"[\s;/]+")
_REPLY_UNITS = ("kwh", "€", "$", "£", "eur", "usd", "gbp", "chf")


@dataclass(frozen=True, slots=True)
class Reply:
    """A typed reply from a phone notification: kWh and optional cost."""

    energy_kwh: float
    cost: float | None = None


def parse_reply(text: str | None, currency: str | None = None) -> Reply | None:
    """Parse "12,4" or "12,4 7,32" (kWh, optional cost); None if invalid.

    Numbers may use a decimal comma or point. Separators: space, ";" or "/"
    (not ",", that is the decimal comma). Units ("kWh", "€", the currency
    code) are ignored. kWh must be positive, the cost may be 0.
    """
    if text is None:
        return None
    cleaned = text.lower()
    for unit in (*_REPLY_UNITS, *([currency.lower()] if currency else [])):
        cleaned = cleaned.replace(unit, " ")
    parts = [p for p in _REPLY_SEPARATORS.split(cleaned) if p]
    if not 1 <= len(parts) <= 2 or not all(_NUMBER.match(p) for p in parts):
        return None
    energy, *rest = (float(p.replace(",", ".")) for p in parts)
    if energy <= 0:
        return None
    return Reply(energy, rest[0] if rest else None)


def round_money(value: float) -> float:
    """Round a monetary amount to two decimals, half up."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _require_non_negative(name: str, value: float | None) -> None:
    if value is not None and value < 0:
        raise ValidationError(f"{name} must not be negative", f"negative_{name}")


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


@dataclass(frozen=True, slots=True)
class Vehicle:
    """A vehicle.

    SoC and capacity are display values, with one exception: without entered
    energy, hand-entered SoC values and the capacity give the energy
    (`energy_from_soc`).
    """

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
    # Odometer sensor for the cost per 100 km (display value)
    odometer_entity_id: str | None = None
    # Charging loss in % per charge type, added to the energy from SoC
    charging_loss_ac: float | None = None
    charging_loss_dc: float | None = None

    def __post_init__(self) -> None:
        """Validate the vehicle."""
        if self.battery_capacity_kwh is not None and self.battery_capacity_kwh <= 0:
            raise ValidationError("battery_capacity_kwh must be positive", "capacity")
        for loss in (self.charging_loss_ac, self.charging_loss_dc):
            if loss is not None and not 0 <= loss < 100:
                raise ValidationError(
                    "charging loss must be between 0 and 100 %", "charging_loss"
                )

    def charging_loss(self, charge_type: str) -> float:
        """Charging loss in % for the charge type; 0 if not configured."""
        loss = (
            self.charging_loss_dc
            if charge_type == CHARGE_TYPE_DC
            else self.charging_loss_ac
        )
        return loss or 0.0

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
            odometer_entity_id=data.get("odometer_entity_id"),
            charging_loss_ac=_optional_float(data.get("charging_loss_ac")),
            charging_loss_dc=_optional_float(data.get("charging_loss_dc")),
        )


def _validate_prices(price_ac: float | None, price_dc: float | None) -> None:
    if price_ac is None and price_dc is None:
        raise ValidationError(
            "a price version needs an AC or a DC price", "price_required"
        )
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
    # Stage 4: prices without subscription, for the savings in the tariff
    # statistics. None means "the previous reference price still applies".
    reference_price_ac: float | None = None
    reference_price_dc: float | None = None

    def __post_init__(self) -> None:
        """Validate the prices."""
        _validate_prices(self.price_ac, self.price_dc)
        _require_non_negative("monthly_fee", self.monthly_fee)
        _require_non_negative("reference_price_ac", self.reference_price_ac)
        _require_non_negative("reference_price_dc", self.reference_price_dc)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for subentry data."""
        return {
            "valid_from": self.valid_from.isoformat(),
            "price_ac": self.price_ac,
            "price_dc": self.price_dc,
            "monthly_fee": self.monthly_fee,
            "reference_price_ac": self.reference_price_ac,
            "reference_price_dc": self.reference_price_dc,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PriceVersion:
        """Load from subentry data."""
        return cls(
            valid_from=date.fromisoformat(data["valid_from"]),
            price_ac=_optional_float(data.get("price_ac")),
            price_dc=_optional_float(data.get("price_dc")),
            monthly_fee=_optional_float(data.get("monthly_fee")),
            reference_price_ac=_optional_float(data.get("reference_price_ac")),
            reference_price_dc=_optional_float(data.get("reference_price_dc")),
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

    Reference prices (stage 4): price per kWh without subscription, for the
    savings in the tariff statistics. Like the fee, a version without a
    reference price keeps the previous one.
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
    reference_price_ac: float | None = None
    reference_price_dc: float | None = None

    def __post_init__(self) -> None:
        """Validate the tariff and sort the versions by date."""
        _validate_prices(self.price_ac, self.price_dc)
        _require_non_negative("monthly_fee", self.monthly_fee)
        _require_non_negative("reference_price_ac", self.reference_price_ac)
        _require_non_negative("reference_price_dc", self.reference_price_dc)
        if self.fee_start and self.fee_end and self.fee_end < self.fee_start:
            raise ValidationError("fee_end is before fee_start", "fee_end_before_start")
        days = [v.valid_from for v in self.versions]
        if len(days) != len(set(days)):
            raise ValidationError(
                "two price versions with the same date", "duplicate_version"
            )
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
                f"tariff {self.name} has no {charge_type.upper()} price",
                "no_price",
                tariff=self.name,
                charge_type=charge_type.upper(),
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

    @property
    def has_fee(self) -> bool:
        """Whether the tariff has or had a monthly fee (base or any version)."""
        return self.monthly_fee is not None or any(
            v.monthly_fee is not None for v in self.versions
        )

    def reference_price_on(self, charge_type: str, day: date) -> float | None:
        """Reference price of a charge type on a day (versions without one
        keep the last)."""
        name = (
            "reference_price_dc"
            if charge_type == CHARGE_TYPE_DC
            else "reference_price_ac"
        )
        price = getattr(self, name)
        for version in self.versions:
            if version.valid_from > day:
                break
            if getattr(version, name) is not None:
                price = getattr(version, name)
        return price

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
            reference_price_ac=_optional_float(data.get("reference_price_ac")),
            reference_price_dc=_optional_float(data.get("reference_price_dc")),
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
            raise ValidationError("timestamp must be timezone-aware", "timestamp")
        if self.energy_kwh <= 0:
            raise ValidationError("energy_kwh must be positive", "energy")
        if self.charge_type not in CHARGE_TYPES:
            raise ValidationError(
                f"invalid charge_type: {self.charge_type}",
                "charge_type",
                charge_type=self.charge_type,
            )
        if self.source not in SOURCES:
            raise ValidationError(
                f"invalid source: {self.source}", "source", source=self.source
            )
        _require_non_negative("cost", self.cost)
        _require_non_negative("price_per_kwh", self.price_per_kwh)
        for name in ("soc_start", "soc_end"):
            value = getattr(self, name)
            if value is not None and not 0 <= value <= 100:
                raise ValidationError(
                    f"{name} must be between 0 and 100", f"{name}_range"
                )

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
        raise ValidationError(
            f"invalid charge_type: {charge_type}",
            "charge_type",
            charge_type=charge_type,
        )
    if energy_kwh <= 0:
        raise ValidationError("energy_kwh must be positive", "energy")
    _require_non_negative("cost", cost)

    price = tariff.price_for(charge_type, day) if tariff is not None else None

    if cost is not None:
        return CostResult(round_money(cost), price, cost_overridden=True)
    if price is None:
        raise ValidationError("either a cost or a tariff is required", "cost_or_tariff")
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


@dataclass(frozen=True, slots=True)
class OdometerBaseline:
    """Odometer reading from which on cost per distance is calculated."""

    entity_id: str
    start: float  # odometer reading in the HA length unit
    since: datetime

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {
            "entity_id": self.entity_id,
            "start": self.start,
            "since": self.since.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OdometerBaseline:
        """Load from storage."""
        return cls(
            entity_id=data["entity_id"],
            start=float(data["start"]),
            since=datetime.fromisoformat(data["since"]),
        )


MIN_DISTANCE: float = 1.0


def cost_per_100(
    sessions: Iterable[Session], baseline: OdometerBaseline, odometer: float
) -> float | None:
    """Session cost per 100 distance units since the baseline.

    Counts the cost of sessions at or after `baseline.since` (monthly fees
    are not included). None until at least MIN_DISTANCE was driven.
    """
    distance = odometer - baseline.start
    if distance < MIN_DISTANCE:
        return None
    cost = sum(
        (Decimal(str(s.cost)) for s in sessions if s.timestamp >= baseline.since),
        Decimal(0),
    )
    return round(float(cost) / distance * 100, 2)


def energy_per_100(
    sessions: Iterable[Session], baseline: OdometerBaseline, odometer: float
) -> float | None:
    """Charged energy (kWh) per 100 distance units since the baseline.

    Same basis as `cost_per_100`: sessions at or after `baseline.since`,
    None until at least MIN_DISTANCE was driven.
    """
    distance = odometer - baseline.start
    if distance < MIN_DISTANCE:
        return None
    energy = sum(
        (Decimal(str(s.energy_kwh)) for s in sessions if s.timestamp >= baseline.since),
        Decimal(0),
    )
    return round(float(energy) / distance * 100, 2)


@dataclass(frozen=True, slots=True)
class HourlyTotal:
    """Running totals of a vehicle at the end of an hour (stage 5).

    `start` is the beginning of the hour in UTC. Only hours in which a
    session or a monthly fee counts have a row.
    """

    start: datetime
    energy_kwh: float
    cost: float


def _hour_start(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def hourly_statistics(
    sessions: Iterable[Session],
    fees: Iterable[Fee],
    vehicle_id: str,
    tz: tzinfo,
) -> list[HourlyTotal]:
    """Running energy and cost totals of a vehicle, per hour of the charge.

    A session counts in the hour of its timestamp, not when it was booked.
    A monthly fee counts on the first of its month at 00:00 in `tz` (local
    time of HA). The cost includes the fees, so the last row equals the
    vehicle's total cost and total energy.
    """
    energy: dict[datetime, Decimal] = {}
    cost: dict[datetime, Decimal] = {}

    def add(moment: datetime, kwh: Decimal, amount: Decimal) -> None:
        hour = _hour_start(moment)
        energy[hour] = energy.get(hour, Decimal(0)) + kwh
        cost[hour] = cost.get(hour, Decimal(0)) + amount

    for session in sessions:
        if session.vehicle_id == vehicle_id:
            add(
                session.timestamp,
                Decimal(str(session.energy_kwh)),
                Decimal(str(session.cost)),
            )
    for fee in fees:
        if fee.vehicle_id == vehicle_id:
            year, month = (int(part) for part in fee.month.split("-"))
            add(
                datetime(year, month, 1, tzinfo=tz),
                Decimal(0),
                Decimal(str(fee.amount)),
            )

    rows: list[HourlyTotal] = []
    energy_sum = Decimal(0)
    cost_sum = Decimal(0)
    for hour in sorted(energy):
        energy_sum += energy[hour]
        cost_sum += cost[hour]
        rows.append(HourlyTotal(hour, float(energy_sum), float(cost_sum)))
    return rows


def month_totals(
    sessions: Iterable[Session], month: str, day_of: Callable[[Session], date]
) -> DailyTotals:
    """Totals per day for all days of a calendar month ("YYYY-MM")."""
    year, number = (int(part) for part in month.split("-"))
    following = date(year + 1, 1, 1) if number == 12 else date(year, number + 1, 1)
    last = following - timedelta(days=1)
    return daily_totals(sessions, last, last.day, day_of)


@dataclass(frozen=True, slots=True)
class TariffMonthRow:
    """One row of the tariff statistics of a month.

    `tariff_id` None is the row "without tariff" (sessions without a tariff
    or of a deleted tariff, plus fees of deleted tariffs). `fee` is None
    without a booked fee, `saving` is None if there is nothing to show.
    """

    tariff_id: str | None
    name: str | None
    session_count: int
    energy_kwh: float
    cost: float
    fee: float | None
    saving: float | None


@dataclass(frozen=True, slots=True)
class TariffMonthStats:
    """Tariff rows (sorted by name, "without tariff" last) and the total."""

    rows: list[TariffMonthRow]
    total: TariffMonthRow


def _sum_or_none(values: Iterable[Decimal]) -> Decimal | None:
    values = list(values)
    return sum(values, Decimal(0)) if values else None


def tariff_month_stats(
    sessions: Iterable[Session],
    fees: Iterable[Fee],
    tariffs: Mapping[str, Tariff],
    month: str,
    day_of: Callable[[Session], date],
) -> TariffMonthStats:
    """Sessions, energy, cost, fee and savings per tariff in a month ("YYYY-MM").

    Rows: every existing tariff with sessions or a booked fee in the month.
    Savings only for tariffs with a booked fee in the month:
    sum(energy x reference price - stored cost) - fee. The reference price is
    the one valid on the session's day; sessions without a reference price
    for their charge type count as 0. The result may be negative.
    """
    groups: dict[str | None, list[Session]] = {}
    for session in sessions:
        if month_of(day_of(session)) != month:
            continue
        key = session.tariff_id if session.tariff_id in tariffs else None
        groups.setdefault(key, []).append(session)
    group_fees: dict[str | None, list[Decimal]] = {}
    for fee in fees:
        if fee.month != month:
            continue
        key = fee.tariff_id if fee.tariff_id in tariffs else None
        group_fees.setdefault(key, []).append(Decimal(str(fee.amount)))

    def row(tariff_id: str | None) -> TariffMonthRow:
        tariff = tariffs.get(tariff_id) if tariff_id else None
        group = groups.get(tariff_id, [])
        fee = _sum_or_none(group_fees.get(tariff_id, []))
        saving = None
        if tariff is not None and fee is not None:
            saving = -fee
            for session in group:
                reference = tariff.reference_price_on(
                    session.charge_type, day_of(session)
                )
                if reference is not None:
                    saving += Decimal(str(session.energy_kwh)) * Decimal(
                        str(reference)
                    ) - Decimal(str(session.cost))
        return TariffMonthRow(
            tariff_id=tariff_id,
            name=tariff.name if tariff else None,
            session_count=len(group),
            energy_kwh=float(
                sum((Decimal(str(s.energy_kwh)) for s in group), Decimal(0))
            ),
            cost=float(sum((Decimal(str(s.cost)) for s in group), Decimal(0))),
            fee=None if fee is None else float(fee),
            saving=None if saving is None else round_money(float(saving)),
        )

    ids = {key for key in (*groups, *group_fees) if key is not None}
    rows = [row(tariff_id) for tariff_id in ids]
    rows.sort(key=lambda r: ((r.name or "").casefold(), r.tariff_id or ""))
    if None in groups or None in group_fees:
        rows.append(row(None))

    fee_total = _sum_or_none(Decimal(str(r.fee)) for r in rows if r.fee is not None)
    saving_total = _sum_or_none(
        Decimal(str(r.saving)) for r in rows if r.saving is not None
    )
    total = TariffMonthRow(
        tariff_id=None,
        name=None,
        session_count=sum(r.session_count for r in rows),
        energy_kwh=float(sum((Decimal(str(r.energy_kwh)) for r in rows), Decimal(0))),
        cost=float(sum((Decimal(str(r.cost)) for r in rows), Decimal(0))),
        fee=None if fee_total is None else float(fee_total),
        saving=None if saving_total is None else float(saving_total),
    )
    return TariffMonthStats(rows, total)


@dataclass(frozen=True, slots=True)
class SocMetrics:
    """Display-only values derived from SoC."""

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


def energy_from_soc(
    soc_start: float | None,
    soc_end: float | None,
    battery_capacity_kwh: float | None,
    charging_loss_percent: float = 0.0,
) -> float:
    """Charged energy: (end - start) / 100 x capacity x (1 + loss / 100).

    Used only when no energy was entered. The charging loss (of the vehicle
    for the charge type, 0 if not configured) is added to the energy that
    reached the battery. Rounded once, to 0.01 kWh.
    """
    if soc_start is None or soc_end is None:
        raise ValidationError(
            "energy_kwh or soc_start and soc_end are required", "energy_required"
        )
    if not battery_capacity_kwh or battery_capacity_kwh <= 0:
        raise ValidationError(
            "the vehicle has no battery capacity", "energy_soc_capacity"
        )
    for name, value in (("soc_start", soc_start), ("soc_end", soc_end)):
        if not 0 <= value <= 100:
            raise ValidationError(f"{name} must be between 0 and 100", f"{name}_range")
    energy = (
        (Decimal(str(soc_end)) - Decimal(str(soc_start)))
        / Decimal(100)
        * Decimal(str(battery_capacity_kwh))
        * (1 + Decimal(str(charging_loss_percent)) / Decimal(100))
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if energy <= 0:
        raise ValidationError(
            "soc_end must be greater than soc_start", "energy_soc_order"
        )
    return float(energy)


@dataclass(slots=True)
class SessionInput:
    """Raw input for booking a session, before cost calculation.

    `energy_kwh` None: the coordinator calculates it from the entered SoC
    (`energy_from_soc`) before booking.
    """

    vehicle_id: str
    energy_kwh: float | None
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
        raise ValidationError(
            f"unknown tariff: {data.tariff_id}", "unknown_tariff", tariff=data.tariff_id
        )
    if data.energy_kwh is None:
        raise ValidationError("energy_kwh is required", "energy_required")
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


# --- CSV export (stage 6) ---------------------------------------------------

# Column keys of the export, in this order; the header texts come from the
# translations ("panel_export_col_<key>").
EXPORT_COLUMNS = (
    "type",
    "date",
    "time",
    "energy",
    "charge_type",
    "tariff",
    "price",
    "cost",
    "cost_overridden",
    "soc_start",
    "soc_end",
    "location",
    "note",
    "source",
)
# Value texts of the export ("panel_export_<key>").
EXPORT_VALUES = (
    "type_session",
    "type_fee",
    "yes",
    "no",
    "source_manual",
    "source_reminder",
)
# Excel runs cells starting with these as formulas.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_text(value: str | None) -> str:
    """Free text for a CSV cell; formulas are defused with a leading quote."""
    if not value:
        return ""
    return f"'{value}" if value.startswith(_FORMULA_START) else value


def _csv_number(value: float | None, decimal: str) -> str:
    """A number without thousands separator; None is an empty cell, 0 is "0"."""
    if value is None:
        return ""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text.replace(".", decimal)


def _csv_money(value: float, decimal: str) -> str:
    return f"{round_money(value):.2f}".replace(".", decimal)


def export_csv(
    sessions: Iterable[Session],
    fees: Iterable[Fee],
    tariff_names: Mapping[str, str],
    start: date | None,
    end: date | None,
    to_local: Callable[[datetime], datetime],
    texts: Mapping[str, str],
    decimal: str = ".",
) -> str:
    """Sessions and booked monthly fees as CSV for Excel, oldest first.

    A session counts on its local day, a fee on the first of its month;
    `start` and `end` are inclusive, None is open. `texts` holds the header
    texts (`EXPORT_COLUMNS`) and value texts (`EXPORT_VALUES`). With a decimal
    comma the separator is ";", else ",". Starts with a BOM so Excel reads
    UTF-8. Tariffs not in `tariff_names` (deleted) give an empty name.
    """
    if start is not None and end is not None and start > end:
        raise ValidationError("start of the export is after its end", "export_range")

    def inside(day: date) -> bool:
        return (start is None or day >= start) and (end is None or day <= end)

    def tariff_name(tariff_id: str | None) -> str:
        return _csv_text(tariff_names.get(tariff_id, "")) if tariff_id else ""

    rows: list[tuple[tuple[date, int, str], list[str]]] = []
    for fee in fees:
        day = date.fromisoformat(f"{fee.month}-01")
        if not inside(day):
            continue
        row = dict.fromkeys(EXPORT_COLUMNS, "")
        row["type"] = texts["type_fee"]
        row["date"] = day.isoformat()
        row["tariff"] = tariff_name(fee.tariff_id)
        row["cost"] = _csv_money(fee.amount, decimal)
        # A fee counts at 00:00 on the first, before sessions of that day.
        rows.append(((day, 0, ""), list(row.values())))
    for session in sessions:
        local = to_local(session.timestamp)
        day = local.date()
        if not inside(day):
            continue
        clock = local.strftime("%H:%M")
        rows.append(
            (
                (day, 1, local.strftime("%H:%M:%S.%f")),
                [
                    texts["type_session"],
                    day.isoformat(),
                    clock,
                    _csv_number(session.energy_kwh, decimal),
                    session.charge_type.upper(),
                    tariff_name(session.tariff_id),
                    _csv_number(session.price_per_kwh, decimal),
                    _csv_money(session.cost, decimal),
                    texts["yes"] if session.cost_overridden else texts["no"],
                    _csv_number(session.soc_start, decimal),
                    _csv_number(session.soc_end, decimal),
                    _csv_text(session.location),
                    _csv_text(session.note),
                    texts[f"source_{session.source}"],
                ],
            )
        )
    # Stable sort keeps the booking order of sessions at the same time.
    rows.sort(key=lambda item: item[0])

    buffer = io.StringIO()
    writer = csv.writer(
        buffer, delimiter=";" if decimal == "," else ",", lineterminator="\r\n"
    )
    writer.writerow([texts[key] for key in EXPORT_COLUMNS])
    writer.writerows(cells for _, cells in rows)
    return "﻿" + buffer.getvalue()


# --- CSV import (stage 7) ---------------------------------------------------

# Limits of one import file (characters and data rows).
IMPORT_MAX_CHARS = 1_000_000
IMPORT_MAX_ROWS = 5000
# Columns an import file needs; the energy may come from the SoC instead.
_IMPORT_REQUIRED = ("date", "charge_type")
_IMPORT_ENERGY = ("energy", "soc_start", "soc_end")
# Time of a row without a time.
_IMPORT_NOON = time(12, 0)
_BRACKETS = re.compile(r"\s*\([^)]*\)\s*$")
_IMPORT_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
_IMPORT_DATE_DOTTED = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_IMPORT_TIME = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2}(?:\.\d+)?)?$")


@dataclass(frozen=True, slots=True)
class ImportRow:
    """One session row of an import file, parsed but not booked.

    `row` is the row number as a spreadsheet shows it (header = 1).
    `timestamp` is aware, in the time zone of the import.
    """

    row: int
    timestamp: datetime
    energy_kwh: float | None
    charge_type: str
    tariff_name: str | None = None
    price_per_kwh: float | None = None
    cost: float | None = None
    cost_overridden: bool | None = None
    soc_start: float | None = None
    soc_end: float | None = None
    location: str | None = None
    note: str | None = None
    source: str = SOURCE_MANUAL


@dataclass(frozen=True, slots=True)
class ImportProblem:
    """A row that was not imported, with the translation key of the reason."""

    row: int
    key: str
    placeholders: dict[str, str]


@dataclass(slots=True)
class ParsedImport:
    """Result of reading an import file."""

    rows: list[ImportRow]
    fees_skipped: int = 0
    problems: list[ImportProblem] = field(default_factory=list)


@dataclass(slots=True)
class ImportResult:
    """Result of booking the rows of an import file."""

    sessions: list[Session]
    duplicates: int = 0
    problems: list[ImportProblem] = field(default_factory=list)


def _header_key(text: str) -> str:
    """Header text without unit in brackets, for comparing ("Kosten (EUR)")."""
    return _BRACKETS.sub("", text.strip()).strip().casefold()


def _import_text(value: str) -> str | None:
    """Free text of a cell; the export's formula protection is removed."""
    value = value.strip()
    if value.startswith("'") and value[1:2] and value[1] in "=+-@\t\r":
        value = value[1:]
    return value or None


def _import_value_error(column: str, value: str) -> ValidationError:
    return ValidationError(
        f"invalid value {value!r} in column {column}",
        "import_value",
        column=column,
        value=value,
    )


def _import_number(value: str, column: str, comma: bool) -> float | None:
    text = value.strip()
    if not text:
        return None
    if comma:
        text = text.replace(",", ".")
    if not _IMPORT_NUMBER.match(text):
        raise _import_value_error(column, value)
    return float(text)


def _import_date(value: str, column: str) -> date:
    text = value.strip()
    dotted = _IMPORT_DATE_DOTTED.match(text)
    try:
        if dotted:
            day, month, year = (int(part) for part in dotted.groups())
            return date(year, month, day)
        return date.fromisoformat(text)
    except ValueError:
        raise _import_value_error(column, value) from None


def _import_time(value: str, column: str) -> time:
    text = value.strip()
    if not text:
        return _IMPORT_NOON
    match = _IMPORT_TIME.match(text)
    try:
        if match is None:
            raise ValueError
        return time(int(match[1]), int(match[2]))
    except ValueError:
        raise _import_value_error(column, value) from None


def parse_import_csv(
    text: str, texts: Iterable[Mapping[str, str]], tz: tzinfo
) -> ParsedImport:
    """Read a CSV file in the format of `export_csv` (any of its languages).

    `texts` holds the header and value texts of each language, like the
    `texts` of `export_csv`. Columns are found by their header text (without
    the unit in brackets, case-insensitive) or their key, in any order;
    unknown columns are ignored. With ";" as separator a decimal comma is
    allowed. Dates as ISO or "D.M.YYYY", a missing time is 12:00 in `tz`.
    Monthly fee rows are skipped (fees are booked automatically). A broken
    file raises `ValidationError`; broken rows become problems.
    """
    if len(text) > IMPORT_MAX_CHARS:
        raise ValidationError("the import file is too large", "import_too_large")
    text = text.removeprefix("﻿")
    first_line = text.split("\n", 1)[0]
    delimiter = ";" if first_line.count(";") >= first_line.count(",") else ","
    comma = delimiter == ";"
    records = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if len(records) - 1 > IMPORT_MAX_ROWS:
        raise ValidationError("the import file is too large", "import_too_large")

    texts = list(texts)
    names: dict[str, str] = {}
    for language in texts:
        for key in EXPORT_COLUMNS:
            names.setdefault(_header_key(language.get(key, key)), key)
    for key in EXPORT_COLUMNS:
        names.setdefault(key, key)
        names.setdefault(key.replace("_", " "), key)

    header = records[0] if records else []
    columns: dict[str, int] = {}
    captions: dict[str, str] = {}
    for index, caption in enumerate(header):
        key = names.get(_header_key(caption.removeprefix("﻿")))
        if key is not None and key not in columns:
            columns[key] = index
            captions[key] = caption.strip()
    if not all(key in columns for key in _IMPORT_REQUIRED) or not (
        "energy" in columns or {"soc_start", "soc_end"} <= columns.keys()
    ):
        raise ValidationError(
            "the import file has no date, charge type or energy column",
            "import_header",
        )

    def values(*keys: str) -> set[str]:
        return {
            language[key].strip().casefold()
            for language in texts
            for key in keys
            if language.get(key)
        }

    fee_texts = values("type_fee")
    yes_texts = values("yes") | {"yes", "true", "1"}
    no_texts = values("no") | {"no", "false", "0"}
    sources = {SOURCE_MANUAL: SOURCE_MANUAL, "reminder": "reminder"}
    for name in SOURCES:
        sources |= dict.fromkeys(values(f"source_{name}"), name)

    result = ParsedImport(rows=[])
    for number, record in enumerate(records[1:], start=2):
        if not any(cell.strip() for cell in record):
            continue

        def cell(key: str, record: list[str] = record) -> str:
            index = columns.get(key)
            return record[index] if index is not None and index < len(record) else ""

        if cell("type").strip().casefold() in fee_texts:
            result.fees_skipped += 1
            continue
        try:
            numbers = {
                key: _import_number(cell(key), captions.get(key, key), comma)
                for key in ("energy", "price", "cost", "soc_start", "soc_end")
            }
            day = _import_date(cell("date"), captions["date"])
            moment = _import_time(cell("time"), captions.get("time", "time"))
            charge_type = cell("charge_type").strip().lower()
            if charge_type not in CHARGE_TYPES:
                raise ValidationError(
                    f"invalid charge_type: {charge_type}",
                    "charge_type",
                    charge_type=cell("charge_type").strip(),
                )
            overridden_text = cell("cost_overridden").strip().casefold()
            if not overridden_text:
                overridden = None
            elif overridden_text in yes_texts:
                overridden = True
            elif overridden_text in no_texts:
                overridden = False
            else:
                raise _import_value_error(
                    captions["cost_overridden"], cell("cost_overridden")
                )
        except ValidationError as err:
            result.problems.append(ImportProblem(number, err.key, err.placeholders))
            continue
        result.rows.append(
            ImportRow(
                row=number,
                timestamp=datetime.combine(day, moment, tzinfo=tz),
                energy_kwh=numbers["energy"],
                charge_type=charge_type,
                tariff_name=_import_text(cell("tariff")),
                price_per_kwh=numbers["price"],
                cost=numbers["cost"],
                cost_overridden=overridden,
                soc_start=numbers["soc_start"],
                soc_end=numbers["soc_end"],
                location=_import_text(cell("location")),
                note=_import_text(cell("note")),
                source=sources.get(cell("source").strip().casefold(), SOURCE_MANUAL),
            )
        )
    return result


def duplicate_key(timestamp: datetime, energy_kwh: float) -> tuple[str, float]:
    """Sessions with the same local minute and energy count as the same."""
    return timestamp.strftime("%Y-%m-%dT%H:%M"), round(energy_kwh, 2)


def build_imported_session(
    session_id: str,
    vehicle: Vehicle,
    row: ImportRow,
    tariff: Tariff | None,
) -> Session:
    """Book an imported row: the cost of the file is the snapshot.

    Without cost in the row, it is calculated from the tariff like a
    booking. A tariff without a price for the charge type rejects the row,
    also with a cost. Without energy, it comes from the row's SoC, the
    battery capacity and the charging loss (`energy_from_soc`).
    """
    energy = row.energy_kwh
    if energy is None:
        energy = energy_from_soc(
            row.soc_start,
            row.soc_end,
            vehicle.battery_capacity_kwh,
            vehicle.charging_loss(row.charge_type),
        )
    day = row.timestamp.date()
    result = calculate_cost(energy, row.charge_type, tariff, row.cost, day)
    if row.cost is None:
        price, overridden = result.price_per_kwh, False
    else:
        price = row.price_per_kwh
        overridden = True if row.cost_overridden is None else row.cost_overridden
    return Session(
        id=session_id,
        vehicle_id=vehicle.id,
        timestamp=row.timestamp,
        energy_kwh=energy,
        tariff_id=tariff.id if tariff is not None else None,
        charge_type=row.charge_type,
        price_per_kwh=price,
        cost=result.cost,
        cost_overridden=overridden,
        soc_start=row.soc_start,
        soc_end=row.soc_end,
        location=row.location,
        note=row.note,
        source=row.source,
    )
