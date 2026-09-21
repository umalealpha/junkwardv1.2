"""commissions/commission_calc.py — the broker commission maths (C6).

Deliberately a pure module with no database and no network: every figure here
is a function of its arguments, so the maths can be proved against Finance's
workbook without a Graphite connection or a logged-in user.

THE RULE (CFO-settled 17-Sep-2026)
----------------------------------
Commission is earned on premium ACTUALLY COLLECTED, not premium written. The
collected amount that RealPay debits from a client is gross — it carries VAT
and the admin fee — so both are stripped before a commission rate is applied:

    net premium = collected / 1.14 / 1.08      (VAT 14%, admin 8%)

Motor and non-motor are split PER POLICY upstream (a policy with a vehicle is
motor, everything else is non-motor — the caller decides this from Graphite),
so this module is handed two already-separated gross amounts, not one amount
and a ratio. Each earns its own rate (motor 12.5%, non-motor 20%). Then:

    commission excl VAT = motor net * motor% + non-motor net * non-motor%
    WHT                 = commission excl VAT * 10%     (per-broker flag)
    VAT on commission   = commission excl VAT * 14%
    current payable     = commission excl VAT - WHT + VAT

Rates are NOT hard-coded here — they arrive as a `Rates` built from the
effective-dated `BrokerCommissionRate` row governing the period, so last
month's payable still recomputes at last month's rate.

Rounding: every money figure is quantised to 2 decimals HALF UP. Rounding is a
tax decision, never a language default (Python's default is banker's rounding,
which would lose a thebe on a .005 and fail a reconciliation to the cent).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

TWOPLACES = Decimal('0.01')


def money(value) -> Decimal:
    """Quantise to 2 decimals, HALF UP (BURS rounding, not banker's)."""
    if value is None:
        value = 0
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Rates:
    """The percentages in force for one period. Percent, not fraction: 12.5."""
    motor_pct: Decimal
    non_motor_pct: Decimal
    vat_pct: Decimal
    admin_pct: Decimal
    wht_pct: Decimal

    @classmethod
    def from_model(cls, row) -> 'Rates':
        return cls(
            motor_pct=Decimal(str(row.motor_pct)),
            non_motor_pct=Decimal(str(row.non_motor_pct)),
            vat_pct=Decimal(str(row.vat_pct)),
            admin_pct=Decimal(str(row.admin_pct)),
            wht_pct=Decimal(str(row.wht_pct)),
        )


def strip_vat_and_admin(gross, rates: Rates) -> Decimal:
    """Gross collected -> net premium, removing VAT then the admin fee.

    Order does not change the result (it is two divisions), but both must
    happen: dividing by 1.14 alone leaves the 8% admin fee inside the base and
    over-pays every broker by roughly 8%.
    """
    gross = Decimal(str(gross or 0))
    vat_divisor = Decimal('1') + (rates.vat_pct / Decimal('100'))
    admin_divisor = Decimal('1') + (rates.admin_pct / Decimal('100'))
    if vat_divisor <= 0 or admin_divisor <= 0:
        raise ValueError('VAT and admin rates must be greater than -100%')
    return gross / vat_divisor / admin_divisor


@dataclass(frozen=True)
class BrokerCommission:
    """One broker's figures for one period. All Decimals, all 2dp."""
    motor_collected: Decimal
    non_motor_collected: Decimal
    collected_gross: Decimal
    motor_net: Decimal
    non_motor_net: Decimal
    net_premium: Decimal
    commission_excl_vat: Decimal
    wht: Decimal
    vat: Decimal
    current_payable: Decimal
    withholding_applied: bool

    def as_dict(self) -> dict:
        return {
            'motor_collected': float(self.motor_collected),
            'non_motor_collected': float(self.non_motor_collected),
            'collected_gross': float(self.collected_gross),
            'motor_net': float(self.motor_net),
            'non_motor_net': float(self.non_motor_net),
            'net_premium': float(self.net_premium),
            'commission_excl_vat': float(self.commission_excl_vat),
            'wht': float(self.wht),
            'vat': float(self.vat),
            'current_payable': float(self.current_payable),
            'withholding_applied': self.withholding_applied,
        }


def compute(motor_collected, non_motor_collected, rates: Rates,
            withholding: bool = True) -> BrokerCommission:
    """Commission for one broker over one window.

    `motor_collected` / `non_motor_collected` are the GROSS amounts already
    split per policy upstream (motor = policy has a vehicle). Each is de-VATed
    and de-admined on its own, then earns its own rate — a motor pula and a
    non-motor pula do not pay the same commission, so they must not be netted
    together first.

    `withholding` is the broker's own flag: an exempt broker's payable is the
    commission plus VAT with nothing deducted.
    """
    motor_gross = Decimal(str(motor_collected or 0))
    non_motor_gross = Decimal(str(non_motor_collected or 0))
    if motor_gross < 0 or non_motor_gross < 0:
        raise ValueError('collected amounts cannot be negative')

    motor_net = strip_vat_and_admin(motor_gross, rates)
    non_motor_net = strip_vat_and_admin(non_motor_gross, rates)

    commission = (motor_net * rates.motor_pct / Decimal('100')
                  + non_motor_net * rates.non_motor_pct / Decimal('100'))
    # Round the commission BEFORE deriving WHT and VAT from it. Deriving both
    # from an unrounded base and rounding each separately lets payable drift a
    # thebe away from commission - WHT + VAT, which is what a reconciliation to
    # the cent catches.
    commission = money(commission)

    wht = money(commission * rates.wht_pct / Decimal('100')) if withholding else money(0)
    vat = money(commission * rates.vat_pct / Decimal('100'))
    payable = money(commission - wht + vat)

    return BrokerCommission(
        motor_collected=money(motor_gross),
        non_motor_collected=money(non_motor_gross),
        collected_gross=money(motor_gross + non_motor_gross),
        motor_net=money(motor_net),
        non_motor_net=money(non_motor_net),
        net_premium=money(motor_net + non_motor_net),
        commission_excl_vat=commission,
        wht=wht,
        vat=vat,
        current_payable=payable,
        withholding_applied=bool(withholding),
    )


def growth_pct(current, previous):
    """Percent change, or None when there is no previous figure to compare to.

    None, not 0 and not 100: a broker with nothing last month has undefined
    growth, and printing 0% would read as "flat" when it means "no history".
    """
    current = Decimal(str(current or 0))
    previous = Decimal(str(previous or 0))
    if previous == 0:
        return None
    return float(money((current - previous) / previous * Decimal('100')))
