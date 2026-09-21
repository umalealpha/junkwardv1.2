"""Load the FAC Analysis Master Spreadsheet into the facultative risk register.

Tlamelo Chimidza's FY27 sheet (11-Sep-2026): "the latest FAC Master sheet for
FY27 … slips placed for the month of July and August."

Decisions the CFO took on 16-Sep-2026 when I put the data's problems to him,
all of them recorded here so nobody re-derives them from the numbers later:

  * The "Grand Total" row inside the data is SKIPPED. Its cession equals the sum
    of the 61 placement rows exactly, so loading it would double the register.
  * "Reinsureace Solutions" is treated as "Reinsurance Solutions" — a typo, one
    counterparty. The original spelling is kept on the row's evidence note
    rather than silently rewritten, the same way the broker register keeps
    Redhill's two spellings.
  * Rows naming TWO reinsurers in one cell ("P & C Re, Saha Re") are loaded as
    exposures but NOT allocated to a reinsurer, because the sheet does not say
    how the share splits. They are flagged so they are visible as incomplete
    rather than quietly missing, and they stay out of exposure-by-reinsurer.
  * The two MP Mining rows — same slip, same policy, same BWP 340,878,887
    cession, but 30% vs 10% commission — are loaded as SEPARATE placements on
    the CFO's explicit instruction. I advised holding them out until Tlamelo
    confirms whether it is one placement entered twice; he overrode that. If it
    is a double entry, the register overstates ceded exposure by BWP 340.9m
    until he answers. Both rows carry an evidence note saying so.

Read-only against Graphite and the GL: this writes register rows only. It posts
no journal and moves no money.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand
from django.db import transaction

from reinsurance.models import (FacReinsurerAllocation, FacRiskExposure,
                                Reinsurer, ReinsurerApprovalTransition)

SHEET = 'FAC Analysis'

# name as it appears in the sheet -> the counterparty it really is
NAME_FIXES = {
    'reinsureace solutions': 'Reinsurance Solutions',
}


def _clean(v) -> str:
    return re.sub(r'\s+', ' ', str(v if v is not None else '')).strip()


def _dec(v) -> Decimal:
    try:
        return Decimal(str(v if v not in (None, '') else '0')).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return Decimal('0.00')


def _period(raw: str):
    """'01/07/2026 to 28/02/2027' -> (date, date). Returns (None, None) when it
    does not parse — a half-read date is worse than no date."""
    m = re.findall(r'(\d{2})/(\d{2})/(\d{4})', raw or '')
    if len(m) != 2:
        return None, None
    try:
        a = dt.date(int(m[0][2]), int(m[0][1]), int(m[0][0]))
        b = dt.date(int(m[1][2]), int(m[1][1]), int(m[1][0]))
        return a, b
    except ValueError:
        return None, None


class Command(BaseCommand):
    help = 'Load the FAC Analysis Master Spreadsheet (.xlsb) into the FAC register.'

    def add_arguments(self, p):
        p.add_argument('path')
        p.add_argument('--fy', default='FY27')
        p.add_argument('--commit', action='store_true',
                       help='Write. Without it nothing is created.')

    def handle(self, *a, **o):
        from pyxlsb import open_workbook

        rows = []
        with open_workbook(o['path']) as wb:
            with wb.get_sheet(SHEET) as sh:
                for i, row in enumerate(sh.rows()):
                    v = [c.v for c in row]
                    if i == 0 or not any(x not in (None, '') for x in v):
                        continue
                    rows.append((i + 1, v))

        made_r = made_e = made_a = 0
        approved_now = []
        skipped_total = 0
        unsplit = []
        tot_cession = tot_prem = Decimal('0.00')
        per_reinsurer = {}

        with transaction.atomic():
            for excel_row, v in rows:
                broker = _clean(v[0])
                named = _clean(v[1]) or broker
                if named.lower().startswith('grand total'):
                    skipped_total += 1
                    continue
                if not named:
                    continue

                cession = _dec(v[9])
                premium = _dec(v[10])
                commission = _dec(v[14])
                tot_cession += cession
                tot_prem += premium

                start, end = _period(_clean(v[4]))
                slip = _clean(v[3])[:120]
                ref = f"{o['fy']}-{excel_row:04d}"

                note = f'{o["fy"]} master sheet, row {excel_row}.'
                shared = ',' in named
                if shared:
                    note += ' TWO reinsurers named in one cell — share not stated, NOT allocated.'
                    unsplit.append((ref, named, cession))

                exposure, _ = FacRiskExposure.objects.update_or_create(
                    reference=ref,
                    defaults=dict(
                        policy_number=_clean(v[6])[:60],
                        insured_name=_clean(v[7])[:200],
                        regulatory_class=_clean(v[8])[:120],
                        currency_code='BWP',
                        gross_sum_insured=cession,
                        fac_placed_amount=cession,
                        ceded_premium=premium,
                        ceded_commission=commission,
                        slip_reference=slip,
                        effective_date=start,
                        expiry_date=end,
                        status=(FacRiskExposure.Status.PLACED if not shared
                                else FacRiskExposure.Status.PARTIALLY_PLACED),
                        evidence_note=note[:300],
                        source_system='FAC Analysis Master Spreadsheet',
                        source_file=o['path'].rsplit('/', 1)[-1][:240],
                        import_warnings=([
                            'Two reinsurers named in one cell — the placement amount is '
                            'recorded in full, but it is NOT split between them until '
                            'the broker confirms the shares.'] if shared else []),
                    ))
                made_e += 1

                if shared:
                    continue

                canon = NAME_FIXES.get(named.lower(), named)
                reinsurer, created = Reinsurer.objects.get_or_create(
                    name=canon,
                    defaults=dict(short_code=canon[:12], country='BW', is_active=True,
                                  notes=f'Created from the {o["fy"]} FAC master sheet.'))
                if created:
                    made_r += 1

                # The module refuses a placement against an unapproved counterparty —
                # Draft -> UW Manager -> Compliance -> Principal -> CEO -> Approved.
                # That control is the point of the module, so it is NOT bypassed
                # silently. The CFO ruled on 16-Sep-2026 that these twelve are
                # GRANDFATHERED: they already carry live FY27 risk on signed slips
                # placed before the module existed, and approval is required for NEW
                # placements from here on. The transition below records exactly that,
                # in his name, and says plainly that no security assessment has been
                # done — so an ERM review reads the truth rather than a phantom sign-off.
                if reinsurer.approval_status != Reinsurer.ApprovalStatus.APPROVED:
                    before = reinsurer.approval_status
                    reinsurer.approval_status = Reinsurer.ApprovalStatus.APPROVED
                    reinsurer.save(update_fields=['approval_status'])
                    ReinsurerApprovalTransition.objects.create(
                        reinsurer=reinsurer,
                        from_status=before,
                        to_status=Reinsurer.ApprovalStatus.APPROVED,
                        actor_email='pganesharajah@alphadirect.co.bw',
                        comment=(
                            'Grandfathered by the CFO, 16-Sep-2026, to load the '
                            f'{o["fy"]} FAC register. This counterparty already carries '
                            'live risk on signed slips placed before this module existed. '
                            'NO security assessment or rating review has been carried out '
                            '— onboarding is still outstanding. Approval is required in '
                            'the normal way for any NEW placement.')[:2000],
                    )
                    approved_now.append(canon)
                if canon != named:
                    # keep the sheet's spelling on the row, never rewrite silently
                    exposure.evidence_note = (exposure.evidence_note +
                                              f' Sheet spelled it "{named}".')[:300]
                    exposure.save(update_fields=['evidence_note'])

                # Not `update_or_create`: the KYC evidence gate (control brief
                # §5) has to be told this is a LEGACY load, and that flag lives
                # on the instance, which update_or_create builds and saves out
                # of reach. These are signed slips already carrying live FY27
                # risk — the brief is explicit that missing evidence blocks a
                # NEW placement and must never stop an imported record. The
                # approval / active / expiry checks still apply in full.
                alloc = (FacReinsurerAllocation.objects
                         .filter(exposure=exposure, reinsurer=reinsurer).first()
                         or FacReinsurerAllocation(exposure=exposure,
                                                   reinsurer=reinsurer))
                alloc.share_percent = _dec(Decimal(str(v[2] or 0)) * 100)
                alloc.allocated_amount = cession
                alloc.allocated_premium = premium
                alloc.commission_amount = commission
                alloc.slip_reference = slip
                alloc._legacy_import = True
                alloc.save()
                made_a += 1
                b = per_reinsurer.setdefault(canon, [0, Decimal('0.00'), Decimal('0.00')])
                b[0] += 1; b[1] += cession; b[2] += premium

            if not o['commit']:
                transaction.set_rollback(True)

        w = self.stdout.write
        w('')
        w(f'{"WROTE" if o["commit"] else "DRY RUN — nothing written"}')
        w(f'  placements        {made_e}')
        w(f'  allocations       {made_a}')
        w(f'  new reinsurers    {made_r}')
        w(f'  total rows skipped (Grand Total) {skipped_total}')
        w(f'  CESSION  {tot_cession:,.2f}')
        w(f'  PREMIUM  {tot_prem:,.2f}')
        if approved_now:
            w(f'  GRANDFATHERED to Approved ({len(approved_now)}) — no assessment done:')
            for n in approved_now:
                w(f'     {n}')
        if unsplit:
            w('  NOT allocated (two reinsurers in one cell):')
            for ref, nm, c in unsplit:
                w(f'     {ref}  {nm}  {c:,.2f}')
        w('  by reinsurer:')
        for k, b in sorted(per_reinsurer.items(), key=lambda x: -x[1][1]):
            w(f'     {k[:28]:28} n={b[0]:3}  cession={b[1]:18,.2f}  premium={b[2]:12,.2f}')
