"""core/approvals_views.py — unified "My Approvals" inbox (CFO 2026-07-13).

One place that shows how many items across omni await THIS user's sign-off, with
a link to act. COUNTS only — the existing pages own the approve/reject actions;
this just stops things sitting unseen in separate queues. Each stream is guarded
so one module's import problem can never blank the whole inbox.
"""
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


def _oldest_days(qs, field="created_at"):
    """How many days the oldest item in qs has been waiting (0 if none/unknown)."""
    row = qs.order_by(field).values_list(field, flat=True).first()
    if not row:
        return 0
    d = row.date() if hasattr(row, "date") else row
    return max(0, (timezone.localdate() - d).days)


def pending_approvals_for(u):
    """The approval streams awaiting THIS user, each with count + oldest_days +
    href. Shared by the /my-approvals inbox, the Task Dashboard panel, and the
    daily chase email (CFO 2026-07-14)."""
    streams = []

    # Leave requests awaiting approval. CFO 2026-07-24: leave has a chosen
    # approver since the 2026-07-15 picker, so show a person ONLY the leave
    # routed to them — they were picked, or they are the employee's manager.
    # Genuine HR keeps the full oversight queue; the CFO / superusers no longer
    # get every employee's leave in their approvals inbox.
    try:
        from django.db.models import Q
        from hris.models import LeaveRequest
        from core.hris_access import user_can_access_hris, hris_role
        from hris.amendment_service import UNAMI_EMAIL, _local
        if user_can_access_hris(u) or u.is_superuser:
            qs = LeaveRequest.objects.filter(status="pending")
            # HR head (Unami) + genuine HR keep the full oversight queue; everyone
            # else — incl. the CFO / superusers — sees only leave routed to them.
            hr_oversight = (hris_role(u) in ("hr", "hris")
                            or _local(getattr(u, "email", "")) == _local(UNAMI_EMAIL))
            if not hr_oversight:
                qs = qs.filter(Q(requested_approver=u)
                               | Q(requested_approver__isnull=True, profile__manager__user=u))
            n = qs.count()
            if n:
                streams.append({"key": "leave", "label": "Leave requests to approve",
                                "count": n, "href": "/hris/leave",
                                "oldest_days": _oldest_days(qs)})
    except Exception:  # noqa: BLE001
        pass

    # Post-leave reversals — the employee worked days they had booked off
    # (Ontlametse Mogomotsi, ref AD/HR/IA/2026/001). Same routing as leave above,
    # so a manager sees their own team and the HR head keeps oversight.
    try:
        from core.hris_access import hris_role, user_can_access_hris
        from hris.amendment_service import UNAMI_EMAIL, _local
        from hris.leave_reversal_models import LeaveReversal
        if user_can_access_hris(u) or u.is_superuser or hris_role(u) == "mgr":
            qs = LeaveReversal.objects.filter(status=LeaveReversal.Status.PENDING)
            hr_oversight = (hris_role(u) in ("hr", "hris")
                            or _local(getattr(u, "email", "")) == _local(UNAMI_EMAIL))
            if not hr_oversight:
                # Call the one shared predicate — do NOT hand-copy the filter.
                # A hand-copied `filter(...manager__user=u)` is exactly how this
                # surface got left behind when the decide gate was fixed: the
                # covering approver was emailed "go to My Approvals", opened it,
                # and saw zero, while the line manager saw a count they were then
                # refused on. This count, the Task Dashboard panel and the daily
                # chase email all read from here (Fable review 2026-08-11).
                from hris.leave_reversal_views import _mine_to_decide
                qs = _mine_to_decide(qs, u)
            # Never show somebody their own claim as theirs to decide.
            qs = qs.exclude(requested_by=u)
            n = qs.count()
            if n:
                streams.append({"key": "leave_reversal",
                                "label": "Leave reversals to approve",
                                "count": n, "href": "/hris/leave",
                                "oldest_days": _oldest_days(qs)})
    except Exception:  # noqa: BLE001
        pass

    # Spend & event requests (events / training / travel / ...) awaiting approval
    try:
        from budgets.models import SpendRequest
        from budgets.spend_views import _can_approve_spend
        if _can_approve_spend(u):
            qs = SpendRequest.objects.filter(status=SpendRequest.Status.SUBMITTED)
            n = qs.count()
            if n:
                streams.append({"key": "spend", "label": "Spend & event requests",
                                "count": n, "href": "/spend-requests",
                                "oldest_days": _oldest_days(qs)})
    except Exception:  # noqa: BLE001
        pass

    # Refunds processed and awaiting CFO approval
    try:
        from hris.expense_api import ExpenseClaim, _is_cfo
        if _is_cfo(u):
            qs = ExpenseClaim.objects.filter(status=ExpenseClaim.Status.PENDING_CFO)
            n = qs.count()
            if n:
                streams.append({"key": "refunds", "label": "Refunds awaiting your approval",
                                "count": n, "href": "/refunds",
                                "oldest_days": _oldest_days(qs, "processed_at")})
    except Exception:  # noqa: BLE001
        pass

    # Refunds SUBMITTED and routed to THIS accountant to process (load the
    # payment in FNB + upload proof). CFO 2026-08-03: a submitted refund used to
    # reach the chosen accountant by EMAIL ONLY — no task, and it never showed on
    # their Approvals count — so it could sit unseen (Bharath's three refunds sat
    # on Pako with nothing surfacing them). Show it on the ASSIGNED accountant's
    # Approvals inbox. Gated to approver=u ONLY — never the superuser/CFO backstop
    # — so it lands on the accountant who was picked and never pads the CFO's
    # inbox (he is out of this stage by design). Counts-only (processing needs a
    # proof upload + GL line), so it is deliberately NOT bulk-approvable.
    try:
        from hris.expense_api import ExpenseClaim
        qs = ExpenseClaim.objects.filter(
            status=ExpenseClaim.Status.SUBMITTED, approver=u)
        n = qs.count()
        if n:
            streams.append({"key": "refunds_process",
                            "label": "Refunds to process (load in FNB)",
                            "count": n, "href": "/refunds",
                            "oldest_days": _oldest_days(qs, "submitted_at")})
    except Exception:  # noqa: BLE001
        pass

    # CUSTOMER refunds sitting in FNB waiting for the CFO's bank authorisation
    # (CFO 2026-07-29: "it should create a dashboard entry"). NOTE this is a
    # different thing from the "refunds" stream above — that one is STAFF
    # EXPENSE claims (hris.ExpenseClaim). Do not merge them; the money, the
    # approver and the action are all different.
    #
    # The action is in FNB online banking, NOT in Omni, so this row exists to
    # make sure a loaded payment is never sitting at the bank unnoticed. It is
    # visibility only — never bulk-approvable (bulk_ok is not set), because
    # there is nothing here for Omni to approve.
    try:
        from customer_refunds.models import CustomerRefund
        from customer_refunds.services import _resolve_cfo_user
        cfo = _resolve_cfo_user()
        if cfo is not None and u.id == cfo.id:
            qs = CustomerRefund.objects.filter(status=CustomerRefund.Status.FNB_LOADED)
            n = qs.count()
            if n:
                streams.append({"key": "customer_refunds_fnb",
                                "label": "Customer refunds loaded in FNB — authorise at the bank",
                                "count": n, "href": "/tasks",
                                "oldest_days": _oldest_days(qs)})
    except Exception:  # noqa: BLE001
        pass

    # Purchase orders awaiting THIS user's own approval leg. Count by PRIMARY
    # ownership of the leg, NOT the superuser/CFO backstop — otherwise the CFO
    # (a superuser who _can_ approve anything) sees the Finance Manager's whole
    # FM-leg queue in his inbox (CFO 2026-07-13: "why is PO in my approval").
    #   - CFO leg  -> the CFO (or superuser backstop).
    #   - FM leg   -> Finance Manager / Financial Controller; claims-dept POs ->
    #                 claims seniors. The CFO is NOT the primary here — the FM
    #                 owns it, and the CFO can still act from the PO page.
    try:
        from procurement.models import PurchaseOrder
        from procurement.services import (
            _can_approve_as_cfo, _fm_leg_sod_ok, _cfo_leg_sod_ok,
        )
        from core.models import UserProfile, get_user_profile
        prof = get_user_profile(u)
        title = prof.title if (prof and prof.is_active) else None
        FM_TITLES = {UserProfile.Title.FINANCE_MANAGER,
                     UserProfile.Title.FINANCIAL_CONTROLLER}
        CLAIMS_TITLES = {UserProfile.Title.CLAIMS_MANAGER,
                         UserProfile.Title.CLAIMS_TEAM_LEADER,
                         UserProfile.Title.SENIOR_CLAIMS_ASSOCIATE}
        pending = PurchaseOrder.objects.filter(status__in=[
            PurchaseOrder.Status.PENDING_FM_APPROVAL,
            PurchaseOrder.Status.PENDING_CFO_APPROVAL])
        n = 0
        for po in pending[:500]:
            # Claims POs belong to the claims seniors ONLY — never the CFO, not
            # even via the superuser/CFO backstop, and even if a legacy row is
            # sitting in PENDING_CFO_APPROVAL. Check department FIRST so a claims
            # PO can never pad the CFO's inbox (CFO 2026-07-19 / 2026-07-22).
            # Segregation of duties (dead-end-button fix 2026-09-06): a two-step
            # operational PO must not sit in the inbox of the person who created
            # or submitted it (nor, at the CFO leg, its FM-approver) — the sign
            # would only 400. Claims + single-step (<10k) carry no SoD bar, so
            # _*_leg_sod_ok returns True for them and the count is unchanged.
            if po.department == PurchaseOrder.Department.CLAIMS:
                if title in CLAIMS_TITLES:
                    n += 1
            elif po.status == PurchaseOrder.Status.PENDING_CFO_APPROVAL:
                if _can_approve_as_cfo(u) and _cfo_leg_sod_ok(po, u):
                    n += 1
            elif title in FM_TITLES and _fm_leg_sod_ok(po, u):
                n += 1
        if n:
            streams.append({"key": "po", "label": "Purchase orders to approve",
                            "count": n, "href": "/purchase-orders",
                            "oldest_days": _oldest_days(pending)})
    except Exception:  # noqa: BLE001
        pass

    # Leave encashment — each approver sees ONLY the stage that is theirs
    # (CFO → HR → FC/FM), so it lands on the right dashboard (CFO 2026-07-21).
    try:
        from hris.leave_encash_models import LeaveEncashment
        from hris import leave_encash_service as _enc
        stage_status = None
        if _enc.is_cfo(u):
            stage_status = LeaveEncashment.Status.PENDING_CFO
        elif _enc.is_hr(u):
            stage_status = LeaveEncashment.Status.PENDING_HR
        elif _enc.is_finance_approver(u):
            stage_status = LeaveEncashment.Status.PENDING_FINANCE
        if stage_status is not None:
            qs = LeaveEncashment.objects.filter(status=stage_status)
            # honour SoD in the count — don't chase someone about their own /
            # an item they already signed
            n = sum(1 for e in qs.select_related('applicant')
                    if _enc.can_approve_now(e, u))
            if n:
                streams.append({"key": "leave_encash",
                                "label": "Leave encashments to approve",
                                "count": n, "href": "/hris/leave-encashment",
                                "oldest_days": _oldest_days(qs)})
        # Finance also gets the "approved, ready to pay" nudge.
        if _enc.can_pay(u):
            payq = LeaveEncashment.objects.filter(
                status=LeaveEncashment.Status.APPROVED, payroll_processed=False)
            n = payq.count()
            if n:
                streams.append({"key": "leave_encash_pay",
                                "label": "Leave encashments to pay",
                                "count": n, "href": "/hris/leave-encashment",
                                "oldest_days": _oldest_days(payq)})
    except Exception:  # noqa: BLE001
        pass

    # Journal entries pending approval this user may action. Authority = CFO /
    # Finance Manager / Financial Controller (can_approve_journal_entries) or a
    # superuser backstop — the SAME gate JournalEntry.approve() enforces. SoD:
    # the creator of an entry can never approve it, so the user's own entries
    # are excluded from the count.
    try:
        from ledger.models import JournalEntry
        from core.models import get_user_profile
        prof = get_user_profile(u)
        can_je = bool(getattr(u, "is_superuser", False)) or bool(
            prof and prof.can_approve_journal_entries)
        if can_je:
            qs = (JournalEntry.objects
                  .filter(status=JournalEntry.Status.PENDING_APPROVAL)
                  .exclude(created_by_id=getattr(u, "id", None)))
            n = qs.count()
            if n:
                streams.append({"key": "journal_entries",
                                "label": "Journal entries to approve",
                                "count": n, "href": "/journal-entries",
                                "oldest_days": _oldest_days(qs, "submitted_at")})
    except Exception:  # noqa: BLE001
        pass

    # Outbound payments awaiting THIS user's signature. Mirrors
    # Payment.approve_payment exactly: only SENT payments in
    # ApprovalStatus.PENDING, the user must be an authorised outbound signer
    # (FM/FC or CFO/CEO via _approver_role), never the maker (creator/submitter
    # — SoD), and not a signature they have already given (PaymentApproval is
    # unique per approver, so a signed payment is now waiting on OTHERS).
    try:
        from payments.models import Payment, PaymentApproval
        uid = getattr(u, "id", None)
        signed_ids = set(PaymentApproval.objects
                         .filter(approver=u).values_list("payment_id", flat=True))
        qs = Payment.objects.filter(
            approval_status=Payment.ApprovalStatus.PENDING,
            payment_type=Payment.PaymentType.SENT)
        n = 0
        for p in qs[:500]:
            if p._approver_role(u) is None:
                continue                       # not an authorised outbound signer
            if uid in (p.created_by_id, p.submitted_for_approval_by_id):
                continue                       # SoD: maker cannot sign own payment
            if p.id in signed_ids:
                continue                       # already signed — awaiting others
            n += 1
        if n:
            streams.append({"key": "payments", "label": "Payments to sign",
                            "count": n, "href": "/payments/approvals",
                            "oldest_days": _oldest_days(qs, "submitted_for_approval_at")})
    except Exception:  # noqa: BLE001
        pass

    # Payment requests (taskboard.PaymentRequest) awaiting THIS user's stage.
    # MY-APPR-01 (CFO 2026-08-15, Manus QC F1): 21 payment authorisations were
    # sitting in /tasks with source='payment_request' but /my-approvals showed
    # none — the two-stage PaymentRequest queue had no stream here, only the
    # per-payment PaymentApproval stream above. This adds the missing count so
    # a CFO or a finance approver sees the same total in both places.
    # Authority mirrors taskboard.payment_views:
    #   - CFO / superuser → PENDING_CFO
    #   - First-approver (Pako / Kago / Legakwa) → PENDING_FINANCE they did NOT raise
    try:
        from taskboard.models import PaymentRequest
        from taskboard.payment_views import _is_cfo, _is_first_approver
        is_cfo = bool(getattr(u, "is_superuser", False) or _is_cfo(u))
        is_first = _is_first_approver(u)
        qs = None
        if is_cfo:
            qs = PaymentRequest.objects.filter(
                status=PaymentRequest.Status.PENDING_CFO)
        elif is_first:
            qs = PaymentRequest.objects.filter(
                status=PaymentRequest.Status.PENDING_FINANCE,
            ).exclude(created_by=u)  # SoD: never sign off your own raise
        if qs is not None:
            n = qs.count()
            if n:
                streams.append({"key": "payment_requests",
                                "label": "Payment requests to authorise",
                                "count": n, "href": "/payment-requests",
                                "oldest_days": _oldest_days(qs)})
    except Exception:  # noqa: BLE001
        pass

    # Staff loan applications pending CFO approval. Authority = CFO / superuser
    # (_is_final_approver); SoD excludes the applicant's own loan (_is_applicant).
    try:
        from staff_loans.models import StaffLoanApplication
        from staff_loans.services import _is_final_approver, _is_applicant
        if _is_final_approver(u):
            qs = StaffLoanApplication.objects.filter(
                status=StaffLoanApplication.Status.PENDING_CFO)
            n = sum(1 for app in qs.select_related("employee")
                    if not _is_applicant(app, u))
            if n:
                streams.append({"key": "staff_loans",
                                "label": "Staff loans to approve",
                                "count": n, "href": "/hris/staff-loans",
                                "oldest_days": _oldest_days(qs, "submitted_at")})
    except Exception:  # noqa: BLE001
        pass

    # Incentive requests awaiting THIS user's signature. The user fills the CFO
    # or HR slot (slot_for). A PENDING request is one the CFO has not yet signed
    # (the CFO's sign-off flips it to APPROVED). SoD: the maker cannot sign; a
    # slot already filled is not re-counted for the signer who filled it.
    try:
        from hris.incentive_models import IncentiveRequest
        from hris.incentive_service import slot_for, is_approver
        if is_approver(u):
            slot = slot_for(u)
            uid = getattr(u, "id", None)
            qs = IncentiveRequest.objects.filter(
                status=IncentiveRequest.Status.PENDING)
            n = 0
            for req in qs:
                if req.maker_id == uid:
                    continue                   # SoD: cannot sign own request
                if slot == "cfo" and req.cfo_approved_at:
                    continue
                if slot == "hr" and req.hr_approved_at:
                    continue
                n += 1
            if n:
                streams.append({"key": "incentives",
                                "label": "Incentive requests to approve",
                                "count": n, "href": "/hris/incentives",
                                "oldest_days": _oldest_days(qs)})
    except Exception:  # noqa: BLE001
        pass

    # Disciplinary cases sitting at THIS user's stage: PENDING_HR for HR (unless
    # they raised it — SoD), PENDING_CFO for the CFO. can_act_now encapsulates
    # both the stage gate and the SoD rule.
    try:
        from hris.disciplinary_models import DisciplinaryCase
        from hris.disciplinary_service import can_act_now, is_cfo, is_hr
        if is_cfo(u) or is_hr(u):
            qs = DisciplinaryCase.objects.filter(
                status__in=DisciplinaryCase.OPEN_STATUSES)
            n = sum(1 for c in qs if can_act_now(c, u))
            if n:
                streams.append({"key": "disciplinary",
                                "label": "Disciplinary cases to review",
                                "count": n, "href": "/hris/disciplinary",
                                "oldest_days": _oldest_days(qs)})
    except Exception:  # noqa: BLE001
        pass

    # Petty-cash vouchers in the two-signature chain awaiting THIS user. Signer
    # pool = the four people the CFO named (Keetile / Pako / Legakwa / Tlamelo).
    # SoD mirrors approve_voucher: never the creator or submitter, and the first
    # signer cannot also give the second signature.
    try:
        from petty_cash.models import PettyCashVoucher
        # CFO 2026-08-03: petty cash is Keetile / Pako / Legakwa / Tlamelo's job,
        # not his. is_routine_approver excludes the superuser break-glass, so it
        # leaves the CFO's dashboard while he can still sign in an emergency.
        from petty_cash.services import is_routine_approver
        if is_routine_approver(u):
            uid = getattr(u, "id", None)
            qs = PettyCashVoucher.objects.filter(status__in=[
                PettyCashVoucher.Status.PENDING_APPROVAL,
                PettyCashVoucher.Status.ONE_SIGNATURE])
            n = 0
            for v in qs[:500]:
                if uid in (v.created_by_id, v.submitted_by_id):
                    continue                   # SoD: maker cannot sign
                if (v.status == PettyCashVoucher.Status.ONE_SIGNATURE
                        and v.first_approved_by_id == uid):
                    continue                   # already gave the first signature
                n += 1
            if n:
                streams.append({"key": "petty_cash",
                                "label": "Petty cash vouchers to approve",
                                "count": n, "href": "/petty-cash",
                                "oldest_days": _oldest_days(qs, "submitted_at")})
    except Exception:  # noqa: BLE001
        pass

    # Petty-cash REIMBURSEMENTS (the float top-up) awaiting THIS user. CFO
    # 2026-08-07: this is the only petty-cash item he wants on his dashboard —
    # it is the step he actually signs, and it releases the bank payment. The
    # per-voucher noise was removed at the same time (core/notifications.py).
    # Two stages: Kago/Pako review, then the CFO posts it.
    try:
        from petty_cash.models import PettyCashReimbursement
        from petty_cash.services import _can_cfo_approve, _can_fm_review
        uid = getattr(u, "id", None)
        stages = []
        if _can_fm_review(u):
            stages.append((PettyCashReimbursement.Status.PENDING_FM, "to review"))
        if _can_cfo_approve(u):
            stages.append((PettyCashReimbursement.Status.PENDING_CFO, "to approve"))
        for status, verb in stages:
            qs = PettyCashReimbursement.objects.filter(status=status)
            # SoD mirrors the service: nobody signs their own work.
            n = sum(1 for r in qs[:500]
                    if uid not in (r.created_by_id, r.submitted_by_id,
                                   r.fm_reviewed_by_id if status ==
                                   PettyCashReimbursement.Status.PENDING_CFO else None))
            if n:
                streams.append({"key": f"petty_cash_reimbursement_{status}",
                                "label": f"Petty cash reimbursements {verb}",
                                "count": n, "href": "/petty-cash/reimbursements",
                                "oldest_days": _oldest_days(qs, "submitted_at")})
    except Exception:  # noqa: BLE001
        pass

    # Payroll awaiting sign-off, per company x month (CFO 2026-07-28: DUAL
    # sign-off). Shows to HR signers (Unami/Dorothy) and Finance signers
    # (Kago/Pako) the company-months where THEIR side is still outstanding.
    # The CFO is out of the routine loop (a superuser/CFO back-stop sees either
    # side). A month closes only when both sides have signed.
    try:
        from payroll.models import PayrollPeriod
        from payroll.signoff_service import (
            companies_awaiting_signoff, signoff_side,
        )
        side = signoff_side(u)
        if side is not None:
            period = PayrollPeriod.objects.order_by('-start_date').first()
            n = 0
            if period is not None:
                for _company, _row, needs_hr, needs_fin in companies_awaiting_signoff(period):
                    if ((side in ('hr', 'both') and needs_hr)
                            or (side in ('finance', 'both') and needs_fin)):
                        n += 1
            if n:
                streams.append({"key": "payroll", "label": "Payrolls to sign off",
                                "count": n, "href": "/payroll/sign-off",
                                "oldest_days": 0})
    except Exception:  # noqa: BLE001
        pass

    # Payroll employee-additions awaiting Finance sign-off (Pako Kago 2026-08-12).
    # Shown to the Finance leg only; SoD (own submissions) handled in the service.
    try:
        from payroll.addition_service import pending_for_approver
        n = pending_for_approver(u).count()
        if n:
            streams.append({"key": "payroll_additions",
                            "label": "Employee additions to sign off",
                            "count": n, "href": "/payroll/dashboard",
                            "oldest_days": 0})
    except Exception:  # noqa: BLE001
        pass

    # Staff file requests awaiting Human Capital / Records (Tshepo Maswabi 2026-08-11).
    try:
        from records.request_service import pending_for_approver as _rec_pending
        n = _rec_pending(u).count()
        if n:
            streams.append({"key": "record_file_requests",
                            "label": "File requests to approve",
                            "count": n, "href": "/records",
                            "oldest_days": 0})
    except Exception:  # noqa: BLE001
        pass

    # Employment / letter requests awaiting manager sign-off (CFO 2026-08-30).
    # An employee requests a letter about themselves at /hris/letters; the row
    # sits PENDING until their line manager (HRISProfile.manager.user) signs it
    # off, which produces the branded PDF. Same routing model as leave: a
    # manager sees their own team's letters; HR sees the full oversight queue.
    # SoD is inherent — a person cannot request a letter about someone else, so
    # a manager can never be the requester of an item they see here.
    try:
        from hris.models import LetterRequest
        from core.hris_access import hris_role
        from hris.amendment_service import UNAMI_EMAIL, _local
        hr_oversight = (hris_role(u) in ("hr", "hris")
                        or _local(getattr(u, "email", "")) == _local(UNAMI_EMAIL)
                        or getattr(u, "is_superuser", False))
        qs = LetterRequest.objects.filter(status=LetterRequest.Status.PENDING)
        if not hr_oversight:
            # Only rows where THIS user is the subject's manager.
            qs = qs.filter(employee__hris_profile__manager__user=u)
        # Never chase someone about their own request (they cannot sign it off
        # anyway — the letter_decide view refuses "subject == signer").
        qs = qs.exclude(requested_by=u)
        n = qs.count()
        if n:
            streams.append({"key": "letters",
                            "label": "Employment letters to sign off",
                            "count": n, "href": "/hris/letters",
                            "oldest_days": _oldest_days(qs)})
    except Exception:  # noqa: BLE001
        pass

    # Authority to Recruit awaiting THIS user's signature (CFO 2026-08-31 — flagged
    # "Important"; was email-only). A PENDING authority is on this user if they are
    # a signatory ON THAT authority's chain AND their slot is still outstanding.
    # The chain is tier-driven since 2026-09-02, so the slot is resolved PER
    # authority (a fixed-five lookup would drop the Finance Manager / hiring
    # managers, and calling the old classmethod here silently killed this whole
    # stream — Fable 5, 2026-09-02).
    try:
        from recruitment.models import AuthorityToRecruit
        pend = AuthorityToRecruit.objects.filter(
            status=AuthorityToRecruit.Status.PENDING).select_related('tier')
        mine = [a for a in pend
                if (slug := a.signatory_for(u)) and slug in a.outstanding_signatories()]
        if mine:
            # oldest of MINE, computed here (pend/mine are lists, not a
            # queryset, so _oldest_days' .order_by would blow up — Fable 5).
            oldest = min(a.created_at for a in mine)
            streams.append({"key": "authority_to_recruit",
                            "label": "Authorities to recruit — sign",
                            "count": len(mine), "href": "/recruitment/authorities",
                            "oldest_days": max(0, (timezone.localdate() - oldest.date()).days)})
    except Exception:  # noqa: BLE001
        pass

    # Commission submissions at THIS user's review stage (CFO 2026-08-31 — was
    # email-only). user_stages() gives the stages this reviewer owns; STATUS_STAGE
    # maps each submission status to its stage, so the ones awaiting them are the
    # submissions whose status maps to a stage this user reviews.
    try:
        from commissions.access import STATUS_STAGE, user_stages
        from commissions.models import CommissionSubmission
        stages = user_stages(u)
        if stages:
            statuses = [st for st, stg in STATUS_STAGE.items() if stg in stages]
            qs = CommissionSubmission.objects.filter(status__in=statuses)
            n = qs.count()
            if n:
                streams.append({"key": "commissions",
                                "label": "Commissions to review",
                                "count": n, "href": "/commissions",
                                "oldest_days": _oldest_days(qs, "submitted_at")})
    except Exception:  # noqa: BLE001
        pass

    return streams


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_approvals(request):
    streams = pending_approvals_for(request.user)
    return Response({"streams": streams, "total": sum(s["count"] for s in streams)})


