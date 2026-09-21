"""core/review_demo.py — the locked APP-STORE REVIEWER identity (CFO 2026-09-06).

Apple and Google reviewers must sign in and browse the Omni staff phone app
before they will approve the listing. Handing them a normal staff login would
show them real salaries, real approvals and real customer names — a Data
Protection Act breach. So there is ONE review identity, and it is boxed in:

  * it signs in WITHOUT an emailed code (a reviewer cannot read our mailbox) —
    see the two short bypasses in core/staff_login_views.py;
  * every screen it opens is served INVENTED demo data by the middleware below,
    never the database;
  * every write it attempts is answered with a polite canned success and
    NOTHING reaches the database — so a reviewer tapping Approve sees the happy
    path instead of an error;
  * the whole thing is inert unless OMNI_REVIEW_MODE is switched on, which is
    the kill switch: flip it off the day the listing is approved.

Belt and braces: REVIEW_USERNAME is also in core.screenshot_bot's
READ_ONLY_USERNAMES, so even if the middleware ever missed a path the
AUTHENTICATION layer (core/token_auth.py, core/device_auth.py) still refuses
the identity any non-safe method.

The people in the fixtures below — Neo, Tumelo Baruti, Lorato Sereto, Kabelo
Moeng, Amantle Dintwe — are INVENTED. Nothing here is a real person, a real
policy or a real number. They are the same fixtures already proven to render
every app screen correctly in the store-screenshot run.
"""
from __future__ import annotations

from django.conf import settings
from django.http import JsonResponse

REVIEW_USERNAME = 'omni-app-review'
SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')

# The canned answer to any write. A reviewer tapping Approve must see success,
# not a 403 they will read as a broken app.
DEMO_WRITE_OK = {'ok': True, 'detail': 'Demo mode — nothing was changed.'}

# Every data path the app can reach. An API path the reviewer opens that is NOT
# in DEMO must still never hit a real view — a store reviewer seeing one real
# salary or one real customer name is the whole thing we are preventing — so
# anything under these prefixes is answered with nothing at all.
API_PREFIXES = ('/api/', '/hris/api/')
DEMO_EMPTY: dict = {}


def review_email() -> str:
    return str(getattr(settings, 'OMNI_REVIEW_EMAIL', '') or '').strip().lower()


def review_code() -> str:
    return str(getattr(settings, 'OMNI_REVIEW_CODE', '') or '').strip()


def review_mode_on() -> bool:
    """The kill switch. OFF unless deliberately armed AND fully configured.

    Both the email and the code must be set: a half-configured switch must not
    open a login path keyed on an empty string.
    """
    return bool(getattr(settings, 'OMNI_REVIEW_MODE', False)
                and review_email() and review_code())


def is_review_user(user) -> bool:
    return getattr(user, 'username', '') == REVIEW_USERNAME


# ---------------------------------------------------------------------------
# Fictional demo data — invented people, invented numbers.
# ---------------------------------------------------------------------------
_ME = 'Neo'
_TEAM = ['Tumelo Baruti', 'Lorato Sereto', 'Kabelo Moeng', 'Amantle Dintwe']

HOME = {
    'as_of': '2026-09-06T07:30:00Z', 'first_name': _ME,
    'capabilities': {
        'view_personal_home': True, 'manage_team': True, 'give_monthly_feedback': True,
        'view_all_feedback': False, 'view_claims_workspace': True,
        'capture_claims_action': True, 'view_underwriting_workspace': True,
        'issue_underwriting_documents': True, 'view_bonu': False,
        'capture_bonu_claim': False, 'edit_bonu_legal': False,
        'view_finance_workspace': True, 'create_finance_transaction': True,
        'approve_finance_transaction': True, 'manage_fnb': False,
        'view_hr_workspace': True, 'manage_leave': True, 'manage_payroll': False,
        'manage_recruitment': False, 'view_executive_dashboard': False,
    },
    'approvals': {'streams': [
        {'key': 'payments', 'label': 'Payments to sign', 'count': 6, 'href': '/payments', 'oldest_days': 2},
        {'key': 'approvals', 'label': 'Approvals', 'count': 5, 'href': '/approvals', 'oldest_days': 4},
    ], 'total': 11},
    'my_tasks_open': 3,
}

