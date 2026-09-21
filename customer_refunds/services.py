"""
customer_refunds/services.py

Business logic for the customer-refund → FNB money leg. Kept out of the views
so it is unit-testable without HTTP.

Design decisions (respecting CFO reservations):
  * Finance approval raises a once-off `payments.Payment` (SENT, is_once_off)
    linked to the refund. It is NOT confirmed here, so NO journal entry is
    posted — GL posting for a customer refund is a deliberate Finance step
    reserved to the CFO (same stance as the staff ExpenseClaim flow).
  * Loading to FNB is GATED: preview-only until REFUND_FNB_SEND_ENABLED is
    True AND the caller can manage FNB. Preview returns exactly what WOULD be
    sent — no money moves.
  * The account number is decrypted in-process only to build the payment.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

log = logging.getLogger(__name__)


#: Where FNB sends the refund proof of payment (Keetile Mokhendo, 2026-09-11).
REFUND_POP_EMAIL = 'refund@alphadirect.co.bw'


class RefundConfigError(Exception):
    """Raised when the company / bank / source account isn't configured."""


def _resolve_company():
    from core.models import Company
    code = getattr(settings, 'CUSTOMER_REFUND_COMPANY_CODE', 'ADIC')
    return Company.objects.filter(code__iexact=code).first()


def _resolve_bank_gl_account(company):
    """The ledger.Account (bank) the refund pays from — for the Payment row."""
    from ledger.models import Account
    code = getattr(settings, 'CUSTOMER_REFUND_BANK_GL_CODE', '')
    qs = Account.objects.all()
    if company is not None:
        qs = qs.filter(owner_company=company) | Account.objects.filter(owner_company__isnull=True)
    if code:
        acc = qs.filter(code=code).first()
        if acc:
            return acc
    # Fallback: first bank account. Payment.save validates is_bank_account=True,
    # so filter on THAT flag (not sub_type) or the raised Payment 500s (Fable fix 6).
    return qs.filter(is_bank_account=True).order_by('code').first()


#: Alpha Direct Current Account. Keetile Mokhendo, who loads these payments into
#: FNB by hand today, named this as the account refunds are actually paid from
#: (2026-09-11); the CFO confirmed it the same day. It is refund-specific on
#: purpose — the general FNB_DEBTOR_ACCOUNT_NUMBER serves supplier and payroll
#: runs, and moving refunds must not move those.
REFUND_DEBIT_ACCOUNT_DEFAULT = '62403392335'


def _resolve_source_bank_account():
    """The banking.BankAccount (FNB debtor) refund money leaves from."""
    from banking.models import BankAccount
    num = (getattr(settings, 'REFUND_FNB_DEBIT_ACCOUNT_NUMBER', '')
           or REFUND_DEBIT_ACCOUNT_DEFAULT)
    # is_active, like the payroll resolver of this same account: a closed
    # account must fail closed here, not quietly become the source.
    return BankAccount.objects.filter(account_number=num, is_active=True).first()


