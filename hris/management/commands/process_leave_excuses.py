"""process_leave_excuses — the Leave Excuse auto-responder (CFO 2026-07-22).

Builds the low/no productive-hours list for a day (hris.leave_excuse_service),
applies the auto-rules (hris.leave_excuse_rules), and — per Rule A (power cut) /
Rule B (tracker with no IT ticket) — records a LeaveExcuseAutoResponse and emails
the employee.

SAFETY GATE: real email is behind env LEAVE_EXCUSE_AUTOSEND (default '0' = OFF).
  OFF → compute the verdict, record the ledger row (sent_at NULL), log
        "would send", send NOTHING.
  ON  → send once, stamp sent_at. Idempotent: one email per employee/date/rule
        (a row whose sent_at is already set is skipped).

The dashboard GET is a separate, read-only surface and never runs this.

  python manage.py process_leave_excuses                 # latest snapshot day
  python manage.py process_leave_excuses --date 2026-07-21
"""
from __future__ import annotations

import datetime

from django.core.management.base import BaseCommand

NAVY, ORANGE, MUT = '#1D3270', '#F47C20', '#6B7280'


def _email_html(name: str, body_text: str) -> str:
    """Wrap the plain rule body in the Alpha Direct house envelope (mirrors
    send_saturday_explain._email_html)."""
    from html import escape
    inner = escape(body_text).replace('\n', '<br>\n')
    return f"""\
<div style="font-family:Georgia,'Book Antiqua',serif;color:{NAVY};max-width:600px;margin:0 auto">
  <div style="background:{NAVY};padding:16px 24px"><span style="color:#fff;font-size:20px;letter-spacing:1px">OMNI</span>
  <span style="color:{ORANGE};font-size:13px;margin-left:8px">Alpha Direct</span></div>
  <div style="padding:24px;font-size:15px;line-height:1.55">{inner}
    <p style="color:{MUT};font-size:12px;margin-top:20px">Automated message from Omni · Alpha Direct.
       If you believe this is wrong, reply and it will be reviewed.</p>
  </div>
</div>"""


class Command(BaseCommand):
    help = ("Leave Excuse auto-responder. Records a verdict + (Rule A/B) emails the "
            "employee. SENDS ONLY when LEAVE_EXCUSE_AUTOSEND=1; otherwise records and "
            "logs 'would send'. Idempotent: one email per employee/date/rule.")

    def add_arguments(self, parser):
        parser.add_argument('--date', default='',
                            help='YYYY-MM-DD; default = the latest Time Doctor snapshot day.')

    def handle(self, *args, **opts):
        from django.utils import timezone as _tz
        from hris import leave_excuse_rules as rules
        from hris.leave_excuse_service import build_day
        from hris.models import LeaveExcuseAutoResponse
        from payroll.models import Employee

        d = None
        if opts['date']:
            try:
                d = datetime.datetime.strptime(opts['date'], '%Y-%m-%d').date()
            except ValueError:
                self.stderr.write(self.style.ERROR('Bad --date; use YYYY-MM-DD.'))
                return

        day = build_day(d)
        if not day['date']:
            self.stdout.write(self.style.WARNING('No Time Doctor snapshot found — nothing to do.'))
            return
        work_date = datetime.date.fromisoformat(day['date'])
        autosend = rules.autosend_enabled()

        recorded = sent = would = skipped = no_email = 0
        for r in day['rows']:
            if r.get('held'):
                # People-data guardrail (CFO 2026-08-01): hours may still be
                # uploading — never chase this person for today.
                continue
            verdict = rules.evaluate(r.get('explanation') or '', r.get('reason') or '')
            if not verdict.rejected:
                continue

            emp = Employee.objects.filter(id=r.get('employee_id')).first()
            if emp is None:
                continue

            resp, created = LeaveExcuseAutoResponse.objects.get_or_create(
                employee=emp, work_date=work_date, rule=verdict.rule)
            if created:
                recorded += 1
            if resp.sent_at is not None:
                skipped += 1                       # already emailed — idempotent
                continue

            subject, body = rules.email_for(verdict.rule, emp.full_name, day['date'])

            if not autosend:
                # SAFETY GATE OFF: recorded above, but send nothing.
                self.stdout.write(f'  would send Rule {verdict.rule} → '
                                  f'{emp.full_name} <{emp.email or "no-email"}>: "{subject}"')
                would += 1
                continue

            if not (emp.email or '').strip():
                no_email += 1
                self.stdout.write(self.style.WARNING(
                    f'  no email on file for {emp.full_name} — Rule {verdict.rule} not sent'))
                continue

            try:
                from core.notifications import send_html_with_cfo_cc
                send_html_with_cfo_cc(
                    subject=subject, html=_email_html(emp.full_name, body),
                    to=[emp.email], cc_cfo=False, text_fallback=body)
                resp.sent_at = _tz.now()
                resp.save(update_fields=['sent_at', 'updated_at'])
                sent += 1
            except Exception as exc:               # noqa: BLE001 — one bad send must not stop the run
                self.stderr.write(self.style.WARNING(
                    f'  send failed for {emp.full_name} <{emp.email}>: {exc}'))

        gate = 'ON' if autosend else 'OFF (recorded only, nothing sent)'
        self.stdout.write(self.style.SUCCESS(
            f'{day["date"]}: rows={len(day["rows"])} newly-recorded={recorded} '
            f'sent={sent} would_send={would} already-sent-skipped={skipped} '
            f'no-email={no_email} | LEAVE_EXCUSE_AUTOSEND={gate}'))
