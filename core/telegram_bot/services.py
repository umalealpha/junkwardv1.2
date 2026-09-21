"""
core/telegram_bot/services.py

Read-only query handlers for the Telegram CFO bot.

Each function returns a Markdown-formatted string ready to send to
Telegram. No mutations — the bot cannot post, approve, pay, or write
anything. Every figure is BWP-functional unless the data is naturally
in another currency (foreign-currency bank accounts).

Conventions:
  - Revenue: account_type='revenue', net = SUM(credit_bwp) - SUM(debit_bwp)
  - Expense: account_type='expense', net = SUM(debit_bwp)  - SUM(credit_bwp)
  - Only POSTED journal entries are counted (drafts and pending-approval
    do not exist for accounting purposes).
  - All Decimal arithmetic; never float.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from django.db.models import Count, Sum
from django.utils import timezone


ZERO = Decimal('0.00')


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _fmt_bwp(amount: Optional[Decimal]) -> str:
    """Format as 'P 1,234.56' — accountant convention parentheses for negatives."""
    if amount is None:
        amount = ZERO
    if amount < 0:
        return f"(P {abs(amount):,.2f})"
    return f"P {amount:,.2f}"


def _fmt_ccy(code: str, amount: Optional[Decimal]) -> str:
    if amount is None:
        amount = ZERO
    if amount < 0:
        return f"({code} {abs(amount):,.2f})"
    return f"{code} {amount:,.2f}"


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------

def _fy_window(anchor: date) -> tuple[date, date, str]:
    """Botswana fiscal year (July-June) that contains *anchor*.

    Returns (start_date, end_date, label).
    """
    if anchor.month >= 7:
        return (date(anchor.year, 7, 1),
                date(anchor.year + 1, 6, 30),
                f"FY {anchor.year}-{anchor.year + 1}")
    return (date(anchor.year - 1, 7, 1),
            date(anchor.year, 6, 30),
            f"FY {anchor.year - 1}-{anchor.year}")


def _latest_posted_date() -> Optional[date]:
    """Most recent entry_date across all posted journal entries, or None."""
    from ledger.models import JournalEntry
    return (JournalEntry.objects
            .filter(status='posted')
            .order_by('-entry_date')
            .values_list('entry_date', flat=True)
            .first())


# ---------------------------------------------------------------------------
# Revenue
# ---------------------------------------------------------------------------

def revenue_last_n_months(n: int = 6) -> str:
    """Monthly revenue series.

    If recent months have no data (e.g. only a year-end snapshot is loaded),
    anchors on the latest month with data instead of today, and prepends a
    "Showing latest data..." note. Falls back to FY summary if everything is
    in a single month (typical for an imported closing trial balance).
    """
    from ledger.models import JournalEntryLine

    n = max(1, min(int(n or 6), 24))
    today = timezone.localdate()

    base = JournalEntryLine.objects.filter(
        journal_entry__status='posted',
        account__account_type='revenue',
    )

    # Anchor: today if data exists in the last n months, else latest data date
    latest = _latest_posted_date()
    if latest is None:
        return "No revenue data on file."

    if (today - latest).days > 31 * n:
        anchor = latest
        note = f"\n_Latest data: {latest:%b %Y}. Bot anchored at year-end snapshot._"
    else:
        anchor = today
        note = ""

    months = []
    y, m = anchor.year, anchor.month
    for _ in range(n):
        months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months.reverse()

    monthly_totals = []
    grand = ZERO
    for (yy, mm) in months:
        start = date(yy, mm, 1)
        end = date(yy, 12, 31) if mm == 12 else date(yy, mm + 1, 1) - timedelta(days=1)
        agg = base.filter(
            journal_entry__entry_date__gte=start,
            journal_entry__entry_date__lte=end,
        ).aggregate(c=Sum('credit_bwp'), d=Sum('debit_bwp'))
        net = (agg['c'] or ZERO) - (agg['d'] or ZERO)
        monthly_totals.append((yy, mm, net))
        grand += net

    # If everything sits in one month, switch to FY-by-category summary
    non_zero_months = [t for t in monthly_totals if t[2] != ZERO]
    if len(non_zero_months) <= 1 and grand != ZERO:
        return revenue_fy_summary(anchor)

    out = [f"*Revenue — last {n} month(s)*{note}", ""]
    for yy, mm, net in monthly_totals:
        out.append(f"`{yy}-{mm:02d}`  {_fmt_bwp(net)}")
    out.append("")
    out.append(f"*Total:* {_fmt_bwp(grand)}")
    return "\n".join(out)


def revenue_fy_summary(anchor: Optional[date] = None) -> str:
    """Insurance Revenue — *Gross Written Premium only* (100xxx accounts).

    In insurance accounting, REVENUE means premium written for the year.
    Reinsurance recoveries, RI commission, subrogations, salvages, interest
    income, FX gains — these are SEPARATE income categories, not revenue.
    Use `insurance_pnl` for the full income statement with all categories
    broken out.
    """
    from ledger.models import JournalEntryLine

    if anchor is None:
        anchor = _latest_posted_date() or timezone.localdate()

    fy_start, fy_end, fy_label = _fy_window(anchor)

    rows = (JournalEntryLine.objects
            .filter(journal_entry__status='posted',
                    journal_entry__entry_date__gte=fy_start,
                    journal_entry__entry_date__lte=fy_end,
                    account__code__startswith='100')
            .exclude(account__code='100011')         # discounts → Cost of Revenue
            .values('account__code', 'account__name')
            .annotate(net_c=Sum('credit_bwp'), net_d=Sum('debit_bwp'))
            .order_by('account__code'))

    out = [f"*Revenue — Gross Written Premium*", f"_{fy_label}_", ""]
    grand = ZERO
    for r in rows:
        net = (r['net_c'] or ZERO) - (r['net_d'] or ZERO)
        if net == ZERO:
            continue
        grand += net
        name = (r['account__name'] or '').replace('[Legacy] ', '')[:40]
        out.append(f"`{r['account__code']}`  {name:40s}  {_fmt_bwp(net)}")

    # Note the discount that lives in COR, not revenue
    disc_agg = (JournalEntryLine.objects
                .filter(journal_entry__status='posted',
                        journal_entry__entry_date__gte=fy_start,
                        journal_entry__entry_date__lte=fy_end,
                        account__code='100011')
                .aggregate(d=Sum('debit_bwp'), c=Sum('credit_bwp')))
    disc = (disc_agg['d'] or ZERO) - (disc_agg['c'] or ZERO)

    out.append("")
    out.append(f"*Total Gross Written Premium:* {_fmt_bwp(grand)}")
    if disc != ZERO:
        out.append("")
        out.append(f"_Note: 100011 Discounts Issued (P {disc:,.2f}) is presented "
                   f"under Cost of Revenue, not contra-revenue._")
    out.append("")
    out.append("_Reinsurance recoveries, RI commission, subrogations, interest "
               "and FX are SEPARATE income categories — not revenue in "
               "insurance accounting. Type `pnl` for the full insurance "
               "income statement._")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Full insurance P&L — matches the CFO's spreadsheet structure
# ---------------------------------------------------------------------------

def _classify_pnl_bucket(code: str) -> str:
    """Map a P&L account code to one of {op_income, cor, other_income, expense}.

    Mirrors the line groupings in the CFO's profit_and_loss.xlsx.
    """
    # ── Specific code overrides (must come before prefix matches) ──
    if code == '100011':  return 'cor'                     # Discounts Issued
    if code == '124005':  return 'op_income'               # PPE disposal under Operating
    if code == '102002':  return 'op_income'               # Change in RI UPR (was legacy 84)
    if code == '104018':  return 'other_income'            # RI share of IBNR (was legacy 147)
    if code == '105002':  return 'other_income'            # Subrogation (vs Salvages)
    if code in ('105003', '105004'):  return 'op_income'   # Salvages — operating
    if code in ('104012','104013','104014','104016','104017'): return 'other_income'
    if code in ('118008','118745','119002'):  return 'expense'  # specific exp
    if code == '122001':  return 'expense'                 # Depreciation - PPE

    # ── Prefix rules ──
    p = code[:3]
    if p == '100':  return 'op_income'      # Gross Written Premium
    if p == '101':  return 'cor'            # Premium Ceded to RI
    if p == '102':  return 'op_income'      # Change in UPR
    if p == '103':  return 'cor'            # Gross Insurance Claims
    if p == '104':  return 'other_income'   # RI Recovery on Claims
    if p == '106':  return 'op_income'      # RI Commission Received
    if p == '109':  return 'other_income'   # Forex Income
    if p == '123':  return 'other_income'   # Interest Income
    if p == '124':  return 'other_income'   # Other Income
    if p in ('107','110','111','112','113','114','115','116','117','118',
             '119','120','121','122'):
        return 'expense'
    return 'expense'                        # safe default


def _pnl_buckets(anchor: Optional[date] = None) -> dict:
    """Return {op_income, cor, other_income, expense, gross_profit, net_profit}
    in BWP for the fiscal year containing *anchor*. Re-usable by both
    `insurance_pnl` and `key_metrics`."""
    from ledger.models import JournalEntryLine

    if anchor is None:
        anchor = _latest_posted_date() or timezone.localdate()
    fy_start, fy_end, fy_label = _fy_window(anchor)

    lines = (JournalEntryLine.objects
             .filter(journal_entry__status='posted',
                     journal_entry__entry_date__gte=fy_start,
                     journal_entry__entry_date__lte=fy_end,
                     account__account_type__in=('revenue', 'expense'))
             .values('account__code')
             .annotate(d=Sum('debit_bwp'), c=Sum('credit_bwp')))

    out = {'op_income': ZERO, 'cor': ZERO, 'other_income': ZERO, 'expense': ZERO}
    for r in lines:
        bucket = _classify_pnl_bucket(r['account__code'])
        d, c = r['d'] or ZERO, r['c'] or ZERO
        if bucket in ('op_income', 'other_income'):
            out[bucket] += (c - d)
        else:
            out[bucket] += (d - c)

    out['gross_profit'] = out['op_income'] - out['cor']
    out['net_profit'] = out['gross_profit'] + out['other_income'] - out['expense']
    out['fy_label'] = fy_label
    return out


def insurance_pnl(anchor: Optional[date] = None) -> str:
    """Full insurance-format P&L for the fiscal year containing *anchor*.

    Structure follows the CFO's profit_and_loss.xlsx exactly:
        Operating Income  -  Cost of Revenue  =  Gross Profit
        Gross Profit  +  Other Income  -  Expenses  =  Net Profit
    """
    b = _pnl_buckets(anchor)
    return "\n".join([
        f"*Insurance P&L — {b['fy_label']}*",
        "",
        f"OPERATING INCOME            {_fmt_bwp(b['op_income'])}",
        "  _GWP, Change in UPR, RI Commission, Salvages, PPE disposal_",
        f"COST OF REVENUE            ({_fmt_bwp(b['cor'])})",
        "  _Premium Ceded, Gross Claims, Discounts, Claims movements_",
        "—————————————————",
        f"*GROSS PROFIT*              {_fmt_bwp(b['gross_profit'])}",
        "",
        f"OTHER INCOME                {_fmt_bwp(b['other_income'])}",
        "  _RI Recovery on Claims, Subrogation, Interest, FX, Movement in RI claims_",
        f"EXPENSES                   ({_fmt_bwp(b['expense'])})",
        "  _Commissions Paid, Staff, IT, Opex, Depreciation, ECL, Tax_",
        "—————————————————",
        f"*NET PROFIT*                {_fmt_bwp(b['net_profit'])}",
        "",
        "_Revenue (Gross Written Premium) sits in Operating Income only. "
        "Reinsurance recoveries are Other Income, NOT revenue. "
        "Ask `revenue` for the GWP breakdown or `balance sheet` for the BS._",
    ])


# ---------------------------------------------------------------------------
# Management Accounts — matches Alpha Direct's FY25 New Format spreadsheet
# ---------------------------------------------------------------------------
# Source: "Financials FY25 - New Format.xlsx" — PL sheet
#
# Critical insurance-specific groupings:
#   - Subrogations & Salvages sit INSIDE the Claims block (not Other Income).
#   - BONU Claims (103014) is a separate line under Claims.
#   - 119004 IBNR Provision Movement (was legacy 135) is part of Gross Claims.
#   - 111041 BONU Admin Expenses + 118007 BONU Acquisition combine into
#     "BONU Acquisition" under Acquisition Cost.
#   - 118005 Broker Entertainment + 118006 Acquisition Cost combine into
#     "Broker Entertainment" under Acquisition Cost.
#   - 112002 Risk AI Mgmt Fees → "Risk Licensing Fees" (separate from IT).
#   - 118745 AD Insurtech License → "Licensing Fee" (separate).
#   - Bad Debt Expenses (121xxx) + Provision for related party (119000) +
#     Provision for Subrogation (120) sit in a dedicated PROVISIONS block.
#   - Loss Ratios per MA formula:
#       Gross Loss = (Gross Claims − Subrogations & Salvages) / GWP
#       Net Loss   = Net Claims Incurred / Net Earned Premium

def _ma_lines(anchor: Optional[date] = None) -> dict:
    """Returns every line item of the management-accounts P&L for the FY
    containing *anchor*. Re-used by `management_accounts`, `insurance_pnl`,
    and `key_metrics` so all three are consistent."""
    from ledger.models import JournalEntryLine
    from django.db.models import Q

    if anchor is None:
        anchor = _latest_posted_date() or timezone.localdate()
    fy_start, fy_end, fy_label = _fy_window(anchor)

    def _sum(prefixes=None, codes=None, exclude_codes=None):
        """Sum (debit - credit) BWP for matching account codes."""
        q = Q()
        for p in (prefixes or []):
            q |= Q(account__code__startswith=p)
        for c in (codes or []):
            q |= Q(account__code=c)
        base = (JournalEntryLine.objects
                .filter(journal_entry__status='posted',
                        journal_entry__entry_date__gte=fy_start,
                        journal_entry__entry_date__lte=fy_end)
                .filter(q))
        if exclude_codes:
            base = base.exclude(account__code__in=exclude_codes)
        agg = base.aggregate(d=Sum('debit_bwp'), c=Sum('credit_bwp'))
        return (agg['d'] or ZERO) - (agg['c'] or ZERO)

    # ── NET EARNED PREMIUM ────────────────────────────────────────────────
    # GWP per MA = 100xxx EXCLUDING 100007 (Alpha SA RI Premium — intra-group,
    # eliminated on consolidation). 100011 Discounts Issued IS netted IN
    # (it has a debit balance, so including it reduces GWP).
    gwp                  = -_sum(prefixes=['100'], exclude_codes=['100007'])
    premium_ceded        =  _sum(prefixes=['101'])
    change_in_upr        = -_sum(prefixes=['102'])
    nep                  =  gwp - premium_ceded + change_in_upr

    # ── CLAIMS ────────────────────────────────────────────────────────────
    # Gross claims per MA = 103xxx (excl 103014 BONU, excl 103015/16/107 movements
    # which net to ~60K and are reported via the Subrogation movement line) +
    # 119004 (IBNR Provision Movement, was legacy 135).
    gross_claims         =  _sum(prefixes=['103'],
                                  exclude_codes=['103014', '103015', '103016', '103107']) \
                          + _sum(codes=['119004'])
    bonu_claims          =  _sum(codes=['103014'])
    ri_recovered         = -_sum(prefixes=['104'])                            # credit-natural
    subrogation_movement = ZERO                                                # CFO MA shows 0
    subrogations_salv    = -_sum(prefixes=['105'])                            # credit-natural
    # Net Claims (signed cost). All positive numbers below; result negative = cost.
    net_claims_signed    = -(gross_claims + bonu_claims - ri_recovered - subrogations_salv)

    # ── ACQUISITION COST ──────────────────────────────────────────────────
    iib_expenses         =  _sum(codes=['118001'])
    bonu_acquisition     =  _sum(codes=['118007', '111041'])
    ri_commission_inc    = -_sum(prefixes=['106'])                            # credit-natural
    commissions_paid     =  _sum(prefixes=['107'])
    broker_entertain     =  _sum(codes=['118005', '118006'])
    net_acquisition      =  ri_commission_inc - iib_expenses - bonu_acquisition \
                          - commissions_paid - broker_entertain

    # ── GROSS PROFIT ──────────────────────────────────────────────────────
    gross_profit         =  nep + net_claims_signed + net_acquisition

    # ── OTHER INCOME ──────────────────────────────────────────────────────
    other_income         = -_sum(prefixes=['109']) - _sum(prefixes=['123']) - _sum(prefixes=['124'])

    # ── OPERATING EXPENSES ────────────────────────────────────────────────
    employee_costs       =  _sum(prefixes=['110'], exclude_codes=['110011'])
    bonus_pay            =  _sum(codes=['110011'])
    # Operating Expenses per MA = 111xxx EXCLUDING:
    #   111041 BONU Admin (sits under BONU Acquisition)
    #   111002 Finance cost - IFRS 16 (sits under Finance Cost)
    operating_expenses   =  _sum(prefixes=['111'],
                                  exclude_codes=['111041', '111002'])
    it_expenses          =  _sum(prefixes=['112'], exclude_codes=['112002'])
    risk_licensing_fees  =  _sum(codes=['112002'])
    licensing_fee        =  _sum(codes=['118745'])
    paygates_expense     =  _sum(prefixes=['113'])
    telephone_internet   =  _sum(prefixes=['114'])
    marketing_advertise  =  _sum(prefixes=['115'])
    staff_welfare        =  _sum(prefixes=['116'])
    consultancy_fees     =  _sum(prefixes=['117'])
    total_opex           = (employee_costs + bonus_pay + operating_expenses
                          + it_expenses + risk_licensing_fees + licensing_fee
                          + paygates_expense + telephone_internet
                          + marketing_advertise + staff_welfare + consultancy_fees)

    # ── PROVISIONS ────────────────────────────────────────────────────────
    bad_debt_expenses    =  _sum(prefixes=['121'])                            # incl 121002 ex-195
    prov_related_party   =  _sum(codes=['119000'])
    prov_subrogation     =  _sum(prefixes=['120'])
    total_provisions     =  bad_debt_expenses + prov_related_party + prov_subrogation

    # ── EBITDA / EBIT / PBT / PAT ─────────────────────────────────────────
    ebitda               =  gross_profit + other_income - total_opex - total_provisions
    depreciation         =  _sum(prefixes=['122'])
    ebit                 =  ebitda - depreciation
    # Finance Cost per MA includes IFRS 16 interest cost + interest expense.
    # (Bank charges 111037 stay in Operating Expenses.)
    finance_cost         =  _sum(codes=['111002', '111046'])
    pbt                  =  ebit - finance_cost
    taxation             =  _sum(codes=['119002', '119001'])                  # income tax + deferred tax
    pat                  =  pbt - taxation

    # ── LOSS RATIOS (per MA formulas) ─────────────────────────────────────
    gross_loss_num       =  gross_claims - subrogations_salv      # excl BONU per MA
    gross_loss_ratio     =  (gross_loss_num / gwp * 100) if gwp != ZERO else None
    net_loss_ratio       =  (-net_claims_signed / nep * 100) if nep != ZERO else None

    return dict(
        fy_label=fy_label,
        gwp=gwp, premium_ceded=premium_ceded, change_in_upr=change_in_upr, nep=nep,
        gross_claims=gross_claims, bonu_claims=bonu_claims,
        ri_recovered=ri_recovered, subrogation_movement=subrogation_movement,
        subrogations_salv=subrogations_salv, net_claims_signed=net_claims_signed,
        gross_loss_ratio=gross_loss_ratio, net_loss_ratio=net_loss_ratio,
        iib_expenses=iib_expenses, bonu_acquisition=bonu_acquisition,
        ri_commission_inc=ri_commission_inc, commissions_paid=commissions_paid,
        broker_entertain=broker_entertain, net_acquisition=net_acquisition,
        gross_profit=gross_profit, other_income=other_income,
        employee_costs=employee_costs, bonus_pay=bonus_pay,
        operating_expenses=operating_expenses, it_expenses=it_expenses,
        risk_licensing_fees=risk_licensing_fees, licensing_fee=licensing_fee,
        paygates_expense=paygates_expense, telephone_internet=telephone_internet,
        marketing_advertise=marketing_advertise, staff_welfare=staff_welfare,
        consultancy_fees=consultancy_fees, total_opex=total_opex,
        bad_debt_expenses=bad_debt_expenses, prov_related_party=prov_related_party,
        prov_subrogation=prov_subrogation, total_provisions=total_provisions,
        ebitda=ebitda, depreciation=depreciation, ebit=ebit,
        finance_cost=finance_cost, pbt=pbt, taxation=taxation, pat=pat,
    )


def management_accounts(anchor: Optional[date] = None) -> str:
    """Management Accounts P&L — Alpha Direct's official FY25 New Format."""
    m = _ma_lines(anchor)

    def neg(x):  # show negative items in parens
        return f"({_fmt_bwp(x)})"

    def pct(r):
        return f"{r:.1f}%" if r is not None else "n/a"

    return "\n".join([
        f"*Alpha Direct Management Accounts — {m['fy_label']}*",
        "",
        "*NET EARNED PREMIUM*",
        f"  Gross Written Premium             {_fmt_bwp(m['gwp'])}",
        f"  Premiums Ceded to Reinsurance    {neg(m['premium_ceded'])}",
        f"  Change in UPR                     {_fmt_bwp(m['change_in_upr'])}",
        f"  *NEP*                             *{_fmt_bwp(m['nep'])}*",
        "",
        "*CLAIMS*",
        f"  Gross Insurance claim expenses   {neg(m['gross_claims'])}",
        f"  Subrogation movement              {_fmt_bwp(m['subrogation_movement'])}",
        f"  RI claims recovered               {_fmt_bwp(m['ri_recovered'])}",
        f"  BONU Claims                      {neg(m['bonu_claims'])}",
        f"  Subrogations & Salvages           {_fmt_bwp(m['subrogations_salv'])}",
        f"  *Net Claim Incurred*              *{_fmt_bwp(m['net_claims_signed'])}*",
        "",
        f"  Gross Loss Ratio   {pct(m['gross_loss_ratio']):>8s}",
        f"  Net Loss Ratio     {pct(m['net_loss_ratio']):>8s}",
        "",
        "*ACQUISITION COST*",
        f"  Insurance in a box expenses      {neg(m['iib_expenses'])}",
        f"  BONU Acquisition                 {neg(m['bonu_acquisition'])}",
        f"  Commission From Reinsurers        {_fmt_bwp(m['ri_commission_inc'])}",
        f"  Commissions Paid                 {neg(m['commissions_paid'])}",
        f"  Broker Entertainment             {neg(m['broker_entertain'])}",
        f"  *Net Acquisition (Costs)/Income*  *{_fmt_bwp(m['net_acquisition'])}*",
        "",
        f"*GROSS PROFIT*                      *{_fmt_bwp(m['gross_profit'])}*",
        "",
        f"Other Income                        {_fmt_bwp(m['other_income'])}",
        "",
        "*OPERATING EXPENSES*",
        f"  Employee costs                   {neg(m['employee_costs'])}",
        f"  Bonus Pay                        {neg(m['bonus_pay'])}",
        f"  Operating Expenses               {neg(m['operating_expenses'])}",
        f"  IT Expenses                      {neg(m['it_expenses'])}",
        f"  Risk Licensing Fees              {neg(m['risk_licensing_fees'])}",
        f"  Licensing Fee                    {neg(m['licensing_fee'])}",
        f"  Paygates Expense                 {neg(m['paygates_expense'])}",
        f"  Telephone & Internet             {neg(m['telephone_internet'])}",
        f"  Marketing & Advertising          {neg(m['marketing_advertise'])}",
        f"  Staff Welfare                    {neg(m['staff_welfare'])}",
        f"  Consultancy Fees                 {neg(m['consultancy_fees'])}",
        f"  *Total Operating Expenses*       *({_fmt_bwp(m['total_opex'])})*",
        "",
        "*PROVISIONS*",
        f"  Bad Debt Expenses                {neg(m['bad_debt_expenses'])}",
        f"  Provision for related party      {neg(m['prov_related_party'])}",
        f"  Provision for Subrogation        {neg(m['prov_subrogation'])}",
        f"  *Total Provisions*               *({_fmt_bwp(m['total_provisions'])})*",
        "",
        f"*EBITDA*                            *{_fmt_bwp(m['ebitda'])}*",
        f"Depreciation                       {neg(m['depreciation'])}",
        f"*EBIT*                              *{_fmt_bwp(m['ebit'])}*",
        f"Finance Cost                       {neg(m['finance_cost'])}",
        f"*PBT*                               *{_fmt_bwp(m['pbt'])}*",
        f"Taxation                           {neg(m['taxation'])}",
        f"*PAT*                               *{_fmt_bwp(m['pat'])}*",
    ])


