"""
taskboard/cfo_views.py — task oversight, performance feedback, evidence-backed
status transitions, personal board, and weekly-plan ingest.

CFO directive 2026-07-13 (weekly-planning tasks). Managers see the tasks THEY
assigned; a superuser (CFO) sees all. Per-person visibility is scoped to tasks
you assigned — covered by the live staff privacy notice + the CFO as data
controller (DPA 2024).
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db.models import F, Q
from django.utils import timezone
from rest_framework import status as http
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import OmniTask, OmniTaskComment, TaskFeedback

logger = logging.getLogger(__name__)

from . import services
from .serializers import (
    OmniTaskBriefSerializer,
    TaskDetailSerializer,
    TaskFeedbackCreateSerializer,
    TaskFeedbackSerializer,
    TaskStatusUpdateSerializer,
)

OPEN = [OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS,
        OmniTask.Status.PARTIAL, OmniTask.Status.BLOCKED]

# ── Fair Delivery Score — "The Alpha League" (CFO 2026-08-26) ────────────────
# Recognition is based on what you FINISH in a rolling window, priority-weighted,
# with an on-time bonus and a CAPPED late-tax that can never wipe out delivery.
# Backlog / pending is NEVER penalised — only currently-overdue work costs, and
# only up to 20% of what you delivered. This replaces pct_done ranking, which
# punished high-volume people (160 done, 10 pending) and over-rewarded a 1-of-1.
# DISPLAY ONLY: this is NOT the Staff Rewards ledger (staff_rewards.points_rules,
# decision-driven, needs HR + CFO sign-off) — do not unify the two.
PRIORITY_WEIGHT = {
    OmniTask.Priority.LOW: 1, OmniTask.Priority.NORMAL: 2,
    OmniTask.Priority.HIGH: 3, OmniTask.Priority.URGENT: 5,
}
STANDINGS_WINDOW_DAYS = 14
ONTIME_BONUS = 0.25          # fraction of the task weight, added when finished on time
LATE_TAX_PER_TASK = 1.0      # points per currently-overdue open task
LATE_TAX_CAP_FRAC = 0.20     # late-tax can never exceed 20% of delivered+bonus

# ── Task incentive (CFO 2026-08-26). Priority-WEIGHTED (2026-08-26 v2, /recc):
# recognition follows task WEIGHT, not a flat count — a hard task is worth more
# than a trivial one. Each MANAGER-CONFIRMED task earns its PRIORITY_WEIGHT in
# points (low 1 / normal 2 / high 3 / urgent 5). A NORMAL task = 2 points, so at
# BWP 25/point a normal task is still worth BWP 50 and the 60-point threshold is
# still ~30 normal tasks — the CFO's original "P50 above 30" holds for ordinary
# work, while genuinely hard work now pays more. Cap unchanged at BWP 2,000.
# "Confirmed" = done AND a manager (a non-assignee) left feedback — so trivial
# self-marked tasks can't farm it. DISPLAY + it feeds a monthly incentive REQUEST
# routed to the CFO + HR — Omni never pays; money leaves only via the bank + 2FA.
REWARD_PER_POINT = 25        # BWP per priority-point above the threshold
REWARD_POINT_MIN = 60        # threshold in points (~30 normal tasks × 2)
REWARD_MONTHLY_CAP = 2000    # BWP cap per person per month (CFO 2026-08-26)
REWARD_PER_TASK = 50         # a NORMAL task (2 pts × 25) — kept for display/back-compat
REWARD_MIN_TASKS = 30        # ~equivalent normal-task minimum — kept for display

# Managers + ExCo do NOT earn the task incentive — they assign/oversee, they do
# not do the tasks (CFO 2026-08-26). System/admin accounts are excluded too.
NO_INCENTIVE_TITLES = frozenset({
    'ceo', 'coo', 'cfo', 'executive',
    'finance_manager', 'financial_controller',
    'claims_manager', 'operations_manager', 'hr_manager',
})

# A payment request / JE / payroll sign-off is an APPROVAL, not a task — it must
# never appear on the task board or count toward the incentive (CFO 2026-08-26).
# They live in the approvals panel (/my-approvals).
APPROVAL_SOURCES = frozenset({'payment_request'})
APPROVAL_TITLE_PREFIXES = ('Approve payment ', 'Approve JE ', 'Payroll sign-off')


def task_reward(points: int) -> int:
    """BWP earned for a month's priority-WEIGHTED confirmed points, capped. Single
    source of truth for the reward maths (dashboard + the generate command).
    A normal task = 2 points, so 30 normal tasks = 60 points = the threshold, and
    each point above earns BWP 25 (a normal task = BWP 50) — CFO 2026-08-26 v2."""
    return min(REWARD_MONTHLY_CAP, max(0, points - REWARD_POINT_MIN) * REWARD_PER_POINT)


def _real_tasks(qs):
    """Drop auto-generated approval items (payment / JE / payroll sign-off) —
    those are not tasks. Used by every dashboard query + the incentive count."""
    cond = Q()
    for p in APPROVAL_TITLE_PREFIXES:
        cond |= Q(title__startswith=p)
    return qs.exclude(source__in=APPROVAL_SOURCES).exclude(cond)


def _excluded_earner_ids() -> set:
    """User ids that do NOT earn the task incentive: managers + ExCo (by title)
    plus system/admin accounts (superuser/staff). CFO 2026-08-26."""
    from django.contrib.auth.models import User
    from core.models import UserProfile
    ids = set(User.objects.filter(Q(is_superuser=True) | Q(is_staff=True))
              .values_list('id', flat=True))
    ids |= set(UserProfile.objects.filter(title__in=NO_INCENTIVE_TITLES)
               .values_list('user_id', flat=True))
    return ids


def confirmed_counts(base_qs, start, end=None):
    """Per-assignee MANAGER-CONFIRMED completions in [start, end): the task is done
    (and NOT an approval item), completed in the window, and a manager (a
    non-assignee) left feedback on it — the anti-gaming rule the CFO chose.
    Managers + ExCo are excluded (they don't earn). Returns, per person, both the
    COUNT (n) and the priority-WEIGHTED points (Σ weight) — points drives the
    reward (v2), the count is for display. ONE definition, used by the dashboard
    reward AND the incentive command (no drift).

    Points are summed in PYTHON over DISTINCT tasks: a task with two manager
    feedbacks would be double-counted by a SQL Sum across the feedback join, so we
    pull distinct (task, priority) rows and add the weights ourselves. Returns a
    list of dicts (not a queryset) — callers sort in Python."""
    q = (_real_tasks(base_qs)
         .filter(status=OmniTask.Status.DONE, completed_at__date__gte=start)
         .exclude(assignee_id__in=_excluded_earner_ids())
         .filter(feedback__isnull=False)
         .exclude(feedback__from_user=F('assignee')))
    if end is not None:
        q = q.filter(completed_at__date__lt=end)
    rows = q.values('id', 'priority', 'assignee_id', 'assignee__first_name',
                    'assignee__last_name', 'assignee__username').distinct()
    agg: dict[int, dict] = {}
    for r in rows:
        a = agg.setdefault(r['assignee_id'], {
            'assignee_id': r['assignee_id'],
            'assignee__first_name': r['assignee__first_name'],
            'assignee__last_name': r['assignee__last_name'],
            'assignee__username': r['assignee__username'],
            'n': 0, 'points': 0})
        a['n'] += 1
        a['points'] += PRIORITY_WEIGHT.get(r['priority'], 2)
    return list(agg.values())


def _monthly_rewards(user) -> dict:
    """{assignee_id: {'confirmed_month': n, 'points_month': p, 'reward_bwp': bwp}}
    for the current calendar month, for the tasks this manager oversees."""
    start = timezone.localdate().replace(day=1)
    return {r['assignee_id']: {'confirmed_month': r['n'], 'points_month': r['points'],
                               'reward_bwp': task_reward(r['points'])}
            for r in confirmed_counts(_visible_tasks(user), start)}

# CFO shorthand for the managers he works with (2026-07-13).
ALIASES = {
    'lg': 'legakwa', 'kt': 'keetile', 'medu': 'meduduetso', 'oprah': 'oprah',
    'bharath': 'bharath', 'tshephang': 'tshephang', 'tlamelo': 'tlamelo',
}


def _monday(d: date | None = None) -> date:
    d = d or timezone.localdate()
    return d - timedelta(days=d.weekday())


def _can_oversee(user) -> bool:
    """True for task-dashboard oversight (paste-plan ingest, overview, leaderboard).

    Bug 2221ece5 (Kago Tshutlhedi, 2026-07-14): this checked only is_superuser /
    is_staff, so genuine managers — e.g. a finance_manager, who is NOT a Django
    staff user — got "Managers only." and could not paste their weekly plan.
    Widen to the HRIS manager tier (the same set timedoctor_views._can_view
    trusts). Safe: the per-view queries stay scoped to the caller's own tasks
    (_visible_tasks filters assigner=user for non-superusers), so a manager still
    only ever sees / creates tasks they themselves assigned.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser or user.is_staff:
        return True
    try:
        from core.hris_access import hris_role
        return hris_role(user) in {'mgr', 'hr', 'hris', 'admin', 'superadmin', 'ceo'}
    except Exception:      # noqa: BLE001
        return False


def _visible_tasks(user):
    # Approval items (payment / JE / payroll sign-off) are NOT tasks — they show
    # in the approvals panel, never on the task board (CFO 2026-08-26).
    qs = _real_tasks(OmniTask.objects.exclude(status=OmniTask.Status.CANCELLED))
    return qs if user.is_superuser else qs.filter(assigner=user)


def _match_user(token: str):
    """Resolve a first-name / alias / username to a single active User, else None."""
    t = (token or '').strip().lower()
    if not t:
        return None
    t = ALIASES.get(t, t)
    q = (User.objects.filter(is_active=True)
         .filter(Q(first_name__iexact=t) | Q(username__iexact=t)
                 | Q(username__istartswith=t + '.') | Q(first_name__istartswith=t)))
    users = list(q[:3])
    return users[0] if len(users) == 1 else None


def _person_groups(user, this_week=False):
    """Tasks grouped by assignee with completion + overdue counts. Shared by the
    overview table and the hall-of-fame ranking. Returns (queryset, groups)."""
    # payment_request is the reverse FK OmniTaskBriefSerializer reads to label a
    # payment task — without the prefetch it is 2 queries per payment row, and
    # for the CFO this queryset is every task ever raised (K6).
    qs = (_visible_tasks(user).select_related('assignee', 'assigner')
          .prefetch_related('payment_request'))
    if this_week:
        qs = qs.filter(week_of=_monday())
    groups: dict[int, dict] = {}
    for t in qs:
        g = groups.setdefault(t.assignee_id, {
            'assignee_id': t.assignee_id,
            'assignee_name': t.assignee.get_full_name() or t.assignee.username,
            'total': 0, 'done': 0, 'in_progress': 0, 'blocked': 0,
            'partial': 0, 'pending': 0, 'overdue': 0,
        })
        g['total'] += 1
        g[t.status] = g.get(t.status, 0) + 1
        if services.is_overdue(t):
            g['overdue'] += 1
    for g in groups.values():
        g['pct_done'] = round(100 * g['done'] / g['total']) if g['total'] else 0
    return qs, list(groups.values())


def _finished_on_time(t) -> bool:
    """A done task counts as on-time only if it had a due date and was completed
    on/before it. No due date = no bonus AND no penalty (fair — you can't be late
    for a deadline that was never set)."""
    if not t.due_at or not t.completed_at:
        return False
    if t.due_time:
        import datetime as _dt
        due_dt = timezone.make_aware(_dt.datetime.combine(t.due_at, t.due_time))
        return t.completed_at <= due_dt
    return timezone.localtime(t.completed_at).date() <= t.due_at


def _score_over(qs, *, this_week=False):
    """Fair Delivery Score rows for every assignee in qs (NO reward attached).
    Sorted by score desc, each row carrying the full breakdown so every score is
    explainable, never a black box. The score formula lives HERE and nowhere else
    — shared by the manager standings and the staff 'My League' page."""
    cutoff = timezone.now() - timedelta(days=STANDINGS_WINDOW_DAYS)
    if this_week:
        qs = qs.filter(week_of=_monday())
    agg: dict[int, dict] = {}
    for t in qs:
        a = agg.setdefault(t.assignee_id, {
            'assignee_id': t.assignee_id,
            'assignee_name': t.assignee.get_full_name() or t.assignee.username,
            '_delivered': 0.0, '_bonus': 0.0, 'done_14d': 0, '_ontime': 0,
            'open': 0, 'overdue': 0, 'blocked': 0, 'total': 0, 'done': 0,
        })
        a['total'] += 1
        w = PRIORITY_WEIGHT.get(t.priority, 2)
        if t.status == OmniTask.Status.DONE:
            a['done'] += 1
            done_at = t.completed_at or t.updated_at          # fallback for old rows
            if this_week or (done_at and done_at >= cutoff):
                a['_delivered'] += w
                a['done_14d'] += 1
                if _finished_on_time(t):
                    a['_bonus'] += ONTIME_BONUS * w
                    a['_ontime'] += 1
        else:
            if t.status in OPEN:
                a['open'] += 1
            if t.status == OmniTask.Status.BLOCKED:
                a['blocked'] += 1
            if services.is_overdue(t):
                a['overdue'] += 1
    rows = []
    for a in agg.values():
        base = a['_delivered'] + a['_bonus']
        late_tax = min(LATE_TAX_PER_TASK * a['overdue'], LATE_TAX_CAP_FRAC * base)
        rows.append({
            'assignee_id': a['assignee_id'], 'assignee_name': a['assignee_name'],
            'score': max(0, round(base - late_tax)),
            'delivered': round(a['_delivered']), 'bonus': round(a['_bonus']),
            'late_tax': round(late_tax),
            'done_14d': a['done_14d'],
            'ontime_rate': round(100 * a['_ontime'] / a['done_14d']) if a['done_14d'] else 0,
            'open': a['open'], 'overdue': a['overdue'], 'blocked': a['blocked'],
            'total': a['total'], 'done': a['done'],
            'pct_done': round(100 * a['done'] / a['total']) if a['total'] else 0,
        })
    return sorted(rows, key=lambda x: (-x['score'], -x['done_14d'], x['assignee_name']))


def _standings(user, *, this_week=False):
    """Manager view: fair score rows for the tasks this manager oversees, each with
    the monthly BWP incentive (priority-weighted points above 60 × P25) attached."""
    rows = _score_over(_visible_tasks(user).select_related('assignee', 'assigner'),
                       this_week=this_week)
    rewards = _monthly_rewards(user)
    for r in rows:
        rw = rewards.get(r['assignee_id'], {})
        r['confirmed_month'] = rw.get('confirmed_month', 0)
        r['points_month'] = rw.get('points_month', 0)
        r['reward_bwp'] = rw.get('reward_bwp', 0)
    return rows


class TaskOverviewView(APIView):
    """CFO/manager dashboard data: tasks grouped by person with completion + overdue."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _can_oversee(request.user):
            return Response({'detail': 'Managers only.'}, status=http.HTTP_403_FORBIDDEN)
        this_week = request.query_params.get('week') == 'this'
        qs, groups = _person_groups(request.user, this_week=this_week)
        # Keep the visible task list to ~2 weeks of finished work: hide (never
        # delete) done tasks completed more than 14 days ago, unless ?all_done=1.
        # The score is computed separately and is UNAFFECTED by this trim.
        if request.query_params.get('all_done') != '1':
            cutoff = timezone.now() - timedelta(days=STANDINGS_WINDOW_DAYS)
            qs = qs.exclude(
                Q(status=OmniTask.Status.DONE)
                & (Q(completed_at__lt=cutoff)
                   | (Q(completed_at__isnull=True) & Q(updated_at__lt=cutoff))))
        return Response({
            'as_of': timezone.localdate(),
            'window_days': STANDINGS_WINDOW_DAYS,
            # CFO-approved incentive policy (26-Aug-2026, via /recc) — sent so the
            # UI never hardcodes the money figures (single source of truth here).
            'reward_per_task': REWARD_PER_TASK,
            'reward_min_tasks': REWARD_MIN_TASKS,
            'reward_per_point': REWARD_PER_POINT,
            'reward_point_min': REWARD_POINT_MIN,
            'reward_cap': REWARD_MONTHLY_CAP,
            'groups': sorted(groups, key=lambda x: (-x['overdue'], -x['total'])),
            'standings': _standings(request.user, this_week=this_week),
            'tasks': OmniTaskBriefSerializer(qs.order_by('due_at', '-priority'), many=True).data,
        })


class MyLeagueView(APIView):
    """Staff-facing 'My League' (CFO 2026-08-26 v2): the caller's OWN standing —
    their fair score + rank, this month's incentive progress, and what they have
    finished that is still waiting on a manager to confirm. EVERY authenticated
    user sees their own; no manager role needed (the manager dashboard stays
    manager-only). Returns only the caller's row — never anyone else's detail."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        me = request.user
        # Everyone's fair score (real tasks, by assignee) — used ONLY to rank the
        # caller; only the caller's own row is returned to the client.
        all_tasks = (_real_tasks(OmniTask.objects.exclude(status=OmniTask.Status.CANCELLED))
                     .select_related('assignee'))
        rows = _score_over(all_tasks)
        mine = next((r for r in rows if r['assignee_id'] == me.id), None)
        rank = next((i + 1 for i, r in enumerate(rows) if r['assignee_id'] == me.id), None)

        # This month's incentive for the caller (points above 60 × P25, capped).
        start = timezone.localdate().replace(day=1)
        cc = confirmed_counts(OmniTask.objects.filter(assignee=me), start)
        row = cc[0] if cc else {}
        points, confirmed = row.get('points', 0), row.get('n', 0)
        earns = me.id not in _excluded_earner_ids()

        # Done this month but NOT yet manager-confirmed (no non-assignee feedback)
        # — what is waiting, and on whom.
        done_month = (_real_tasks(OmniTask.objects.filter(assignee=me))
                      .filter(status=OmniTask.Status.DONE, completed_at__date__gte=start)
                      .select_related('assigner').prefetch_related('feedback'))
        waiting = [{
            'title': t.title,
            'waiting_on': (t.assigner.get_full_name() or t.assigner.username) if t.assigner else 'your manager',
        } for t in done_month if not t.feedback.exclude(from_user=me).exists()]

        return Response({
            'as_of': timezone.localdate(),
            'window_days': STANDINGS_WINDOW_DAYS,
            'me': mine or {
                'assignee_id': me.id, 'assignee_name': me.get_full_name() or me.username,
                'score': 0, 'done_14d': 0, 'ontime_rate': 0,
                'open': 0, 'overdue': 0, 'blocked': 0, 'total': 0, 'done': 0},
            'rank': rank, 'players': len(rows),
            'earns_incentive': earns,
            'reward': {
                'confirmed_month': confirmed, 'points_month': points,
                'reward_bwp': task_reward(points) if earns else 0,
                'point_min': REWARD_POINT_MIN, 'per_point': REWARD_PER_POINT,
                'cap': REWARD_MONTHLY_CAP,
                'points_to_bonus': max(0, REWARD_POINT_MIN - points),
            },
            'waiting_on_confirm': waiting,
        })


# ---------------------------------------------------------------------------
# Hall of Fame / Wall of Shame (CFO 2026-07-13) — gamify the task dashboard.
# A witty AI (Gemini/DeepSeek via reasoning_complete, CFO order — never forced
# Anthropic) glorifies the top finisher and playfully roasts the bottom one.
# ONLY first names + task counts go to the model (no PII); comments are cached
# so the model isn't called on every page load, with canned fallback lines.
# ---------------------------------------------------------------------------

CHAMP_SYSTEM = (
    'You are a witty Botswana office MC celebrating the top task-finisher of the '
    'fortnight on a staff leaderboard. Write ONE short punchy celebratory '
    'one-liner, max 18 words, proud and a little over the top, never cruel. A '
    'little Setswana flavour is welcome (Sharp sharp, O dire sentle). Respond '
    'with ONLY valid JSON: {"champion":"..."}.'
)

CANNED_CHAMP = [
    '{n} tops the league this fortnight. O dire sentle! 👑',
    'Sharp sharp! {n} delivered the most — everyone else, take notes.',
    '{n} is carrying this team. Absolute engine. 🏆',
    'Most finished, most on time. {n} is built different.',
]


def _first(name: str) -> str:
    return (name or '').strip().split(' ')[0] or 'Someone'


def _canned(pool: list[str], name: str) -> str:
    idx = int(hashlib.md5(_first(name).encode()).hexdigest(), 16) % len(pool)
    return pool[idx].format(n=_first(name))


def _champion_comment(champ: dict, *, force=False) -> dict:
    """One celebratory line for the league leader — cached by standings, AI with a
    canned fallback. Only the first name + counts go to the model (never PII)."""
    cn = _first(champ['assignee_name'])
    sig = f"{cn}:{champ['score']}:{champ['done_14d']}"
    key = 'taskfame2:' + hashlib.md5(sig.encode()).hexdigest()
    if not force:
        cached = cache.get(key)
        if cached:
            return cached
    line = _canned(CANNED_CHAMP, cn)
    source = 'canned'
    facts = (f'Champion: {cn} leads with a delivery score of {champ["score"]}, '
             f'finishing {champ["done_14d"]} tasks in the last 14 days.')
    try:
        from core.ai_assist import is_safe_for_ai, reasoning_complete
        if is_safe_for_ai(facts).safe:
            raw = reasoning_complete(
                facts + ' Write the celebratory line.',
                system_prompt=CHAMP_SYSTEM, response_format='json_object')
            line = (json.loads(raw).get('champion') or line).strip()[:160]
            source = 'ai'
    except Exception:                       # noqa: BLE001
        logger.info('champion AI comment unavailable — using canned line')
    result = {'comment': line, 'source': source}
    cache.set(key, result, 7200 if source == 'ai' else 600)
    return result


class TaskLeaderboardView(APIView):
    """The Alpha League — fair task standings. Managers only. Ranks the people you
    assigned by Delivery Score (what they finished in 14 days, priority-weighted,
    on-time bonus, capped late-tax), crowns the leader with a witty AI one-liner,
    and — instead of shaming anyone — surfaces who NEEDS SUPPORT (overdue/blocked)
    so a manager can step in. ?refresh=1 forces a fresh AI line; ?week=this scopes
    to this week's plan."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _can_oversee(request.user):
            return Response({'detail': 'Managers only.'}, status=http.HTTP_403_FORBIDDEN)
        standings = _standings(
            request.user, this_week=request.query_params.get('week') == 'this')
        if not standings:
            return Response({'champion': None, 'needs_support': None,
                             'standings': [], 'source': 'none'})

        top = standings[0] if standings[0]['score'] > 0 else None
        champion, source = None, 'none'
        if top:
            c = _champion_comment(top, force=request.query_params.get('refresh') == '1')
            champion = {**top, 'comment': c['comment']}
            source = c['source']

        # Needs support = whoever is actually stuck (>=2 overdue OR any blocked),
        # never the champion. Factual + constructive — no roast, no shame.
        support = [s for s in standings
                   if (s['overdue'] >= 2 or s['blocked'] > 0)
                   and not (top and s['assignee_id'] == top['assignee_id'])]
        needs_support = (max(support, key=lambda s: (s['overdue'], s['blocked']))
                         if support else None)

        return Response({
            'champion': champion,
            'needs_support': needs_support,
            'standings': standings[:12],
            'source': source,
        })


class TaskStatusUpdateView(APIView):
    """Assignee moves a task to in_progress / partial / blocked WITH a required
    justification, and evidence for blocked/partial (CFO 2026-07-13)."""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, task_id):
        task = OmniTask.objects.filter(pk=task_id).first()
        if not task:
            return Response({'detail': 'Task not found.'}, status=http.HTTP_404_NOT_FOUND)
        if task.assignee_id != request.user.id and not request.user.is_superuser:
            return Response({'detail': 'Only the assignee can update this task.'},
                            status=http.HTTP_403_FORBIDDEN)
        ser = TaskStatusUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        new_status = ser.validated_data['status']
        evidence = ser.validated_data.get('evidence')
        if new_status in (OmniTask.Status.BLOCKED, OmniTask.Status.PARTIAL) and not evidence:
            return Response(
                {'detail': 'Attach evidence (a screenshot or document) when a task is '
                           'blocked or only partly done.'},
                status=http.HTTP_400_BAD_REQUEST)
        if evidence and evidence.size > 10 * 1024 * 1024:
            return Response({'detail': 'Evidence file too large (10 MB limit).'},
                            status=http.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        OmniTaskComment.objects.create(
            task=task, author=request.user, body=ser.validated_data['body'],
            new_status=new_status, evidence=evidence)
        task.status = new_status
        task.save(update_fields=['status', 'updated_at'])
        return Response(TaskDetailSerializer(task).data)


class TaskFeedbackView(APIView):
    """CFO/assigner gives immediate performance feedback on a task (esp. unfinished).
    Shows on the assignee's personal board; they must acknowledge; feeds the ELRA
    performance check-ins at review time."""
    permission_classes = [IsAuthenticated]

    def post(self, request, task_id):
        task = OmniTask.objects.filter(pk=task_id).select_related('assignee').first()
        if not task:
            return Response({'detail': 'Task not found.'}, status=http.HTTP_404_NOT_FOUND)
        if not (request.user.is_superuser or task.assigner_id == request.user.id):
            return Response({'detail': 'Only the task assigner (or CFO) may give feedback.'},
                            status=http.HTTP_403_FORBIDDEN)
        ser = TaskFeedbackCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        body = ser.validated_data['body']
        decision = ser.validated_data.get('status')          # done | partial | not_done | None
        pct = ser.validated_data.get('completion_pct')

        # Optional status decision from the dashboard control (CFO 2026-07-13):
        # Done / Partially done (with %) / Not done. "Not done" must carry a
        # substantive note — at least 15 words — so it is never a bare rejection.
        # (Halved from 30 per CFO 2026-07-13: staff write extensively on refunds,
        # so the writing load elsewhere is lightened to 50%.)
        if decision:
            if decision == 'not_done' and len(body.split()) < 15:
                return Response(
                    {'detail': 'When a task is Not done, explain why in at least '
                               '15 words.'},
                    status=http.HTTP_400_BAD_REQUEST)
            if decision == 'partial' and pct not in (25, 50, 75, 100):
                return Response(
                    {'detail': 'Choose a completion percentage (25, 50, 75 or 100).'},
                    status=http.HTTP_400_BAD_REQUEST)
            status_map = {
                'done': OmniTask.Status.DONE,
                'partial': OmniTask.Status.PARTIAL,
                'not_done': OmniTask.Status.PENDING,
            }
            new_status = status_map[decision]
            task.status = new_status
            task.completion_pct = {'done': 100, 'not_done': 0}.get(decision, pct)
            # completed_at is the REAL first-completion time, and the 14-day league
            # window keys on it. A manager confirming 'Done' days after the work
            # was actually finished must NOT rewrite it to now() — that silently
            # slides the task in/out of the score window (reconciliation bug,
            # 2026-09-01). So stamp it only on the first move to Done; preserve an
            # existing time (e.g. set by the assignee's own complete_task); and
            # clear it only when the task is reopened off Done.
            if decision == 'done':
                if task.completed_at is None:
                    task.completed_at = timezone.now()
            else:
                task.completed_at = None
            task.save(update_fields=['status', 'completion_pct',
                                     'completed_at', 'updated_at'])
            # A 'Done' decision here bypasses complete_task(), so clear the
            # task's standing due/overdue reminders now — otherwise the
            # force-modal keeps nagging after the task is finished (CFO
            # 2026-07-15, "I finished it, why is it still showing?").
            if new_status in (OmniTask.Status.DONE, OmniTask.Status.CANCELLED):
                from . import services as _svc
                _svc.clear_task_reminders(task)
            OmniTaskComment.objects.create(
                task=task, author=request.user, body=body, new_status=new_status)

            # Gamification (CFO 2026-07-13): the assigner's Done/Partial/Not-done
            # decision feeds the assignee's Staff Rewards score — Done earns
            # points, a task later marked Not-done forfeits them. Best-effort:
            # a rewards error must never break the feedback itself.
            try:
                from staff_rewards.service import award_task_performance
                award_task_performance(task, decision, completion_pct=pct)
            except Exception:                       # noqa: BLE001
                logger.exception('staff-rewards task-performance award failed '
                                 'for task %s', task.id)

        fb = TaskFeedback.objects.create(
            task=task, from_user=request.user, to_user=task.assignee,
            body=body)
        return Response(TaskFeedbackSerializer(fb).data, status=http.HTTP_201_CREATED)


class AcknowledgeFeedbackView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, feedback_id):
        fb = TaskFeedback.objects.filter(pk=feedback_id, to_user=request.user).first()
        if not fb:
            return Response({'detail': 'Not found.'}, status=http.HTTP_404_NOT_FOUND)
        if not fb.acknowledged_at:
            fb.acknowledged_at = timezone.now()
            fb.save(update_fields=['acknowledged_at', 'updated_at'])
        return Response({'detail': 'ok', 'acknowledged_at': fb.acknowledged_at})


class TaskEvidenceView(APIView):
    """Authenticated download of a task comment's evidence file. Raw /media/
    URLs are not served in production (DEBUG=False) — evidence goes through
    this endpoint (Fable review fix, 2026-07-13). Visible to the comment
    author, the task's assigner/assignee, or a superuser."""
    permission_classes = [IsAuthenticated]

    def get(self, request, comment_id):
        from django.http import FileResponse, Http404
        c = (OmniTaskComment.objects.select_related('task')
             .filter(pk=comment_id).first())
        if not c or not c.evidence:
            raise Http404
        t = c.task
        if request.user.id not in (c.author_id, t.assigner_id, t.assignee_id) \
                and not request.user.is_superuser:
            return Response({'detail': 'Not your task.'}, status=http.HTTP_403_FORBIDDEN)
        return FileResponse(c.evidence.open('rb'), as_attachment=True,
                            filename=(c.evidence.name or 'evidence').split('/')[-1])


class PersonalBoardView(APIView):
    """Each employee's own board: open tasks, this-week done, feedback received."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        u = request.user
        open_tasks = (OmniTask.objects.filter(assignee=u, status__in=OPEN)
                      .prefetch_related('payment_request')
                      .order_by('due_at', '-priority'))
        done_week = (OmniTask.objects.filter(assignee=u, status=OmniTask.Status.DONE,
                                             completed_at__date__gte=_monday())
                     .prefetch_related('payment_request')
                     .order_by('-completed_at'))
        fb = TaskFeedback.objects.filter(to_user=u).select_related('task', 'from_user')
        return Response({
            'open': OmniTaskBriefSerializer(open_tasks, many=True).data,
            'done_this_week': OmniTaskBriefSerializer(done_week, many=True).data,
            'feedback': TaskFeedbackSerializer(fb, many=True).data,
            'unacknowledged_feedback': fb.filter(acknowledged_at__isnull=True).count(),
        })


class IngestPlanView(APIView):
    """Paste the weekly planning message -> auto-create + assign tasks. Manager only.
    Body: {text, due_at?}. Each line like 'Name - task...' or '3. Name: task'."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not _can_oversee(request.user):
            return Response({'detail': 'Managers only.'}, status=http.HTTP_403_FORBIDDEN)
        text = (request.data.get('text') or '').strip()
        if not text:
            return Response({'detail': 'Paste the plan text.'}, status=http.HTTP_400_BAD_REQUEST)
        commit = str(request.data.get('commit', '')).lower() in ('1', 'true', 'yes')
        week = _monday()
        # No auto-deadline (CFO 2026-07-13 "be fair") — reminders fire only on the
        # deadline day, so ingested tasks carry no due date until one is set.
        import re
        created, unmatched = [], []
        for raw in text.splitlines():
            line = re.sub(r'^\s*\d+[\.\)]\s*', '', raw).strip()   # strip "1." / "2)"
            if len(line) < 4:
                continue
            m = re.match(r'^([A-Za-z]+)\s*[-:–]\s*(.+)$', line)
            if not m:
                continue
            who, what = m.group(1), m.group(2).strip()
            user = _match_user(who)
            if not user:
                unmatched.append(who); continue
            title = (what[:120]) if what else line[:120]
            if commit:
                t = OmniTask.objects.create(
                    assigner=request.user, assignee=user, title=title, body=what,
                    week_of=week, source='planning_meeting', due_at=None,
                    priority=OmniTask.Priority.NORMAL)
                services.notify_on_assign(t)
            created.append({'assignee': user.get_full_name() or user.username, 'title': title})
        return Response({'week_of': week, 'due_at': None, 'commit': commit,
                         'created': created, 'created_count': len(created),
                         'unmatched': sorted(set(unmatched))})