# ---------------------------------------------------------------------------
# Bulk approve — "select all + sign once" (CFO 2026-07-22)
#
# The counts inbox above stops items sitting unseen; this lets the approver
# clear the sign-only streams WITHOUT clicking into each page. It NEVER
# reimplements an approval rule — every item is dispatched to the SAME service
# entrypoint the module's own page calls, so authority, segregation-of-duties,
# quorum, row-locking and audit logging are byte-for-byte identical to signing
# on the page. This endpoint is only a fan-out.
#
# Scope is deliberately the six clean single-signature money/HR streams. Leave,
# spend, refunds, POs and disciplinary are itemised for VISIBILITY but are NOT
# bulk-approvable here: leave/spend already have one-click email links, and the
# rest are multi-field decisions (a reason, an amount, a stage choice) that must
# not be reduced to one tap. Those come back with bulk_ok=False so the UI shows
# them read-only with an "open to act" link.
# ---------------------------------------------------------------------------

def _fmt_amount(value, ccy="BWP"):
    try:
        return f"{ccy} {value:,.2f}"
    except Exception:  # noqa: BLE001
        return ""


_LARGE_BWP = 50_000        # "large amount" risk chip threshold (CFO wow-feature 2)
_AFTER_HOURS = (18, 6)     # submitted at/after 18:00 or before 06:00 local = after-hours


