"""
Daily digest of operational-level Purchase Order authorisations.

    python manage.py daily_po_authorizations [--dry-run] [--date YYYY-MM-DD]

CFO directive 2026-07-05: the CFO stays in the approval loop for operational
POs at or above the P10,000 threshold, but wants a DAILY VISIBILITY email of
every operational (Admin / HR) PO authorised that day — both the FM-only
single-step approvals (< P10,000) and the ones he co-approved — so operational
spend is reviewable without him being a bottleneck on the small ones.

Scheduled 09:00 (before the 09:00 payment cut-off):
    0 9 * * *  cd /opt/alpha-finance && docker compose --env-file /etc/alpha-finance/.env \
               exec -T backend python manage.py daily_po_authorizations
"""
from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from procurement.models import PurchaseOrder

OPERATIONAL_DEPTS = [PurchaseOrder.Department.ADMIN, PurchaseOrder.Department.HR]
NAVY, ORANGE = '#0D1B2A', '#F4A623'


class Command(BaseCommand):
    help = "Email a daily summary of operational-level PO authorisations (Admin/HR)."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Print the HTML instead of sending.')
        parser.add_argument('--date', default=None,
                            help='YYYY-MM-DD to report on (default: today).')

    def handle(self, *args, **opts):
        import datetime as _dt
        day = (_dt.date.fromisoformat(opts['date']) if opts['date']
               else timezone.localdate())

        pos = list(
            PurchaseOrder.objects
            .filter(status=PurchaseOrder.Status.APPROVED,
                    department__in=OPERATIONAL_DEPTS,
                    fm_approved_at__date=day)
            .select_related('supplier', 'fm_approved_by', 'cfo_approved_by')
            .order_by('-total_bwp')
        )

        if not pos:
            self.stdout.write(f"No operational PO authorisations for {day}.")
            return

        total = sum((p.total_bwp or Decimal('0')) for p in pos)
        fm_only = [p for p in pos if not p.cfo_approved_by_id]

        rows = []
        for p in pos:
            path = ('FM only' if not p.cfo_approved_by_id
                    else f'FM + CFO ({p.cfo_approved_by.get_full_name() or p.cfo_approved_by.username})')
            fm = (p.fm_approved_by.get_full_name() or p.fm_approved_by.username) if p.fm_approved_by_id else '—'
            rows.append(
                f'<tr>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;">{p.po_number}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;">{(p.supplier.name if p.supplier_id else "—")}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;">{p.get_department_display()}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;text-align:right;">P{(p.total_bwp or 0):,.2f}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;">{fm}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;">{path}</td>'
                f'</tr>')

        html = f"""<div style="max-width:680px;margin:0 auto;font-family:'Book Antiqua',Palatino,Georgia,serif;color:#1F2937;">
  <div style="background:{NAVY};border-radius:12px 12px 0 0;padding:18px 24px;">
    <div style="color:#fff;font-size:18px;font-weight:bold;">Operational PO authorisations — {day:%d %b %Y}</div>
    <div style="color:{ORANGE};font-size:13px;margin-top:3px;">{len(pos)} PO(s) authorised · {len(fm_only)} at FM level only · P{total:,.2f} total</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-top:none;border-radius:0 0 12px 12px;padding:20px 24px;">
    <p style="margin:0 0 12px;font-size:14px;">Operational (Admin / HR) purchase orders authorised today. POs at or above P10,000 still carry your CFO sign-off; the rest are Finance-Manager single-step.</p>
    <div style="overflow-x:auto;">
    <table style="border-collapse:collapse;width:100%;font-size:13px;">
      <thead><tr style="background:#F9FAFB;text-align:left;">
        <th style="padding:6px 10px;">PO</th><th style="padding:6px 10px;">Supplier</th>
        <th style="padding:6px 10px;">Dept</th><th style="padding:6px 10px;text-align:right;">Amount</th>
        <th style="padding:6px 10px;">FM approver</th><th style="padding:6px 10px;">Path</th>
      </tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    </div>
  </div>
  <div style="text-align:center;color:#9CA3AF;font-size:11px;padding:12px;">Alpha Direct Insurance Company (Pty) Ltd · omni</div>
</div>"""

        if opts['dry_run']:
            self.stdout.write(html)
            self.stdout.write(f"\n[dry-run] {len(pos)} PO(s), P{total:,.2f} — not sent.")
            return

        from core.notifications import send_html_with_cfo_cc
        sent = send_html_with_cfo_cc(
            subject=f"Operational PO authorisations — {day:%d %b %Y} ({len(pos)} PO, P{total:,.0f})",
            html=html,
            to=['excoboard@alphadirect.co.bw'],  # CFO/EXCO inbox as primary recipient
            text_fallback=f"{len(pos)} operational POs authorised on {day} totalling P{total:,.2f}; "
                          f"{len(fm_only)} at FM level only.",
        )
        self.stdout.write(self.style.SUCCESS(f"Sent operational-PO digest for {day} ({len(pos)} PO). result={sent}"))
