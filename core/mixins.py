"""
core/mixins.py — reusable DRF view mixins.

CompanyScopedViewSetMixin
-------------------------
CFO structural-audit directive 2026-05-19 (the "Odoo Gap"):

  > The frontend sends the selected company id, but the backend ignores
  > it. ADSA can see ADIC assets. Fix: a DRF base mixin that auto-applies
  > .filter(company_id=<id>) to every queryset.

This mixin reads the company id from (in order):
  1. ``?company=`` query parameter
  2. ``X-Company-ID`` request header
  3. the user's ``UserProfile.default_company_id`` (if present)

If a model has a ``company`` FK we filter by id; otherwise we transparently
no-op so this mixin can be the universal base for ViewSets across the
codebase without breaking non-scoped resources (Currency, FiscalPeriod,
etc.).

Reverse-relation filter: some models scope per-company through their
parent (e.g. ``BankAccount.gl_account.owner_company_id``). Subclasses can
override :attr:`company_lookup_field` — defaults to ``'company_id'``.
"""

from __future__ import annotations

from typing import Optional


# Sentinel: caller provided a company filter but it didn't resolve to a real
# Company. get_queryset should return qs.none() rather than 500 on UUID cast.
UNRESOLVED = object()


def resolve_company(val):
    """Resolve a Company by its code (e.g. 'ADIC') OR by its UUID primary key.

    The frontend's apiFetch auto-injects the globally selected company as its
    UUID id (?company=<uuid>), while machine callers and hand-built URLs pass
    the code — accept either so the same endpoint works for both. Returns the
    Company row, or None if nothing matches.

    CFO standing rule 2026-07-28 (the intel-summary incident): any view that
    filters by company must accept BOTH forms, and must go through this one
    helper. A second copy of this logic is how the bug came back — see
    ``audit_company_param.py`` at the repo root, which gates on it.
    """
    if not val:
        return None
    import uuid as _uuid

    from django.db.models import Q

    from core.models import Company
    s = str(val).strip()
    q = Q(code__iexact=s)
    try:
        _uuid.UUID(s)
        q = q | Q(id=s)
    except (ValueError, AttributeError, TypeError):
        pass
    return Company.objects.filter(q).only('id', 'code').first()


def resolve_company_id_param(request) -> Optional[str]:
    """Public helper for non-mixin views that need code↔UUID resolution.

    Returns the resolved Company UUID as a string, or None if either no
    company param was supplied or the value didn't match. Callers that need
    strict isolation should use ``CompanyScopedViewSetMixin`` instead.
    """
    cid = _resolve_company_id(request)
    if cid is None or cid is UNRESOLVED:
        return None
    return cid


def _resolve_company_id(request):
    """
    Look at query string, then header, then user profile. Returns:
      - a UUID string  → apply .filter(company_id=<uuid>)
      - None           → no company filter requested; return qs unchanged
      - UNRESOLVED     → caller supplied a filter but the value didn't match
                         any Company.code or Company.id; return qs.none()

    Recognised query-param keys (in order):
      company, owner_company, company_id, company__code, owner_company__code

    Plus the ``X-Company-ID`` header and ``UserProfile.default_company_id``
    fall-back. CFO directive 2026-05-19 (Manus audit follow-up): values may
    be either a UUID or a company code (e.g. ``ADIC``). Codes are resolved
    to UUIDs before returning so callers never see a UUID-cast 500.
    """
    import uuid

    code_only_keys = {'company__code', 'owner_company__code'}
    raw_value = None
    explicit_code = False
    for q in ('company', 'owner_company', 'company_id',
              'company__code', 'owner_company__code'):
        v = (request.query_params.get(q) or '').strip()
        if v:
            raw_value = v
            explicit_code = q in code_only_keys
            break
    if raw_value is None:
        v = (request.headers.get('X-Company-ID') or '').strip()
        if v:
            raw_value = v
    if raw_value is None:
        user = getattr(request, 'user', None)
        # CFO directive 2026-05-19 — BULK_UPLOADER role: holders of the
        # `read-all` permission see every entity when no scope is asked
        # for, instead of being pinned to their default_company.
        if user and user.is_authenticated:
            try:
                from core.models import user_has_permission
                if user_has_permission(user, 'read-all'):
                    return None
            except Exception:    # noqa: BLE001
                pass
            prof = getattr(user, 'profile', None)
            if prof and getattr(prof, 'default_company_id', None):
                return str(prof.default_company_id)
        return None

    # UUID? Pass through. Otherwise resolve as Company.code.
    if not explicit_code:
        try:
            uuid.UUID(raw_value)
            return raw_value
        except (ValueError, AttributeError):
            pass

    try:
        from core.models import Company
        comp = (Company.objects.filter(code__iexact=raw_value).first()
                or Company.objects.filter(code=raw_value).first())
        if comp:
            return str(comp.id)
    except Exception:    # noqa: BLE001
        return UNRESOLVED
    return UNRESOLVED