def create_refund_payment(refund, user):
    """Raise a once-off Payment for an approved refund. No GL posting.

    Returns the Payment. Raises RefundConfigError with a plain message if the
    company / bank account isn't set up (so the caller can 400, never 500).
    """
    from billing.models import Contact
    from payments.models import Payment

    company = _resolve_company()
    if company is None:
        raise RefundConfigError(
            'No company configured for customer refunds. Set '
            'CUSTOMER_REFUND_COMPANY_CODE to a real company code.')
    bank_gl = _resolve_bank_gl_account(company)
    if bank_gl is None:
        raise RefundConfigError(
            'No bank GL account found for refunds. Set CUSTOMER_REFUND_BANK_GL_CODE.')

    acct = refund.get_account_number()
    if not acct:
        raise RefundConfigError('Refund has no customer bank account number.')

    sentinel = Contact.objects.filter(
        company=company, contact_type='vendor',
        name='Ad-hoc / One-off Payee').order_by('created_at').first()
    if sentinel is None:
        sentinel = Contact.objects.create(
            company=company, contact_type='vendor',
            name='Ad-hoc / One-off Payee', currency_code_id='BWP')

    # The three bank-facing strings and the proof-of-payment address, set here
    # so the refund reaches FNB filled in exactly as Finance asks and nobody
    # re-types it on the bank screen (Keetile Mokhendo, 2026-09-11).
    # build_defaults is Finance's own 2026-08-20 wording — "ALPHA DIRECT REFUND
    # + policy number" to the client, "REFUND + policy + initials" for our own
    # statement — and it is the SAME call the payment request uses, so the bank
    # leg and the covering table cannot say two different things.
    from taskboard.narration_templates import (PaymentNarrationType,
                                               build_defaults)
    wording = build_defaults(
        payment_type=PaymentNarrationType.CLIENT_REFUND,
        policy_number=refund.policy_number,
        account_name=(refund.customer_name or ''))

    payment = Payment(
        payment_type=Payment.PaymentType.SENT,
        contact=sentinel, company=company, bank_account=bank_gl,
        payment_date=timezone.localdate(),
        currency_code_id=(refund.currency or 'BWP'),
        amount=Decimal(str(refund.refund_amount)),
        payment_method=Payment.PaymentMethod.BANK_TRANSFER,
        reference=f'Refund {refund.policy_number}'[:200],
        description=f'MIS customer refund · policy {refund.policy_number} · '
                    f'reason: {refund.reason}'[:1000],
        is_once_off=True,
        payee_name=(refund.customer_name or refund.policy_number)[:200],
        payee_bank_name=(refund.bank_name or '')[:120],
        payee_account_number=acct[:40],
        payee_branch_code=(refund.branch_code or '')[:20],
        bank_beneficiary_name=(refund.customer_name or refund.policy_number)[:140],
        bank_narration=wording['narration'],
        bank_our_reference=wording['our_reference'],
        # FNB emails the proof of payment here. A shared refunds mailbox, not a
        # person: whoever is covering refunds that week must see it.
        remittance_email=REFUND_POP_EMAIL,
        created_by=user,
    )
    payment.save(audit_user=user)
    return payment


def fnb_send_enabled() -> bool:
    """The refund route's own arming switch.

    It used to read FNB_QUICK_TRANSFER_ENABLED — the SAME flag that opens the
    type-in-any-account quick-transfer screen, where one person can key any bank
    account with no second approver. Arming refunds therefore also opened that
    side door (Pramod Bisen, 2026-09-02). They are now separate settings so
    refunds can go live on their own: REFUND_FNB_SEND_ENABLED arms refunds,
    FNB_QUICK_TRANSFER_ENABLED still gates only the quick-transfer screen.
    """
    return bool(getattr(settings, 'REFUND_FNB_SEND_ENABLED', False))