# ---------------------------------------------------------------------------
# Balance Sheet — IFRS-friendly layout
# ---------------------------------------------------------------------------

def balance_sheet(anchor: Optional[date] = None) -> str:
    """Statement of Financial Position at the latest posted date.

    Each fiscal year is loaded as a year-end SNAPSHOT JE — so we filter by
    FY date range (not cumulative) to avoid double-counting when prior-year
    comparatives are also in the GL.
    """
    from ledger.models import Account, JournalEntryLine

    if anchor is None:
        anchor = _latest_posted_date() or timezone.localdate()
    fy_start, fy_end, fy_label = _fy_window(anchor)

    qs = (JournalEntryLine.objects
          .filter(journal_entry__status='posted',
                  journal_entry__entry_date__gte=fy_start,
                  journal_entry__entry_date__lte=fy_end,
                  account__account_type__in=('asset', 'liability', 'equity'))
          .values('account__account_type', 'account__sub_type',
                  'account__code', 'account__name')
          .annotate(d=Sum('debit_bwp'), c=Sum('credit_bwp'))
          .order_by('account__account_type', 'account__sub_type', 'account__code'))

    # Group by (type, sub_type)
    groups: dict[tuple[str, str], Decimal] = {}
    for r in qs:
        atype = r['account__account_type']
        sub   = r['account__sub_type'] or '(unclassified)'
        d, c  = r['d'] or ZERO, r['c'] or ZERO
        natural_balance = (d - c) if atype == 'asset' else (c - d)
        groups[(atype, sub)] = groups.get((atype, sub), ZERO) + natural_balance

    # Current-year P&L net rolls into equity
    pnl = _pnl_buckets(anchor)
    current_year_profit = pnl['net_profit']

    def _section(atype: str, header: str, lines: list):
        sub_totals = [(sub, amt) for (t, sub), amt in groups.items() if t == atype]
        sub_totals.sort(key=lambda x: x[0])
        section_total = sum((amt for _, amt in sub_totals), ZERO)
        lines.append(f"*{header}*")
        for sub, amt in sub_totals:
            lines.append(f"  {sub:<40s}{_fmt_bwp(amt)}")
        return section_total, lines

    out = [f"*Statement of Financial Position — as at {fy_end:%d %b %Y}*",
           f"_{fy_label} year-end_", ""]

    assets_total, out = _section('asset', 'ASSETS', out)
    out.append(f"  {'TOTAL ASSETS':<40s}{_fmt_bwp(assets_total)}")
    out.append("")

    liab_total, out = _section('liability', 'LIABILITIES', out)
    out.append(f"  {'TOTAL LIABILITIES':<40s}{_fmt_bwp(liab_total)}")
    out.append("")

    equity_total, out = _section('equity', 'EQUITY (GL only)', out)
    out.append(f"  {'+ Current Year Profit':<40s}{_fmt_bwp(current_year_profit)}")
    total_equity = equity_total + current_year_profit
    out.append(f"  {'TOTAL EQUITY':<40s}{_fmt_bwp(total_equity)}")
    out.append("")

    check = assets_total - (liab_total + total_equity)
    out.append(f"*L + E:* {_fmt_bwp(liab_total + total_equity)}    "
               f"_diff vs Assets: {_fmt_bwp(check)}_")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Key insurance metrics — Loss Ratio, Expense Ratio, Combined Ratio
