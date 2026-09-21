"""
weekly_task_summary — Friday email to the CFO: who did what, what's overdue,
and feedback given this week (CFO directive 2026-07-13). Cron: Fri ~16:00 SAST.

  python manage.py weekly_task_summary [--dry-run]
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import OmniTask, TaskFeedback


class Command(BaseCommand):
    help = 'Email the CFO the weekly task summary (completed / overdue / feedback).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        today = timezone.localdate()
        monday = today - timedelta(days=today.weekday())
        tasks = (OmniTask.objects.filter(week_of=monday)
                 .exclude(status=OmniTask.Status.CANCELLED)
                 .select_related('assignee'))
        rows = {}
        for t in tasks:
            r = rows.setdefault(t.assignee_id, {
                'name': t.assignee.get_full_name() or t.assignee.username,
                'total': 0, 'done': 0, 'overdue': 0, 'blocked': 0})
            r['total'] += 1
            if t.status == OmniTask.Status.DONE:
                r['done'] += 1
            if t.status == OmniTask.Status.BLOCKED:
                r['blocked'] += 1
            if t.due_at and t.status != OmniTask.Status.DONE and t.due_at < today:
                r['overdue'] += 1
        fb_count = TaskFeedback.objects.filter(created_at__date__gte=monday).count()

        body = [f'<p>Week of {monday:%d %b %Y} — task summary.</p>',
                '<table style="border-collapse:collapse;width:100%;font-size:14px;">',
                '<tr style="background:#0D1B2A;color:#fff;">'
                '<th style="text-align:left;padding:7px 10px;">Person</th>'
                '<th style="padding:7px 10px;">Done</th><th style="padding:7px 10px;">Total</th>'
                '<th style="padding:7px 10px;">Overdue</th><th style="padding:7px 10px;">Blocked</th></tr>']
        for r in sorted(rows.values(), key=lambda x: (-x['overdue'], x['name'])):
            body.append(
                f'<tr><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">{r["name"]}</td>'
                f'<td align="center" style="border-bottom:1px solid #e5e7eb;">{r["done"]}</td>'
                f'<td align="center" style="border-bottom:1px solid #e5e7eb;">{r["total"]}</td>'
                f'<td align="center" style="border-bottom:1px solid #e5e7eb;color:{"#B04E00" if r["overdue"] else "#333"};">{r["overdue"]}</td>'
                f'<td align="center" style="border-bottom:1px solid #e5e7eb;">{r["blocked"]}</td></tr>')
        body.append('</table>')
        body.append(f'<p>Feedback given this week: {fb_count}.</p>')
        html_body = '\n'.join(body)

        self.stdout.write(f'week={monday} people={len(rows)} feedback={fb_count} dry_run={opts["dry_run"]}')
        if opts['dry_run']:
            self.stdout.write('DRY RUN — not sending.')
            return
        try:
            from core.notifications import send_html_with_cfo_cc, wrap_plain_as_html
            html = wrap_plain_as_html('Weekly task summary', html_body) \
                if 'wrap_plain_as_html' in dir(__import__('core.notifications', fromlist=['x'])) else html_body
            n = send_html_with_cfo_cc(
                subject=f'Weekly task summary — week of {monday:%d %b %Y}',
                html=html, to=['pganesharajah@alphadirect.co.bw'], cc_cfo=False)
            self.stdout.write(self.style.SUCCESS(f'sent={n}'))
        except Exception as e:  # noqa: BLE001
            self.stderr.write(f'send failed: {e}')
