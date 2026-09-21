#!/usr/bin/env bash
# rewards/test_health_points_shell.sh — idempotency smoke test for Health Points.
# Run from the repo root (or inside the backend container). Asserts that
# re-posting the SAME member+date does NOT double-award points.
#
#   python manage.py shell -c "$(cat rewards/test_health_points_shell.sh)"
#
# It uses a throwaway test member and cleans up after itself.

python manage.py shell -c "
from django.utils import timezone
from rewards.models import RewardMember, HealthMetric, PointsTransaction, RewardProgram
from rewards import health_points
from rewards.api_views import _get_health_program

# Pure-function sanity (no weights surprise).
assert health_points.steps_to_points(0) == 0
assert health_points.steps_to_points(999) == 0
assert health_points.steps_to_points(4500) == 4
assert health_points.steps_to_points(20000) == 15  # daily cap
assert health_points.tier_for(0) == 'bronze'
assert health_points.tier_for(600) == 'silver'
print('pure-function checks PASS')

# Throwaway member (DPA: name is a test label, not a real customer).
m = RewardMember.objects.create(customer_name='ZZ_TEST_HEALTH', points_balance=0)
program = _get_health_program()
today = timezone.localdate()

def post(steps):
    new_points = health_points.steps_to_points(steps)
    metric, created = HealthMetric.objects.get_or_create(
        member=m, date=today,
        defaults={'steps': steps, 'points_awarded': new_points})
    prior = 0 if created else metric.points_awarded
    delta = new_points - prior
    if not created:
        metric.steps = steps; metric.points_awarded = new_points
        metric.save(update_fields=['steps','points_awarded','updated_at'])
    txn, tc = PointsTransaction.objects.get_or_create(
        member=m, program=program, kind=PointsTransaction.Kind.EARN,
        occurred_at__date=today,
        defaults={'points': new_points, 'occurred_at': timezone.now()})
    if not tc:
        txn.points = new_points; txn.save(update_fields=['points','updated_at'])
    m.points_balance = (m.points_balance or 0) + delta
    m.tier = health_points.tier_for(m.points_balance)
    m.save(update_fields=['points_balance','tier','updated_at'])
    return new_points

# First post: 4500 steps -> 4 points.
p1 = post(4500)
m.refresh_from_db()
assert p1 == 4, p1
assert m.points_balance == 4, m.points_balance
assert HealthMetric.objects.filter(member=m, date=today).count() == 1
assert PointsTransaction.objects.filter(member=m, kind='earn').count() == 1
print('after first post: balance =', m.points_balance)

# Re-post SAME day, SAME steps: must NOT double-award.
p2 = post(4500)
m.refresh_from_db()
assert m.points_balance == 4, ('DOUBLE-AWARD BUG: balance=%s' % m.points_balance)
assert HealthMetric.objects.filter(member=m, date=today).count() == 1
assert PointsTransaction.objects.filter(member=m, kind='earn').count() == 1
print('after re-post (same): balance =', m.points_balance, '-> no double-award PASS')

# Re-post SAME day, MORE steps (12000 -> 12): balance becomes 12, not 16.
p3 = post(12000)
m.refresh_from_db()
assert m.points_balance == 12, ('DELTA BUG: balance=%s' % m.points_balance)
assert PointsTransaction.objects.filter(member=m, kind='earn').count() == 1
print('after re-post (more):  balance =', m.points_balance, '-> delta-correct PASS')

# Cleanup.
PointsTransaction.objects.filter(member=m).delete()
HealthMetric.objects.filter(member=m).delete()
m.delete()
print('ALL HEALTH-POINTS IDEMPOTENCY CHECKS PASS')
"
