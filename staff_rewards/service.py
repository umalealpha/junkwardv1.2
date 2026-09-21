"""staff_rewards/service.py — submit / approve / reject business logic.

All point arithmetic reads staff_rewards.points_rules.POINTS_RULES — never
an inline number. Approval is idempotent: re-approving an already-approved
submission never double-awards (the transaction + account bump happen once).

DPA enforcement (Botswana DPA No. 18 of 2024): submit_staff strips any
name-like keys from the payload before persisting, so a name can never enter
the staff-rewards database even if a caller sends one.
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from payroll.models import Employee

from .models import (
    FEATURE_PILLAR, PILLAR_FIELD, FeatureCode, StaffPointsAccount,
    StaffPointsTransaction, StaffSubmission,
)
from .points_rules import FEATURE_ENABLED, POINTS_RULES


class FeatureDisabled(Exception):
    """Raised when a submission targets a feature gated off pending sign-off."""


class UnknownFeature(Exception):
    """Raised when feature_code is not a recognised staff feature."""


# Keys we refuse to persist in a payload — DPA: never store a personal name.
_BANNED_PAYLOAD_KEYS = {
    'name', 'full_name', 'fullname', 'customer_name', 'customername',
    'customer', 'employee_name', 'staff_name', 'first_name', 'last_name',
    'surname', 'national_id', 'omang', 'id_number', 'address', 'lat', 'lng',
    'latitude', 'longitude', 'location', 'gps', 'image', 'photo', 'raw_image',
}

# Features verified by the system (AI meal score / wearable steps) — no human
# approver; submit_staff auto-awards them. The other four need an approver.
SYSTEM_VERIFIED = {'meal', 'steps'}


def _sanitise_payload(payload: dict | None) -> dict:
    """Drop banned (name / identity / location / raw-image) keys — DPA."""
    if not isinstance(payload, dict):
        return {}
    return {k: v for k, v in payload.items()
            if str(k).strip().lower() not in _BANNED_PAYLOAD_KEYS}


def _as_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------

def submit_staff(employee, feature_code, payload, *, maker_email='') -> StaffSubmission:
    """Create a pending StaffSubmission for an employee.

    Rejects (raises FeatureDisabled) any feature whose FEATURE_ENABLED flag
    is False — meal/steps are deferred pending data-protection sign-off.
    """
    if feature_code not in FEATURE_PILLAR:
        raise UnknownFeature(f'Unknown staff feature: {feature_code!r}')
    if not FEATURE_ENABLED.get(feature_code, False):
        raise FeatureDisabled(
            f'Feature "{feature_code}" is pending data-protection sign-off.')

    pillar = FEATURE_PILLAR[feature_code]
    sub = StaffSubmission.objects.create(
        employee=employee,
        pillar=pillar,
        feature_code=feature_code,
        payload=_sanitise_payload(payload),
        status=StaffSubmission.Status.PENDING,
        maker_email=(maker_email or '')[:254],
    )
    # System-verified features (AI meal score / wearable steps) need no human
    # approver — the AI/device IS the verification, so auto-award immediately.
    # The other four (profdev/bizdev/fitness/compliment) stay PENDING for a
    # human approver (HR / BD lead / supervisor).
    if feature_code in SYSTEM_VERIFIED:
        return approve_submission(sub, None)
    return sub


# ---------------------------------------------------------------------------
# Points computation — reads POINTS_RULES only
# ---------------------------------------------------------------------------

def compute_points(feature_code: str, payload: dict | None) -> int:
    """Compute the points for a feature/payload from the configurable rules.

    Caps are applied here so the reward liability stays bounded. Returns a
    non-negative integer.
    """
    payload = payload or {}
    rules = POINTS_RULES.get(feature_code, {})

    if feature_code == FeatureCode.PROFDEV:
        level = str(payload.get('level') or 'short').lower()
        base = rules.get('levels', {}).get(level, 0)
        cap = rules.get('annual_cap')
        return min(base, cap) if cap is not None else base

    if feature_code == FeatureCode.BIZDEV:
        reach = str(payload.get('reach') or 'local').lower()
        base = rules.get('reach', {}).get(reach, 0)
        outcomes = max(_as_int(payload.get('outcomeCount'), 0), 0)
        bonus = outcomes * rules.get('outcome_bonus_per_unit', 0)
        bonus = min(bonus, rules.get('outcome_bonus_cap', bonus))
        return base + bonus

    if feature_code == FeatureCode.FITNESS:
        kind = str(payload.get('kind') or 'session').lower()
        base = rules.get('per_event', 0) if kind == 'event' else rules.get('per_session', 0)
        if payload.get('isTeam'):
            base += rules.get('team_bonus', 0)
        return base

    if feature_code == FeatureCode.COMPLIMENT:
        base = rules.get('base', 0)
        repeat = max(_as_int(payload.get('repeatIndex'), 0), 0)
        bonus = repeat * rules.get('repeat_bonus', 0)
        bonus = min(bonus, rules.get('repeat_bonus_cap', bonus))
        return base + bonus

    if feature_code == FeatureCode.MEAL:
        if not payload.get('qualifying'):
            return 0   # below the healthiness threshold — logged but no points
        per = rules.get('per_qualifying_meal', 0)
        meals = min(max(_as_int(payload.get('mealsTodayCount'), 1), 0),
                    rules.get('daily_cap_meals', 0))
        total = per * meals
        if _as_int(payload.get('streakDays'), 0) >= rules.get('streak_days_required', 99):
            total += rules.get('weekly_streak_bonus', 0)
        return total

    if feature_code == FeatureCode.STEPS:
        total = rules.get('per_day_target_met', 0) if payload.get('targetMet') else 0
        if _as_int(payload.get('weeklyDaysMet'), 0) >= rules.get('weekly_days_required', 99):
            total += rules.get('weekly_bonus', 0)
        return total

    return 0


# ---------------------------------------------------------------------------
# Approve / Reject
# ---------------------------------------------------------------------------

def _account_for(employee) -> StaffPointsAccount:
    account, _ = StaffPointsAccount.objects.get_or_create(employee=employee)
    return account


@transaction.atomic
def approve_submission(sub, approver, *, reason='') -> StaffSubmission:
    """Approve a submission: compute points, post a ledger transaction, bump
    the account balance + pillar total.

    Idempotent: if the submission is already approved, this is a no-op (no
    second transaction, no double-award). Re-approval just returns it.
    """
    # Lock the row so concurrent approvals serialise.
    sub = StaffSubmission.objects.select_for_update().get(pk=sub.pk)
    if sub.status == StaffSubmission.Status.APPROVED:
        return sub  # idempotent — already awarded, never double-count

    points = compute_points(sub.feature_code, sub.payload)
    account = StaffPointsAccount.objects.select_for_update().get_or_create(
        employee=sub.employee)[0]

    StaffPointsTransaction.objects.create(
        account=account,
        submission=sub,
        points=points,
        kind=StaffPointsTransaction.Kind.EARN,
        pillar=sub.pillar,
        detail=f'{sub.get_feature_code_display()} approved',
        occurred_at=timezone.now(),
    )

    # Bump overall balance + the pillar total.
    account.points_balance = (account.points_balance or 0) + points
    pillar_field = PILLAR_FIELD[sub.pillar]
    setattr(account, pillar_field, (getattr(account, pillar_field, 0) or 0) + points)
    account.save(update_fields=['points_balance', pillar_field, 'updated_at'])

    sub.status = StaffSubmission.Status.APPROVED
    sub.points_awarded = points
    sub.approver = approver if getattr(approver, 'pk', None) else None
    sub.approver_email = (getattr(approver, 'email', '') or '')[:254]
    sub.decided_at = timezone.now()
    if reason:
        sub.reason = reason
    sub.save(update_fields=['status', 'points_awarded', 'approver',
                            'approver_email', 'decided_at', 'reason',
                            'updated_at'])
    return sub


@transaction.atomic
def reject_submission(sub, approver, *, reason='') -> StaffSubmission:
    """Reject a submission. No points post. Idempotent on already-decided."""
    sub = StaffSubmission.objects.select_for_update().get(pk=sub.pk)
    if sub.status != StaffSubmission.Status.PENDING:
        return sub
    sub.status = StaffSubmission.Status.REJECTED
    sub.points_awarded = 0
    sub.approver = approver if getattr(approver, 'pk', None) else None
    sub.approver_email = (getattr(approver, 'email', '') or '')[:254]
    sub.decided_at = timezone.now()
    sub.reason = reason or sub.reason
    sub.save(update_fields=['status', 'points_awarded', 'approver',
                            'approver_email', 'decided_at', 'reason',
                            'updated_at'])
    return sub


# ---------------------------------------------------------------------------
# Task performance -> staff rewards (CFO directive 2026-07-13)
# ---------------------------------------------------------------------------

def _employee_for_user(user) -> Employee | None:
    """Resolve a Django user to their payroll.Employee — linked user first,
    then email. Mirrors staff_rewards.api_views._resolve_employee. Returns None
    when the user has no staff record (e.g. a system account)."""
    if user is None:
        return None
    emp = getattr(user, 'employee_record', None)
    if emp is not None:
        return emp
    email = (getattr(user, 'email', '') or '').strip()
    if email:
        return Employee.objects.filter(email__iexact=email).first()
    return None


def _task_performance_target(decision: str, completion_pct=None) -> int:
    """Points a task SHOULD have credited given the assigner's current
    decision. done = full; partial = full scaled by completion %; anything
    else (not_done) = 0. Reads POINTS_RULES only."""
    rules = POINTS_RULES.get('task_performance', {})
    if decision == 'done':
        return int(rules.get('done', 0))
    if decision == 'partial':
        pct = max(0, min(_as_int(completion_pct, 0), 100))
        return int(round(int(rules.get('partial', 0)) * pct / 100.0))
    return int(rules.get('not_done', 0))


@transaction.atomic
def award_task_performance(task, decision, *, completion_pct=None):
    """Reconcile an OmniTask's Staff Rewards contribution to the assigner's
    current Done/Partial/Not-done decision.

    Posts the DELTA between the decision's target points and what this task has
    already credited (``task.performance_points_awarded``), so:
      * marking Done credits the points once,
      * re-saving the same decision is a no-op (delta 0 — idempotent),
      * flipping Done -> Not done forfeits exactly what it earned.
    Because a task never subtracts more than it previously added, an account is
    never driven below its other legitimately-earned points. Feeds the Business
    Impact pillar (configurable in points_rules). No-op if the assignee has no
    payroll record. Returns the delta applied (or None if nothing changed)."""
    emp = _employee_for_user(getattr(task, 'assignee', None))
    if emp is None:
        return None

    # Lock + re-read the task row: `prior` must come from a row locked in THIS
    # transaction, or two near-simultaneous identical decisions both see the
    # stale pre-award value and each post the full delta (double-credit race —
    # Fable review fix, 2026-07-13).
    task = type(task).objects.select_for_update().get(pk=task.pk)

    target = _task_performance_target(decision, completion_pct)
    prior = int(getattr(task, 'performance_points_awarded', 0) or 0)
    delta = target - prior
    if delta == 0:
        return None

    rules = POINTS_RULES.get('task_performance', {})
    pillar = rules.get('pillar', 'business_impact')
    pillar_field = PILLAR_FIELD.get(pillar, 'business_impact_points')

    account = StaffPointsAccount.objects.select_for_update().get_or_create(
        employee=emp)[0]

    StaffPointsTransaction.objects.create(
        account=account,
        submission=None,
        points=delta,
        kind=StaffPointsTransaction.Kind.ADJUST,
        pillar=pillar,
        detail=f'Task performance: {decision} — {(getattr(task, "title", "") or "")[:60]}',
        occurred_at=timezone.now(),
    )

    account.points_balance = (account.points_balance or 0) + delta
    setattr(account, pillar_field, (getattr(account, pillar_field, 0) or 0) + delta)
    account.save(update_fields=['points_balance', pillar_field, 'updated_at'])

    task.performance_points_awarded = target
    task.save(update_fields=['performance_points_awarded', 'updated_at'])
    return delta
