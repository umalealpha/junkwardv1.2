"""
regulatory/services.py

Capital-adequacy computation for Alpha Direct Insurance.

The formula is intentionally simple and configurable — the parameters are
stored in CapitalRequirementParameter and editable by the CFO. Replace the
formula entirely if NBFIRA publishes a new methodology; just change the
inputs in the database.

Key inputs pulled live from the GL:
  - Total equity from the balance sheet
  - Intangibles deductions (account codes from parameter)
  - Gross Written Premium (12m sum of revenue lines on configured accounts)
  - Claims incurred (3y sum of expense lines on configured accounts)
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.contrib.auth.models import User
from django.db.models import Sum

from .models import CapitalRequirementParameter, RegulatoryCapitalSnapshot


ZERO = Decimal('0.00')


def _get_param(code: str, default: str = '0') -> str:
    p = CapitalRequirementParameter.objects.filter(code=code, is_active=True).first()
    return p.value if p else default


def _decimal(value: str, default: Decimal = ZERO) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _csv_codes(raw: str) -> list[str]:
    return [c.strip() for c in (raw or '').split(',') if c.strip()]


def _sum_gl_lines(account_codes: list[str], from_date, to_date, *, side: str,
                  company_id=None) -> Decimal:
    """Sum debit OR credit on the given account codes from posted JE lines.
    Scoped to one company when company_id is given (NBFIRA capital adequacy is
    per LICENSED ENTITY, not consolidated)."""
    if not account_codes:
        return ZERO
    from ledger.models import JournalEntry, JournalEntryLine
    qs = (
        JournalEntryLine.objects
        .filter(
            journal_entry__status=JournalEntry.Status.POSTED,
            journal_entry__entry_date__gte=from_date,
            journal_entry__entry_date__lte=to_date,
            account__code__in=account_codes,
        )
    )
    if company_id:
        qs = qs.filter(journal_entry__company_id=company_id)
    field = 'credit_bwp' if side == 'credit' else 'debit_bwp'
    total = qs.aggregate(t=Sum(field))['t'] or ZERO
    return total


# Parameters the LIVE formula actually consumes. Regulation 4 has no premium
# factor and no claims factor, so premium_factor / claims_factor /
# gwp_account_codes / claims_account_codes no longer feed the requirement. They
# are left in the table (other screens read gwp_account_codes) but they must not
# hold the ratio hostage: before this list existed, adopting the method meant
# putting an effective date on nine parameters, four of which the formula had
# stopped using.
_FORMULA_PARAMETERS = frozenset({
    'minimum_capital_floor',
    'opex_factor',
    'compliant_threshold',
    'margin_threshold',
    'intangibles_account_codes',
})


def _unverified_parameters() -> list:
    """Parameters that have never been formally adopted.

    regulatory/models.py says it outright: the values were "seeded with the
    historical NBFIRA general-insurer numbers as PLACEHOLDERS — the CFO MUST
    verify against the current Insurance Industry Regulations before any real
    submission."

    Two signals, both read from fields that already exist:

      * `effective_from` is null — a regulatory parameter with no effective date
        has never been adopted. On prod, all nine are null.
      * the note still carries the seeder's own "VERIFY against current …" text.

    An earlier version of this compared `updated_at` to `created_at`. That was a
    guess dressed as a test: inside one second it cannot tell a saved parameter
    from a seeded one, and it would have silently reported everything verified
    the first time anyone touched a row for an unrelated reason.
    """
    out = []
    seen = set()
    for p in CapitalRequirementParameter.objects.filter(is_active=True):
        if p.code not in _FORMULA_PARAMETERS:
            continue
        seen.add(p.code)
        if p.effective_from is None or 'VERIFY' in (p.notes or '').upper():
            out.append(p.code)
    # A parameter the formula reads but nobody has ever stored is running on a
    # code default. opex_factor arrived with the reg 4 rebuild and has no row
    # yet, so without this the gate would open on four adopted rows while the
    # 25% itself had never been signed off.
    out.extend(_FORMULA_PARAMETERS - seen)
    return sorted(out)


def adopted_parameters() -> dict:
    """The parameter values currently in force, code -> value.

    A snapshot is only signable against the method that was in force when it was
    computed. Storing the values (not a version number) means the comparison
    keeps working without a new table.
    """
    return {p.code: p.value
            for p in CapitalRequirementParameter.objects.filter(is_active=True)}


def snapshot_method_state(snap) -> dict:
    """Is this stored snapshot still on the current method?

    Two ways a snapshot goes stale, both fatal for a signature:

      * the method is under revision — no parameter has been adopted yet, so
        there is nothing to sign against;
      * a parameter has moved since the snapshot was computed, so its ratio was
        produced by a formula no longer in force.

    The only snapshot on prod (2026-07-24, CAR 612.02%) is stale on both counts:
    it was computed with premium_factor 0.18 and claims_factor 0.26, neither of
    which appears anywhere in the Insurance Industry Regulations 2019.
    """
    unverified = _unverified_parameters()
    stored = ((snap.components or {}).get('parameters') or {})
    current = adopted_parameters()
    drifted = sorted(
        code for code, value in stored.items()
        if code in current and str(current[code]) != str(value)
    )
    if unverified:
        return {
            'method_current': False,
            'method_note': ('Computed on parameters that have never been adopted. '
                            'The capital requirement is under revision against '
                            'Regulation 4 of the Insurance Industry Regulations '
                            '2019, so this figure cannot be signed or used in a '
                            'return or board pack.'),
            'drifted_parameters': drifted,
        }
    if drifted:
        return {
            'method_current': False,
            'method_note': ('Superseded — computed on parameter values that have '
                            'since changed. Recompute and save a new snapshot '
                            'before signing.'),
            'drifted_parameters': drifted,
        }
    return {'method_current': True, 'method_note': '', 'drifted_parameters': []}


def _addback_basis(codes, as_of: date, company_id=None) -> list:
    """Every account contributing to the add-back, named, with its amount.

    Manus asked for the workings rather than an assurance, and it was right to:
    printing them is what showed that all five contributors are reinsurance
    share, not negative reserves.
    """
    rows = []
    for code in (codes or []):
        cr = _sum_gl_lines([code], from_date=date(1900, 1, 1), to_date=as_of,
                           side='credit', company_id=company_id)
        dr = _sum_gl_lines([code], from_date=date(1900, 1, 1), to_date=as_of,
                           side='debit', company_id=company_id)
        net = cr - dr
        if net < ZERO:
            rows.append({'account_code': code, 'net': str(net.quantize(Decimal('0.01'))),
                         'added': str((-net).quantize(Decimal('0.01')))})
    return rows


def _negative_reserves_addback(codes, as_of: date, company_id=None) -> Decimal:
    """NBFIRA own-funds adjustment (CFO directive 2026-07-02: "all technical
    provisions, add the negatives only, for NBFIRA").

    A technical provision is a credit-normal liability. If a provision sits in a
    NET DEBIT position its reserve is *negative* — effectively additional own
    funds — so it is ADDED to available capital. Positive provisions are already
    ordinary liabilities reflected in equity, so they are NOT touched here. Per
    account: net = credit − debit; when net < 0, add abs(net)."""
    if not codes:
        return ZERO
    addback = ZERO
    for code in codes:
        cr = _sum_gl_lines([code], from_date=date(1900, 1, 1), to_date=as_of,
                           side='credit', company_id=company_id)
        dr = _sum_gl_lines([code], from_date=date(1900, 1, 1), to_date=as_of,
                           side='debit', company_id=company_id)
        net = cr - dr            # liability net (credit-normal)
        if net < ZERO:
            addback += (-net)    # negative reserve → add its absolute value
    return addback


def _total_equity_at(as_of: date, company_id=None) -> Decimal:
    """Pull total equity (incl. negative reserves) from the balance-sheet
    builder, scoped to one entity. Capital = own funds of the LICENSED insurer,
    so this must be the entity's equity — its retained-earnings losses and
    negative reserves included — NOT the consolidated group equity, which
    dilutes them away (CFO 2026-06-11)."""
    from reporting.reports import build_balance_sheet
    bs = build_balance_sheet(as_of, company_id=company_id)
    raw = bs.get('totals', {}).get('total_equity', '0.00')
    return _decimal(raw, ZERO)


def _estimated_next_year_opex(as_of: date, company_id=None) -> dict:
    """The opex figure Regulation 4 multiplies by 25%, plus its provenance.

    Reg 4 asks for "the operating expenses estimated for the following year".
    Nobody keys an estimate in monthly, so the default basis is the LAST
    COMPLETED financial year's actual operating expenses, taken from the MA P&L
    — the CFO's frozen management-accounts format, not a fresh account sweep, so
    the capital return and the board pack cannot disagree about what opex means.
    Setting `estimated_annual_opex` overrides it once a budget exists.

    Manus's 2026-08-15 brief put ADIC opex at ~P79.7m and the requirement at
    P19.93m. The ledger does not support that: ADIC standalone opex is P28.24m
    (FY26) and P29.52m (FY25); group-wide is P35.79m. P79.7m is in the region of
    claims + acquisition + opex, which is total expenditure, not operating
    expenditure. Using it would have overstated the requirement roughly
    threefold and shown a false breach. CFO confirmed the ledger basis
    2026-08-15.
    """
    override = _get_param('estimated_annual_opex', '')
    if (override or '').strip():
        return {'amount': _decimal(override),
                'basis': 'manual', 'period': None,
                'note': 'Manually entered estimate for the following year.'}

    from reporting.reports import _fy_end_month_for
    from reporting.ma_pl import build_ma_pl

    import calendar

    fy_end_month = _fy_end_month_for(company_id)
    # Last COMPLETED financial year on or before as_of. With a June year end and
    # as_of in Aug 2026 that is 1 Jul 2025 – 30 Jun 2026.
    end_year = as_of.year if as_of.month > fy_end_month else as_of.year - 1
    to_date = date(end_year, fy_end_month,
                   calendar.monthrange(end_year, fy_end_month)[1])
    start_month = fy_end_month % 12 + 1
    start_year = end_year - 1 if start_month != 1 else end_year
    from_date = date(start_year, start_month, 1)

    try:
        ma = build_ma_pl(from_date, to_date, company_id=company_id)
        raw = ma.get('totals', {}).get('total_operating_expenses')
    except Exception:                      # a reporting failure must not publish a wrong requirement
        return {'amount': None, 'basis': 'unavailable', 'period': None,
                'note': 'The management-accounts P&L could not be built, so no '
                        'opex basis is available and no requirement is shown.'}
    if raw is None:
        return {'amount': None, 'basis': 'unavailable', 'period': None,
                'note': 'The management-accounts P&L returned no operating-expense total.'}
    # The MA P&L signs expenses negative; the requirement needs the magnitude.
    return {
        'amount': _decimal(raw).copy_abs(),
        'basis': 'last_completed_financial_year',
        'period': f'{from_date} to {to_date}',
        'note': ('Actual operating expenses for the last completed financial year, '
                 'from the MA P&L, used as the estimate for the following year. '
                 'Set the estimated_annual_opex parameter to override with a budget.'),
    }


def compute_capital_check(as_of: date, company_id=None) -> dict:
    """
    Compute the live capital-adequacy ratio at *as_of*.

    Returns a dict with full breakdown — designed to be stored verbatim in
    RegulatoryCapitalSnapshot.components for audit.
    """
    # Parameters
    minimum_floor   = _decimal(_get_param('minimum_capital_floor', '5000000'))
    opex_factor     = _decimal(_get_param('opex_factor',           '0.25'))
    compliant_thr   = _decimal(_get_param('compliant_threshold',   '1.25'))
    margin_thr      = _decimal(_get_param('margin_threshold',      '1.00'))
    gwp_codes       = _csv_codes(_get_param('gwp_account_codes',          '4100'))
    intangible_codes = _csv_codes(_get_param('intangibles_account_codes', ''))

    # Available capital — entity own funds (equity) less intangibles, scoped to
    # the licensed entity (company_id).
    total_equity   = _total_equity_at(as_of, company_id)
    intangibles    = _sum_gl_lines(intangible_codes,
                                   from_date=date(1900, 1, 1), to_date=as_of,
                                   side='debit', company_id=company_id) - \
                     _sum_gl_lines(intangible_codes,
                                   from_date=date(1900, 1, 1), to_date=as_of,
                                   side='credit', company_id=company_id)
    available_capital = total_equity - intangibles

    # The reinsurers' share of technical provisions is NO LONGER added to own
    # funds. It used to be, under the CFO directive of 2026-07-02 ("all
    # technical provisions, add the negatives only"), which was written before
    # anyone had read Schedule 1. Schedule 1 lists the allowed assets of a
    # general insurer at items 8.1–8.11 and neither "reinsurance recoverable"
    # nor "reinsurers' share of technical provisions" appears in it; printing
    # the workings showed that all five contributing accounts are reinsurance
    # share, so the whole P14.15m add-back was inadmissible. It is still
    # COMPUTED and disclosed below — the figure has to stay visible for the
    # audit trail and for an item 8.11(d) application — but it no longer
    # inflates the capital base. CFO confirmed the removal 2026-08-15,
    # overriding the 2026-07-02 directive.
    reserve_codes     = _csv_codes(_get_param('technical_provision_account_codes', ''))
    reinsurance_share = _negative_reserves_addback(reserve_codes, as_of, company_id)

    # Requirement — Insurance Industry Regulations 2019 (S.I. 68 of 2019) reg 4:
    # the higher of P5,000,000 or 25% of the operating expenses estimated for
    # the following year. No premium factor, no claims factor: neither appears
    # anywhere in Botswana insurance law, and the 0.18 / 0.26 that produced the
    # withdrawn 612% were invented.
    opex = _estimated_next_year_opex(as_of, company_id)
    opex_amount = opex['amount']
    if opex_amount is None:
        opex_required = None
        required_capital = None
    else:
        opex_required = (opex_amount * opex_factor).quantize(Decimal('0.01'))
        required_capital = max(minimum_floor, opex_required)

    # GWP is no longer part of the requirement. It is still reported because the
    # NBFIRA schedules quote it — but note the mapping is known-wrong (account
    # 4100 carries no posted activity; ADIC's premium revenue sits in the 1000xx
    # series), so treat this line as indicative until the codes are corrected.
    twelve_months_ago = date(as_of.year - 1, as_of.month, 1) if as_of.month > 1 else date(as_of.year - 1, 12, 1)
    gwp_total = _sum_gl_lines(gwp_codes, from_date=twelve_months_ago, to_date=as_of, side='credit', company_id=company_id)
    gwp_total -= _sum_gl_lines(gwp_codes, from_date=twelve_months_ago, to_date=as_of, side='debit', company_id=company_id)

    # CAR + status
    if not required_capital:
        car = None
        status = 'under_revision'
    else:
        car = (available_capital / required_capital).quantize(Decimal('0.0001'))
        if car >= compliant_thr:
            status = 'compliant'
        elif car >= margin_thr:
            status = 'margin'
        else:
            status = 'breach'

    workings = {
        'entity_scoped':      bool(company_id),
        'total_equity':       str(total_equity.quantize(Decimal('0.01'))),
        'intangibles':        str(intangibles.quantize(Decimal('0.01'))),
        'reinsurance_share_excluded': str(reinsurance_share.quantize(Decimal('0.01'))),
        'reinsurance_share_basis': _addback_basis(reserve_codes, as_of, company_id),
        'reinsurance_share_note': (
            'Schedule 1 of the Regulations lists the allowed assets of a general '
            'insurer at items 8.1 to 8.11. "Reinsurance recoverable" and '
            '"reinsurers\' share of technical provisions" appear nowhere in it. '
            'Every account below is a reinsurance share, so it is EXCLUDED from '
            'own funds. It would need an express approval under item 8.11(d) to '
            'count.'),
        'opex_basis_amount':  str(opex_amount.quantize(Decimal('0.01'))) if opex_amount is not None else None,
        'opex_basis':         opex['basis'],
        'opex_basis_period':  opex['period'],
        'opex_basis_note':    opex['note'],
        'opex_factor':        str(opex_factor),
        'opex_required':      str(opex_required) if opex_required is not None else None,
        'minimum_floor':      str(minimum_floor),
        'binding_constraint': (None if required_capital is None else
                               'minimum_floor' if required_capital == minimum_floor
                               else 'opex'),
        'gwp_12m':            str(gwp_total.quantize(Decimal('0.01'))),
        'gwp_mapping_warning': (
            'gwp_account_codes is set to %s. Account 4100 carries no posted '
            'activity for ADIC; premium revenue is in the 1000xx series. This '
            'line does not feed the capital requirement under reg 4, but it is '
            'wrong wherever it is displayed.' % ','.join(gwp_codes)
        ) if gwp_codes == ['4100'] else None,
    }

    # ── SUPPRESSED UNTIL THE PARAMETERS ARE VERIFIED ────────────────────────
    # Manus read the primary legislation on 2026-08-10. The formula below is NOT
    # the Botswana test. Insurance Industry Regulations 2019 (S.I. 68 of 2019)
    # Regulation 4: the minimum capital target is the HIGHER of P5,000,000 or
    # 25% of the operating expenses estimated for the following year. There is no
    # premium factor and no claims factor anywhere in Botswana insurance law —
    # the 0.18 and 0.26 are inventions. On the corrected basis cover is between
    # 0.83x and 1.54x, not the 612% this screen showed.
    #
    # Quarterly statutory returns fall due within thirty days of quarter end and
    # the annual return carries an approved person's report. A figure reading
    # 612% when it is really 0.83 must not reach either, so no ratio is published
    # while any parameter is still an unverified placeholder. This clears itself
    # the moment the CFO saves a verified parameter — it is not a hard-coded flag.
    parameters = {
        'minimum_capital_floor':       str(minimum_floor),
        'opex_factor':                 str(opex_factor),
        'compliant_threshold':         str(compliant_thr),
        'margin_threshold':            str(margin_thr),
        'gwp_account_codes':           gwp_codes,
        'intangibles_account_codes':   intangible_codes,
    }

    unverified = _unverified_parameters()
    if unverified or required_capital is None:
        # The requirement and every working are still returned — Manus's brief
        # asked for the audit trail before release, and a withheld ratio over a
        # blank page tells the CFO nothing about whether the rebuild is right.
        # What stays withheld is the RATIO, because publishing one is what makes
        # a figure look signable.
        return {
            'as_of':                str(as_of),
            'available_capital':    str(available_capital.quantize(Decimal('0.01'))),
            'required_capital':     None,
            'capital_adequacy_ratio': None,
            'car_percent':          None,
            'status':               'under_revision',
            'status_note': ('Under revision — not for regulatory or board use. '
                            'The requirement below is computed on Regulation 4 of '
                            'the Insurance Industry Regulations 2019 (higher of '
                            'P5,000,000 or 25% of next year\'s estimated operating '
                            'expenses), but no ratio is published until the CFO '
                            'puts an effective date on each parameter.'),
            'unverified_parameters': unverified,
            # Indicative only, and named so. This is the recalculation Manus
            # asked for; it is deliberately NOT `capital_adequacy_ratio`, so no
            # caller can pick it up and treat it as the adopted figure.
            'indicative_required_capital': (
                str(required_capital.quantize(Decimal('0.01')))
                if required_capital is not None else None),
            'indicative_car_percent': (
                str((car * Decimal('100')).quantize(Decimal('0.01')))
                if car is not None else None),
            'indicative_status': status if required_capital is not None else 'under_revision',
            'components': workings,
            'parameters': parameters,
        }

    return {
        'as_of':                str(as_of),
        'available_capital':    str(available_capital.quantize(Decimal('0.01'))),
        'required_capital':     str(required_capital.quantize(Decimal('0.01'))),
        'capital_adequacy_ratio': str(car),
        'car_percent':          str((car * Decimal('100')).quantize(Decimal('0.01'))),
        'status':               status,
        'components':           workings,
        'parameters':           parameters,
    }


def take_snapshot(*, as_of: date, user: User, notes: str = '') -> RegulatoryCapitalSnapshot:
    """Compute and persist a snapshot. Status starts as DRAFT.

    Capital adequacy is per LICENSED ENTITY, not consolidated. Only ADIC (the
    general insurer) is NBFIRA-regulated, so the snapshot is always scoped to
    ADIC — matching CapitalCheckView's default (CFO 2026-06-11). A group-wide
    figure (company=None) would dilute ADIC's negative reserves and misstate
    the CAR.
    """
    from core.models import Company
    company_id = (Company.objects.filter(code='ADIC')
                  .values_list('id', flat=True).first())
    payload = compute_capital_check(as_of, company_id=company_id)
    # No snapshot while the ratio is withheld. Decimal(None) would raise a bare
    # TypeError and the take view has no handler, so the button 500s (Fable
    # 2026-08-10). It fails closed either way — no wrong snapshot can persist —
    # but a designed 400 carrying the reason is not the same as a crash.
    if payload.get('status') == 'under_revision':
        from rest_framework.exceptions import ValidationError
        raise ValidationError(payload['status_note'])
    snap = RegulatoryCapitalSnapshot.objects.create(
        as_of_date=as_of,
        company_id=company_id,
        available_capital=Decimal(payload['available_capital']),
        required_capital=Decimal(payload['required_capital']),
        capital_adequacy_ratio=Decimal(payload['capital_adequacy_ratio']),
        status=payload['status'],
        components=payload,
        notes=notes,
        prepared_by=user,
    )
    snap.save(audit_user=user, audit_description=f'Capital snapshot {as_of}')
    return snap