def _age_days(dt):
    """Whole days `dt` has been waiting (0 if none/unknown)."""
    if not dt:
        return 0
    d = dt.date() if hasattr(dt, "date") else dt
    return max(0, (timezone.localdate() - d).days)


def _base_flags(amount, age_days, created_dt=None):
    """Risk chips every stream gets: aged, large, after-hours (wow-feature 2)."""
    flags = []
    if age_days >= 2:
        flags.append({"level": "warn", "text": f"waiting {age_days}d"})
    try:
        if amount is not None and float(amount) >= _LARGE_BWP:
            flags.append({"level": "info", "text": "large amount"})
    except (TypeError, ValueError):
        pass
    if created_dt is not None:
        try:
            h = timezone.localtime(created_dt).hour
            if h >= _AFTER_HOURS[0] or h < _AFTER_HOURS[1]:
                flags.append({"level": "info", "text": "after-hours"})
        except Exception:  # noqa: BLE001
            pass
    return flags


def _num(amount):
    """Amount as a float for the running cash total, or None (wow-feature 1)."""
    try:
        return float(amount) if amount is not None else None
    except (TypeError, ValueError):
        return None


def _mkitem(pk, title, sub, *, amount=None, ccy="BWP", age_days=0, flags=None):
    return {"id": str(pk), "title": title, "sub": sub,
            "amount": _num(amount), "ccy": ccy or "BWP",
            "age_days": int(age_days or 0), "flags": flags or []}


