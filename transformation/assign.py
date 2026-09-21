"""Putting a person on a step, and keeping the task honest.

Assigning creates a real OmniTask so the person is chased by the reminders
Omni already sends, and so the board's "who is not responding" signal reads a
live task rather than a private list nobody else can see.

Two rules:
  * Only a real, active Omni login can be assigned. A typed-in address would
    create a task nobody ever receives — the board would show work assigned and
    the person would never know.
  * Re-assigning the same person on the same step updates their task; it does
    not pile up duplicates.
"""
from __future__ import annotations

import datetime as _dt

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone

from transformation.models import Initiative, InitiativeAssignment


def assignable_people() -> list[dict]:
    """Active Omni logins that can be given a step, with their department."""
    from django.apps import apps

    Employee = apps.get_model('payroll', 'Employee')
    dept_by_email = {
        (e or '').strip().lower(): (d or '').strip()
        for e, d in Employee.objects.filter(status='active')
                                    .values_list('email', 'department')
        if e
    }

    people = []
    for user in User.objects.filter(is_active=True).exclude(email='').order_by('first_name', 'username'):
        email = (user.email or '').strip().lower()
        full = (user.get_full_name() or '').strip() or user.username
        people.append({
            'email': email,
            'name': full,
            'username': user.username,
            'department': dept_by_email.get(email, ''),
            # Someone with no payroll record can still be assigned (vendors,
            # contractors) — the screen just says so rather than hiding them.
            'on_payroll': email in dept_by_email,
        })
    return people


def _find_user(email: str) -> User | None:
    """Positive AND unambiguous match only.

    `.first()` on an unordered queryset silently picks one of several. Omni
    has a documented history of duplicate people records, and email is not
    unique on User — so two active accounts on one address would hand the task
    to whichever row the database happened to return, and with `is_owner` set,
    stamp the wrong name on the step and then score that person. Unknown is
    refused; ambiguous is refused too, and says so.
    """
    clean = (email or '').strip().lower()
    if not clean:
        return None
    for lookup in ({'email__iexact': clean}, {'username__iexact': clean}):
        matches = list(User.objects.filter(is_active=True, **lookup)[:2])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
    return None


def assign(initiative: Initiative, email: str, *, due_date=None, role: str = '',
           is_owner: bool = False, assigner: User | None = None,
           note: str = '') -> tuple[InitiativeAssignment | None, str]:
    """Put a person on a step and raise their task. Returns (row, error)."""
    user = _find_user(email)
    if user is None:
        return None, (f'{email!r} is not an active Omni login. Assign someone who '
                      f'can actually receive the task.')

    if due_date and isinstance(due_date, str):
        try:
            due_date = _dt.date.fromisoformat(due_date)
        except ValueError:
            return None, 'due_date must look like 2026-11-20.'

    # Default to the step's own target date — a step with a deadline should not
    # produce a task without one.
    due_date = due_date or initiative.target_date

    # One transaction: the row and its task are created together or not at
    # all. Without this, a failure between the two leaves either an orphan
    # task nobody can see on the board, or an assignment with no task — and
    # two people assigning at once produces two tasks for one person.
    try:
        with transaction.atomic():
            row, _ = (InitiativeAssignment.objects
                      .select_for_update()
                      .get_or_create(initiative=initiative, assignee=user))
            return _apply(row, initiative, user, due_date, role, is_owner,
                          assigner, note), ''
    except IntegrityError:
        # Someone assigned the same person at the same instant and won the
        # race. Their row is the one that exists; ours becomes an update.
        with transaction.atomic():
            row = (InitiativeAssignment.objects
                   .select_for_update()
                   .get(initiative=initiative, assignee=user))
            return _apply(row, initiative, user, due_date, role, is_owner,
                          assigner, note), ''


def _apply(row, initiative, user, due_date, role, is_owner, assigner, note):
    """Write the assignment and its task. Always inside a transaction."""
    from core.models import OmniTask

    row.assignee_name = (user.get_full_name() or '').strip() or user.username
    row.assignee_email = (user.email or '').strip().lower()
    row.due_date = due_date
    row.role = role[:120]
    row.is_owner = is_owner

    title = f'{initiative.code}: {initiative.title}'[:200]
    body = (
        f'{initiative.plain_summary}\n\n'
        f'This is part of the four-month transformation programme '
        f'(everything delivered by 20 January 2027).\n'
        f'{("Your part: " + role) if role else ""}\n'
        f'{note}'
    ).strip()

    if row.task_id and row.task:
        task = row.task
        task.title = title
        task.body = body
        task.due_at = due_date
        task.save(update_fields=['title', 'body', 'due_at', 'updated_at'])
    else:
        task = OmniTask.objects.create(
            assigner=assigner or user,
            assignee=user,
            title=title,
            body=body,
            due_at=due_date,
        )
        row.task = task

    row.save()

    # The owner of a step is the name the board shows against it.
    if is_owner:
        initiative.manager_name = row.assignee_name
        initiative.manager_email = row.assignee_email
        initiative.save(update_fields=['manager_name', 'manager_email', 'updated_at'])

    return row


def unassign(initiative: Initiative, email: str) -> bool:
    """Take a person off a step. Their task is cancelled, never deleted —
    the record of having been asked stays."""
    # Key on the SAME thing the unique constraint keys on. Matching on the
    # stored email instead means a person whose address changed can never be
    # taken off a step — and the next assign quietly updates a row the CFO
    # believes she removed.
    user = _find_user(email)
    if user is None:
        return False
    with transaction.atomic():
        row = (InitiativeAssignment.objects
               .select_for_update()
               .filter(initiative=initiative, assignee=user)
               .first())
        if not row:
            return False
        if row.task:
            row.task.status = 'cancelled'
            row.task.save(update_fields=['status', 'updated_at'])
        row.delete()
    return True


def assignments_for(initiative: Initiative) -> list[dict]:
    rows = []
    today = timezone.localdate()
    for row in initiative.assignments.select_related('task').all():
        task = row.task
        rows.append({
            'name': row.assignee_name,
            'email': row.assignee_email,
            'role': row.role,
            'is_owner': row.is_owner,
            'due_date': row.due_date.isoformat() if row.due_date else None,
            'task_id': str(task.id) if task else None,
            'task_status': task.status if task else None,
            'overdue': bool(task and task.due_at and task.due_at < today
                            and task.status in ('pending', 'in_progress', 'blocked')),
        })
    return rows
