"""
hris/amendment_service.py — submit / approve / reject HRIS amendments.

Workflow (CFO directive 2026-06-07):
  1. An HRIS-role user (maker) submits a proposed change → HRISAmendment(PENDING).
     Nothing on the live record changes yet.
  2. omni emails the APPROVER ("<maker> has made amendments, please approve")
     with a DeepSeek-written, plain-English description, and emails the MAKER a
     confirmation of what they submitted.
  3. The approver approves → the change is applied to the live record (audited)
     and the maker is notified. Or rejects → maker is notified, record untouched.

Segregation of duties: the maker can never approve their own amendment. Routing:
Unami (Head of Human Capital) approves the team's amendments; when Unami is the
maker, approval escalates to the CFO.

DeepSeek (external) is given only field LABELS and the change count — never the
actual values (salaries, IDs, dates) — so no employee PII leaves the building.
The concrete old→new values are rendered locally into the email.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from django.apps import apps
from django.core.exceptions import ValidationError
from django.utils import timezone

from .amendment_models import HRISAmendment

log = logging.getLogger(__name__)

# --- approver routing -------------------------------------------------------
UNAMI_EMAIL = 'ubutale@alphadirect.co.bw'
CFO_EMAIL   = 'pganesharajah@alphadirect.co.bw'


def _local(email: str | None) -> str:
    return (email or '').strip().lower().split('@')[0]


def approver_email_for(maker) -> str:
    """Who approves an amendment made by `maker`.

    Unami approves the team; when Unami herself is the maker it escalates to the
    CFO so no one approves their own change.
    """
    if _local(getattr(maker, 'email', '')) == _local(UNAMI_EMAIL):
        return CFO_EMAIL
    return UNAMI_EMAIL


# --- amendable target registry ---------------------------------------------
# Per target: dotted model + the editable field whitelist {field: label}.
def _registry() -> dict[str, dict[str, Any]]:
    return {
        HRISAmendment.Target.EMPLOYEE: {
            'model': 'payroll.Employee',
            'fields': {
                'full_name': 'Full name', 'department': 'Department',
                'job_title': 'Job title', 'email': 'Work email',
                'phone': 'Phone', 'national_id': 'National ID',
                'hire_date': 'Hire date', 'termination_date': 'Termination date',
                'status': 'Status', 'bank_name': 'Bank',
                'bank_account_no': 'Bank account', 'bank_branch': 'Bank branch',
                # Conditions-of-service contract type (Unami 2026-07-01). Read from
                # the Employee.contract_type property; written specially in _apply
                # onto the active EmploymentContract (see _apply_contract_type).
                'contract_type': 'Contract type',
            },
            'label': lambda o: getattr(o, 'full_name', '') or str(o.pk),
        },
        HRISAmendment.Target.PROFILE: {
            'model': 'hris.HRISProfile',
            'fields': {
                'location': 'Location', 'gender': 'Gender',
                'nationality': 'Nationality', 'date_of_birth': 'Date of birth',
                'demographic_marker': 'Demographic marker',
                'talent_segment': 'Talent segment',
                'grade_id': 'Grade', 'manager_id': 'Manager',
                # HR data expansion (Unami 2026-07-01) — all on HRISProfile.
                'marital_status': 'Marital status',
                'passport_number': 'Passport number',
                'permit_number': 'Permit number',
                'disabilities': 'Disabilities',
                'allergies': 'Allergies',
                'emergency_contact_name': 'Emergency contact — name',
                'emergency_contact_phone': 'Emergency contact — phone',
                'emergency_contact_relationship': 'Emergency contact — relationship',
            },
            'label': lambda o: getattr(getattr(o, 'employee', None), 'full_name', '') or str(o.pk),
        },
        HRISAmendment.Target.GRADE: {
            'model': 'hris.Grade',
            'fields': {
                'name': 'Name', 'level': 'Level', 'spread': 'Spread',
                'midpoint': 'Midpoint salary', 'is_active': 'Active',
            },
            'label': lambda o: f"{getattr(o, 'code', '')} {getattr(o, 'name', '')}".strip(),
        },
    }


def _get_target(kind: str):
    spec = _registry().get(kind)
    if not spec:
        raise ValidationError(f"Unknown amendment target kind: {kind!r}.")
    return spec, apps.get_model(spec['model'])


def _stringify(value: Any) -> str:
    if value is None:
        return ''
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value)


# --- self-apply (independent) HRIS owners ----------------------------------
# CFO directive 2026-06-25: Unami (Head of Human Capital) and Dorothy apply
# their HRIS amendments independently — no second-approver gate — but every
# change is still written to the audit trail. Env-overridable.
import os

SELFAPPLY_DEFAULT_LOCAL_PARTS = ('ubutale', 'dikgopoleng')   # Unami, Dorothy


def _selfapply_local_parts() -> set[str]:
    env = (os.environ.get('OMNI_HRIS_SELFAPPLY_LOCAL_PARTS') or '').strip()
    if env:
        return {p.strip().lower() for p in env.split(',') if p.strip()}
    return {x.lower() for x in SELFAPPLY_DEFAULT_LOCAL_PARTS}


def _can_self_apply(user) -> bool:
    """True if `user` may apply their own HRIS amendment without approval
    (superuser, or a named independent HRIS owner). The change is still audited."""
    if getattr(user, 'is_superuser', False):
        return True
    return _local(getattr(user, 'email', '') or '') in _selfapply_local_parts()


# --- authorization scope (BUG 16d631ce, CFO 2026-06-26) ---------------------
# Who may PROPOSE an amendment for whom. Maker-checker approval still applies on
# top of this — this just stops, e.g., a finance user editing a stranger's bank
# account. Rules: your own record = self-service; someone else = HR-admin / CFO /
# the target's manager only; bank fields on another person = HR/Finance only;
# grades = HR/Finance only.
_BANK_FIELDS = {'bank_name', 'bank_account_no', 'bank_branch'}


def _is_hr_admin(user) -> bool:
    if getattr(user, 'is_superuser', False):
        return True
    p = getattr(user, 'profile', None)
    if p and getattr(p, 'is_administrator', False):
        return True
    return _local(getattr(user, 'email', '') or '') in (
        _selfapply_local_parts() | {_local(UNAMI_EMAIL), _local(CFO_EMAIL)})


def _target_employee(target_kind, obj):
    if target_kind == HRISAmendment.Target.EMPLOYEE:
        return obj
    if target_kind == HRISAmendment.Target.PROFILE:
        return getattr(obj, 'employee', None)
    return None


def _is_manager_of(user, target_emp) -> bool:
    if not target_emp:
        return False
    prof = apps.get_model('hris', 'HRISProfile').objects.filter(employee=target_emp).first()
    mgr = getattr(prof, 'manager', None) if prof else None
    return bool(mgr and getattr(mgr, 'user_id', None) and mgr.user_id == getattr(user, 'id', None))


def _enforce_amendment_scope(user, target_kind, obj, proposed):
    if _is_hr_admin(user):
        return
    if target_kind == HRISAmendment.Target.GRADE:
        raise ValidationError("Only HR / Finance can change salary grades.")
    target_emp = _target_employee(target_kind, obj)
    maker_emp = getattr(user, 'employee_record', None)
    is_self = bool(target_emp and maker_emp and target_emp.pk == maker_emp.pk)
    if is_self:
        return
    if _is_manager_of(user, target_emp):
        if _BANK_FIELDS & set(proposed or {}):
            raise ValidationError("Bank account details can only be changed by HR / Finance.")
        return
    raise ValidationError(
        "You can only propose changes to your own record. Amending another employee "
        "is limited to HR or that person's manager.")


# --- submit -----------------------------------------------------------------
def submit_amendment(*, maker, target_kind: str, target_id: str,
                     proposed: dict[str, Any], reason: str = '',
                     reversal_of: HRISAmendment | None = None) -> HRISAmendment:
    """Create a PENDING amendment from a maker's proposed field changes.

    `proposed` is {field_name: new_value}. Only whitelisted fields are accepted.
    The old value is read from the live record for the audit trail. Live data is
    NOT changed here.
    """
    spec, Model = _get_target(target_kind)
    allowed = spec['fields']

    try:
        obj = Model.objects.get(pk=target_id)
    except Model.DoesNotExist as exc:
        raise ValidationError(f"{target_kind} {target_id} not found.") from exc

    # BUG 16d631ce — who may amend whom (self / manager / HR), and bank fields.
    _enforce_amendment_scope(maker, target_kind, obj, proposed)

    bad = [f for f in proposed if f not in allowed]
    if bad:
        raise ValidationError(f"Fields not amendable on {target_kind}: {', '.join(bad)}.")

    changes: dict[str, dict[str, str]] = {}
    for field, new_value in proposed.items():
        old = getattr(obj, field, None)
        old_s, new_s = _stringify(old), _stringify(new_value)
        if old_s == new_s:
            continue                       # no-op, skip
        changes[field] = {'old': old_s, 'new': new_s, 'label': allowed[field]}

    if not changes:
        raise ValidationError("No actual changes detected — nothing to amend.")

    amendment = HRISAmendment.objects.create(
        target_kind  = target_kind,
        target_id    = str(target_id),
        target_label = spec['label'](obj),
        changes      = changes,
        reason       = reason or '',
        maker        = maker,
        maker_email  = getattr(maker, 'email', '') or '',
        approver_email = approver_email_for(maker),
        reversal_of  = reversal_of,
    )

    # CFO directive 2026-06-25: senior HRIS owners (Unami, Dorothy) apply their
    # amendments straight to the live record — no approval step — but it is still
    # audited (AuditableMixin via _apply + an applied-record email to EXCO).
    if _can_self_apply(maker):
        amendment.approver       = maker
        amendment.approver_email = getattr(maker, 'email', '') or amendment.maker_email
        _apply(amendment, maker)
        amendment.status      = HRISAmendment.Status.APPROVED
        amendment.decided_at  = timezone.now()
        amendment.save(update_fields=['status', 'approver', 'approver_email',
                                      'decided_at', 'updated_at'])
        from .amendment_notify import notify_decided
        try:
            notify_decided(amendment, applied=True)
        except Exception:                   # noqa: BLE001
            log.exception("HRIS amendment %s: notify_decided (self-apply) failed", amendment.pk)
        return amendment

    from .amendment_notify import notify_submitted
    try:
        notify_submitted(amendment)
    except Exception:                       # noqa: BLE001 — never block the submit on email
        log.exception("HRIS amendment %s: notify_submitted failed", amendment.pk)

    return amendment


# --- reverse (compensating amendment) ---------------------------------------
def reverse_amendment(amendment: HRISAmendment, maker) -> HRISAmendment:
    """Propose a compensating amendment that puts `amendment` back.

    Manus nine-area retest P2 (2026-08-25): an applied amendment had no supported
    reversal path, so undoing one meant either a hand-typed amendment with no
    link to the original or a direct database edit.

    Deliberately built as a NORMAL amendment rather than an undo button:

      * it is created through `submit_amendment`, so the field whitelist, the
        who-may-amend-whom scope check (BUG 16d631ce) and the maker-checker
        approval all apply unchanged — one person cannot unwind a dual-approved
        change on their own;
      * `submit_amendment` reads `old` from the LIVE record, which is what a
        compensating entry should do: it moves the field from wherever it is now
        back to the pre-amendment value, and refuses as a no-op if it is already
        there;
      * senior HRIS owners who may self-apply (CFO directive 2026-06-25) still
        self-apply here — same authority as making the change in the first place.

    Only an APPROVED (i.e. applied) amendment can be reversed, and only once.
    """
    if amendment.status != HRISAmendment.Status.APPROVED:
        raise ValidationError(
            f"Only an applied amendment can be reversed — this one is "
            f"{amendment.get_status_display().lower()}."
        )
    existing = amendment.reversals.exclude(
        status=HRISAmendment.Status.REJECTED
    ).first()
    if existing is not None:
        raise ValidationError(
            f"This amendment already has a reversal ({existing.get_status_display()})."
        )

    proposed = {field: change.get('old', '')
                for field, change in (amendment.changes or {}).items()}
    if not proposed:
        raise ValidationError("This amendment recorded no field changes to reverse.")

    return submit_amendment(
        maker       = maker,
        target_kind = amendment.target_kind,
        target_id   = amendment.target_id,
        proposed    = proposed,
        reason      = (f'Reversal of amendment {amendment.pk} '
                       f'({amendment.target_label})'),
        reversal_of = amendment,
    )


# --- approve / reject -------------------------------------------------------
def _can_approve(user, amendment: HRISAmendment) -> bool:
    if getattr(user, 'is_superuser', False):
        return user.pk != amendment.maker_id           # SoD even for superuser
    if user.pk == amendment.maker_id:
        return False                                   # never approve own
    return _local(getattr(user, 'email', '')) in {_local(UNAMI_EMAIL), _local(CFO_EMAIL)}


def approve_amendment(amendment: HRISAmendment, approver) -> HRISAmendment:
    if amendment.status != HRISAmendment.Status.PENDING:
        raise ValidationError(f"Amendment is {amendment.status}, not pending.")
    if not _can_approve(approver, amendment):
        raise ValidationError(
            "You are not authorised to approve this amendment (segregation of "
            "duties: the maker cannot approve their own change)."
        )

    _apply(amendment, approver)

    amendment.status        = HRISAmendment.Status.APPROVED
    amendment.approver      = approver
    amendment.approver_email = getattr(approver, 'email', '') or amendment.approver_email
    amendment.decided_at    = timezone.now()
    amendment.save(update_fields=['status', 'approver', 'approver_email',
                                  'decided_at', 'updated_at'])

    from .amendment_notify import notify_decided
    try:
        notify_decided(amendment, applied=True)
    except Exception:                       # noqa: BLE001
        log.exception("HRIS amendment %s: notify_decided failed", amendment.pk)
    return amendment


def reject_amendment(amendment: HRISAmendment, approver, notes: str = '') -> HRISAmendment:
    if amendment.status != HRISAmendment.Status.PENDING:
        raise ValidationError(f"Amendment is {amendment.status}, not pending.")
    if not _can_approve(approver, amendment):
        raise ValidationError("You are not authorised to decide this amendment.")

    amendment.status         = HRISAmendment.Status.REJECTED
    amendment.approver       = approver
    amendment.approver_email = getattr(approver, 'email', '') or amendment.approver_email
    amendment.decided_at     = timezone.now()
    amendment.decision_notes = notes or ''
    amendment.save(update_fields=['status', 'approver', 'approver_email',
                                  'decided_at', 'decision_notes', 'updated_at'])

    from .amendment_notify import notify_decided
    try:
        notify_decided(amendment, applied=False)
    except Exception:                       # noqa: BLE001
        log.exception("HRIS amendment %s: notify_decided failed", amendment.pk)
    return amendment


def _apply_contract_type(employee, value, approver=None) -> None:
    """Set the CoS contract type on the employee's active (else most-recent)
    EmploymentContract, creating a permanent one dated today if none exists.
    Called from _apply for the EMPLOYEE.contract_type virtual field, since the
    value lives on a related record rather than a column (Unami 2026-07-01)."""
    from payroll.contract_models import EmploymentContract
    value = (value or '').strip()
    if not value:
        return                              # nothing to set; never clears a type
    valid = set(EmploymentContract.ContractType.values)
    if value not in valid:
        raise ValidationError(
            f"Contract type must be one of: {', '.join(sorted(valid))}.")
    contract = (employee.current_contract
                or employee.contracts.order_by('-start_date').first())
    if contract is None:
        contract = EmploymentContract(employee=employee, start_date=timezone.localdate())
    contract.contract_type = value
    desc = 'contract_type set via HRIS amendment'
    try:
        contract.save(audit_user=approver, audit_description=desc)
    except TypeError:
        contract.save()


def _apply(amendment: HRISAmendment, approver) -> None:
    """Write the approved changes onto the live record."""
    spec, Model = _get_target(amendment.target_kind)
    obj = Model.objects.get(pk=amendment.target_id)

    for field, change in amendment.changes.items():
        if field not in spec['fields']:
            continue                        # defensive — whitelist drift
        new_raw = change.get('new', '')
        if field == 'contract_type' and amendment.target_kind == HRISAmendment.Target.EMPLOYEE:
            _apply_contract_type(obj, new_raw, approver)   # related record, not a column
            continue
        if field.endswith('_id'):
            setattr(obj, field, new_raw or None)
        else:
            model_field = Model._meta.get_field(field)
            setattr(obj, field, model_field.to_python(new_raw) if new_raw != '' else
                    (new_raw if not model_field.null else None))

    desc = f"HRIS amendment {amendment.pk} applied (approved by {amendment.approver_email})"
    try:
        obj.save(audit_user=approver, audit_description=desc)   # AuditableMixin
    except TypeError:
        obj.save()                          # plain model
