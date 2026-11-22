"""Totals and statistics derived from stored sessions and fees.

This module must not import Home Assistant so it can be tested on its own.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal
from typing import Any

from .models import Fee, Session, Tariff, month_of, round_money


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
