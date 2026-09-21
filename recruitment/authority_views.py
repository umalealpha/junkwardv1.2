"""Authority to Recruit — API (CFO 2026-08-03).

  GET  recruitment/authorities/                  list (five signatories only)
  POST recruitment/authorities/                  create one
  GET  recruitment/authorities/<id>/             one, with cost + who still owes a signature
  POST recruitment/authorities/<id>/sign/        {decision: approve|decline, notes}
  GET  recruitment/authorities/<id>/document/    the .docx

Every route is behind authority_access.can_view — a staff login that is not one
of the five gets 403, not a filtered empty list, because "there is nothing here"
and "you may not see what is here" must not look the same to the caller.
"""
from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status as http
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import authority_access as access
from . import authority_doc
from . import authority_notify
from .models import AuthorityToRecruit, PositionTier

DENIED = {'detail': 'Authorities to Recruit are restricted to their signatories.'}
log = logging.getLogger('recruitment.authority')


def record_decision(a, user, decision: str, notes: str):
    """Apply one signatory's decision, recompute status, and chase/announce.

    Single source of truth shared by the in-app API (authority_sign) and the
    login-free email route (authority_actions) so the two can never drift — the
    exact bug the leave email route once had. Assumes can_sign has already
    passed and `decision` is 'approve' or 'decline'.
    """
    slug = a.signatory_for(user)
    approvals = dict(a.approvals or {})
    approvals[slug] = {
        'decision': 'approved' if decision == 'approve' else 'declined',
        'by': user.get_full_name() or user.username,
        'at': timezone.now().isoformat(),
        'notes': (notes or '')[:2000],
    }
    a.approvals = approvals
    a.recompute_status()
    a.save(update_fields=['approvals', 'status', 'updated_at'])

    who = approvals[slug]['by']
    if a.status == AuthorityToRecruit.Status.DECLINED:
        authority_notify.notify_declined(a, by=who, notes=notes)
    elif a.status == AuthorityToRecruit.Status.PENDING:
        authority_notify.notify_after_signature(a, by=who)
    # APPROVED (all five in) sends nothing here: the offer step owns that.
    return a


