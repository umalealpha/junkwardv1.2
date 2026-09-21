"""
hris/transfer_service.py — business logic for inter-entity employee transfers.

Flow: submit (PENDING_OUT) → approve_out (PENDING_IN) → approve_in (COMPLETED,
applies the move). Either side may reject. Segregation of duties: the submitter
cannot provide either approval; the out-approver cannot also be the in-approver.
Approver authority reuses the HRIS whitelist (Unami / CFO / superuser), matching
the amendment workflow.

Two MODES (CFO 2026-09-05, Naomi Pheko — redundancy at ADIC, re-hired at Unicoin):

  carry   — the original behaviour. The SAME employee record simply changes
            entity. Leave balance, login, Time Doctor link and payslip history
            all travel with the person.
  rehire  — a redundancy followed by a fresh start elsewhere in the group:
              1. the SOURCE record is terminated the day before the effective
                 date (the asset-return gate applies — nothing is finalised
                 while the leaver still holds a laptop);
              2. the annual-leave balance is either PAID OUT (a leaver
                 settlement is raised for CFO → HR → Finance to approve) or
                 CARRIED to the new record — HR chooses on the form;
              3. a NEW employee record is created at the destination with the
                 person's details, a new employee number, their login, their
                 Time Doctor link and an annual-leave opening balance dated the
                 start date (zero if paid out, the carried balance otherwise).
            The old record stays for payslip history until HR archives it.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Company
from payroll.models import Employee

from .amendment_service import CFO_EMAIL, UNAMI_EMAIL, _local
from .transfer_models import EmployeeTransfer

log = logging.getLogger(__name__)


# ─── helpers ──────────────────────────────────────────────────────────────────


#: Who may approve an inter-entity transfer, besides superusers.
#:
#: CFO 2026-09-18, in his words: "We should give Dorothy also access to approve,
#: kago or pako, or legakwa also so its done." Two approvers was too few — every
#: transfer needs TWO DIFFERENT signatures on top of the submitter, so with only
#: Unami and the CFO one busy week stalls the queue, and a transfer they had both
#: already touched could not be approved by anyone at all. Three of the four
#: transfers on the system were stuck, two of them since June.
#:
#: Segregation of duties is UNCHANGED and still does the real work: whoever
#: submitted a transfer cannot approve it, and whoever approved the Out step
#: cannot approve the In step. Dorothy raises most transfers herself, so on her
#: own she still cannot wave one through.
TRANSFER_APPROVER_EMAILS = (
    UNAMI_EMAIL,                          # Unami Butale
    CFO_EMAIL,                            # Prathap Ganesharajah
    "dikgopoleng@alphadirect.co.bw",      # Dorothy K. Ikgopoleng — Senior Associate, Human Capital
    "pkago@alphadirect.co.bw",            # Pako Lisley Kago — Financial Controller
    "lntabeni@alphadirect.co.bw",         # Legakwa Tsala Ntabeni — Senior Accountant
)


def _can_approve(user) -> bool:
    if getattr(user, "is_superuser", False):
        return True
    return _local(getattr(user, "email", "")) in {
        _local(e) for e in TRANSFER_APPROVER_EMAILS
    }


def _assets_still_held(emp: Employee) -> str:
    """Plain-English list of assets the leaver still holds, or '' when clear.
    Same gate as payroll.archive_service / EmployeeViewSet._guard_terminate
    (Asset Control & Handover AC6, CFO 2026-09-02)."""
    try:
        from assets.control_services import assets_blocking_offboarding

        held = list(assets_blocking_offboarding(emp))
    except ImportError:  # assets app absent in a stripped test DB — nothing to hold
        return ""
    if not held:
        return ""
    tags = ", ".join(a.tag_number for a in held[:10])
    more = "" if len(held) <= 10 else f" (+{len(held) - 10} more)"
    return (
        f"{emp.full_name} still holds {len(held)} asset(s): {tags}{more}. "
        "Return or write off every asset before the exit can be finalised."
    )


_NUM_RE = re.compile(r"^(?P<prefix>[A-Za-z]+)[_-](?P<num>\d+)$")


def next_employee_number(dest: Company) -> str:
    """Next `<CODE>_<n>` number at the destination entity (e.g. UNI_042), one
    above the highest existing numeric suffix for that prefix. Falls back to a
    unique suffix if the numbering pattern is not in use there."""
    code = (dest.code or "EMP").upper()
    best = 0
    for num in Employee.objects.filter(
        employee_number__istartswith=f"{code}_"
    ).values_list("employee_number", flat=True):
        m = _NUM_RE.match(num or "")
        if m and m.group("prefix").upper() == code:
            best = max(best, int(m.group("num")))
    candidate = f"{code}_{best + 1:03d}"
    while Employee.objects.filter(employee_number__iexact=candidate).exists():
        best += 1
        candidate = f"{code}_{best + 1:03d}"
    return candidate


def _annual_available(profile) -> Decimal:
    from .leave_encash_service import annual_available

    return annual_available(profile) if profile is not None else Decimal("0")


def _annual_entitlement(profile) -> Decimal:
    """The person's annual entitlement (days/year) from their latest opening-
    balance row, else the Conditions-of-Service default."""
    from hris.leave_onboarding import DEFAULT_ANNUAL_DAYS
    from hris.models import LeaveOpeningBalance

    if profile is None:
        return DEFAULT_ANNUAL_DAYS
    row = (
        LeaveOpeningBalance.objects.filter(profile=profile, leave_type_code="annual")
        .order_by("-as_at_date", "-created_at")
        .first()
    )
    if row is not None and row.entitlement_days and row.entitlement_days > 0:
        return Decimal(row.entitlement_days)
    return DEFAULT_ANNUAL_DAYS


# ─── applying a move ──────────────────────────────────────────────────────────


def _apply_carry(transfer: EmployeeTransfer, actor) -> None:
    emp = transfer.employee
    emp.company = transfer.dest_company
    emp.save(
        audit_user=actor,
        audit_description=(
            f"Inter-entity transfer {transfer.pk}: "
            f"{transfer.source_company.code} → {transfer.dest_company.code} "
            f"(effective {transfer.effective_date})"
        ),
    )


def _apply_rehire(transfer: EmployeeTransfer, actor) -> None:
    """Terminate at source, settle or carry the leave, create the new record.
    Everything below runs inside the caller's transaction — a failure anywhere
    leaves the old record untouched."""
    from django.contrib.auth.models import User
    from hris.models import HRISProfile, LeaveOpeningBalance
    from .leave_encash_service import raise_leaver_settlement

    old = transfer.employee
    dest = transfer.dest_company
    last_day = transfer.effective_date - _dt.timedelta(days=1)

    blocked = _assets_still_held(old)
    if blocked:
        raise ValidationError(blocked)
    if old.status == Employee.Status.TERMINATED and old.termination_date:
        raise ValidationError(
            f"{old.full_name} is already terminated ({old.termination_date})."
        )

    old_profile = getattr(old, "hris_profile", None)
    entitlement = _annual_entitlement(old_profile)

    # 1. Terminate the source record on the last working day. Accrual stops
    #    there (leave_balance.accrual_cutoff), so the balance read next is the
    #    balance TO the last day — no stub-month credit.
    old.status = Employee.Status.TERMINATED
    old.termination_date = last_day
    login = old.user
    old.user = None  # the login moves to the new record (OneToOne)
    old.save(
        audit_user=actor,
        audit_description=(
            f"Redundancy & re-hire transfer {transfer.pk}: terminated at "
            f"{transfer.source_company.code} on {last_day}; "
            f"re-hired at {dest.code} from {transfer.effective_date}"
        ),
    )

    # 2. Leave: pay it out (a leaver settlement for CFO → HR → Finance) or
    #    carry the balance onto the new record.
    carried = Decimal("0")
    if transfer.leave_treatment == EmployeeTransfer.LeaveTreatment.PAYOUT:
        if old_profile is not None and _annual_available(old_profile) > 0:
            settlement = raise_leaver_settlement(
                initiator=transfer.submitter,
                employee=old,
                last_day=last_day,
                reason=(
                    f"Redundancy at {transfer.source_company.code}; re-hired at "
                    f"{dest.code} from {transfer.effective_date}. "
                    f"{transfer.reason}"
                ).strip(),
                actor=actor,
            )
            transfer.settlement = settlement
    else:
        carried = _annual_available(old_profile)

    # 3. The new record at the destination.
    new_email = (transfer.new_email or old.email or "").strip()
    new = Employee(
        employee_number=next_employee_number(dest),
        full_name=old.full_name,
        department=old.department,
        job_title=old.job_title,
        email=new_email,
        phone=old.phone,
        national_id=old.national_id,
        qualifications=old.qualifications,
        hire_date=transfer.effective_date,
        company=dest,
        status=Employee.Status.ACTIVE,
        bank_name=old.bank_name,
        bank_account_no=old.bank_account_no,
        bank_branch=old.bank_branch,
        bank_branch_code=old.bank_branch_code,
        external_ref=f"rehire:{transfer.pk}",
        user=login,
    )
    new.save(
        audit_user=actor,
        audit_description=(
            f"Created by redundancy & re-hire transfer {transfer.pk} "
            f"from {old.employee_number} ({transfer.source_company.code})"
        ),
    )

    # The login follows the person. If the new mailbox already has a never-used
    # duplicate login, retire that duplicate so the staff sign-in (which refuses
    # ambiguous emails) keeps working for the real account.
    if (
        login is not None
        and new_email
        and (login.email or "").lower() != new_email.lower()
    ):
        for dup in User.objects.filter(email__iexact=new_email).exclude(pk=login.pk):
            if dup.last_login is None and not hasattr(dup, "employee_record"):
                dup.is_active = False
                dup.save(update_fields=["is_active"])
        login.email = new_email
        login.save(update_fields=["email"])

    # HR profile: copy the person's profile onto the new record (manager,
    # co-reviewer, grade, personal details) — the reporting line survives.
    if old_profile is not None:
        skip = {"id", "employee", "created_at", "updated_at"}
        values = {
            f.name: getattr(old_profile, f.name)
            for f in HRISProfile._meta.concrete_fields
            if f.name not in skip and not f.primary_key
        }
        new_profile = HRISProfile(employee=new, **values)
        new_profile.save(
            audit_user=actor,
            audit_description=f"Profile carried by re-hire transfer {transfer.pk}",
        )
    else:
        new_profile = HRISProfile.objects.create(employee=new)

    # Annual leave starts at the destination on the start date: zero after a
    # payout, or the carried balance. Entitlement stays what it was.
    LeaveOpeningBalance.objects.create(
        profile=new_profile,
        leave_type_code="annual",
        as_at_date=transfer.effective_date,
        entitlement_days=entitlement,
        opening_balance_days=carried.quantize(Decimal("0.01")),
        accrued_days=Decimal("0"),
        batch=f"rehire:{transfer.pk}",
        uploaded_by=actor if getattr(actor, "pk", None) else None,
    )

    # Time Doctor link + tracking directive follow the person, still confirmed.
    try:
        from integrations.models import TimeDoctorUserMap

        TimeDoctorUserMap.objects.filter(employee=old).update(employee=new)
    except Exception:  # noqa: BLE001
        log.exception("rehire %s: could not move Time Doctor map", transfer.pk)
    try:
        from hris.models import TrackingDirective

        TrackingDirective.objects.filter(employee=old).update(employee=new)
    except Exception:  # noqa: BLE001
        log.exception("rehire %s: could not move tracking directive", transfer.pk)

    # Fable review 2026-09-05: everyone who reported to the OLD record must now
    # report to the NEW one, or the team is orphaned on a terminated record.
    HRISProfile.objects.filter(manager=old).update(manager=new)
    HRISProfile.objects.filter(co_manager=old).update(co_manager=new)

    transfer.new_employee = new


def _apply_move(transfer: EmployeeTransfer, actor) -> None:
    """Apply the move (audited) and mark COMPLETED. Idempotent: a transfer
    already applied is left untouched. Atomic: a rehire that fails halfway
    leaves the old record exactly as it was."""
    if transfer.applied_at:
        return
    with transaction.atomic():
        locked = EmployeeTransfer.objects.select_for_update().get(pk=transfer.pk)
        if locked.applied_at:
            transfer.applied_at = locked.applied_at
            transfer.status = locked.status
            return
        if transfer.mode == EmployeeTransfer.Mode.REHIRE:
            _apply_rehire(transfer, actor)
        else:
            _apply_carry(transfer, actor)
        transfer.status = EmployeeTransfer.Status.COMPLETED
        transfer.applied_at = timezone.now()
        transfer.save(
            update_fields=[
                "status",
                "applied_at",
                "new_employee",
                "settlement",
                "updated_at",
            ]
        )


# ─── submit / approve / reject ────────────────────────────────────────────────


def submit_transfer(
    *,
    submitter,
    employee_id,
    dest_company_id,
    effective_date,
    reason="",
    mode=EmployeeTransfer.Mode.CARRY,
    leave_treatment=EmployeeTransfer.LeaveTreatment.PAYOUT,
    new_email="",
) -> EmployeeTransfer:
    emp = Employee.objects.filter(pk=employee_id).select_related("company").first()
    if emp is None:
        raise ValidationError("Employee not found.")
    if not emp.company_id:
        raise ValidationError("Employee has no current entity to transfer from.")
    dest = Company.objects.filter(pk=dest_company_id).first()
    if dest is None:
        raise ValidationError("Destination entity not found.")
    if dest.id == emp.company_id:
        raise ValidationError("Destination entity is the same as the current entity.")
    if mode not in EmployeeTransfer.Mode.values:
        raise ValidationError(
            "Choose how to move them: carry over, or redundancy & re-hire."
        )
    if leave_treatment not in EmployeeTransfer.LeaveTreatment.values:
        raise ValidationError("Say what happens to the leave: pay it out or carry it.")
    if EmployeeTransfer.objects.filter(
        employee=emp,
        status__in=[
            EmployeeTransfer.Status.PENDING_OUT,
            EmployeeTransfer.Status.PENDING_IN,
            EmployeeTransfer.Status.SCHEDULED,
        ],
    ).exists():
        raise ValidationError("A transfer is already in progress for this employee.")
    if mode == EmployeeTransfer.Mode.REHIRE:
        if isinstance(effective_date, str):
            try:
                effective_date = _dt.date.fromisoformat(effective_date)
            except ValueError as exc:
                raise ValidationError("Effective date must be YYYY-MM-DD.") from exc
        blocked = _assets_still_held(emp)
        if blocked:
            raise ValidationError(blocked)
    transfer = EmployeeTransfer.objects.create(
        employee=emp,
        source_company=emp.company,
        dest_company=dest,
        effective_date=effective_date,
        reason=reason or "",
        mode=mode,
        leave_treatment=leave_treatment,
        new_email=(new_email or "").strip(),
        submitter=submitter,
        submitter_email=(getattr(submitter, "email", "") or ""),
        status=EmployeeTransfer.Status.PENDING_OUT,
    )
    _notify_out_approver(transfer)
    return transfer


def _notify_out_approver(transfer: EmployeeTransfer) -> None:
    """Bug 96bfcab4: email the Transfer Out approver (Unami; CFO auto-cc'd) the
    moment a transfer is submitted, so it doesn't sit unseen in 'Awaiting source
    (Transfer Out) approval'. Non-blocking — a mail hiccup must not fail the submit."""
    try:
        import html as _html
        from core.notifications import send_html_with_cfo_cc

        emp = transfer.employee
        nm = _html.escape(emp.full_name or "")
        src = _html.escape(
            transfer.source_company.code if transfer.source_company else ""
        )
        dst = _html.escape(transfer.dest_company.code if transfer.dest_company else "")
        reason_li = (
            f"<li><b>Reason:</b> {_html.escape(transfer.reason)}</li>"
            if transfer.reason
            else ""
        )
        mode_li = (
            f"<li><b>Type:</b> {_html.escape(transfer.get_mode_display())}"
            + (
                f" — leave: {_html.escape(transfer.get_leave_treatment_display().lower())}"
                if transfer.mode == EmployeeTransfer.Mode.REHIRE
                else ""
            )
            + "</li>"
        )
        html = (
            "<div style=\"font-family:'Book Antiqua',Georgia,serif;color:#0D1B2A;max-width:620px\">"
            "<div style='background:#0D1B2A;padding:14px 18px;border-radius:8px 8px 0 0'>"
            "<span style='color:#F4A623;font-weight:700;font-size:16px'>Employee transfer — your approval is needed</span></div>"
            "<div style='border:1px solid #e5e7eb;border-top:0;padding:16px 18px;border-radius:0 0 8px 8px'>"
            "<p>A transfer has been submitted and is <b>awaiting your Transfer Out approval</b>:</p>"
            f"<ul><li><b>Employee:</b> {nm}</li>"
            f"<li><b>Move:</b> {src} &rarr; {dst}</li>"
            f"{mode_li}"
            f"<li><b>Effective date:</b> {transfer.effective_date}</li>"
            f"<li><b>Submitted by:</b> {_html.escape(transfer.submitter_email or 'n/a')}</li>"
            f"{reason_li}</ul>"
            "<p>Open <b>HRIS &rarr; Transfers</b> in Omni to approve or reject it.</p>"
            "</div></div>"
        )
        # Everyone who may sign it, minus the person who raised it — they are
        # barred from approving their own. Sending to one name meant a single
        # person's inbox was the whole queue.
        to = [e for e in TRANSFER_APPROVER_EMAILS
              if _local(e) != _local(transfer.submitter_email or "")]
        send_html_with_cfo_cc(
            subject=f"Transfer approval needed — {emp.full_name} ({src} → {dst})",
            html=html,
            to=to or [CFO_EMAIL],
        )
    except Exception:  # noqa: BLE001
        log.exception(
            "transfer %s submitted but approver notification failed", transfer.pk
        )


def _notify_in_approver(transfer: EmployeeTransfer) -> None:
    """The other half of the chain. Submitting a transfer emailed the Transfer
    Out approver (bug 96bfcab4) — approving it told NOBODY, so the transfer
    moved into "Awaiting destination (Transfer In) approval" and sat there
    unseen. One ADIC -> UNI move was approved out on 7-Sep and was still waiting
    on 18-Sep, when Payroll reported it as "the transfer did not work"
    (bug 93e12326). The machinery worked; nobody had been told. The employee is
    named in the bug report, not here — this file already carries her name and
    her redundancy from the 5-Sep work, which is raised with the CFO separately;
    it is not being added to.

    Segregation of duties decides the recipients, not a fixed list: whoever
    submitted it and whoever approved the Out step are both barred from the In
    step, so mailing them would only send people to a button that refuses them.
    If that leaves nobody, say so loudly to the CFO — a transfer no one is
    allowed to approve will never move on its own, and silence is how it hides.
    """
    try:
        import html as _html
        from core.notifications import send_html_with_cfo_cc

        barred = {
            _local(transfer.submitter_email or ""),
            _local(transfer.out_approver_email or ""),
        }
        to = [e for e in TRANSFER_APPROVER_EMAILS if _local(e) not in barred]

        emp = transfer.employee
        nm = _html.escape(emp.full_name or "")
        src = _html.escape(transfer.source_company.code if transfer.source_company else "")
        dst = _html.escape(transfer.dest_company.code if transfer.dest_company else "")

        if not to:
            send_html_with_cfo_cc(
                subject=f"Transfer STUCK — nobody may approve {emp.full_name} ({src} → {dst})",
                html=(
                    "<div style=\"font-family:'Book Antiqua',Georgia,serif;color:#0D1B2A;max-width:620px\">"
                    "<div style='background:#0D1B2A;padding:14px 18px;border-radius:8px 8px 0 0'>"
                    "<span style='color:#F4A623;font-weight:700;font-size:16px'>"
                    "A transfer cannot be approved by anyone</span></div>"
                    "<div style='border:1px solid #e5e7eb;border-top:0;padding:16px 18px;"
                    "border-radius:0 0 8px 8px'>"
                    f"<p><b>{nm}</b> ({src} &rarr; {dst}) has passed the Transfer Out step and is "
                    "waiting for Transfer In approval &mdash; but everyone on the approver list "
                    "either submitted it or approved the Out step, so nobody is allowed to "
                    "sign the second time.</p>"
                    "<p>It will not move until someone else is given the right.</p>"
                    "</div></div>"
                ),
                to=[CFO_EMAIL],
            )
            log.warning("transfer %s reached PENDING_IN with no eligible approver", transfer.pk)
            return

        html = (
            "<div style=\"font-family:'Book Antiqua',Georgia,serif;color:#0D1B2A;max-width:620px\">"
            "<div style='background:#0D1B2A;padding:14px 18px;border-radius:8px 8px 0 0'>"
            "<span style='color:#F4A623;font-weight:700;font-size:16px'>"
            "Employee transfer — the second approval is needed</span></div>"
            "<div style='border:1px solid #e5e7eb;border-top:0;padding:16px 18px;"
            "border-radius:0 0 8px 8px'>"
            "<p>The Transfer Out step is approved. This transfer is now "
            "<b>awaiting your Transfer In approval</b>, and the employee does not move "
            "until it is given:</p>"
            f"<ul><li><b>Employee:</b> {nm}</li>"
            f"<li><b>Move:</b> {src} &rarr; {dst}</li>"
            f"<li><b>Effective date:</b> {transfer.effective_date}</li>"
            f"<li><b>Submitted by:</b> {_html.escape(transfer.submitter_email or 'n/a')}</li>"
            f"<li><b>Transfer Out approved by:</b> "
            f"{_html.escape(transfer.out_approver_email or 'n/a')}</li></ul>"
            "<p>Open <b>HRIS &rarr; Transfers</b> in Omni to approve or reject it.</p>"
            "</div></div>"
        )
        send_html_with_cfo_cc(
            subject=f"Transfer In approval needed — {emp.full_name} ({src} → {dst})",
            html=html,
            to=to,
        )
    except Exception:  # noqa: BLE001
        log.exception(
            "transfer %s approved out but In-approver notification failed", transfer.pk
        )


def approve_out(transfer: EmployeeTransfer, approver) -> EmployeeTransfer:
    if transfer.status != EmployeeTransfer.Status.PENDING_OUT:
        raise ValidationError(
            f"Transfer is {transfer.get_status_display()}, not awaiting Transfer Out."
        )
    if not _can_approve(approver):
        raise ValidationError("You are not authorised to approve transfers.")
    if approver.pk and approver.pk == transfer.submitter_id:
        raise ValidationError(
            "The submitter cannot approve the Transfer Out (segregation of duties)."
        )
    transfer.out_approver = approver
    transfer.out_approver_email = getattr(approver, "email", "") or ""
    transfer.out_approved_at = timezone.now()
    transfer.status = EmployeeTransfer.Status.PENDING_IN
    transfer.save(
        update_fields=[
            "out_approver",
            "out_approver_email",
            "out_approved_at",
            "status",
            "updated_at",
        ]
    )
    _notify_in_approver(transfer)
    return transfer


def approve_in(transfer: EmployeeTransfer, approver) -> EmployeeTransfer:
    if transfer.status != EmployeeTransfer.Status.PENDING_IN:
        raise ValidationError(
            f"Transfer is {transfer.get_status_display()}, not awaiting Transfer In."
        )
    if not _can_approve(approver):
        raise ValidationError("You are not authorised to approve transfers.")
    if approver.pk and approver.pk == transfer.submitter_id:
        raise ValidationError(
            "The submitter cannot approve the Transfer In (segregation of duties)."
        )
    if approver.pk and approver.pk == transfer.out_approver_id:
        raise ValidationError(
            "The Transfer Out approver cannot also approve the Transfer In."
        )
    if transfer.mode == EmployeeTransfer.Mode.REHIRE:
        blocked = _assets_still_held(transfer.employee)
        if blocked:
            raise ValidationError(blocked)
    transfer.in_approver = approver
    transfer.in_approver_email = getattr(approver, "email", "") or ""
    transfer.in_approved_at = timezone.now()
    # Honour the effective date: apply the move now only if the date has
    # arrived; otherwise park as SCHEDULED and let apply_due_transfers move the
    # employee on the day. (Feature c0d110b6 asked for an effective date that
    # says WHEN the transfer takes effect — not "apply at approval".)
    if transfer.effective_date and transfer.effective_date > timezone.localdate():
        transfer.status = EmployeeTransfer.Status.SCHEDULED
        transfer.save(
            update_fields=[
                "in_approver",
                "in_approver_email",
                "in_approved_at",
                "status",
                "updated_at",
            ]
        )
    else:
        transfer.save(
            update_fields=[
                "in_approver",
                "in_approver_email",
                "in_approved_at",
                "updated_at",
            ]
        )
        _apply_move(transfer, approver)
    return transfer


def apply_due_transfers() -> int:
    """Apply SCHEDULED transfers whose effective date has arrived. Returns the
    count applied. Driven by the apply_due_transfers management command (daily)."""
    today = (
        timezone.localdate()
    )  # business date in TIME_ZONE (Africa/Gaborone), not UTC
    due = EmployeeTransfer.objects.filter(
        status=EmployeeTransfer.Status.SCHEDULED,
        applied_at__isnull=True,
        effective_date__lte=today,
    ).select_related("employee", "source_company", "dest_company")
    n = 0
    for t in due:
        try:
            _apply_move(t, None)
            n += 1
        except Exception:  # noqa: BLE001
            log.exception("apply_due_transfers: failed to apply transfer %s", t.pk)
    return n


def reject_transfer(transfer: EmployeeTransfer, approver, notes="") -> EmployeeTransfer:
    if transfer.status in (
        EmployeeTransfer.Status.COMPLETED,
        EmployeeTransfer.Status.REJECTED,
    ):
        raise ValidationError(
            f"Cannot reject a {transfer.get_status_display()} transfer."
        )
    if not _can_approve(approver):
        raise ValidationError("You are not authorised to reject transfers.")
    transfer.status = EmployeeTransfer.Status.REJECTED
    transfer.decision_notes = notes or ""
    transfer.save(update_fields=["status", "decision_notes", "updated_at"])
    return transfer
