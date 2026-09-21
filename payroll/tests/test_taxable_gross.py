"""Anti-drift guard for payroll.paye.monthly_taxable_gross.

`monthly_taxable_gross` re-states the taxable-base rule that lives inside
`Payslip.recompute_totals`. Two implementations of a tax rule is exactly the kind
of thing that silently drifts, so these tests pin them together: for the same
payslip, `calculate_monthly_paye(monthly_taxable_gross(ps))` must equal the PAYE
that `recompute_totals(recompute_paye=True)` puts on the slip.

If someone changes the line-kind handling in recompute_totals and not here (or
vice versa), these fail.
"""
import datetime as dt
from decimal import Decimal

from django.test import TestCase

from core.models import Company
from payroll.models import (
    Employee, PayrollPeriod, Payslip, PayslipComponent, PayslipLine, TaxBracket,
)
from payroll.paye import (
    active_brackets_from_db, calculate_monthly_paye, marginal_tax_on_extra,
    monthly_taxable_gross,
)

# The schedule in force from 1 Jul 2026 — Income Tax Act 2026 added the 27.5 %
# band above P400,000. Kept literal here so a bad seed can't make a test pass.
BANDS_2026 = [
    (Decimal('0'),      Decimal('48000'),  Decimal('0'),     Decimal('0')),
    (Decimal('48000'),  Decimal('84000'),  Decimal('0'),     Decimal('5')),
    (Decimal('84000'),  Decimal('120000'), Decimal('1800'),  Decimal('12.5')),
    (Decimal('120000'), Decimal('156000'), Decimal('6300'),  Decimal('18.75')),
    (Decimal('156000'), Decimal('400000'), Decimal('13050'), Decimal('25')),
    (Decimal('400000'), None,              Decimal('74050'), Decimal('27.5')),
]


def seed_bands_2026():
    TaxBracket.objects.all().update(is_active=False)
    for lo, hi, base, rate in BANDS_2026:
        TaxBracket.objects.create(
            name='test 2026-27', lower_bound=lo, upper_bound=hi,
            base_amount=base, rate_pct=rate, is_active=True,
            effective_from=dt.date(2026, 7, 1),
        )


class TaxableGrossTests(TestCase):
    """monthly_taxable_gross must agree with recompute_totals' own PAYE base."""

    def setUp(self):
        seed_bands_2026()
        self.company = Company.objects.create(code='TGT', name='Taxable Gross Test Co.')
        self.period = PayrollPeriod.objects.create(
            period_name='2099-08',
            start_date=dt.date(2099, 8, 1), end_date=dt.date(2099, 8, 31),
        )
        self.employee = Employee.objects.create(
            employee_number='ETG1', full_name='Taxable Gross Person',
            company=self.company, status='active',
        )
        self.basic = PayslipComponent.objects.create(
            code='BASIC', name='Basic Salary',
            kind=PayslipComponent.Kind.EARNING, is_taxable=True)
        self.nontax = PayslipComponent.objects.create(
            code='RELOC', name='Relocation',
            kind=PayslipComponent.Kind.EARNING_NON_TAXABLE)
        self.pretax = PayslipComponent.objects.create(
            code='PENEE', name='Pension EE',
            kind=PayslipComponent.Kind.EMPLOYEE_PRETAX)
        self.posttax = PayslipComponent.objects.create(
            code='MEDEE', name='Medical EE',
            kind=PayslipComponent.Kind.EMPLOYEE_DEDUCTION)

    def _slip(self, lines):
        # Payslip is unique per (employee, period), so each slip needs its own
        # period — several are built inside a single test.
        self._n = getattr(self, '_n', 0) + 1
        period = PayrollPeriod.objects.create(
            period_name=f'2099-{self._n:02d}',
            start_date=dt.date(2099, self._n, 1), end_date=dt.date(2099, self._n, 28),
        )
        ps = Payslip.objects.create(
            employee=self.employee, period=period, company=self.company,
            status=Payslip.Status.DRAFT)
        for comp, amt in lines:
            PayslipLine.objects.create(payslip=ps, component=comp, amount=amt)
        return ps

    def test_earnings_only(self):
        ps = self._slip([(self.basic, Decimal('20000'))])
        self.assertEqual(monthly_taxable_gross(ps), Decimal('20000.00'))

    def test_non_taxable_earning_excluded(self):
        ps = self._slip([(self.basic, Decimal('20000')),
                         (self.nontax, Decimal('5000'))])
        self.assertEqual(monthly_taxable_gross(ps), Decimal('20000.00'))

    def test_pretax_reduces_base_regardless_of_stored_sign(self):
        """The importer stores deductions NEGATIVE (2026-07-23 sign bug) — the
        magnitude must reduce the base either way, never add to it."""
        positive = self._slip([(self.basic, Decimal('20000')),
                               (self.pretax, Decimal('1000'))])
        negative = self._slip([(self.basic, Decimal('20000')),
                               (self.pretax, Decimal('-1000'))])
        self.assertEqual(monthly_taxable_gross(positive), Decimal('19000.00'))
        self.assertEqual(monthly_taxable_gross(negative), Decimal('19000.00'))

    def test_post_tax_deduction_does_not_touch_base(self):
        ps = self._slip([(self.basic, Decimal('20000')),
                         (self.posttax, Decimal('-800'))])
        self.assertEqual(monthly_taxable_gross(ps), Decimal('20000.00'))

    def test_agrees_with_recompute_totals_paye(self):
        """THE anti-drift assertion: same base ⇒ same PAYE as the payroll engine."""
        for lines in (
            [(self.basic, Decimal('8000'))],
            [(self.basic, Decimal('20000')), (self.nontax, Decimal('3000'))],
            [(self.basic, Decimal('70000')), (self.pretax, Decimal('-2500'))],
            [(self.basic, Decimal('35000')), (self.posttax, Decimal('-900'))],
        ):
            with self.subTest(lines=lines):
                ps = self._slip(lines)
                ps.recompute_totals(recompute_paye=True)
                ours = calculate_monthly_paye(
                    monthly_taxable_gross(ps), active_brackets_from_db())
                self.assertEqual(ps.paye_amount, ours)