def _serialize(a, *, full=False, viewer=None):
    cost_m, cost_a = a.cost_to_company()
    var_m, var_a = a.variance_to_quote()
    ceiling = a.tier_ceiling()
    my_slug = a.signatory_for(viewer) if viewer is not None else ''
    out = {
        'id': str(a.id),
        'reference': a.reference,
        'kind': a.kind,
        'kind_label': a.get_kind_display(),
        'person_name': a.person_name,
        'entity': a.entity,
        'position': a.position,
        'department': a.department,
        'level': a.level,
        'employment_type': a.employment_type,
        'headcount': a.headcount,
        'effective_date': a.effective_date.isoformat() if a.effective_date else None,
        'currency': a.currency,
        'quoted_ctc_monthly': str(a.quoted_ctc_monthly),
        'quoted_ctc_annual': str(a.quoted_ctc_annual),
        'cost_to_company_monthly': str(cost_m),
        'cost_to_company_annual': str(cost_a),
        'variance_monthly': str(var_m),
        'variance_annual': str(var_a),
        'status': a.status,
        'status_label': a.get_status_display(),
        'outstanding': a.outstanding_signatories(),
        'signatories': [{'slug': s, 'label': l} for s, l, _e in a.signatory_chain()],
        'approvals': a.approvals or {},
        # Position tier + basic-salary band (Unami Hiring-SOP, CFO 2026-09-02).
        'tier': a.tier.tier if a.tier_id else None,
        'tier_id': a.tier_id,
        'tier_name': (a.tier.name if a.tier_id else (a.level or None)),
        'proposed_basic_salary': str(a.proposed_basic_salary),
        'salary_band_min': (str(a.tier.basic_salary_min)
                            if a.tier_id and a.tier.basic_salary_min is not None else None),
        'salary_band_max': (str(ceiling) if ceiling is not None else None),
        'is_salary_exception': a.is_salary_exception(),
        'exception_signers': [{'slug': s, 'label': l} for s, l, _e in a.exception_signers()],
        'hiring_manager_name': a.hiring_manager_name,
        'hiring_manager_email': a.hiring_manager_email,
        'my_slug': my_slug,
        'mine': bool(my_slug) and my_slug in a.outstanding_signatories(),
        'created_at': a.created_at.isoformat(),
        # H3 conversion state (CFO 2026-08-26) — drives the "Create employee" button.
        'application_id': str(a.application_id) if a.application_id else None,
        'converted_employee_id': a.converted_employee_id,
        'converted_employee_name': (a.converted_employee.full_name
                                    if a.converted_employee_id else None),
        'converted_at': a.converted_at.isoformat() if a.converted_at else None,
        'can_convert': (a.status == AuthorityToRecruit.Status.APPROVED
                        and a.kind == AuthorityToRecruit.Kind.RECRUIT
                        and not a.converted_employee_id),
    }
    if full:
        out['justification'] = a.justification
        out['salary_lines'] = a.salary_lines or []
    return out


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def authorities(request):
    if not access.can_view(request.user):
        return Response(DENIED, status=http.HTTP_403_FORBIDDEN)

    if request.method == 'GET':
        qs = access.visible_authorities(request.user)
        st = (request.query_params.get('status') or '').strip()
        if st:
            qs = qs.filter(status=st)
        items = [_serialize(a, viewer=request.user) for a in qs[:300]]
        # my_signature_slug is retained for back-compat; per-authority the caller
        # should prefer each row's own `my_slug`/`mine` (a person's slug can differ
        # by tier — e.g. a hiring manager on one authority, nothing on another).
        my = next((x['my_slug'] for x in items if x['my_slug']), '')
        return Response({'my_signature_slug': my, 'authorities': items})

    # Raising is limited to the fixed role-holders (HR / execs). A hiring manager
    # may VIEW and SIGN the papers they are on (that is why can_view lets them in),
    # but must not raise new ones (Fable 2026-09-02: create shared the view gate).
    if not (getattr(request.user, 'is_superuser', False)
            or (getattr(request.user, 'email', '') or '').strip().lower() in access.role_emails()):
        return Response({'detail': 'Raising an authority is restricted to HR and the executives.'},
                        status=http.HTTP_403_FORBIDDEN)

    d = request.data or {}
    name = (d.get('person_name') or '').strip()
    position = (d.get('position') or '').strip()
    if not name or not position:
        return Response({'detail': 'A name and a position are required.'},
                        status=http.HTTP_400_BAD_REQUEST)
    kind = (d.get('kind') or 'recruit').strip()
    if kind not in {k.value for k in AuthorityToRecruit.Kind}:
        kind = AuthorityToRecruit.Kind.RECRUIT

    # Position tier (Unami Hiring-SOP). Optional for back-compat; when given it
    # drives the signatory chain and the basic-salary band check. If not given,
    # try to suggest it from the position via the job-title→tier map.
    tier = None
    tier_in = d.get('tier')
    if tier_in not in (None, '', 0, '0'):
        tier = PositionTier.objects.filter(tier=tier_in).first() \
            or PositionTier.objects.filter(pk=tier_in).first()
        if tier is None:
            return Response({'detail': f'Unknown position tier {tier_in!r}.'},
                            status=http.HTTP_400_BAD_REQUEST)
    else:
        from .authority_tiers_views import tier_for_title
        tier = tier_for_title(position)

    a = AuthorityToRecruit(
        kind=kind,
        person_name=name[:120],
        position=position[:120],
        entity=(d.get('entity') or 'Alpha Direct Insurance Company')[:120],
        department=(d.get('department') or '')[:120],
        level=(d.get('level') or '')[:24],
        employment_type=(d.get('employment_type') or 'permanent'),
        headcount=int(d.get('headcount') or 1),
        justification=(d.get('justification') or ''),
        salary_lines=d.get('salary_lines') or [],
        currency=(d.get('currency') or 'BWP')[:3],
        quoted_ctc_monthly=d.get('quoted_ctc_monthly') or 0,
        quoted_ctc_annual=d.get('quoted_ctc_annual') or 0,
        tier=tier,
        proposed_basic_salary=d.get('proposed_basic_salary') or 0,
        hiring_manager_name=(d.get('hiring_manager_name') or '')[:120],
        hiring_manager_email=(d.get('hiring_manager_email') or '').strip(),
        created_by=request.user,
    )
    eff = (d.get('effective_date') or '').strip()
    if eff:
        from datetime import date
        try:
            a.effective_date = date.fromisoformat(eff)
        except ValueError:
            return Response({'detail': 'effective_date must be YYYY-MM-DD.'},
                            status=http.HTTP_400_BAD_REQUEST)

    # A tier whose chain includes the Hiring Manager needs one captured, or the
    # signature can never be routed.
    if tier and 'hiring_manager' in tier.standard_slugs() and not a.hiring_manager_email:
        return Response({'detail': 'This tier requires a hiring manager — give a name and email.'},
                        status=http.HTTP_400_BAD_REQUEST)

    # A tiered paper must state the basic salary — it is the figure the band is
    # checked against; without it the band gate is silently skipped.
    if tier and a._num(a.proposed_basic_salary) <= 0:
        return Response({'detail': 'Enter the basic salary (monthly) for a tiered authority.'},
                        status=http.HTTP_400_BAD_REQUEST)

    # Same-person-two-slots deadlock (Fable 2026-09-02): if the hiring manager is
    # also a fixed signatory on this chain, only the first slot resolves and the
    # paper is stuck PENDING forever. Refuse it up front.
    if tier and a.hiring_manager_email:
        hm = a.hiring_manager_email.strip().lower()
        if any(addr and addr == hm and slug != 'hiring_manager'
               for slug, _l, addr in a.signatory_chain()):
            return Response({'detail': 'The hiring manager is already another signatory on this '
                                       'tier — choose a different person, or the paper can never '
                                       'be fully signed.'}, status=http.HTTP_400_BAD_REQUEST)

    # Tier-5 (C-suite) is signed by the Board Chair via a login-free email link,
    # but that link binds to an active omni account for the chair's address. If
    # the Board Chair is not set up (settings.RECRUIT_BOARD_CHAIR + an active
    # account), the slot is unsignable and the paper would dead-end. Block it.
    if tier and 'board_chair' in tier.standard_slugs():
        from django.conf import settings
        from django.contrib.auth.models import User
        cfg = getattr(settings, 'RECRUIT_BOARD_CHAIR', {}) or {}
        chair_email = (cfg.get('email') or '').strip().lower()
        chair_ok = bool(chair_email) and User.objects.filter(
            email__iexact=chair_email, is_active=True).exists()
        if not chair_ok:
            return Response({'detail': 'C-suite (Tier 5) sign-off needs the Board Chair set up first '
                                       '(name, email and an omni account). Ask the CFO before raising '
                                       'a Tier 5 authority.'}, status=http.HTTP_400_BAD_REQUEST)

    # Over-ceiling basic salary is blocked without a written justification
    # (below the floor is not flagged). The exception approvers then sign it in
    # the normal chain.
    try:
        a.validate_offer()
    except ValidationError as e:
        msg = '; '.join(e.messages) if hasattr(e, 'messages') else str(e)
        return Response({'detail': msg, 'salary_exception': True},
                        status=http.HTTP_400_BAD_REQUEST)

    a.save()
    # Tell the signatories. Before this (CFO 2026-08-11) an authority was raised
    # and simply sat there — both prod records were 0-of-5 signed because nobody
    # was ever told. A mail failure must never fail the create: the record is the
    # thing that matters and notify_* swallows and logs.
    if a.status == AuthorityToRecruit.Status.PENDING:
        authority_notify.notify_raised(a)
    return Response(_serialize(a, full=True), status=http.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def authority_detail(request, auth_id):
    a = AuthorityToRecruit.objects.filter(pk=auth_id).first()
    if a is None:
        return Response({'detail': 'Not found.'}, status=http.HTTP_404_NOT_FOUND)
    if not access.can_view_authority(request.user, a):
        return Response(DENIED, status=http.HTTP_403_FORBIDDEN)
    body = _serialize(a, full=True, viewer=request.user)
    may, why = access.can_sign(request.user, a)
    body['can_sign'] = may
    body['cannot_sign_reason'] = why
    return Response(body)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def authority_sign(request, auth_id):
    a = AuthorityToRecruit.objects.filter(pk=auth_id).first()
    if a is None:
        return Response({'detail': 'Not found.'}, status=http.HTTP_404_NOT_FOUND)
    if not access.can_view_authority(request.user, a):
        return Response(DENIED, status=http.HTTP_403_FORBIDDEN)
    may, why = access.can_sign(request.user, a)
    if not may:
        return Response({'detail': why}, status=http.HTTP_403_FORBIDDEN)

    decision = (request.data.get('decision') or '').strip().lower()
    if decision not in ('approve', 'decline'):
        return Response({'detail': 'decision must be approve or decline.'},
                        status=http.HTTP_400_BAD_REQUEST)
    notes = (request.data.get('notes') or '').strip()
    if decision == 'decline' and not notes:
        return Response({'detail': 'Give a reason when declining.'},
                        status=http.HTTP_400_BAD_REQUEST)

    record_decision(a, request.user, decision, notes)
    return Response(_serialize(a))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def authority_document(request, auth_id):
    a = AuthorityToRecruit.objects.filter(pk=auth_id).first()
    if a is None:
        return Response({'detail': 'Not found.'}, status=http.HTTP_404_NOT_FOUND)
    if not access.can_view_authority(request.user, a):
        return Response(DENIED, status=http.HTTP_403_FORBIDDEN)
    data = authority_doc.build(a)
    resp = HttpResponse(
        data,
        content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    )
    resp['Content-Disposition'] = f'attachment; filename="{authority_doc.filename_for(a)}"'
    return resp


# ── H3: one-click convert an approved authority into an employee ──────────────
# CFO 2026-08-26. Once all five have signed, HR clicks once and the person
# becomes a payroll Employee with their onboarding started. IT still creates the
# login separately (existing setup_employee_users / link_staff_logins) — this
# deliberately does NOT mint a login. Idempotent via converted_employee.

def _resolve_company(entity: str):
    """Map the authority's free-text entity to a Company. Returns (company, None)
    or (None, error_message_listing_the_valid_entities)."""
    from core.models import Company
    name = (entity or '').strip()
    qs = Company.objects.filter(is_active=True)
    hit = (qs.filter(name__iexact=name).first()
           or qs.filter(legal_name__iexact=name).first()
           or qs.filter(code__iexact=name).first())
    if hit is not None:
        return hit, None
    valid = ', '.join(f'{c.name} ({c.code})' for c in qs.order_by('name')[:20])
    return None, (f'The entity on this authority ("{name}") does not match a known '
                  f'company, so we will not guess which payroll to put the new '
                  f'employee under. Set the entity to one of: {valid}.')


def _seed_onboarding(employee, a):
    """Start the new hire's onboarding: the 30-60-90 manager check-ins plus the
    two first-day essentials. Best-effort — never block the hire on this."""
    try:
        from datetime import date, timedelta
        from hris.models import OnboardingTask
    except Exception:  # noqa: BLE001
        return 0
    start = a.effective_date or timezone.localdate()
    plan = [
        ('it',        'Create IT accounts + equipment', start),
        ('paperwork', 'Sign contract + collect documents', start),
        ('check30',   '30-day manager check-in', start + timedelta(days=30)),
        ('check60',   '60-day manager check-in', start + timedelta(days=60)),
        ('check90',   '90-day manager check-in', start + timedelta(days=90)),
    ]
    made = 0
    for cat, title, due in plan:
        _, created = OnboardingTask.objects.get_or_create(
            employee=employee, category=cat, title=title,
            defaults={'due_date': due})
        made += 1 if created else 0
    return made


def _atr_employment_type(a):
    """Map the ATR's employment_type (recruitment.JobRequisition.EMPLOYMENT
    choices) to payroll.contract_models.EmploymentContract.ContractType.
    Falls back to the configured default (atr.default_employment_type) for a
    value with no direct match (e.g. 'casual') — never guessed silently, the
    fallback is a named config key."""
    from payroll import config as payroll_config
    from payroll.contract_models import EmploymentContract
    mapping = {
        'permanent':  EmploymentContract.ContractType.PERMANENT,
        'fixed_term': EmploymentContract.ContractType.FIXED_TERM,
        'intern':     EmploymentContract.ContractType.INTERNSHIP,
    }
    et = (getattr(a, 'employment_type', '') or '').strip().lower()
    if et in mapping:
        return mapping[et]
    default = payroll_config.get_setting('atr.default_employment_type', 'permanent')
    return mapping.get(default, EmploymentContract.ContractType.PERMANENT)


def _atr_hire_date(a):
    """Effective/hire date for the seeded Employee (Prompt 03): the
    authority's own effective_date when set; otherwise "On acceptance" reads
    as the conversion date (today) — the only other option this pack
    defines, hardcoded rather than a config key since there is no second
    value anything ever reads it against."""
    if a.effective_date:
        return a.effective_date
    return timezone.localdate()


def _seed_payroll(employee, a):
    """Seed payroll for a newly-converted Authority to Recruit (Prompt 03,
    Shared Contract v1, 2026-09-12) — job title, hire date, the back-link
    reference, an EmploymentContract, and the recurring components so the
    first payslip computes without HR re-keying (picked up automatically by
    payroll.eligibility.is_active_for_period + the roll-forward joiner step,
    Prompt 02). Additive only: never overwrites an employee this authority is
    merely RE-linking to (an existing same-entity match) — those already have
    their own payroll history.
    """
    from payroll import config as payroll_config
    from payroll.contract_models import EmploymentContract

    if not payroll_config.get_bool('recruitment.atr_seed_payroll', True):
        return None  # flag OFF — today's name/dept-only convert, unchanged

    hire_date = _atr_hire_date(a)
    update_fields = []
    if a.position and employee.job_title != a.position:
        employee.job_title = a.position
        update_fields.append('job_title')
    if not employee.hire_date:
        employee.hire_date = hire_date
        update_fields.append('hire_date')
    if payroll_config.get_bool('atr.store_reference_on_employee', True) and a.reference:
        employee.recruit_authority_ref = a.reference
        update_fields.append('recruit_authority_ref')
    if update_fields:
        employee.save(update_fields=update_fields + ['updated_at'])

    # One EmploymentContract per ATR conversion — idempotent on a re-click via
    # the authority's own converted_employee guard (the caller never gets
    # here twice for the same authority).
    basic = a.proposed_basic_salary or 0
    allowance_template = {}
    for ln in (a.salary_lines or []):
        item = (ln.get('item') or '').strip()
        if not item or item.lower().startswith('total') or item.lower() == 'basic salary':
            continue
        try:
            from decimal import Decimal as _D
            allowance_template[item.upper().replace(' ', '_')] = str(_D(str(ln.get('monthly') or 0)))
        except Exception:  # noqa: BLE001 — a malformed row is skipped, never guessed
            # Visible, not invisible (coordinator review, 2026-09-12): a
            # dropped pay line used to vanish silently. Name the employee and
            # the item so Finance can see what didn't make it onto the
            # contract.
            log.warning('ATR %s seed: %s — salary line %r could not be read '
                       '(monthly=%r), dropped from allowance_template',
                       a.reference, employee.full_name, item, ln.get('monthly'))
            continue

    currency = None
    try:
        from core.models import Currency
        currency = Currency.objects.filter(code=(a.currency or 'BWP').upper()).first()
    except Exception:  # noqa: BLE001
        currency = None

    contract_kwargs = dict(
        employee=employee, start_date=hire_date, basic=basic,
        status=EmploymentContract.Status.ACTIVE,
        contract_type=_atr_employment_type(a),
        allowance_template=allowance_template,
    )
    if currency is not None:
        contract_kwargs['currency_code'] = currency
    contract = EmploymentContract.objects.create(**contract_kwargs)
    return contract


def _apply_regrade(a, request_user):
    """Apply an approved Authority to REGRADE: move the existing employee onto
    the new BASIC **from the date on the signed authority** and raise a
    PayrollAmendment for the period that date falls in — never creates a new
    employee (Prompt 03, item 5).

    TIMING — CFO decision 2026-09-12 (this replaced the earlier
    pay-it-immediately behaviour, which was flagged to him as unconfirmed):
    the authority carries the START DATE OF EMPLOYMENT on the new grade
    (`effective_date`), and the new basic starts THEN, not on the day the
    form is converted.

      * effective date today or already past -> the current contract's basic
        is raised now, and the amendment lands on the open period, exactly as
        before.
      * effective date in the future -> the current contract is left paying
        the OLD basic and is closed the day before; a second contract window
        opens on the effective date carrying the new basic. The amendment is
        raised only against an OPEN period that actually CONTAINS the
        effective date. If no such period exists yet, no amendment is raised
        — raising it against the current open period is precisely how
        someone gets the rise a month early, which is what this change
        exists to stop. The contract window carries the figure until that
        period is opened."""
    import datetime
    from decimal import Decimal

    from payroll import config as payroll_config
    from payroll.contract_models import EmploymentContract
    from payroll.models import Employee, PayrollAmendment, PayrollAmendmentBatch, \
        PayrollPeriod, PayslipComponent

    company, err = _resolve_company(a.entity)
    if err:
        return None, err

    # Resolve the existing employee. `a.employee` (a Django User, set for a
    # regrade per the model docstring) links to payroll.Employee via its
    # one-to-one `user` field; fall back to a same-entity name match, same
    # dedup helper the recruit path uses, so a regrade never invents a row.
    employee = None
    if a.employee_id:
        employee = Employee.objects.filter(user_id=a.employee_id, company=company).first()
    if employee is None:
        from payroll.addition_service import possible_matches
        matches = [e for e in possible_matches(a.person_name, '', company)
                  if e.company_id == company.id]
        # Exactly one match required (coordinator review, 2026-09-12): a
        # regrade raises someone's basic pay — picking matches[0] among two+
        # namesakes silently raises the WRONG person's pay, and that class of
        # error is not one anyone catches quickly. Refuse rather than guess.
        if len(matches) == 1:
            employee = matches[0]
        elif len(matches) > 1:
            names = ', '.join(f'{e.full_name} ({e.employee_number})' for e in matches[:5])
            return None, (f'{a.person_name} matches more than one employee under '
                          f'{company.name}: {names}. A regrade must name exactly one '
                          f'person — resolve the ambiguity before converting.')
    if employee is None:
        return None, (f'{a.person_name} was not found as an existing employee under '
                      f'{company.name} — a regrade cannot create a new employee.')

    hire_date = _atr_hire_date(a)
    new_basic = a.proposed_basic_salary or Decimal('0')

    # Move the employee onto the new basic FROM the authority's own date
    # (CFO 2026-09-12) — see this function's docstring.
    today = timezone.localdate()
    future_dated = hire_date > today
    contract = employee.current_contract
    if contract is None:
        contract = EmploymentContract.objects.create(
            employee=employee, start_date=hire_date, basic=new_basic,
            status=EmploymentContract.Status.ACTIVE,
            contract_type=_atr_employment_type(a),
        )
    elif not future_dated:
        contract.basic = new_basic
        contract.save(update_fields=['basic', 'updated_at'])
    else:
        # Close the old window the day before the new grade starts, then open
        # the new one. `current_contract` filters start_date <= today, so the
        # employee keeps being paid the OLD basic until the effective date.
        day_before = hire_date - datetime.timedelta(days=1)
        if contract.end_date is None or contract.end_date > day_before:
            contract.end_date = day_before
            contract.save(update_fields=['end_date', 'updated_at'])
        contract = EmploymentContract.objects.create(
            employee=employee, start_date=hire_date, basic=new_basic,
            currency_code=contract.currency_code,
            frequency=contract.frequency,
            grade=contract.grade,
            status=EmploymentContract.Status.ACTIVE,
            contract_type=contract.contract_type,
            allowance_template=contract.allowance_template,
        )

    if payroll_config.get_bool('atr.store_reference_on_employee', True) and a.reference:
        employee.recruit_authority_ref = a.reference
        employee.save(update_fields=['recruit_authority_ref', 'updated_at'])

    # Raise a PayrollAmendment for the new BASIC — the canonical feed pattern
    # (Shared Contract v1): one PARSED batch per (target_period, company),
    # reused across the month; the monthly close reviews and applies it, dual
    # sign-off actually pays it. This never writes to a payslip directly.
    # The amendment belongs to the OPEN period that CONTAINS the effective
    # date - one rule for both branches, effective-today included.
    #
    # It was `status=OPEN` ordered by '-start_date' for the immediate case,
    # which picks the LATEST open period. ensure_payroll_periods opens through
    # today + 2 months, so an effective-today regrade raised contract.basic
    # immediately and then put the amendment on the period two months out: the
    # rise lands late and the contract and the payslip disagree meanwhile.
    # (Fable, 2026-09-12. The original test opened exactly one period, so it
    # could not tell the two orderings apart - the case has to discriminate.)
    target = (PayrollPeriod.objects
             .filter(status=PayrollPeriod.Status.OPEN,
                     start_date__lte=hire_date, end_date__gte=hire_date)
             .order_by('start_date').first())

    # No open period covers that date. For a future-dated regrade this is a
    # REFUSAL, not a silent skip: nothing downstream reads the new contract
    # window - the roll-forward copies the baseline period's lines verbatim and
    # contract_for_period is only consulted for a brand-new joiner - so a
    # dropped amendment is a rise that never arrives and nobody is told.
    if target is None and future_dated:
        return None, (
            f'No open payroll period covers {hire_date:%d %b %Y}, the start date on '
            f'this authority. Open that period first, then convert - otherwise the '
            f'new salary would be recorded on the contract and never reach a payslip.')
    amendment = None
    if target is not None:
        baseline = (PayrollPeriod.objects.filter(start_date__lt=target.start_date)
                   .order_by('-start_date').first())
        batch = (PayrollAmendmentBatch.objects
                .filter(target_period=target, company=company, file_name='AUTO-REGRADE',
                        status=PayrollAmendmentBatch.Status.PARSED)
                .first())
        if batch is None:
            batch = PayrollAmendmentBatch.objects.create(
                target_period=target, baseline_period=baseline or target, company=company,
                file_name='AUTO-REGRADE', status=PayrollAmendmentBatch.Status.PARSED,
                notes='Auto-generated from an approved Authority to Regrade. Pending — '
                      'reviewed and applied at the monthly payroll close.',
            )
        basic_comp = PayslipComponent.objects.filter(code__iexact='basic').first()
        amendment, _created = PayrollAmendment.objects.update_or_create(
            batch=batch, employee=employee, kind=PayrollAmendment.Kind.SALARY_CHANGE,
            component=basic_comp,
            defaults={'amount': new_basic, 'employee_ref': employee.employee_number,
                     'reason': f'Approved regrade {a.reference} (auto)',
                     'approver': 'Auto (approved regrade)', 'resolution_error': ''},
        )
        batch.row_count = batch.amendments.count()
        batch.save(update_fields=['row_count', 'updated_at'])
    return employee, None


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def authority_convert(request, auth_id):
    """Turn an APPROVED Authority to Recruit into a payroll Employee + start
    onboarding. Restricted to the five signatories (same gate as viewing it)."""
    import re
    import uuid

    if not access.can_view(request.user):
        return Response(DENIED, status=http.HTTP_403_FORBIDDEN)
    a = AuthorityToRecruit.objects.filter(pk=auth_id).first()
    if a is None:
        return Response({'detail': 'Not found.'}, status=http.HTTP_404_NOT_FOUND)

    if a.status != AuthorityToRecruit.Status.APPROVED:
        return Response({'detail': 'All five signatories must approve before this can '
                         'be converted to an employee.'}, status=http.HTTP_400_BAD_REQUEST)

    # Fast idempotent short-circuit for the common re-click (re-checked under a
    # row lock below to close the concurrent double-click race).
    if a.converted_employee_id:
        return Response({'already': True, 'employee_id': a.converted_employee_id,
                         'employee_name': a.converted_employee.full_name,
                         'authority': _serialize(a)})

    # Regrade — never creates a new employee (Prompt 03, item 5). Updates the
    # existing employee's contract + BASIC and raises a PayrollAmendment from
    # the effective date. Kept as its own branch: the RECRUIT path below (new
    # hire, onboarding, dedup-by-name) does not apply to an existing employee.
    if a.kind == AuthorityToRecruit.Kind.REGRADE:
        from django.db import transaction
        with transaction.atomic():
            a = AuthorityToRecruit.objects.select_for_update().get(pk=a.pk)
            if a.converted_employee_id:
                return Response({'already': True, 'employee_id': a.converted_employee_id,
                                 'employee_name': a.converted_employee.full_name,
                                 'authority': _serialize(a)})
            employee, err = _apply_regrade(a, request.user)
            if err:
                return Response({'detail': err}, status=http.HTTP_400_BAD_REQUEST)
            a.converted_employee = employee
            a.converted_at = timezone.now()
            a.save(update_fields=['converted_employee', 'converted_at', 'updated_at'])
        return Response({
            'employee_id': employee.id, 'employee_name': employee.full_name,
            'employee_number': employee.employee_number, 'regrade': True,
            'authority': _serialize(a),
        }, status=http.HTTP_200_OK)

    company, err = _resolve_company(a.entity)
    if err:
        return Response({'detail': err}, status=http.HTTP_400_BAD_REQUEST)

    from django.db import transaction
    from payroll.models import Employee
    from payroll.addition_service import possible_matches

    with transaction.atomic():
        # Lock the authority row so two concurrent clicks cannot both convert: the
        # second waits, then sees converted_employee set and returns it. The
        # uuid-suffix employee_number means the unique constraint would NOT catch a
        # double create, so the lock is the real guard (July-2026 dup-row class).
        a = AuthorityToRecruit.objects.select_for_update().get(pk=a.pk)
        if a.converted_employee_id:
            return Response({'already': True, 'employee_id': a.converted_employee_id,
                             'employee_name': a.converted_employee.full_name,
                             'authority': _serialize(a)})

        # Reuse an existing (non-terminated) match IN THE SAME ENTITY, never mint a
        # duplicate shell (payroll.addition_service dedup). A namesake — or a real
        # inter-entity transfer — who is active under ANOTHER company is NOT this
        # hire: refuse and name them, never silently attach the new appointment to
        # the wrong entity (H90). Convert has no human-checker step that the
        # payroll-addition flow uses to catch this.
        matches = possible_matches(a.person_name, '', company)
        same_entity = [e for e in matches if e.company_id == company.id]
        cross_entity = [e for e in matches if e.company_id != company.id]
        if not same_entity and cross_entity:
            other = cross_entity[0]
            return Response({'detail':
                f'{a.person_name} is already an active employee under '
                f'{other.company.name} ({other.employee_number}). If this is a '
                f'transfer, handle it as a transfer, not a new appointment.'},
                status=http.HTTP_400_BAD_REQUEST)
        employee = same_entity[0] if same_entity else None
        linked = employee is not None
        if employee is None:
            base_no = (f"{company.code}-"
                       f"{re.sub(r'[^A-Za-z0-9]+', '', a.person_name)[:12].upper()}")
            empno = base_no or f"EMP-{uuid.uuid4().hex[:6].upper()}"
            while Employee.objects.filter(employee_number=empno).exists():
                empno = f"{base_no}-{uuid.uuid4().hex[:4].upper()}"
            employee = Employee.objects.create(
                full_name=a.person_name, department=a.department,
                company=company, employee_number=empno,
                status=Employee.Status.ACTIVE,
            )

        # Talent profile shell (mirrors payroll.importer / hris.feature_views).
        from hris.models import HRISProfile
        HRISProfile.objects.get_or_create(employee=employee)

        onboarding_made = _seed_onboarding(employee, a)

        # Seed payroll (Prompt 03) — ONLY for a genuinely new employee. A
        # re-used same-entity match (`linked`) already has their own pay
        # history; re-seeding a contract over it would be the wrong action,
        # not a connection.
        contract_created = None
        if not linked:
            contract_created = _seed_payroll(employee, a)

        a.converted_employee = employee
        a.converted_at = timezone.now()
        a.save(update_fields=['converted_employee', 'converted_at', 'updated_at'])

        # If this authority came from an application, mark that candidate hired.
        if a.application_id and a.application.stage != 'hired':
            app = a.application
            app.stage = 'hired'
            app.human_reviewed = True
            app.save(update_fields=['stage', 'human_reviewed', 'updated_at'])

    return Response({
        'employee_id': employee.id,
        'employee_name': employee.full_name,
        'employee_number': employee.employee_number,
        'company': company.name,
        'linked_existing': linked,
        'onboarding_tasks_created': onboarding_made,
        'employment_contract_id': str(contract_created.pk) if contract_created else None,
        'login_created': False,   # IT provisions the login separately
        'authority': _serialize(a),
    }, status=http.HTTP_201_CREATED)
