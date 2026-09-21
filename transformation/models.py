"""The Transformation Board — "First AI Insurance Company in Botswana".

The CFO's ask (2026-09-20): one living screen for the CEO, CFO, COO and the
Chief Human Capital Officer that answers, every morning:

  * how far down the path are we, and who owns each step;
  * who is pushing automation and who is not;
  * which department is not using what we built, and what that costs;
  * the consolidated staff cost today against the plan;
  * where the people we free up go (customer acquisition, not the door first).

Everything here is READ-MOSTLY. The board computes from what Omni already
knows — Development Dialogues, the Build Log, tasks, payslips — and stores one
snapshot a day so the screen can animate a real trend instead of a guess.

Nothing in this app moves money, changes payroll, or writes to Graphite.
"""
from __future__ import annotations

from django.db import models

from core.models import BaseModel

# The four-month clock the CFO set on 2026-09-20. Everything on the board is
# measured against this, not the 18-month plan in the blueprint.
PROGRAMME_START = '2026-09-20'
PROGRAMME_END = '2027-01-20'


class Initiative(BaseModel):
    """One step on the path. Seeded from the blueprint, edited by the CFO."""

    class Status(models.TextChoices):
        NOT_STARTED = 'not_started', 'Not started'
        IN_PROGRESS = 'in_progress', 'In progress'
        BLOCKED = 'blocked', 'Blocked'
        DONE = 'done', 'Done'

    class Track(models.TextChoices):
        SWITCH_ON = 'switch_on', 'Switch on what exists'
        COLLECTIONS = 'collections', 'Collections and refunds'
        POLICY = 'policy', 'Policy lifecycle'
        CLAIMS = 'claims', 'Claims end to end'
        FINANCE = 'finance', 'Finance close'
        SERVICE = 'service', 'Customer service'
        ACQUISITION = 'acquisition', 'Customer acquisition'

    code = models.CharField(max_length=32, unique=True, db_index=True)
    title = models.CharField(max_length=200)
    # Plain English, for a board screen read by people who do not code.
    plain_summary = models.TextField(blank=True, default='')

    track = models.CharField(max_length=16, choices=Track.choices,
                             default=Track.SWITCH_ON, db_index=True)
    # Month 1..4 of the CFO's four-month clock.
    month = models.PositiveSmallIntegerField(default=1, db_index=True)

    department = models.CharField(max_length=64, blank=True, default='', db_index=True,
                                  help_text='Canonical department (hris.departments).')
    manager_name = models.CharField(max_length=120, blank=True, default='',
                                    help_text='The person answerable for this step.')
    manager_email = models.CharField(max_length=200, blank=True, default='', db_index=True)

    target_date = models.DateField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.NOT_STARTED, db_index=True)
    percent = models.PositiveSmallIntegerField(default=0)

    # What finishing this is worth, so the board can rank by money, not noise.
    annual_saving_bwp = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    fte_released = models.DecimalField(max_digits=5, decimal_places=1, default=0)

    # A step waiting on someone is the single most useful thing on the screen.
    blocked_on = models.CharField(max_length=160, blank=True, default='')
    blocked_since = models.DateField(null=True, blank=True)

    # Outside Alpha Direct's control (Pramod / Graphite, Phil / MotoLink,
    # RealPay). Never allowed on the critical path.
    is_vendor = models.BooleanField(default=False, db_index=True)

    # Links the step to the Build Log so progress is evidence, not opinion.
    devlog_area = models.CharField(max_length=64, blank=True, default='')
    improves_customer_service = models.BooleanField(default=False)

    class Meta(BaseModel.Meta):
        ordering = ['month', 'code']

    def __str__(self) -> str:
        return f'{self.code} — {self.title}'


