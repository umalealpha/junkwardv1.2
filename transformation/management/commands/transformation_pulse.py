"""Take today's transformation pulse — the job that makes the board living.

Runs every morning. Recomputes the whole board from live payroll, the Build
Log, tasks, Time Doctor and the per-day workday record, refreshes each
department's headcount and cost, asks the AI panel for a paragraph, and stores
one snapshot for the day so the screen can animate a real trend.

Dry run by default. --commit writes.
"""
from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from transformation import pulse
from transformation.models import DepartmentPlan, PulseSnapshot


class Command(BaseCommand):
    help = 'Recompute the transformation board and store today\'s snapshot.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Write the snapshot (default is a dry run).')
        parser.add_argument('--no-ai', action='store_true',
                            help='Skip the AI panel (figures are unaffected).')

    def handle(self, *args, **opts):
        commit = opts['commit']
        board = pulse.build_board()

        # Keep each department's headcount and cost in step with payroll, so
        # the plan table can never drift from what payroll actually says.
        refreshed = 0
        for row in board['departments']:
            plan = DepartmentPlan.objects.filter(department=row['department']).first()
            if not plan:
                continue
            plan.headcount_now = row['headcount_now']
            plan.cost_now = Decimal(str(row['cost_now']))
            if commit:
                plan.save(update_fields=['headcount_now', 'cost_now', 'updated_at'])
            refreshed += 1

        read = {'narrative': '', 'narrative_source': '', 'judge_verdict': '',
                'judge_source': '', 'note': 'AI panel skipped (--no-ai).'}
        if not commit:
            # A dry run must not spend real tokens at three providers. Say so
            # rather than quietly calling out.
            read['note'] = 'AI panel skipped — dry run makes no external calls.'
        elif not opts['no_ai']:
            from transformation import ai_panel
            read = ai_panel.daily_read(board)

        board['ai'] = {
            'narrative': read.get('narrative', ''),
            'source': read.get('narrative_source', ''),
            'judge': read.get('judge_verdict', ''),
            'judge_source': read.get('judge_source', ''),
            'note': read.get('note', ''),
        }

        today = timezone.localdate()
        cost = board['staff_cost']
        fields = dict(
            overall_percent=board['overall_percent'],
            days_remaining=board['clock']['days_remaining'],
            staff_cost_now=Decimal(str(cost['now'])),
            staff_cost_target=Decimal(str(cost['target'])),
            headcount_now=cost['headcount_now'],
            headcount_target=cost['headcount_target'],
            cost_of_delay_bwp=Decimal(str(board['cost_of_delay']['monthly'])),
            payload=board,
            narrative=read.get('narrative', ''),
            narrative_source=read.get('narrative_source', ''),
            judge_verdict=read.get('judge_verdict', ''),
            judge_source=read.get('judge_source', ''),
        )

        if commit:
            PulseSnapshot.objects.update_or_create(taken_for=today, defaults=fields)

        self.stdout.write(
            f"{'WROTE' if commit else 'DRY RUN'} pulse {today}: "
            f"{board['overall_percent']}% done against {board['time_percent']}% of the time, "
            f"{board['clock']['days_remaining']} days left, "
            f"{len(board['blocked'])} blocked, {len(board['behind'])} behind, "
            f"{refreshed} departments refreshed, "
            f"salary at risk P{board['workforce']['salary_at_risk_month']:,.2f}/month, "
            f"AI: {board['ai']['source'] or 'unavailable'}"
        )
