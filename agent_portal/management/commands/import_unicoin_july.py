"""
import_unicoin_july — load a UniCoin Instant Insurance commission pack (the
monthly .xlsx set Bharath sends) into a CommissionCycle, then build payslips.

The pack is the FINISHED run (paid figures + statuses already computed), not the
raw source rows, so we import the results directly rather than re-run the engine
(CFO 2026-07-27: "no worry about 78,540 I uploaded only" — his figures are the
truth). Payable lines come from the master 'Payable (per agent)' matrix so the
cycle total ties to the pack's Grand Total exactly; the per-policy detail tabs
add the NOT-PAID rows (with reasons) for transparency only.

Usage:
    python manage.py import_unicoin_july --dir "<pack folder>" \
        [--label "UniCoin Instant Insurance — July 2026"] \
        [--start 2026-06-23 --end 2026-07-24] [--expect 78540.44]
"""
from __future__ import annotations

import glob
import os
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from agent_portal.models import Agent, CommissionCycle, CommissionLine
from agent_portal.payslip_service import build_payslips_for_cycle

ZERO = Decimal('0.00')

# master 'Payable (per agent)' column header -> engine stream key
_PAYABLE_COLS = {
    'new sales': 'new_sales_mis',
    'conversion': 'conversion',
    'collection': 'collection',
    'motor comp': 'motor',
    'kyc & banking': 'kyc_claims',
    'backlog': 'backlog',
    'incentives': 'incentives',
}
# detail-tab filename fragment -> engine stream key (for NOT-PAID transparency)
_DETAIL_FILES = {
    'New_Sales': 'new_sales_mis',
    'RealPay_Conversion': 'conversion',
    'Collection': 'collection',
    'Motor_Comprehensive': 'motor',
    'Backlog': 'backlog',
    'Reprocessed': 'reprocessed',
}


def _dec(v) -> Decimal:
    if v is None or v == '':
        return ZERO
    try:
        return Decimal(str(v).replace(',', '').replace('BWP', '').strip() or '0')
    except (InvalidOperation, ValueError):
        return ZERO


def _norm(s) -> str:
    return ('' if s is None else str(s)).strip()