def load_refund_to_fnb(refund, user, *, live=False, from_handoff=False,
                       allow_single_person=False):
    """Preview or (if enabled) send the refund's payment to FNB.

    Returns a dict:
      {mode: 'preview'|'live', would_send:{...}, batch_id?, fnb_reference?}
    Raises RefundConfigError on missing config.

    from_handoff=True is the Customer Refund Engine path (CFO 2026-07-28): the
    refund was already approved in Graphite (one approver <=P5k, two >P5k) and
    the CFO authorises every payment in FNB online banking — those are the two
    eyes. So an engine refund does NOT additionally need the Omni Payments-queue
    CONFIRMED/GL step before loading; the fraud re-check + master switch below
    still apply. (GL posting for refunds stays a manual CFO step, as accepted.)
    """
    if refund.payment is None:
        raise RefundConfigError('Refund has no payment raised yet — approve it first.')

    source = _resolve_source_bank_account()
    would = {
        'payee_name':      refund.payment.payee_name,
        'account_last4':   refund.account_last4,
        'bank_name':       refund.payment.payee_bank_name,
        'branch_code':     refund.payment.payee_branch_code,
        'amount':          str(refund.payment.amount),
        'currency':        refund.currency,
        'reference':       refund.payment.reference,
        'source_account':  getattr(source, 'account_number', None),
    }

    # Gate: preview unless explicitly live AND the master switch is on.
    if not (live and fnb_send_enabled()):
        return {'mode': 'preview', 'would_send': would,
                'note': 'FNB live send is OFF (safety flag). This is a preview only — '
                        'no money moved.'}

    if source is None:
        raise RefundConfigError(
            'No FNB source account found for refunds — set '
            'REFUND_FNB_DEBIT_ACCOUNT_NUMBER to a real, active banking.BankAccount.')

    from payments.models import Payment

    # H9 — money-out eligibility invariant. Send ONLY a payment that passed the
    # standard controls (confirmed + maker-checked + GL-posted), the same filter
    # the canonical FNB view enforces. The refund's Payment is raised as DRAFT;
    # it must be confirmed/approved through the Payments queue (where the GL
    # posts — a CFO-reserved step) BEFORE it can leave here. This blocks sending
    # an unapproved DRAFT even when the master switch is on.
    base = Payment.objects.filter(
        pk=refund.payment_id,
        payment_type=Payment.PaymentType.SENT,
        bank_submitted_at__isnull=True,
    ).exclude(approval_status=Payment.ApprovalStatus.REJECTED)
    # Engine refunds (from_handoff) carry their four-eyes from Graphite + the
    # CFO's FNB authorisation, so they skip the Omni Payments-queue CONFIRMED
    # requirement. The standalone (UI) money path keeps the strict gate.
    qs = base if from_handoff else base.filter(status=Payment.Status.CONFIRMED)
    if not qs.exists():
        raise RefundConfigError(
            'The refund payment is not eligible to send (already sent, rejected, '
            'or — for the standalone path — not yet confirmed through the Payments queue).')

    # Four-eyes: whoever approved the refund cannot also be the one sending it.
    if refund.finance_approved_by_id and refund.finance_approved_by_id == getattr(user, 'id', None):
        raise RefundConfigError('A different person must send the payment to the bank '
                                '(four-eyes control).')

    # Last-moment fraud re-check — velocity/structuring can turn true between
    # approval and payment; the FNB load is the last point money is stoppable.
    # A flag a senior already reviewed + overrode at approval is exempt; only a
    # NEW critical code blocks the send (Fable fix 4).
    from .fraud import apply_scan
    apply_scan(refund)
    overridden = set((refund.ai_evidence or {}).get('fraud_override', {}).get('codes', []))
    new_critical = [f for f in (refund.fraud_flags or [])
                    if f.get('severity') == 'CRITICAL' and f.get('code') not in overridden]
    if new_critical:
        raise RefundConfigError(
            'Blocked at payment — a new CRITICAL fraud flag is on this refund '
            f'({", ".join(f["code"] for f in new_critical)}). Re-review before money leaves.')

    from fnb.payments import submit_eft_batch
    batch = submit_eft_batch(qs, source_account=source, user=user,
                             allow_single_person=allow_single_person)
    refund.fnb_batch = batch
    refund.status = refund.Status.FNB_LOADED
    refund.save(update_fields=['fnb_batch', 'status', 'updated_at'])
    return {'mode': 'live', 'would_send': would,
            'batch_id': str(batch.pk),
            'fnb_reference': getattr(batch, 'fnb_reference', '')}