def pending_approval_items_for(u):
    """Itemised view of the sign-only approval streams awaiting THIS user.

    Returns a list of {key, label, href, bulk_ok, items:[{id, title, sub, amount,
    ccy, age_days, flags}]}. Mirrors the authority/SoD filtering of
    pending_approvals_for exactly (same gates, same querysets) but yields each
    individual item so the inbox can offer per-item checkboxes + one "Approve
    selected", a running cash total (amount), aging chips (age_days) and risk
    chips (flags). id is a STRING so uuid and int primary keys serialise alike.
    """
    out = []

    # Journal entries — approve() gate: CFO/FM/FC (or superuser), maker≠checker.
    try:
        from ledger.models import JournalEntry
        from core.models import get_user_profile
        prof = get_user_profile(u)
        can_je = bool(getattr(u, "is_superuser", False)) or bool(
            prof and prof.can_approve_journal_entries)
        if can_je:
            qs = (JournalEntry.objects
                  .filter(status=JournalEntry.Status.PENDING_APPROVAL)
                  .exclude(created_by_id=getattr(u, "id", None))
                  .order_by("submitted_at")[:200])
            items = []
            for je in qs:
                amt = getattr(je, "total_debit", None) or getattr(je, "total", None)
                age = _age_days(getattr(je, "submitted_at", None))
                items.append(_mkitem(
                    je.pk, je.entry_number, (je.description or "")[:80],
                    amount=amt, age_days=age,
                    flags=_base_flags(amt, age, getattr(je, "submitted_at", None))))
            if items:
                out.append({"key": "journal_entries",
                            "label": "Journal entries to approve",
                            "href": "/journal-entries", "bulk_ok": True,
                            "items": items})
    except Exception:  # noqa: BLE001
        pass

    # Outbound payments — approve_payment() adds THIS user's signature (quorum
    # 1 FM/FC + 1 CFO/CEO). Idempotent: a payment already signed by u is
    # filtered out (PaymentApproval unique per approver). SoD inside the service.
    try:
        from payments.models import Payment, PaymentApproval
        uid = getattr(u, "id", None)
        signed_ids = set(PaymentApproval.objects
                         .filter(approver=u).values_list("payment_id", flat=True))
        qs = list(Payment.objects.filter(
            approval_status=Payment.ApprovalStatus.PENDING,
            payment_type=Payment.PaymentType.SENT).order_by(
            "submitted_for_approval_at")[:200])
        # Cheap duplicate detection: same payee+amount appearing >1x in the queue.
        from collections import Counter
        dup_key = Counter((getattr(p, "contact_id", None), p.payee_name, str(p.amount))
                          for p in qs)
        items = []
        for p in qs:
            if p._approver_role(u) is None:
                continue
            if uid in (p.created_by_id, p.submitted_for_approval_by_id):
                continue
            if p.id in signed_ids:
                continue
            payee = (p.payee_name if getattr(p, "is_once_off", False)
                     else getattr(p.contact, "name", "")) or "—"
            age = _age_days(getattr(p, "submitted_for_approval_at", None))
            flags = _base_flags(p.amount, age, getattr(p, "submitted_for_approval_at", None))
            if dup_key[(getattr(p, "contact_id", None), p.payee_name, str(p.amount))] > 1:
                flags.append({"level": "warn", "text": "possible duplicate"})
            # First-ever payment to this payee → worth a second look.
            if p.contact_id and not Payment.objects.filter(
                    contact_id=p.contact_id, payment_type=Payment.PaymentType.SENT
                    ).exclude(pk=p.pk).exclude(
                    approval_status=Payment.ApprovalStatus.PENDING).exists():
                flags.append({"level": "info", "text": "first payment to payee"})
            items.append(_mkitem(
                p.pk, p.payment_number, f"{_fmt_amount(p.amount, p.currency_code_id)} → {payee}",
                amount=p.amount, ccy=p.currency_code_id or "BWP", age_days=age, flags=flags))
        if items:
            out.append({"key": "payments", "label": "Payments to sign",
                        "href": "/payments/approvals", "bulk_ok": True,
                        "items": items})
    except Exception:  # noqa: BLE001
        pass

    # Incentive requests — approve_request() fills the user's CFO/HR slot; SoD +
    # two-humans rule inside. Slot-already-filled items are excluded here.
    try:
        from hris.incentive_models import IncentiveRequest
        from hris.incentive_service import slot_for, is_approver
        if is_approver(u):
            slot = slot_for(u)
            uid = getattr(u, "id", None)
            qs = IncentiveRequest.objects.filter(
                status=IncentiveRequest.Status.PENDING).order_by("created_at")
            items = []
            for req in qs:
                if req.maker_id == uid:
                    continue
                if slot == "cfo" and req.cfo_approved_at:
                    continue
                if slot == "hr" and req.hr_approved_at:
                    continue
                age = _age_days(getattr(req, "created_at", None))
                items.append(_mkitem(
                    req.pk, (req.title or str(req))[:80], _fmt_amount(req.total),
                    amount=req.total, age_days=age,
                    flags=_base_flags(req.total, age, getattr(req, "created_at", None))))
            if items:
                out.append({"key": "incentives",
                            "label": "Incentive requests to approve",
                            "href": "/hris/incentives", "bulk_ok": True,
                            "items": items})
    except Exception:  # noqa: BLE001
        pass

    # Staff loans — cfo_decide(approve=True) at the requested figures; CFO/
    # superuser only, SoD inside. Bulk approve records the request as submitted.
    try:
        from staff_loans.models import StaffLoanApplication
        from staff_loans.services import _is_final_approver, _is_applicant
        if _is_final_approver(u):
            qs = (StaffLoanApplication.objects
                  .filter(status=StaffLoanApplication.Status.PENDING_CFO)
                  .select_related("employee").order_by("submitted_at"))
            items = []
            for app in qs:
                if _is_applicant(app, u):
                    continue
                who = getattr(getattr(app, "employee", None), "full_name", "") or "—"
                amt = getattr(app, "amount_requested", None)
                age = _age_days(getattr(app, "submitted_at", None))
                items.append(_mkitem(
                    app.pk, who, _fmt_amount(amt), amount=amt, age_days=age,
                    flags=_base_flags(amt, age, getattr(app, "submitted_at", None))))
            if items:
                out.append({"key": "staff_loans",
                            "label": "Staff loans to approve",
                            "href": "/hris/staff-loans", "bulk_ok": True,
                            "items": items})
    except Exception:  # noqa: BLE001
        pass

    # Leave encashments at THIS user's stage — approve() advances one stage;
    # can_approve_now() encodes stage authority + SoD.
    try:
        from hris.leave_encash_models import LeaveEncashment
        from hris import leave_encash_service as _enc
        stage_status = None
        if _enc.is_cfo(u):
            stage_status = LeaveEncashment.Status.PENDING_CFO
        elif _enc.is_hr(u):
            stage_status = LeaveEncashment.Status.PENDING_HR
        elif _enc.is_finance_approver(u):
            stage_status = LeaveEncashment.Status.PENDING_FINANCE
        if stage_status is not None:
            qs = (LeaveEncashment.objects.filter(status=stage_status)
                  .select_related("applicant").order_by("created_at"))
            items = []
            for e in qs:
                if not _enc.can_approve_now(e, u):
                    continue
                who = (getattr(getattr(e, "applicant", None), "get_full_name", lambda: "")()
                       or getattr(e, "applicant", None) and e.applicant.username) or "—"
                amt = getattr(e, "amount", None)
                age = _age_days(getattr(e, "created_at", None))
                items.append(_mkitem(
                    e.pk, str(who), f"{getattr(e, 'days', '')} days · {_fmt_amount(amt)}",
                    amount=amt, age_days=age,
                    flags=_base_flags(amt, age, getattr(e, "created_at", None))))
            if items:
                out.append({"key": "leave_encash",
                            "label": "Leave encashments to approve",
                            "href": "/hris/leave-encashment", "bulk_ok": True,
                            "items": items})
    except Exception:  # noqa: BLE001
        pass

    # Petty-cash vouchers awaiting THIS user's signature — approve_voucher();
    # signer pool + SoD (never maker, never the same first-signer) inside.
    try:
        from petty_cash.models import PettyCashVoucher
        from petty_cash.services import is_routine_approver   # CFO out (2026-08-03)
        if is_routine_approver(u):
            uid = getattr(u, "id", None)
            qs = (PettyCashVoucher.objects.filter(status__in=[
                PettyCashVoucher.Status.PENDING_APPROVAL,
                PettyCashVoucher.Status.ONE_SIGNATURE]).order_by("submitted_at"))
            items = []
            for v in qs[:200]:
                if uid in (v.created_by_id, v.submitted_by_id):
                    continue
                if (v.status == PettyCashVoucher.Status.ONE_SIGNATURE
                        and v.first_approved_by_id == uid):
                    continue
                amt = getattr(v, "amount", None)
                age = _age_days(getattr(v, "submitted_at", None))
                items.append(_mkitem(
                    v.pk, v.voucher_number, _fmt_amount(amt), amount=amt, age_days=age,
                    flags=_base_flags(amt, age, getattr(v, "submitted_at", None))))
            if items:
                out.append({"key": "petty_cash",
                            "label": "Petty cash vouchers to approve",
                            "href": "/petty-cash", "bulk_ok": True,
                            "items": items})
    except Exception:  # noqa: BLE001
        pass

    # Purchase orders awaiting THIS user's own leg — the Finance Manager /
    # Financial Controller (operational POs), the CFO (operational CFO leg), or
    # the claims seniors (CLAIMS POs — the CFO is OUT of those entirely). Same
    # ownership test as the counts inbox; _can_approve_po / _can_approve_as_cfo
    # are the SAME gates fm_approve()/cfo_approve() enforce, so a mis-listed row
    # is refused at sign time anyway. The adapter picks the leg by status.
    try:
        from procurement.models import PurchaseOrder
        from procurement.services import (
            _can_approve_po, _can_approve_as_cfo,
            _fm_leg_sod_ok, _cfo_leg_sod_ok,
        )
        pending = (PurchaseOrder.objects.filter(status__in=[
            PurchaseOrder.Status.PENDING_FM_APPROVAL,
            PurchaseOrder.Status.PENDING_CFO_APPROVAL])
            .select_related("supplier").order_by("created_at"))
        items = []
        for po in pending[:200]:
            # Same guard fm_approve()/cfo_approve() enforce, INCLUDING segregation
            # of duties (dead-end-button fix 2026-09-06): the creator/submitter of
            # a two-step operational PO (and its FM-approver at the CFO leg) must
            # not be listed a row whose Approve would only 400. Claims + single-
            # step (<10k) have no SoD bar (_*_leg_sod_ok returns True), unchanged.
            if po.status == PurchaseOrder.Status.PENDING_CFO_APPROVAL:
                allowed = _can_approve_as_cfo(u) and _cfo_leg_sod_ok(po, u)
            else:
                allowed = _can_approve_po(u, po) and _fm_leg_sod_ok(po, u)
            if not allowed:
                continue
            who = getattr(getattr(po, "supplier", None), "name", "") or "—"
            amt = getattr(po, "total_bwp", None) or getattr(po, "total_amount", None)
            ccy = getattr(po, "currency_code_id", "BWP") or "BWP"
            age = _age_days(getattr(po, "created_at", None))
            items.append(_mkitem(
                po.pk, po.po_number or str(po),
                f"{_fmt_amount(getattr(po, 'total_amount', None), ccy)} · {who}",
                amount=amt, ccy=ccy, age_days=age,
                flags=_base_flags(amt, age, getattr(po, "created_at", None))))
        if items:
            out.append({"key": "po", "label": "Purchase orders to approve",
                        "href": "/purchase-orders", "bulk_ok": True,
                        "items": items})
    except Exception:  # noqa: BLE001
        pass

    # Authorities to recruit awaiting THIS user's signature (CFO 2026-09-12:
    # "In Omni app i am not seeing authority of recruit ... it should be full
    # approval suit"). The COUNT stream has existed since 2026-08-31, but only
    # as a link out to the desktop page - on a phone that is a dead end. Same
    # per-authority signatory resolution pending_approvals_for uses.
    try:
        from recruitment.models import AuthorityToRecruit
        pend = (AuthorityToRecruit.objects
                .filter(status=AuthorityToRecruit.Status.PENDING)
                .select_related('tier').order_by('created_at')[:200])
        items = []
        for a in pend:
            slug = a.signatory_for(u)
            if not slug or slug not in a.outstanding_signatories():
                continue
            amt = getattr(a, 'proposed_basic_salary', None)
            age = _age_days(getattr(a, 'created_at', None))
            who = (getattr(a, 'person_name', '') or '').strip()
            sub = ' · '.join(bit for bit in (
                (getattr(a, 'position', '') or '').strip(),
                who,
                (getattr(a, 'entity', '') or '').strip(),
            ) if bit)[:110]
            items.append(_mkitem(
                a.pk, getattr(a, 'reference', '') or str(a), sub,
                amount=amt, age_days=age,
                flags=_base_flags(amt, age, getattr(a, 'created_at', None))))
        if items:
            out.append({"key": "authority_to_recruit",
                        "label": "Authorities to recruit — sign",
                        "href": "/recruitment/authorities", "bulk_ok": True,
                        "items": items})
    except Exception:  # noqa: BLE001
        pass

    # Commission submissions at THIS user's review stage — itemised for the
    # same reason (CFO 2026-09-12). commissions.service.review() re-checks the
    # stage, separation of duties and the payee rule itself, so listing one
    # here can never widen who may act on it.
    try:
        from commissions.access import STATUS_STAGE, user_stages
        from commissions.models import CommissionSubmission
        stages = user_stages(u)
        if stages:
            statuses = [st for st, stg in STATUS_STAGE.items() if stg in stages]
            qs = (CommissionSubmission.objects.filter(status__in=statuses)
                  .select_related('agent').order_by('submitted_at')[:200])
            items = []
            for sub_row in qs:
                amt = getattr(sub_row, 'gross_commission', None)
                age = _age_days(getattr(sub_row, 'submitted_at', None))
                agent_name = getattr(getattr(sub_row, 'agent', None), 'name', '') or '—'
                items.append(_mkitem(
                    sub_row.pk, f"{agent_name} · {getattr(sub_row, 'period_label', '') or ''}".strip(' ·'),
                    sub_row.get_status_display(),
                    amount=amt, age_days=age,
                    flags=_base_flags(amt, age, getattr(sub_row, 'submitted_at', None))))
            if items:
                out.append({"key": "commissions",
                            "label": "Commissions to review",
                            "href": "/commissions", "bulk_ok": True,
                            "items": items})
    except Exception:  # noqa: BLE001
        pass

    return out


