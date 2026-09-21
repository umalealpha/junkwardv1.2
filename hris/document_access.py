"""
hris/document_access.py — who may see a personal HR document.

CFO directive 2026-07-18 (Development Dialogues): a staff member's own review
is confidential. It may be seen ONLY by:
  * the employee it is about (self),
  * that employee's direct line manager,
  * the CEO / CFO / COO (the executive tier),
  * the HR team.
Everyone else is refused — including other employees and other managers.

Non-admins are additionally restricted to SELF_VISIBLE_CATEGORIES, so opening
the employee/manager surface never exposes HR-only records (disciplinary, exit
interviews, contracts) that also live in the same vault.
"""

from __future__ import annotations

import os

from django.db.models import Q

# Categories an employee / line manager may reach for themselves. Everything
# else in the vault stays HR-only.
SELF_VISIBLE_CATEGORIES = {'development_dialogue'}

# HR-team role assignments (Unami / Dorothy / Thapelo). NB: a generic omni
# `is_administrator` / Finance-manager is deliberately NOT here — being an app
# admin or Finance leadership does NOT grant sight of everyone's review. Only
# the C-suite and the HR team do (CFO directive 2026-07-18, Medu/Kago).
_HR_ROLE_CODES = ['HRIS', 'HR_MANAGER']

# GROUP C-suite — the ONLY non-HR accounts that may see every confidential
# review, across ADIC and all subsidiaries (CFO directive 2026-07-18):
#   * Arun Iyer         — CEO   (aiyer@)
#   * Arjun Iyer        — COO   (arjuniyer@)   NB: his omni title is 'operations'
#   * Prathap G.        — CFO   (pganesharajah@; also holds superuser)
# Deliberately NOT the read-only 'Executive' title: in prod that title is held
# by pbeka@ (a non-C-suite exec) and by aiyer@, and it MISSES Arjun entirely —
# so title-matching both over- and under-shot. We pin to the exact accounts
# (exact local-part, not startswith — 'aiyerX@' must not match). The 'Executive'
# and generic 'CFO' titles no longer grant sight (the CFO title had also leaked
# to siddharth@cuberoute, an external-domain account). Override for other
# environments via OMNI_DD_CSUITE_LOCAL_PARTS=aiyer,arjuniyer,pganesharajah.
_DEFAULT_CSUITE_LOCAL_PARTS = {'aiyer', 'arjuniyer', 'pganesharajah'}


def _csuite_local_parts() -> set:
    raw = os.environ.get('OMNI_DD_CSUITE_LOCAL_PARTS')
    if raw:
        return {p.strip().lower() for p in raw.split(',') if p.strip()}
    return _DEFAULT_CSUITE_LOCAL_PARTS


def _is_csuite(user) -> bool:
    """Exact-account match against the group C-suite (CEO / COO / CFO)."""
    email = (getattr(user, 'email', '') or '').strip().lower()
    if not email or '@' not in email:
        return False
    return email.split('@', 1)[0] in _csuite_local_parts()


def is_hr_doc_admin(user) -> bool:
    """May see EVERY personal HR document. Strictly the group C-suite + HR team:
      * CEO / COO / CFO — the three named group executives (see above),
      * HR (the HR_MANAGER title, or a LIVE HR/HRIS role assignment).
    A generic administrator / Finance manager / read-only Executive is NOT
    elevated here.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    if _is_csuite(user):
        return True
    prof = getattr(user, 'profile', None)
    if prof and getattr(prof, 'is_active', False):
        from core.models import UserProfile
        if prof.title == UserProfile.Title.HR_MANAGER:
            return True
    try:
        # Route through the canonical permission entry point: it counts only
        # LIVE assignments (not revoked, role still active, not past expires_at).
        # A raw UserRoleAssignment.filter().exists() would let a revoked ex-HR
        # grant or an expired break-glass HR_MANAGER grant keep full sight of
        # every review — DPA-relevant. (Fable review 2026-07-18.)
        from core.models import _active_assignments
        if any(a.role.code in _HR_ROLE_CODES for a in _active_assignments(user)):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _user_employee(user):
    return getattr(user, 'employee_record', None)


def can_access_hr_document(user, doc) -> bool:
    """True if `user` may download `doc`."""
    if is_hr_doc_admin(user):
        return True
    if doc.category not in SELF_VISIBLE_CATEGORIES:
        return False
    if doc.employee_id is None:
        return False
    me = _user_employee(user)
    if me is None:
        return False
    subject = doc.employee.employee            # payroll.Employee the doc is about
    if subject and subject.pk == me.pk:         # self
        return True
    mgr = doc.employee.manager                  # subject's direct line manager
    if mgr and mgr.pk == me.pk:
        return True
    return False


def visible_hr_documents(user):
    """The HR documents `user` may list (self + direct reports; all for admins)."""
    from hris.models import HRDocument
    if is_hr_doc_admin(user):
        return HRDocument.objects.all()
    me = _user_employee(user)
    if me is None:
        return HRDocument.objects.none()
    return (HRDocument.objects
            .filter(category__in=SELF_VISIBLE_CATEGORIES)
            .filter(Q(employee__employee=me) | Q(employee__manager=me)))