# ---------------------------------------------------------------------------

def key_metrics(anchor: Optional[date] = None) -> str:
    """Insurance KPIs for the fiscal year containing *anchor*, using the
    Alpha Direct management-accounts formulas.

    Loss Ratios (per MA):
      Gross Loss Ratio = (Gross Claims − Subrogations & Salvages) / GWP
      Net Loss Ratio   = Net Claim Incurred / Net Earned Premium

    Other KPIs:
      Expense Ratio    = Operating Expenses / NEP
      Acquisition Ratio= Net Acquisition Cost (income +ve) / NEP
      Combined Ratio   = Net Loss Ratio + Expense Ratio − Acquisition Ratio
    """
    m = _ma_lines(anchor)

    def pct(n, d):
        if d == ZERO:
            return None
        return n / d * 100

    expense_ratio  = pct(m['total_opex'], m['nep'])
    acq_ratio      = pct(-m['net_acquisition'], m['nep'])   # net cost positive
    combined_ratio = None
    if m['net_loss_ratio'] is not None and expense_ratio is not None:
        combined_ratio = m['net_loss_ratio'] + expense_ratio
        if acq_ratio is not None:
            combined_ratio += acq_ratio

    def s(r):
        return f"{r:.1f}%" if r is not None else "n/a"

    return "\n".join([
        f"*Key Insurance Metrics — {m['fy_label']}*",
        f"_per Alpha Direct management-accounts formulas_",
        "",
        f"Gross Written Premium       {_fmt_bwp(m['gwp'])}",
        f"Premiums Ceded             ({_fmt_bwp(m['premium_ceded'])})",
        f"Change in UPR               {_fmt_bwp(m['change_in_upr'])}",
        f"────────",
        f"*Net Earned Premium (NEP)*  {_fmt_bwp(m['nep'])}",
        "",
        f"Gross Claims (excl BONU)    {_fmt_bwp(m['gross_claims'])}",
        f"BONU Claims                 {_fmt_bwp(m['bonu_claims'])}",
        f"RI Recovered               ({_fmt_bwp(m['ri_recovered'])})",
        f"Subrogations & Salvages    ({_fmt_bwp(m['subrogations_salv'])})",
        f"────────",
        f"*Net Claim Incurred*        {_fmt_bwp(-m['net_claims_signed'])}",
        "",
        f"*RATIOS*",
        f"Gross Loss Ratio    {s(m['gross_loss_ratio']):>8s}    _(Gross Claims − Subro) / GWP_",
        f"Net Loss Ratio      {s(m['net_loss_ratio']):>8s}    _(Net Claims / NEP)_",
        f"Acquisition Ratio   {s(acq_ratio):>8s}    _(Net Acq Cost / NEP)_",
        f"Expense Ratio       {s(expense_ratio):>8s}    _(OpEx / NEP)_",
        f"*Combined Ratio*    {s(combined_ratio):>8s}",
        "",
        f"_Net Acquisition is income, not cost: P {m['net_acquisition']:,.2f}. "
        f"That depresses the combined ratio favourably._",
    ])
    from ledger.models import JournalEntryLine
    from django.db.models import Q

    if anchor is None:
        anchor = _latest_posted_date() or timezone.localdate()
    fy_start, fy_end, fy_label = _fy_window(anchor)

    def _net(prefixes: list[str], natural: str) -> Decimal:
        q = Q()
        for p in prefixes:
            q |= Q(account__code__startswith=p)
        agg = (JournalEntryLine.objects
               .filter(journal_entry__status='posted',
                       journal_entry__entry_date__gte=fy_start,
                       journal_entry__entry_date__lte=fy_end)
               .filter(q)
               .aggregate(d=Sum('debit_bwp'), c=Sum('credit_bwp')))
        d, c = agg['d'] or ZERO, agg['c'] or ZERO
        return (c - d) if natural == 'credit' else (d - c)

    # Net Earned Premium = GWP + Change in UPR  -  Premium Ceded
    gwp        = _net(['100'], 'credit')
    upr_change = _net(['102'], 'credit')
    ceded      = _net(['101'], 'debit')
    nep = gwp + upr_change - ceded

    # Net Claims Incurred = Gross Claims  -  RI Recoveries  -  Salvages & Subrogation
    gross_claims = _net(['103'], 'debit')
    ri_recovery  = _net(['104'], 'credit')
    sub_sal      = _net(['105'], 'credit')
    net_claims = gross_claims - ri_recovery - sub_sal

    # Net Acquisition Cost = Commissions Paid + Direct Acquisition  -  RI Commission Income
    comm_paid  = _net(['107'], 'debit')
    direct_acq = _net(['118'], 'debit')
    ri_comm    = _net(['106'], 'credit')
    net_acq = comm_paid + direct_acq - ri_comm

    # Operating Expenses = everything in expense buckets EXCEPT commissions/acq/claims/discounts
    op_exp = (_net(['110'], 'debit') + _net(['111'], 'debit') + _net(['112'], 'debit')
            + _net(['113'], 'debit') + _net(['114'], 'debit') + _net(['115'], 'debit')
            + _net(['116'], 'debit') + _net(['117'], 'debit') + _net(['119'], 'debit')
            + _net(['120'], 'debit') + _net(['121'], 'debit') + _net(['122'], 'debit'))

    def _pct(n, d):
        if d == ZERO:
            return "n/a"
        return f"{(n / d * 100):.1f}%"

    loss_ratio    = _pct(net_claims, nep)
    acq_ratio     = _pct(net_acq,    nep)
    expense_ratio = _pct(op_exp,     nep)
    combined_pct  = (net_claims + net_acq + op_exp) / nep * 100 if nep != ZERO else None
    combined_str  = f"{combined_pct:.1f}%" if combined_pct is not None else "n/a"

    return "\n".join([
        f"*Key Insurance Metrics — {fy_label}*",
        "",
        f"Gross Written Premium      {_fmt_bwp(gwp)}",
        f"Change in UPR              {_fmt_bwp(upr_change)}",
        f"Premium Ceded to Reinsurers ({_fmt_bwp(ceded)})",
        f"────────",
        f"*Net Earned Premium (NEP)*  {_fmt_bwp(nep)}",
        "",
        f"Gross Claims Incurred      {_fmt_bwp(gross_claims)}",
        f"Reinsurance Recoveries     ({_fmt_bwp(ri_recovery)})",
        f"Subrogations & Salvages    ({_fmt_bwp(sub_sal)})",
        f"────────",
        f"*Net Claims Incurred*       {_fmt_bwp(net_claims)}",
        "",
        f"Commissions Paid           {_fmt_bwp(comm_paid)}",
        f"Direct Acquisition         {_fmt_bwp(direct_acq)}",
        f"RI Commission Income       ({_fmt_bwp(ri_comm)})",
        f"────────",
        f"*Net Acquisition Cost*      {_fmt_bwp(net_acq)}",
        "",
        f"Operating Expenses         {_fmt_bwp(op_exp)}",
        "",
        f"*RATIOS (over NEP)*",
        f"Loss Ratio          {loss_ratio:>8s}",
        f"Acquisition Ratio   {acq_ratio:>8s}",
        f"Expense Ratio       {expense_ratio:>8s}",
        f"*Combined Ratio*    {combined_str:>8s}",
        "",
        "_Combined Ratio < 100% means underwriting-profitable before investment income._",
    ])