class CompanyScopedViewSetMixin:
    """
    Adds automatic ``company_id`` filtering to every queryset.

    Usage:

        class JournalEntryViewSet(CompanyScopedViewSetMixin,
                                   viewsets.ModelViewSet):
            ...

    Override hook:

        company_lookup_field = 'gl_account__owner_company_id'

    If the model has no field matching ``company_lookup_field`` the mixin
    silently skips filtering — non-scoped resources still work.
    """

    #: dotted ORM path from the model to the company id. Override if the
    #: company is reached via a relation.
    company_lookup_field: str = 'company_id'

    def get_queryset(self):   # type: ignore[override]
        from core.models import allowed_company_ids

        qs = super().get_queryset()
        cid = _resolve_company_id(self.request)

        # CFO directive 2026-05-22: gate by user's allowed companies.
        # Unrestricted bucket bypasses (superuser / admin / CFO).
        user = getattr(self.request, 'user', None)
        allowed = allowed_company_ids(user)

        # Resolve the model's filter path validity (early so we can
        # short-circuit non-scoped models without doing extra work).
        model = qs.model
        parts = self.company_lookup_field.split('__')
        meta = model._meta
        path_valid = True
        for part in parts[:-1]:
            try:
                rel = meta.get_field(part)
            except Exception:    # noqa: BLE001
                path_valid = False
                break
            related = getattr(rel, 'related_model', None)
            if related is None:
                path_valid = False
                break
            meta = related._meta
        if path_valid:
            last = parts[-1]
            field_name = last[:-3] if last.endswith('_id') else last
            try:
                meta.get_field(field_name)
            except Exception:    # noqa: BLE001
                path_valid = False

        # Branch A: caller asked for one specific company.
        if cid is UNRESOLVED:
            return qs.none()
        if cid is not None:
            if allowed != {'*'} and str(cid) not in allowed:
                # User lacks access to this company entirely.
                return qs.none()
            if not path_valid:
                return qs
            return qs.filter(**{self.company_lookup_field: cid})

        # Branch B: no explicit company filter — scope to the user's allowed set.
        if allowed == {'*'} or not path_valid:
            return qs
        if not allowed:
            return qs.none()
        return qs.filter(**{f'{self.company_lookup_field}__in': list(allowed)})


# ─────────────────────────────────────────────────────────────────────────────
# Function-view entity scoping (CFO directive 2026-06-16 — Lakshmi / ADRisk).
#
# The HRIS *function* views (hris/feature_views.py, api_views.py, talent_views.py,
# …) predate CompanyScopedViewSetMixin and trusted the client-supplied ?company=
# param: with no param they returned EVERY entity's employees. That let a user
# with view_all / view_team see all entities. These helpers port the mixin's
# rules (UserCompanyAccess via allowed_company_ids) to function views so a user
# restricted to one entity (e.g. ADRisk Global) can never see another, no matter
# what ?company= they send.
# ─────────────────────────────────────────────────────────────────────────────