APPROVAL_ITEMS = {'total': 5, 'streams': [
    {'key': 'leave', 'label': 'Leave requests to approve', 'href': '/approvals', 'bulk_ok': True, 'items': [
        {'id': 'a1', 'title': 'Tumelo Baruti', 'sub': 'Annual leave · 12–16 Oct · 5 days',
         'amount': None, 'ccy': '', 'age_days': 2, 'flags': []},
        {'id': 'a2', 'title': 'Lorato Sereto', 'sub': 'Sick leave · 3 Sep · 1 day',
         'amount': None, 'ccy': '', 'age_days': 1, 'flags': []},
    ]},
    {'key': 'po', 'label': 'Purchase orders to approve', 'href': '/approvals', 'bulk_ok': True, 'items': [
        {'id': 'b1', 'title': 'Office network switch — Gaborone', 'sub': 'Kalahari IT Supplies',
         'amount': '8450.00', 'ccy': 'BWP', 'age_days': 3, 'flags': []},
        {'id': 'b2', 'title': 'Branch signage refresh', 'sub': 'Boitumelo Signs',
         'amount': '12300.00', 'ccy': 'BWP', 'age_days': 6, 'flags': []},
    ]},
    {'key': 'loan', 'label': 'Staff loans to approve', 'href': '/approvals', 'bulk_ok': True, 'items': [
        {'id': 'c1', 'title': 'Kabelo Moeng', 'sub': 'Staff loan · repay over 6 months',
         'amount': '1500.00', 'ccy': 'BWP', 'age_days': 4, 'flags': []},
    ]},
]}

TASKS = [
    {'id': 't1', 'title': 'Confirm September reinsurance bordereau',
     'body': 'Check the cession split before it goes to the reinsurer.',
     'due_at': '2026-09-08T14:00:00Z', 'due_time': '16:00', 'priority': 'high',
     'status': 'pending', 'completion_pct': None, 'assignee': 1, 'assignee_name': _ME},
    {'id': 't2', 'title': 'Sign off branch petty cash — August',
     'body': 'Three floats to reconcile.',
     'due_at': '2026-09-09T10:00:00Z', 'due_time': '12:00', 'priority': 'normal',
     'status': 'in_progress', 'completion_pct': 60, 'assignee': 1, 'assignee_name': _ME},
    {'id': 't3', 'title': 'Review broker loss ratios', 'body': 'Flag anything above 70%.',
     'due_at': '2026-09-11T10:00:00Z', 'due_time': '12:00', 'priority': 'normal',
     'status': 'pending', 'completion_pct': None, 'assignee': 1, 'assignee_name': _ME},
]

OVERVIEW = {'as_of': '2026-09-06T07:30:00Z', 'window_days': 7, 'reward_per_task': 0,
            'reward_min_tasks': 0, 'reward_per_point': 0, 'reward_point_min': 0,
            'reward_cap': 0, 'groups': [], 'standings': [], 'tasks': TASKS}

LEAVE_BAL = {'year': 2026, 'has_profile': True, 'balances': [
    {'code': 'ANN', 'name': 'Annual leave', 'days': 22, 'accrued': 16, 'used': 6, 'encashed': 0,
     'available': 10, 'remaining': 10, 'carry_over_cap': 5, 'accrues': True, 'paid_pct': 100,
     'cos': '', 'rule': '', 'source': 'profile'},
    {'code': 'SICK', 'name': 'Sick leave', 'days': 20, 'accrued': 20, 'used': 2, 'encashed': 0,
     'available': 18, 'remaining': 18, 'carry_over_cap': 0, 'accrues': False, 'paid_pct': 100,
     'cos': '', 'rule': '', 'source': 'profile'},
    {'code': 'COMP', 'name': 'Compassionate leave', 'days': 5, 'accrued': 5, 'used': 0, 'encashed': 0,
     'available': 5, 'remaining': 5, 'carry_over_cap': 0, 'accrues': False, 'paid_pct': 100,
     'cos': '', 'rule': '', 'source': 'profile'},
]}

GLANCE = {'as_of': '2026-09-06T07:30:00Z',
          'counts': {'in': 8, 'on_leave': 1, 'dark': 0, 'overdue': 2},
          'reports': [{'id': i + 1, 'name': n, 'status': 'on_leave' if i == 1 else 'in',
                       'tracked_hours': '7.4', 'open_tasks': i, 'overdue': 1 if i == 3 else 0}
                      for i, n in enumerate(_TEAM)]}


