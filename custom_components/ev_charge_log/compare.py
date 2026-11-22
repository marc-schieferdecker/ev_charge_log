"""Tariff comparison "what if" (stage 8).

This module must not import Home Assistant so it can be tested on its own.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .const import CHARGE_TYPE_DC
from .models import (
    Fee,
    Session,
    ValidationError,
    month_of,
    month_starts,
    require_non_negative,
    round_money,
    validate_prices,
)

# --- Tariff comparison "what if" (stage 8) ----------------------------------


@dataclass(frozen=True, slots=True)
class WhatIfTariff:
    """A tariff entered for the comparison: prices and a monthly fee.

    At least one price; None is "no price" / "no fee", 0 is valid.
    """

    price_ac: float | None = None
    price_dc: float | None = None
    monthly_fee: float | None = None

    def __post_init__(self) -> None:
        """Validate like a tariff."""
        validate_prices(self.price_ac, self.price_dc)
        require_non_negative("monthly_fee", self.monthly_fee)

    def price(self, charge_type: str) -> float | None:
        """Price per kWh of a charge type; None without one."""
        return self.price_dc if charge_type == CHARGE_TYPE_DC else self.price_ac


@dataclass(frozen=True, slots=True)
class WhatIfResult:
    """Costs of the selected sessions with one entered tariff.

    `missing_count`: sessions of a charge type without price in the tariff;
    they count with their stored cost. `average` is None without energy.
    """

    cost: float
    fees: float
    total: float
    average: float | None
    missing_count: int


@dataclass(frozen=True, slots=True)
class TariffComparison:
    """Comparison of two entered tariffs over the selected sessions.

    `months` counts the calendar months with selected sessions (the months
    the entered monthly fees are charged for). `difference` is B - A
    (negative: B is cheaper). `actual_*` is what was really paid: stored
    costs and the booked fees of all months of the period in the selection.
    """

    months: int
    session_count: int
    energy_kwh: float
    a: WhatIfResult
    b: WhatIfResult
    difference: float
    actual_cost: float
    actual_fees: float
    actual_total: float


def compare_tariffs(
    sessions: Iterable[Session],
    fees: Iterable[Fee],
    start: date,
    end: date,
    day_of: Callable[[Session], date],
    a: WhatIfTariff,
    b: WhatIfTariff,
    vehicle_id: str | None = None,
    tariff_id: str | None = None,
) -> TariffComparison:
    """Recalculate the sessions of a period with two entered tariffs.

    Sessions count on their local day, `start` and `end` inclusive, filtered
    by vehicle and tariff (None: all). Per session energy x price of its
    charge type; without a price the stored cost. The monthly fee counts
    only for calendar months with at least one selected session: a month
    without charging would not need the subscription. Nothing is stored:
    stored sessions stay unchanged.
    """
    if start > end:
        raise ValidationError(
            "start of the comparison is after its end", "compare_range"
        )
    months = {month_of(first) for first in month_starts(start, end)}
    selected = [
        s
        for s in sessions
        if start <= day_of(s) <= end
        and (vehicle_id is None or s.vehicle_id == vehicle_id)
        and (tariff_id is None or s.tariff_id == tariff_id)
    ]
    fee_months = {month_of(day_of(s)) for s in selected}
    energy = sum((Decimal(str(s.energy_kwh)) for s in selected), Decimal(0))
    actual_cost = sum((Decimal(str(s.cost)) for s in selected), Decimal(0))
    actual_fees = sum(
        (
            Decimal(str(f.amount))
            for f in fees
            if f.month in months
            and (vehicle_id is None or f.vehicle_id == vehicle_id)
            and (tariff_id is None or f.tariff_id == tariff_id)
        ),
        Decimal(0),
    )

    def result(tariff: WhatIfTariff) -> WhatIfResult:
        cost = Decimal(0)
        missing = 0
        for session in selected:
            price = tariff.price(session.charge_type)
            if price is None:
                cost += Decimal(str(session.cost))
                missing += 1
            else:
                cost += Decimal(str(session.energy_kwh)) * Decimal(str(price))
        fee = Decimal(str(tariff.monthly_fee or 0)) * len(fee_months)
        total = cost + fee
        return WhatIfResult(
            cost=round_money(float(cost)),
            fees=round_money(float(fee)),
            total=round_money(float(total)),
            average=round(float(total / energy), 4) if energy > 0 else None,
            missing_count=missing,
        )

    result_a, result_b = result(a), result(b)
    return TariffComparison(
        months=len(fee_months),
        session_count=len(selected),
        energy_kwh=float(energy),
        a=result_a,
        b=result_b,
        difference=round_money(result_b.total - result_a.total),
        actual_cost=round_money(float(actual_cost)),
        actual_fees=round_money(float(actual_fees)),
        actual_total=round_money(float(actual_cost + actual_fees)),
    )