class InitiativeUpdate(BaseModel):
    """Every movement on a step, so the trend line is auditable."""

    initiative = models.ForeignKey(Initiative, on_delete=models.CASCADE,
                                   related_name='updates')
    percent = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=12, default='')
    note = models.CharField(max_length=300, blank=True, default='')
    actor_name = models.CharField(max_length=120, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']


class InitiativeAssignment(BaseModel):
    """A person put on a step, with a date — and the task that proves it.

    CFO, 2026-09-20: "assign people for each task and it should create a task
    for them with deadlines so I can monitor".

    Assigning here creates a real OmniTask, so the person is chased by the same
    reminders as everything else in Omni and the board's own "who is not
    responding" score picks it up automatically. A board that invented its own
    private to-do list would be one more thing nobody looks at.
    """

    initiative = models.ForeignKey(Initiative, on_delete=models.CASCADE,
                                   related_name='assignments')
    assignee = models.ForeignKey('auth.User', null=True, blank=True,
                                 on_delete=models.SET_NULL,
                                 related_name='transformation_assignments')
    assignee_name = models.CharField(max_length=120, blank=True, default='')
    assignee_email = models.CharField(max_length=200, blank=True, default='',
                                      db_index=True)
    due_date = models.DateField(null=True, blank=True)
    role = models.CharField(max_length=120, blank=True, default='',
                            help_text='What this person is on the hook for.')

    # The OmniTask this created. Kept so the board can show its live status
    # instead of a second, quietly diverging copy of the truth.
    task = models.ForeignKey('core.OmniTask', null=True, blank=True,
                             on_delete=models.SET_NULL,
                             related_name='transformation_assignments')
    is_owner = models.BooleanField(default=False,
                                   help_text='The one answerable person for the step.')

    class Meta(BaseModel.Meta):
        ordering = ['initiative__code', '-is_owner', 'assignee_name']
        constraints = [
            # The database is the only thing that can actually stop two
            # simultaneous assignments creating two tasks for one person.
            # A check-then-insert in Python cannot: with no row yet there is
            # nothing to lock, so both requests look, both see nothing, and
            # both insert.
            models.UniqueConstraint(
                fields=['initiative', 'assignee'],
                name='uniq_assignment_per_person_per_step',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.assignee_name or self.assignee_email} on {self.initiative.code}'


class DepartmentPlan(BaseModel):
    """Today's shape of a department against the shape we are heading for.

    Headcount and cost are NOT typed in — they are refreshed from the live
    payroll rows by `transformation_pulse`, so this table can never drift from
    what payroll actually says. Only the targets and the narrative are edited.
    """

    department = models.CharField(max_length=64, unique=True, db_index=True)
    manager_name = models.CharField(max_length=120, blank=True, default='')
    manager_email = models.CharField(max_length=200, blank=True, default='')

    # Refreshed nightly from payroll — never hand-typed.
    headcount_now = models.PositiveSmallIntegerField(default=0)
    cost_now = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    headcount_target = models.PositiveSmallIntegerField(default=0)
    # Of the people freed, how many move to winning customers rather than out.
    reallocate_to_acquisition = models.PositiveSmallIntegerField(default=0)

    automation_note = models.TextField(blank=True, default='')
    # What the department is still doing by hand that a shipped feature covers.
    manual_work_note = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['department']

    def __str__(self) -> str:
        return self.department


class PulseSnapshot(BaseModel):
    """One row a day. The whole board, frozen, so the screen can animate.

    Storing the computed payload (rather than recomputing on every page load)
    means the board is fast, the trend is real history, and a bad day cannot be
    quietly rewritten later.
    """

    taken_for = models.DateField(unique=True, db_index=True)
    overall_percent = models.PositiveSmallIntegerField(default=0)
    days_remaining = models.SmallIntegerField(default=0)
    staff_cost_now = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    staff_cost_target = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    headcount_now = models.PositiveSmallIntegerField(default=0)
    headcount_target = models.PositiveSmallIntegerField(default=0)
    cost_of_delay_bwp = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Department scores, manager leaderboard, blocked list, customer-service
    # numbers, acquisition plan — everything the screen draws.
    payload = models.JSONField(default=dict, blank=True)

    # The daily read from the AI panel. Words only; the numbers above are
    # computed by rule and the panel can never change them.
    narrative = models.TextField(blank=True, default='')
    narrative_source = models.CharField(max_length=60, blank=True, default='')
    judge_verdict = models.TextField(blank=True, default='')
    judge_source = models.CharField(max_length=60, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-taken_for']

    def __str__(self) -> str:
        return f'Transformation pulse {self.taken_for} — {self.overall_percent}%'
