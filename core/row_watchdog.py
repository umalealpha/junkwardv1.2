"""Row-count watchdog — notices when data disappears.

WHY THIS EXISTS (CFO, 2026-07-31): he believed two weeks of uploaded development
dialogues and a five-year plan had been deleted. Establishing that the dialogues
were in fact all still there took an hour of digging through tables, audit logs and
backups. That hour is the real problem. Nobody should have to take an AI's word for
"your data is fine", and nobody should have to ask.

So: snapshot the row count of every table on a schedule, compare against the last
snapshot, and EMAIL THE CFO the moment anything drops. Deletion becomes something he
is told about within the hour, by name and by number, instead of something he
discovers weeks later and cannot prove either way.

Deliberately dumb: it counts rows. It does not care WHY a count fell — a script, a
migration, an agent, a person, a bad cascade. Anything that removes data trips it.
A guardrail that only catches the causes we thought of is not a guardrail.
"""
from __future__ import annotations

from decimal import Decimal

from django.apps import apps
from django.db import models

# Tables where ANY drop at all is worth an email. These hold work that a human
# typed in and cannot simply re-derive — losing one row is a real loss.
PROTECTED = {
    'hris.DevelopmentDialogue', 'hris.OKR', 'hris.OKRCheckIn', 'hris.CareerTrack',
    'hris.CareerMilestone', 'hris.PerformanceImprovementPlan', 'hris.HRDocument',
    'hris.LeaveRequest', 'hris.ExpenseClaim', 'hris.EmployeeLoan',
    'payroll.Payslip', 'payroll.PayslipLine', 'payroll.Employee',
    'ledger.JournalEntry', 'ledger.JournalEntryLine', 'ledger.Account',
    'healthcare.HealthQuote', 'healthcare.HealthQuoteMember',
    'billing.Invoice', 'billing.Contact', 'procurement.PurchaseOrder',
    'core.BugReport', 'core.AuditLog', 'core.NotebookPage',
    'iso_compliance.SOPDocument', 'documents.DocumentUpload',
}

# Everywhere else, ignore trivial churn: only shout if the fall is both material in
# proportion AND in absolute size. Stops log/cache tables crying wolf every hour.
DROP_PCT_THRESHOLD = Decimal('2')
DROP_ABS_THRESHOLD = 5

# Tables that legitimately shrink as part of doing their job — pruning caches,
# rotating snapshots, clearing queues. Counting them would train everyone to ignore
# the alert, which is worse than not having it.
IGNORE = {
    'sessions.Session', 'admin.LogEntry', 'django_celery_beat.PeriodicTasks',
}


def current_counts() -> dict[str, int]:
    """Row count of every concrete model, keyed by app_label.ModelName."""
    out: dict[str, int] = {}
    for model in apps.get_models():
        label = model._meta.label
        if model._meta.abstract or model._meta.proxy or label in IGNORE:
            continue
        try:
            out[label] = model.objects.count()
        except Exception:
            # A table that cannot be counted (unmigrated, permissions) is not a
            # drop — skip it rather than reporting a phantom loss.
            continue
    return out


def find_drops(previous: dict[str, int], now: dict[str, int]) -> list[dict]:
    """Tables that lost rows since the last snapshot, worst proportional loss first.

    A table that has vanished from `now` entirely counts as a total loss — that is
    exactly the case a naive dict comparison would miss.
    """
    drops = []
    for label, before in previous.items():
        if label in IGNORE:
            continue
        after = now.get(label)
        missing_table = after is None
        after_n = 0 if missing_table else after
        if after_n >= before:
            continue
        lost = before - after_n
        pct = (Decimal(lost) / Decimal(before) * 100) if before else Decimal(0)
        protected = label in PROTECTED
        if not protected and (pct < DROP_PCT_THRESHOLD or lost < DROP_ABS_THRESHOLD):
            continue
        drops.append({
            'table': label, 'before': before, 'after': after_n, 'lost': lost,
            'pct': pct.quantize(Decimal('0.1')), 'protected': protected,
            'table_missing': missing_table,
        })
    drops.sort(key=lambda d: (not d['protected'], -d['pct']))
    return drops


def build_alert_html(drops: list[dict], previous_at, now_at) -> str:
    """The email. Leads with the number, names every table, says what to do."""
    NAVY, ORANGE = '#0D1B2A', '#F4A623'
    total = sum(d['lost'] for d in drops)
    rows = ''
    for d in drops:
        flag = ' 🔒' if d['protected'] else ''
        gone = ' <em>(table itself is gone)</em>' if d['table_missing'] else ''
        rows += (
            f'<tr><td><code>{d["table"]}</code>{flag}{gone}</td>'
            f'<td align="right">{d["before"]:,}</td>'
            f'<td align="right">{d["after"]:,}</td>'
            f'<td align="right"><strong>−{d["lost"]:,}</strong></td>'
            f'<td align="right">{d["pct"]}%</td></tr>'
        )
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
      body {{ font-family:'Segoe UI',Arial,sans-serif; color:#1F2937; font-size:14px; line-height:1.55; max-width:700px }}
      .hdr {{ background:{NAVY}; padding:18px 22px; border-radius:6px 6px 0 0 }}
      .hdr h1 {{ color:{ORANGE}; font-family:'Book Antiqua',Georgia,serif; font-size:20px; margin:0 }}
      .box {{ border:1px solid #E5E7EB; border-top:0; padding:20px 22px; border-radius:0 0 6px 6px }}
      table {{ border-collapse:collapse; width:100%; margin:12px 0; font-size:13px }}
      th {{ background:{NAVY}; color:#fff; padding:7px 10px; text-align:left }}
      td {{ padding:6px 10px; border-bottom:1px solid #E5E7EB }}
      code {{ background:#F3F4F6; padding:1px 5px; border-radius:3px; font-size:12px }}
      .small {{ color:#6B7280; font-size:12px }}
    </style></head><body>
      <div class="hdr"><h1>Data has disappeared from Omni</h1></div>
      <div class="box">
        <p><strong>{total:,} row(s)</strong> are gone across <strong>{len(drops)}</strong> table(s)
           since the last check.</p>
        <p>Between <strong>{previous_at:%Y-%m-%d %H:%M}</strong> and
           <strong>{now_at:%Y-%m-%d %H:%M}</strong> (UTC).</p>
        <table>
          <tr><th>Table</th><th align="right">Before</th><th align="right">Now</th>
              <th align="right">Lost</th><th align="right">%</th></tr>
          {rows}
        </table>
        <p>🔒 marks a table holding work someone typed in by hand — those alert on any
           drop at all, however small.</p>
        <p><strong>If this was not deliberate, say so now — it is recoverable.</strong>
           Database backups run hourly to S3, and every deletion Omni performs also
           writes the full row into the audit log before removing it.</p>
        <p class="small">Sent by the row-count watchdog. It counts rows and does not care
        why they fell, so a legitimate clean-up will also appear here.</p>
      </div></body></html>"""