def post_refund_back_to_graphite(refund) -> dict:
    """Tell Graphite the refund is paid so it can post to the policy + flip the
    portal flag. Dormant until GRAPHITE_REFUND_CALLBACK_URL + token are set —
    never crashes the caller.
    """
    import json
    import urllib.error
    import urllib.request

    url = getattr(settings, 'GRAPHITE_REFUND_CALLBACK_URL', '') or ''
    token = getattr(settings, 'GRAPHITE_REFUND_CALLBACK_TOKEN', '') or ''
    if not url or not token:
        return {'sent': False, 'reason': 'callback_not_configured'}

    payload = json.dumps({
        'graphite_ref': refund.graphite_ref,
        'policy_number': refund.policy_number,
        'amount': str(refund.refund_amount),
        'fnb_reference': getattr(refund.fnb_batch, 'fnb_reference', ''),
        'status': 'paid',
    }).encode()
    req = urllib.request.Request(
        url, data=payload, method='POST',
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            ok = 200 <= r.status < 300
        if ok:
            refund.graphite_posted = True
            refund.graphite_posted_at = timezone.now()
            refund.status = refund.Status.POSTED_BACK
            refund.save(update_fields=['graphite_posted', 'graphite_posted_at',
                                       'status', 'updated_at'])
        return {'sent': ok}
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        return {'sent': False, 'reason': str(e)[:200]}


# ─── Auto-stage on Graphite handoff + notify the CFO (CFO 2026-07-28) ────────
# Finance approves in Graphite → Omni AUTO-raises the payment, loads it to FNB
# (preview until REFUND_FNB_SEND_ENABLED is on), and tasks + emails the CFO
# to authorise it in FNB online banking. The CFO does NOTHING in Omni.

def _resolve_cfo_user():
    """The CFO user (UserProfile.title == CFO), else the first superuser."""
    from django.contrib.auth import get_user_model
    from core.models import UserProfile
    User = get_user_model()
    u = (User.objects.filter(profile__title=UserProfile.Title.CFO, is_active=True)
         .order_by('id').first())
    return u or User.objects.filter(is_superuser=True, is_active=True).order_by('id').first()


def notify_cfo_to_authorise(refund, load_result, cfo) -> None:
    """Task + email the CFO that a refund is loaded and awaiting his FNB
    authorisation. Best-effort — never raises."""
    if cfo is None:
        return
    try:
        from core.models import OmniTask
        mode = (load_result or {}).get('mode', 'preview')
        amt = refund.refund_amount
        live_note = ('LIVE — awaiting your authorisation in FNB'
                     if mode == 'live'
                     else 'PREVIEW only — the FNB money leg is still switched OFF')
        title = f'Authorise refund in FNB — {refund.policy_number} · P{amt}'
        body = (
            f'Refund {refund.graphite_ref} is approved in Graphite and loaded to FNB '
            f'({live_note}).\n\n'
            f'Customer: {refund.customer_name}\n'
            f'Policy: {refund.policy_number}\n'
            f'Amount: P{amt} {refund.currency}\n'
            f'Bank: {refund.bank_name} · branch {refund.branch_name or "-"} '
            f'{("(" + refund.branch_code + ")") if refund.branch_code else ""} · acct ****{refund.account_last4}\n\n'
            f'Action: log into FNB online banking and authorise the payment. '
            f'Money does not leave until you approve it in FNB.'
        )
        t = OmniTask.objects.create(
            assigner=cfo, assignee=cfo,
            title=title[:200], body=body[:5000],
            status=OmniTask.Status.PENDING,
            source='refund_engine',
        )
        # The email must be sent HERE, not through taskboard.email_task_assigned
        # (CFO 2026-07-29). That helper returns 0 immediately when
        # assigner == assignee — "don't email someone a task they gave
        # themselves" — and this task is raised BY the CFO FOR the CFO, so the
        # only alert on a live money path could never send. Proven by reading
        # the guard; the task was created and the email silently dropped.
        _email_cfo_authorisation(refund, t, cfo, mode)
    except Exception:  # noqa: BLE001 — notification must never break the money leg
        pass


def _email_cfo_authorisation(refund, task, cfo, mode: str) -> int:
    """Email the CFO that a refund is sitting in FNB waiting for him.

    Purpose-built rather than reusing the generic task mail: this is a money
    alert, the action is in ANOTHER system (FNB online banking), and the wording
    has to say whether the money is real yet. Never raises. Returns 1 on send.
    """
    email = (getattr(cfo, 'email', '') or '').strip()
    if not email:
        return 0
    from django.utils.html import escape
    base = (getattr(settings, 'PUBLIC_BASE_URL',
                    'https://omni.alphadirect.co.bw') or '').rstrip('/')
    live = mode == 'live'
    banner = (('#991B1B', 'LIVE — this payment is in FNB now and needs your '
                          'authorisation before the money moves')
              if live else
              ('#6B7280', 'PREVIEW only — the FNB money leg is switched OFF, '
                          'nothing is loaded at the bank'))
    first = (cfo.get_full_name() or '').split(' ')[0] or cfo.username
    html = (
        f'<p>{escape(first)},</p>'
        f'<p style="padding:9px 12px;border-left:3px solid {banner[0]};'
        f'background:#F9FAFB;color:{banner[0]};font-weight:600;">{banner[1]}</p>'
        '<table style="border-collapse:collapse;width:100%;font-size:14px;">'
        f'<tr><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;width:120px;'
        f'color:#6B7280;">Refund</td><td style="padding:5px 9px;'
        f'border-bottom:1px solid #e5e7eb;font-weight:600;">{escape(refund.graphite_ref)}</td></tr>'
        f'<tr><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;color:#6B7280;">'
        f'Customer</td><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;">'
        f'{escape(refund.customer_name or "—")}</td></tr>'
        f'<tr><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;color:#6B7280;">'
        f'Policy</td><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;">'
        f'{escape(refund.policy_number)}</td></tr>'
        f'<tr><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;color:#6B7280;">'
        f'Amount</td><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;'
        f'font-weight:700;color:#0D1B2A;">{refund.currency} {refund.refund_amount:,.2f}</td></tr>'
        f'<tr><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;color:#6B7280;">'
        f'Paying into</td><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;">'
        f'{escape(refund.bank_name or "—")} &middot; acct ****{escape(refund.account_last4)}</td></tr>'
        '</table>'
        + (f'<p style="margin-top:14px;"><strong>What you do:</strong> log into FNB '
           f'online banking and authorise the payment. Money does not leave until '
           f'you approve it there — there is nothing to click in Omni.</p>'
           if live else
           '<p style="margin-top:14px;">Nothing to do — this is a dry run. The '
           'payment was not loaded at the bank.</p>')
        + f'<p style="color:#6B7280;font-size:12px;">It is also on your Omni '
          f'dashboard: <a href="{base}/my-approvals">{base}/my-approvals</a></p>'
    )
    text = (
        f'{first}, {banner[1]}.\n\n'
        f'Refund: {refund.graphite_ref}\nCustomer: {refund.customer_name or "-"}\n'
        f'Policy: {refund.policy_number}\n'
        f'Amount: {refund.currency} {refund.refund_amount:,.2f}\n'
        f'Paying into: {refund.bank_name or "-"} acct ****{refund.account_last4}\n\n'
        + ('Authorise the payment in FNB online banking. Nothing to click in Omni.\n'
           if live else 'Nothing to do — dry run, not loaded at the bank.\n')
        + f'\nDashboard: {base}/my-approvals\n'
    )
    subject = (('Authorise in FNB — refund ' if live else 'Refund staged (preview) — ')
               + f'{refund.policy_number} · {refund.currency} {refund.refund_amount:,.2f}')
    try:
        from core.notifications import send_html_with_cfo_cc
        return send_html_with_cfo_cc(subject=subject[:150], html=html, to=[email],
                                     text_fallback=text, cc_cfo=False)
    except Exception:  # noqa: BLE001 — mail must never break the money leg
        return 0


# BWP 5,000 — the same line Graphite uses for one approver vs two.
REFUND_AUTOLOAD_MAX_BWP_DEFAULT = Decimal('5000')


def refund_autoload_ceiling() -> Decimal:
    """Up to this amount an engine refund still loads to the bank on one actor.

    CFO 2026-08-20, resolving a collision between two of his own rulings: on
    2026-07-28 he held that a Graphite refund's two pairs of eyes are Graphite's
    own approval plus his authorisation in FNB banking, deliberately a single
    actor inside Omni; on 2026-08-20 he ruled that whoever prepares a payment
    may not release it. His answer was "auto-load only below an amount".

    The amount is BWP 5,000 because that is the line the business ALREADY uses
    for refunds — Graphite requires one approver up to P5k and two above it (see
    load_refund_to_fnb). Reusing that boundary keeps one definition of a
    material refund instead of inventing a second one.

    Honest limitation: no refund on record comes close — 37 of them, average
    BWP 484, largest ever BWP 2,166 — so today this never fires. It is a ceiling
    for the unusual refund, which is exactly when a second person is wanted.
    """
    raw = getattr(settings, 'REFUND_AUTOLOAD_MAX_BWP', None)
    if raw in (None, ''):
        return REFUND_AUTOLOAD_MAX_BWP_DEFAULT
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        # Never guess on a money control: refuse to auto-load anything.
        log.error('REFUND_AUTOLOAD_MAX_BWP is not a number (%r) — no refund '
                  'will auto-load until it is corrected', raw)
        return Decimal('0')
    return value if value >= 0 else Decimal('0')


def stage_handoff_refund(refund) -> dict:
    """On a Graphite handoff: raise the payment, mark APPROVED, load to FNB
    (preview unless armed), and notify the CFO. Idempotent-ish and best-effort:
    a config problem leaves the refund in FINANCE_QUEUE for manual handling and
    is reported, never raised."""
    from django.utils import timezone
    actor = _resolve_cfo_user()
    try:
        if refund.payment is None:
            refund.payment = create_refund_payment(refund, actor)
        if refund.status in (refund.Status.RECEIVED, refund.Status.FINANCE_QUEUE):
            refund.status = refund.Status.APPROVED
            refund.finance_approved_at = timezone.now()
        refund.save(update_fields=['payment', 'status', 'finance_approved_at', 'updated_at'])
    except RefundConfigError as e:
        return {'staged': False, 'reason': str(e)}
    except Exception as e:  # noqa: BLE001
        return {'staged': False, 'reason': str(e)[:200]}

    # BEFORE the bank load, not after (Fable, 2026-09-11). load_refund_to_fnb
    # does not catch the fnb.client exceptions — FNBNotConfigured / FNBAuthError
    # / FNBAPIError escape past the two clauses below, the ingest view's blanket
    # except eats them, and the refund ends APPROVED with a payment raised, on
    # no tab, with nobody told. Raising the request first means the worst case
    # is a refund visible and waiting rather than a refund that vanished.
    request = ensure_payment_request(refund)

    load = {}
    ceiling = refund_autoload_ceiling()
    amount = refund.refund_amount or Decimal('0')
    try:
        if amount > ceiling:
            # Over the line: no automatic load. The refund still stages and the
            # CFO is still told — it simply waits for a second person to
            # release it, per the 2026-08-20 rule.
            load = {
                'mode': 'preview',
                'error': (f'BWP {amount} is above the BWP {ceiling} limit for '
                          'loading a refund automatically. It is staged and '
                          'needs a second person to release it to the bank.'),
            }
        else:
            load = load_refund_to_fnb(refund, actor, live=True,
                                      from_handoff=True,
                                      allow_single_person=True)
    except RefundConfigError as e:
        load = {'mode': 'preview', 'error': str(e)}
    except ValidationError as e:
        # The 2026-08-20 rule — whoever creates a payment may not release it —
        # collides with this path by design: one resolved CFO user both raises
        # the refund payment and sends it, because the two pairs of eyes here
        # are Graphite's approval plus the CFO authorising in FNB's own banking
        # (CFO 2026-07-28), not two Omni users.
        #
        # So the load is REFUSED and reported, never silently dropped. Without
        # this clause the ValidationError escaped the whole function, the CFO
        # notification below never sent, and the ingest view's blanket except
        # swallowed it: a refund approved, never loaded, and nobody told. The
        # refund still stages and the CFO still gets the email, now saying it
        # needs a second person to release it.
        load = {'mode': 'preview',
                'error': f'Not loaded to the bank: {e}'}
    notify_cfo_to_authorise(refund, load, actor)
    return {'staged': True, 'load': load, 'payment_request': request}


def ensure_payment_request(refund) -> str:
    """Put the refund on the Premium refunds tab, the way the overnight pipe does.

    Keetile Mokhendo, 2026-09-11: an approved Graphite refund must land under
    Payment Request -> Refund, not Operational. The overnight importer already
    raises exactly that request, so this calls the SAME builder rather than
    inventing a second one — one refund, one shape, whichever pipe carried it.

    The UNIQUE graphite_ref is what makes running both pipes safe: whichever
    arrives first raises the request and the other finds it already there. An
    IntegrityError here is the guard WORKING, not a fault.

    Best-effort by design: the refund and its bank leg are already saved, and a
    missing Keetile login or a rendering fault must not undo them. Returns the
    request reference, or '' with the reason logged.
    """
    from django.db import IntegrityError

    from taskboard.models import PaymentRequest
    from taskboard.premium_refund_requests import (entered_by_user,
                                                   raise_premium_refund_request)

    ref_g = (refund.graphite_ref or '').strip()
    if not ref_g:
        # Without a reference nothing can be de-duplicated, and a blank one
        # passes the "not seen before" test every time — the shape that raised
        # the same request every midnight. Refuse rather than raise a twin.
        log.error('refund %s has no graphite_ref — no payment request raised, '
                  'because it could not be de-duplicated', refund.pk)
        return ''
    existing = PaymentRequest.objects.filter(graphite_ref=ref_g).first()
    if existing is not None:
        return existing.ref

    keetile = entered_by_user()
    if keetile is None:
        log.error('no Omni login for the refunds maker of record — refund %s '
                  'has no payment request on the Premium refunds tab', ref_g)
        return ''
    row = {
        'graphite_ref':  ref_g,
        'policy_number': refund.policy_number,
        'customer_name': refund.customer_name,
        'refund_amount': refund.refund_amount,
        'currency':      refund.currency,
        'reason':        refund.reason,
    }
    try:
        return raise_premium_refund_request(row, keetile, origin='direct').ref
    except IntegrityError:
        # The overnight importer got there first between the check and the
        # insert. Expected, harmless: the request exists either way.
        found = PaymentRequest.objects.filter(graphite_ref=ref_g).first()
        return found.ref if found else ''
    except Exception as e:  # noqa: BLE001 — never undo a saved refund
        log.error('could not raise the payment request for refund %s: %s',
                  ref_g, e)
        return ''