def _approve_authority_to_recruit(user, pk):
    """Sign one Authority to Recruit. recruitment.authority_access.can_sign is
    the same gate the desktop page and the emailed link use."""
    from django.core.exceptions import ValidationError as DjangoValidationError
    from recruitment import authority_access as _access
    from recruitment.models import AuthorityToRecruit
    from recruitment.authority_views import record_decision
    a = AuthorityToRecruit.objects.get(pk=pk)
    may, why = _access.can_sign(user, a)
    if not may:
        raise DjangoValidationError(why or 'You cannot sign this authority.')
    record_decision(a, user, 'approve', '')


def _reject_authority_to_recruit(user, pk, reason):
    from django.core.exceptions import ValidationError as DjangoValidationError
    from recruitment import authority_access as _access
    from recruitment.models import AuthorityToRecruit
    from recruitment.authority_views import record_decision
    a = AuthorityToRecruit.objects.get(pk=pk)
    may, why = _access.can_sign(user, a)
    if not may:
        raise DjangoValidationError(why or 'You cannot sign this authority.')
    if not (reason or '').strip():
        raise DjangoValidationError('Give a reason when declining.')
    record_decision(a, user, 'decline', reason)


def _approve_commission(user, pk):
    """Advance one commission submission. review() enforces the stage, the
    maker-checker rule and the pays-you rule itself.

    review() signals a refusal with ValueError, which the bulk handler renders
    as the generic "Could not approve - it may have changed". That hides the
    only useful part: WHY. Re-raised as a validation error so the person is told
    "You cannot review your own submission" instead of a shrug. (/fabe 2026-09-13.)
    """
    from django.core.exceptions import ValidationError as DjangoValidationError
    from commissions import service as _csvc
    from commissions.models import CommissionSubmission
    try:
        _csvc.review(CommissionSubmission.objects.get(pk=pk), user, approve=True)
    except ValueError as exc:
        raise DjangoValidationError(str(exc)) from exc