class MarginalTaxTests(TestCase):
    """marginal_tax_on_extra — the rule used for a leave cash-out or a bonus."""

    def setUp(self):
        seed_bands_2026()
        self.b = active_brackets_from_db()

    def test_top_earner_pays_new_275_rate(self):
        """P70,000/month = P840,000/year — deep in the 27.5 % band, so the whole
        extra is taxed at 27.5 %."""
        tax = marginal_tax_on_extra(Decimal('70000'), Decimal('43750.05'), self.b)
        self.assertEqual(tax, Decimal('12031.26'))

    def test_junior_pays_their_own_low_rate_not_the_top_rate(self):
        """The CFO's reason for rejecting a flat rate: a junior on P8,000/month
        (P96,000/year) sits in the 12.5 % band, and P100,000 is still inside it,
        so the whole extra is charged at 12.5 % — not the top 27.5 %."""
        tax = marginal_tax_on_extra(Decimal('8000'), Decimal('4000'), self.b)
        self.assertEqual(tax, Decimal('500.00'))          # 12.5 % of 4,000
        self.assertLess(tax, Decimal('4000') * Decimal('0.275'))

    def test_zero_rated_employee_pays_no_tax(self):
        """P3,000/month = P36,000/year — under the P48,000 threshold. A flat rate
        would have taxed this person; the marginal rule correctly charges nil."""
        self.assertEqual(
            marginal_tax_on_extra(Decimal('3000'), Decimal('1000'), self.b),
            Decimal('0.00'))

    def test_payout_straddling_a_band_boundary_is_split_correctly(self):
        """Base P3,800/month = P45,600/yr, just under the P48,000 tax-free
        threshold. A P5,000 payout takes annual income to P50,600, so only the
        P2,600 above the threshold is taxable, at 5 % → P130. A single-rate
        lookup would charge 5 % on the whole P5,000 (P250)."""
        tax = marginal_tax_on_extra(Decimal('3800'), Decimal('5000'), self.b)
        self.assertEqual(tax, Decimal('130.00'))
        self.assertLess(tax, Decimal('5000') * Decimal('0.05'))

    def test_one_off_payout_is_not_annualised(self):
        """A single payment must NOT be treated as recurring. P3,000/month is
        P36,000/yr — below the P48,000 threshold — so a P1,500 cash-out is
        tax-free. Annualising the extra (12 × 1,500) would wrongly cross the
        threshold and charge tax."""
        self.assertEqual(
            marginal_tax_on_extra(Decimal('3000'), Decimal('1500'), self.b),
            Decimal('0.00'))

    def test_new_band_actually_bites(self):
        """Proof the 27.5 % band is doing something: the same extra costs more
        than it would under the old 25 %-forever schedule."""
        old = [b for b in BANDS_2026[:5]]
        old[-1] = (Decimal('156000'), None, Decimal('13050'), Decimal('25'))
        under_new = marginal_tax_on_extra(Decimal('70000'), Decimal('43750.05'), self.b)
        under_old = marginal_tax_on_extra(Decimal('70000'), Decimal('43750.05'), old)
        self.assertEqual(under_old, Decimal('10937.51'))
        self.assertEqual(under_new - under_old, Decimal('1093.75'))

    def test_no_brackets_means_no_tax_not_a_crash(self):
        TaxBracket.objects.all().update(is_active=False)
        self.assertEqual(
            marginal_tax_on_extra(Decimal('70000'), Decimal('5000'),
                                  active_brackets_from_db()),
            Decimal('0.00'))

    def test_never_negative_and_zero_for_zero_extra(self):
        self.assertEqual(marginal_tax_on_extra(Decimal('70000'), Decimal('0'), self.b),
                         Decimal('0.00'))
        self.assertEqual(marginal_tax_on_extra(Decimal('70000'), Decimal('-100'), self.b),
                         Decimal('0.00'))
        self.assertEqual(marginal_tax_on_extra(Decimal('-500'), Decimal('1000'), self.b),
                         Decimal('0.00'))