def _slip(period, gross, paye, net):
    return {'id': 'p' + period.replace(' ', ''), 'period': period,
            'company': 'Alpha Direct Insurance', 'gross': gross, 'paye': paye, 'net': net,
            'currency': 'BWP', 'status': 'released', 'available': True,
            'detail': {'earnings': [{'label': 'Basic salary', 'amount': gross}],
                       'deductions': [{'label': 'PAYE', 'amount': paye}]}}


PAYSLIPS = {'payslips': [
    _slip('August 2026', '24000.00', '3837.50', '20162.50'),
    _slip('July 2026', '24000.00', '3837.50', '20162.50'),
    _slip('June 2026', '23400.00', '3687.50', '19712.50'),
], 'detail': ''}


# Request path -> canned response. Every screen the reviewer can reach.
DEMO = {
    '/api/v1/mobile/home/': HOME,
    '/api/v1/my-approvals/': {'streams': HOME['approvals']['streams'], 'total': 11},
    '/api/v1/my-approvals/items/': APPROVAL_ITEMS,
    '/api/v1/taskboard/my-tasks/': TASKS,
    '/api/v1/taskboard/overview/': OVERVIEW,
    '/api/v1/team/glance/': GLANCE,
    '/hris/api/leave-balances/': LEAVE_BAL,
    '/hris/api/leave-requests/mine/': {'count': 0, 'requests': []},
    '/hris/api/leave-requests/queue/': {'count': 0, 'pending': []},
    '/api/v1/payroll/my-payslips/': PAYSLIPS,
    '/api/v1/expense-claims/queue/': [],
    '/api/v1/expense-claims/cfo-queue/': [],
    '/api/v1/spend-requests/': {'can_approve': True, 'requests': []},
    '/api/v1/payment-requests/bulk/preview/': {'ready': [], 'blocked': [], 'ready_total': '0.00',
                                               'ready_count': 0, 'blocked_count': 0},
    '/api/v1/timedoctor/justifications/pending/': {'items': [], 'aria_summary': ''},
    '/hris/api/performance/monthly/': {'tier': '', 'period': {'year': 2026, 'month': 9},
                                       'employees': []},
}


def _review_session_user(request):
    """The review user IF this request carries the review identity's device
    token, else None.

    Resolved here rather than from request.user because DRF authenticates
    inside the view — at middleware time request.user is still anonymous.
    Every gate below fails CLOSED to None, so for any other caller this
    middleware is a complete no-op.
    """
    header = request.META.get('HTTP_AUTHORIZATION', '')
    parts = header.split(' ', 1)
    if len(parts) != 2:
        return None
    scheme, raw = parts[0].lower(), parts[1].strip()
    if len(raw) != 64 or '.' in raw:          # never a JWT; same shape rule as device_auth
        # A DRF token is 40 chars, so fall through rather than returning here.
        if not (scheme == 'token' and raw and '.' not in raw):
            return None
    from django.utils import timezone

    user = None
    if scheme == 'bearer':
        from core.device_session_models import StaffDeviceSession
        sess = (StaffDeviceSession.objects.select_related('user')
                .filter(token_hash=StaffDeviceSession.hash_token(raw)).first())
        if sess is None or sess.revoked_at is not None or sess.expires_at <= timezone.now():
            return None
        user = sess.user
    elif scheme == 'token':
        # H96: an interceptor keyed on ONE credential shape is blind to every other
        # way the same identity can authenticate. The review identity can also hold a
        # DRF token; without this branch its GETs reached real views.
        from rest_framework.authtoken.models import Token as DRFToken
        tok = DRFToken.objects.select_related('user').filter(key=raw).first()
        if tok is None:
            return None
        user = tok.user
    else:
        return None

    if user is None or not user.is_active or not is_review_user(user):
        return None
    sess = type('_S', (), {'user': user})()
    return sess.user


class ReviewDemoMiddleware:
    """Serve the store reviewer invented data, and swallow their writes.

    Inert unless review mode is armed AND the caller is the review identity.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not review_mode_on():
            return self.get_response(request)
        if _review_session_user(request) is None:
            return self.get_response(request)
        method = (request.method or 'GET').upper()
        if method in SAFE_METHODS:
            payload = DEMO.get(request.path)
            if payload is not None:
                return JsonResponse(payload, safe=False)
            # Deny by default: no real data may be read by this identity. Pages,
            # scripts, styles and images still load normally — only data does not.
            if request.path.startswith(API_PREFIXES):
                return JsonResponse(DEMO_EMPTY, safe=False)
            return self.get_response(request)
        # A write. Never reaches a view, so never reaches the database.
        return JsonResponse(DEMO_WRITE_OK)