def _reject_commission(user, pk, reason):
    from django.core.exceptions import ValidationError as DjangoValidationError
    from commissions import service as _csvc
    from commissions.models import CommissionSubmission
    try:
        _csvc.review(CommissionSubmission.objects.get(pk=pk), user, approve=False,
                     note=reason)
    except ValueError as exc:
        raise DjangoValidationError(str(exc)) from exc


def _approve_je(user, pk):
    from ledger.models import JournalEntry
    JournalEntry.objects.get(pk=pk).approve(user)


def _approve_payment(user, pk):
    from payments.models import Payment
    Payment.objects.get(pk=pk).approve_payment(
        user, comment="Approved via My Approvals (bulk)")


def _approve_incentive(user, pk):
    from hris.incentive_models import IncentiveRequest
    from hris.incentive_service import approve_request
    approve_request(IncentiveRequest.objects.get(pk=pk), user)


def _approve_staff_loan(user, pk):
    from staff_loans.models import StaffLoanApplication
    from staff_loans.services import cfo_decide
    cfo_decide(StaffLoanApplication.objects.get(pk=pk), user, approve=True,
               notes="Approved via My Approvals (bulk)")


def _approve_leave_encash(user, pk):
    from hris.leave_encash_models import LeaveEncashment
    from hris import leave_encash_service as _enc
    _enc.approve(LeaveEncashment.objects.get(pk=pk), user)


def _approve_petty_cash(user, pk):
    from petty_cash.models import PettyCashVoucher
    from petty_cash.services import approve_voucher
    approve_voucher(PettyCashVoucher.objects.get(pk=pk), user)


def _approve_po(user, pk):
    """Advance a PO one leg. fm_approve for the operational/claims-senior leg
    (PENDING_FM_APPROVAL), cfo_approve for the final CFO leg
    (PENDING_CFO_APPROVAL). Both re-check authority, SoD and status themselves."""
    from procurement.models import PurchaseOrder
    from procurement.services import fm_approve, cfo_approve
    po = PurchaseOrder.objects.get(pk=pk)
    if po.status == PurchaseOrder.Status.PENDING_CFO_APPROVAL:
        cfo_approve(po, user)
    else:
        fm_approve(po, user)


# stream key -> the SAME service call the module's own page uses. Any authority,
# SoD, quorum or status violation raises inside these (ValidationError etc.) and
# is reported per-item; a bad id raises DoesNotExist and is reported too.
# The streams that offer a tick box. Declared here, beside the adapters, so a
# new bulk-able stream and the code that acts on it are added in one place - the
# guard test asserts this list matches the `"bulk_ok": True` entries actually
# rendered by pending_approval_items_for(). Before this the guard iterated the
# rendered streams on an empty test database, so its loop body never ran and it
# passed by having nothing to check (/fabe 2026-09-13).
BULK_STREAM_KEYS = (
    'journal_entries',
    'payments',
    'incentives',
    'staff_loans',
    'leave_encash',
    'petty_cash',
    'po',
    'authority_to_recruit',
    'commissions',
)


_BULK_ADAPTERS = {
    "journal_entries": _approve_je,
    "payments": _approve_payment,
    "incentives": _approve_incentive,
    "staff_loans": _approve_staff_loan,
    "leave_encash": _approve_leave_encash,
    "petty_cash": _approve_petty_cash,
    "po": _approve_po,
    "authority_to_recruit": _approve_authority_to_recruit,
    "commissions": _approve_commission,
}

def _reject_je(user, pk, reason):
    from ledger.models import JournalEntry
    JournalEntry.objects.get(pk=pk).reject(user, reason)


def _reject_payment(user, pk, reason):
    from payments.models import Payment
    Payment.objects.get(pk=pk).reject_payment(user, reason)


def _reject_incentive(user, pk, reason):
    from hris.incentive_models import IncentiveRequest
    from hris.incentive_service import reject_request
    reject_request(IncentiveRequest.objects.get(pk=pk), user, notes=reason)


def _reject_staff_loan(user, pk, reason):
    from staff_loans.models import StaffLoanApplication
    from staff_loans.services import cfo_decide
    cfo_decide(StaffLoanApplication.objects.get(pk=pk), user, approve=False,
               decline_reason=reason)


def _reject_leave_encash(user, pk, reason):
    from hris.leave_encash_models import LeaveEncashment
    from hris import leave_encash_service as _enc
    _enc.reject(LeaveEncashment.objects.get(pk=pk), user, notes=reason)


def _reject_petty_cash(user, pk, reason):
    from petty_cash.models import PettyCashVoucher
    from petty_cash.services import reject_voucher
    reject_voucher(PettyCashVoucher.objects.get(pk=pk), user, reason)


def _reject_po(user, pk, reason):
    from procurement.models import PurchaseOrder
    from procurement.services import reject as po_reject
    po_reject(PurchaseOrder.objects.get(pk=pk), user, reason)


# stream key -> the SAME reject the module's own page uses. A reject always
# carries a reason (the canned "send back" text the approver tapped) and sends
# the item back to its submitter to fix + re-submit (payments drop to DRAFT, JEs
# to REJECTED-reopenable, etc.) — this is how "give me more info" / "hold" /
# "possible duplicate" are actioned without a bespoke state per module.
_REJECT_ADAPTERS = {
    "journal_entries": _reject_je,
    "payments": _reject_payment,
    "incentives": _reject_incentive,
    "staff_loans": _reject_staff_loan,
    "leave_encash": _reject_leave_encash,
    "petty_cash": _reject_petty_cash,
    "po": _reject_po,
    "authority_to_recruit": _reject_authority_to_recruit,
    "commissions": _reject_commission,
}