# ---------------------------------------------------------------------------
# Cash
# ---------------------------------------------------------------------------

def cash_balance() -> str:
    """All active bank accounts with current balance, BWP total."""
    from banking.models import BankAccount

    accounts = (BankAccount.objects
                .filter(is_active=True)
                .select_related('currency_code')
                .order_by('bank_name', 'account_name'))
    if not accounts.exists():
        return "No active bank accounts on file."

    out = ["*Cash position — active bank accounts*", ""]
    total_bwp = ZERO
    has_foreign = False
    for ba in accounts:
        ccy = ba.currency_code_id
        bal = ba.current_balance or ZERO
        out.append(f"• {ba.bank_name} — {ba.account_name}")
        out.append(f"  {_fmt_ccy(ccy, bal)}")
        if ccy == 'BWP':
            total_bwp += bal
        else:
            has_foreign = True

    out.append("")
    out.append(f"*Total BWP cash:* {_fmt_bwp(total_bwp)}")
    if has_foreign:
        out.append("_Foreign-currency balances shown in their own currency, not summed._")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Purchase Orders
# ---------------------------------------------------------------------------

def pending_purchase_orders(limit: int = 10) -> str:
    """POs awaiting FM or CFO approval, plus draft."""
    from procurement.models import PurchaseOrder

    qs = (PurchaseOrder.objects
          .filter(status__in=['draft', 'pending_fm_approval', 'pending_cfo_approval'])
          .select_related('supplier')
          .order_by('-issue_date'))[:limit]

    if not qs:
        return "No pending purchase orders."

    label = {
        'draft':                'Draft',
        'pending_fm_approval':  'Pending FM',
        'pending_cfo_approval': 'Pending CFO',
    }
    out = ["*Pending purchase orders*", ""]
    for po in qs:
        out.append(f"• `{po.po_number}` — {po.supplier.name}")
        out.append(f"  {_fmt_ccy(po.currency_code_id, po.total_amount)} — {label.get(po.status, po.status)}")
    return "\n".join(out)