class Command(BaseCommand):
    help = 'Import a UniCoin Instant Insurance commission pack into a cycle + build payslips.'

    def add_arguments(self, p):
        p.add_argument('--dir', required=True, help='Folder containing the pack .xlsx files')
        p.add_argument('--label', default='UniCoin Instant Insurance — July 2026')
        p.add_argument('--start', default='2026-06-23')
        p.add_argument('--end', default='2026-07-24')
        p.add_argument('--expect', default='78540.44', help='Expected grand total (BWP) to reconcile against')

    def handle(self, *args, **o):
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            raise CommandError('openpyxl is required to read the pack.')
        folder = o['dir']
        if not os.path.isdir(folder):
            raise CommandError(f'Not a folder: {folder}')

        master = self._find(folder, 'Unicoin Commissions', 'Payable')
        if not master:
            master = self._find_any_payable(folder)
        if not master:
            raise CommandError("Could not find a workbook with a 'Payable (per agent)' sheet.")

        start = date.fromisoformat(o['start'])
        end = date.fromisoformat(o['end'])
        with transaction.atomic():
            cycle, _ = CommissionCycle.objects.get_or_create(
                label=o['label'], defaults={'start_date': start, 'end_date': end})
            if cycle.status != CommissionCycle.Status.OPEN:
                raise CommandError(f'Cycle "{cycle.label}" is {cycle.get_status_display()} — reopen before re-import.')
            cycle.start_date, cycle.end_date = start, end
            cycle.save(update_fields=['start_date', 'end_date', 'updated_at'])
            CommissionLine.objects.filter(cycle=cycle).delete()

            payable_total = self._import_payable(master, cycle)
            not_paid_n = self._import_not_paid(folder, cycle)
            reg = self._load_registry(folder)

            # Reconcile against the workbook's OWN Grand Total BEFORE building any
            # payslips (Fable review 2026-07-27): a mismatch must fail loudly and
            # roll the import back, never print a warning and exit 0 with slips
            # already issued. Fall back to --expect only if no Grand Total cell.
            sheet_total = self._sheet_grand_total(master[0])
            expect = sheet_total if sheet_total is not None else _dec(o['expect'])
            src = 'workbook Grand Total' if sheet_total is not None else '--expect'
            if abs(payable_total - expect) > Decimal('1.00'):
                raise CommandError(
                    f'Reconciliation FAILED: payable lines total BWP {payable_total:,.2f} '
                    f'!= {src} BWP {expect:,.2f}. Nothing imported. Fix the pack and re-run.')

        # Payslips (own transaction inside) — only reached when the import ties.
        summary = build_payslips_for_cycle(cycle)
        tie = f'OK (vs {src})'
        self.stdout.write(self.style.SUCCESS(
            f"\nCycle: {cycle.label}  [{cycle.start_date} → {cycle.end_date}]\n"
            f"  Payable lines total : BWP {payable_total:,.2f}   reconcile vs {expect:,.2f}: {tie}\n"
            f"  Not-paid detail rows: {not_paid_n}\n"
            f"  Registry matched    : {reg}\n"
            f"  Payslips            : {summary['payslips']} "
            f"(gross {summary['gross_total_bwp']}, tax {summary['tax_total_bwp']}, net {summary['net_total_bwp']})"
        ))

    # ── helpers ──────────────────────────────────────────────────────────────
    def _find(self, folder, name_frag, sheet_frag):
        import openpyxl
        for path in glob.glob(os.path.join(folder, '*.xlsx')):
            if name_frag.lower() in os.path.basename(path).lower():
                wb = openpyxl.load_workbook(path, data_only=True)
                for ws in wb.worksheets:
                    if sheet_frag.lower() in ws.title.lower():
                        return (path, ws.title)
        return None

    def _find_any_payable(self, folder):
        import openpyxl
        for path in glob.glob(os.path.join(folder, '*.xlsx')):
            if os.path.basename(path).startswith('~$'):
                continue
            try:
                wb = openpyxl.load_workbook(path, data_only=True)
            except Exception:
                continue
            for ws in wb.worksheets:
                if 'payable' in ws.title.lower():
                    return (path, ws.title)
        return None

    def _sheet_grand_total(self, path):
        """Read 'GRAND TOTAL PAYABLE' from any sheet of the master workbook, so
        the import checks itself against the pack's own headline figure."""
        import openpyxl
        try:
            wb = openpyxl.load_workbook(path, data_only=True)
        except Exception:
            return None
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                cells = [_norm(c) for c in row]
                for i, c in enumerate(cells):
                    if 'grand total' in c.lower():
                        # take the first numeric cell to the right on this row
                        for v in row[i + 1:]:
                            d = _dec(v)
                            if d > 0:
                                return d
        return None

    def _import_payable(self, master, cycle) -> Decimal:
        """Master 'Payable (per agent)' matrix -> payable CommissionLines."""
        import openpyxl
        path, sheet = master
        ws = openpyxl.load_workbook(path, data_only=True)[sheet]
        rows = list(ws.iter_rows(values_only=True))
        # find the header row (contains 'Agent' + 'TOTAL')
        hdr_i = next((i for i, r in enumerate(rows)
                      if r and _norm(r[0]).lower() == 'agent'), None)
        if hdr_i is None:
            raise CommandError("No 'Agent' header row in the Payable sheet.")
        header = [_norm(c).lower() for c in rows[hdr_i]]
        col_stream = {}
        for ci, h in enumerate(header):
            for frag, key in _PAYABLE_COLS.items():
                if h.startswith(frag):
                    col_stream[ci] = key
        total = ZERO
        bulk = []
        for r in rows[hdr_i + 1:]:
            name = _norm(r[0])
            if not name or name.lower().startswith(('total', 'grand')):
                continue
            agent, _ = Agent.objects.get_or_create(name=name)
            for ci, key in col_stream.items():
                amt = _dec(r[ci]) if ci < len(r) else ZERO
                if amt <= 0:
                    continue
                total += amt
                bulk.append(CommissionLine(
                    cycle=cycle, agent=agent, stream=key,
                    basis=amt, commission=amt, payable=True,
                    reason='Paid — July run', policy_ref='', source=[],
                ))
        CommissionLine.objects.bulk_create(bulk)
        return total

    def _import_not_paid(self, folder, cycle) -> int:
        """Detail tabs -> NOT-PAID lines only (transparency, zero commission)."""
        import openpyxl
        n = 0
        bulk = []
        for path in glob.glob(os.path.join(folder, '*.xlsx')):
            base = os.path.basename(path)
            key = next((k for frag, k in _DETAIL_FILES.items() if frag in base), None)
            if not key:
                continue
            wb = openpyxl.load_workbook(path, data_only=True)
            ws = wb['Detail'] if 'Detail' in wb.sheetnames else wb.worksheets[0]
            rows = list(ws.iter_rows(values_only=True))
            hdr_i = next((i for i, r in enumerate(rows)
                          if r and _norm(r[0]).lower() == 'agent'), None)
            if hdr_i is None:
                continue
            for r in rows[hdr_i + 1:]:
                name = _norm(r[0])
                if not name or name.lower().startswith(('total', 'grand')):
                    continue
                status = _norm(r[6]) if len(r) > 6 else ''
                if status.upper().startswith('PAID'):
                    continue  # paid rows already come from the Payable matrix
                agent, _ = Agent.objects.get_or_create(name=name)
                bulk.append(CommissionLine(
                    cycle=cycle, agent=agent, stream=key,
                    basis=_dec(r[3]) if len(r) > 3 else ZERO,
                    commission=ZERO, payable=False,
                    reason=(_norm(r[7]) if len(r) > 7 else 'Not paid')[:200],
                    policy_ref=(_norm(r[1]) if len(r) > 1 else '')[:60], source=[],
                ))
                n += 1
        CommissionLine.objects.bulk_create(bulk)
        return n

    def _load_registry(self, folder) -> int:
        """Tax-compiler workbook: 'Tax Compiler' -> ref_id; 'Agent ID' -> contact."""
        import openpyxl
        path = next((p for p in glob.glob(os.path.join(folder, '*.xlsx'))
                     if 'Tax Calculation' in os.path.basename(p)
                     or 'Commission Compiler' in os.path.basename(p)), None)
        if not path:
            return 0
        wb = openpyxl.load_workbook(path, data_only=True)
        # name -> ref_id (from Tax Compiler)
        ref_by_name = {}
        if 'Tax Compiler' in wb.sheetnames:
            for r in wb['Tax Compiler'].iter_rows(min_row=3, values_only=True):
                if r and r[0] not in (None, '') and _norm(r[1]):
                    ref_by_name[_norm(r[1]).lower()] = _norm(r[0])
        # name -> (email, phone, agency) from 'Agent ID'
        contact = {}
        if 'Agent ID' in wb.sheetnames:
            for r in wb['Agent ID'].iter_rows(min_row=2, values_only=True):
                nm = _norm(r[0]) if r else ''
                if not nm:
                    continue
                contact[nm.lower()] = (
                    _norm(r[2]) if len(r) > 2 else '',   # email
                    _norm(r[4]) if len(r) > 4 else '',   # phone
                    _norm(r[1]) if len(r) > 1 else '',   # agency
                )
        matched = 0
        for agent in Agent.objects.all():
            k = agent.name.lower()
            changed = []
            if k in ref_by_name and not agent.ref_id:
                agent.ref_id = ref_by_name[k][:20]; changed.append('ref_id')
            if k in contact:
                email, phone, agency = contact[k]
                if email and '@' in email and not agent.email:
                    agent.email = email[:254]; changed.append('email')
                if phone and not agent.phone:
                    agent.phone = phone[:40]; changed.append('phone')
                if agency and not agent.agency:
                    agent.agency = agency[:120]; changed.append('agency')
            if changed:
                agent.save(update_fields=changed + ['updated_at'])
                matched += 1
        return matched