_BULK_CAP = 100   # most items one click may action — a guardrail, not a limit anyone hits


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_approval_items(request):
    """Itemised bulk-able streams for the current user (drives the checkboxes)."""
    streams = pending_approval_items_for(request.user)
    return Response({"streams": streams,
                     "total": sum(len(s["items"]) for s in streams)})


def _payment_brief(pk):
    """High-signal facts about ONE pending payment (CFO wow-feature 3). Real
    numbers, no LLM: Nth payment to this payee this month, how it compares to
    their recent average, first-ever. Read-only."""
    from payments.models import Payment
    from django.db.models import Avg
    from decimal import Decimal
    p = Payment.objects.filter(pk=pk).select_related("contact").first()
    if p is None:
        return ""
    bits = []
    cid = getattr(p, "contact_id", None)
    payee = (p.payee_name if getattr(p, "is_once_off", False)
             else getattr(p.contact, "name", "")) or "this payee"
    if cid:
        paid = Payment.objects.filter(
            contact_id=cid, payment_type=Payment.PaymentType.SENT).exclude(
            approval_status=Payment.ApprovalStatus.PENDING)
        if not paid.exists():
            bits.append(f"First-ever payment to {payee}.")
        else:
            month_n = paid.filter(
                created_at__year=timezone.now().year,
                created_at__month=timezone.now().month).count() + 1
            bits.append(f"Payment #{month_n} to {payee} this month.")
            avg = paid.aggregate(a=Avg("amount"))["a"]
            if avg and avg > 0 and p.amount:
                pct = (Decimal(p.amount) - avg) / avg * 100
                if abs(pct) >= 10:
                    bits.append(f"{abs(int(pct))}% {'above' if pct > 0 else 'below'} their usual.")
    if not bits:
        bits.append(f"{_fmt_amount(p.amount, p.currency_code_id)} to {payee}.")
    return " ".join(bits)


_BRIEF_BUILDERS = {"payments": _payment_brief}


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def approval_brief(request):
    """A one-line brief for the decision sheet (wow-feature 3). Computed on
    demand for a SINGLE item (the sheet the approver just opened) so the
    dashboard list stays fast. Deterministic facts today; the same endpoint can
    later call reasoning_complete for phrasing without changing the contract."""
    stream = request.GET.get("stream")
    item_id = request.GET.get("id")
    builder = _BRIEF_BUILDERS.get(stream)
    if builder is None or not item_id:
        return Response({"brief": ""})
    try:
        return Response({"brief": builder(item_id)})
    except Exception:  # noqa: BLE001 — a brief is a bonus, never an error
        return Response({"brief": ""})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def approval_pack(request):
    """The full detail behind ONE approval — the lines, the rates, the
    deductions and the sanity checks — for the phone app's decision sheet.

    CFO 2026-09-20: approving from a phone meant opening the web version to see
    what you were signing. Same pack core/approval_pack.py renders into the
    approval email and the no-login confirm page, so the three cannot drift.

    Entitlement, not obscurity: an id is only answered when it is CURRENTLY in
    this user's own pending list. Packs carry payee bank details, salaries and
    client names, so "you knew the id" is not a permission.
    """
    stream = request.GET.get("stream") or ""
    item_id = request.GET.get("id") or ""
    if not stream or not item_id:
        return Response({"pack": None})
    entitled = False
    for s in pending_approval_items_for(request.user):
        if s.get("key") == stream and any(str(i.get("id")) == str(item_id)
                                          for i in s.get("items", [])):
            entitled = True
            break
    if not entitled:
        return Response({"pack": None, "reason": "not on your approval list"}, status=403)
    from core.approval_pack import build_pack
    return Response({"pack": build_pack(stream, item_id)})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def push_vapid_key(request):
    """The VAPID PUBLIC key the browser needs to subscribe (wow-feature 4).
    Empty string if push isn't configured — the client then hides the button."""
    import os
    from django.conf import settings
    key = getattr(settings, "WEBPUSH_VAPID_PUBLIC_KEY", "") or os.environ.get(
        "WEBPUSH_VAPID_PUBLIC_KEY", "")
    return Response({"key": key})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def push_subscribe(request):
    """Save (or refresh) THIS device's Web Push subscription for the current
    user. Body is the PushSubscription JSON the browser returns."""
    sub = request.data.get("subscription") or request.data or {}
    endpoint = sub.get("endpoint")
    keys = sub.get("keys") or {}
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not (endpoint and p256dh and auth):
        return Response({"detail": "Incomplete subscription."}, status=400)
    from core.models import PushSubscription
    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={"user": request.user, "p256dh": p256dh, "auth": auth,
                  "user_agent": request.META.get("HTTP_USER_AGENT", "")[:300]})
    return Response({"ok": True})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def bulk_approve(request):
    """Approve the selected items in one request.

    Body: {"items": [{"stream": "<key>", "id": "<pk>"}, ...]}.
    Each item is dispatched to its module's real approve entrypoint, one at a
    time, each in its own service-level transaction — so one item failing (SoD,
    already-signed, gone) NEVER rolls back the items that succeeded. Returns a
    per-item result so the UI can show "7 approved, 1 couldn't (reason)".
    """
    from django.core.exceptions import ValidationError as DjangoValidationError
    from django.db import transaction
    from rest_framework.exceptions import ValidationError as DRFValidationError

    items = request.data.get("items") or []
    if not isinstance(items, list) or not items:
        return Response({"detail": "No items selected."}, status=400)
    if len(items) > _BULK_CAP:
        return Response(
            {"detail": f"Too many at once — select {_BULK_CAP} or fewer."},
            status=400)

    approved, failed = [], []
    for raw in items:
        stream = (raw or {}).get("stream")
        item_id = (raw or {}).get("id")
        adapter = _BULK_ADAPTERS.get(stream)
        if adapter is None or item_id in (None, ""):
            failed.append({"stream": stream, "id": item_id,
                           "error": "Not a bulk-approvable item."})
            continue
        try:
            # Each item in its OWN savepoint: a DB-level failure on one item
            # rolls back only that item and can never leave the connection in an
            # aborted state for the items that follow it.
            with transaction.atomic():
                adapter(request.user, item_id)
            approved.append({"stream": stream, "id": str(item_id)})
        except (DjangoValidationError, DRFValidationError) as exc:
            msg = getattr(exc, "message", None) or getattr(exc, "messages", None) \
                or getattr(exc, "detail", None) or str(exc)
            if isinstance(msg, (list, tuple)):
                msg = "; ".join(str(m) for m in msg)
            failed.append({"stream": stream, "id": str(item_id), "error": str(msg)})
        except Exception as exc:  # noqa: BLE001 — bad id / gone / anything else
            failed.append({"stream": stream, "id": str(item_id),
                           "error": "Could not approve — it may have changed or "
                                    "already been actioned."})
            import logging
            logging.getLogger(__name__).warning(
                "bulk_approve %s/%s failed: %s", stream, item_id, exc)

    return Response({"approved": len(approved), "approved_items": approved,
                     "failed": failed})


def _decide_error_text(exc):
    from django.core.exceptions import ValidationError as DjangoValidationError
    from rest_framework.exceptions import ValidationError as DRFValidationError
    if isinstance(exc, (DjangoValidationError, DRFValidationError)):
        msg = getattr(exc, "message", None) or getattr(exc, "messages", None) \
            or getattr(exc, "detail", None) or str(exc)
        if isinstance(msg, (list, tuple)):
            msg = "; ".join(str(m) for m in msg)
        return str(msg)
    return "Could not action this — it may have changed or already been decided."


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def decide(request):
    """One-tap decision on a SINGLE approval item — the five preset buttons
    (CFO 2026-07-22: "come up with five buttons which I can press", reusing the
    Omni completion-note presets).

    Body: {stream, id, action, note?}.
      action='approve' → the module's real approve (note ignored).
      action='reject'  → the module's real reject with `note` as the reason.
                         Every "send back" button — need-more-info, hold,
                         possible-duplicate — is a reject carrying that button's
                         canned reason, so the item returns to its submitter to
                         fix + re-submit. The reason is mandatory (the buttons
                         always supply one), so a reject is never reasonless.

    Runs inside one savepoint; authority / SoD / status / quorum are all enforced
    by the underlying service, identical to acting on the item's own page."""
    from django.db import transaction

    stream = request.data.get("stream")
    item_id = request.data.get("id")
    action = (request.data.get("action") or "").strip()
    note = (request.data.get("note") or "").strip()

    if not stream or item_id in (None, ""):
        return Response({"detail": "Missing item."}, status=400)

    if action == "approve":
        adapter = _BULK_ADAPTERS.get(stream)
        if adapter is None:
            return Response({"detail": "This item cannot be approved here."}, status=400)
    elif action == "reject":
        adapter = _REJECT_ADAPTERS.get(stream)
        if adapter is None:
            return Response({"detail": "This item cannot be rejected here."}, status=400)
        if not note:
            return Response({"detail": "A reason is required to send this back."}, status=400)
    else:
        return Response({"detail": "Unknown action."}, status=400)

    try:
        with transaction.atomic():
            if action == "approve":
                adapter(request.user, item_id)
            else:
                adapter(request.user, item_id, note)
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "decide %s %s/%s failed: %s", action, stream, item_id, exc)
        return Response({"detail": _decide_error_text(exc)}, status=400)

    return Response({"ok": True, "action": action, "stream": stream, "id": str(item_id)})