def purchase_order_detail(po_number: str) -> str:
    """Snapshot of a single PO."""
    from procurement.models import PurchaseOrder

    po_number = (po_number or '').strip()
    if not po_number:
        return "Please include the PO number."

    try:
        po = (PurchaseOrder.objects
              .select_related('supplier')
              .get(po_number__iexact=po_number))
    except PurchaseOrder.DoesNotExist:
        return f"PO `{po_number}` not found."

    out = [
        f"*{po.po_number}*",
        f"Supplier: {po.supplier.name}",
        f"Department: {po.get_department_display()}",
        f"Issue date: {po.issue_date}",
        f"Status: *{po.get_status_display()}*",
        f"Amount: {_fmt_ccy(po.currency_code_id, po.total_amount)}",
    ]
    if po.currency_code_id != 'BWP':
        out.append(f"BWP equivalent: {_fmt_bwp(po.total_bwp)}")
    if po.related_claim_reference:
        out.append(f"Claim ref: `{po.related_claim_reference}`")
    if po.justification:
        j = po.justification[:200]
        out.append(f"Justification: {j}")

    trail = []
    if po.submitted_at:
        trail.append(f"submitted {po.submitted_at:%Y-%m-%d}")
    if po.fm_approved_at:
        trail.append(f"FM approved {po.fm_approved_at:%Y-%m-%d}")
    if po.cfo_approved_at:
        trail.append(f"CFO approved {po.cfo_approved_at:%Y-%m-%d}")
    if trail:
        out.append("Approvals: " + " → ".join(trail))

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Bills / Receivables / Payables
# ---------------------------------------------------------------------------

_OPEN_INVOICE_STATUSES = ['posted', 'partially_paid', 'overdue']


def bills_to_pay(limit: int = 10) -> str:
    """Vendor bills with balance > 0, oldest due first."""
    from billing.models import Invoice

    qs = (Invoice.objects
          .filter(invoice_type='vendor_bill',
                  status__in=_OPEN_INVOICE_STATUSES,
                  balance_due__gt=0)
          .select_related('contact')
          .order_by('due_date'))[:limit]

    if not qs:
        return "No outstanding vendor bills."

    today = timezone.localdate()
    out = ["*Bills to pay — oldest first*", ""]
    for inv in qs:
        flag = ""
        if inv.due_date and inv.due_date < today:
            flag = " *(OVERDUE)*"
        out.append(f"• `{inv.invoice_number}` — {inv.contact.name}")
        out.append(f"  Due {inv.due_date or '—'}: {_fmt_bwp(inv.balance_due)}{flag}")
    return "\n".join(out)


def _gl_balance_by_prefix(prefixes: list[str], natural: str,
                          anchor: Optional[date] = None) -> Decimal:
    """Sum (Dr - Cr) or (Cr - Dr) across posted JE lines in the FY containing
    *anchor* for accounts whose code starts with any of *prefixes*."""
    from ledger.models import JournalEntryLine
    from django.db.models import Q

    if anchor is None:
        anchor = _latest_posted_date() or timezone.localdate()
    fy_start, fy_end, _ = _fy_window(anchor)

    q = Q()
    for p in prefixes:
        q |= Q(account__code__startswith=p)
    agg = (JournalEntryLine.objects
           .filter(journal_entry__status='posted',
                   journal_entry__entry_date__gte=fy_start,
                   journal_entry__entry_date__lte=fy_end)
           .filter(q)
           .aggregate(d=Sum('debit_bwp'), c=Sum('credit_bwp')))
    d = agg['d'] or ZERO
    c = agg['c'] or ZERO
    return (d - c) if natural == 'debit' else (c - d)


def ar_outstanding(limit: int = 10) -> str:
    """Customer invoices outstanding. Falls back to GL totals from 290xxx
    + 202xxx + 240xxx + 260xxx when no sub-ledger Invoice rows exist
    (e.g. just after importing a year-end trial balance)."""
    from billing.models import Invoice

    base = Invoice.objects.filter(
        invoice_type='customer_invoice',
        status__in=_OPEN_INVOICE_STATUSES,
        balance_due__gt=0,
    )
    qs = base.select_related('contact').order_by('-balance_due')[:limit]

    if qs:
        grand = base.aggregate(t=Sum('balance_due'))['t'] or ZERO
        out = ["*AR outstanding — top by balance*", ""]
        for inv in qs:
            out.append(f"• `{inv.invoice_number}` — {inv.contact.name}: {_fmt_bwp(inv.balance_due)}")
        out.append("")
        out.append(f"*Total AR:* {_fmt_bwp(grand)}")
        return "\n".join(out)

    # ---- GL fallback ----
    insurance_recv = _gl_balance_by_prefix(['290'], 'debit')
    other_recv     = _gl_balance_by_prefix(['202'], 'debit')
    subro_recv     = _gl_balance_by_prefix(['240'], 'debit')
    salvage_recv   = _gl_balance_by_prefix(['260'], 'debit')
    related_party  = _gl_balance_by_prefix(['201'], 'debit')

    out = [
        "*Receivables (GL totals — sub-ledger detail not loaded)*",
        "",
        f"Insurance premiums (290xxx) : {_fmt_bwp(insurance_recv)}",
        f"Other receivables (202xxx)  : {_fmt_bwp(other_recv)}",
        f"Subrogation receivable (240): {_fmt_bwp(subro_recv)}",
        f"Salvage receivable (260)    : {_fmt_bwp(salvage_recv)}",
        f"Related party receivable    : {_fmt_bwp(related_party)}",
        "",
        f"*Total receivables:* "
        f"{_fmt_bwp(insurance_recv + other_recv + subro_recv + salvage_recv + related_party)}",
        "",
        "_From FY 2024-25 trial balance closing position._",
    ]
    return "\n".join(out)


def ap_outstanding(limit: int = 10) -> str:
    """Vendor bills outstanding. Falls back to GL totals from 214xxx + 215xxx
    + 204xxx + 212xxx when no Invoice sub-ledger detail exists."""
    from billing.models import Invoice

    base = Invoice.objects.filter(
        invoice_type='vendor_bill',
        status__in=_OPEN_INVOICE_STATUSES,
        balance_due__gt=0,
    )
    qs = base.select_related('contact').order_by('-balance_due')[:limit]

    if qs:
        grand = base.aggregate(t=Sum('balance_due'))['t'] or ZERO
        out = ["*AP outstanding — top by balance*", ""]
        for inv in qs:
            out.append(f"• `{inv.invoice_number}` — {inv.contact.name}: {_fmt_bwp(inv.balance_due)}")
        out.append("")
        out.append(f"*Total AP:* {_fmt_bwp(grand)}")
        return "\n".join(out)

    # ---- GL fallback ----
    accounts_pay   = _gl_balance_by_prefix(['214'], 'credit')
    other_pay      = _gl_balance_by_prefix(['215'], 'credit')
    related_party  = _gl_balance_by_prefix(['204'], 'credit')
    re_insurers    = _gl_balance_by_prefix(['212'], 'credit')
    claims_pay     = _gl_balance_by_prefix(['208'], 'credit')

    out = [
        "*Payables (GL totals — sub-ledger detail not loaded)*",
        "",
        f"Accounts payable (214xxx)    : {_fmt_bwp(accounts_pay)}",
        f"Other payables (215xxx)      : {_fmt_bwp(other_pay)}",
        f"Related party payable (204)  : {_fmt_bwp(related_party)}",
        f"Due to reinsurers (212)      : {_fmt_bwp(re_insurers)}",
        f"Claims payable (208)         : {_fmt_bwp(claims_pay)}",
        "",
        f"*Total payables:* "
        f"{_fmt_bwp(accounts_pay + other_pay + related_party + re_insurers + claims_pay)}",
        "",
        "_From FY 2024-25 trial balance closing position._",
    ]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

def open_exceptions(limit: int = 10) -> str:
    """Open exception queue."""
    from exceptions.models import Exception as Exc

    base = Exc.objects.filter(status__in=['open', 'acknowledged', 'in_progress'])

    # Severity sort order: critical > high > medium > low
    severity_rank = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
    qs_all = list(base.order_by('-created_at'))
    qs_all.sort(key=lambda e: (severity_rank.get(e.severity, 9), -int(e.created_at.timestamp())))
    qs = qs_all[:limit]

    if not qs:
        return "No open exceptions. Clean ledger."

    out = ["*Open exceptions*", ""]
    for ex in qs:
        out.append(f"• [{ex.get_severity_display()}] {ex.title}")
        if ex.source_label:
            out.append(f"  ref: `{ex.source_label}`")

    counts = (base.values('severity')
              .annotate(n=Count('id'))
              .order_by('severity'))
    if counts:
        summary = ", ".join(f"{c['severity']}={c['n']}" for c in counts)
        out.append("")
        out.append(f"_Counts: {summary}_")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# P&L summary
# ---------------------------------------------------------------------------

