"""
Manager objectives — raise and settle (CFO 2026-09-09).

    "we need to make managers work — currently they are chilling and not being
     accountable... creating new tasks for them automatically system generated
     on Sunday midnight, and due on Wednesday"

    "...ensure board meeting information is send to EXCO 5th of every quarter
     ending"

Two jobs in one command, so the raise and the settle can never drift apart:

  --raise    Reads each due objective's counter NOW, freezes it as the period's
             baseline, and puts ONE task per manager per cadence on their board.
             Weekly runs Sunday 00:00 and is due Wednesday 16:00. Quarterly runs
             on the first day of a new quarter and is due the 5th.

  --settle   Re-reads the counters, writes the actual, decides met/missed, and
             CLOSES the task when every number on it moved.

Dry-run by default; --commit writes.

WHY SETTLING IS IN THE SAME MODULE, NOT A LATER IMPROVEMENT
Omni's existing monthly cycles raise tasks and never close them. Proven on prod
on the morning of 2026-09-09: 14 managers — the CFO among them — were being
emailed daily to chase monthly feedback they had already filed, one of them four
days running after filing all five of her check-ins. Bolting a WEEKLY cadence
onto that behaviour would have turned one false nag into five a week and taught
every manager to ignore Omni mail. An objective that cannot close itself is
worse than no objective at all.

A counter that cannot be READ is never a miss. If Graphite is unreachable the
run records the error and stays open — an unreachable database is not a manager
failing their number.
"""
from __future__ import annotations

import datetime as dt

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

BASE = 'https://omni.alphadirect.co.bw'
WEEKLY_DUE_WEEKDAY = 2        # Monday=0 -> Wednesday=2 (CFO: raised Sunday, due Wednesday)
DUE_TIME = dt.time(16, 0)     # 4pm, the house task deadline
ANNUAL_DEFAULT_DUE_DAYS = 90  # only when an annual objective sets no due_days of its own
# Alpha Direct's financial year starts 1 July — an annual objective is asked for
# the financial year, not the calendar one. Kept as a named constant so it reads
# as a stated assumption rather than a magic 7 buried inside a date call.
FY_START_MONTH = 7


# ── period arithmetic ───────────────────────────────────────────────────────

def week_start_for(day: dt.date) -> dt.date:
    """The MONDAY of the week `day` belongs to.

    Sunday midnight is the RAISE moment but Sunday belongs to the week that is
    about to start, not the one ending — so a Sunday raise books the task
    against tomorrow's Monday. Any other day resolves to its own Monday, which
    keeps a Wednesday --settle landing on the same row the Sunday raise wrote.
    """
    if day.weekday() == 6:              # Sunday
        return day + dt.timedelta(days=1)
    return day - dt.timedelta(days=day.weekday())