_HISTORY_LABELS = {
    "JournalEntry": "Journal entry", "Payment": "Payment",
    "IncentiveRequest": "Incentive", "StaffLoanApplication": "Staff loan",
    "LeaveEncashment": "Leave encashment", "PettyCashVoucher": "Petty cash",
    "PurchaseOrder": "Purchase order", "LeaveRequest": "Leave", "SpendRequest": "Spend",
    "ExpenseClaim": "Refund",
}


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_approval_history(request):
    """"What I signed" — this user's recent approve decisions across Omni, newest
    first (CFO 2026-07-22 wow-feature). Reads the AuditLog APPROVE rows the
    modules already write, so it needs no new storage."""
    from core.models import AuditLog
    rows = (AuditLog.objects
            .filter(user=request.user, action=AuditLog.Action.APPROVE)
            .order_by("-created_at")[:60])
    out = [{
        "kind": _HISTORY_LABELS.get(r.table_name, r.table_name),
        "detail": (r.description or "")[:160],
        "at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]
    return Response({"history": out, "total": len(out)})


def _approver_candidates():
    """Who the DAILY CHASE emails. Deliberately NARROW — the real sign-off owners,
    NOT everyone the whitelist lets see a queue (that would spam dev/admin/external
    superusers about the same items). The CFO + Head of Human Capital (Unami) are
    the accountable approvers for leave/refunds/spend today (CFO 2026-07-14:
    'shame ME when I delay'). The dashboard panel stays a pull view for everyone.
    Widen here later if more roles should be chased."""
    from django.contrib.auth.models import User
    from hris.amendment_service import CFO_EMAIL, UNAMI_EMAIL
    emails = [e for e in (CFO_EMAIL, UNAMI_EMAIL) if e]
    q = User.objects.none()
    from django.db.models import Q
    cond = Q()
    for e in emails:
        cond |= Q(email__iexact=e)
    return User.objects.filter(cond, is_active=True) if emails else q


_QUICK_LINK_CAP = 6   # per-item approve links shown before "+N more"


def _quick_link_row(label_html: str, url: str) -> str:
    return (
        '<tr><td style="padding:8px 10px;border-bottom:1px solid #f0f3f8;font-size:13px;">'
        f'{label_html}</td>'
        '<td style="padding:8px 10px;border-bottom:1px solid #f0f3f8;text-align:right;">'
        f'<a href="{url}" style="background:#0D1B2A;color:#F4A623;text-decoration:none;'
        'font-size:12.5px;font-weight:700;padding:6px 14px;border-radius:7px;'
        'display:inline-block;">Approve &rarr;</a></td></tr>')


def _leave_quick_links(u) -> str:
    """Up to _QUICK_LINK_CAP individual one-click Approve links for leave
    (CFO 2026-07-14: 'a link would be great'). Reuses hris.leave_actions —
    the SAME signed-token mechanism the leave email itself uses."""
    try:
        from hris.models import LeaveRequest
        from hris.leave_actions import action_url
    except Exception:  # noqa: BLE001
        return ''
    qs = (LeaveRequest.objects.filter(status='pending')
          .select_related('profile__employee', 'leave_type')
          .order_by('created_at'))
    total = qs.count()
    if not total:
        return ''
    rows = []
    for lr in qs[:_QUICK_LINK_CAP]:
        name = lr.profile.employee.full_name if lr.profile_id else 'Unknown'
        when = lr.day_breakdown() if hasattr(lr, 'day_breakdown') else ''
        rows.append(_quick_link_row(f'<b>{name}</b><br><span style="color:#6B7280;">{when}</span>',
                                    action_url(lr, u)))
    more = total - len(rows)
    tail = (f'<p style="font-size:12px;color:#6B7280;margin:4px 0 0;">+{more} more — '
            f'open My Approvals to see all.</p>' if more > 0 else '')
    return (f'<p style="margin:14px 0 4px;font-weight:700;font-size:13px;">Leave — quick approve</p>'
            f'<table style="border-collapse:collapse;width:100%;">{"".join(rows)}</table>{tail}')


def _spend_quick_links(u) -> str:
    """Up to _QUICK_LINK_CAP individual one-click Approve links for spend &
    event requests (CFO 2026-07-14). Reuses budgets.spend_actions — the same
    signed-token mechanism, mirrored from the leave one-click pattern."""
    try:
        from budgets.models import SpendRequest
        from budgets.spend_actions import action_url
    except Exception:  # noqa: BLE001
        return ''
    qs = (SpendRequest.objects.filter(status=SpendRequest.Status.SUBMITTED)
          .select_related('requester').order_by('created_at'))
    total = qs.count()
    if not total:
        return ''
    rows = []
    for sr in qs[:_QUICK_LINK_CAP]:
        who = sr.requester.get_full_name() or sr.requester.username
        rows.append(_quick_link_row(
            f'<b>{sr.title}</b><br><span style="color:#6B7280;">{who} · BWP {sr.amount:,.2f}</span>',
            action_url(sr, u)))
    more = total - len(rows)
    tail = (f'<p style="font-size:12px;color:#6B7280;margin:4px 0 0;">+{more} more — '
            f'open My Approvals to see all.</p>' if more > 0 else '')
    return (f'<p style="margin:14px 0 4px;font-weight:700;font-size:13px;">Spend &amp; event — quick approve</p>'
            f'<table style="border-collapse:collapse;width:100%;">{"".join(rows)}</table>{tail}')


def email_pending_approvals():
    """Daily nudge: email each approver the items sitting on them, with how long
    they've waited — the 'name & shame me when I delay' chase (CFO 2026-07-14).
    Leave and spend rows also carry individual one-click Approve links (CFO
    2026-07-14: 'can't I approve these from my phone, a link would be great') —
    same signed-token, no-sign-in-needed pattern as the leave approval email.
    Returns {'people': n, 'emailed': n}."""
    emailed = 0
    people = 0
    for u in _approver_candidates():
        streams = pending_approvals_for(u)
        if not streams:
            continue
        people += 1
        email = (getattr(u, "email", "") or "").strip()
        if not email:
            continue
        first = (u.get_full_name() or u.username).split(" ")[0]
        cell = "padding:6px 10px;border-bottom:1px solid #e5e7eb;"
        row_parts = []
        for s in streams:
            days = int(s.get("oldest_days", 0) or 0)
            waited = f'{days} day(s)'
            if days >= 2:
                waited = f'<b style="color:#B04E00">{waited}</b>'
            row_parts.append(
                f'<tr><td style="{cell}">{s["label"]}</td>'
                f'<td style="{cell}" align="center">{s["count"]}</td>'
                f'<td style="{cell}" align="center">{waited}</td></tr>')
        rows = "".join(row_parts)
        try:
            quick_links = _leave_quick_links(u) + _spend_quick_links(u)
        except Exception:  # noqa: BLE001 — quick links are a bonus, never block the chase
            quick_links = ''
        html = (
            f"<p>{first},</p>"
            f"<p>These approvals are waiting on <b>you</b> in Omni. Please clear them "
            f"so nobody is held up:</p>"
            '<table style="border-collapse:collapse;width:100%;font-size:14px;">'
            '<tr style="background:#0D1B2A;color:#fff;text-align:left;">'
            '<th style="padding:7px 10px;">Awaiting you</th><th style="padding:7px 10px;">Count</th>'
            '<th style="padding:7px 10px;">Waiting</th></tr>'
            + rows + "</table>"
            + quick_links +
            '<p style="margin-top:12px;">Open Omni &rarr; <strong>My Approvals</strong> (or the Task Dashboard) to act.</p>')
        try:
            from core.notifications import send_html_with_cfo_cc
            emailed += send_html_with_cfo_cc(
                subject="Omni — approvals waiting on you",
                html=html, to=[email], cc_cfo=False)
        except Exception:  # noqa: BLE001
            continue
    return {"people": people, "emailed": emailed}