def pnl_summary() -> str:
    """P&L summary. Defers to `insurance_pnl` when the latest data is more
    than 60 days old (typical of a year-end TB snapshot); otherwise returns
    a quick MTD revenue / expense / net summary."""
    from ledger.models import JournalEntryLine

    today = timezone.localdate()
    latest = _latest_posted_date()

    if latest is None:
        return "No journal entries on file."

    # Stale data → full insurance P&L for the FY of the latest entry
    if (today - latest).days > 60:
        return insurance_pnl(latest)

    # Fresh data — quick MTD summary (lump revenue / expense, current month)
    period_start = date(today.year, today.month, 1)
    common = dict(
        journal_entry__status='posted',
        journal_entry__entry_date__gte=period_start,
        journal_entry__entry_date__lte=today,
    )
    rev = (JournalEntryLine.objects
           .filter(account__account_type='revenue', **common)
           .aggregate(c=Sum('credit_bwp'), d=Sum('debit_bwp')))
    revenue = (rev['c'] or ZERO) - (rev['d'] or ZERO)

    exp = (JournalEntryLine.objects
           .filter(account__account_type='expense', **common)
           .aggregate(c=Sum('credit_bwp'), d=Sum('debit_bwp')))
    expense = (exp['d'] or ZERO) - (exp['c'] or ZERO)

    net = revenue - expense

    return "\n".join([
        f"*P&L — month-to-date ({period_start} to {today})*",
        "",
        f"Revenue:  {_fmt_bwp(revenue)}",
        f"Expenses: {_fmt_bwp(expense)}",
        f"———",
        f"*Net Profit:* {_fmt_bwp(net)}",
        "",
        "_For full insurance P&L by category, type `insurance pnl`._",
    ])


# ---------------------------------------------------------------------------
# System status
# ---------------------------------------------------------------------------

def system_status() -> str:
    """Quick health snapshot — counts only, no figures."""
    from ledger.models import JournalEntry
    from procurement.models import PurchaseOrder
    from billing.models import Invoice
    from exceptions.models import Exception as Exc

    je_pending   = JournalEntry.objects.filter(status='pending_approval').count()
    po_pending   = PurchaseOrder.objects.filter(
                       status__in=['pending_fm_approval', 'pending_cfo_approval']
                   ).count()
    bills_unpaid = Invoice.objects.filter(
                       invoice_type='vendor_bill',
                       status__in=_OPEN_INVOICE_STATUSES,
                       balance_due__gt=0,
                   ).count()
    exc_open     = Exc.objects.filter(status='open').count()
    exc_critical = Exc.objects.filter(
                       status__in=['open', 'acknowledged'],
                       severity='critical',
                   ).count()

    out = [
        "*System status*",
        "",
        f"JEs pending approval: *{je_pending}*",
        f"POs pending approval: *{po_pending}*",
        f"Bills unpaid: *{bills_unpaid}*",
        f"Open exceptions: *{exc_open}*",
    ]
    if exc_critical:
        out.append("")
        out.append(f"⚠️ *Critical exceptions: {exc_critical}* — needs CFO eye.")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Payroll — individual staff pay (SENSITIVE: whitelist-gated in bot.py)
# ---------------------------------------------------------------------------
# Returns Payslip net/gross/PAYE for one named employee in one period.
# National ID and bank details are NEVER included. The employee is matched
# by name here (local DB) — the name is never sent to any external AI.

def _resolve_period(period_name: Optional[str], want_last_month: bool):
    """Pick a PayrollPeriod. Priority: explicit '2026-05' > calendar last
    month > latest period that actually has payslips. Returns None if there
    are no periods at all."""
    from payroll.models import PayrollPeriod

    if period_name:
        p = PayrollPeriod.objects.filter(period_name=period_name).first()
        if p:
            return p

    if want_last_month:
        today = timezone.localdate()
        y, m = today.year, today.month - 1
        if m == 0:
            m, y = 12, y - 1
        p = PayrollPeriod.objects.filter(period_name=f"{y:04d}-{m:02d}").first()
        if p:
            return p

    return (PayrollPeriod.objects
            .filter(payslips__isnull=False)
            .order_by('-start_date')
            .distinct()
            .first())


def staff_pay(employee_query: str, period_name: Optional[str] = None,
              want_last_month: bool = False) -> str:
    """What a named employee was paid (net take-home) in a period."""
    from payroll.models import Employee, Payslip

    q = (employee_query or '').strip()
    if len(q) < 2:
        return ("Whose pay? e.g. `how much did we pay Pako last month` "
                "or `Pako salary 2026-05`.")

    matches = list(Employee.objects
                   .filter(full_name__icontains=q)
                   .order_by('full_name')[:11])
    if not matches:                       # phrase missed — try each token
        for tok in q.split():
            if len(tok) < 3:
                continue
            matches = list(Employee.objects
                           .filter(full_name__icontains=tok)
                           .order_by('full_name')[:11])
            if matches:
                break

    if not matches:
        return f"No staff member matches “{q}”."
    if len(matches) > 1:
        names = "\n".join(f"• {e.full_name}" for e in matches[:10])
        more = "\n_…and more. Be more specific._" if len(matches) > 10 else ""
        return f"Several staff match “{q}” — which one?\n{names}{more}"

    emp = matches[0]
    period = _resolve_period(period_name, want_last_month)
    if period is None:
        return "No payroll periods on file yet."

    # unique_together(employee, period) ⇒ at most one slip; drop cancelled.
    slip = (Payslip.objects
            .filter(employee=emp, period=period)
            .exclude(status=Payslip.Status.CANCELLED)
            .first())

    if slip is None:
        latest = (Payslip.objects
                  .filter(employee=emp)
                  .exclude(status=Payslip.Status.CANCELLED)
                  .select_related('period')
                  .order_by('-period__start_date')
                  .first())
        if latest is None:
            return f"No payslip on file for {emp.full_name}."
        return (f"No payslip for *{emp.full_name}* in {period.period_name}. "
                f"Latest on file: {latest.period.period_name} — net "
                f"{_fmt_bwp(latest.net_amount)} ({latest.get_status_display()}).")

    status_note = ""
    if slip.status != Payslip.Status.PAID:
        status_note = f"   _({slip.get_status_display()} — not yet marked paid)_"

    out = [
        f"*{emp.full_name}* — {period.period_name}",
        f"  Net pay:  {_fmt_bwp(slip.net_amount)}{status_note}",
        f"  Gross:    {_fmt_bwp(slip.gross_amount)}",
        f"  PAYE:     {_fmt_bwp(slip.paye_amount)}",
    ]
    if slip.is_foreign_currency and slip.source_net is not None:
        out.append(f"  Paid in {slip.source_currency}: "
                   f"{_fmt_ccy(slip.source_currency, slip.source_net)}")
    out.append("")
    out.append("_Net = take-home after tax & deductions. Source: payroll register._")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Time Doctor — workforce hours / productivity (SENSITIVE: whitelist-gated)
# ---------------------------------------------------------------------------
# Reads the privacy-safe daily aggregate (integrations.TimeDoctorDailySnapshot).
# Raw window/app titles are never stored, so never returned. Per-person hours
# are personal data → the ANSWER is gated behind the authorised whitelist.

def _fmt_last_seen(value) -> Optional[str]:
    """The moment only, in Botswana time — never the rest of the object.

    Time Doctor does not send `last_seen` as a string. In prod it is the raw
    API object and the reply printed the whole thing, internal IP included:

        Last seen: {'ip': '<an internal address>', 'online': False,
                    'updatedAt': '2026-09-08T09:39:54.706Z'}

    Anything that is not a recognised shape returns None rather than being
    str()'d, because str() of an unknown object is how the IP got out.
    """
    from django.utils.dateparse import parse_datetime

    if isinstance(value, dict):
        raw = value.get('updatedAt')
        if not isinstance(raw, str) or not raw.strip():
            return None          # no moment in it -> say nothing at all
    elif isinstance(value, str):
        raw = value
    else:
        return None

    raw = raw.strip()
    try:
        dt = parse_datetime(raw)
    except ValueError:
        dt = None
    if dt is None:
        # An opaque string the feed sent us ("sometime yesterday"). A plain
        # string is safe to show; a dict never reaches here.
        return raw if isinstance(value, str) else None
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)      # TIME_ZONE is Africa/Gaborone
    # A naive value carries no zone, so it is NOT shifted -- inventing a
    # two-hour move would misreport the moment.
    return f"{dt:%Y-%m-%d %H:%M} SAST"


def _latest_td_rows():
    """All snapshot rows for the most recent as_of date (one per company)."""
    from integrations.models import TimeDoctorDailySnapshot

    latest = (TimeDoctorDailySnapshot.objects
              .order_by('-as_of')
              .values_list('as_of', flat=True)
              .first())
    if latest is None:
        return None, []
    return latest, list(TimeDoctorDailySnapshot.objects.filter(as_of=latest))