def scoped_company_ids(request):
    """Effective company-id filter honouring the caller's UserCompanyAccess.

    Returns one of:
      None        → apply NO company filter. Used for the unrestricted bucket
                    (super / admin / CFO) AND for users with no explicit
                    UserCompanyAccess rows — i.e. scoping ONLY bites a user who
                    has been given an explicit per-entity grant. This preserves
                    the pre-2026-06-16 consolidated HRIS view for the whitelisted
                    HR users (Kago / Pako / Unami) who were never row-scoped.
      []          → DENY (return an empty queryset): an explicitly-scoped caller
                    asked for a company outside their grant, or sent an
                    unresolvable code.
      [id, …]     → restrict the queryset to these Company ids.

    NOTE: this is deliberately MORE lenient than CompanyScopedViewSetMixin
    (which denies no-grant users). HRIS function views were historically open
    to every whitelisted user; we only clamp users who carry an explicit grant.
    """
    from core.models import allowed_company_ids
    user = getattr(request, 'user', None)
    allowed = allowed_company_ids(user)
    cid = _resolve_company_id(request)

    # Genuinely unrestricted (superuser / administrator / CFO) — unchanged.
    if allowed == {'*'}:
        if cid is UNRESOLVED:
            return []
        return None if cid is None else [str(cid)]

    # NO GRANT AT ALL. This used to fall through to the branch above and mean
    # "see everything" — so a brand-new account, which nobody had restricted
    # yet, could read all 13 entities. Meanwhile CompanyScopedViewSetMixin
    # treated the same user as DENIED, so the two helpers disagreed about the
    # same person (SEC-02, CFO 2026-08-08).
    #
    # "No restriction recorded" now means "your own entity", not "everything".
    # Two deliberate exceptions:
    #   * the consolidated HRIS view — people holding 'view_all' (HR / exec)
    #     genuinely need every entity at once, and that is the view this
    #     leniency was written for in the first place;
    #   * a caller whose own entity cannot be determined falls back to the
    #     PREVIOUS behaviour rather than being locked out mid-day — flip
    #     OMNI_ENTITY_SCOPE_STRICT off to restore the old default everywhere.
    if not allowed:
        from django.conf import settings
        if not getattr(settings, 'OMNI_ENTITY_SCOPE_STRICT', True):
            if cid is UNRESOLVED:
                return []
            return None if cid is None else [str(cid)]
        if _keeps_consolidated_view(user):
            if cid is UNRESOLVED:
                return []
            return None if cid is None else [str(cid)]
        own = _own_company_id(user)
        if own is None:
            # Cannot tell which entity they belong to. Do NOT silently show
            # everything; show nothing and let it be reported.
            return []
        if cid is UNRESOLVED:
            return []
        if cid is None:
            return [own]
        return [str(cid)] if str(cid) == own else []
    # Explicitly scoped (has ≥1 UserCompanyAccess row) → clamp hard.
    if cid is UNRESOLVED:
        return []
    if cid is None:                            # no param → their whole allowed set
        return sorted(allowed)
    return [str(cid)] if str(cid) in allowed else []   # one company, if granted


def _keeps_consolidated_view(user) -> bool:
    """True for the people the no-grant leniency was written for: HR and the
    exec team, who legitimately read every entity at once. Keyed on the
    existing 'view_all' capability rather than a new hard-coded name list."""
    try:
        from core.hris_access import ROLE_CAPABILITIES, hris_role
        return 'view_all' in ROLE_CAPABILITIES.get(hris_role(user), set())
    except Exception:      # noqa: BLE001 — never fail a read on a lookup error
        return False


def _own_company_id(user) -> str | None:
    """The entity this person actually belongs to, via their payroll record."""
    emp = getattr(user, 'employee_record', None)
    cid = getattr(emp, 'company_id', None)
    if cid:
        return str(cid)
    prof = getattr(user, 'profile', None)
    cid = getattr(prof, 'company_id', None)
    return str(cid) if cid else None


def apply_company_scope(request, qs, company_field='employee__company_id'):
    """Filter `qs` by `company_field` per the caller's entity scope.

    Restricted callers can never widen past their UserCompanyAccess grant;
    unrestricted callers keep today's behaviour. `company_field` is the dotted
    ORM path from the queryset's model to the Company id (default suits an
    HRISProfile queryset).
    """
    ids = scoped_company_ids(request)
    if ids is None:
        return qs
    if not ids:
        return qs.none()
    return qs.filter(**{f'{company_field}__in': ids})