def quarter_start_for(day: dt.date) -> dt.date:
    """First day of the quarter being REPORTED ON.

    A quarterly objective is raised at the start of a new quarter and asks about
    the one that just closed — you cannot report on a quarter that has not
    happened yet. So this returns the PREVIOUS quarter's first day.
    """
    this_q_first = dt.date(day.year, ((day.month - 1) // 3) * 3 + 1, 1)
    last_day_prev = this_q_first - dt.timedelta(days=1)
    return dt.date(last_day_prev.year, ((last_day_prev.month - 1) // 3) * 3 + 1, 1)


def year_start_for(day: dt.date) -> dt.date:
    """First day of the financial year `day` falls in (1 July, Alpha Direct)."""
    year = day.year if day.month >= FY_START_MONTH else day.year - 1
    return dt.date(year, FY_START_MONTH, 1)


def period_start_for(cadence: str, day: dt.date) -> dt.date:
    from hris.weekly_objective_models import Cadence
    if cadence == Cadence.QUARTERLY:
        return quarter_start_for(day)
    if cadence == Cadence.ANNUAL:
        return year_start_for(day)
    return week_start_for(day)


def due_date_for(cadence: str, period_start: dt.date, due_days: int = 0) -> dt.date:
    """When the task must be done, given the period it reports on."""
    from hris.weekly_objective_models import Cadence
    if cadence == Cadence.QUARTERLY:
        # The CFO's rule: the 5th of the month AFTER the quarter ends.
        from iso_compliance.aml_models import board_pack_due, quarter_of
        year, quarter = quarter_of(period_start)
        return board_pack_due(year, quarter)
    if cadence == Cadence.ANNUAL:
        return period_start + dt.timedelta(days=due_days or ANNUAL_DEFAULT_DUE_DAYS)
    return period_start + dt.timedelta(days=WEEKLY_DUE_WEEKDAY)


def period_label(cadence: str, period_start: dt.date) -> str:
    from hris.weekly_objective_models import Cadence
    if cadence == Cadence.QUARTERLY:
        return f'{period_start.year} Q{(period_start.month - 1) // 3 + 1}'
    if cadence == Cadence.ANNUAL:
        return f'FY{period_start.year + 1}'
    return f'week of {period_start:%d %b}'


# ── people ──────────────────────────────────────────────────────────────────

def _system_assigner():
    for email in ('pganesharajah@alphadirect.co.bw', 'ubutale@alphadirect.co.bw'):
        u = User.objects.filter(email__iexact=email).first()
        if u:
            return u
    return User.objects.filter(is_superuser=True).order_by('id').first()


def _manager_user(employee):
    u = getattr(employee, 'user', None)
    if u:
        return u
    email = (getattr(employee, 'email', '') or '').strip()
    return User.objects.filter(email__iexact=email).first() if email else None


class Command(BaseCommand):
    help = 'Raise or settle the manager objectives (weekly, quarterly, annual).'

    def add_arguments(self, parser):
        parser.add_argument('--raise', dest='do_raise', action='store_true',
                            help='Raise the tasks due now.')
        parser.add_argument('--settle', action='store_true',
                            help='Read the actuals and close what was met.')
        parser.add_argument('--commit', action='store_true', help='Write (else dry-run).')
        parser.add_argument('--cadence', type=str, default='',
                            help='Limit to one cadence: weekly / quarterly / annual.')
        parser.add_argument('--period', type=str, default='',
                            help='Override the period start, YYYY-MM-DD. Testing only.')

    def handle(self, *args, **opts):
        if not (opts['do_raise'] or opts['settle']):
            self.stderr.write('Pass --raise or --settle.')
            return
        from hris.weekly_objective_models import Cadence

        cadences = [opts['cadence']] if opts['cadence'] else [c for c, _ in Cadence.choices]
        today = timezone.localdate()
        for cadence in cadences:
            start = (dt.date.fromisoformat(opts['period']) if opts['period']
                     else period_start_for(cadence, today))
            if opts['do_raise']:
                self._raise_period(cadence, start, opts['commit'])
            if opts['settle']:
                self._settle_period(cadence, start, opts['commit'])

    # ── raise ───────────────────────────────────────────────────────────────
    def _raise_period(self, cadence, period_start, commit):
        from collections import defaultdict

        from core.models import OmniTask
        from hris.objective_counters import CounterUnavailable, read
        from hris.weekly_objective_models import (
            Cadence,
            WeeklyObjective,
            WeeklyObjectiveRun,
        )

        objectives = list(WeeklyObjective.objects.filter(active=True, cadence=cadence)
                          .select_related('profile', 'profile__employee'))
        if not objectives:
            return

        label = period_label(cadence, period_start)
        tag = f'objective:{cadence}:{period_start.isoformat()}'
        self.stdout.write(f'Raising {cadence} objectives for {label} (commit={commit})')

        by_profile = defaultdict(list)
        for obj in objectives:
            by_profile[obj.profile_id].append(obj)

        assigner = _system_assigner()
        raised = skipped = 0
        for group in by_profile.values():
            employee = group[0].profile.employee
            user = _manager_user(employee)
            if user is None:
                self.stderr.write(f'  ! {employee.full_name}: no login — cannot raise')
                continue
            if OmniTask.objects.filter(assignee=user, source=tag).exists():
                skipped += 1
                continue

            due = due_date_for(cadence, period_start,
                               max((o.due_days for o in group), default=0))
            lines, pending = [], []
            for obj in group:
                try:
                    baseline = read(obj.counter)
                except CounterUnavailable as exc:
                    # No baseline means no honest test at settle time. Skip this
                    # ONE objective; the manager's other numbers still go out.
                    self.stderr.write(f'  ! {obj.key}: {exc}')
                    continue
                pending.append((obj, baseline))
                lines.append(self._line(obj, baseline))

            if not pending:
                self.stderr.write(f'  ! {employee.full_name}: no counter could be read')
                continue

            if cadence == Cadence.WEEKLY:
                title = f'Your numbers this week — due {due:%A %d %b} 4pm'
            else:
                title = f'{label} compliance obligations — due {due:%A %d %b} 4pm'
            body = (
                'These are read straight out of the system at the deadline. You do '
                'not fill anything in — do the work and the task closes itself.\n\n'
                + '\n\n'.join(lines)
                + f'\n\nYour board: {BASE}/tasks'
            )
            if commit:
                task = OmniTask.objects.create(
                    assigner=assigner or user, assignee=user,
                    title=title[:200], body=body,
                    priority=OmniTask.Priority.HIGH, status=OmniTask.Status.PENDING,
                    due_at=due, due_time=DUE_TIME,
                    # week_of drives the CFO's weekly-plan grouping, so only a
                    # weekly objective claims a week — a quarterly task stamped
                    # with a Monday would land in that week's plan and read as
                    # something the manager owes by Friday.
                    week_of=period_start if cadence == Cadence.WEEKLY else None,
                    source=tag)
                for obj, baseline in pending:
                    WeeklyObjectiveRun.objects.update_or_create(
                        objective=obj, period_start=period_start,
                        defaults={'baseline': baseline, 'target': obj.target,
                                  'direction': obj.direction, 'cadence': cadence,
                                  'task': task})
            raised += 1
            self.stdout.write(f'  -> {employee.full_name}: {len(pending)} number(s), due {due}')

        self.stdout.write(self.style.SUCCESS(
            f'{cadence}: raised {raised}, skipped {skipped} already raised.'
            + ('' if commit else '  [DRY RUN — pass --commit to write]')))

    def _line(self, obj, baseline):
        from hris.weekly_objective_models import Direction

        if obj.direction == Direction.NIL:
            head = f'{obj.title}\n  Target: ZERO. Right now: {baseline:,}.'
        elif obj.direction == Direction.INCREASE:
            head = f'{obj.title}\n  Target: +{obj.target:,} (done so far: {baseline:,}).'
        else:
            head = f'{obj.title}\n  Target: clear {obj.target:,} (backlog now: {baseline:,}).'
        progress = self._progress(obj, baseline)
        return (head
                + (f'\n{progress}' if progress else '')
                + (f'\n  How: {obj.how_to}' if obj.how_to else ''))

    def _progress(self, obj, baseline):
        """Last period's result and the running total, in the manager's own task.

        CFO 2026-09-09, on Kakale's 2,627-policy backlog: "she will take a team
        and fix the 2627 policies weekly until then it will put tasks for her,
        and she can SEE HOW THE NUMBERS ARE REDUCING."

        A target of 25 against a backlog of 2,627 looks hopeless in isolation —
        the same number appears every Sunday and reads as no progress at all.
        The distance travelled is the only thing that shows the work is landing,
        and it belongs in the task the manager already opens, not on a screen
        somebody has to remember to visit.

        Both halves are shown deliberately: LAST period answers "did I hit it?",
        and SINCE answers "is this ever going to end?". Neither alone does both.

        Silent on the first period — there is no history yet, and a progress line
        reading "0 so far" on week one is discouraging noise.
        """
        from hris.weekly_objective_models import Direction, WeeklyObjectiveRun

        history = list(WeeklyObjectiveRun.objects
                       .filter(objective=obj, met__isnull=False)
                       .order_by('period_start'))
        if not history:
            return ''

        last = history[-1]
        first = history[0]
        mark = 'hit' if last.met else 'missed'

        if obj.direction == Direction.NIL:
            lines = [f'  Last time: {last.actual:,} — {mark}.']
            clean = sum(1 for r in history if r.met)
            lines.append(f'  Clean {clean} of the last {len(history)}.')
            return '\n'.join(lines)

        moved_last = last.moved or 0
        # Total distance travelled from the FIRST baseline ever recorded, not a
        # sum of weekly movements — summing double-counts a week where the
        # underlying data moved for reasons other than this manager's work.
        moved_total = (first.baseline - baseline if obj.direction == Direction.REDUCE
                       else baseline - first.baseline)
        verb = 'cleared' if obj.direction == Direction.REDUCE else 'added'

        lines = [f'  Last time: {last.baseline:,} to {last.actual:,} — '
                 f'{verb} {moved_last:,}, {mark}.']
        if moved_total > 0:
            since = f'  Since {first.period_start:%d %b}: {verb} {moved_total:,}'
            if obj.direction == Direction.REDUCE and first.baseline:
                left = max(baseline, 0)
                pct = round(moved_total * 100 / first.baseline)
                since += f' of {first.baseline:,} ({pct}% done, {left:,} left)'
            lines.append(since + '.')
        return '\n'.join(lines)

    # ── settle ──────────────────────────────────────────────────────────────
    def _settle_period(self, cadence, period_start, commit):
        from core.models import OmniTask
        from hris.objective_counters import CounterUnavailable, read
        from hris.weekly_objective_models import WeeklyObjectiveRun

        runs = list(WeeklyObjectiveRun.objects
                    .filter(period_start=period_start, cadence=cadence, met__isnull=True)
                    .select_related('objective', 'objective__profile__employee', 'task'))
        if not runs:
            return
        self.stdout.write(f'Settling {cadence} for {period_label(cadence, period_start)} '
                          f'(commit={commit})')

        by_task = {}
        settled = unmeasured = 0
        for run in runs:
            try:
                actual = read(run.objective.counter)
            except CounterUnavailable as exc:
                unmeasured += 1
                if commit:
                    run.settle_error = str(exc)[:500]
                    run.save(update_fields=['settle_error', 'updated_at'])
                self.stderr.write(f'  ? {run.objective.key}: not measured — {exc}')
                continue
            run.actual = actual
            met = run.evaluate()
            if commit:
                run.met = met
                run.settled_at = timezone.now()
                run.settle_error = ''
                run.save(update_fields=['actual', 'met', 'settled_at',
                                        'settle_error', 'updated_at'])
            settled += 1
            self.stdout.write(
                f'  {"MET" if met else "MISSED"}: {run.objective.key} '
                f'{run.baseline:,} -> {actual:,} (needed {run.target:,})')
            if run.task_id:
                by_task.setdefault(run.task_id, []).append(met)

        # A task closes only when EVERY number on it was met AND every number on
        # it was measured — an unread counter leaves the task open, on purpose.
        closed = 0
        for task_id, results in by_task.items():
            task = OmniTask.objects.filter(pk=task_id).first()
            if task is None or task.status == OmniTask.Status.DONE:
                continue
            if commit and WeeklyObjectiveRun.objects.filter(
                    task_id=task_id, met__isnull=True).exists():
                continue
            if not all(results):
                continue
            if commit:
                task.status = OmniTask.Status.DONE
                task.completion_pct = 100
                task.completed_at = timezone.now()
                task.save(update_fields=['status', 'completion_pct',
                                         'completed_at', 'updated_at'])
                self._write_completion_note(task)
            closed += 1

        self.stdout.write(self.style.SUCCESS(
            f'{cadence}: settled {settled}, {unmeasured} not measured, closed {closed} task(s).'
            + ('' if commit else '  [DRY RUN — pass --commit to write]')))

    def _write_completion_note(self, task):
        """Say WHY the task closed, on the task itself.

        A manager who opens a task Omni closed for them must be able to see it
        was the numbers and not a glitch. `OmniTask.completion_note` is a
        one-to-one row, not a text field — best-effort, because failing to write
        the note must never leave a met task sitting open.
        """
        try:
            from taskboard.models import CompletionNote
            CompletionNote.objects.get_or_create(
                task=task,
                defaults={
                    'author': task.assigner,
                    'body': ('Closed automatically — every number on this task was hit '
                             'and verified against live data at the deadline.'),
                },
            )
        except Exception as exc:      # noqa: BLE001
            self.stderr.write(f'  ! completion note for task {task.pk} failed: {exc}')