def time_doctor_query(name_query: str = '') -> str:
    """Per-person hours if a name is given, else the workforce roll-up."""
    as_of, rows = _latest_td_rows()
    if not rows:
        return "No Time Doctor data on file yet."

    q = (name_query or '').strip().lower()
    if q:
        from hris import hours_for_day
        from integrations.td_matching import (TDMatcher, collapse_users,
                                              fold_uid, hours_by_employee)
        from payroll.models import Employee

        # Resolve WHO first, against the payroll roster — the authoritative list
        # of who exists. Searching the Time Doctor text instead breaks both ways
        # when the two names differ: "Modiri Katai" (HR) vs "Modiri Mokati" (TD)
        # returns "no record" for someone who tracked all day, and a common first
        # name can land on the wrong person (checklist L19 — the Modiri /
        # Christopher case, corrected ~20 times).
        # ACTIVE staff, not just the tracking-eligible ones: the question here is
        # "who is this person", and someone may legitimately ask about a
        # colleague who is not on the hours regime. Whether Omni HOLDS hours for
        # them is answered further down, from the confirmed account link.
        employees = list(Employee.objects.filter(status='active')
                         .only('id', 'full_name', 'email'))
        # Tiered, not a flat any(): "Christopher Moeng" is a FULL name and must
        # answer, but under any-token it matched both Christophers and the bot
        # replied "ask again with the full name" — which the user had just done
        # (Fable review 2026-09-09). Narrowest tier that finds anyone wins.
        toks = [t for t in q.split() if len(t) > 2]

        def _blob(e):
            return f"{e.full_name or ''} {e.email or ''}".lower()

        people = [e for e in employees if q in _blob(e)]
        if not people and toks:
            people = [e for e in employees
                      if all(t in _blob(e) for t in toks)]
        if not people and toks:
            people = [e for e in employees
                      if any(t in _blob(e) for t in toks)]

        if not people:
            return f"No one on the staff list matches \u201c{name_query}\u201d."

        if len(people) > 1:
            who = "\n".join(
                f"  \u2022 {e.full_name}" + (f" ({e.email})" if e.email else '')
                for e in people[:8])
            more = "" if len(people) <= 8 else f"\n  \u2026and {len(people) - 8} more"
            return (f"\u201c{name_query}\u201d matches {len(people)} people. "
                    f"Who did you mean?\n{who}{more}\n\n"
                    f"_Ask again with the full name or the email \u2014 I will not guess "
                    f"whose hours to show._")

        emp = people[0]
        # The FIGURE comes through the confirmed Time Doctor account link, and is
        # raised to the permanent record (the stored snapshot freezes at the
        # 06:30 pull and Time Doctor back-fills late uploads \u2014 L27/L28).
        # The WHOLE roster goes to the matcher, never just this one person: with
        # a singleton roster TDMatcher's reverse guard can only check against
        # that person, so an unconfirmed account could bind to the wrong
        # employee (Fable review 2026-09-09). One matcher over the roster is the
        # designed shape.
        #
        # And this leg FAILS CLOSED. Unlike the HTTP report and the /my-omni
        # tile, this prints a figure about a NAMED person into a chat as fact,
        # which is judgement-adjacent — a stale low number there is the harm we
        # spent the day removing. The cost of refusing is one sentence.
        from hris.hours_for_day import RecordUnavailable
        hrs = None
        extra = {}
        try:
            for row in rows:
                floored = hours_for_day.floored_rows(row.as_of, row.payload,
                                                     employees=employees,
                                                     on_error='raise')
                got = hours_by_employee(floored, employees).get(emp.id)
                if got is None:
                    continue
                got = float(got)
                # `hrs is None`, NOT `got > (hrs or 0.0)`. A matched person who
                # tracked 0.00 h is not the same as a person with no matched
                # Time Doctor account, and `> 0.0` collapsed the two — a
                # correctly-linked employee on a zero day was told to go and
                # "confirm their account under Who-Tracks" (Fable review
                # 2026-09-09, checklist L29).
                if hrs is None or got > hrs:
                    hrs = got
                    # Extras come from the row resolved by the SAME account link
                    # that produced the figure. Picking "the row whose hours
                    # equal the answer" returns a stranger's row whenever two
                    # people share a value — and 0.0 is the commonest value on
                    # the roster (Fable review 2026-09-09, checklist L30).
                    uid = (TDMatcher(collapse_users(floored), employees)
                           .uid_for_employee_id.get(emp.id))
                    extra = next(
                        (m for m in floored
                         if uid is not None
                         and str(fold_uid(m.get('user_id'))) == str(fold_uid(uid))),
                        {})
        except RecordUnavailable:
            return ("Omni's permanent record could not be read just now, so I "
                    "will not quote hours that might be too low. Try again "
                    "shortly.")

        if hrs is None:
            return (f"*{emp.full_name}* has no matched Time Doctor account, so "
                    f"Omni holds no hours for them on {as_of:%d %b %Y}.\n"
                    f"_Confirm their account under Who-Tracks._")

        out = [f"*{emp.full_name}* \u2014 Time Doctor {as_of:%d %b %Y}",
               f"  Hours tracked:  {round(hrs, 2)}h"]
        if extra.get('productive_pct') is not None:
            out.append(f"  Productive:     {extra['productive_pct']}%")
        if extra and not extra.get('tracked_today'):
            out.append("  _No time tracked this day._")
        seen = _fmt_last_seen(extra.get('last_seen'))
        if seen:
            out.append(f"  Last seen: {seen}")
        return "\n".join(out)

    # Workforce roll-up across companies
    out = [f"*Workforce — Time Doctor {as_of:%d %b %Y}*", ""]
    tot_hours = 0.0
    tot_active = 0
    for row in rows:
        t = row.totals or {}
        tot_hours += float(t.get('total_hours') or 0)
        tot_active += int(t.get('active_users') or 0)
        cid = row.company_id or '?'
        out.append(f"• {cid}: {t.get('active_users','?')} active, "
                   f"{t.get('total_hours','?')}h, "
                   f"prod {t.get('productive_pct','?')}%")
    out.append("")
    out.append(f"*Total:* {tot_active} active · {round(tot_hours,1)}h tracked")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Fleet — company vehicles (Cartrack). Company property.
# ---------------------------------------------------------------------------

