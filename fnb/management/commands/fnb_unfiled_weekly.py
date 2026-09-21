"""
fnb_unfiled_weekly — the money that left the bank with no record in Omni.

CFO 2026-08-26. Measuring a month of FNB confirmations before this was built:
48 payments, BWP 2,585,588.37, and only 3 matched an Omni payment request. The
proofs are now filed where they can be; this is the other half — a weekly list of
paid bank payments Omni has no home for, so the gap is visible instead of assumed.

The CFO asked to see it weekly and decide later, so this REPORTS and never acts.

  python manage.py fnb_unfiled_weekly --dry-run
  python manage.py fnb_unfiled_weekly --days 7
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from fnb.models import FNBProofOfPayment as POP

NAVY, ORANGE = '#0D1B2A', '#F4A623'


class Command(BaseCommand):
    help = 'Email the CFO the bank payments Omni has no record of.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--to', default='')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **o):
        since = timezone.now() - timedelta(days=o['days'])
        qs = POP.objects.filter(paid=True, received_at__gte=since)

        unfiled = list(qs.filter(state__in=[POP.State.UNFILED, POP.State.PROPOSED])
                         .order_by('-amount'))
        filed_n = qs.filter(state__in=[POP.State.FILED_CLAIM,
                                       POP.State.FILED_REQUEST]).count()
        total = qs.aggregate(s=Sum('amount'))['s'] or 0
        unfiled_total = sum(p.amount for p in unfiled)

        self.stdout.write(f'{o["days"]}d: {qs.count()} paid, BWP {total:,.2f} | '
                          f'filed {filed_n} | unfiled {len(unfiled)} '
                          f'(BWP {unfiled_total:,.2f})')
        if not unfiled:
            self.stdout.write('Nothing unfiled — no email sent.')
            return

        rows = ''.join(
            f'<tr>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eef2f6;'
            f'font-family:Book Antiqua,Georgia,serif;font-size:13px">{p.reference[:70]}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eef2f6;'
            f'font-family:Book Antiqua,Georgia,serif;font-size:13px;text-align:right;'
            f'white-space:nowrap">{p.amount:,.2f}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eef2f6;'
            f'font-family:Book Antiqua,Georgia,serif;font-size:12px;color:#64748b">'
            f'{p.received_at:%d %b}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eef2f6;'
            f'font-family:Book Antiqua,Georgia,serif;font-size:12px;color:#64748b">'
            f'{"match proposed" if p.state == POP.State.PROPOSED else "no record"}</td>'
            f'</tr>' for p in unfiled[:60])

        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;background:#f4f6f8">
<div style="max-width:660px;margin:0 auto;background:#fff">
  <div style="background:{NAVY};padding:20px 24px">
    <div style="color:#fff;font-family:Book Antiqua,Georgia,serif;font-size:11px;
                letter-spacing:2px;text-transform:uppercase;opacity:.7">Alpha Direct Insurance</div>
    <div style="color:{ORANGE};font-family:Book Antiqua,Georgia,serif;font-size:20px;
                font-weight:bold;margin-top:5px">Bank payments with no record in Omni</div>
  </div>
  <div style="padding:24px;font-family:Book Antiqua,Georgia,serif;font-size:14px;
              line-height:1.6;color:#222">
    <p style="margin:0 0 14px">Prathap</p>
    <p style="margin:0 0 14px">
      Last {o['days']} days, First National Bank confirmed
      <b>{qs.count()}</b> payments totalling <b>BWP {total:,.2f}</b>.
      Omni filed a proof against a claim or a payment request for <b>{filed_n}</b> of them.
    </p>
    <p style="margin:0 0 14px">
      <b>{len(unfiled)}</b> — <b>BWP {unfiled_total:,.2f}</b> — left the bank with
      nothing in Omni to file the proof against. The proofs are safely stored either
      way; what is missing is the payment record itself.
    </p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <thead><tr style="background:#f7f9fb">
        <th style="padding:7px 10px;text-align:left;font-size:11px;letter-spacing:1px;
                   text-transform:uppercase;color:#64748b;
                   font-family:Book Antiqua,Georgia,serif">Bank reference</th>
        <th style="padding:7px 10px;text-align:right;font-size:11px;letter-spacing:1px;
                   text-transform:uppercase;color:#64748b;
                   font-family:Book Antiqua,Georgia,serif">BWP</th>
        <th style="padding:7px 10px;text-align:left;font-size:11px;letter-spacing:1px;
                   text-transform:uppercase;color:#64748b;
                   font-family:Book Antiqua,Georgia,serif">Paid</th>
        <th style="padding:7px 10px;text-align:left;font-size:11px;letter-spacing:1px;
                   text-transform:uppercase;color:#64748b;
                   font-family:Book Antiqua,Georgia,serif">Status</th>
      </tr></thead><tbody>{rows}</tbody>
    </table>
    <p style="margin:0 0 14px;color:#64748b;font-size:13px">
      Nothing has been changed or actioned. You asked to watch this weekly before
      deciding whether these should be routed through Omni.
    </p>
    <p style="margin:20px 0 0">Regards,<br><b>Omni</b><br>
      <span style="color:#64748b">Alpha Direct ERP</span></p>
  </div>
</div></body></html>"""

        if o['dry_run']:
            self.stdout.write(self.style.WARNING(
                f'DRY RUN — would email {len(unfiled)} unfiled payment(s), '
                f'html {len(html)} bytes.'))
            return

        from core.notifications import send_html_with_cfo_cc
        to = [o['to']] if o['to'] else ['pganesharajah@alphadirect.co.bw']
        sent = send_html_with_cfo_cc(
            subject=f'{len(unfiled)} bank payments with no record in Omni '
                    f'(BWP {unfiled_total:,.2f})',
            html=html, to=to,
            text_fallback=(f'{len(unfiled)} paid bank payments totalling BWP '
                           f'{unfiled_total:,.2f} have no matching record in Omni.'),
            allow_named_exec=True,
        )
        self.stdout.write(self.style.SUCCESS(f'Sent: {sent}'))