def fleet_query(query: str = '', limit: int = 20) -> str:
    """One vehicle's live status if a plate/driver/model is given, else the
    fleet list."""
    from nexus.models import FleetVehicle
    from django.db.models import Q

    q = (query or '').strip()
    if q:
        veh = list(FleetVehicle.objects.filter(
            Q(registration__icontains=q) | Q(driver_name__icontains=q) |
            Q(make__icontains=q) | Q(model__icontains=q) |
            Q(description__icontains=q))[:11])
        if not veh:
            return f"No fleet vehicle matches “{q}”."
        if len(veh) > 1:
            names = "\n".join(f"• `{v.registration}` — {v.make} {v.model} "
                              f"({v.driver_name or 'no driver'})" for v in veh[:10])
            return f"Several vehicles match “{q}” — which?\n{names}"
        v = veh[0]
        moving = "moving" if v.moving else ("ignition on" if v.ignition_on else "stopped")
        out = [
            f"*{v.registration}* — {v.make} {v.model}".rstrip(),
            f"  Driver:   {v.driver_name or '—'}",
            f"  Status:   {moving}"
            + (f", {v.last_speed_kmh:.0f} km/h" if v.last_speed_kmh else ""),
            f"  Where:    {v.where or '—'}",
        ]
        if v.odometer_km:
            out.append(f"  Odometer: {v.odometer_km:,.0f} km")
        if v.last_seen:
            out.append(f"  Last seen: {v.last_seen}")
        return "\n".join(out)

    vehicles = list(FleetVehicle.objects.all()[:limit])
    if not vehicles:
        return "No fleet vehicles on file. (Cartrack key may not be set.)"
    out = [f"*Fleet — {len(vehicles)} vehicle(s)*", ""]
    for v in vehicles:
        state = "🟢" if v.moving else "⚪"
        out.append(f"{state} `{v.registration}` {v.make} {v.model} — "
                   f"{v.driver_name or 'no driver'}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Assets — fixed-asset register. Company property + custodian.
# ---------------------------------------------------------------------------

def asset_query(query: str = '', limit: int = 15) -> str:
    """Asset detail if a single tag/name matches, a list if a custodian or a
    broad term matches several, else the register head."""
    from assets.models import Asset
    from django.db.models import Q

    q = (query or '').strip()
    base = Asset.objects.select_related('category', 'company').exclude(
        status=Asset.Status.MIGRATED_DUPLICATE)

    if not q:
        rows = list(base.order_by('tag_number')[:limit])
        if not rows:
            return "No assets on the register."
        head = [f"*Asset register — first {len(rows)}*", ""]
        head += [f"• `{a.tag_number}` {a.name} — {a.custodian or 'unassigned'}"
                 for a in rows]
        return "\n".join(head)

    matches = list(base.filter(
        Q(tag_number__icontains=q) | Q(name__icontains=q) |
        Q(serial_number__icontains=q) | Q(custodian__icontains=q) |
        Q(custodian_employee__full_name__icontains=q))[:limit + 1])

    if not matches:
        return f"No asset matches “{q}”."
    if len(matches) > 1:
        rows = "\n".join(f"• `{a.tag_number}` {a.name} — "
                         f"{a.custodian or 'unassigned'} @ {a.location or '—'}"
                         for a in matches[:limit])
        more = "\n_…more. Narrow it down._" if len(matches) > limit else ""
        return f"*{len(matches)} assets match “{q}”*\n{rows}{more}"

    a = matches[0]
    out = [
        f"*{a.name}* — `{a.tag_number}`",
        f"  Category:  {a.category.name if a.category_id else '—'}",
        f"  Company:   {a.company.code if a.company_id else '—'}",
        f"  Cost:      {_fmt_bwp(a.cost)}",
        f"  Status:    {a.get_status_display()}",
        f"  Custodian: {a.custodian or '—'}",
        f"  Location:  {a.location or '—'}",
    ]
    if a.serial_number:
        out.append(f"  Serial:    {a.serial_number}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# AI ask-anything fallback (CFO 2026-07-16) — DeepSeek answers off-menu
# questions from a COMPANY-LEVEL figure bundle. Individual salaries, Omang and
# bank details are NEVER put in the context: per-person pay stays on the
# whitelist-gated `payroll` intent. DeepSeek is told to answer only from the
# context and never to guess.
# ---------------------------------------------------------------------------

import re as _re

# Strip anything that looks like an ID / account / card number (7+ digit runs)
# from the context as defence-in-depth before it leaves the box.
_LONG_DIGITS = _re.compile(r'\b\d{7,}\b')


def _ai_context() -> str:
    parts = []
    # Authoritative audit-locked headline figures FIRST — these are the source
    # of truth for GWP / PAT / Total Assets / Cash (the GL P&L can be thin for
    # the current FY when only frozen headlines + a TB snapshot are loaded).
    try:
        from ledger.models import FrozenFigure
        fr = list(FrozenFigure.objects.filter(is_active=True)
                  .order_by('period', 'line_label'))
        if fr:
            lines = [f"  {f.period} · {f.line_label}: P {f.value_bwp:,.2f}"
                     for f in fr]
            parts.append("*AUTHORITATIVE FROZEN HEADLINE FIGURES (audit-locked "
                         "— use these for GWP / PAT / revenue / profit / total "
                         "assets / cash)*\n" + "\n".join(lines))
    except Exception:                                       # noqa: BLE001
        pass
    for fn in (management_accounts, balance_sheet, cash_balance, key_metrics):
        try:
            parts.append(fn())
        except Exception:                                   # noqa: BLE001
            pass
    # Non-PII counts
    try:
        from payroll.models import Employee
        from nexus.models import FleetVehicle
        from assets.models import Asset
        hc = Employee.objects.filter(status='active').count()
        fl = FleetVehicle.objects.count()
        ac = Asset.objects.exclude(
            status=Asset.Status.MIGRATED_DUPLICATE).count()
        parts.append(f"*Counts*\nActive staff: {hc}\nFleet vehicles: {fl}\n"
                     f"Assets on register: {ac}")
    except Exception:                                       # noqa: BLE001
        pass
    ctx = "\n\n".join(parts)
    return _LONG_DIGITS.sub('[redacted]', ctx)


def ai_answer(question: str) -> str:
    """Free-form answer over company figures, via DeepSeek. Company-level
    aggregates only — no individual pay / Omang / bank details."""
    from core.ai_assist import deepseek_complete, DeepSeekUnavailable

    q = (question or '').strip()
    if len(q) < 3:
        return "Ask a full question, or type `help` for the menu."

    ctx = _ai_context()
    if not ctx.strip():
        return "No figures loaded yet to answer from. Type `help`."

    system = (
        "You are the Alpha Direct Insurance CFO assistant (Botswana, BWP). "
        "Answer the question using ONLY the figures in CONTEXT. For headline "
        "metrics (GWP, revenue, PAT, profit, total assets, cash) PREFER the "
        "AUTHORITATIVE FROZEN figures; treat any GL-derived line that reads "
        "P 0.00 as 'not loaded', not as an actual zero. If the answer is not "
        "present in CONTEXT, say you don't have that figure — never guess, "
        "never use outside knowledge, never invent numbers. Individual staff "
        "salaries are NOT in context and must be requested via the payroll "
        "command. Be concise and use Pula (P) amounts."
    )
    try:
        ans = deepseek_complete(
            f"CONTEXT:\n{ctx}\n\nQUESTION: {q}",
            system_prompt=system,
            timeout=30.0,
        )
    except DeepSeekUnavailable:
        return ("AI is unavailable right now. Try a specific command — "
                "type `help` for what I can pull directly.")
    except Exception:                                       # noqa: BLE001
        return "That one broke on the AI side. Try `help` for direct commands."

    return (ans or '').strip() + (
        "\n\n_AI answer from live company figures — verify in Omni for "
        "anything material._")


# ---------------------------------------------------------------------------
# Leave balances (HR) — SENSITIVE (personal data): whitelist-gated in bot.py.
# Reuses hris.leave_balance.balances_for_profile — the SINGLE source of truth
# behind the self-service balances page AND the leave-approval emails — so the
# bot can never drift from what Omni shows on screen.
# ---------------------------------------------------------------------------

def _match_employee(query: str, limit: int = 11):
    """Resolve a free-text name to payroll.Employee rows: whole phrase first,
    then fall back to individual tokens. Returns (cleaned_query, [Employee])."""
    from payroll.models import Employee

    q = (query or '').strip()
    if len(q) < 2:
        return q, []
    matches = list(Employee.objects.filter(full_name__icontains=q)
                   .order_by('full_name')[:limit])
    if not matches:
        for tok in q.split():
            if len(tok) < 3:
                continue
            matches = list(Employee.objects.filter(full_name__icontains=tok)
                           .order_by('full_name')[:limit])
            if matches:
                break
    return q, matches


def leave_query(name_query: str = '') -> str:
    """One employee's leave balances (annual, sick, ...)."""
    from hris.leave_balance import balances_for_profile

    q, matches = _match_employee(name_query)
    if len(q) < 2:
        return "Whose leave? e.g. `leave for Pako` or `Kago leave balance`."
    if not matches:
        return f"No staff member matches “{q}”."
    if len(matches) > 1:
        names = "\n".join(f"• {e.full_name}" for e in matches[:10])
        more = "\n_…and more. Be more specific._" if len(matches) > 10 else ""
        return f"Several staff match “{q}” — which one?\n{names}{more}"

    emp = matches[0]
    profile = getattr(emp, 'hris_profile', None)
    if profile is None:
        return (f"*{emp.full_name}* has no HR profile, so no leave balance is "
                f"tracked. (Payroll is separate — ask for their pay instead.)")

    try:
        balances = balances_for_profile(profile)
    except Exception:                                       # noqa: BLE001
        return f"Couldn't read leave balances for {emp.full_name} right now."
    if not balances:
        return f"No leave types configured for {emp.full_name}."

    def _row(b):
        return (f"  {(b.get('name') or 'Leave'):<15} "
                f"{float(b.get('available') or 0):>5.1f} left  "
                f"({float(b.get('used') or 0):.1f} used)")

    annual = [b for b in balances if b.get('code') == 'annual']
    others = [b for b in balances if b.get('code') != 'annual']
    out = [f"*{emp.full_name}* — leave balances", ""]
    out += [_row(b) for b in annual + others]
    out.append("")
    out.append("_Days available now. Annual leave accrues monthly (CoS §7.5). "
               "Source: Omni leave register._")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Claims (Graphite mirror) — SENSITIVE (carries customer names): whitelist-gated.
# Reads integrations.GraphiteClaim, the read-only mirror synced from Graphite V2
# (the same data behind Omni's claims-register screen). No GL involvement.
# ---------------------------------------------------------------------------

def claims_query(query: str = '', limit: int = 10) -> str:
    from django.db.models import Count, Q, Sum
    from integrations.models import GraphiteClaim

    q = (query or '').strip()

    # Specific lookup — claim number, policy number, or customer name
    if len(q) >= 4:
        hits = list(GraphiteClaim.objects.filter(
            Q(claim_number__icontains=q) | Q(policy_number__icontains=q) |
            Q(customer_name__icontains=q)).order_by('-registered_date')[:limit + 1])
        if not hits:
            return f"No claim matches “{q}”."
        if len(hits) == 1:
            c = hits[0]
            out = [
                f"*Claim {c.claim_number or c.graphite_id}* — {c.status or 'status?'}",
                f"  Customer:    {c.customer_name or '—'}",
                f"  Policy:      {c.policy_number or '—'}  ({c.product_name or '—'})",
                f"  Type:        {c.claim_type or '—'}",
                f"  Handler:     {c.claim_handler or '—'}",
                f"  Loss date:   {c.date_of_loss or '—'}",
                f"  Registered:  {c.registered_date or '—'}",
                f"  Reserve:     {_fmt_bwp(c.total_reserve)}",
                f"  Paid:        {_fmt_bwp(c.total_payment)}",
                f"  Outstanding: {_fmt_bwp(c.balance)}",
            ]
            if c.damage_cause:
                out.append(f"  Cause:       {c.damage_cause}")
            out.append("")
            out.append("_Source: Graphite claims register (mirror). "
                       "Verify in Graphite for anything material._")
            return "\n".join(out)
        rows = "\n".join(
            f"• `{c.claim_number or c.graphite_id}` — {c.customer_name or '—'} "
            f"({c.status or '?'}, out {_fmt_bwp(c.balance)})" for c in hits[:limit])
        more = "\n_…and more. Give the exact claim number._" if len(hits) > limit else ""
        return f"Several claims match “{q}”:\n{rows}{more}"

    # Register summary
    total = GraphiteClaim.objects.count()
    if not total:
        return "No claims synced from Graphite yet."
    by_status = (GraphiteClaim.objects.values('status')
                 .annotate(n=Count('id'), out=Sum('balance'))
                 .order_by('-n'))
    agg = GraphiteClaim.objects.aggregate(
        reserve=Sum('total_reserve'), paid=Sum('total_payment'), out=Sum('balance'))
    out = [f"*Claims register* — {total:,} claims", ""]
    for r in by_status[:12]:
        out.append(f"• {(r['status'] or '—'):<12} {r['n']:>4}   "
                   f"out {_fmt_bwp(r['out'] or ZERO)}")
    out.append("")
    out.append(f"*Totals:* reserve {_fmt_bwp(agg['reserve'] or ZERO)} · "
               f"paid {_fmt_bwp(agg['paid'] or ZERO)} · "
               f"outstanding {_fmt_bwp(agg['out'] or ZERO)}")
    out.append("")
    out.append("_Give a claim, policy, or customer name for detail. "
               "Source: Graphite mirror._")
    return "\n".join(out)
