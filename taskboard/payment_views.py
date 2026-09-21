"""taskboard/payment_views.py — payment authorisation requests (CFO 2026-07-15).

Finance stops emailing payment authorisations. Instead they fill a structured
form; this builds the exact authorisation table (deterministic — matches the
format the team already sends), writes a short covering summary with the AI,
and drops it into the CFO's task inbox as an OmniTask he can pay or reassign
(e.g. escalate above his limit to the CEO).

  GET  /api/v1/payment-requests/            list (own, or incoming for the CFO)
  POST /api/v1/payment-requests/            create -> renders table -> CFO task
  GET  /api/v1/payment-requests/<id>/       detail (+ the formatted table)

No customer PII: payees are vendors/beneficiaries and bank details are Alpha
Direct's own accounts. Account numbers are NEVER sent to the AI — only the
subject + line descriptions + amounts go into the covering-summary prompt.
"""
import logging
import re
from datetime import date as _date
from decimal import Decimal, InvalidOperation
from html import escape

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import OmniTask
from fnb.destination_bank import branch_code_advisory, branch_code_problem
from taskboard.models import (
    PaymentLoadOverride, PaymentRequest, PaymentReleaseSignoff, money_dec,
)
from taskboard.narration_templates import build_defaults, default_type_for
from taskboard.payee_bank_history import (
    bank_change_warning, bank_details_changed, first_payment_warning,
    last_known_bank, normalise_payee,
)
from taskboard.pop_recipients import (
    SOURCE_OFF_LIST as POP_SOURCE_OFF_LIST,
    accounts_default as pop_accounts_default,
    classify as pop_classify,
    is_valid_email as pop_email_valid,
    linked_recipients as pop_linked_recipients,
    off_list_lines as pop_off_list_lines,
)
from taskboard.payment_duplicates import (
    CONTROL_CODE as DUP_CONTROL_CODE,
    blocking_message,
    find_duplicates,
    hard_total,
)

log = logging.getLogger(__name__)

NAVY = '#0D1B2A'
ORANGE = '#F4A623'

#: How many requests the Payment History screen reads per call. It fans out to
#: one row PER LINE, so 400 requests is already several thousand rows — enough
#: for any month anyone asks for, and the response says plainly when it capped
#: rather than truncating in silence.
HISTORY_MAX_REQUESTS = 400

# Sub-brand aliases whose ref code is NOT a Company code of its own
# (e.g. the Instant sub-brand). The generic 'alpha direct' -> ADIC catch-all
# was REMOVED (bug lntabeni 2026-07-15): it swallowed every entity that wasn't
# already listed and stamped it ADIC, so an "Risk Software Africa" request came
# out PAY/ADIC/... . Real entities are now resolved to their Company code
# below; ADIC remains only the last-resort default.
_ENTITY_CODES = [
    ('alpha direct sa', 'SA'), ('south africa', 'SA'), ('adrisk', 'ADR'),
    ('unicoin', 'UNI'), ('instant', 'ADII'),
]


def _is_cfo(user) -> bool:
    cfo = _cfo_user()
    return bool(user and (user.is_superuser or (cfo and user.id == cfo.id)))


def _truthy(v) -> bool:
    """A real JSON true, or an explicit affirmative string — never bool('false')."""
    return v is True or (isinstance(v, str) and v.strip().lower() in {'true', 'yes', '1', 'on'})


# Petty cash is exempt from bank details only up to a float-sized amount. Above
# it the request is an ordinary payment and every bank control applies (CFO
# 2026-09-02; Fable 5.1 audit M4 — "petty cash" was a label that skipped every
# bank check with no cap).
PETTY_CASH_NO_BANK_MAX_BWP = Decimal('5000.00')


# ── Payment loading window — ABOLISHED 2026-09-02 (was PAY-WIN-02) ──────────
# The 08:00-09:15 morning window that limited when a payment could be RAISED is
# gone: payments can be raised at ANY time and nothing blocks an off-window raise.
# Instead, a non-CFO who raises off-window is flagged (PaymentRequest.loaded_off_
# window) and it surfaces as a "did not plan the load in time" concern in their
# monthly performance feedback. _load_window_times / _load_window_is_open survive
# only so the create leg can tell whether a raise was off-window (for that flag).
# Sign-off was never windowed. Nothing here moves money.
import datetime as _win_dt


def _load_window_times():
    """(open, close) as datetime.time, from settings, with safe fallbacks."""
    from django.conf import settings

    def _parse(raw, fallback):
        try:
            h, m = str(raw).strip().split(":")
            return _win_dt.time(int(h), int(m))
        except Exception:
            return fallback

    open_t = _parse(getattr(settings, "PAYMENT_LOAD_WINDOW_OPEN", "08:00"),
                    _win_dt.time(8, 0))
    close_t = _parse(getattr(settings, "PAYMENT_LOAD_WINDOW_CLOSE", "09:15"),
                     _win_dt.time(9, 15))
    return open_t, close_t


def _load_window_is_open(now_local=None) -> bool:
    if now_local is None:
        now_local = timezone.localtime(timezone.now())
    open_t, close_t = _load_window_times()
    return open_t <= now_local.time() <= close_t


def _has_approved_load_override(user, on_date) -> bool:
    # Retained only so the window-status payload can report an honest value for a
    # stale pre-abolition override. New overrides are no longer created (the
    # request route returns 400 — the window is abolished 2026-09-02).
    return PaymentLoadOverride.objects.filter(
        requested_by=user, for_date=on_date,
        status=PaymentLoadOverride.Status.APPROVED,
    ).exists()


# _load_blocked_response removed 2026-09-02: the PAY-WIN-02 window is abolished,
# so nothing blocks an off-window raise. The PaymentLoadOverride model + endpoint
# stay only for the audit trail of the window that WAS.


def _cfo_user():
    """The person payment requests are sent to for payment — the CFO. Falls
    back to any superuser so the feature still works in a fresh environment."""
    u = User.objects.filter(username__iexact='pganesharajah', is_active=True).first()
    return u or User.objects.filter(is_superuser=True, is_active=True).order_by('id').first()


# Stage-1 finance sign-off (CFO 2026-07-23): a payment request must be approved
# by ONE of these finance approvers BEFORE it reaches the CFO's view. The CFO
# only ever sees a request once it is PENDING_CFO. Overridable via settings.
_DEFAULT_FIRST_APPROVER_EMAILS = [
    'pkago@alphadirect.co.bw',        # Pako Kago — Financial Controller
    'ktshutlhedi@alphadirect.co.bw',  # Kago Tshutlhedi
    'lntabeni@alphadirect.co.bw',     # Legakwa Ntabeni — Finance Manager
]

# ── Payment exception committee (CFO 2026-09-02) ─────────────────────────────
# A fraud-risk exception (a changed bank account; later, anything deemed
# possible fraud) is decided by a COMMITTEE: THREE distinct members of this
# SIX-person pool, never the raiser and never the CFO. Pinned to NAMED ACCOUNTS,
# NEVER job titles — Omni titles are unreliable (Oprah shows finance_manager but
# is Internal Audit; Pako shows financial_controller). Keetile and Tlamelo are
# reserves who fill any empty seat when a primary is on leave, so a leave day
# never stalls a payment ("so we are safer"). With six in the pool, any three
# always include someone beyond the Pako+Kago pair, so no separate independence
# gate is needed. Legakwa and Oprah are flagged as the independent side for the
# record. Overridable via settings.PAYMENT_COMMITTEE_EMAILS.
_DEFAULT_COMMITTEE_EMAILS = [
    'pkago@alphadirect.co.bw',        # Pako Kago
    'ktshutlhedi@alphadirect.co.bw',  # Kago Tshutlhedi
    'lntabeni@alphadirect.co.bw',     # Legakwa Ntabeni — independent (group)
    'omogomotsi@alphadirect.co.bw',   # Oprah Mogomotsi — independent (Internal Audit)
    'kmokhendo@alphadirect.co.bw',    # Keetile Mokhendo — reserve
    'tchimidza@alphadirect.co.bw',    # Tlamelo Chimidza — reserve
]
_INDEPENDENT_COMMITTEE_EMAILS = {
    'lntabeni@alphadirect.co.bw', 'omogomotsi@alphadirect.co.bw',
}
COMMITTEE_SIGNOFFS_REQUIRED = 3


def _committee_emails() -> set[str]:
    from django.conf import settings
    raw = getattr(settings, 'PAYMENT_COMMITTEE_EMAILS', _DEFAULT_COMMITTEE_EMAILS)
    return {(e or '').strip().lower() for e in raw if e}


def _is_committee_member(user) -> bool:
    return (getattr(user, 'email', '') or '').strip().lower() in _committee_emails()


def _is_independent_committee(user) -> bool:
    return (getattr(user, 'email', '') or '').strip().lower() in _INDEPENDENT_COMMITTEE_EMAILS


def _first_approver_emails() -> set[str]:
    from django.conf import settings
    raw = getattr(settings, 'PAYMENT_FIRST_APPROVER_EMAILS', _DEFAULT_FIRST_APPROVER_EMAILS)
    return {(e or '').strip().lower() for e in raw if e}


# Who may record that a payment was REJECTED inside the FNB app (CFO decision
# 2026-09-11, on Leano Makwapa's report via Unopa Male). FNB tells Omni nothing
# when a human declines an authorisation in the app, so the batch sits on
# 'submitted' for ever and Omni keeps saying "Waiting for your authorisation in
# the FNB app" long after the bank has said no.
#
# It goes to the accounts team who load the payments and live with the wrong
# records — not to one controller — because they are the people in the FNB app
# every day and can fix it the same hour. The usual maker-checker worry does not
# apply: this does NOT move money and does NOT close anything as paid. It is the
# opposite of a close — it re-opens the payment as "Rejected by FNB — fix the
# bank details and reload", which is work put BACK on the team, and the reload
# still goes through the normal load and dual authorisation untouched.
#
# Settings-overridable so a joiner or leaver needs no code change.
_DEFAULT_FNB_REJECT_EMAILS = [
    'kkgetse@alphadirect.co.bw',    # Koketso Kgetse
    'lmakwapa@alphadirect.co.bw',   # Leano Makwapa (who reported it)
    'btendani@alphadirect.co.bw',   # Bontle Tendani
]


def _fnb_reject_emails() -> set[str]:
    from django.conf import settings
    raw = getattr(settings, 'PAYMENT_FNB_REJECT_EMAILS', _DEFAULT_FNB_REJECT_EMAILS)
    return {(e or '').strip().lower() for e in raw if e}


def _may_mark_fnb_rejected(user) -> bool:
    """The accounts team, plus the finance approvers and the CFO."""
    if getattr(user, 'is_superuser', False):
        return True
    email = (getattr(user, 'email', '') or '').strip().lower()
    return email in _fnb_reject_emails() or _is_first_approver(user)


def _first_approvers():
    """Active finance approvers (Pako / Kago / Legakwa)."""
    from django.db.models import Q
    emails = _first_approver_emails()
    if not emails:
        return User.objects.none()
    q = Q()
    for e in emails:
        q |= Q(email__iexact=e)
    return User.objects.filter(q, is_active=True)


def _is_readonly_key_reader(request) -> bool:
    """True when this request came in on a READ-ONLY API key (the QC reader).

    Manus, 2026-08-09: the QC key saw an empty payment register and the response
    said `submission_window.is_open: false`, so the empty list read like a closed
    window. It was neither — the key belongs to no approver, so it fell through to
    "requests I raised myself", which for a service account is always none.

    *"A QC reader needs all requests across all entities regardless of window state
    and regardless of whose approval is pending."* It reads; it can never approve,
    submit or pay — `_enforce_read_only_key` refuses every non-GET before the view
    is reached.
    """
    from core.models import ApiKey
    auth = getattr(request, 'auth', None)
    if not isinstance(auth, ApiKey):
        return False
    scopes = set(auth.allowed_scopes or ())
    from core.api_key_auth import READ_ONLY_SCOPES
    return bool(scopes) and scopes.issubset(READ_ONLY_SCOPES)


def _is_first_approver(user) -> bool:
    return (getattr(user, 'email', '') or '').strip().lower() in _first_approver_emails()


def _entity_code(entity: str) -> str:
    e = (entity or '').lower()
    for needle, code in _ENTITY_CODES:
        if needle in e:
            return code
    # Not a known sub-brand alias — resolve the typed entity to the real
    # Company code (by code, then exact name, then a contains match) instead of
    # silently defaulting to ADIC (bug lntabeni 2026-07-15: an RSA payment
    # request was stamped PAY/ADIC/...). ADIC only if nothing matches at all.
    ent = (entity or '').strip()
    if ent:
        from core.models import Company
        c = (Company.objects.filter(code__iexact=ent).first()
             or Company.objects.filter(name__iexact=ent).first()
             or Company.objects.filter(name__icontains=ent).first())
        if c:
            return c.code
    return 'ADIC'


def _resolves_to_adic(entity: str) -> bool:
    """Does this entity POSITIVELY resolve to ADIC? Used by the claims-are-ADIC
    control, and deliberately NOT built on _entity_code().

    _entity_code() ends in `return 'ADIC'` as a catch-all so a payment reference
    always gets a code. That default is fine for naming a reference and fatal for
    a control: a claims pack posted with a misspelled or unknown entity would
    resolve to 'ADIC' and so SATISFY the very check meant to stop it (Fable
    review 2026-07-29). A control must never be satisfied by a fallback, so this
    requires an affirmative match and returns False when the entity is unknown.
    """
    ent = (entity or '').strip()
    if not ent:
        return False
    # A known sub-brand alias resolves to some other entity — never ADIC.
    low = ent.lower()
    for needle, code in _ENTITY_CODES:
        if needle in low:
            return code == PaymentRequest.CLAIMS_ONLY_ENTITY_CODE
    # EXACT match only. _entity_code() also accepts a `name__icontains` match,
    # which is right for naming a reference and wrong for a control: a bare
    # fragment ('Alpha', or even 'an') would match ADIC's row and pass, and with
    # Company ordered by -created_at the winner of a multi-row match depends on
    # insertion order. The entity always arrives as an exact name from the
    # company drop-down, so nothing legitimate needs the substring leg.
    from core.models import Company
    c = (Company.objects.filter(code__iexact=ent).first()
         or Company.objects.filter(name__iexact=ent).first())
    return bool(c and c.code == PaymentRequest.CLAIMS_ONLY_ENTITY_CODE)


#: What an unrecognised or never-set category reads as everywhere it is
#: shown. Found on 20 historical requests (all raised 2026-07-15 to 2026-07-29,
#: before category became mandatory on this endpoint) — the authorisation pack's
#: CATEGORY row used to be built from `if _category_label(...): ...` and so
#: DROPPED ITSELF ENTIRELY on those requests rather than showing blank; the
#: register's Category column read as empty with nothing to flag it. A blank
#: nobody can see is the failure mode (CFO 2026-09-14) — this makes every one
#: of those call sites show a warning instead of silently doing nothing.
UNCATEGORISED_LABEL = '⚠ Uncategorised'


def _category_label(cat: str) -> str:
    return dict(PaymentRequest.Category.choices).get(
        (cat or '').strip(), '') or UNCATEGORISED_LABEL


def _replay_response(pr: 'PaymentRequest') -> dict:
    """The same shape create() returns, for a retried submit that is being
    handed back its earlier result rather than raising a second request."""
    task = pr.task
    return {
        'id': str(pr.id), 'ref': pr.ref,
        'task_id': str(pr.task_id) if pr.task_id else None,
        'status': pr.status,
        'assigned_to': ((task.assignee.get_full_name() or task.assignee.username)
                        if task and task.assignee_id else ''),
        'replayed': True,
    }


# There is deliberately NO category inference (removed 2026-07-29).
#
# It used to guess from words in the subject and line descriptions, checking
# 'claim' before 'supplier'. That is how the control was defeated: a pack titled
# "Claims payable batch" for a panel beater auto-classified as a CLAIM and so
# never met the supplier terms gate, while the same words could push a genuine
# supplier pack either way. Guessing also cannot answer the question that
# actually matters on a claim — whether the money goes to the policyholder or to
# a repairer who has invoiced us. Nothing in a description reveals that.
#
# So the raiser must SAY (CFO 2026-07-29: "it should ask whether its claims or
# operations payment so we dont confuse it"). A request with no category is
# refused rather than silently filed under a guess, which also closes the
# obvious bypass: omitting the field can no longer skip the gate.


def _next_ref(entity: str) -> str:
    """PAY/<CODE>/YYYY/MM/DD/NNNN — NNNN is today's running count for that code."""
    today = timezone.localdate()
    code = _entity_code(entity)
    prefix = f'PAY/{code}/{today:%Y/%m/%d}/'
    # Highest sequence so far + 1 — NEVER a row count. Drop Box drafts share
    # this sequence and are deleted on submit/discard, so a count left a gap
    # and every later request that day recomputed a number that already
    # existed: six IntegrityError retries, then "Could not allocate a unique
    # reference — please retry", for the rest of the day (Fable 5.1 audit
    # 2026-09-02, C1). Refs are zero-padded to four digits, so the largest
    # string is the largest number.
    last = (PaymentRequest.objects.filter(ref__startswith=prefix)
            .order_by('-ref').values_list('ref', flat=True).first())
    n = 1
    if last:
        try:
            n = int(last.rsplit('/', 1)[-1]) + 1
        except ValueError:
            n = PaymentRequest.objects.filter(ref__startswith=prefix).count() + 1
    return f'{prefix}{n:04d}'


def _dec(v) -> Decimal:
    # HALF UP — the house rule for money (CFO 2026-08-09). Decimal's default is
    # banker's rounding, which silently turned 10.005 into 10.00 (Fable 5.1
    # audit 2026-09-02, M10).
    #
    # Delegates to taskboard.models.money_dec so there is exactly ONE money
    # parser behind both the total and the reconciliation guard that checks it.
    # Two parsers is how the P90k cap was bypassed.
    return money_dec(v)


def _money(cur: str, amount: Decimal) -> str:
    return f'{cur} {amount:,.2f}'


def _no_marker(s) -> str:
    """Strip brackets and newlines from raiser-supplied text before it is
    embedded in exception_reason, so it cannot forge a line-anchored control
    marker like '[PAY-DUP-01]' that the sign-off re-check keys on (Fable 5.1
    audit 2026-09-09)."""
    import re as _re
    return _re.sub(r'[\[\]\r\n]+', ' ', str(s or '')).strip()


def _clean_lines(raw):
    """Normalise the posted line items -> [{description, gl_code, ref, amount}]
    plus the supplier-terms fields (invoice_number / invoice_date / terms_days /
    due_date / discount_checked / discount_pct). The terms fields are carried
    for EVERY category but only ENFORCED where _terms_gate_applies() says so —
    see _validate_supplier_terms (CFO control PAY-SUP-01, 2026-07-28; widened to
    claims-payable suppliers 2026-07-29)."""
    out = []
    for r in (raw or []):
        if not isinstance(r, dict):
            continue
        # Collapse ALL whitespace (newlines/tabs -> single spaces): a description
        # is one line, and this stops raiser text from injecting a line-anchored
        # control marker like "\n[PAY-DUP-01] ..." into exception_reason, which
        # the sign-off re-check keys on (Fable 5.1 audit 2026-09-09).
        desc = ' '.join(str(r.get('description') or '').split())
        amt = _dec(r.get('amount'))
        if not desc and amt == 0:
            continue
        out.append({
            'description':      desc[:200],
            'gl_code':          str(r.get('gl_code') or '').strip()[:40],
            'ref':              str(r.get('ref') or '').strip()[:60],
            'amount':           amt,
            'invoice_number':   str(r.get('invoice_number') or '').strip()[:60],
            'invoice_date':     str(r.get('invoice_date') or '').strip()[:10],
            'terms_basis':      str(r.get('terms_basis') or '').strip()[:10],
            'terms_days':       str(r.get('terms_days') or '').strip()[:4],
            'due_date':         str(r.get('due_date') or '').strip()[:10],
            'discount_checked': bool(r.get('discount_checked')),
            'discount_pct':     str(r.get('discount_pct') or '').strip()[:8],
            # Which claim this line settles. Required on every line of a CLAIM
            # pack (CFO 2026-07-29), whoever is paid — the Graphite claim number
            # is how a claims payment is tied back to the claim it belongs to.
            'claim_number':     str(r.get('claim_number') or '').strip()[:60],
            # ── Who gets the proof of payment for THIS line (Finance spec
            # 2026-09-08). Per line, not per request: one claim can pay a
            # panel beater, a parts supplier and the claimant, and the proof
            # belongs to whoever was paid. Carried in the line JSON — the
            # lines already hold everything else about a payment.
            # pop_recipient_source is stamped by the create view from the
            # deterministic lookup, never taken from the browser: a request
            # that could label itself "linked" would walk straight past the
            # off-list gate.
            'pop_recipient_name':  str(r.get('pop_recipient_name') or '').strip()[:200],
            'pop_recipient_email': str(r.get('pop_recipient_email') or '').strip().lower()[:254],
            # Which earlier pack this line was copied off, when the raiser used
            # "reload previous payments" (Leano Makwapa feature request
            # 2026-09-15). A provenance stamp, never a permission: the guards
            # below ADD a clash when it is present and never skip one when it
            # is absent, so a browser that omits it gains nothing.
            # 48 to match PaymentRequest.ref's own max_length — a shorter cap
            # would silently truncate a stamp into a lookup that finds nothing.
            'copied_from_ref':  str(r.get('copied_from_ref') or '').strip()[:48],
        })
    return out


# ── Supplier payment terms control (CFO directive 2026-07-28) ────────────────
# A supplier invoice was settled in the month it was raised, at full value,
# while the supplier book was unpaid and an offshore settlement discount was on
# the table (Korean Auto, BWP 211,130.30). It passed because the authorisation
# pack carried a claim reference and an amount and NOTHING ELSE — no invoice
# number, no invoice date, no due date. Nobody could see it wasn't due.
#
# From now on a gated payment request cannot be created at all unless every
# line carries the invoice number, the invoice date, the agreed credit term and
# the resulting due date; the due date must respect the term (you cannot type a
# shorter one); the pay date must not be before the due date; and the raiser
# must attest that the early-settlement discount was checked.
#
# SCOPE — widened 2026-07-29 (CFO: "it's actually supplier payments for claims").
# The first cut gated category=supplier ONLY, and the payments the control
# existed for do not use that category: a panel beater or parts supplier billing
# us for a repair is raised as a CLAIM payment and posted to claims payable
# (e.g. Carfil Services, five invoices, BWP 50,689.84 — raised with no invoice
# date and no due date, the exact Korean Auto shape). Vendor packs escaped for
# the same reason. A supplier is a supplier whichever ledger the cost lands in.
#
# So the raiser is now asked, FIRST, whether the payment is claims or operations
# (CFO 2026-07-29: "it should ask whether its claims or operations payment so we
# don't confuse it"), and on a claims payment, who is being paid:
#   provider — a repairer / parts supplier / hospital billing us on the claim.
#              There is a real invoice, so this is gated like any supplier.
#   client   — settled straight to the policyholder. No third-party invoice
#              exists to date or age, so the gate does not apply.
# The answer is stored on the record (claim_payee_type) and printed on the pack,
# so an approver can see which branch was taken and challenge it.
#
# Still NOT gated: petty cash and recurring operational payments (rent, rates,
# utilities, statutory) — categories 'petty_cash' / 'other' keep the old flow.
DEFAULT_SUPPLIER_TERMS_DAYS = 30

# Operations payments to a third party who invoices us. Always gated.
TERMS_GATED_CATEGORIES = frozenset({
    PaymentRequest.Category.SUPPLIER,
    PaymentRequest.Category.VENDOR,
})


def _terms_gate_applies(category: str, claim_payee_type: str) -> bool:
    """Does the invoice/due-date gate bite on this pack?

    Supplier and vendor packs: always. Claim packs: only when a provider is
    being paid — a settlement to the policyholder has no invoice to age."""
    if category in TERMS_GATED_CATEGORIES:
        return True
    return (category == PaymentRequest.Category.CLAIM
            and claim_payee_type == PaymentRequest.ClaimPayeeType.PROVIDER)

# Trade terms run from the STATEMENT, not the invoice — the month closes and the
# credit clock starts at month-end. A 24-Jul invoice on 30-day statement terms
# therefore falls due 30 Aug (31 Jul statement + 30 days), not 23 Aug.
# Getting this wrong is not academic: invoice-basis would have released the
# Korean Auto payment a week early. 'invoice' remains available for the
# suppliers who genuinely bill per-invoice terms.
TERMS_BASIS_STATEMENT = 'statement'
TERMS_BASIS_INVOICE   = 'invoice'
DEFAULT_TERMS_BASIS   = TERMS_BASIS_STATEMENT


def _parse_iso(value):
    from datetime import date as _date
    try:
        return _date.fromisoformat((value or '').strip())
    except (ValueError, AttributeError):
        return None


def _statement_date(invoice_date):
    """Month-end of the month the invoice falls in — the statement it lands on."""
    import calendar
    last = calendar.monthrange(invoice_date.year, invoice_date.month)[1]
    return invoice_date.replace(day=last)


def _terms_anchor(invoice_date, basis):
    """The date the credit clock starts: the statement (default) or the invoice."""
    if (basis or DEFAULT_TERMS_BASIS) == TERMS_BASIS_INVOICE:
        return invoice_date
    return _statement_date(invoice_date)


def _validate_supplier_terms(lines, *, pay_date, override_reason=''):
    """Enforce the supplier terms gate. Returns (error_message, meta).

    error_message is None when the pack may be raised. `meta` carries the
    derived facts the authorisation pack must display (the date the pack becomes
    payable, how many days early the pay date is, whether an override was
    invoked).

    The binding date is the LATEST due date on the request, not the earliest:
    approving the pack pays every line on it, so one not-yet-due invoice riding
    alongside a due one would otherwise be settled early unnoticed — which is
    exactly how the Korean Auto invoice went out."""
    today = timezone.localdate()
    binding_due = None
    for i, ln in enumerate(lines, 1):
        where = f'Line {i} ({ln["description"] or "no description"})'

        if not ln['invoice_number']:
            return f'{where}: supplier invoice number is required.', {}

        inv = _parse_iso(ln['invoice_date'])
        if inv is None:
            return f'{where}: supplier invoice date is required (YYYY-MM-DD).', {}
        if inv > today:
            return f'{where}: invoice date {inv} is in the future.', {}

        try:
            terms = int(ln['terms_days'] or DEFAULT_SUPPLIER_TERMS_DAYS)
        except ValueError:
            return f'{where}: credit terms must be a whole number of days.', {}
        if terms < 0:
            return f'{where}: credit terms cannot be negative.', {}
        ln['terms_days'] = str(terms)

        basis = ln['terms_basis'] or DEFAULT_TERMS_BASIS
        if basis not in (TERMS_BASIS_STATEMENT, TERMS_BASIS_INVOICE):
            return (f'{where}: terms basis must be "{TERMS_BASIS_STATEMENT}" or '
                    f'"{TERMS_BASIS_INVOICE}".'), {}
        ln['terms_basis'] = basis

        due = _parse_iso(ln['due_date'])
        if due is None:
            return f'{where}: due date is required (YYYY-MM-DD).', {}

        # The due date is not a free field — it must respect the agreed term,
        # counted from the statement the invoice lands on. This is what stops
        # "due today" being typed onto a fresh invoice.
        import datetime as _dt
        anchor = _terms_anchor(inv, basis)
        earliest_allowed = anchor + _dt.timedelta(days=terms)
        if due < earliest_allowed:
            anchor_word = ('statement' if basis == TERMS_BASIS_STATEMENT else 'invoice')
            return (f'{where}: due date {due} is earlier than the agreed terms. '
                    f'Invoice {inv} sits on the {anchor} {anchor_word}; on '
                    f'{terms}-day terms it falls due {earliest_allowed}.'), {}

        if not ln['discount_checked']:
            return (f'{where}: confirm the early-settlement / offshore discount '
                    f'was checked before requesting payment.'), {}

        if binding_due is None or due > binding_due:
            binding_due = due

    days_early = (binding_due - pay_date).days if binding_due else 0
    if days_early > 0 and not override_reason:
        not_due = [f'line {i} due {ln["due_date"]}'
                   for i, ln in enumerate(lines, 1)
                   if (_parse_iso(ln['due_date']) or pay_date) > pay_date]
        return (f'Payment is {days_early} day(s) early — {", ".join(not_due)} '
                f'is not yet due. This request becomes payable on {binding_due}. '
                f'Move the payment date to {binding_due} or later, split the '
                f'not-yet-due lines onto a separate request, or state the '
                f'CFO-approved reason for settling early.'), {}

    return None, {
        'binding_due_date': binding_due.isoformat() if binding_due else '',
        'days_early':       max(days_early, 0),
        'override_reason':  override_reason,
    }


def _render_terms_block_html(pr: dict) -> str:
    """Supplier packs get an explicit terms-compliance block: the pay date, the
    earliest due date on the request, whether it is early, and whether cash was
    moved before authority was given. The approver should not have to work any
    of this out (CFO 2026-07-28)."""
    pay = pr.get('payment_date') or timezone.localdate()
    # Derive the verdict here from the payment date and the line due dates —
    # never trust a days_early handed in. A pack that says "on time" while the
    # payment date sits before the due date is worse than no control at all.
    meta = _terms_meta_from_lines(pr.get('line_items') or [], pay)
    # Escape the value, THEN fall back to the dash — escaping the dash itself
    # turned it into "&amp;mdash;", which is what the approver actually read on
    # the pack. The other columns in this pack already do it in this order.
    binding = escape(str(meta.get('binding_due_date') or '')) or '&mdash;'
    days_early = int(meta.get('days_early') or 0)
    reason = (pr.get('early_payment_reason') or '').strip()
    moved = bool(pr.get('funds_already_moved'))
    # A pack can now reach this render with dates missing (it went to the
    # committee as a PAY-SUP-01 exception rather than being refused, CFO
    # 2026-09-09). Never print a green "on or after due date" over missing dates —
    # that told the approver the opposite of what the committee was shown
    # (Fable 5.1 audit 2026-09-09).
    _lines = pr.get('line_items') or []
    dates_missing = any(
        not _parse_iso(ln.get('invoice_date') or '') or not _parse_iso(ln.get('due_date') or '')
        for ln in _lines)
    discount_all = bool(_lines) and all(ln.get('discount_checked') for ln in _lines)

    if dates_missing:
        timing = ('<span style="color:#991B1B;font-weight:bold;">&#10007; TERMS NOT '
                  'ESTABLISHED — dates missing; committee decision on record (PAY-SUP-01)</span>')
    elif days_early > 0:
        timing = (f'<span style="color:#991B1B;font-weight:bold;">&#10007; '
                  f'{days_early} DAY(S) EARLY</span>')
    else:
        timing = ('<span style="color:#065F46;font-weight:bold;">&#10003; '
                  'ON OR AFTER DUE DATE</span>')
    rows = [
        ('Payment date (money leaves)', escape(str(pay))),
        ('This request becomes payable on', f'<strong>{binding}</strong>'),
        ('Terms compliance', timing),
        ('Early-settlement discount',
         'Checked on every line by the raiser' if discount_all
         else '<span style="color:#991B1B;">Not confirmed on every line — committee to check</span>'),
    ]
    if reason:
        rows.append(('Reason for early settlement',
                     f'<span style="color:#991B1B;">{escape(reason)}</span>'))
    if moved:
        rows.append(('Funds already transferred to fund this request',
                     '<span style="color:#991B1B;font-weight:bold;">&#10007; YES — '
                     'cash moved BEFORE authorisation</span>'))
    body = ''.join(
        f'<tr><td style="padding:6px 8px;border:1px solid #E5E7EB;width:55%;">{label}</td>'
        f'<td style="padding:6px 8px;border:1px solid #E5E7EB;">{value}</td></tr>'
        for label, value in rows)
    return (f'<h3 style="color:{NAVY};font-size:13px;margin:18px 0 6px;">'
            f'PAYMENT TERMS COMPLIANCE (PAY-SUP-01)</h3>'
            f'<table style="border-collapse:collapse;width:100%;font-size:13px;">{body}</table>')


def _render_html(pr: dict) -> str:
    """The exact authorisation table (house HTML). `pr` is a plain dict of the
    saved fields. Mirrors the formal template Finance already emails."""
    cur = pr['currency']
    total = pr['total']
    lines = pr['line_items']
    # Supplier packs carry two extra columns so the approver can see, without
    # opening anything else, whether the invoice is actually due (CFO 2026-07-28).
    show_terms = _terms_gate_applies(pr.get('category') or '',
                                     pr.get('claim_payee_type') or '')
    # A claims pack shows the claim each line settles (CFO 2026-07-29).
    is_claim = (pr.get('category') or '') == PaymentRequest.Category.CLAIM
    claim_head = ('<th style="padding:6px 8px;border:1px solid #E5E7EB;text-align:left;">Claim #</th>'
                  if is_claim else '')
    extra_head = ('<th style="padding:6px 8px;border:1px solid #E5E7EB;text-align:left;">Invoice #</th>'
                  '<th style="padding:6px 8px;border:1px solid #E5E7EB;text-align:left;">Invoice date</th>'
                  '<th style="padding:6px 8px;border:1px solid #E5E7EB;text-align:left;">Terms from</th>'
                  '<th style="padding:6px 8px;border:1px solid #E5E7EB;text-align:left;">Due date</th>'
                  if show_terms else '')
    total_colspan = 4 + (1 if is_claim else 0) + (4 if show_terms else 0)
    rows = ''
    for i, ln in enumerate(lines, 1):
        claim_cell = ''
        if is_claim:
            claim_cell = (f'<td style="padding:6px 8px;border:1px solid #E5E7EB;">'
                          f'{escape(ln.get("claim_number") or "") or "&mdash;"}</td>')
        extra = ''
        if show_terms:
            terms = ln.get('terms_days') or str(DEFAULT_SUPPLIER_TERMS_DAYS)
            basis = ln.get('terms_basis') or DEFAULT_TERMS_BASIS
            inv_date = _parse_iso(ln.get('invoice_date'))
            anchor = _terms_anchor(inv_date, basis) if inv_date else None
            # Show the anchor the clock actually runs from, so the approver can
            # check the arithmetic without asking anyone.
            anchor_cell = (f'{escape(str(anchor))} {escape(basis)}<br>'
                           f'<span style="color:#6B7280;">+ {escape(terms)} days</span>'
                           if anchor else '&mdash;')
            extra = (
                f'<td style="padding:6px 8px;border:1px solid #E5E7EB;">{escape(ln.get("invoice_number") or "") or "&mdash;"}</td>'
                f'<td style="padding:6px 8px;border:1px solid #E5E7EB;">{escape(ln.get("invoice_date") or "") or "&mdash;"}</td>'
                f'<td style="padding:6px 8px;border:1px solid #E5E7EB;font-size:12px;">{anchor_cell}</td>'
                f'<td style="padding:6px 8px;border:1px solid #E5E7EB;"><strong>{escape(ln.get("due_date") or "") or "&mdash;"}</strong></td>'
            )
        rows += (
            f'<tr>'
            f'<td style="padding:6px 8px;border:1px solid #E5E7EB;">{i}</td>'
            f'<td style="padding:6px 8px;border:1px solid #E5E7EB;">{escape(ln["description"])}</td>'
            f'<td style="padding:6px 8px;border:1px solid #E5E7EB;">{escape(ln["gl_code"]) or "&mdash;"}</td>'
            f'<td style="padding:6px 8px;border:1px solid #E5E7EB;">{escape(ln["ref"]) or "&mdash;"}</td>'
            f'{claim_cell}'
            f'{extra}'
            f'<td style="padding:6px 8px;border:1px solid #E5E7EB;text-align:right;">{ln["amount"]:,.2f}</td>'
            f'</tr>'
        )
    # Processing method (Kelvin Kimani spec 2026-09-08) — read from the SAME
    # helper the saved request uses, so the pack and the record cannot disagree.
    _pm_word, _pm_count, _pm_label = PaymentRequest.processing_summary(
        lines, pr.get('processing_method') or PaymentRequest.ProcessingMethod.BULK)
    opening = pr.get('opening_balance')
    bank_block = ''
    if opening is not None:
        closing = opening - total
        ok = closing >= 0
        bank_block = f'''
        <h3 style="color:{NAVY};font-size:13px;margin:18px 0 6px;">SECTION B: BANK ACCOUNT POSITION &amp; LIQUIDITY</h3>
        <table style="border-collapse:collapse;width:100%;font-size:13px;">
          <tr><td style="padding:6px 8px;border:1px solid #E5E7EB;width:55%;">Account</td><td style="padding:6px 8px;border:1px solid #E5E7EB;">{escape(pr["account_name"])} {escape(pr["bank_name"])} {escape(pr["account_number"])}</td></tr>
          <tr><td style="padding:6px 8px;border:1px solid #E5E7EB;">Opening balance</td><td style="padding:6px 8px;border:1px solid #E5E7EB;text-align:right;">{_money(cur, opening)}</td></tr>
          <tr><td style="padding:6px 8px;border:1px solid #E5E7EB;">Less: total payments requested</td><td style="padding:6px 8px;border:1px solid #E5E7EB;text-align:right;">({total:,.2f})</td></tr>
          <tr><td style="padding:6px 8px;border:1px solid #E5E7EB;"><strong>Projected closing balance</strong></td><td style="padding:6px 8px;border:1px solid #E5E7EB;text-align:right;"><strong>{_money(cur, closing)}</strong></td></tr>
          <tr><td style="padding:6px 8px;border:1px solid #E5E7EB;">Liquidity status</td><td style="padding:6px 8px;border:1px solid #E5E7EB;color:{'#065F46' if ok else '#991B1B'};font-weight:bold;">{'&#10003; SUFFICIENT' if ok else '&#10007; INSUFFICIENT'}</td></tr>
          <tr><td style="padding:6px 8px;border:1px solid #E5E7EB;">Transactions to key</td><td style="padding:6px 8px;border:1px solid #E5E7EB;"><strong>{_pm_count}</strong> &mdash; processing method {escape(_pm_word)}</td></tr>
        </table>'''
    # Finance, 2026-08-20: "the final narration must display on the approval
    # screen … The approver then releases exactly what the bank will receive."
    # The drawer with the Approve button renders THIS html, so the block belongs
    # here as well as in the plaintext body — a promise shown on one screen and
    # not the other is worse than not showing it.
    _narr = (pr.get('bank_narration') or '').strip()
    _oref = (pr.get('bank_our_reference') or '').strip()
    _floor = '(nothing chosen — the bank will be given the payment number)'
    wording_block = ''
    if _narr or _oref:
        _ptype = (pr.get('bank_payment_type') or '').strip()
        _ptype_row = (f'<tr><td style="padding:6px 8px;border:1px solid #E5E7EB;">'
                      f'Payment type</td><td style="padding:6px 8px;border:1px '
                      f'solid #E5E7EB;">{escape(_ptype)}</td></tr>'
                      if _ptype else '')
        wording_block = f'''
        <h3 style="color:{NAVY};font-size:13px;margin:18px 0 6px;">WHAT THE BANK WILL BE TOLD</h3>
        <table style="border-collapse:collapse;width:100%;font-size:13px;">
          <tr><td style="padding:6px 8px;border:1px solid #E5E7EB;width:55%;">They will see</td><td style="padding:6px 8px;border:1px solid #E5E7EB;">{escape(_narr) or _floor}</td></tr>
          <tr><td style="padding:6px 8px;border:1px solid #E5E7EB;">Our reference</td><td style="padding:6px 8px;border:1px solid #E5E7EB;">{escape(_oref) or _floor}</td></tr>
          {_ptype_row}
        </table>'''

    due = pr.get('due_date') or ''
    cat_label = _category_label(pr.get('category', ''))
    cat_row = (f'<tr><td style="padding:4px 8px;color:#6B7280;">CATEGORY</td>'
               f'<td style="padding:4px 8px;"><strong>{escape(cat_label)}</strong></td></tr>'
               if cat_label else '')
    # On a claims pack, say plainly who is being paid — the approver can then see
    # at a glance whether the terms gate should have applied (CFO 2026-07-29).
    payee_row = ''
    if pr.get('category') == PaymentRequest.Category.CLAIM:
        pt = pr.get('claim_payee_type') or ''
        pt_label = dict(PaymentRequest.ClaimPayeeType.choices).get(pt, 'NOT STATED')
        colour = ('#111' if pt == PaymentRequest.ClaimPayeeType.CLIENT
                  else '#991B1B' if not pt else '#111')
        payee_row = (f'<tr><td style="padding:4px 8px;color:#6B7280;">PAID TO</td>'
                     f'<td style="padding:4px 8px;color:{colour};">'
                     f'<strong>{escape(pt_label)}</strong></td></tr>')
    terms_block = _render_terms_block_html(pr) if show_terms else ''
    # A self-named verifier is now admitted to the committee (CFO 2026-09-09), so
    # the pack must NOT print a green "verified by" tick over it — that asserts an
    # independent check that never happened (Fable 5.1 audit 2026-09-09). Show a
    # red note when the verifier is the preparer; the tick only stands for a real
    # second name.
    _vf = _no_marker(pr.get('verifier') or '')
    _inp = _no_marker(pr.get('inputter') or '')
    if _vf and normalise_payee(_vf) == normalise_payee(_inp):
        verif_block = (
            '<p style="font-size:13px;margin:2px 0;color:#991B1B;font-weight:bold;">'
            '&#10007; Verifier is the preparer — the independent check is the '
            "committee's decision (PAY-BANK-04)</p>")
    else:
        verif_block = (
            '<p style="font-size:13px;margin:2px 0;">&#10003; Payments verified '
            'against supporting documentation</p>'
            f'<p style="font-size:13px;margin:2px 0;">&#10003; Verified by: '
            f'{escape(_vf) or escape(_inp) or "&mdash;"}</p>')
    return f'''<div style="font-family:'Book Antiqua',Georgia,serif;max-width:720px;color:#111;">
      <div style="background:{NAVY};color:#fff;padding:14px 18px;border-radius:6px 6px 0 0;">
        <div style="font-size:16px;font-weight:bold;">{escape(pr["entity"])}</div>
        <div style="color:{ORANGE};font-size:13px;letter-spacing:.5px;">PAYMENT AUTHORISATION REQUEST</div>
      </div>
      <div style="border:1px solid #E5E7EB;border-top:none;padding:16px 18px;border-radius:0 0 6px 6px;">
        <table style="border-collapse:collapse;width:100%;font-size:13px;margin-bottom:12px;">
          <tr><td style="padding:4px 8px;width:18%;color:#6B7280;">DATE</td><td style="padding:4px 8px;">{escape(str(due) if due else timezone.localdate().isoformat())}</td></tr>
          <tr><td style="padding:4px 8px;color:#6B7280;">REF</td><td style="padding:4px 8px;">{escape(pr["ref"])}</td></tr>
          <tr><td style="padding:4px 8px;color:#6B7280;">TO</td><td style="padding:4px 8px;">CFO — Prathap Ganesharajah</td></tr>
          <tr><td style="padding:4px 8px;color:#6B7280;">FROM</td><td style="padding:4px 8px;">{escape(pr["inputter"]) or "Finance Team"}</td></tr>
          <tr><td style="padding:4px 8px;color:#6B7280;">SUBJECT</td><td style="padding:4px 8px;"><strong>{escape(pr["subject"])}</strong></td></tr>
          {cat_row}
          {payee_row}
          <tr><td style="padding:4px 8px;color:#6B7280;">PROCESSING</td><td style="padding:4px 8px;"><strong>{escape(_pm_label)}</strong></td></tr>
        </table>
        <h3 style="color:{NAVY};font-size:13px;margin:6px 0;">SECTION A: PAYMENT DETAILS</h3>
        <table style="border-collapse:collapse;width:100%;font-size:13px;">
          <thead><tr style="background:#F9FAFB;">
            <th style="padding:6px 8px;border:1px solid #E5E7EB;text-align:left;">Item</th>
            <th style="padding:6px 8px;border:1px solid #E5E7EB;text-align:left;">Description</th>
            <th style="padding:6px 8px;border:1px solid #E5E7EB;text-align:left;">GL Code</th>
            <th style="padding:6px 8px;border:1px solid #E5E7EB;text-align:left;">Ref #</th>
            {claim_head}
            {extra_head}
            <th style="padding:6px 8px;border:1px solid #E5E7EB;text-align:right;">Amount ({escape(cur)})</th>
          </tr></thead>
          <tbody>
            {rows}
            <tr style="background:#FFF7ED;font-weight:bold;">
              <td style="padding:6px 8px;border:1px solid #E5E7EB;" colspan="{total_colspan}">TOTAL PAYABLE</td>
              <td style="padding:6px 8px;border:1px solid #E5E7EB;text-align:right;">{total:,.2f}</td>
            </tr>
          </tbody>
        </table>
        {terms_block}
        {bank_block}
        {wording_block}
        <h3 style="color:{NAVY};font-size:13px;margin:18px 0 6px;">SECTION C: VERIFICATION &amp; APPROVAL</h3>
        {verif_block}
        <h3 style="color:{NAVY};font-size:13px;margin:18px 0 6px;">SECTION D: AUTHORITY DECLARATION</h3>
        <p style="font-size:13px;">Please authorise the payment of <strong>{_money(cur, total)}</strong> for the items above.</p>
        <table style="width:100%;font-size:12px;color:#374151;margin-top:10px;">
          <tr>
            <td style="padding-top:18px;border-top:1px solid #9CA3AF;">Inputter<br>{escape(pr["inputter"]) or "&mdash;"}</td>
            <td style="padding-top:18px;border-top:1px solid #9CA3AF;">Verifier<br>{escape(pr["verifier"]) or "&mdash;"}</td>
            <td style="padding-top:18px;border-top:1px solid #9CA3AF;">Approver<br>Prathap Ganesharajah, CFO</td>
          </tr>
        </table>
      </div>
    </div>'''


def _render_plaintext(pr: dict) -> str:
    """Readable text version for the OmniTask body (the drawer shows body as
    pre-wrapped text, not HTML)."""
    cur = pr['currency']
    lines = [
        f'{pr["entity"]}',
        f'PAYMENT AUTHORISATION REQUEST',
        f'REF: {pr["ref"]}    TO: CFO Prathap Ganesharajah',
        f'SUBJECT: {pr["subject"]}',
    ]
    if _category_label(pr.get('category', '')):
        lines.append(f'CATEGORY: {_category_label(pr["category"])}')
    if pr.get('category') == PaymentRequest.Category.CLAIM:
        lines.append('PAID TO: ' + dict(PaymentRequest.ClaimPayeeType.choices).get(
            pr.get('claim_payee_type') or '', 'NOT STATED'))
    lines += ['', 'PAYMENT DETAILS']
    show_terms = _terms_gate_applies(pr.get('category') or '',
                                     pr.get('claim_payee_type') or '')
    is_claim = (pr.get('category') or '') == PaymentRequest.Category.CLAIM
    for i, ln in enumerate(pr['line_items'], 1):
        gl = f' [{ln["gl_code"]}]' if ln['gl_code'] else ''
        rf = f' ({ln["ref"]})' if ln['ref'] else ''
        lines.append(f'  {i}. {ln["description"]}{gl}{rf} — {cur} {ln["amount"]:,.2f}')
        if is_claim:
            lines.append(f'       claim {ln.get("claim_number") or "—"}')
        # Who the proof of payment goes to, and whether we hold that address
        # (Finance spec 2026-09-08). On the pack the approver reads, not only in
        # an API field a screen might or might not render.
        if ln.get('pop_recipient_email'):
            _off = ' — NOT ON FILE for this claim or payee' if (
                ln.get('pop_recipient_source') == POP_SOURCE_OFF_LIST) else ''
            lines.append(f'       POP to {ln.get("pop_recipient_name") or "—"} '
                         f'<{ln["pop_recipient_email"]}>{_off}')
        if show_terms:
            terms = ln.get('terms_days') or str(DEFAULT_SUPPLIER_TERMS_DAYS)
            basis = ln.get('terms_basis') or DEFAULT_TERMS_BASIS
            lines.append(f'       invoice {ln.get("invoice_number") or "—"} '
                         f'dated {ln.get("invoice_date") or "—"} · {terms} days from '
                         f'{basis} · DUE {ln.get("due_date") or "—"}')
    lines.append(f'  TOTAL PAYABLE: {_money(cur, pr["total"])}')
    # How the money leaves, and how many transactions that implies (Kelvin
    # Kimani spec 2026-09-08). The liquidity total is identical under both
    # methods; only the count changes, and whoever keys the bank needs it.
    lines.append('  PROCESSING METHOD: ' + PaymentRequest.processing_summary(
        pr['line_items'],
        pr.get('processing_method') or PaymentRequest.ProcessingMethod.BULK)[2])

    # Finance, 2026-08-20: "the final narration must display on the approval
    # screen … The approver then releases exactly what the bank will receive."
    # So it goes in the pack the approver actually reads, not only in an API
    # field a screen might or might not render.
    _narr = (pr.get('bank_narration') or '').strip()
    _oref = (pr.get('bank_our_reference') or '').strip()
    if _narr or _oref:
        lines.append('')
        lines.append('WHAT THE BANK WILL BE TOLD')
        lines.append(f'  They will see : {_narr or "(nothing — it will be filled in from the payment number)"}')
        lines.append(f'  Our reference : {_oref or "(nothing — it will be filled in from the payment number)"}')
        if pr.get('bank_payment_type'):
            lines.append(f'  Payment type  : {pr["bank_payment_type"]}')
    if show_terms:
        pay = pr.get('payment_date') or timezone.localdate()
        meta = _terms_meta_from_lines(pr['line_items'], pay)
        days_early = int(meta.get('days_early') or 0)
        _tlines = pr.get('line_items') or []
        _dates_missing = any(
            not _parse_iso(ln.get('invoice_date') or '') or not _parse_iso(ln.get('due_date') or '')
            for ln in _tlines)
        if _dates_missing:
            _timing = 'TERMS NOT ESTABLISHED — dates missing; committee decision on record'
        elif days_early > 0:
            _timing = f'EARLY by {days_early} day(s)'
        else:
            _timing = 'on or after due date'
        lines += [
            '',
            'PAYMENT TERMS COMPLIANCE (PAY-SUP-01)',
            f'  Payment date: {pr.get("payment_date") or timezone.localdate()}',
            f'  This request becomes payable on: {meta.get("binding_due_date") or "—"}',
            f'  Timing: {_timing}',
        ]
        if (pr.get('early_payment_reason') or '').strip():
            lines.append(f'  Early-settlement reason: {pr["early_payment_reason"].strip()}')
        if pr.get('funds_already_moved'):
            lines.append('  WARNING: funds were transferred to fund this request '
                         'BEFORE authorisation.')
    # PAY-DUP-01 (CFO 2026-08-03) — a waved-through duplicate is stated on the
    # pack, in the plaintext the approver gets by email as well as the HTML.
    dup_reason = (pr.get('duplicate_override_reason') or '').strip()
    if dup_reason:
        lines += ['', 'DUPLICATE OVERRIDE (PAY-DUP-01)',
                  '  This request repeats lines already raised or paid. It was '
                  'released on the following written justification:',
                  f'  {dup_reason}']
        for m in (pr.get('duplicate_matches') or [])[:12]:
            if m.get('clash_kind') in ('existing_request', 'same_request'):
                lines.append(f'  • Line {m.get("line")} ({m.get("label")}): '
                             f'{m.get("detail")}.')
    if pr.get('opening_balance') is not None:
        closing = pr['opening_balance'] - pr['total']
        lines += [
            '',
            f'BANK: {pr["bank_name"]} {pr["account_number"]}'.strip(),
            f'  Opening: {_money(cur, pr["opening_balance"])}',
            f'  Closing after payment: {_money(cur, closing)}'
            f'  ({"SUFFICIENT" if closing >= 0 else "INSUFFICIENT"})',
        ]
    lines += ['', 'Open in Omni → Payment Requests to pay or reassign.']
    return '\n'.join(lines)


def _ai_summary(pr: dict) -> str:
    """One-line covering summary via reasoning_complete. Only the subject + line
    descriptions + amounts are sent (no account numbers / bank detail). Falls
    back to a deterministic sentence if no AI engine is configured or the text
    isn't safe to send."""
    items = '; '.join(f'{ln["description"]} ({pr["currency"]} {ln["amount"]:,.2f})'
                      for ln in pr['line_items'])
    deterministic = (f'Payment authorisation for {pr["subject"]} — '
                     f'{len(pr["line_items"])} item(s) totalling '
                     f'{_money(pr["currency"], pr["total"])}.')
    try:
        from core import ai_assist
        prompt = (
            'Write ONE short, plain-English sentence a finance clerk would use to '
            'introduce this payment authorisation to the CFO. No greeting, no '
            'sign-off, just the sentence. '
            f'Subject: {pr["subject"]}. Items: {items}. '
            f'Total: {_money(pr["currency"], pr["total"])}.'
        )
        rep = ai_assist.is_safe_for_ai(prompt)
        if not rep.safe:
            return deterministic
        # Short per-engine cap so a vendor outage can't hang the payment submit
        # in the request path — fall back to the deterministic sentence instead.
        out = ai_assist.reasoning_complete(rep.redacted_text, timeout=6, max_tokens=120).strip()
        return out[:400] or deterministic
    except Exception:      # noqa: BLE001 — any AI failure -> deterministic
        return deterministic


def _task_title(entity: str, category: str, currency: str, total: Decimal, *, prefix: str = 'Payment authorisation') -> str:
    cat_seg = f' · {_category_label(category)}' if _category_label(category) else ''
    return (f'{prefix} — {_entity_code(entity)}{cat_seg} · {_money(currency, total)}')[:200]


def _approval_task_due(payment_due):
    """Floor an approval task's deadline so it is never overdue before it exists.

    A payment's own due date can predate the moment its approval lands in the
    approver's queue — a supplier bill dated last week, a petty-cash
    reimbursement for a period that already closed. Stamping that historical
    date straight onto the OmniTask made the task 'overdue' the instant it was
    created, so the daily 'N task(s) due or overdue' digest nagged the approver
    about work they had only just been handed (CFO 2026-09-10: check the alerts
    make sense). The payment's real due date is left untouched on the
    PaymentRequest and still shown in the pack; this only floors the approver's
    SLA to no earlier than today. Africa/Gaborone via timezone.localdate().
    """
    today = timezone.localdate()
    if payment_due and payment_due < today:
        return today
    return payment_due


def _pr_public_dict(p: 'PaymentRequest') -> dict:
    """Rebuild the dict shape _render_plaintext/_render_html expect from a saved
    PaymentRequest (line-item amounts are stored as strings → back to Decimal)."""
    return {
        'entity': p.entity, 'category': p.category, 'currency': p.currency,
        # Without this the re-rendered pack would drop the invoice/due-date
        # columns on a claims-provider payment — the gate would look absent.
        'claim_payee_type': p.claim_payee_type,
        # B8: the approver must be able to see that a refund IS a refund, and
        # which payment it reverses, without leaving the authorisation screen.
        'original_payment_ref': p.original_payment_ref,
        'duplicated_from_ref': p.duplicated_from_ref,
        'ref': p.ref, 'subject': p.subject, 'payee': p.payee,
        # Without this the re-rendered pack would drop the processing method and
        # read as if the request never stated how the money should leave.
        'processing_method': p.processing_method,
        'inputter': p.inputter,
        'verifier': p.verifier, 'due_date': p.due_date,
        'account_name': p.account_name, 'account_number': p.account_number,
        'bank_name': p.bank_name,
        'branch_code': p.branch_code, 'account_type': p.account_type,
        'bank_payment_type': p.bank_payment_type,
        'bank_narration': p.bank_narration,
        'bank_our_reference': p.bank_our_reference,
        # So the screen can say whether it reached the bank, and why not.
        'fnb_loaded_at': p.fnb_loaded_at, 'fnb_load_error': p.fnb_load_error,
        'fnb_batch_id': str(p.fnb_batch_id) if p.fnb_batch_id else None,
        'opening_balance': p.opening_balance,
        'total': p.total,
        'payment_date': p.payment_date,
        'early_payment_reason': p.early_payment_reason,
        'funds_already_moved': p.funds_already_moved,
        # PAY-DUP-01 (CFO 2026-08-03) — an overridden duplicate must be visible
        # on the pack itself, not only in the database.
        'duplicate_override_reason': p.duplicate_override_reason,
        'duplicate_matches': p.duplicate_matches or [],
        'line_items': [{
            'description': ln.get('description', ''),
            'gl_code': ln.get('gl_code', ''),
            'ref': ln.get('ref', ''),
            'amount': _dec(ln.get('amount')),
            'invoice_number': ln.get('invoice_number', ''),
            'invoice_date': ln.get('invoice_date', ''),
            'terms_basis': ln.get('terms_basis', ''),
            'terms_days': ln.get('terms_days', ''),
            'due_date': ln.get('due_date', ''),
            # Without this a re-rendered claims pack would drop the Claim #
            # column and read as if nothing tied it to a claim.
            'claim_number': ln.get('claim_number', ''),
            # Without this the re-rendered pack would drop the POP recipient and
            # read as if nobody had been named.
            'pop_recipient_name': ln.get('pop_recipient_name', ''),
            'pop_recipient_email': ln.get('pop_recipient_email', ''),
            'pop_recipient_source': ln.get('pop_recipient_source', ''),
        } for ln in (p.line_items or [])],
    }


def _email_authorisation(task, pr: dict, *, raised_by: str, stage: str,
                         summary: str = '') -> int:
    """Email the person a payment authorisation has just landed on (CFO
    2026-07-29: four requests arrived in the CFO's inbox that morning and no
    email went out — the money queue was the ONE workflow with no notification).

    Sent at BOTH legs: stage 'finance' → the nominated finance approver,
    stage 'cfo' → the CFO once finance has signed off.

    Deliberately NOT the full task body: that carries the bank account number
    and the liquidity block, which belong inside Omni and not in a mailbox. The
    email carries what the approver needs to decide whether to open it now —
    entity, reference, payee, the line items, the total, the deadline and who
    raised it — plus the link.

    Best-effort: never raises, so a flaky mailer can never block a payment
    request from being created. Returns 1 on send, 0 otherwise.
    """
    from django.conf import settings

    assignee = getattr(task, 'assignee', None)
    email = (getattr(assignee, 'email', '') or '').strip()
    if not email or task.assigner_id == task.assignee_id:
        return 0

    # CFO 2026-08-12: the CFO no longer wants a separate email per payment. The
    # 09:30 payment digest (payment_daily_digest) rolls up everything waiting on
    # him into ONE email. Suppress only the CFO-stage notification; the
    # finance-stage email to the nominated approver is unchanged. Gated on the
    # consolidation flag so it is reversible in one switch.
    #
    # ⚠️ PREMISE CHANGED 2026-08-21. This suppression was justified partly by
    # PAY-WIN-01: "the day's requests are all raised inside the 07:00–09:30
    # window, so that single digest catches them all." That window has been
    # abolished — a request raised at 16:00 is now suppressed here AND missed by
    # the 09:30 digest that already went out, so the CFO first sees it at 09:30
    # the NEXT day. Left as-is deliberately rather than changed in the same
    # commit that removed the window: whether he wants a per-payment email back,
    # a second digest, or the overnight wait is his call, not a side effect.
    if stage == 'cfo' and getattr(settings, 'CONSOLIDATED_EMAILS_ENABLED', False):
        return 0

    first = (assignee.get_full_name() or '').split(' ')[0] or assignee.username
    base = getattr(settings, 'PUBLIC_BASE_URL',
                   'https://omni.alphadirect.co.bw').rstrip('/')
    cur, total = pr['currency'], pr['total']
    due = pr.get('due_date')
    due_txt = f'{due:%d %b %Y}' if hasattr(due, 'year') else (str(due) if due else 'No date given')
    cat = _category_label(pr.get('category', '')) or '—'
    action = ('sign this off so it can reach the CFO' if stage == 'finance'
              else 'authorise or reject the payment')

    rows = ''.join(
        f'<tr><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;">'
        f'{escape(ln["description"])}'
        + (f' <span style="color:#6B7280;">({escape(ln["ref"])})</span>' if ln.get('ref') else '')
        + f'</td><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;'
          f'text-align:right;white-space:nowrap;">{ln["amount"]:,.2f}</td></tr>'
        for ln in pr['line_items'])

    warn = ('<p style="margin:12px 0;padding:9px 11px;background:#FEF2F2;'
            'border-left:3px solid #991B1B;color:#991B1B;font-weight:600;">'
            'Funds were moved to fund this request BEFORE authorisation.</p>'
            if pr.get('funds_already_moved') else '')

    # PAY-DUP-01 (CFO 2026-08-03) — if a duplicate was overridden, the approver
    # must see it in the notification itself, before opening anything.
    dup_reason = (pr.get('duplicate_override_reason') or '').strip()
    if dup_reason:
        clashes = ''.join(
            f'<li>Line {escape(str(m.get("line")))} '
            f'({escape(str(m.get("label") or ""))}): '
            f'{escape(str(m.get("detail") or ""))}.</li>'
            for m in (pr.get('duplicate_matches') or [])[:8]
            if m.get('clash_kind') in ('existing_request', 'same_request'))
        warn += ('<p style="margin:12px 0;padding:9px 11px;background:#FEF2F2;'
                 'border-left:3px solid #991B1B;color:#991B1B;">'
                 '<strong>DUPLICATE OVERRIDE (PAY-DUP-01).</strong> '
                 'This request repeats lines already raised or paid. '
                 f'Released on this justification: &ldquo;{escape(dup_reason)[:400]}&rdquo;'
                 + (f'<ul style="margin:8px 0 0;padding-left:18px;">{clashes}</ul>'
                    if clashes else '')
                 + '</p>')

    html = (
        f'<p>{escape(first)},</p>'
        f'<p><strong>{escape(raised_by)}</strong> has sent you a payment '
        f'authorisation in Omni. Please {action}.</p>'
        '<table style="border-collapse:collapse;width:100%;font-size:14px;">'
        f'<tr><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;width:120px;'
        f'color:#6B7280;">Reference</td><td style="padding:5px 9px;'
        f'border-bottom:1px solid #e5e7eb;font-weight:600;">{escape(pr["ref"])}</td></tr>'
        f'<tr><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;color:#6B7280;">'
        f'Entity</td><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;">'
        f'{escape(pr["entity"])}</td></tr>'
        f'<tr><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;color:#6B7280;">'
        f'Category</td><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;">'
        f'{escape(cat)}</td></tr>'
        f'<tr><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;color:#6B7280;">'
        f'Payee</td><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;">'
        f'{escape(pr.get("payee") or pr["subject"])}</td></tr>'
        f'<tr><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;color:#6B7280;">'
        f'Total</td><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;'
        f'font-weight:700;color:{NAVY};">{_money(cur, total)}</td></tr>'
        f'<tr><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;color:#6B7280;">'
        f'Needed by</td><td style="padding:5px 9px;border-bottom:1px solid #e5e7eb;">'
        f'{escape(due_txt)}</td></tr>'
        '</table>'
        + warn +
        '<p style="margin:14px 0 4px;font-weight:600;color:' + NAVY + ';">What is being paid</p>'
        '<table style="border-collapse:collapse;width:100%;font-size:13px;">' + rows + '</table>'
        + (f'<p style="margin:14px 0 4px;font-weight:600;color:{NAVY};">Summary</p>'
           f'<div style="padding:9px 11px;background:#F9FAFB;border-left:3px solid {ORANGE};'
           f'border-radius:4px;">{escape(summary.strip())[:800]}</div>'
           if (summary or '').strip() else '')
        + '<p style="margin-top:14px;">Open it in Omni &rarr; '
          f'<strong>Payment Requests</strong>:<br>'
          f'<a href="{base}/payment-requests">{base}/payment-requests</a></p>'
          '<p style="color:#6B7280;font-size:12px;">The bank account and the '
          'liquidity position are shown in Omni, not in this email.</p>'
    )
    text = (
        f'{first}, {raised_by} has sent you a payment authorisation in Omni.\n\n'
        f'Reference: {pr["ref"]}\nEntity: {pr["entity"]}\nCategory: {cat}\n'
        f'Payee: {pr.get("payee") or pr["subject"]}\n'
        f'Total: {_money(cur, total)}\nNeeded by: {due_txt}\n\n'
        f'Please {action}.\nOpen: {base}/payment-requests\n'
    )
    try:
        from core.notifications import send_html_with_cfo_cc
        return send_html_with_cfo_cc(
            subject=f'Omni — payment authorisation waiting on you: {task.title}'[:150],
            html=html, to=[email], text_fallback=text, cc_cfo=False)
    except Exception:  # noqa: BLE001 — mail must never block a payment request
        return 0


def _terms_meta_from_lines(raw_lines, pay_date):
    """Binding due date / days-early, recomputed from the stored line items.
    Binding = the LATEST due date, matching _validate_supplier_terms."""
    dues = [d for d in (_parse_iso(ln.get('due_date')) for ln in raw_lines) if d]
    if not dues:
        return {}
    binding = max(dues)
    ref_date = pay_date or timezone.localdate()
    return {'binding_due_date': binding.isoformat(),
            'days_early': max((binding - ref_date).days, 0)}



# A claim reference sitting at the front of a free-text description, e.g.
# "G2026004287 CARFIL SERVICES". Letter(s) then at least six digits.
_CLAIM_IN_DESC = re.compile(r'^\s*([A-Z]{1,3}\d{6,})\b', re.I)


def _same_loader_recent_same_amount(lines, *, currency: str, user, hours: int = 48) -> list:
    """The same person, the same amount, inside 48 hours.

    Manus, 2026-08-10: one loader appears in 13 of the 15 paid-twice cases. Six
    pairs are consecutive-day (7 then 8 August) and three are same-day. *"It reads
    as one person re-loading the previous day's batch rather than checking what
    had already cleared."* This single rule would have caught nine of the fifteen
    on its own, and it needs no reference number, no claim and no payee match —
    only that the same hand loaded the same amount twice in two days.
    """
    from datetime import timedelta

    from django.utils import timezone as _tz

    from .models import PaymentRequest
    from .payment_duplicates import _money, exclude_dead

    if user is None or getattr(user, 'id', None) is None:
        return []
    amounts = {}
    for i, ln in enumerate(lines or [], 1):
        if isinstance(ln, dict):
            amounts.setdefault(_money(ln.get('amount')), []).append((i, ln))
    amounts.pop(Decimal('0'), None)
    if not amounts:
        return []

    recent = (exclude_dead(
        PaymentRequest.objects.filter(
            created_by=user, currency=currency,
            created_at__gte=_tz.now() - timedelta(hours=hours)))
        # A draft is pre-submission WIP, never a "you loaded this earlier"
        # clash — and it would else flag itself on submit (CFO 2026-09-02).
        .exclude(status=PaymentRequest.Status.DRAFT)
        .only('ref', 'status', 'line_items', 'created_at'))

    out = []
    for pr in recent.iterator():
        for ex in (pr.line_items or []):
            if not isinstance(ex, dict):
                continue
            amt = _money(ex.get('amount'))
            for i, ln in amounts.get(amt, []):
                out.append({
                    'line': i,
                    'label': str(ln.get('description') or ln.get('ref') or ''),
                    'amount': str(amt),
                    'clash_kind': 'same_loader_48h',
                    'clash_ref': pr.ref,
                    'clash_status': pr.status,
                    'detail': (f'you loaded {currency} {amt:,.2f} on {pr.ref} '
                               f'{_ago(pr.created_at)} — check it has not already '
                               f'cleared before loading it again'),
                })
    return out


def _ago(when) -> str:
    from django.utils import timezone as _tz
    hrs = int((_tz.now() - when).total_seconds() // 3600)
    if hrs < 1:
        return 'earlier this hour'
    if hrs < 24:
        return f'{hrs} hour{"" if hrs == 1 else "s"} ago'
    return 'yesterday' if hrs < 48 else f'{hrs // 24} days ago'


def _lines_previously_rejected(lines, *, currency: str) -> list[dict]:
    """Lines on this pack that already appear on a REJECTED request.

    BOBLIN COMPUTERS G2026004718 was rejected on 4 August, then paid on the 5th,
    7th and 8th across three consecutive requests by the same loader. A rejection
    that does not stick is not a control. These go to the CFO rather than back to
    the approver who turned them down (Manus 2026-08-09).
    """
    from .models import PaymentRequest
    from .payment_duplicates import compare_lines

    rejected = (PaymentRequest.objects
                .filter(currency=currency, status=PaymentRequest.Status.REJECTED)
                .only('ref', 'status', 'line_items'))
    existing = ((pr.ref, pr.status, pr.line_items or []) for pr in rejected.iterator())
    return compare_lines(lines, existing, currency=currency)['hard']


def _lines_copied_from_settled(lines, *, currency: str) -> list[dict]:
    """Lines reloaded off a pack whose own line has ALREADY been settled.

    Leano Makwapa asked for a "reload the previous supplier payments" button
    (2026-09-15). Reloading yesterday's batch is precisely the action behind the
    paid-twice sweep — 15 cases, BWP 418,392.87 already paid, 27 July to 8
    August 2026 (Manus, 2026-08-09, corrected 2026-08-10). The reading is
    quoted in full on _same_loader_recent_same_amount above, the guard built
    for the same shape.

    So the copy carries the source reference, and this turns that stamp into the
    control the duplicate sweep never had: instead of INFERRING a repeat from a
    matching amount, we can read the source line and see it was already paid.
    A hard clash, because there is nothing to interpret — the raiser is asking
    to pay an invoice this system records as settled.

    Matching is by invoice number first (the identity of a supplier bill), and
    falls back to the amount when the source line carries no invoice number.
    """
    from .models import PaymentRequest

    refs = {ln.get('copied_from_ref') for ln in (lines or [])
            if isinstance(ln, dict) and (ln.get('copied_from_ref') or '').strip()}
    if not refs:
        return []

    sources = {p.ref: p for p in PaymentRequest.objects
               .filter(ref__in=refs, currency=currency)
               .only('ref', 'status', 'line_items')}
    if not sources:
        return []

    out = []
    for i, ln in enumerate(lines or [], 1):
        if not isinstance(ln, dict):
            continue
        ref = (ln.get('copied_from_ref') or '').strip()
        src = sources.get(ref)
        if src is None:
            continue
        inv = (ln.get('invoice_number') or '').strip().upper()
        amt = _dec(ln.get('amount'))
        for ex in (src.line_items or []):
            if not isinstance(ex, dict):
                continue
            ex_inv = (ex.get('invoice_number') or '').strip().upper()
            # Invoice number is the identity of a supplier bill, so when BOTH
            # sides carry one it decides, and nothing else is consulted.
            #
            # The amount is the fallback ONLY when the NEW line has no invoice
            # number. That covers the real mistake — reloading a settled line
            # and leaving the invoice box empty — without punishing the raiser
            # who typed a new one.
            #
            # The wider `not ex_inv or not inv` form fired on the SOURCE being
            # blank too, and most real lines on prod carry no invoice number
            # (verified live on PAY/ADIC/2026/09/12/0009, 2026-09-15): every
            # next-month invoice at a repeated amount was clashing. A hard
            # clash never refuses a payment, but it does send it to the
            # committee, and a control that cries wolf gets ignored.
            same = ((inv and ex_inv and inv == ex_inv)
                    or (not inv and amt and _dec(ex.get('amount')) == amt))
            if not same:
                continue
            code, label = _history_line_status(src, ex)
            if code not in (PaymentRequest.Status.PAID, PaymentRequest.Status.CANCELLED):
                continue
            out.append({
                'line': i,
                'label': str(ln.get('description') or inv or ''),
                'amount': str(amt),
                'clash_kind': 'copied_from_settled',
                'clash_ref': src.ref,
                'clash_status': src.status,
                'detail': (f'you reloaded this line off {src.ref}, where it is already '
                           f'recorded as {label.lower()} — it has been settled once and '
                           f'must not be paid again'),
            })
            break
    return out


def _line_rows(pr) -> list:
    """Per-line detail for the duplicate sweep: claim, invoice, amount, payee.

    The fields are already captured on every request; the API returned only the
    envelope, so nothing downstream could match a line against another request's
    line. Keys are normalised the way taskboard.payment_duplicates matches —
    uppercased and stripped — so a caller does not have to re-do it.
    """
    out = []
    for i, ln in enumerate(pr.line_items or [], start=1):
        if not isinstance(ln, dict):
            continue
        # The stored keys are claim_number / invoice_number. Verified against
        # live data 2026-08-09 — an earlier version guessed claim_no/invoice_no
        # and returned empty strings for every real line, while the unit test
        # (which used the guessed names) passed. Aliases kept for older rows.
        claim = str(ln.get('claim_number') or ln.get('claim_no')
                    or ln.get('claim') or '').strip().upper()
        inv = str(ln.get('invoice_number') or ln.get('invoice_no')
                  or ln.get('invoice') or '').strip().upper()
        desc = str(ln.get('description') or ln.get('ref') or '').strip()
        # Many rows carry no claim_number at all and keep the claim inside the
        # description instead — CARFIL SERVICES lines read
        # "G2026004287 CARFIL SERVICES" with claim_number empty. Claim-tier
        # duplicate matching missed every one of them; only payee+amount caught
        # them, which is the weaker test (Manus 2026-08-09, 4 CARFIL pairs).
        if not claim:
            m = _CLAIM_IN_DESC.match(desc)
            if m:
                claim = m.group(1).upper()
        # The payee is the description with any leading claim token removed.
        payee = desc[len(claim):].strip() if claim and desc.upper().startswith(claim) else desc
        # 101 of 233 live lines legitimately carry no claim number — rent,
        # utilities, petty cash, premiums. Saying which kind a line is lets a
        # sweep apply the right rule instead of guessing, rather than dropping
        # 43% of lines to the weaker payee+amount test (Manus 2026-08-09).
        kind = 'claim' if claim else ('supplier' if inv else 'internal')
        out.append({
            'line': i,
            'description': desc,
            'line_kind': kind,
            'claim_no': claim,
            'invoice_no': inv,
            'invoice_key': ''.join(ch for ch in inv if ch.isalnum()),
            'payee': payee,
            'amount': str(ln.get('amount') or ''),
            'invoice_date': ln.get('invoice_date') or None,
            'due_date': ln.get('due_date') or None,
        })
    return out


def _status_label(code: str) -> str:
    return dict(PaymentRequest.Status.choices).get(code, code)


def _can_view_request(user, p: 'PaymentRequest') -> bool:
    """Who may read a payment pack (the lines, the invoices, the attachments).

    The CFO, any finance approver, and the raiser — plus the person the
    authorisation task is currently sitting on (CFO 2026-08-03). Without the
    assignee rule the phone card showed a total and nothing else, so an
    approver could not see which suppliers and amounts were inside it and was
    approving blind. No customer PII lives here: payees are vendors and the
    bank details are Alpha Direct's own.
    """
    cfo = _cfo_user()
    if user.is_superuser or (cfo and user.id == cfo.id):
        return True
    if _is_first_approver(user) or p.created_by_id == user.id:
        return True
    # A committee member must be able to OPEN an exception they are deciding —
    # otherwise three of the six (Oprah, Keetile, Tlamelo, who are not finance
    # approvers) would decide it blind (Fable fix 2, the exact failure this
    # function exists to stop).
    if p.exception_control and _is_committee_member(user):
        return True
    # The accounts team who load the payments to FNB and record the bank's answer
    # (CFO 2026-09-11). Without this the "rejected in the FNB app" button would be
    # invisible to the exact people it was given to — they could not open the pack
    # to reach it. READ ONLY: this function grants no approval and no sign-off;
    # every decision endpoint checks _is_first_approver / the CFO separately.
    if _may_mark_fnb_rejected(user):
        return True
    return bool(p.task_id and p.task and p.task.assignee_id == user.id)


def _mask_account(num: str) -> str:
    """Show only the last 4 digits of an account number in bulk output."""
    d = ''.join(c for c in (num or '') if c.isdigit())
    return f'••••{d[-4:]}' if len(d) >= 4 else ('••••' if d else '')


# Entity name variants that are the SAME licensed insurer, collapsed to one
# canonical label so audit / NBFIRA pivots and entity filters stop fragmenting
# (Kago 2026-08-31). EXACT-match keys only — never a substring test, or the
# separate "…Company South Africa" entity would be swallowed into the BWP one.
_ENTITY_CANON = {
    'adic': 'Alpha Direct Insurance Company',
    'alpha direct insurance': 'Alpha Direct Insurance Company',
    'alpha direct insurance company': 'Alpha Direct Insurance Company',
}


def _entity_norm(name: str) -> str:
    return _ENTITY_CANON.get((name or '').strip().lower(), (name or '').strip())


_CLAIM_RE = re.compile(r'\bG\d{6,}\b')


def _claim_no(p) -> str:
    """The Graphite claim number for a payment, or ''. Refund imports carry it
    in graphite_ref; claim payments carry it as the leading G-number in the
    subject (e.g. 'G2026005213 COLDLINE (PTY) LTD') or a line reference."""
    if (p.graphite_ref or '').strip():
        return p.graphite_ref.strip()
    hunt = [p.subject or ''] + [str(li.get('ref', '')) for li in (p.line_items or [])
                                if isinstance(li, dict)]
    for hay in hunt:
        m = _CLAIM_RE.search(hay)
        if m:
            return m.group(0)
    return ''


def _acct_last4(num: str) -> str:
    """The last four account digits as PLAIN TEXT — no bullet characters, which
    are noise in Excel (Kago 2026-08-31). '' when there is no account number."""
    d = ''.join(c for c in (num or '') if c.isdigit())
    return d[-4:] if len(d) >= 4 else ''


def _claim_desc(p) -> str:
    """The claim description/summary for a request's claim(s) — the export column
    (CFO 2026-08-31). Prefers the stored AI summary; falls back to the
    deterministic facts. Reads the GraphiteClaim mirror only — no AI call, no
    Graphite call, so the export never hangs on an outage."""
    # Reuse the insight lookup so the export and the live box pick the SAME row
    # (its order_by handles NULL detail_synced_at correctly on Postgres — H96).
    from integrations.claim_insight import build_facts_text, _lookup
    nums: list[str] = []
    for li in (p.line_items or []):
        if isinstance(li, dict):
            cn = str(li.get('claim_number', '') or '').strip()
            if cn and cn not in nums:
                nums.append(cn)
    if not nums:
        one = _claim_no(p)
        if one:
            nums = [one]
    out = []
    for cn in nums[:5]:
        c = _lookup(cn)
        if c:
            out.append((c.ai_summary or '').strip() or build_facts_text(c, p.currency or 'BWP'))
    return '\n\n'.join(out)


def _line_field(p, key: str) -> str:
    """Distinct, non-empty values of `key` across a request's line items, in
    order, joined for a single cell (GL code / invoice ref can differ per line)."""
    seen, out = set(), []
    for li in (p.line_items or []):
        if not isinstance(li, dict):
            continue
        v = str(li.get(key, '') or '').strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return ' / '.join(out)


def _payment_register_xlsx(qs, request=None):
    """The filtered payment register as an audit-ready .xlsx (Kago 2026-08-31).

    Fixes over the first cut:
      * Headers sit in ROW 1 of the data sheet; the title + the filters applied
        move to a separate cover sheet, so autofilter / pivots / Power Query work.
      * Dates are real Excel date values (sortable / range-filterable / pivotable).
      * 'Days to authorise' counts CALENDAR days in Botswana local time, so a
        request raised on the 28th and authorised on the 29th reads 1, not 0.
      * Entity names collapse to one canonical label per entity.
      * Payee falls back to the account holder name when the payee field is blank.
      * Claim number is its own column; account numbers are the last 4 as text.
    Account numbers never leave in full. Capped so an export can never try to
    stream the entire table at once.
    """
    from reporting.xlsx_export import Sheet, build_sheets_xlsx_response
    MAX = 5000
    headers = ['Reference', 'Date raised', 'Date authorised', 'Days to authorise',
               'Requester', 'Authoriser', 'Type / Category', 'Entity', 'Payee',
               'Subject', 'Claim #', 'Ref # / Invoice no.', 'GL code', 'Currency',
               'Amount', 'Status', 'Account (last 4)', 'Claim description']

    def _ld(dt):
        return timezone.localtime(dt).date() if dt else None

    rows, n = [], 0
    for p in qs.iterator():
        n += 1
        if n > MAX:
            break
        raised, authed = _ld(p.created_at), _ld(p.first_approved_at)
        rows.append([
            p.ref,
            raised,
            authed,
            (authed - raised).days if (raised and authed) else '',
            (p.created_by.get_full_name() or p.created_by.username) if p.created_by else '',
            (p.first_approver.get_full_name() or p.first_approver.username) if p.first_approver else '',
            _category_label(p.category),
            _entity_norm(p.entity),
            p.payee or p.account_name,
            p.subject,
            _claim_no(p),
            _line_field(p, 'ref'),
            _line_field(p, 'gl_code'),
            p.currency,
            p.total,
            _status_label(p.status),
            _acct_last4(p.account_number),
            _claim_desc(p),
        ])

    data = Sheet(title='Payment register', headers=headers, rows=rows,
                 date_cols=[1, 2], int_cols=[3], numeric_cols=[14])

    # Cover sheet — the title and the exact filters that were applied, kept OFF
    # the data grid so the data sheet's header can be row 1.
    now_local = timezone.localtime(timezone.now())
    crit = [['Report', 'Payment register'],
            ['Generated', now_local.strftime('%Y-%m-%d %H:%M')],
            ['Rows', str(len(rows))]]
    if n > MAX:
        crit.append(['Note', f'Showing the first {MAX}; narrow the filters to see the rest.'])
    if request is not None:
        labels = [('entity', 'Entity contains'), ('payee', 'Payee / ref contains'),
                  ('status', 'Status'), ('since', 'From date'), ('until', 'To date'),
                  ('min', 'Min amount'), ('max', 'Max amount'), ('window', 'Last N days')]
        applied = [[lab, (request.query_params.get(key) or '').strip()]
                   for key, lab in labels
                   if (request.query_params.get(key) or '').strip()]
        if str(request.query_params.get('all', '')).strip() == '1':
            applied.append(['History', 'All statuses (incl. paid / cancelled)'])
        crit.append(['', ''])
        crit.append(['Filters applied', 'none' if not applied else ''])
        crit.extend(applied)
    cover = Sheet(title='Export info', headers=['Field', 'Value'], rows=crit)

    return build_sheets_xlsx_response('payment-register.xlsx', [cover, data])


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def payment_requests(request):
    me = request.user

    if request.method == 'GET':
        from django.db.models import Q
        cfo = _cfo_user()
        is_cfo = bool(me.is_superuser or (cfo and me.id == cfo.id))
        is_first = _is_first_approver(me)
        # Register mode (Kago Tshutlhedi 2026-08-29): a READ-ONLY history view for
        # detective review — every request, every entity, every status, no 200-row
        # cap. It changes NOTHING about the action queues below; it only widens
        # what a CFO / finance approver (or the QC key) may READ. A normal user
        # asking for register mode is ignored and stays scoped to their own rows,
        # so this cannot leak the estate.
        can_register = (str(request.query_params.get('register', '')).strip() == '1'
                        and (is_cfo or is_first or _is_readonly_key_reader(request)))
        qs = (PaymentRequest.objects
              .select_related('task', 'created_by', 'first_approver', 'fnb_batch')
              # Drafts (CFO handover 2026-09-02) are pre-submission — they never
              # appear on the approval board, only on the raiser's Drafts list.
              .exclude(status=PaymentRequest.Status.DRAFT))
        if _is_readonly_key_reader(request) or can_register:
            # A read-only QC reader / register viewer sees every request, every
            # entity, every status — no approver narrowing, no window (Manus
            # 2026-08-09; Kago 2026-08-29).
            pass
        elif is_cfo:
            # CFO sees a request only AFTER a finance approver has signed it off
            # (CFO 2026-07-23) — never while it is still PENDING_FINANCE.
            qs = qs.exclude(status=PaymentRequest.Status.PENDING_FINANCE)
        elif is_first:
            # Finance approvers see the sign-off queue + anything they raised.
            qs = qs.filter(Q(status=PaymentRequest.Status.PENDING_FINANCE) | Q(created_by=me))
        else:
            qs = qs.filter(created_by=me)
        # Bank-rejected count across everything this viewer may see — computed
        # BEFORE the active-queue / status / date filters below, because a
        # bank-rejected payment usually still carries status "paid" and would
        # otherwise drop out of the default view and never be counted (EOH
        # Consulting). This is the headline "you have N rejected payments".
        # Counted across EVERY batch a request produced, not just the one its
        # own `fnb_batch` FK happens to hold. A request paid line by line
        # becomes one batch per line: on 16-Sep-2026 a supplier request became
        # four rejected instructions and the FK pointed at one, so three were
        # invisible here and the headline under-counted (CFO 18-Sep-2026).
        bank_rejected_count = qs.filter(
            Q(fnb_batch__status='failed') | Q(fnb_batches__status='failed')
        ).distinct().count()
        # Active queue only, by default: paid / rejected / cleared requests drop
        # out (bug ktshutlhedi 2026-07-25). `?all=1` shows the full history so
        # nothing is ever lost — the terminal rows are still there to review.
        show_all = str(request.query_params.get('all', '')).strip() == '1'
        # A read-only QC reader gets the whole history by default. Without this
        # `?lines=1` alone returned a silent empty 200 — every line-carrying
        # request is already PAID, so the active-queue default hid all 233 of
        # them, and the sweep would have reported "no duplicates" (Manus
        # 2026-08-09). A sweep that silently finds nothing is worse than none.
        if _is_readonly_key_reader(request) or can_register:
            show_all = True
        if not show_all:
            qs = qs.exclude(status__in=PaymentRequest.TERMINAL_STATUSES)

        # ?status=paid,pending_cfo — isolate paid from rejected without pulling
        # the whole register. ?window=N — the last N days; the BOBLIN case only
        # becomes visible when three consecutive days are compared together
        # (Manus 2026-08-09).
        want_status = [w.strip().lower() for w in
                       (request.query_params.get('status') or '').split(',') if w.strip()]
        if want_status:
            valid = {c for c, _ in PaymentRequest.Status.choices}
            bad = [w for w in want_status if w not in valid]
            if bad:
                return Response({'detail': f'Unknown status: {", ".join(bad)}.',
                                 'valid': sorted(valid)}, status=400)
            qs = qs.filter(status__in=want_status)
            show_all = True          # an explicit status beats the active-queue default

        window = (request.query_params.get('window') or '').strip()
        if window:
            if not window.isdigit() or int(window) < 1:
                return Response({'detail': 'window must be a whole number of days.'},
                                status=400)
            from datetime import timedelta as _td
            qs = qs.filter(created_at__gte=timezone.now() - _td(days=int(window)))

        # ?since=YYYY-MM-DD — so a daily sweep pulls only what is new instead of
        # re-reading the whole register every morning (CFO/Manus 2026-08-09).
        since = (request.query_params.get('since') or '').strip()
        if since:
            from django.utils.dateparse import parse_date, parse_datetime
            when = parse_datetime(since) or parse_date(since)
            if when is None:
                return Response({'detail': 'since must be YYYY-MM-DD or an ISO timestamp.'},
                                status=400)
            qs = qs.filter(created_at__gte=when)

        # Register filters (Kago 2026-08-29). Safe, additive narrowing on the
        # already-scoped queryset — a normal user could only ever narrow their own
        # rows with these, never widen. `entity`/`payee` are contains-matches;
        # `until` bounds the top of the date range (`since` bounds the bottom);
        # `min`/`max` bound the amount.
        entity_q = (request.query_params.get('entity') or '').strip()
        if entity_q:
            qs = qs.filter(entity__icontains=entity_q)
        payee_q = (request.query_params.get('payee') or '').strip()
        if payee_q:
            qs = qs.filter(Q(payee__icontains=payee_q) | Q(ref__icontains=payee_q)
                           | Q(subject__icontains=payee_q))
        until = (request.query_params.get('until') or '').strip()
        if until:
            from django.utils.dateparse import parse_date as _pd, parse_datetime as _pdt
            when = _pdt(until) or _pd(until)
            if when is None:
                return Response({'detail': 'until must be YYYY-MM-DD or an ISO timestamp.'},
                                status=400)
            qs = qs.filter(created_at__lte=when)
        for bound, op in (('min', 'total__gte'), ('max', 'total__lte')):
            raw = (request.query_params.get(bound) or '').strip()
            if raw:
                try:
                    qs = qs.filter(**{op: Decimal(raw)})
                except (InvalidOperation, ValueError):
                    return Response({'detail': f'{bound} must be a number.'}, status=400)

        # ?lines=1 — the line-level detail the duplicate sweep matches on. Off by
        # default: the register screen does not need it and the payload triples.
        want_lines = str(request.query_params.get('lines', '')).strip() == '1'

        # Export the whole filtered register to Excel (Kago 2026-08-29). Account
        # numbers are masked to the last 4 in the export — full numbers never
        # leave in a bulk file.
        # The sort key MUST end in a unique column. Postgres gives no ordering
        # guarantee among rows sharing a created_at, so two separate page queries
        # can put a tied pair on either side of a page edge — skipping one row or
        # showing it twice. Ties are real here: the overnight Graphite refund feed
        # creates requests in a tight loop. On a payment approval queue a skipped
        # row is the exact failure this paging exists to prevent (Fable 5, H88,
        # 2026-09-15).
        ordered = qs.order_by('-created_at', '-id')
        if can_register and (request.query_params.get('export') or '').strip().lower() in ('xlsx', 'excel'):
            return _payment_register_xlsx(ordered, request)

        # Pagination. The register paginated (Kago 2026-08-29); the action screens
        # kept a bare `ordered[:200]`, which is a SILENT truncation — with 378
        # requests on the system, ticking "include settled" showed 200 and dropped
        # 178 with nothing on screen saying so (CFO 2026-09-15). Both branches now
        # page, and the TRUE total always travels with the rows so the screen can
        # say "200 of 378" instead of implying 200 is all there is.
        #
        # The action screens keep 200 as their DEFAULT page size on purpose: this
        # change must not quietly shrink anyone's list. The size is now a page, not
        # a ceiling.
        try:
            page_limit = min(max(int(request.query_params.get(
                'limit', 100 if can_register else 200)), 1), 500)
            page_offset = max(int(request.query_params.get('offset', 0)), 0)
        except (TypeError, ValueError):
            return Response({'detail': 'limit/offset must be whole numbers.'}, status=400)
        page_total = ordered.count()
        _page = ordered[page_offset:page_offset + page_limit]
        # "Why is this still open?" — a plain-English reason per OPEN row, so an
        # unpaid queue never looks like a broken auto-closer (CFO 2026-09-05).
        # Batch-status reasons ('waiting for FNB sign-off' / 'rejected — reload' /
        # 'paid — closing') need no network. The extra "looks already PAID outside
        # Omni" hint needs the FNB emails, so it is read (cached ~15 min, fail-safe)
        # only for the CFO / finance approvers who could act on it.
        from django.conf import settings as _settings
        from fnb.email_reconcile import open_reason as _open_reason, catchup_matches as _catchup
        _paid_emails = []
        if is_cfo or is_first:
            from .fnb_email_view_helpers import fetch_paid_fnb_emails_cached
            _paid_emails = fetch_paid_fnb_emails_cached(
                getattr(_settings, 'FNB_EMAIL_CATCHUP_LOOKBACK_HOURS', 720))

        def _why_open(p):
            if p.status in PaymentRequest.TERMINAL_STATUSES:
                return ('', '')
            b = p.fnb_batch if p.fnb_batch_id else None
            # None from the cached fetch = the mailbox could not be read this load;
            # surface that ('unavailable') instead of a silent "nothing paid" (Fable F1).
            conf = 'unavailable' if _paid_emails is None else 'none'
            if _paid_emails:
                conf = _catchup(_payment_match_dict(p), _paid_emails).get('confidence', 'none')
            return _open_reason(
                batch_status=(getattr(b, 'status', '') or ''),
                failure_reason=(getattr(b, 'failure_reason', '') or ''),
                has_batch=bool(b),
                is_exception=(p.status == PaymentRequest.Status.EXCEPTION),
                email_confidence=conf,
                batch_statuses=list({**_batch_st.get(p.id, {}),
                                     **({b.id: b.status} if b else {})}.values()),
            )

        # Every batch of every request on this page, in ONE query — a request
        # paid line by line has one batch per line, and the single FK only
        # holds one of them (CFO master M4: part-settled flagging).
        from fnb.models import FNBBatchSubmission as _FBS
        _batch_st: dict = {}
        for _rid, _bid, _st in (_FBS.objects.filter(payment_request__in=list(_page))
                                .values_list('payment_request_id', 'id', 'status')):
            _batch_st.setdefault(_rid, {})[_bid] = _st
        _reasons = {p.id: _why_open(p) for p in _page}
        rows = [{
            'id':          str(p.id),
            'why_open':       _reasons[p.id][0],
            'why_open_tone':  _reasons[p.id][1],
            'ref':         p.ref,
            'entity':      p.entity,
            'category':      p.category,
            'category_label': _category_label(p.category),
            'currency':    p.currency,
            'subject':     p.subject,
            'payee':       p.payee,
            'total':       str(p.total),
            # Per-line state counts for the row "line lights" (CFO 2026-08-31,
            # Fable idea #1): approved / held / rejected / pending payees on this
            # pack. line_items is already loaded, so this adds no query.
            'line_progress': p.line_progress(),
            'status':        p.status,
            'status_label':  _status_label(p.status),
            # The bank REJECTED this payment (FNB batch failed) even though the
            # workflow may read "paid". Surfaced so the register can flag it red
            # and count it — a rejected payment never announces itself otherwise
            # (EOH Consulting sat unnoticed after an AC08 branch-code reject).
            'bank_rejected': _any_batch_failed(p),
            'first_approver': (p.first_approver.get_full_name() or p.first_approver.username) if p.first_approver else '',
            # This viewer may sign off stage 1: a finance approver, not the
            # requester, request still awaiting finance.
            'can_approve': bool(is_first and p.status == PaymentRequest.Status.PENDING_FINANCE
                                and p.created_by_id != me.id),
            # The CFO OR a finance approver (Pako / Kago / Legakwa) may clear a
            # signed-off request out of the queue without paying it (expired /
            # paid outside Omni / duplicate) — CFO decision 2026-07-26. A finance
            # approver may NOT clear one they signed off themselves (segregation
            # of duties, CFO 2026-07-26): otherwise one person could sign a
            # request off and then make it vanish before the CFO ever saw it.
            'can_clear':  bool(p.status == PaymentRequest.Status.PENDING_CFO
                               and (is_cfo or (is_first and p.first_approver_id != me.id))),
            'created_at':  p.created_at.isoformat(),
            'created_by':  (p.created_by.get_full_name() or p.created_by.username) if p.created_by else '',
            'task_id':     str(p.task_id) if p.task_id else None,
            'task_status': p.task.status if p.task else None,
            'due_date':    p.due_date.isoformat() if p.due_date else None,
            'loader':      p.inputter or ((p.created_by.get_full_name() or p.created_by.username)
                                          if p.created_by else ''),
            'verifier':    p.verifier or '',
            # Detective-review columns for the register (Kago 2026-08-29). Account
            # numbers are deliberately NOT here — masked in lists, shown in full
            # only on the single-request authorisation screen.
            'first_approved_at': p.first_approved_at.isoformat() if p.first_approved_at else None,
            'rejected_at':       p.rejected_at.isoformat() if p.rejected_at else None,
            'days_to_authorise': (
                (p.first_approved_at - p.created_at).days
                if p.first_approved_at and p.created_at else None),
            # Line-level detail, only when asked for. These fields already exist
            # on the record; the API simply never returned them, so a duplicate
            # sweep had nothing to match on (Manus 2026-08-09).
            **({'lines': _line_rows(p)} if want_lines else {}),
        } for p in _page]
        # Say plainly what was left out, so an empty list is never mistaken for
        # "there is nothing" when it means "you were not shown it".
        scope = ('all requests' if _is_readonly_key_reader(request) or show_all
                 else ('everything except requests still with finance' if is_cfo
                       else 'the finance sign-off queue plus your own' if is_first
                       else 'only requests you raised yourself'))
        payload = {'requests': rows, 'is_cfo': is_cfo, 'is_first_approver': is_first,
                   'showing': scope,
                   'bank_rejected_count': bank_rejected_count,
                   'includes_settled': show_all}
        # One shape, sent to every caller. `register` is kept as an alias so the
        # register screen's existing reader does not change.
        payload['page'] = {
            'total': page_total, 'limit': page_limit, 'offset': page_offset,
            'returned': len(rows),
            'has_more': (page_offset + len(rows)) < page_total,
        }
        if can_register:
            payload['register'] = payload['page']
        return Response(payload)

    # POST — create
    # PAY-WIN-02 window ABOLISHED (CFO 2026-09-02): raising a payment outside the
    # old 08:00-09:15 morning window is no longer blocked — payments can be raised
    # at any time. Instead, a non-CFO who raises off-window is recorded
    # (loaded_off_window), and the count surfaces as a "did not plan the payment
    # load in time" concern in that person's monthly performance feedback
    # (hris.auto_feedback). Nothing here moves money; the CFO is never flagged.
    now_local = timezone.localtime(timezone.now())
    loaded_off_window = (not _is_cfo(me)) and (not _load_window_is_open(now_local))

    # Premium refunds are never hand-keyed here: two open routes to the same
    # refund is how a payment goes out twice — once from the overnight feed,
    # once from whoever did not know it existed (CFO 2026-08-11).
    if (request.data.get('category') or '').strip() in \
            PaymentRequest.IMPORTER_ONLY_CATEGORIES:
        return Response({'detail': (
            'Premium refunds are not raised here. Approve the refund in the Graphite '
            'refund platform and it becomes a payment request automatically '
            'overnight, on the Premium refunds tab.'),
            'control': 'PAY-REFUND-01'}, status=400)

    body = request.data or {}

    # ── Idempotency (CFO 2026-09-14: "a retry, a double click or a re-sent
    # message cannot create the same record twice") ─────────────────────────
    # The client sends this once per submit attempt and repeats it unchanged
    # on any retry of that SAME click. If a request with this key already
    # exists, this call is a retry of an already-successful submit — hand
    # back that same request rather than raising a second one.
    # Scoped to the caller: the key means "my retry of my click", and the value
    # is supplied by the client, so an unscoped read would hand one user another
    # user's payment request back on a colliding key. The uniqueness constraint
    # behind it stays global — a key that belongs to someone else falls through
    # to the IntegrityError path below and is refused, never replayed.
    client_request_id = str(body.get('client_request_id') or '').strip()[:64]
    if client_request_id:
        _replay = PaymentRequest.objects.filter(
            client_request_id=client_request_id,
            created_by=me).select_related('task').first()
        if _replay is not None:
            return Response(_replay_response(_replay), status=200)

    subject = (body.get('subject') or '').strip()
    if not subject:
        return Response({'detail': 'Subject is required.'}, status=400)
    lines = _clean_lines(body.get('line_items'))
    if not lines:
        return Response({'detail': 'Add at least one payment line with an amount.'}, status=400)
    total = sum((ln['amount'] for ln in lines), Decimal('0.00'))
    if total <= 0:
        return Response({'detail': 'Total must be greater than zero.'}, status=400)

    # ── Processing method: Bulk or Individual (Kelvin Kimani spec 2026-09-08) ─
    # One decision by the inputter, recorded on the request so the approver and
    # whoever executes it both know the intent. Bulk is the default, and with a
    # single line the choice is meaningless — bulk and individual are the same
    # one payment — so an unrecognised or absent answer silently becomes Bulk
    # rather than refusing a request over a grouping preference.
    processing_method = (body.get('processing_method') or '').strip().lower()
    if processing_method not in {c for c, _ in PaymentRequest.ProcessingMethod.choices}:
        processing_method = PaymentRequest.ProcessingMethod.BULK
    if not PaymentRequest.show_processing_choice(lines):
        processing_method = PaymentRequest.ProcessingMethod.BULK

    # The build-time assertion the spec demands, as a real guard: "bulk total
    # and the sum of the individual payments must reconcile exactly to TOTAL
    # PAYABLE, or the request does not finalise." `total` is derived from the
    # same lines here, so this can only fire on a genuinely inconsistent pack —
    # which is exactly the point: nothing downstream may assume it, and an amend
    # (which recalculates the total) runs the same check.
    recon = PaymentRequest.reconciliation_error(lines, total, currency=(
        (body.get('currency') or 'BWP').strip().upper()[:3] or 'BWP'))
    if recon:
        return Response({'detail': recon, 'control': 'PAY-RECON-01'}, status=400)

    # 🔴 NO BLOCKER (CFO 2026-09-09): "there should be no blocker — the committee
    # decides and the CFO is notified." Completeness / judgement problems a
    # raiser hits on a real payment (a missing claim number, a first-time payee,
    # a changed or missing bank account, no verifier, no POP address) no longer
    # dead-end the request with a 400/409. Each is collected here and folded into
    # a committee EXCEPTION before the request is created — the raiser is never
    # blocked, the control is still seen by the committee and the CFO, and money
    # never moves from Omni regardless (FNB + the CFO's phone 2-factor is the
    # gate). Structural minimums (a subject, at least one line, a positive total,
    # a valid category) still apply — without them there is no payable
    # instruction to route anywhere.
    soft_reasons: list[str] = []
    # Notes for the approver that hold nothing (CFO master M4, 18-Sep-2026).
    advisory_notes: list[str] = []
    entity = (body.get('entity') or 'Alpha Direct Insurance Company').strip()[:120]
    currency = (body.get('currency') or 'BWP').strip().upper()[:3] or 'BWP'
    category = (body.get('category') or '').strip()
    valid_cats = {c for c, _ in PaymentRequest.Category.choices}
    if category not in valid_cats:
        return Response({'detail': (
            'Say what kind of payment this is: a claims payment, or an '
            'operations payment (supplier, vendor, petty cash or other). It is '
            'no longer guessed from the description.'),
            'control': 'PAY-SUP-01'}, status=400)

    # ── Refund payments: the reference of the money being reversed ───────────
    # B8 (CFO spec 2026-09-13). "A refund with no original payment is not a
    # refund." The two hand-raised refunds must each name the payment they
    # reverse, and this is enforced HERE, on the server, not only in the form:
    # the mobile screen, the Drop Box submit and any direct API call all arrive
    # through this one endpoint, and a control that lives in a React component
    # is not a control. Premium refunds never reach this line — they are
    # importer-only and were refused above (PAY-REFUND-01).
    #
    # A structural minimum, so a hard 400 rather than a committee exception: a
    # refund with nothing to reverse is not an incomplete instruction, it is not
    # an instruction at all. There is nothing for a committee to decide about.
    original_payment_ref = (body.get('original_payment_ref') or '').strip()[:64]
    if category in PaymentRequest.HAND_RAISED_REFUND_CATEGORIES and not original_payment_ref:
        return Response({'detail': (
            'A refund must say which payment it reverses. Give the original '
            'payment reference — the payment request reference, the bank '
            'reference or the receipt number of the money that went out. '
            'Without it the refund cannot be tied back to source, and nothing '
            'stops the same amount being refunded twice.'),
            'control': 'PAY-REFUND-02'}, status=400)
    if category not in PaymentRequest.REFUND_CATEGORIES:
        # Never leave a reversal reference on something that is not a refund.
        original_payment_ref = ''

    # ── Excess refunds: the claim number as well (CFO 2026-09-14) ────────────
    # An excess only exists BECAUSE of a claim — the policyholder paid it on
    # that claim. So a refund of an excess must name the claim, or nobody can
    # verify the refund afterwards: the original payment reference alone proves
    # money went out, not that this excess was ever due back.
    #
    # EXCESS refunds only, deliberately. An erroneous payment has no claim
    # behind it, and a gate on a document that cannot exist never opens.
    # A hard 400 for the same reason as PAY-REFUND-02 above — a refund nobody
    # can verify is not an incomplete instruction, it is not an instruction.
    if category == PaymentRequest.Category.EXCESS_REFUND:
        # .strip() here even though _clean_lines already strips: a control
        # must not depend on a distant helper staying the way it is today.
        missing_claim = [i for i, ln in enumerate(lines, 1)
                         if not str(ln.get('claim_number') or '').strip()]
        if missing_claim:
            where = ', '.join(f'line {i}' for i in missing_claim)
            return Response({'detail': (
                f'An excess refund must say which claim the excess was paid '
                f'on — the claim number is missing on {where}. Add the '
                f'Graphite claim number (e.g. G2026004287). An excess only '
                f'exists because of a claim, and without that number the '
                f'refund cannot be verified later.'),
                'control': 'PAY-REFUND-03'}, status=400)

    # B7 — the audit trail on a copy ("Duplicated from PAY/ADIC/..."). Only kept
    # when it names a request that really exists, so it cannot become a free
    # text field that says anything the caller likes.
    duplicated_from_ref = (body.get('duplicated_from_ref') or '').strip()[:48]
    if duplicated_from_ref and not PaymentRequest.objects.filter(
            ref=duplicated_from_ref).exists():
        duplicated_from_ref = ''

    # ── Claims vs operations (CFO 2026-07-29) ────────────────────────────────
    # Claims are ADIC's alone — it is the licensed insurer; no other group
    # company settles claims. Catch it here so a claims pack can never be
    # raised against the wrong entity and then quietly reclassified.
    claim_payee_type = (body.get('claim_payee_type') or '').strip()
    valid_payee_types = {c for c, _ in PaymentRequest.ClaimPayeeType.choices}
    if category == PaymentRequest.Category.CLAIM:
        if not _resolves_to_adic(entity):
            return Response({'detail': (
                'Claims payments are only valid for Alpha Direct Insurance '
                'Company (ADIC) — it is the licensed insurer. Pick ADIC from the '
                f'entity list, or raise this as an operations payment. ("{entity}" '
                'did not resolve to a known company.)')}, status=400)
        if claim_payee_type not in valid_payee_types:
            return Response({'detail': (
                'Say who is being paid on this claim: the client / policyholder '
                'direct, or a supplier / repairer / service provider. A supplier '
                'being paid through claims payable still needs its invoice '
                'numbers and dates.'), 'control': 'PAY-SUP-01'}, status=400)
        # A claims payment must name the claim it settles, on every line —
        # whoever is being paid. This is how the pack is tied back to the claim
        # in Graphite (CFO 2026-07-29).
        missing = [i for i, ln in enumerate(lines, 1) if not ln['claim_number']]
        if missing:
            where = ', '.join(f'line {i}' for i in missing)
            # No blocker: a missing claim number goes to the committee, not a wall.
            soft_reasons.append(
                f'PAY-CLM-01: claim number missing on {where} — add the Graphite '
                f'claim number (e.g. G2026004287); the committee confirms it.')
    else:
        # Only a claim pack carries these — never leave them on another category
        # where they would read as a fact nobody was asked for. (A user who
        # typed a claim number then switched to operations would otherwise leave
        # a stray one buried in the stored line JSON.)
        claim_payee_type = ''
        # An excess refund is the one non-claim category that legitimately
        # carries a claim number — it was just REQUIRED above (PAY-REFUND-03),
        # so wiping it here would throw away the very fact the gate insisted
        # on and leave the stored pack unverifiable.
        if category != PaymentRequest.Category.EXCESS_REFUND:
            for ln in lines:
                ln['claim_number'] = ''
    opening_raw = body.get('opening_balance')
    opening = _dec(opening_raw) if opening_raw not in (None, '') else None
    due_raw = (body.get('due_date') or '').strip()
    due = None
    if due_raw:
        from datetime import date as _date
        try:
            due = _date.fromisoformat(due_raw)
        except ValueError:
            # No blocker (CFO 2026-09-09): an unreadable date is treated as blank
            # and flagged to the committee, never a dead-end.
            soft_reasons.append(f'PAY-DATE-01: due date "{_no_marker(due_raw)}" is not a '
                                'readable date (YYYY-MM-DD) — the committee confirms it.')

    # ── Supplier terms gate (CFO 2026-07-28, widened 2026-07-29) ─────────────
    # Supplier and vendor payments, plus claim payments going to a repairer or
    # other provider. A pack with a missing invoice date or due date, a due date
    # that undercuts the agreed term, or a pay date ahead of the due date is
    # REFUSED here — it never becomes a task and never reaches an approver.
    # Claims settled direct to the policyholder, petty cash and recurring
    # operational payments are unaffected.
    pay_raw = (body.get('payment_date') or '').strip()
    pay_date = None
    if pay_raw:
        pay_date = _parse_iso(pay_raw)
        if pay_date is None:
            # No blocker: unreadable pay date -> blank + a note for the committee.
            soft_reasons.append(f'PAY-DATE-01: payment date "{_no_marker(pay_raw)}" is not a '
                                'readable date (YYYY-MM-DD) — the committee confirms it.')
    early_reason = (body.get('early_payment_reason') or '').strip()
    funds_moved = bool(body.get('funds_already_moved'))
    # A supplier-terms problem NO LONGER dead-ends the raiser (CFO 2026-09-09:
    # "even if there is a Graphite issue or ANYTHING you should allow and make
    # the committee make the final decision" — Omni never silently blocks a
    # payment). A missing invoice date, a due date that undercuts the term, or
    # an early pay date used to REFUSE the pack at capture (a silent dead-end).
    # It now enters as a committee EXCEPTION: the raiser is not blocked, the
    # committee reads the terms problem and decides, and money never moves from
    # Omni regardless (FNB + the CFO's phone 2-factor is the gate).
    terms_exception_reason = ''
    if _terms_gate_applies(category, claim_payee_type):
        # Left blank means "pay it now", which is what the gate must judge — so
        # default to today HERE and store it, rather than stamping every
        # category with a payment date nobody entered.
        pay_date = pay_date or timezone.localdate()
        err, _meta = _validate_supplier_terms(
            lines, pay_date=pay_date, override_reason=early_reason)
        if err:
            terms_exception_reason = err

    # ── The default wording, by payment type (Finance, 2026-08-20) ───────────
    # Anything typed wins; a blank field takes the template for this payment
    # type. Their rule on who may change it: "Anyone who creates a payment
    # should be able to edit both fields… The control sits at approval, not at
    # capture" — so nothing here restricts editing, and the approval pack shows
    # the final wording.
    bank_type = ((body.get('bank_payment_type') or '').strip()
                 or default_type_for(category, claim_payee_type))
    _tpl = build_defaults(
        payment_type=bank_type,
        line_items=lines,
        payee=(body.get('payee') or '').strip(),
        account_name=(body.get('account_name') or '').strip(),
        policy_number=(body.get('policy_number') or '').strip(),
        refund_type=(body.get('refund_type') or '').strip(),
    )
    _typed_narr = (body.get('bank_narration') or '').strip()
    _typed_ref = (body.get('bank_our_reference') or '').strip()
    # A template that could not find its claim/invoice/policy number would
    # otherwise emit a stub — 'AOL' on its own, identical on every AOL payment
    # and matching nothing. Drop it and let the floor apply; the pack says
    # plainly that the payment number will be used instead.
    _tpl_ok = not _tpl['missing']
    bank_narration = (_typed_narr or (_tpl['narration'] if _tpl_ok else ''))[:140]
    bank_our_reference = (_typed_ref or (_tpl['our_reference'] if _tpl_ok else ''))[:35]

    # ── Bank details are mandatory now (CFO 2026-08-20) ──────────────────────
    # "In the payment request section make the bank account details mandatory
    # going forward." Going forward is the operative word: 18 of the 99 requests
    # already raised have no account number and are left alone. A petty-cash
    # float has no payee account, so it is exempt.
    acct_in   = (body.get('account_number') or '').strip()
    bank_in   = (body.get('bank_name') or '').strip()
    holder_in = (body.get('account_name') or '').strip()
    branch_in = (body.get('branch_code') or '').strip()
    acct_type_in = (body.get('account_type') or '').strip().upper()[:10]
    # Server-side autofill (Fable 5.1 audit 2026-09-02, H4): the payee lookup
    # now hands an ordinary raiser a MASKED account, so the form asks Omni to
    # use the account it already holds for this payee instead of echoing the
    # digits to the browser. Only fills what the raiser left blank or masked.
    if _truthy(body.get('use_known_account')):
        # exact=True: only an EXACT payee match fills the account, so a fuzzy
        # neighbour's real account is never silently attached (Fable 5.1 audit,
        # MEDIUM). A near miss falls through to the normal bank controls.
        _known = last_known_bank((body.get('payee') or '').strip() or holder_in, exact=True)
        if _known:
            if not acct_in or '*' in acct_in:
                acct_in = (_known['account_number'] or '').strip()
            if not bank_in:
                bank_in = (_known['bank_name'] or '').strip()
            if not holder_in:
                holder_in = (_known['account_name'] or '').strip()
            if not branch_in:
                branch_in = (_known['branch_code'] or '').strip()
            if not acct_type_in:
                acct_type_in = (_known['account_type'] or '').strip().upper()[:10]
    # A masked placeholder must never be stored as an account number: it happens
    # if use_known_account was set but no exact history resolved (a lookup/submit
    # race, or a hand-crafted request). Ask for the real digits (Fable 5.1 audit, LOW).
    if '*' in acct_in:
        # No blocker (CFO 2026-09-09): a masked placeholder must never be STORED
        # as an account (that is a data fault), but it must not dead-end the
        # raiser either — blank it and let the missing-bank-details path below
        # send it to the committee to confirm the real digits.
        acct_in = ''
    petty_exempt = (category == PaymentRequest.Category.PETTY_CASH
                    and total <= PETTY_CASH_NO_BANK_MAX_BWP)
    if not petty_exempt:
        missing = [label for label, val in (
            ('the account holder name', holder_in),
            ('the bank', bank_in),
            ('the account number', acct_in),
        ) if not val]
        if missing:
            # No blocker: missing bank details go to the committee, not a wall.
            # (The masked-placeholder guard above stays — storing '*' as an
            # account is a data fault, not a business decision.)
            soft_reasons.append(
                'PAY-BANK-02: bank details incomplete (' + ', '.join(missing) +
                ') — fill them so the payment need not be retyped in FNB; the '
                'committee confirms.')
        # ── Branch code (PAY-BANK-05, CFO 2026-09-17) ───────────────────────
        # FNB Botswana is the only bank we can pay without a branch code (it
        # falls back to the FNB-to-FNB branch 287867). Anyone else needs one or
        # the bank rejects with AC08.
        #
        # This used to check only for a BLANK code, with a substring test
        # (`fnb|first national`) deciding who was exempt. Both halves were
        # wrong, and ten AC08 rejects worth BWP 677,284.93 on 17-Sep-2026 are
        # the evidence:
        #   - the substring matched "FNB SA", so a South African account was
        #     told it needed no branch code;
        #   - nothing at all looked at the SHAPE of a code that WAS given, so
        #     '6700', an 11-digit account number typed into the branch box,
        #     '64967' (a leading zero dropped from 064967), '202-067' and a
        #     bare '-' all went to FNB exactly as typed.
        # fnb.destination_bank holds both rules in one place so the capture
        # screen and the batch builder cannot drift apart.
        #
        # Still a soft reason: it NEVER blocks the raiser. It goes to the
        # committee, who supply the real code (CFO 2026-09-17: "you will never
        # block a payment, if there is a blocker the exception committee kicks
        # in").
        if acct_in:
            _branch_problem = branch_code_problem(bank_in, branch_in)
            if _branch_problem:
                soft_reasons.append(
                    f'PAY-BANK-05: {_no_marker(_branch_problem)} The committee confirms the '
                    'branch code before this goes to the bank.')
            else:
                _branch_note = branch_code_advisory(bank_in, branch_in)
                if _branch_note:
                    advisory_notes.append(f'BRANCH CODE: {_no_marker(_branch_note)}')

    # ── Changing a payee's bank account (CFO 2026-08-20, PAY-BANK-01) ─────────
    # "if a person is changing the bank account details it rejects, saying 'Why
    # are you doing this because you paid this person with another bank
    # account?'" Supplier account substitution is the classic invoice fraud: the
    # name and the amount look right and only the account moved. Refused unless
    # the raiser writes what happened and who confirmed it.
    bank_change_reason = (body.get('bank_change_reason') or '').strip()
    payee_for_bank = (body.get('payee') or '').strip() or holder_in
    warn = bank_change_warning(payee_for_bank, acct_in)
    # EXCEPTION → committee (CFO 2026-09-02). A changed bank account NO LONGER
    # blocks the raiser — never dead-end a payment ("we are not blocking people
    # from entering the payments"). The request is entered and flagged as an
    # exception; a three-of-six committee decides it, the payment proceeds once
    # they approve, and the CFO clears it for his records only. Money never
    # moves here — FNB + the CFO's 2-factor stay the real gate.
    exception_control = (warn.get('control') or 'PAY-BANK-01') if warn else ''
    exception_reason  = (warn.get('detail') or '') if warn else ''

    # ── 🔴 STANDING RULE — a Graphite claim signal NEVER blocks a payment ────
    # (CFO 2026-09-09, hard-coded so no later change quietly re-introduces it.)
    # A signal DERIVED FROM GRAPHITE about a claim — a closed / settled /
    # repudiated status, the reserve, the payoff, or the balance — may NEVER
    # park, hold, block or reject a payment, and must never turn it into a
    # committee EXCEPTION on its own. Graphite posts the loss payment the moment
    # Claims RAISES it, BEFORE FNB has paid anyone, so "Graphite shows it
    # settled" is a FALSE "already paid" for the repairer/supplier still owed
    # the money (Leano Makwapa, 2026-09-08: "it picks up the reserve amount from
    # Graphite and treats the payoff as though the payment has been processed
    # while it hasn't"). Parking it read as a refusal and stalled nearly every
    # claim. It is now attached to the pack as a NOTE the finance approver /
    # committee reads, and the payment flows the normal sign-off path — a human
    # makes the final decision, never a Graphite figure. Genuine controls that
    # DO route to the committee are unrelated to Graphite and stay put: a
    # changed bank account (PAY-BANK-01), an Omni-record duplicate (PAY-DUP-01,
    # matched on Omni's OWN paid rows), premium not received (PAY-PREM-01).
    # Omni never moves money regardless — FNB + the CFO's phone 2-factor is the
    # real gate — so a Graphite note here can only ever inform, never stop.
    graphite_claim_notes: list[str] = []
    if category == PaymentRequest.Category.CLAIM:
        from integrations.claim_insight import claim_exception_reason
        for cn in dict.fromkeys(ln['claim_number'] for ln in lines if ln.get('claim_number')):
            try:
                reason = claim_exception_reason(cn, currency=currency)
            except Exception:  # noqa: BLE001 — a mirror hiccup must never block a payment
                log.warning('PAY-CLAIM-01 Graphite check failed for %s', cn, exc_info=True)
                reason = ''
            if reason:
                graphite_claim_notes.append(reason)

    # PAY-PREM-01 (Kago Tshutlhedi memo v2, 6-Sep-2026, GC 3.B): the premium for the
    # period of loss was not received (red — lapsed, or loss in an unpaid period).
    # The payment does not proceed straight through — it goes to the committee, which
    # cannot release it without the bank-error proof attached (payment_exception_signoff).
    # Amber / green / cannot-assess do not block.
    if category == PaymentRequest.Category.CLAIM:
        from integrations.claim_insight import (PREMIUM_EXCEPTION_CONTROL,
                                                premium_gate_exception_reason)
        prem_reasons = []
        for cn in dict.fromkeys(ln['claim_number'] for ln in lines if ln.get('claim_number')):
            try:
                pr_reason = premium_gate_exception_reason(cn)
            except Exception:  # noqa: BLE001 — a mirror hiccup must never block a payment
                log.warning('PAY-PREM-01 check failed for %s', cn, exc_info=True)
                pr_reason = ''
            if pr_reason:
                prem_reasons.append(pr_reason)
        if prem_reasons:
            exception_control = exception_control or PREMIUM_EXCEPTION_CONTROL
            exception_reason = '\n'.join([r for r in [exception_reason] if r] + prem_reasons)

    # Supplier-terms problem (missing invoice date, due date undercuts the term,
    # early payment) — allow it in as a committee exception instead of refusing
    # the pack (CFO 2026-09-09). The raiser is never dead-ended.
    if terms_exception_reason:
        exception_control = exception_control or 'PAY-SUP-01'
        exception_reason = '\n'.join(
            [r for r in [exception_reason] if r] + [f'Supplier terms: {terms_exception_reason}'])

    # ── First payment to a payee we have never paid (PAY-BANK-03, CFO 2026-09-01)
    # PAY-BANK-01 above can only fire when there IS a known account to differ
    # from, so a brand-new payee sailed through with nothing checking the
    # digits at all. Reading the account straight off the invoice makes that
    # worse — nobody is forced to look. A new payee now needs an explicit tick
    # that the raiser checked the number against the invoice and confirmed the
    # supplier through a channel they already had. The server is the gate; the
    # browser only renders the question.
    # Deliberately strict: only a real JSON true, or an explicit affirmative
    # string, counts as confirmed. A plain bool() would treat the STRING
    # "false" as True (every non-empty string is truthy in Python) and wave the
    # control through — the exact silent bypass this gate exists to prevent.
    _confirm_raw = body.get('new_payee_confirmed')
    new_payee_confirmed = (
        _confirm_raw is True
        or (isinstance(_confirm_raw, str)
            and _confirm_raw.strip().lower() in {'true', 'yes', '1', 'on'})
    )
    first_warn = first_payment_warning(payee_for_bank, acct_in)
    if first_warn and not new_payee_confirmed:
        # No blocker: a first-ever payee no longer has to tick a confirmation to
        # proceed — it goes to the committee, which confirms the account before
        # it moves on. The raiser is not stopped.
        soft_reasons.append(
            f'PAY-BANK-03: first payment Omni has seen to {_no_marker(payee_for_bank)} — the '
            'committee confirms the account against the invoice before it proceeds.')

    # ── Bank cross-check is now a HARD gate (PAY-BANK-04, Finance spec
    # 2026-09-08) ─────────────────────────────────────────────────────────────
    # "Right now that flag is informational only. Change it to a hard gate:
    # when it's showing, the 'Verified by' field … becomes required before the
    # request can proceed, and the bank details entered there must be confirmed
    # against the beneficiary details on the attached supporting document by
    # Finance — not by the original preparer."
    #
    # Two halves, because a preparer typing a colleague's name into a text box
    # is not that colleague confirming anything:
    #   HERE      a verifier must be NAMED, and it may not be the preparer.
    #   SIGN-OFF  Finance positively confirms the details against the attached
    #             document before the payment moves on (PAY-BANK-DOC below).
    # It fires on a first-ever payee AND on a change to the bank, the branch
    # code or the account number of a payee whose account we already hold — the
    # spec's "re-trigger the same first-time-payee verification flow rather than
    # silently overwriting the stored details".
    bank_details_change = bank_details_changed(
        payee_for_bank, bank_name=bank_in, branch_code=branch_in,
        account_number=acct_in)
    verifier_in = (body.get('verifier') or '').strip()
    if first_warn or bank_details_change:
        _why = (bank_details_change['detail'] if bank_details_change
                else f'This is the first payment Omni has seen to {payee_for_bank}.')
        # Entering the payment is NEVER blocked — the CFO's standing rule since
        # 2026-09-02 ("we are not blocking people from entering the payments").
        # A missing verifier is therefore held at SIGN-OFF, not here: the form
        # marks the field required and will not submit without it, and the
        # finance approver refuses a pack that arrives without one. What IS
        # refused here is the one thing no later stage can undo: the preparer
        # naming THEMSELVES as the verifier. That reads on the pack as a second
        # pair of eyes which never existed, and it is exactly what the spec
        # rules out — "by Finance, not by the original preparer".
        _me_names = {normalise_payee(n) for n in (
            me.get_full_name(), me.username,
            (body.get('inputter') or '').strip()) if n}
        _me_names.discard('')
        if verifier_in and normalise_payee(verifier_in) in _me_names:
            # No blocker: naming yourself as the verifier no longer refuses the
            # pack — it goes to the committee flagged that the "second pair of
            # eyes" was the preparer, so a real independent check happens there.
            soft_reasons.append(
                'PAY-BANK-04: the request names its own preparer as the verifier — '
                'the committee provides the independent check on the bank details.')
        # A MISSING verifier is deliberately NOT converted here: the original
        # design already lets it flow to the normal finance sign-off (not a
        # block), where the approver handles it. Routing it to the committee
        # instead would add friction, not remove a blocker.

    # ── Duplicate payment gate (CFO 2026-08-03) ──────────────────────────────
    # PAY-DUP-01. Every line is matched on reference AND amount against every
    # live and paid request, and against the rest of this pack. A hard clash
    # refuses the request outright — it never becomes a task and never reaches an
    # approver — unless the raiser writes why it is not a duplicate. See
    # taskboard/payment_duplicates.py for the ten-request queue that forced this.
    dup = find_duplicates(lines, currency=currency, payee=payee_for_bank)
    # The same hand, the same amount, inside two days — nine of the fifteen
    # paid-twice cases fit that shape and nothing else about them matched.
    dup['hard'] = dup['hard'] + _same_loader_recent_same_amount(
        lines, currency=currency, user=me)
    # A line reloaded off a pack where it is already settled (Leano Makwapa
    # 2026-09-15). The reload button is what makes this stamp available, so the
    # feature carries its own control rather than leaning on the amount match.
    dup['hard'] = dup['hard'] + _lines_copied_from_settled(lines, currency=currency)
    # A hard duplicate is NEVER refused and NEVER overridden by the raiser
    # (CFO 2026-09-04: "even if there is a genuine duplicate the committee can
    # decide to pay"). It is entered as an EXCEPTION: the committee reads which
    # earlier request already carries the money and three of six decide. The
    # 2-Sep refusal, and the free-text override before it (ARCON CRAFTS, Manus
    # 2026-08-09), are both gone — the decision sits with named people, on the
    # record. A bank-account exception on the same request keeps its control
    # (it is the one needing the call-back); the duplicate text is appended.
    if dup['hard']:
        exception_control = exception_control or DUP_CONTROL_CODE
        dup_text = blocking_message(dup['hard'], currency=currency,
                                    total_hard=hard_total(dup['hard']),
                                    context='authorise')
        # Marker so the sign-off re-check can tell a duplicate the committee has
        # already decided from a fresh one — even when the dup RODE on another
        # control (e.g. PAY-SUP-01) and so is not the stored exception_control.
        # Without it, a terms+dup pack bounces committee↔sign-off forever (Fable
        # 5.1 audit 2026-09-09).
        exception_reason = '\n'.join(
            [r for r in [exception_reason] if r] + ['[PAY-DUP-01] ' + dup_text])
    dup_reason = ''
    dup_category = ''

    # ── POP recipient per line (Finance spec 2026-09-08) ─────────────────────
    # Who receives the proof of payment for each line, and whether that is
    # somebody we already hold. The form makes the column required; a blank
    # here falls back to Accounts — the same standing default the POP email
    # field on /payments/new has carried since CFO 2026-08-22 ("Defaults to
    # Accounts — change it to send the POP to the payee"). Refusing a blank
    # outright would dead-end every Drop Box draft, which is read off an
    # invoice and cannot know a recipient. A POP therefore always has somewhere
    # to go, and the risky case — an address we do not hold — is the one that
    # is gated, at sign-off, by a second person.
    _pop_accounts = pop_accounts_default()
    _pop_off_list = []
    for _i, _ln in enumerate(lines, start=1):
        if not _ln.get('pop_recipient_email') or not pop_email_valid(
                _ln['pop_recipient_email']):
            # No blocker: a malformed POP address no longer refuses the pack — it
            # falls back to Accounts (the standing default) and is noted, so the
            # raiser is never stopped over where the receipt goes.
            if _ln.get('pop_recipient_email'):
                soft_reasons.append(
                    f'PAY-POP-01: line {_i} proof-of-payment address '
                    f'"{_no_marker(_ln["pop_recipient_email"])}" is not valid — defaulted to '
                    'Accounts; set the right address if the payee should get it.')
            _ln['pop_recipient_name'] = _pop_accounts['name']
            _ln['pop_recipient_email'] = _pop_accounts['email']
        _options = pop_linked_recipients(claim_number=_ln.get('claim_number') or '',
                                         payee=payee_for_bank)
        _ln['pop_recipient_source'] = pop_classify(
            _ln.get('pop_recipient_name') or '', _ln['pop_recipient_email'], _options)
        if _ln['pop_recipient_source'] == POP_SOURCE_OFF_LIST:
            _pop_off_list.append({'line': _i, 'name': _ln['pop_recipient_name'],
                                  'email': _ln['pop_recipient_email']})

    # Control 4: a line that was REJECTED and is now back goes to the CFO, not to
    # the approver who rejected it. BOBLIN COMPUTERS G2026004718 was rejected on
    # 4 August and then paid on the 5th, 7th and 8th (Manus 2026-08-09).
    repeat_after_rejection = _lines_previously_rejected(lines, currency=currency)
    if not dup['hard']:
        # Never keep a reason nobody needed — it would read on the pack as if a
        # duplicate had been waved through when none was found.
        dup_reason = ''

    pr_data = {
        # 'ref' is allocated inside the atomic retry loop below (unique column).
        'entity':         entity,
        'category':       category,
        'client_request_id': client_request_id,
        'claim_payee_type': claim_payee_type,
        'original_payment_ref': original_payment_ref,
        'duplicated_from_ref': duplicated_from_ref,
        'currency':       currency,
        'subject':        subject[:200],
        'processing_method': processing_method,
        'payee':          (body.get('payee') or '').strip()[:200],
        'line_items':     lines,
        'total':          total,
        'account_name':   holder_in[:120],
        'account_number': acct_in[:64],
        'bank_name':      bank_in[:120],
        # Needed to pay it without retyping in FNB (CFO 2026-08-20). A branch is
        # mandatory for FNB and only defaultable for an FNB-to-FNB payee.
        'branch_code':    branch_in[:20],
        # PAY-BANK-03: stamp the new-payee confirmation into the same audit
        # field that carries the PAY-BANK-01 explanation, so "who waved this
        # first-time account through" is answerable later from the record
        # itself. Only written when the control actually fired.
        'bank_change_reason': (
            bank_change_reason[:2000] if bank_change_reason
            else ('PAY-BANK-03: first payment to this payee — raiser confirmed '
                  'the account number against the invoice and verified the '
                  'supplier independently.' if first_warn else '')
        ),
        'bank_payment_type': bank_type[:20],
        'bank_narration': bank_narration,
        'bank_our_reference': bank_our_reference,
        'account_type':   acct_type_in,
        'opening_balance': opening,
        'due_date':       due,
        'payment_date':   pay_date,
        'early_payment_reason': early_reason,
        'funds_already_moved':  funds_moved,
        'inputter':       (body.get('inputter') or (me.get_full_name() or me.username)).strip()[:160],
        'verifier':       (body.get('verifier') or '').strip()[:160],
        'duplicate_override_reason': dup_reason[:2000],
        'duplicate_override_category': dup_category if dup['hard'] else '',
        # Store the hard clashes AND the soft warnings — the near misses are what
        # an approver wants to see even when nothing blocked.
        'duplicate_matches': (dup['hard'] + dup['soft']) if (dup['hard'] or dup['soft']) else [],
    }
    # Two-stage authorisation (CFO 2026-07-23): the request first goes to a
    # finance approver (Pako / Kago / Legakwa) for sign-off, and only reaches
    # the CFO once one of them approves. Pick the approver — the requester may
    # nominate one (never themselves, SoD); otherwise the first available.
    approvers = list(_first_approvers().exclude(id=me.id).order_by('first_name', 'id'))
    if not approvers:
        return Response({'detail': 'No finance approver (Pako / Kago / Legakwa) is configured to sign off payments.'},
                        status=500)
    nominated = None
    aid = str(body.get('approver_id') or '').strip()
    if aid:
        nominated = next((u for u in approvers if str(u.id) == aid), None)
        if nominated is None:
            return Response({'detail': 'Choose a finance approver (Pako, Kago or Legakwa) from the list.'}, status=400)
    first_assignee = nominated or approvers[0]

    # 🔴 Fold the no-blocker completeness reasons into a committee exception
    # (CFO 2026-09-09) — the raiser is never dead-ended; the committee and the
    # CFO see every point and make the final decision. The first reason's own
    # control (e.g. PAY-CLM-01) becomes the stored code so the raiser message
    # and the committee pack name it correctly.
    if soft_reasons:
        first_ctrl = (soft_reasons[0].split(':', 1)[0].strip() or 'PAY-CHK-01')[:16]
        exception_control = exception_control or first_ctrl
        exception_reason = '\n'.join([r for r in [exception_reason] if r] + soft_reasons)

    # The covering summary is a slow LLM call — do it ONCE, up front, before the
    # ref is allocated (it doesn't depend on the ref).
    summary = _ai_summary(pr_data)

    # The Graphite claim note (CFO 2026-09-09) — informational only, shown to
    # the approver on BOTH the normal and the exception pack. It never sets an
    # exception and never holds the payment; the human decides with it in view.
    graphite_note_block = ''
    if graphite_claim_notes:
        graphite_note_block = (
            '\n\nGRAPHITE NOTE (information for the approver — NOT a block, CFO '
            '2026-09-09):\n' + '\n'.join(graphite_claim_notes)
            + '\nThis payment is ALLOWED through on the normal path — a Graphite '
              'figure never blocks a payment. You make the final decision with '
              'this in front of you.')

    if advisory_notes:
        graphite_note_block += (
            '\n\nNOTE FOR THE APPROVER (advisory only - NOT a hold):\n'
            + '\n'.join(advisory_notes))

    # Allocate the reference and create task + PaymentRequest atomically. `ref`
    # is a count()+1 on a unique column, so two concurrent submits for the same
    # entity/day can collide — retry on IntegrityError so neither the task nor
    # the PR is ever left orphaned (both roll back together on collision).
    from django.db import IntegrityError, transaction
    task = pr = None
    for _attempt in range(6):
        pr_data['ref'] = _next_ref(entity)
        # The POP heading, stamped as at submission (Kelvin Kimani spec
        # 2026-09-08). Inside the retry loop because the fallback is the ref,
        # and the ref is only known here. A draft submitted through
        # dropbox_views.submit_draft comes back through this same endpoint, so
        # that path is covered by this one line too.
        pr_data['pop_subject'] = PaymentRequest.pop_subject_at_submission(
            subject, pr_data['ref'])
        # line_items is a JSONField — Decimal isn't JSON-serializable, so store
        # amounts as strings (exact precision). total/opening_balance are
        # DecimalFields and serialize natively.
        save_data = {**pr_data,
                     'line_items': [{**ln, 'amount': str(ln['amount'])} for ln in lines]}
        try:
            with transaction.atomic():
                is_exception = bool(exception_control)
                if is_exception:
                    task_prefix = 'Payment exception — committee decision needed'
                    body_txt = (summary + '\n\n' + _render_plaintext(pr_data)
                                + f'\n\nEXCEPTION ({exception_control}): {exception_reason}'
                                + graphite_note_block
                                + '\n\nCOMMITTEE: three of the six-member committee decide this in '
                                  'Omni → Payment Requests → Exceptions. The payment is NOT blocked; '
                                  'it proceeds once the committee approves. The CFO clears it for '
                                  'his records only — his sign-off never holds the payment up.')
                else:
                    task_prefix = 'Payment authorisation (finance sign-off)'
                    body_txt = (summary + '\n\n' + _render_plaintext(pr_data)
                                + graphite_note_block
                                + '\n\nFINANCE SIGN-OFF: approve or reject in Omni → '
                                  'Payment Requests. It reaches the CFO only once approved.')
                task = OmniTask.objects.create(
                    assigner=me, assignee=first_assignee,
                    title=_task_title(entity, category, currency, total,
                                      prefix=task_prefix),
                    body=body_txt[:5000],
                    priority=OmniTask.Priority.HIGH,
                    status=OmniTask.Status.PENDING,
                    due_at=_approval_task_due(due),
                    source='payment_request',
                )
                pr = PaymentRequest.objects.create(
                    created_by=me, task=task, summary=summary,
                    status=(PaymentRequest.Status.EXCEPTION if is_exception
                            else PaymentRequest.Status.PENDING_FINANCE),
                    loaded_off_window=loaded_off_window,
                    formatted_html=_render_html(pr_data),
                    exception_control=exception_control,
                    exception_reason=exception_reason,
                    exception_raised_at=(timezone.now() if is_exception else None),
                    **save_data,
                )
            break
        except IntegrityError:
            task = pr = None
            if client_request_id and PaymentRequest.objects.filter(
                    client_request_id=client_request_id).exists():
                # Lost the race to a concurrent submit of the SAME attempt
                # (the classic double-click / retried-request race this key
                # exists to close). That other transaction is the real
                # request; stop retrying with a new ref — it would never be
                # this collision's cause and would just waste every attempt.
                break
            continue
    if pr is None:
        if client_request_id:
            _replay = PaymentRequest.objects.filter(
                client_request_id=client_request_id,
                created_by=me).select_related('task').first()
            if _replay is not None:
                return Response(_replay_response(_replay), status=200)
        return Response({'detail': 'Could not allocate a unique reference — please retry.'},
                        status=409)
    # Tell the finance approver by email. AFTER the transaction commits, so a
    # rolled-back ref collision can never send mail about a request that
    # doesn't exist.
    if exception_control:
        _email_committee_exception(pr, pr_data,
                                   raised_by=me.get_full_name() or me.username)
        # 🔴 Amber CFO alert on EVERY exception (CFO 2026-09-09): a task on his
        # Omni account + an email, "research before paying". Best-effort.
        _alert_cfo_research(pr, pr_data,
                            raised_by=me.get_full_name() or me.username)
    else:
        _email_authorisation(task, pr_data, stage='finance', summary=summary,
                             raised_by=me.get_full_name() or me.username)
    resp = {'id': str(pr.id), 'ref': pr.ref, 'task_id': str(task.id),
            'status': pr.status,
            'assigned_to': first_assignee.get_full_name() or first_assignee.username}
    if exception_control:
        # The POP the raiser sees — inform, never a silent fail (the Bontle lesson).
        _what = {
            'PAY-CLAIM-01': 'a claim Graphite already shows as settled',
            'PAY-PREM-01': ('the premium for the period of loss was not received '
                            '(GC 3.B) — attach the bank-error proof for the committee'),
            'PAY-SUP-01': ('a supplier-terms point (invoice date / due date / early '
                           'payment) — you are not blocked, the committee decides'),
            'PAY-CLM-01': 'a claim number still to confirm — the committee decides',
            'PAY-BANK-02': 'bank details still to complete — the committee decides',
            'PAY-BANK-03': 'a first-time payee to confirm — the committee decides',
            'PAY-BANK-04': 'a beneficiary verification point — the committee decides',
            'PAY-BANK-05': ('the branch code — FNB rejects a payment whose branch code '
                            'is missing or the wrong shape; the committee confirms it'),
            'PAY-POP-01': 'the proof-of-payment address — the committee decides',
            'PAY-DATE-01': 'a date to confirm — the committee decides',
            DUP_CONTROL_CODE: 'a possible duplicate of an earlier request',
        }.get(exception_control, 'a point for the committee to decide')
        resp['exception'] = {
            'control': exception_control,
            'message': (f'This is an exception ({_what}). Your payment is '
                        'entered and has gone to the committee to decide — you are not '
                        'blocked. You will be told the moment they decide.'),
        }
    elif graphite_claim_notes:
        # A Graphite "already settled" note on a payment that went through the
        # normal path — reassure the raiser it is NOT blocked (Leano Makwapa).
        resp['note'] = (
            'This claim shows as already settled in Graphite, but that is just '
            'Claims posting it — it does not mean the money has left. Your '
            'payment is entered and is on the normal finance sign-off path; the '
            'approver confirms it. You are not blocked.')
    if advisory_notes:
        resp['advisory'] = advisory_notes
    return Response(resp, status=201)


def _override_row(o):
    u = o.requested_by
    d = o.decided_by
    return {
        'id': str(o.id),
        'requested_by': (u.get_full_name() or u.username) if u else '—',
        'requested_by_email': getattr(u, 'email', '') or '',
        'reason': o.reason,
        'for_date': o.for_date.isoformat(),
        'status': o.status,
        'requested_at': o.created_at.isoformat(),
        'decided_by': (d.get_full_name() or d.username) if d else None,
        'decided_at': o.decided_at.isoformat() if o.decided_at else None,
        'decision_note': o.decision_note or '',
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def payment_load_override(request):
    """PAY-WIN-02 (CFO 2026-09-01) — the place staff ask the CFO for permission
    to LOAD a payment outside the 08:00–09:15 morning window, and the CFO's
    approval queue + record of who keeps loading off-window.

    GET  → the CFO (or a read-only register key) sees the pending queue, and the
           whole recent record with ?all=1. Anyone else sees their own requests.
           Always returns the current window state so the UI can show the box.
    POST → the signed-in user asks to load now. body {reason}. One live request
           per person per day: an existing pending/approved row for today is
           returned as-is rather than duplicated.
    """
    me = request.user
    now_local = timezone.localtime(timezone.now())
    today = now_local.date()
    open_t, close_t = _load_window_times()
    window = {
        'open': f'{open_t:%H:%M}', 'close': f'{close_t:%H:%M}',
        # PAY-WIN-02 window ABOLISHED (CFO 2026-09-02): payments raise at any time
        # now — nothing blocks an off-window raise. `abolished` is the flag callers
        # should read; open / exempt / has_override are still reported HONESTLY (a
        # read-only-key reader must not be handed a false 'has_override'). The
        # off-window count that feeds a raiser's monthly feedback is computed on the
        # CREATE leg, independently of this display payload.
        'is_open': _load_window_is_open(now_local),
        'abolished': True,
        'exempt': _is_cfo(me),
        'has_override': _has_approved_load_override(me, today),
    }

    if request.method == 'GET':
        is_cfo = _is_cfo(me)
        if is_cfo or _is_readonly_key_reader(request):
            show_all = str(request.query_params.get('all', '')).strip() == '1'
            qs = PaymentLoadOverride.objects.select_related('requested_by', 'decided_by')
            if not show_all:
                qs = qs.filter(status=PaymentLoadOverride.Status.PENDING)
            else:
                from datetime import timedelta as _td
                qs = qs.filter(for_date__gte=today - _td(days=30))
            rows = [_override_row(o) for o in qs[:500]]
            pending_count = PaymentLoadOverride.objects.filter(
                status=PaymentLoadOverride.Status.PENDING).count()
            return Response({'overrides': rows, 'is_cfo': True,
                             'pending_count': pending_count, 'window': window})
        qs = PaymentLoadOverride.objects.filter(requested_by=me).select_related(
            'requested_by', 'decided_by')[:100]
        return Response({'overrides': [_override_row(o) for o in qs],
                         'is_cfo': False, 'window': window})

    # POST — retired 2026-09-02: the PAY-WIN-02 window is abolished, so there is
    # nothing to override. A stale browser tab or a direct API call gets a clear
    # 400 rather than creating a dead override row and emailing the CFO about a
    # permission that no longer exists. An off-window raise is recorded against the
    # raiser's monthly performance feedback instead (see the create leg).
    return Response({
        'detail': 'Payments can be loaded at any time now — the morning window '
                  'was abolished, so there is no approval to request.',
        'abolished': True, 'window': window,
    }, status=400)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_load_override_decide(request, pk):
    """POST /payment-requests/load-override/<id>/decide/ body {decision, note}
    CFO only. approve → that user may load for the rest of today; decline →
    recorded and refused. Either way the row stays as the audit trail."""
    from django.utils import timezone as _tz
    me = request.user
    if not _is_cfo(me):
        return Response({'detail': 'Only the CFO can decide a load override.'},
                        status=403)
    o = PaymentLoadOverride.objects.filter(pk=pk).select_related('requested_by').first()
    if o is None:
        return Response({'detail': 'Override request not found.'}, status=404)
    decision = (request.data.get('decision') or '').strip().lower()
    if decision not in ('approve', 'decline'):
        return Response({'detail': 'decision must be "approve" or "decline".'},
                        status=400)
    if o.status != PaymentLoadOverride.Status.PENDING:
        return Response({'detail': f'This request was already {o.status}.',
                         'override': _override_row(o)}, status=409)
    o.status = (PaymentLoadOverride.Status.APPROVED if decision == 'approve'
                else PaymentLoadOverride.Status.DECLINED)
    o.decided_by = me
    o.decided_at = _tz.now()
    o.decision_note = (request.data.get('note') or '').strip()
    o.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note',
                          'updated_at'])
    return Response({'override': _override_row(o)}, status=200)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_request_decide(request, req_id):
    """POST /payment-requests/<id>/decide/  body {decision: approve|reject, notes}

    Stage-1 finance sign-off (CFO 2026-07-23). A finance approver
    (Pako / Kago / Legakwa) may act, and never on a request they raised (SoD).
      - approve → status PENDING_CFO, a CFO authorisation task is created and
        the request now appears in the CFO's view.
      - reject  → status REJECTED (a reason is required); no CFO task is made.

    Reject is also allowed AFTER sign-off, while the request is PENDING_CFO, by
    the CFO or any finance approver (CFO 2026-09-01) — the CFO stage previously
    offered only "Clear", so a payment could not be turned down outright.
    """
    from django.utils import timezone as _tz
    from django.db import transaction

    me = request.user
    p = (PaymentRequest.objects.select_related('task', 'created_by').filter(pk=req_id).first())
    if p is None:
        return Response({'detail': 'Payment request not found.'}, status=404)
    cfo = _cfo_user()
    is_cfo = bool(me.is_superuser or (cfo and me.id == cfo.id))
    is_first = _is_first_approver(me)

    body = request.data or {}
    decision = (body.get('decision') or '').strip().lower()
    notes = (body.get('notes') or '').strip()
    if decision not in ('approve', 'reject'):
        return Response({'detail': 'decision must be approve or reject.'}, status=400)

    # Who may act, and at which stage:
    #  - a finance approver signs off (approve/reject) a request still awaiting
    #    finance, never one they raised (segregation of duties);
    #  - the CFO OR a finance approver (Pako / Kago / Legakwa) may REJECT a
    #    request that has already reached the CFO (PENDING_CFO). Until now that
    #    stage offered only "Clear"; a straight Reject was missing, so a payment
    #    could not be turned down outright once it passed sign-off (CFO 2026-09-01).
    finance_stage = bool(is_first and p.status == PaymentRequest.Status.PENDING_FINANCE)
    # A finance approver may not reject a pack they themselves signed off — the
    # same own-signoff bar payment_request_clear applies (Fable 5.1 audit
    # 2026-09-02, M3). The CFO may.
    if (decision == 'reject' and is_first and not is_cfo
            and p.status == PaymentRequest.Status.PENDING_CFO
            and p.first_approver_id == me.id):
        return Response({'detail': 'You signed this request off, so you cannot also reject it. '
                                   'Ask the CFO or another finance approver.'}, status=403)
    late_reject   = bool(decision == 'reject' and (is_cfo or is_first)
                         and p.status == PaymentRequest.Status.PENDING_CFO)
    if not (finance_stage or late_reject):
        if not (is_first or is_cfo):
            return Response({'detail': 'Only a finance approver (Pako, Kago or Legakwa) or the CFO can decide a payment request.'},
                            status=403)
        return Response({'detail': f'This request is {_status_label(p.status)} — it cannot be decided at this stage.'},
                        status=409)
    if finance_stage and p.created_by_id == me.id:
        return Response({'detail': 'You cannot sign off a payment request you raised (segregation of duties).'},
                        status=403)

    fin_task = p.task
    now = _tz.now()

    if decision == 'reject':
        if not notes:
            return Response({'detail': 'Please give a reason when rejecting a payment request.'}, status=400)
        with transaction.atomic():
            p.status = PaymentRequest.Status.REJECTED
            p.rejected_by = me
            p.rejected_at = now
            p.decision_notes = notes[:2000]
            p.save(update_fields=['status', 'rejected_by', 'rejected_at', 'decision_notes', 'updated_at'])
            if fin_task and fin_task.status != OmniTask.Status.CANCELLED:
                fin_task.status = OmniTask.Status.CANCELLED
                fin_task.completed_at = now
                fin_task.save(update_fields=['status', 'completed_at', 'updated_at'])
        return Response({'status': p.status, 'status_label': _status_label(p.status)})

    # ── Duplicate re-check at sign-off (CFO 2026-08-03) ──────────────────────
    # PAY-DUP-01 bites again here, not only at creation, for two reasons. First,
    # the requests already in the queue when this control shipped were never
    # checked — ten of them, BWP 219,600.10 of duplication. Second, two requests
    # raised minutes apart both pass creation (neither exists yet when the other
    # is checked) and only collide once one of them is signed off. The sign-off
    # is the last point before the money reaches the CFO, so it is checked here
    # against everything live and paid.
    if p.exception_decision == 'approve' and (
            p.exception_control == DUP_CONTROL_CODE
            or any(ln.startswith('[PAY-DUP-01] ')
                   for ln in (p.exception_reason or '').splitlines())):
        # The committee already decided this duplicate may be paid (CFO
        # 2026-09-04). Re-finding the same clash here would undo their decision —
        # and where the duplicate rode on another control (PAY-SUP-01 etc.) the
        # re-check would bounce the pack back to the committee forever (Fable 5.1
        # audit 2026-09-09). The marker catches that case.
        recheck = {'hard': [], 'soft': []}
    else:
        recheck = find_duplicates(p.line_items or [], currency=p.currency,
                                  exclude_pk=p.pk, payee=p.payee or '')
    if recheck['hard']:
        # A duplicate found only now (a twin raised minutes apart, or a request
        # that predates the control) is not refused either — it goes to the
        # committee like one caught at creation (CFO 2026-09-04). The finance
        # approver is told where it went; the committee gets the pack.
        # context='authorise': names the other request and what is available on
        # this screen, never the raiser's form (the 3 Aug 2026 misdirection).
        dup_text = blocking_message(recheck['hard'], currency=p.currency,
                                    total_hard=hard_total(recheck['hard']),
                                    context='authorise')
        p.status = PaymentRequest.Status.EXCEPTION
        p.exception_control = p.exception_control or DUP_CONTROL_CODE
        p.exception_reason = '\n'.join([r for r in [p.exception_reason or ''] if r] + [dup_text])
        p.exception_raised_at = now
        p.exception_decision = ''
        p.save(update_fields=['status', 'exception_control', 'exception_reason',
                              'exception_raised_at', 'exception_decision', 'updated_at'])
        _email_committee_exception(
            p, {'payee': p.payee, 'subject': p.subject, 'currency': p.currency},
            raised_by=(p.created_by.get_full_name() or p.created_by.username) if p.created_by else '')
        return Response({
            'status': p.status, 'status_label': _status_label(p.status),
            'control': DUP_CONTROL_CODE,
            'duplicates': recheck['hard'],
            'exception': {
                'control': DUP_CONTROL_CODE,
                'message': ('This request repeats lines already raised or paid. It has gone '
                            'to the payment committee to decide — nothing is blocked, and '
                            'you will be told when they decide.'),
            },
        })

    # ── Changed-account acknowledgement at sign-off (Kago 2026-08-29) ─────────
    # Kago's acceptance criterion: "A changed beneficiary account triggers an
    # alert that cannot be bypassed without acknowledgement, and the
    # acknowledgement is logged." So a CHANGED account (PAY-BANK-01) is a hard
    # gate here — the finance approver must positively acknowledge it before the
    # payment reaches the CFO. First-payment and holder-name mismatch are shown
    # as warnings on the authorisation screen (the detail `bank` block) but do
    # not hard-block, to avoid friction on every genuinely new supplier. Same
    # deterministic helper as the create-time control; dual control untouched.
    from .payee_bank_history import bank_change_warning as _bcw
    _change = _bcw(p.payee, p.account_number, exclude_pk=p.pk)
    if _change:
        _ack = body.get('bank_ack')
        _acked = _ack is True or str(_ack).strip().lower() in ('1', 'true', 'yes', 'on')
        if not _acked:
            return Response({
                'detail': ('This payee was previously paid into a different '
                           'account. Confirm you have checked and accept the new '
                           'account before signing this payment off.'),
                'control': 'PAY-BANK-ACK',
                'bank': {'change': _change},
            }, status=409)
        # Record the acknowledgement in the decision notes so it is on the trail.
        _ack_note = f'Bank-change acknowledged at sign-off (PAY-BANK-ACK): new account ends {_change["new_account_tail"]}.'
        notes = (notes + ' | ' + _ack_note).strip(' |') if notes else _ack_note

    # ── Off-list POP recipient needs a second approver (Finance spec 2026-09-08)
    # "If the value entered isn't one of the existing linked contacts, require a
    # second approver to clear it." The finance approver IS that second person —
    # they may never sign off a request they raised (segregation of duties is
    # enforced above), so this tick can only ever come from somebody other than
    # the raiser. Same shape as PAY-BANK-ACK immediately above: an alert that
    # cannot be bypassed without an acknowledgement, and the acknowledgement is
    # logged. A proof of payment naming an address nobody holds is how a payment
    # confirmation reaches the wrong hands.
    _pop_off = pop_off_list_lines(p.line_items or [], payee=p.payee or '')
    if _pop_off:
        _pop_ack = body.get('pop_ack')
        _pop_acked = (_pop_ack is True
                      or str(_pop_ack).strip().lower() in ('1', 'true', 'yes', 'on'))
        if not _pop_acked:
            _which = '; '.join(f'line {o["line"]} → {o["email"]}' for o in _pop_off[:6])
            return Response({
                'detail': ('The proof of payment on this request goes to an address '
                           'Omni does not hold for this claim or payee '
                           f'({_which}). Check it is right, then confirm it before '
                           'signing this payment off.'),
                'control': 'PAY-POP-ACK',
                'pop_off_list': _pop_off,
            }, status=409)
        _pop_note = ('Off-list POP recipient accepted at sign-off (PAY-POP-ACK): '
                     + ', '.join(o['email'] for o in _pop_off[:6]) + '.')
        notes = (notes + ' | ' + _pop_note).strip(' |') if notes else _pop_note

    # ── Finance confirms the bank details against the document (PAY-BANK-DOC,
    # Finance spec 2026-09-08) ────────────────────────────────────────────────
    # "the bank details entered there must be confirmed against the beneficiary
    # details on the attached supporting document by Finance — not by the
    # original preparer. If the two don't match, block submission."
    #
    # The named verifier is captured at creation (PAY-BANK-04); this is the half
    # that makes it real, because only a person acting HERE can be relied on to
    # have looked. The finance approver signing off is never the raiser
    # (segregation of duties is enforced above), so this is Finance and not the
    # preparer by construction. A first-ever payee, or changed bank details, and
    # the sign-off does not pass without it — and there must be a document to
    # check against, or the confirmation is a tick over nothing.
    # SCOPE, and it is deliberate: this gate fires on CHANGED bank details for a
    # payee whose account Omni already holds — the spec's "rather than silently
    # overwriting the stored details". It does NOT also hard-gate every
    # first-ever payee at sign-off. PAY-BANK-03 already stops a brand-new payee
    # at creation with an explicit confirmation, so the incremental control there
    # is small, while gating it here would rewrite the sign-off path for every
    # new supplier — a change of that size is the CFO's to authorise, not one to
    # slip in behind a cross-check. Flagged in the handover; one line to widen
    # (add `or _first_doc`) once he says so.
    _details_doc = bank_details_changed(
        p.payee, bank_name=p.bank_name, branch_code=p.branch_code,
        account_number=p.account_number, exclude_pk=p.pk)
    if _details_doc:
        # "Verified by … becomes required before the request can proceed." It
        # proceeds no further than here: a pack with the flag showing and nobody
        # named as verifier goes back to the raiser rather than on to the CFO.
        if not (p.verifier or '').strip():
            return Response({
                'detail': (_details_doc['detail']
                           + '\n\nNobody is named as "Verified by" on this request, '
                             'so there is no record of who checked the bank details. '
                             'Reject it back to the raiser to name the person in '
                             'Finance who verified them.'),
                'control': 'PAY-BANK-04',
                'needs_verifier': True,
            }, status=409)
        _doc_ack = body.get('bank_doc_ack')
        _doc_acked = (_doc_ack is True
                      or str(_doc_ack).strip().lower() in ('1', 'true', 'yes', 'on'))
        _has_doc = p.attachments.exists()
        if not _has_doc:
            return Response({
                'detail': ('There is no supporting document on this request to check '
                           'the beneficiary bank details against. Attach the invoice '
                           'or letterhead showing the account, then confirm the '
                           'details match before signing it off.'),
                'control': 'PAY-BANK-DOC',
                'reason': _details_doc['detail'],
                'needs_document': True,
            }, status=409)
        if not _doc_acked:
            return Response({
                'detail': (_details_doc['detail']
                           + '\n\nConfirm the bank name, branch code and account '
                             'number on this request match the beneficiary details on '
                             'the attached document. If they do not match, reject the '
                             'request — do not sign it off.'),
                'control': 'PAY-BANK-DOC',
                'reason': _details_doc['detail'],
                'needs_document': False,
            }, status=409)
        _doc_note = ('Bank details confirmed against the supporting document at '
                     'sign-off (PAY-BANK-DOC)'
                     + f'; changed: {", ".join(_details_doc["fields"])}.')
        notes = (notes + ' | ' + _doc_note).strip(' |') if notes else _doc_note

    # approve → hand to the CFO
    cfo = _cfo_user()
    if cfo is None:
        return Response({'detail': 'No CFO / approver account configured.'}, status=500)
    pr_dict = _pr_public_dict(p)
    with transaction.atomic():
        # F2: the status check above read an unlocked row, so a double-click
        # could put two approvals through — two CFO tasks, and now two FNB
        # loads of the same payment into the CFO's phone queue. Re-read under a
        # row lock and re-check before transitioning; the loser backs out.
        locked = (PaymentRequest.objects.select_for_update()
                  .filter(pk=p.pk).first())
        if locked is None or locked.status != PaymentRequest.Status.PENDING_FINANCE:
            return Response(
                {'detail': 'This request has already been signed off.'},
                status=409)
        cfo_task = OmniTask.objects.create(
            assigner=me, assignee=cfo,
            title=_task_title(p.entity, p.category, p.currency, p.total),
            body=((p.summary + '\n\n' if p.summary else '')
                  + _render_plaintext(pr_dict)
                  + f'\n\nFinance sign-off by {me.get_full_name() or me.username}.')[:5000],
            priority=OmniTask.Priority.HIGH,
            status=OmniTask.Status.PENDING,
            due_at=_approval_task_due(p.due_date),
            source='payment_request',
        )
        p.status = PaymentRequest.Status.PENDING_CFO
        p.first_approver = me
        p.first_approved_at = now
        if notes:
            p.decision_notes = notes[:2000]
        p.task = cfo_task
        p.save(update_fields=['status', 'first_approver', 'first_approved_at',
                              'decision_notes', 'task', 'updated_at'])
        if fin_task and fin_task.id != cfo_task.id and fin_task.status != OmniTask.Status.DONE:
            fin_task.status = OmniTask.Status.DONE
            fin_task.completed_at = now
            fin_task.save(update_fields=['status', 'completed_at', 'updated_at'])
    # Load it into FNB so nobody types the same payment a second time (CFO
    # 2026-08-20). Deliberately AFTER the commit and deliberately unable to
    # raise: a bank problem must not cost finance their sign-off. The money does
    # not move here — it lands in the CFO's FNB queue and he authorises it on
    # his phone.
    #
    # The releaser is `me`, the finance approver, while the payment is created by
    # the person who RAISED the request. Since a request cannot be signed off by
    # its own raiser, the two-person release rule is satisfied by the workflow
    # itself rather than by an exemption.
    from .fnb_autoload import load_request_to_fnb
    fnb = load_request_to_fnb(p, me)

    # Tell the CFO by email that a signed-off payment is now waiting on him
    # (CFO 2026-07-29). After the commit, so a failed sign-off never mails.
    _email_authorisation(cfo_task, pr_dict, stage='cfo', summary=p.summary or '',
                         raised_by=me.get_full_name() or me.username)
    return Response({'status': p.status, 'status_label': _status_label(p.status),
                     'cfo_task_id': str(cfo_task.id),
                     'fnb_loaded': fnb['loaded'],
                     'fnb_reason': fnb['reason']})


def _notify_lines_sent_back(p, cfo, action, indexes, note):
    """Best-effort: tell the raiser (and the finance approver) which lines the CFO
    held or rejected, and why. A held/rejected line nobody is told about is a note
    to himself (same lesson as the payment-comment email). Never raises."""
    try:
        from django.conf import settings
        from django.utils.html import escape
        people = [p.created_by, p.first_approver]
        seen, to = set(), []
        for who in people:
            addr = (getattr(who, 'email', '') or '').strip()
            if addr and addr.lower() not in seen:
                seen.add(addr.lower()); to.append(addr)
        if not to:
            return 0
        lines = p.line_items or []
        verb = 'held (waiting)' if action == 'hold' else 'rejected'
        rows = []
        for i in indexes:
            try:
                ln = lines[int(i)]
            except (TypeError, ValueError, IndexError):
                continue
            desc = escape(str((ln or {}).get('description') or (ln or {}).get('ref') or f'line {i}'))
            amt = escape(str((ln or {}).get('amount') or ''))
            rows.append(f'<tr><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">{desc}</td>'
                        f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:right;">'
                        f'{escape(p.currency)} {amt}</td></tr>')
        base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')
        html = (f'<p>The CFO has <strong>{verb}</strong> the following line(s) on '
                f'payment request <strong>{escape(p.ref)}</strong>:</p>'
                '<table style="border-collapse:collapse;width:100%;font-size:14px;">'
                + ''.join(rows) + '</table>'
                + (f'<p style="margin-top:10px;"><strong>Reason:</strong> {escape(note)}</p>' if note else '')
                + f'<p style="margin-top:12px;">Open Omni &rarr; <strong>Payments</strong>: '
                  f'<a href="{base}/payment-requests">{base}/payment-requests</a></p>')
        from core.notifications import send_html_with_cfo_cc
        return send_html_with_cfo_cc(
            subject=f'Omni — payment line(s) {verb} on {p.ref}'[:150],
            html=html, to=to, cc_cfo=False)
    except Exception:  # noqa: BLE001 — a notice must never break the decision
        return 0


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_request_decide_lines(request, req_id):
    """POST /payment-requests/<id>/decide-lines/
    body {line_indexes:[int], action:'approve'|'hold'|'reject', note?}

    Per-line CFO authorisation — "approve 9, hold 1" (CFO 2026-08-31). The whole
    batch is already loaded to FNB at finance sign-off; this records the CFO's
    per-line decision so his Omni queue matches what he ticks at the bank, and
    lets him HOLD a line without being unable to close the rest. Money never moves
    here. Only the CFO, only a PENDING_CFO request. The request auto-closes
    (PAID / CANCELLED) once every line is approved or rejected; a held line keeps
    it open on that line alone.
    """
    from django.core.exceptions import ValidationError
    from django.db import transaction
    from django.utils import timezone as _tz

    me = request.user
    if not _is_cfo(me):
        return Response({'detail': 'Only the CFO can authorise payment lines.'}, status=403)

    body = request.data or {}
    action = (body.get('action') or '').strip().lower()
    if action not in ('approve', 'hold', 'reject'):
        return Response({'detail': 'action must be approve, hold or reject.'}, status=400)
    idxs = body.get('line_indexes')
    if not isinstance(idxs, list) or not idxs:
        return Response({'detail': 'Select at least one line.'}, status=400)
    note = (body.get('note') or '').strip()
    if action == 'reject' and not note:
        return Response({'detail': 'Please give a reason when rejecting a line.'}, status=400)

    status_val = {'approve': 'approved', 'hold': 'held', 'reject': 'rejected'}[action]

    with transaction.atomic():
        p = (PaymentRequest.objects.select_for_update().filter(pk=req_id).first())
        if p is None:
            return Response({'detail': 'Payment request not found.'}, status=404)
        if p.status != PaymentRequest.Status.PENDING_CFO:
            return Response({'detail': f'This request is {_status_label(p.status)} — '
                             f'its lines can no longer be changed.'}, status=409)
        lines = list(p.line_items or [])
        n = len(lines)
        touched = []
        for i in idxs:
            try:
                i = int(i)
            except (TypeError, ValueError):
                continue
            if 0 <= i < n and isinstance(lines[i], dict):
                lines[i] = {**lines[i], 'line_status': status_val,
                            'decided_by': me.get_full_name() or me.username,
                            'decided_at': _tz.now().isoformat()}
                if note:
                    lines[i]['decision_note'] = note[:500]
                touched.append(i)
        if not touched:
            return Response({'detail': 'No valid lines to update.'}, status=400)
        p.line_items = lines
        p.save(update_fields=['line_items', 'updated_at'])

        # Try to close the request if the CFO has now decided every line.
        from .services import close_payment_request_lines
        try:
            close_payment_request_lines(p, me)
        except ValidationError as exc:
            # A caught exception that returns normally would COMMIT this atomic
            # block — leaving the lines marked approved on a request the duplicate
            # control just refused to close (Fable 5, 2026-08-31). Force the whole
            # transaction to roll back so the line decisions revert and the CFO can
            # re-decide (reject the duplicate line) instead of being dead-ended.
            transaction.set_rollback(True)
            msgs = getattr(exc, 'messages', None) or [str(exc)]
            return Response({'detail': '; '.join(str(m) for m in msgs),
                             'control': DUP_CONTROL_CODE}, status=409)

    # After commit: tell the raiser about anything held or rejected.
    if action in ('hold', 'reject'):
        _notify_lines_sent_back(p, me, action, touched, note)

    p.refresh_from_db()
    return Response({'status': p.status, 'status_label': _status_label(p.status),
                     'line_progress': p.line_progress(),
                     'closed': p.status in PaymentRequest.TERMINAL_STATUSES})


def _clear_payment_request(p, user, reason, *, now=None):
    """Terminal-CANCEL a PENDING_CFO payment request, cancel its open task, and
    write the governance AuditLog. Shared by the manual Clear button and the FNB
    auto-close (CFO 2026-08-12) so the close path can never drift between them.
    The CALLER is responsible for permission + SoD checks.

    The row is re-locked (select_for_update) and re-checked to be PENDING_CFO
    INSIDE the transaction, so a stale read in the batch loop can never re-cancel
    a request a parallel action already moved. Returns the request, or None if it
    was no longer PENDING_CFO (nothing done)."""
    from django.utils import timezone as _tz
    from django.db import transaction
    reason = (reason or '')
    now = now or _tz.now()
    with transaction.atomic():
        # NOTE: no select_related('task') here — task is a NULLABLE FK, so joining
        # it makes an OUTER JOIN and Postgres refuses "FOR UPDATE ... nullable side
        # of an outer join". Lock the request row only; task loads lazily below.
        locked = (PaymentRequest.objects.select_for_update()
                  .filter(pk=p.id, status=PaymentRequest.Status.PENDING_CFO)
                  .first())
        if locked is None:
            return None  # already left the queue in a parallel action — do nothing
        locked.status = PaymentRequest.Status.CANCELLED
        locked.decision_notes = reason[:2000]
        locked.save(update_fields=['status', 'decision_notes', 'updated_at'])
        task = locked.task
        if task and task.status not in (OmniTask.Status.DONE, OmniTask.Status.CANCELLED):
            task.status = OmniTask.Status.CANCELLED
            task.completed_at = now
            task.save(update_fields=['status', 'completed_at', 'updated_at'])
        try:
            from core.models import AuditLog
            AuditLog.objects.create(
                table_name='taskboard.PaymentRequest',
                record_id=str(locked.id),
                action=AuditLog.Action.UPDATE,
                new_values={'status': 'cancelled', 'ref': locked.ref, 'reason': reason[:500]},
                user=user,
                description=f'Cleared payment request {locked.ref} from the queue: {reason[:200]}',
            )
        except Exception:  # noqa: BLE001 — audit write must not fail the action
            pass
        # Reflect the change on the caller's in-memory instance.
        p.status = locked.status
        p.decision_notes = locked.decision_notes
    return p


def _may_correct_branch_code(user, p) -> bool:
    """Whether to offer the branch-code correction box on the drawer.

    Mirrors the endpoint exactly — one predicate decides who may, so the screen
    can never offer a button the server then refuses (the two-parsers trap).
    """
    from taskboard.branch_code_correction import may_correct
    return bool(
        may_correct(user, p)
        and p.status in (PaymentRequest.Status.EXCEPTION,
                         PaymentRequest.Status.PENDING_FINANCE,
                         PaymentRequest.Status.DRAFT))


def _any_batch_failed(p) -> bool:
    """True when ANY bank instruction behind this request was rejected.

    Different question from _batches_all_rejected: that one asks "did nothing
    get through" (so do not close it); this one asks "is there something red to
    show a person" (so flag it). A request whose line 1 settled and whose lines
    2-4 were rejected answers False to the first and True to this — and it is
    exactly the case the old single-FK read showed as perfectly fine.
    """
    from fnb.models import FNBBatchSubmission
    if getattr(p, 'fnb_batch_id', None) and p.fnb_batch and p.fnb_batch.status == 'failed':
        return True
    return FNBBatchSubmission.objects.filter(
        payment_request=p, status='failed').exists()


def _reject_reason(p) -> str:
    """What the bank said, from whichever instruction it rejected.

    Reads the request's own batch first (so an unsplit request is unchanged),
    then any other failed batch it produced. Several rejects on one request are
    joined, because "AC08 on one line and AG01 on another" is two problems.
    """
    from fnb.models import FNBBatchSubmission
    reasons = []
    if getattr(p, 'fnb_batch_id', None) and p.fnb_batch and p.fnb_batch.status == 'failed':
        if (p.fnb_batch.failure_reason or '').strip():
            reasons.append(p.fnb_batch.failure_reason.strip())
    for r in (FNBBatchSubmission.objects
              .filter(payment_request=p, status='failed')
              .exclude(pk=getattr(p, 'fnb_batch_id', None) or '00000000-0000-0000-0000-000000000000')
              .values_list('failure_reason', flat=True)):
        if (r or '').strip() and r.strip() not in reasons:
            reasons.append(r.strip())
    return ' | '.join(reasons)[:2000]


#: Why a payment request is left open by the FNB-list close, in one place.
#:
#: 🔴 It deliberately does NOT say "the money did not move". The predicate
#: below folds in 'unknown' — the status whose own label reads "verify with FNB
#: before resubmit" because the POST left us and no clear answer came back. A
#: reader told the money did not move re-raises it, and that is the double
#: payment UNKNOWN exists to prevent (Fable 5.1, round 2, 17-Sep-2026).
_STAY_OPEN_REASON = (
    "The bank either rejected every instruction behind these or gave no clear "
    "answer, so they are absent from FNB's pending list because the bank threw "
    "them out or never confirmed them — not because they were paid. Verify with "
    "FNB before anything is resent. They stay open until somebody records what "
    "actually happened."
)


def _batches_all_rejected(p):
    """True when EVERY bank batch this request produced was rejected, cancelled
    before it left, or came back with no clear answer.

    NOT the same as "the money did not move": 'unknown' means the POST left us
    and the bank never answered cleanly, so it MAY have moved. What this
    supports is the one safe action either way — do not close the request.

    Reads through FNBBatchSubmission.payment_request (added 2026-09-17), which
    sees every batch, and falls back to the request's own single `fnb_batch` FK
    for batches loaded before that stamp existed. Both are needed: the FK holds
    one batch, and a request processed line-by-line produces one per line.

    Returns False when there is no batch at all — "nothing was loaded" is not
    "the bank said no", and a guard that fires on absence would stop every
    payment made outside the FNB pipe.
    """
    from fnb.models import FNBBatchSubmission

    # 'cancelled' belongs here too: FNBBatchSubmission.Status.CANCELLED is
    # "cancelled before submission" — the instruction never left us, so the
    # money certainly did not move (Fable 5.1, 17-Sep-2026).
    DEAD = ('failed', 'unknown', 'cancelled')
    statuses = set(
        FNBBatchSubmission.objects.filter(payment_request=p)
        .values_list('status', flat=True))
    if getattr(p, 'fnb_batch_id', None) and p.fnb_batch:
        statuses.add(p.fnb_batch.status)
    return bool(statuses) and statuses.issubset(set(DEAD))


def _mark_paid_from_bank(p, user, reason, *, now=None, automatic=False):
    """Terminal-PAID a PENDING_CFO request whose money the bank has confirmed left
    the account — the FNB "Fully Processed" email (CFO 2026-08-24). Mirrors
    _clear_payment_request exactly, but records the true terminal state PAID, NOT
    CANCELLED: taskboard.payment_duplicates treats cancelled requests as dead, so a
    genuinely-paid request closed as 'cancelled' would drop out of the
    duplicate-payment control and let the same invoice be re-raised and paid twice.
    Re-locks + re-checks PENDING_CFO inside the transaction; returns the request,
    or None if it already left the queue."""
    from django.utils import timezone as _tz
    from django.db import transaction
    reason = (reason or '')
    now = now or _tz.now()

    # 🔴 Never close a request as PAID just because a batch exists (CFO's FNB
    # brief, control 3: *"a failed FNB batch must keep its originating payment
    # request open and actionable unless a human records verified
    # alternative-payment evidence"*).
    #
    # This fires on the AUTOMATIC caller only — the overnight FNB-email
    # autoclose. It never stops a person: the two human callers (the "mark paid
    # at the bank" button and the FNB-list reconcile) pass automatic=False, and
    # the reason they type IS the evidence the brief asks for. Omni does not
    # block payments; the exception committee decides (CFO 2026-09-17).
    #
    # The live case this stops: 16 payment requests were sitting on PAID with a
    # rejected batch behind them on 17-Sep-2026. A bank email whose reference
    # and amount happen to match must not add to that pile — the money did not
    # move, so the request has to stay open for somebody to act on.
    if automatic and _batches_all_rejected(p):
        log.warning('payment request %s: NOT auto-closing as paid — every FNB '
                    'batch behind it was rejected, cancelled or never confirmed. '
                    'Verify with FNB before anything is resent. Left open for a '
                    'person.', p.ref)
        return None

    with transaction.atomic():
        locked = (PaymentRequest.objects.select_for_update()
                  .filter(pk=p.id, status=PaymentRequest.Status.PENDING_CFO)
                  .first())
        if locked is None:
            return None  # already left the queue in a parallel action — do nothing
        locked.status = PaymentRequest.Status.PAID
        locked.decision_notes = reason[:2000]
        locked.save(update_fields=['status', 'decision_notes', 'updated_at'])
        task = locked.task
        if task and task.status not in (OmniTask.Status.DONE, OmniTask.Status.CANCELLED):
            task.status = OmniTask.Status.CANCELLED
            task.completed_at = now
            task.save(update_fields=['status', 'completed_at', 'updated_at'])
        try:
            from core.models import AuditLog
            AuditLog.objects.create(
                table_name='taskboard.PaymentRequest',
                record_id=str(locked.id),
                action=AuditLog.Action.UPDATE,
                new_values={'status': 'paid', 'ref': locked.ref, 'reason': reason[:500]},
                user=user,
                description=f'Payment request {locked.ref} marked paid from FNB confirmation: {reason[:200]}',
            )
        except Exception:  # noqa: BLE001 — audit write must not fail the action
            pass
        p.status = locked.status
        p.decision_notes = locked.decision_notes
    return p


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_request_clear(request, req_id):
    """POST /payment-requests/<id>/clear/  body {notes}

    Clear a signed-off request out of the queue WITHOUT paying it — for an
    expired request, one paid outside Omni, or a duplicate (bug ktshutlhedi
    2026-07-25 suggestion). The CFO OR a finance approver (Pako / Kago / Legakwa)
    may do it — CFO decision 2026-07-26 — and only on a PENDING_CFO request (a
    PENDING_FINANCE one is rejected by finance instead). Sets the terminal
    CANCELLED state and cancels the open payment task.

    Segregation of duties (CFO 2026-07-26): a finance approver may NOT clear a
    request they themselves signed off at stage 1 — that would let one person
    move a payment request from raised to gone without the CFO ever seeing it.
    Only the CFO can clear their own sign-offs.
    """
    me = request.user
    p = PaymentRequest.objects.select_related('task').filter(pk=req_id).first()
    if p is None:
        return Response({'detail': 'Payment request not found.'}, status=404)
    cfo = _cfo_user()
    is_cfo = bool(me.is_superuser or (cfo and me.id == cfo.id))
    if not (is_cfo or _is_first_approver(me)):
        return Response({'detail': 'Only the CFO or a finance approver can clear a payment request from the queue.'}, status=403)
    if not is_cfo and p.first_approver_id == me.id:
        return Response({'detail': 'You signed this request off — you cannot also clear it from the '
                                   'queue (segregation of duties). Ask the CFO or another finance '
                                   'approver to clear it.'}, status=403)
    if p.status != PaymentRequest.Status.PENDING_CFO:
        return Response({'detail': f'This request is {_status_label(p.status)} — only a request '
                                   f'awaiting CFO authorisation can be cleared.'}, status=409)

    notes = (request.data or {}).get('notes') or ''
    notes = notes.strip()
    if not notes:
        return Response({'detail': 'Please say why you are clearing this request (e.g. paid outside Omni, duplicate, expired).'},
                        status=400)

    if _clear_payment_request(p, me, notes) is None:
        p.refresh_from_db()   # a parallel action already moved it — report the truth
    return Response({'status': p.status, 'status_label': _status_label(p.status)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_request_fnb_reconcile(request):
    """POST /payment-requests/fnb-reconcile/

    Reconcile the open (PENDING_CFO) payment requests against the FNB "Batch
    Payments" pending list. Accepts EITHER an uploaded PDF (multipart 'fnb_pdf')
    OR pasted text ('fnb_text'). When 'auto_close' is set (default true), every
    request that is clearly already paid — NONE of its lines still in the bank
    list — is closed automatically through the shared audited clear
    (_clear_payment_request), and a DeepSeek intelligence note explains what was
    done. Falls back to a deterministic summary if the AI is unavailable, so the
    reconcile never 500s or blocks on the AI box (CFO 2026-08-12).
    """
    import json as _json
    import datetime as _date
    from django.conf import settings
    from .fnb_reconcile import (parse_fnb_report, reconcile, find_fnb_duplicates,
                                pdf_bytes_to_text, deterministic_summary)
    me = request.user
    cfo = _cfo_user()
    is_cfo = bool(me.is_superuser or (cfo and me.id == cfo.id))
    if not (is_cfo or _is_first_approver(me)):
        return Response({'detail': 'Only the CFO or a finance approver can reconcile the payment queue.'}, status=403)

    # Source: an uploaded PDF wins; otherwise pasted text.
    pdf = request.FILES.get('fnb_pdf')
    if pdf is not None:
        fnb_text = pdf_bytes_to_text(pdf.read())
        if not fnb_text.strip():
            return Response({'detail': 'Could not read any text from that PDF — is it the FNB Batch Payments export?'}, status=400)
    else:
        fnb_text = ((request.data or {}).get('fnb_text') or '').strip()
        if not fnb_text:
            return Response({'detail': 'Upload the FNB PDF or paste the batch-payments list.'}, status=400)

    auto_close = str((request.data or {}).get('auto_close', 'true')).strip().lower() in ('1', 'true', 'yes', 'on')

    pr_by_ref, requests_data, id_by_ref = {}, [], {}
    for p in (PaymentRequest.objects.filter(status=PaymentRequest.Status.PENDING_CFO)
              .select_related('task').order_by('ref')):
        lines = [{'amount': row.get('amount'), 'ref': row.get('ref', ''),
                  'description': row.get('description', '')}
                 for row in (p.line_items or [])]
        requests_data.append({'ref': p.ref, 'subject': p.subject, 'total': str(p.total),
                              'category': p.category, 'lines': lines})
        id_by_ref[p.ref] = str(p.id)
        pr_by_ref[p.ref] = p

    parsed = parse_fnb_report(fnb_text)
    result = reconcile(parsed, requests_data)
    duplicates = find_fnb_duplicates(parsed)
    for bucket in ('already_paid', 'still_pending', 'no_lines'):
        for r in result[bucket]:
            r['id'] = id_by_ref.get(r['ref'])

    # ── Auto-close the clearly-already-paid ones (high confidence: no line in FNB) ──
    # SAFETY GATE (Fable review 2026-08-12, class L7 "negative-evidence auto-action"):
    # "already paid" means NONE of a request's lines matched the FNB list — so an
    # unreadable / wrong / stale document that yields no amounts and no reference
    # tokens would classify EVERY open request as already-paid and, with auto-close
    # on, cancel the whole queue in one click. Absence of a match is never evidence
    # of payment. So refuse to auto-close when the document parsed to nothing, or
    # when it would close 100% of what was judged — degrade to the manual list.
    closed, skipped = [], []
    warning = ''
    parse_empty = not parsed['amount_set'] and not parsed['tokens']
    judgeable = sum(1 for rq in requests_data if rq.get('lines'))
    would_close_all = judgeable > 0 and len(result['already_paid']) >= judgeable
    if auto_close and parse_empty:
        warning = ('Could not read any bank amounts or references from that document, so nothing '
                   'was closed. Please check it is the FNB "Batch Payments" export.')
    elif auto_close and would_close_all:
        warning = ('Every open request looked already-paid — that usually means the wrong or an '
                   'out-of-date file was used. Nothing was auto-closed; review the list and use '
                   'Close if it is genuinely correct.')
    auto_close_applied = bool(auto_close and not warning)

    if auto_close_applied:
        reason = (f'Paid via FNB. Auto-reconciled to the FNB pending list '
                  f'({timezone.localdate().isoformat()}): none of its lines are in the bank '
                  f'queue, so already authorised.')
        for r in result['already_paid']:
            p = pr_by_ref.get(r['ref'])
            if p is None or p.status != PaymentRequest.Status.PENDING_CFO:
                continue
            # Segregation of duties: a finance approver may not clear their own sign-off.
            if not is_cfo and getattr(p, 'first_approver_id', None) == me.id:
                r['closed'] = False
                skipped.append({'ref': r['ref'], 'why': 'you signed this off — another approver must clear it'})
                continue
            if _clear_payment_request(p, me, reason) is not None:
                r['closed'] = True
                closed.append({'ref': r['ref'], 'subject': r['subject'], 'total': r['total']})

    # ── Intelligence note: DeepSeek (PII-safe aggregates only), deterministic fallback ──
    def _tot(rows):
        s = 0.0
        for x in rows:
            try:
                s += float(x.get('total') or 0)
            except (TypeError, ValueError):
                pass
        return round(s, 2)
    stats = {
        'auto_close': auto_close,
        'closed_count': len(closed), 'closed_total_pula': _tot(closed),
        'closed_refs': [c['ref'] for c in closed],
        'already_paid_found': len(result['already_paid']),
        'still_pending_count': len(result['still_pending']),
        'still_pending_total_pula': _tot(result['still_pending']),
        'duplicate_amounts_pula': [{'amount': d['amount'], 'times_loaded': d['count']} for d in duplicates],
        'fnb_lines_read': len(parsed['entries']),
    }
    ai_source = 'fallback'
    try:
        from core.ai_assist import deepseek_complete
        sys_p = ('You are the CFO of Alpha Direct Insurance (Botswana) writing a short internal '
                 'note. Currency is Botswana Pula (P). Given a JSON reconciliation of the bank '
                 '(FNB) pending list against the payment queue, write 4-6 short plain-English '
                 'lines: what was auto-closed and the total cleared, how many payments are still '
                 'waiting in the bank, and MOST IMPORTANT flag any duplicate amounts as a '
                 'double-payment risk to check before authorising. No customer names, no preamble.')
        intelligence = (deepseek_complete(_json.dumps(stats), system_prompt=sys_p, max_tokens=350) or '').strip()
        if intelligence:
            ai_source = 'deepseek' if getattr(settings, 'DEEPSEEK_ENABLED', True) else 'gemini'
        else:
            intelligence = deterministic_summary(result, duplicates, closed=closed)
    except Exception:  # noqa: BLE001 — the AI must never break the reconcile
        intelligence = deterministic_summary(result, duplicates, closed=closed)

    result['duplicates'] = duplicates
    result['closed'] = closed
    result['skipped'] = skipped
    result['intelligence'] = intelligence
    result['ai_source'] = ai_source
    result['auto_close'] = auto_close
    result['auto_close_applied'] = auto_close_applied
    result['warning'] = warning
    result['fnb_lines_read'] = len(parsed['entries'])
    result['requests_checked'] = len(requests_data)
    return Response(result)


def _payment_match_dict(p):
    """The request shape fed to fnb.email_reconcile matchers (catchup_matches /
    match_paid_email). Shared by the catch-up screen and the payment-list "why is
    this still open?" reason so both build the request the SAME way. They can still
    reach different verdicts: the catch-up screen grades amount-only candidates
    against the UNCLAIMED emails, while the list grades against all paid emails - a
    list amber "check" is deliberately the cautious side (Fable F3, 2026-09-06)."""
    return {
        'ref': p.ref, 'total': p.total,
        'bank_our_reference': p.bank_our_reference, 'bank_narration': p.bank_narration,
        'payee': p.payee, 'subject': p.subject, 'line_items': p.line_items,
        'batch_key': getattr(p.fnb_batch, 'idempotency_key', '') or '',
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payment_request_email_catchup(request):
    """GET /payment-requests/email-catchup/

    The catch-up list (CFO 2026-08-24). The nightly auto-close
    (fnb_email_autoclose) only closes a request when a FNB "Fully Processed"
    email overlaps its Omni reference or EFT batch id. Requests that were paid
    straight in FNB (never loaded through Omni) have no such reference, so they
    sit forever in the queue even though the money is gone.

    This reads the same FNB emails and, for each still-open (PENDING_CFO)
    request, shows the bank confirmation(s) that match it — graded confident /
    review / ambiguous — so the CFO can eyeball and clear them in one tap
    (POST .../mark-paid-bank/). READ-ONLY: it closes nothing.
    """
    from django.conf import settings
    from django.db.models import Q
    from .fnb_email_view_helpers import fetch_paid_fnb_emails
    from fnb.email_reconcile import catchup_matches, _reference_overlaps, _batch_matches, _to_amount

    me = request.user
    cfo = _cfo_user()
    is_cfo = bool(me.is_superuser or (cfo and me.id == cfo.id))
    if not (is_cfo or _is_first_approver(me)):
        return Response({'detail': 'Only the CFO or a finance approver can read the catch-up list.'}, status=403)

    hours = getattr(settings, 'FNB_EMAIL_CATCHUP_LOOKBACK_HOURS', 720)  # 30 days
    try:
        paid_emails = fetch_paid_fnb_emails(hours)
    except RuntimeError as exc:
        return Response({'detail': f'Could not read the FNB emails: {exc}'}, status=502)
    except ValueError as exc:  # no mailbox / reader configured
        return Response({'detail': str(exc)}, status=503)

    # Open requests to place, plus recently-terminal ones so we can tell that a
    # bank email already belongs to a settled request (never offer it as an
    # amount-only candidate for a different open request).
    from datetime import timedelta
    cutoff = timezone.now() - timedelta(days=120)
    qs = (PaymentRequest.objects.select_related('fnb_batch', 'task')
          .filter(Q(status=PaymentRequest.Status.PENDING_CFO)
                  | Q(status__in=[PaymentRequest.Status.PAID, PaymentRequest.Status.CANCELLED],
                      created_at__gte=cutoff)))

    all_reqs = list(qs)
    match_dicts = {p.id: _payment_match_dict(p) for p in all_reqs}

    # An email is "claimed" if it strictly (amount + ref/batch) matches ANY request,
    # open or terminal — those are the auto-matcher's job and must not be offered as
    # a loose amount-only candidate for some unrelated open request.
    def _strictly_claimed(e):
        for p in all_reqs:
            md = match_dicts[p.id]
            if _to_amount(md['total']) == e['amount'] and (
                    _reference_overlaps(e.get('ref', ''), md) or _batch_matches(e.get('ref', ''), md)):
                return True
        return False

    unclaimed = [e for e in paid_emails if not _strictly_claimed(e)]

    # How many still-open requests share each exact amount. If a bank email only
    # matches on amount (no reference), and more than one open request has that
    # amount, one real payment could be tapped against several requests — so those
    # rows are forced to 'ambiguous' below (never the softer 'review').
    from collections import Counter
    open_amount_counts = Counter(
        _to_amount(match_dicts[p.id]['total'])
        for p in all_reqs if p.status == PaymentRequest.Status.PENDING_CFO
    )

    rows = []
    for p in all_reqs:
        if p.status != PaymentRequest.Status.PENDING_CFO:
            continue
        md = match_dicts[p.id]
        # Prefer a strict (confident) match on this request; else loose amount-only.
        res = catchup_matches(md, paid_emails)
        if res['confidence'] != 'confident':
            res = catchup_matches(md, unclaimed)
        if res['confidence'] == 'none':
            continue
        confidence = res['confidence']
        if confidence == 'review' and open_amount_counts.get(_to_amount(p.total), 0) > 1:
            confidence = 'ambiguous'
        rows.append({
            'id': str(p.id), 'ref': p.ref, 'entity': p.entity,
            'category': p.category, 'subject': p.subject,
            'total': str(p.total),
            'made': p.created_at.date().isoformat() if p.created_at else '',
            'can_clear': bool(is_cfo or getattr(p, 'first_approver_id', None) != me.id),
            'confidence': confidence,
            'matches': [{'ref': e.get('ref', ''),
                         'amount': f"{e['amount']:.2f}",
                         'date': e.get('date', '')} for e in res['matches']],
        })

    order = {'confident': 0, 'review': 1, 'ambiguous': 2}
    rows.sort(key=lambda r: (order.get(r['confidence'], 9), -float(r['total'])))
    return Response({
        'rows': rows,
        'emails_read': len(paid_emails),
        'lookback_days': round(hours / 24),
        'is_cfo': is_cfo,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_request_mark_paid_bank(request, req_id):
    """POST /payment-requests/<id>/mark-paid-bank/  body {reason}

    Mark a PENDING_CFO request PAID because the CFO has eyeballed the bank's
    "Fully Processed" confirmation on the catch-up screen. Terminal state is
    PAID (not CANCELLED) so the paid request still counts in the duplicate-payment
    control (PAY-DUP-01). Same authority + segregation-of-duties as clear: the CFO
    or a finance approver, but not the approver who signed this request off.
    """
    me = request.user
    p = PaymentRequest.objects.select_related('task').filter(pk=req_id).first()
    if p is None:
        return Response({'detail': 'Payment request not found.'}, status=404)
    cfo = _cfo_user()
    is_cfo = bool(me.is_superuser or (cfo and me.id == cfo.id))
    if not (is_cfo or _is_first_approver(me)):
        return Response({'detail': 'Only the CFO or a finance approver can mark a payment paid.'}, status=403)
    if not is_cfo and p.first_approver_id == me.id:
        return Response({'detail': 'You signed this request off — you cannot also mark it paid '
                                   '(segregation of duties). Ask the CFO or another approver.'}, status=403)
    if p.status != PaymentRequest.Status.PENDING_CFO:
        return Response({'detail': f'This request is {_status_label(p.status)} — only a request '
                                   f'awaiting CFO authorisation can be marked paid.'}, status=409)

    reason = ((request.data or {}).get('reason') or '').strip()
    if not reason:
        reason = 'Paid via FNB — CFO confirmed the bank "Fully Processed" email on the catch-up screen.'

    if _mark_paid_from_bank(p, me, reason) is None:
        p.refresh_from_db()   # a parallel action already moved it — report the truth
    return Response({'status': p.status, 'status_label': _status_label(p.status)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_request_mark_rejected_fnb(request, req_id):
    """POST /payment-requests/<id>/mark-rejected-fnb/  body {reason}

    Record that this payment was REJECTED inside the FNB app, so Omni stops
    saying "Waiting for your authorisation in the FNB app" for a payment the bank
    has already turned down (Leano Makwapa / Koketso Kgetse, 2026-09-11).

    WHAT IT CHANGES, AND WHAT IT DELIBERATELY DOES NOT. It writes the rejection
    onto the FNB BATCH, not onto the payment request: FnbBatch.Status.FAILED is
    the state the bank's own API sets when FNB rejects a batch, and
    fnb.email_reconcile.open_reason ALREADY renders that as "Rejected by FNB —
    fix the bank/branch details and reload". So the queue line corrects itself
    with no new status, no new wording and no second source of truth.

    The payment request itself is left exactly where it is, open. A rejection is
    not a conclusion — the bank details get fixed and it is loaded again. Closing
    it here would drop it out of the queue and out of the duplicate-payment
    control (PAY-DUP-01), which is how a genuinely unpaid invoice disappears.

    No money moves and nothing is marked paid, so there is no segregation-of-duty
    bar on the person who approved it: sending work back to yourself is not a
    conflict. Who may do it is _may_mark_fnb_rejected (the accounts team + the
    finance approvers + the CFO).
    """
    me = request.user
    if not _may_mark_fnb_rejected(me):
        return Response({'detail': 'Only the accounts team, a finance approver or the '
                                   'CFO can record an FNB rejection.'}, status=403)
    p = (PaymentRequest.objects.select_related('fnb_batch')
         .filter(pk=req_id).first())
    if p is None:
        return Response({'detail': 'Payment request not found.'}, status=404)

    reason = ((request.data or {}).get('reason') or '').strip()
    if len(reason) < 3:
        return Response({'detail': 'Say in a few words why FNB rejected it — it is what '
                                   'tells the next person what to fix.'}, status=400)

    batch = p.fnb_batch if p.fnb_batch_id else None
    if batch is None:
        return Response({'detail': 'This payment was never loaded to FNB through Omni, so '
                                   'there is nothing for FNB to have rejected.'}, status=409)
    from fnb.models import FNBBatchSubmission as FnbBatch
    if batch.status == FnbBatch.Status.SETTLED:
        return Response({'detail': 'The bank has confirmed this payment SETTLED — the money '
                                   'left the account. It cannot also be a rejection. Check '
                                   'FNB before changing anything.'}, status=409)
    if batch.status == FnbBatch.Status.FAILED:
        return Response({'detail': 'This one is already recorded as rejected by FNB.'},
                        status=409)

    who = me.get_full_name() or me.username
    was = batch.status
    batch.status = FnbBatch.Status.FAILED
    # WHO DID WHAT, and they are two different people (reported 2026-09-11).
    # The first wording read "Rejected in the FNB app by <name>", which named the
    # person who RECORDED the rejection in Omni as the person who REJECTED it at
    # the bank. They are never the same act — rejecting inside the FNB app is the
    # authoriser's — so it put one person's decision against another's name.
    #
    # THE REASON COMES FIRST, and that ordering is load-bearing, not taste.
    # fnb.email_reconcile.open_reason renders this field as `fr[:70]` on the
    # payment queue. The attribution alone is 55 characters, so leading with it
    # pushed the typed reason clean out of that window — the queue line would
    # have kept the name and lost the very thing the reason exists to carry, and
    # a longer name was cut mid-name. Reason first, attribution after: the queue
    # line shows what to fix, and the banner (which renders the whole string)
    # still says exactly who did which half. Caught by Fable, not by the tests.
    batch.failure_reason = (
        f'{reason} (rejected in the FNB app; marked as rejected in Omni '
        f'by {who})'
    )[:500]
    batch.save(update_fields=['status', 'failure_reason', 'updated_at'])

    from core.models import AuditLog
    AuditLog.objects.create(
        table_name='fnb_fnbbatch', record_id=str(batch.id),
        action=AuditLog.Action.UPDATE, user=me,
        old_values={'status': was},
        new_values={'status': batch.status, 'failure_reason': batch.failure_reason},
        description=(f'FNB rejection recorded by hand for payment request {p.ref}: '
                     f'{reason[:300]}. The request stays OPEN for reload; no money moved.'))

    return Response({
        'id': str(p.id),
        'batch_status': batch.status,
        'failure_reason': batch.failure_reason,
        'message': ('Recorded. The queue now reads "Rejected by FNB" — fix the bank '
                    'details and load it again. Nothing was paid or closed.'),
    })


# How an uploaded list arrives. CFO decision 2026-09-11, in his words: "if he
# loads 10 supplier payments via omni csv I want it to display separately in Omni
# and FNB - so if I want to reject one supplier I don't reject everyone".
#
# SEPARATE IS THE DEFAULT, and it is the default in the safe direction: ten
# suppliers become ten requests and ten entries at the bank, so refusing one
# refuses one. Bundling is the exception, for a commission or a salary run —
# genuinely one run to one group, approved as a run, which is how Finance already
# works them.
#
# It is NOT decided from the category, because Omni has no 'commission' category:
# Legakwa's own commission run is filed under 'unicoin', which also carries rent
# and supplier invoices. Guessing from the category would silently bundle ten
# separate Unicoin suppliers into one all-or-nothing approval — the exact thing
# the CFO asked us not to do. So the person uploading says which it is, and
# anything they do not positively mark as a run stays separate.
def _bundles_into_one_request(arrive_as: str) -> bool:
    return (arrive_as or '').strip().lower() in ('run', 'bundle', 'one', 'bundled')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def payment_request_bulk_parse(request):
    """POST /payment-requests/bulk-parse/  (multipart: file, category)

    READ a list of payments out of a file and say what is in it. It CREATES
    NOTHING (Legakwa Ntabeni 2026-09-11).

    That is the whole safety design. Omni's create path carries ~600 lines of
    money controls — the duplicate gate, the bank-change gate, first-payment-to-a-
    new-payee, the hard bank cross-check, supplier terms. An importer that wrote
    rows into the database directly would walk past all of them, and past every
    control added after it. So this reads and checks; the rows are then created
    through the ordinary gated endpoint, one call each, and every gate applies to
    every row exactly as if it had been typed.

    The reply also says HOW the rows will arrive, so the person uploading sees it
    before anything happens rather than afterwards.
    """
    upload = request.FILES.get('file')
    if upload is None:
        return Response({'detail': 'Attach the payment list.'}, status=400)
    if upload.size > 5 * 1024 * 1024:
        return Response({'detail': f'"{upload.name}" is too large — 5 MB max.'},
                        status=400)

    from taskboard.bulk_payment_upload import parse_payment_file
    try:
        read = parse_payment_file(upload)
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=400)

    category = (request.data.get('category') or '').strip().lower()
    # Absent, misspelled or anything unexpected all mean SEPARATE. A default that
    # bundles would be a default that removes the CFO's ability to refuse one.
    bundled = _bundles_into_one_request(request.data.get('arrive_as'))
    n_ok = read['ok']
    if bundled:
        shape = (f'These {n_ok} lines will arrive as ONE payment request for the '
                 f'whole run, approved in one go — the way a commission or salary '
                 f'run already works.')
    else:
        shape = (f'These {n_ok} lines will arrive as {n_ok} SEPARATE payment '
                 f'requests, and as {n_ok} separate entries in FNB. You can '
                 f'refuse any one of them without touching the others.')

    return Response({**read, 'category': category, 'bundled': bundled,
                     'arrive_as': 'run' if bundled else 'separate',
                     'how_they_will_arrive': shape,
                     'message': (f'Read {len(read["rows"])} line(s): {n_ok} ready, '
                                 f'{read["bad"]} needing a fix. Nothing has been '
                                 f'created yet.')})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payment_request_fnb_file(request, req_id):
    """GET /payment-requests/<id>/fnb-file/  -> the FNB bulk-payment CSV.

    The exact template Finance builds by hand today, built by machine instead
    (Legakwa Ntabeni 2026-09-11). Every number is written as text, so the
    `1.23E+11` in the hand-made July file — a twelve-digit account number Excel
    silently shortened into scientific notation, and which went to the bank that
    way — cannot happen here.
    """
    p = PaymentRequest.objects.filter(pk=req_id).first()
    if p is None:
        return Response({'detail': 'Payment request not found.'}, status=404)
    if not _can_view_request(request.user, p):
        return Response({'detail': 'Not allowed.'}, status=403)

    from taskboard.fnb_autoload import source_account_number_for
    source = source_account_number_for(p.category)

    lines = p.line_items or []
    rows = []
    for ln in lines:
        if not isinstance(ln, dict):
            continue
        # A bundled run carries each payee's own account on the line; a
        # single-payee request carries it once on the request itself.
        acct = str(ln.get('account_number') or p.account_number or '').strip()
        rows.append([
            str(ln.get('payee') or ln.get('description') or p.payee or '').strip(),
            acct,
            str(ln.get('account_type') or p.account_type or '1').strip() or '1',
            str(ln.get('branch_code') or p.branch_code or '').strip(),
            f"{Decimal(str(ln.get('amount') or '0')):.2f}",
            (p.subject or '')[:30],
            (str(ln.get('recipient_reference') or '').strip() or (p.subject or ''))[:30],
            str(ln.get('email') or '').strip(),
        ])
    if not rows:
        return Response({'detail': 'There are no payment lines on this request.'},
                        status=409)

    import csv as _csv
    import io as _io
    from django.http import HttpResponse
    buf = _io.StringIO()
    w = _csv.writer(buf)
    # The three preamble lines of the real FNB template, then the header row.
    w.writerow(['BInSol - U ver 1.00', '', '', '', '', '', '', ''])
    w.writerow([timezone.localdate().strftime('%d/%m/%Y'), '', '', '', '', '', '', ''])
    w.writerow([source, '', '', '', '', '', '', ''])
    w.writerow(['RECIPIENT NAME', 'RECIPIENT ACCOUNT', 'RECIPIENT ACCOUNT TYPE',
                'BRANCHCODE', 'AMOUNT', 'OWN REFERENCE', 'RECIPIENT REFERENCE',
                'EMAIL 1 NOTIFY'])
    for r in rows:
        w.writerow(r)

    from core.models import AuditLog
    AuditLog.objects.create(
        table_name='taskboard_paymentrequest', record_id=str(p.id),
        action=AuditLog.Action.DOWNLOAD, user=request.user,
        description=(f'FNB bulk-payment file downloaded for {p.ref}: '
                     f'{len(rows)} line(s), {p.currency} {p.total}.'))

    resp = HttpResponse(buf.getvalue(), content_type='text/csv')
    resp['Content-Disposition'] = (
        f'attachment; filename="Payment_CSV_Template_All - {p.ref}.csv"')
    return resp


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_request_reconcile_fnb_list(request):
    """POST /payment-requests/reconcile-fnb-list/  (multipart: file=<FNB PDF>, mode=preview|apply)

    The CFO's "upload FNB list" button (CFO directive 2026-09-06). The FNB "Batch
    Payments" export is everything still awaiting his signature in the bank; so any
    open Omni request NOT on that list has been dealt with and is closed.

    mode=preview (default) reads the PDF and returns what WOULD close/stay — it
    writes nothing. mode=apply closes the ones not on the list (marks them PAID,
    keeping PAY-DUP-01, with an audit note) — reversible, anyone can re-raise a
    request closed in error. CFO or a finance approver only.
    """
    from django.db import transaction
    from fnb.fnb_list_reconcile import parse_fnb_pending_list, request_in_list

    me = request.user
    cfo = _cfo_user()
    is_cfo = bool(me.is_superuser or (cfo and me.id == cfo.id))
    if not (is_cfo or _is_first_approver(me)):
        return Response({'detail': 'Only the CFO or a finance approver can reconcile the queue.'}, status=403)

    f = request.FILES.get('file')
    if f is None:
        return Response({'detail': 'Attach the FNB Batch Payments PDF.'}, status=400)
    if f.size > 8 * 1024 * 1024:
        return Response({'detail': 'That file is too big — the FNB list is normally well under a megabyte.'}, status=400)
    try:
        entries = parse_fnb_pending_list(f.read())
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=400)
    except Exception:
        log.exception('FNB-list reconcile: PDF parse crashed')  # so "send it to IT" is diagnosable
        return Response({'detail': 'Could not read that PDF. Please upload the FNB "Batch Payments" list.'}, status=400)

    mode = (request.data.get('mode') or 'preview').strip().lower()
    # Only the CFO's OWN authorisation queue (pending_cfo). Exception/committee rows
    # are NOT "his queue" — they are held for the exception committee and were never
    # loaded to FNB, so this button must not close them (Fable, 2026-09-06).
    open_qs = (PaymentRequest.objects
               .filter(status=PaymentRequest.Status.PENDING_CFO)
               .select_related('fnb_batch', 'task').order_by('-total'))

    def _row(p, in_list):
        b = p.fnb_batch if p.fnb_batch_id else None
        # `bank_rejected` used to read the single FK and only the status
        # 'failed', so a row left open because of an UNKNOWN or CANCELLED batch
        # carried no tag at all and read on screen as an ordinary close.
        _dead = _batches_all_rejected(p)
        return {
            'id': str(p.id), 'ref': p.ref, 'payee': p.payee or '',
            'total': str(p.total), 'currency': p.currency, 'status': p.status,
            'bank_rejected': _dead,
            'not_loaded': not p.fnb_batch_id,
            'in_list': in_list,
        }

    keep, to_close, skipped = [], [], []
    for p in open_qs:
        if request_in_list(_payment_match_dict(p), entries):
            keep.append(p)
            continue
        # Segregation of duties: a finance approver may not close a request they
        # signed off themselves — leave it for the CFO / another approver
        # (CFO 2026-07-26, mirrors clear / mark-paid-bank). The CFO is exempt.
        if not is_cfo and p.first_approver_id == me.id:
            skipped.append(p)
            continue
        to_close.append(p)

    # PARTITION FIRST, THEN COUNT. `close_count` drives the button label, so
    # counting rows the apply leg will skip makes the button say "Close 5" and
    # close 4 — a screen that contradicts its own action (Fable 5.1, round 2).
    #
    # The predicate is the SAME one the apply leg uses. It used to read
    # `p.fnb_batch` — the single FK — while apply asked _batches_all_rejected,
    # which sees every batch a request produced. The two disagreed: the preview
    # could say "0 will stay open" and apply then leave one open (the
    # two-parsers trap; caught by this file's own new test, 17-Sep-2026).
    rejected = [p for p in to_close if _batches_all_rejected(p)]
    _rejected_ids = {p.id for p in rejected}
    closable = [p for p in to_close if p.id not in _rejected_ids]
    close_total = sum((Decimal(str(p.total)) for p in closable), Decimal('0.00'))
    not_loaded = [p for p in closable if not p.fnb_batch_id]

    if mode != 'apply':
        return Response({
            'mode': 'preview',
            'entries_read': len(entries),
            'keep_count': len(keep),
            'close_count': len(closable),
            'close_total': f'{close_total:.2f}',
            'rejected_in_close': len(rejected),
            'rejected_will_stay_open': len(rejected),
            'not_loaded_in_close': len(not_loaded),
            'skipped_count': len(skipped),
            'would_close': [_row(p, False) for p in closable],
            'would_keep': [_row(p, True) for p in keep],
            'would_stay_open': [_row(p, False) for p in rejected],
            'stay_open_reason': _STAY_OPEN_REASON,
            'skipped_own': [_row(p, False) for p in skipped],   # not on the list; skipped for SoD
        })

    # apply — close the ones not on the list (all pending_cfo here).
    reason = (f"Closed via 'upload FNB list' by "
              f"{(me.get_full_name() or me.username)} on "
              f"{timezone.localtime(timezone.now()).strftime('%Y-%m-%d %H:%M')}: not on the "
              f"uploaded FNB Batch Payments list, so closed per CFO rule. Reversible — re-raise if closed in error.")
    closed, failed = [], []
    closed_total = Decimal('0.00')
    # 🔴 A BANK-REJECTED REQUEST IS NOT CLOSED HERE (CFO's FNB brief, control 3:
    # "never mark a request paid merely because a submission was created or a
    # linked batch exists"). Of course a rejected payment is absent from FNB's
    # PENDING list — the bank threw it out — so the "not on the list, so it must
    # be done" rule reads it exactly backwards, and this is the likeliest source
    # of the 16 requests found sitting on PAID against a failed batch on
    # 17-Sep-2026. They stay OPEN and actionable, which is what the brief asks
    # for; the response says so plainly rather than silently dropping them.
    _left_open = rejected
    for p in closable:
        # The close is atomic. If it raises, or the request left the queue between
        # the read and now, we record it as FAILED — never count it closed without
        # proof it actually closed (Fable/panel H6 + hole 1c, 2026-09-06).
        try:
            with transaction.atomic():
                _mark_paid_from_bank(p, me, reason)   # sets PAID + audit + cancels its task
        except Exception as exc:                      # noqa: BLE001 — report, don't hide
            failed.append({'ref': p.ref, 'error': str(exc)[:140]})
            continue
        p.refresh_from_db()
        if p.status != PaymentRequest.Status.PAID:
            failed.append({'ref': p.ref, 'error': f'moved to {p.status} before we closed it'})
            continue
        closed.append(_row(p, False))
        closed_total += Decimal(str(p.total))
    resp = {
        'mode': 'apply',
        'entries_read': len(entries),
        'closed_count': len(closed),
        'closed_total': f'{closed_total:.2f}',
        'kept_count': len(keep),
        'rejected_closed': len([r for r in closed if r['bank_rejected']]),
        'not_loaded_closed': len([r for r in closed if r['not_loaded']]),
        'skipped_count': len(skipped),
        'closed': closed,
        # Left open on purpose — the bank rejected, cancelled or never
        # confirmed every instruction behind them, so somebody has to act.
        'left_open_rejected_count': len(_left_open),
        'left_open_rejected': [_row(p, False) for p in _left_open],
        'left_open_reason': _STAY_OPEN_REASON,
    }
    if failed:
        resp['failed_count'] = len(failed)
        resp['failed'] = failed
    return Response(resp)


# ── Amend / cancel before sign-off (Kelvin Kimani spec 2026-09-08) ───────────
# The inputter's own window to fix a wrong amount or drop an invoice that should
# not be paid, without abandoning and re-raising the whole request. The rules
# live in taskboard/payment_amend.py; these two endpoints are the doors.
#
# OMNI MOVES NO MONEY. Both of these change a workflow record — money leaves
# only through FNB / First Capital with the CFO's phone 2-factor.

def _may_amend_request(user, p) -> bool:
    from taskboard.payment_amend import may_amend
    return may_amend(user, p)


def _amend_change_rows(p) -> list:
    from taskboard.payment_amend import change_rows
    return change_rows(p)


def _amend_error(exc) -> Response:
    return Response({'detail': exc.detail, 'control': exc.control}, status=exc.status)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_request_correct_branch_code(request, req_id):
    """POST /payment-requests/<id>/correct-branch-code/   body {branch_code}

    The committee&rsquo;s way to FIX the thing PAY-BANK-05 flagged. Until this
    existed the control could find a branch code that guarantees an AC08 reject
    and nobody could do anything about it in Omni except cancel the request and
    start again.

    One field, deliberately. The account number is what identifies the payee and
    stays under PAY-BANK-01; a branch code cannot redirect a payment to someone
    else. See taskboard/branch_code_correction.py for the full reasoning.
    """
    from taskboard import branch_code_correction as bcc
    from taskboard.payment_amend import AmendError

    p = (PaymentRequest.objects.select_related('task', 'created_by')
         .filter(pk=req_id).first())
    if p is None:
        return Response({'detail': 'Payment request not found.'}, status=404)
    if not _can_view_request(request.user, p):
        return Response({'detail': 'Permission denied.'}, status=403)

    try:
        result = bcc.correct_branch_code(
            p, request.user,
            branch_code=(request.data or {}).get('branch_code') or '')
    except AmendError as exc:
        return _amend_error(exc)

    from taskboard import payment_amend as amend_svc
    result['changes'] = amend_svc.change_rows(p)
    return Response(result)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_request_amend(request, req_id):
    """POST /payment-requests/<id>/amend/

    body {line_changes: [{line, field, value}], request_changes: {field: value}}

    Corrects a line (amount / invoice number / invoice date / due date / GL code
    / description) or a request-level field (payment date / processing method /
    subject / payee). Only before finance sign-off. Every amend recalculates
    TOTAL PAYABLE, invalidates a completed cross-check, and is logged with
    before/after, who and when — attribution read from the logged-in user,
    never typed.
    """
    from taskboard import payment_amend as amend_svc

    p = PaymentRequest.objects.select_related('task', 'created_by').filter(pk=req_id).first()
    if p is None:
        return Response({'detail': 'Payment request not found.'}, status=404)
    if not _can_view_request(request.user, p):
        return Response({'detail': 'Permission denied.'}, status=403)

    body = request.data or {}
    try:
        result = amend_svc.amend(
            p, request.user,
            line_changes=body.get('line_changes') or [],
            request_changes=body.get('request_changes') or {})
    except amend_svc.AmendError as exc:
        return _amend_error(exc)

    resp = {
        'total': f'{result["total"]:.2f}',
        'currency': p.currency,
        'payment_count': p.payment_count(),
        'changes': amend_svc.change_rows(p),
        'cross_check_cleared': result['cross_check_cleared'],
    }
    # "Changing the payee is a higher-risk amend … Recommend a payee change is
    # flagged distinctly and forces the cross-check's payee-match confirmation
    # to be re-ticked, rather than being treated as an ordinary field edit."
    # Both gates re-derive against the NEW payee's own history at sign-off, so
    # the confirmation genuinely has to be given again; this says so out loud.
    if 'payee' in result['high_risk']:
        resp['payee_changed'] = {
            'control': 'PAY-AMEND-PAYEE',
            'message': ('You changed who is being paid. That is the classic '
                        'payment-redirection risk, so the bank cross-check has to '
                        'be done again against this payee — a finance approver '
                        'will be asked to confirm the account and the beneficiary '
                        'details on the document before this is signed off.'),
        }
    return Response(resp, status=200)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_request_cancel(request, req_id):
    """POST /payment-requests/<id>/cancel/  body {reason, line?}

    `line` cancels ONE invoice; without it the whole request is voided. A reason
    is required either way — a pulled payment is what an auditor asks "why"
    about. NOTHING IS EVER DELETED: the line or the request is flagged cancelled
    and kept in full, with who pulled it, when and why.
    """
    from taskboard import payment_amend as amend_svc

    p = PaymentRequest.objects.select_related('task', 'created_by').filter(pk=req_id).first()
    if p is None:
        return Response({'detail': 'Payment request not found.'}, status=404)
    if not _can_view_request(request.user, p):
        return Response({'detail': 'Permission denied.'}, status=403)

    body = request.data or {}
    reason = (body.get('reason') or '').strip()
    raw_line = body.get('line')
    try:
        if raw_line in (None, ''):
            result = amend_svc.cancel_request(p, request.user, reason=reason)
        else:
            try:
                line_no = int(raw_line)
            except (TypeError, ValueError):
                return Response({'detail': 'Say which line number to cancel.'},
                                status=400)
            result = amend_svc.cancel_line(p, request.user, line_no=line_no,
                                           reason=reason)
    except amend_svc.AmendError as exc:
        return _amend_error(exc)

    return Response({
        'status': p.status,
        'status_label': _status_label(p.status),
        'total': f'{result["total"]:.2f}',
        'currency': p.currency,
        'emptied': bool(result.get('emptied')),
        'changes': amend_svc.change_rows(p),
    }, status=200)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payment_request_detail(request, req_id):
    me = request.user
    p = (PaymentRequest.objects.select_related('task', 'created_by', 'fnb_batch')
         .filter(pk=req_id).first())
    if p is None:
        return Response({'detail': 'Payment request not found.'}, status=404)
    cfo = _cfo_user()
    is_cfo = bool(me.is_superuser or (cfo and me.id == cfo.id))
    is_first = _is_first_approver(me)
    if not _can_view_request(me, p):
        return Response({'detail': 'Permission denied.'}, status=403)

    # Detective controls for the authoriser, so a payment is never approved
    # without seeing the account it goes to (Kago 2026-08-29). All three reuse
    # the existing deterministic helper — no new logic, no AI.
    from .payee_bank_history import (
        last_known_bank, bank_change_warning, normalise_payee)
    known = last_known_bank(p.payee, exclude_pk=p.pk)
    change = bank_change_warning(p.payee, p.account_number, exclude_pk=p.pk)
    name_mismatch = bool(
        p.account_name and p.payee
        and normalise_payee(p.account_name) != normalise_payee(p.payee))
    bank_block = {
        'first_payment': known is None,        # never paid this payee before
        'name_mismatch': name_mismatch,        # holder name ≠ payee name
        'change': change,                      # PAY-BANK-01 dict, or None
        'change_reason': p.bank_change_reason or '',
        'has_flag': bool(known is None or name_mismatch or change),
    }
    # DPA L-5: log every view of a full beneficiary account number, with who and
    # when. Best-effort — must never break the screen it protects.
    if p.account_number:
        try:
            from core.audit_reads import log_read
            log_read(me, 'PaymentRequest', p.pk,
                     'viewed full beneficiary bank account', request)
        except Exception:                                        # noqa: BLE001
            pass
    # "Why is this still open?" — same plain-English reason as the queue list, so the
    # authoriser sees at a glance whether it is just waiting on their FNB sign-off,
    # was rejected, or already looks paid outside Omni (CFO 2026-09-05).
    _why = ('', '')
    if p.status not in PaymentRequest.TERMINAL_STATUSES:
        from django.conf import settings as _settings
        from fnb.email_reconcile import open_reason as _open_reason, catchup_matches as _catchup
        _b = p.fnb_batch if p.fnb_batch_id else None
        _conf = 'none'
        if is_cfo or is_first:
            from .fnb_email_view_helpers import fetch_paid_fnb_emails_cached
            _emails = fetch_paid_fnb_emails_cached(
                getattr(_settings, 'FNB_EMAIL_CATCHUP_LOOKBACK_HOURS', 720))
            _conf = 'unavailable' if _emails is None else 'none'
            if _emails:
                _conf = _catchup(_payment_match_dict(p), _emails).get('confidence', 'none')
        _why = _open_reason(
            batch_status=(getattr(_b, 'status', '') or ''),
            failure_reason=(getattr(_b, 'failure_reason', '') or ''),
            has_batch=bool(_b),
            is_exception=(p.status == PaymentRequest.Status.EXCEPTION),
            email_confidence=_conf,
        )
    return Response({
        'id':             str(p.id),
        'ref':            p.ref,
        'why_open':       _why[0],
        'why_open_tone':  _why[1],
        'entity':         p.entity,
        'category':       p.category,
        'category_label': _category_label(p.category),
        'claim_payee_type': p.claim_payee_type,
        'claim_payee_label': dict(PaymentRequest.ClaimPayeeType.choices).get(
            p.claim_payee_type, ''),
        # B8 / B7 — the approver sees a refund as a refund, and a copy as a copy.
        'is_refund':            p.category in PaymentRequest.REFUND_CATEGORIES,
        'original_payment_ref': p.original_payment_ref or '',
        'duplicated_from_ref':  p.duplicated_from_ref or '',
        'status':         p.status,
        'status_label':   _status_label(p.status),
        # Exception committee (CFO 2026-09-02): why it is with the committee and
        # how far the decision has got, so the drawer is not a dead screen for
        # an exception (Fable 5.1 audit, M2).
        'exception_control':  p.exception_control or '',
        'exception_reason':   p.exception_reason or '',
        'exception_decision': p.exception_decision or '',
        'exception_signoffs': [{
            'signer': (sg.signer.get_full_name() or sg.signer_email) if sg.signer else sg.signer_email,
            'decision': sg.decision, 'at': sg.created_at.isoformat(),
        } for sg in (p.release_signoffs.all() if p.exception_control else [])],
        # The bank REJECTED this payment (FNB batch failed) — show it in red on
        # the authorisation screen with the reason, even when the workflow still
        # reads "paid". No money moved on a failed batch.
        'bank_rejected':  _any_batch_failed(p),
        'bank_reject_reason': _reject_reason(p) or (p.fnb_load_error or ''),
        # May THIS person correct the branch code? Without this the endpoint
        # exists and no screen offers it — the feature is live and the way in
        # is not, which is the trap the notebook records from 17-Sep.
        # NB: 'branch_code' is already in this dict further down — do not add
        # a second one (ruff F601; last key wins, so it looks harmless and is
        # simply a lie in the source).
        'can_correct_branch_code': _may_correct_branch_code(request.user, p),
        # May THIS person record that FNB rejected it in the app? Shown only
        # while the payment is still sitting on the bank's authorisation queue —
        # once the batch is failed or settled there is nothing left to record
        # (Leano Makwapa 2026-09-11).
        'can_mark_fnb_rejected': bool(
            _may_mark_fnb_rejected(request.user)
            and p.fnb_batch_id
            and getattr(p.fnb_batch, 'status', '') in ('submitted', 'acknowledged', 'pending')),
        'first_approver': (p.first_approver.get_full_name() or p.first_approver.username) if p.first_approver else '',
        'first_approved_at': p.first_approved_at.isoformat() if p.first_approved_at else None,
        'decision_notes': p.decision_notes,
        # The CFO OR a finance approver can REJECT a payment that has reached the
        # CFO (PENDING_CFO); the stage previously offered only "Clear" (CFO 2026-09-01).
        'can_reject':     bool((is_cfo or is_first) and p.status == PaymentRequest.Status.PENDING_CFO),
        'can_approve':    bool(is_first and p.status == PaymentRequest.Status.PENDING_FINANCE
                               and p.created_by_id != me.id),
        'can_clear':      bool(is_cfo and p.status == PaymentRequest.Status.PENDING_CFO),
        # Per-line CFO authorisation — "approve 9, hold 1" (2026-08-31). The CFO
        # may tick individual payees at the PENDING_CFO stage; line_progress drives
        # the "9/10 authorised" badge. Money already loaded to FNB at sign-off.
        'can_decide_lines': bool(is_cfo and p.status == PaymentRequest.Status.PENDING_CFO
                                 and len(p.line_items or []) > 1),
        'line_progress':  p.line_progress(),
        'currency':       p.currency,
        'subject':        p.subject,
        # The heading the POP will carry, inherited from SUBJECT as it stood at
        # submission (Kelvin Kimani spec 2026-09-08). Shown so the raiser and
        # the approver can both see what the proof of payment will be titled
        # BEFORE it goes out, instead of discovering a mismatch afterwards.
        'pop_subject':    p.pop_heading(),
        # Bulk or Individual, and the payment count it implies (Kelvin Kimani
        # spec 2026-09-08). show_processing_choice says whether the toggle was
        # ever a live decision on this request — with one line it is not.
        'processing_method':       p.processing_method,
        'processing_method_label': p.processing_method_label(),
        'payment_count':           p.payment_count(),
        'processing_choice_shown': PaymentRequest.show_processing_choice(p.line_items),
        # Lines naming a POP recipient Omni does not hold (Finance spec
        # 2026-09-08). Non-empty means the finance approver must tick pop_ack
        # before sign-off — the second person who clears it.
        'pop_off_list':   pop_off_list_lines(p.line_items or [], payee=p.payee or ''),
        # PAY-BANK-04 / PAY-BANK-DOC (Finance spec 2026-09-08): the bank
        # cross-check is a hard gate, not a note. Present means the finance
        # approver must confirm the details against the attached document
        # before this can be signed off.
        'bank_details_change': bank_details_changed(
            p.payee, bank_name=p.bank_name, branch_code=p.branch_code,
            account_number=p.account_number, exclude_pk=p.pk),
        # Amend / cancel before sign-off (Kelvin Kimani spec 2026-09-08): the
        # window, who may act in it, the readable change history, and — on a
        # cancelled request — who pulled it, when and why.
        'is_amendable':      p.is_amendable,
        'amend_lock_reason': p.amend_lock_reason(),
        'can_amend':         bool(p.is_amendable
                                  and _may_amend_request(request.user, p)),
        'changes':           _amend_change_rows(p),
        'cancelled_reason':  p.cancelled_reason,
        'cancelled_by':      ((p.cancelled_by.get_full_name() or p.cancelled_by.username)
                              if p.cancelled_by_id else ''),
        'cancelled_at':      p.cancelled_at.isoformat() if p.cancelled_at else None,
        'payee':          p.payee,
        'line_items':     p.line_items,
        'total':          str(p.total),
        'account_name':   p.account_name,
        'account_number': p.account_number,
        'bank_name':      p.bank_name,
        'branch_code':    p.branch_code,
        'account_type':   p.account_type,
        'bank':           bank_block,
        'opening_balance': str(p.opening_balance) if p.opening_balance is not None else None,
        'due_date':       p.due_date.isoformat() if p.due_date else None,
        'inputter':       p.inputter,
        'verifier':       p.verifier,
        'summary':        p.summary,
        'formatted_html': p.formatted_html,
        'created_at':     p.created_at.isoformat(),
        'created_by':     (p.created_by.get_full_name() or p.created_by.username) if p.created_by else '',
        'task_id':        str(p.task_id) if p.task_id else None,
        'task_status':    p.task.status if p.task else None,
        'attachments':    [{
            'id':   str(a.id),
            'name': a.original_name or (a.file.name or '').split('/')[-1],
            'uploaded_by': (a.uploaded_by.get_full_name() or a.uploaded_by.username) if a.uploaded_by else '',
            'created_at': a.created_at.isoformat(),
        } for a in p.attachments.all()],
    })


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def payment_request_attachments(request, req_id):
    """GET list / POST upload an optional supporting document (agreement of
    loss, invoice, quote…) on a payment request (CFO 2026-07-15). Multipart
    POST field 'file'. The creator or the CFO may attach/see."""
    me = request.user
    p = PaymentRequest.objects.select_related('task').filter(pk=req_id).first()
    if p is None:
        return Response({'detail': 'Payment request not found.'}, status=404)
    # Anyone who may OPEN the request may see and add its documents: the raiser,
    # the CFO, the finance approvers and — on an exception — the committee
    # members deciding it. It was CFO-or-raiser only, so an approver could read
    # a pack but never attach the supplier's letter or the call-back note to it
    # (Fable 5.1 audit 2026-09-02, M11).
    if not _can_view_request(me, p):
        return Response({'detail': 'Permission denied.'}, status=403)

    if request.method == 'POST':
        from core.api_views import _validate_task_attachment
        from taskboard.models import PaymentRequestAttachment
        f = request.FILES.get('file')
        if not f:
            return Response({'detail': 'No file supplied.'}, status=400)
        err = _validate_task_attachment(f)
        if err:
            return Response({'detail': err}, status=400)
        a = PaymentRequestAttachment.objects.create(
            request=p, file=f, original_name=(f.name or '')[:255], uploaded_by=me)
        return Response({'id': str(a.id),
                         'name': a.original_name or (a.file.name or '').split('/')[-1]},
                        status=201)

    return Response({'attachments': [{
        'id':   str(a.id),
        'name': a.original_name or (a.file.name or '').split('/')[-1],
        'uploaded_by': (a.uploaded_by.get_full_name() or a.uploaded_by.username) if a.uploaded_by else '',
        'created_at': a.created_at.isoformat(),
    } for a in p.attachments.all()]})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payment_request_attachment_file(request, att_id):
    """Authenticated download of a payment-request attachment. Never a raw
    /media/ URL (prod serves DEBUG=False)."""
    from django.http import FileResponse, Http404
    from taskboard.models import PaymentRequestAttachment
    a = (PaymentRequestAttachment.objects
         .select_related('request', 'request__created_by', 'request__task')
         .filter(pk=att_id).first())
    if a is None or not a.file:
        raise Http404
    # Same rule as the pack itself — an approver who can see the lines must be
    # able to open the invoice behind them.
    if not _can_view_request(request.user, a.request):
        return Response({'detail': 'Permission denied.'}, status=403)
    return FileResponse(a.file.open('rb'), as_attachment=True,
                        filename=(a.original_name or a.file.name or 'attachment').split('/')[-1])


# ── Smart fill: paste messy text → structured lines (CFO 2026-07-29) ─────────
# "make their life easy — a place they copy-paste their junk data and DeepSeek
# nicely formats it to fill these fields." This does NOT reinvent anything: it
# reuses omni's existing cleanup engine (core.ai_assist.reasoning_complete +
# the is_safe_for_ai PII firewall — the same path Smart Entry and the spreadsheet
# mapper already use). It is a CONVENIENCE that only SUGGESTS field values; it
# writes nothing and creates nothing. The raiser reviews the filled form and the
# real create endpoint still enforces every control (claim number, invoice dates,
# ADIC-only, bank details, duplicates). So a bad parse can never become a payment —
# at worst the clerk fixes a field the AI misread.
_SMART_FILL_SYSTEM = (
    'You clean up messy pasted text (an email, a WhatsApp message, a list) into '
    'the line items of an insurance payment authorisation. Extract only what is '
    'clearly present — never invent a number. Respond ONLY as JSON:\n'
    '{"payee":"<who is being paid, or empty>",'
    '"lines":[{"description":"<what it is>","amount":"<number only, no currency, '
    'or empty>","invoice_number":"<or empty>","invoice_date":"<YYYY-MM-DD or '
    'empty>","claim_number":"<Graphite claim ref e.g. G2026004287, or empty>"}]}\n'
    'Rules: amount is digits and a dot only (strip P/BWP/commas). Dates as '
    'YYYY-MM-DD; if a date is ambiguous leave it empty rather than guess. One '
    'object per payment line. If you cannot find any line, return an empty lines '
    'array. Do not add commentary.'
)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_request_parse(request):
    """Advisory ONLY. Turn pasted free text into suggested payment lines using
    omni's existing AI-cleanup engine. Writes nothing, creates nothing — the
    caller fills a form with the result and the create endpoint re-validates.

    Returns {ok, payee, lines:[...], note, redactions}. Never 500s — an AI
    outage, an unsafe paste, or a bad/oddly-shaped response all return 200
    ok:false with a plain reason so the clerk just types the lines by hand."""
    text = (request.data or {}).get('text') or ''
    text = str(text).strip()
    if not text:
        return Response({'ok': False, 'reason': 'Paste some text first.'}, status=400)
    if len(text) > 8000:
        text = text[:8000]

    from core import ai_assist
    # PII firewall FIRST — the same guarantee every other AI path here carries.
    # Bank account numbers / IDs / phone numbers are redacted before send; the
    # clerk fills those in the proper fields, they never go to the model.
    rep = ai_assist.is_safe_for_ai(text)
    if not rep.safe:
        return Response({'ok': False,
                         'reason': 'That paste looks like it holds account or ID '
                                   'numbers — type the lines in by hand.'}, status=200)
    try:
        raw = ai_assist.reasoning_complete(
            rep.redacted_text, system_prompt=_SMART_FILL_SYSTEM,
            response_format='json_object', timeout=20, max_tokens=900,
            feature='payment_smart_fill')
    except ai_assist.DeepSeekUnavailable as exc:
        log.warning('smart-fill engine unavailable: %s', exc)
        return Response({'ok': False,
                         'reason': 'The cleanup helper is busy — type the lines '
                                   'in by hand.'}, status=200)

    import json as _json
    try:
        parsed = _json.loads(raw)
    except (ValueError, TypeError):
        return Response({'ok': False,
                         'reason': 'Could not read that paste cleanly — type the '
                                   'lines in by hand.'}, status=200)
    # 'json_object' mode guarantees valid JSON, NOT a JSON *object* — a cheap
    # cascade tier can legally hand back a top-level array or scalar. Shape-check
    # before .get()/slicing, or the "never 500s" promise is a lie (Fable review).
    if not isinstance(parsed, dict):
        return Response({'ok': False,
                         'reason': 'Could not read that paste cleanly — type the '
                                   'lines in by hand.'}, status=200)
    raw_lines = parsed.get('lines')
    if not isinstance(raw_lines, list):
        raw_lines = []

    # Normalise defensively: the model output is untrusted. Keep only the fields
    # the form uses, cap counts and lengths, coerce the amount to a plain number.
    out_lines = []
    for ln in raw_lines[:40]:
        if not isinstance(ln, dict):
            continue
        amt = str(ln.get('amount') or '').replace(',', '').replace('P', '').strip()
        amt = str(_dec(amt)) if amt else ''   # _dec never raises → junk becomes ''/0.00
        out_lines.append({
            'description':    str(ln.get('description') or '').strip()[:200],
            'amount':         amt,
            'invoice_number': str(ln.get('invoice_number') or '').strip()[:60],
            'invoice_date':   str(ln.get('invoice_date') or '').strip()[:10],
            'claim_number':   str(ln.get('claim_number') or '').strip()[:60],
        })
    out_lines = [l for l in out_lines if l['description'] or l['amount']]
    return Response({
        'ok': True,
        'payee': str(parsed.get('payee') or '').strip()[:200],
        'lines': out_lines,
        'note': ('Checked and cleaned — please review every line before you send. '
                 'Nothing was submitted.'),
        'redactions': rep.redactions_made,
    })


# ── Payment History (Finance spec 2026-09-08, Bontle Tendani / Leano Makwapa) ─
# The payment register already reads request-by-request. What Finance asked for
# is the LINE view — "claim number, invoice number, amount, supplier/payee and
# the paid figures already captured per line in the payment request flow" —
# because that is the grain a claim is queried at. Deliberately built on the
# SAME line records the rest of the module uses (_line_rows, the duplicate sweep
# and the authorisation pack all read them), never a second data path.
#
# Read-only. It moves no money, changes no state and posts nothing.

#: Quick-select accepts a month with a year. A month on its own is ambiguous
#: (which year?) and silently picking "this year" is the kind of guess that
#: makes a total wrong without anybody noticing.
def _history_window(params):
    """Resolve the date filters -> (from_date, to_date, label, error).

    Custom range: ?from=YYYY-MM-DD&to=YYYY-MM-DD (either end may stand alone).
    Quick-select: ?year=2026 on its own, or ?year=2026&month=8.
    The quick-select WINS over a custom range when both are sent, because it is
    the control the user just clicked.
    """
    import calendar
    year_raw = (params.get('year') or '').strip()
    month_raw = (params.get('month') or '').strip()
    if month_raw and not year_raw:
        return None, None, '', 'Choose a year as well as a month.'
    if year_raw:
        if not year_raw.isdigit() or not (2000 <= int(year_raw) <= 2100):
            return None, None, '', 'Year must be a four-digit year.'
        year = int(year_raw)
        if month_raw:
            if not month_raw.isdigit() or not (1 <= int(month_raw) <= 12):
                return None, None, '', 'Month must be a number from 1 to 12.'
            month = int(month_raw)
            last = calendar.monthrange(year, month)[1]
            return (_date(year, month, 1), _date(year, month, last),
                    f'{calendar.month_name[month]} {year}', None)
        return _date(year, 1, 1), _date(year, 12, 31), str(year), None

    frm = to = None
    for key, label in (('from', 'from'), ('to', 'to')):
        raw = (params.get(key) or '').strip()
        if not raw:
            continue
        parsed = _parse_iso(raw)
        if parsed is None:
            return None, None, '', f'{label} must be a date, YYYY-MM-DD.'
        if key == 'from':
            frm = parsed
        else:
            to = parsed
    if frm and to and frm > to:
        return None, None, '', 'The from date is after the to date.'
    bits = [f'from {frm.isoformat()}' if frm else '', f'to {to.isoformat()}' if to else '']
    return frm, to, ' '.join(b for b in bits if b), None


def _history_line_status(p, ln) -> tuple:
    """(code, label) for one line on the history screen.

    A cancelled line is shown AS CANCELLED, never dropped — a payment that was
    pulled is exactly the row someone comes looking for later, and nothing in
    this module is ever hard-deleted.
    """
    if isinstance(ln, dict) and ln.get('cancelled'):
        return 'cancelled', 'Cancelled'
    if p.status == PaymentRequest.Status.CANCELLED:
        return 'cancelled', 'Cancelled'
    # The CFO's per-line authorisation, where he made one ("approve 9, hold 1").
    state = PaymentRequest.line_state(ln)
    if state == 'rejected':
        return 'rejected', 'Rejected'
    if state == 'held':
        return 'held', 'Held by the CFO'
    return p.status, _status_label(p.status)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payment_history(request):
    """GET /payment-requests/history/ — the per-line payment history.

    Columns as Finance specified: date, claim number, payee, invoice number,
    amount, status, plus a totals row. Filters: a custom date range, or the
    month/year quick-select.

    Scoped exactly like the request list above: the CFO, a finance approver and
    a read-only QC reader see the estate; anyone else sees only the requests
    they raised. Read-only — no money, no state change.
    """
    from django.db.models import Q
    from django.db.models.functions import Coalesce, TruncDate

    me = request.user
    cfo = _cfo_user()
    sees_all = bool(me.is_superuser or (cfo and me.id == cfo.id)
                    or _is_first_approver(me) or _is_readonly_key_reader(request))

    frm, to, window_label, err = _history_window(request.query_params)
    if err:
        return Response({'detail': err}, status=400)

    qs = (PaymentRequest.objects
          .select_related('created_by')
          # A DRAFT was never submitted, so it is not history — it is a form
          # somebody is still filling in.
          .exclude(status=PaymentRequest.Status.DRAFT))
    if not sees_all:
        qs = qs.filter(created_by=me)

    # The date a row is filed under: the date the money was due to leave when
    # the request carries one, otherwise the date it was raised. Annotated so
    # the filter runs in the database rather than over every row in Python.
    qs = qs.annotate(hist_date=Coalesce('payment_date', TruncDate('created_at')))
    if frm:
        qs = qs.filter(hist_date__gte=frm)
    if to:
        qs = qs.filter(hist_date__lte=to)

    payee_q = (request.query_params.get('payee') or '').strip()
    if payee_q:
        qs = qs.filter(Q(payee__icontains=payee_q)
                       | Q(account_name__icontains=payee_q)
                       | Q(ref__icontains=payee_q))

    # ?copy=1 adds the per-row copy-forward payload the payment-request form
    # uses for "reload previous payments". Off by default: the history screen
    # itself does not need it and must not grow (Leano Makwapa 2026-09-15).
    want_copy = str(request.query_params.get('copy', '')).strip() == '1'

    rows = []
    # Totals per currency — a single grand total across BWP, ZAR and USD would
    # be a meaningless number, and a meaningless total on a finance screen is
    # worse than none. Cancelled lines are counted apart: they are shown, but a
    # payment that was pulled is not payable and must not swell the total.
    totals: dict = {}
    for p in qs.order_by('-hist_date', '-created_at')[:HISTORY_MAX_REQUESTS]:
        cur = p.currency or 'BWP'
        slot = totals.setdefault(cur, {'currency': cur, 'amount': Decimal('0.00'),
                                       'count': 0, 'cancelled_amount': Decimal('0.00'),
                                       'cancelled_count': 0})
        # Keyed by line number, not zipped: _line_rows SKIPS a non-dict entry
        # while keeping the true 1-based index, so zipping the two lists would
        # silently shift every row after a malformed one onto the wrong line.
        by_line = {d['line']: d for d in _line_rows(p)}
        for idx, ln in enumerate(p.line_items or [], start=1):
            detail = by_line.get(idx)
            if detail is None:
                continue
            code, label = _history_line_status(p, ln)
            amount = _dec(detail['amount'])
            rows.append({
                'request_id':   str(p.id),
                'ref':          p.ref,
                'date':         p.hist_date.isoformat() if p.hist_date else None,
                'entity':       p.entity,
                'line':         detail['line'],
                'claim_number': detail['claim_no'],
                # The line's own payee reading, which strips the leading claim
                # token off a description like "G2026004287 CARFIL SERVICES".
                # Falls back to the request payee, then the account holder.
                'payee':        detail['payee'] or p.payee or p.account_name,
                'invoice_number': detail['invoice_no'],
                'description':  detail['description'],
                'currency':     cur,
                'amount':       f'{amount:.2f}',
                'status':       code,
                'status_label': label,
                'cancelled':    code == 'cancelled',
                # ── "Reload previous payments" (Leano Makwapa 2026-09-15).
                # Only rendered on ?copy=1, so the plain history screen is
                # byte-for-byte what it was.
                #
                # What is DELIBERATELY absent: the amount, the invoice number
                # and both dates. A supplier paid every month is paid on a NEW
                # invoice for a NEW figure, so carrying last month's across is
                # not a convenience — it is the paid-twice shape. Everything
                # that genuinely does not change (the supplier, the narration,
                # the GL code, the agreed terms, who gets the proof) copies, and
                # that is where the typing actually went.
                #
                # A cancelled line is shown but never copyable: it was pulled.
                **({'copyable': code != 'cancelled',
                    'copy': {
                        'description':         detail['description'],
                        'gl_code':             str(ln.get('gl_code') or ''),
                        'ref':                 str(ln.get('ref') or ''),
                        'terms_basis':         str(ln.get('terms_basis') or ''),
                        'terms_days':          str(ln.get('terms_days') or ''),
                        'claim_number':        detail['claim_no'],
                        'pop_recipient_name':  str(ln.get('pop_recipient_name') or ''),
                        'pop_recipient_email': str(ln.get('pop_recipient_email') or ''),
                        'copied_from_ref':     p.ref,
                    }} if want_copy else {}),
            })
            if code == 'cancelled':
                slot['cancelled_amount'] += amount
                slot['cancelled_count'] += 1
            else:
                slot['amount'] += amount
                slot['count'] += 1

    return Response({
        'window': window_label,
        'scope': 'all' if sees_all else 'mine',
        'rows': rows,
        'row_count': len(rows),
        'totals': [{'currency': t['currency'],
                    'amount': f'{t["amount"]:.2f}',
                    'count': t['count'],
                    'cancelled_amount': f'{t["cancelled_amount"]:.2f}',
                    'cancelled_count': t['cancelled_count']}
                   for t in sorted(totals.values(), key=lambda t: t['currency'])],
        'truncated': len(rows) > 0 and qs.count() > HISTORY_MAX_REQUESTS,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payment_summary(request):
    """GET /payment-requests/summary/ — the same figures as the 09:30 email.

    CFO 2026-08-09: he approves for seven-plus accountants and asked not to open
    each request to know what is waiting. The screen and the email read the SAME
    function (taskboard.payment_digest.collect) precisely so they can never tell
    him two different things.

    The AI narrative is included, but every number comes from Omni's own
    arithmetic — a model that adds up money will eventually add it up wrong.
    """
    from taskboard import payment_digest

    # This returns the WHOLE payment estate — every request, payee, amount,
    # loader and entity, across all companies. The list it sits above is scoped
    # (ordinary staff see only their own), so leaving this on IsAuthenticated
    # alone let any of the seven accountants read everyone else's requests and
    # the company-wide totals, straight through the SEC-02 entity walls.
    # Found by Fable 2026-08-09, hours after I shipped it.
    me = request.user
    if not (_is_cfo(me) or _is_first_approver(me) or me.is_superuser):
        return Response(
            {'detail': 'The payment summary is for the CFO and finance approvers.'},
            status=403)

    d = payment_digest.collect()
    text, source = payment_digest.narrative(d)

    def money(v):
        return f'{v:.2f}'

    # Collapse spelling variants of one entity ("Alpha Direct Insurance" vs
    # "Alpha Direct Insurance Company") into a single "By company" row by
    # resolving each typed entity to its Company code (Kago 2026-08-29). This is
    # what made the tile read 17 while the two spellings sat apart. Display-level
    # only — no data is changed.
    _ent_roll: dict = {}
    for _k, _v in d['by_entity'].items():
        _code = _entity_code(_k)
        _slot = _ent_roll.setdefault(_code, {'name': _k, 'count': 0, 'total': Decimal('0')})
        _slot['count'] += _v['count']
        _slot['total'] += _v['total']
    from core.models import Company
    _ent_names = {c.code: c.name for c in Company.objects.filter(code__in=list(_ent_roll))}
    by_entity_rows = [
        {'name': _ent_names.get(_code, _slot['name']),
         'count': _slot['count'], 'total': money(_slot['total'])}
        for _code, _slot in sorted(_ent_roll.items(), key=lambda kv: -kv[1]['total'])
    ]

    return Response({
        'generated_at': d['generated_at'].isoformat(),
        'narrative': text,
        'narrative_source': source,
        'open_count': d['open_count'],
        'waiting_cfo_count': len(d['waiting_cfo']),
        'waiting_cfo_total': money(d['total_waiting_cfo']),
        'waiting_finance_count': len(d['waiting_finance']),
        'waiting_finance_total': money(d['total_waiting_finance']),
        'waiting_committee_count': len(d.get('waiting_committee', [])),
        'waiting_committee_total': money(d.get('total_waiting_committee', Decimal('0'))),
        'stale_count': len(d['stale']),
        'settled_24h': len(d['settled_24h']),
        'no_due_date': len(d['no_due_date']),
        'overrides': [
            {'ref': o['ref'], 'payee': o['payee'], 'currency': o['currency'],
             'total': money(o['total']), 'loader': o['loader'],
             'countersigned': o['countersigned'],
             'category': o['category_label']}
            for o in d['overrides']
        ],
        'by_loader': [
            {'name': k, 'count': v['count'], 'total': money(v['total'])}
            for k, v in sorted(d['by_loader'].items(), key=lambda kv: -kv[1]['total'])
        ],
        'by_entity': by_entity_rows,
        'waiting_cfo': [
            {'ref': r['ref'], 'payee': r['payee'], 'currency': r['currency'],
             'total': money(r['total']), 'loader': r['loader'],
             'age_days': r['age_days'], 'entity': r['entity']}
            for r in sorted(d['waiting_cfo'], key=lambda r: -r['age_days'])
        ],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def reports_index(request):
    """GET /api/v1/reports/ — what lives under this prefix.

    It 404'd, while SCOPE_PATHS grants '/api/v1/reports/' to bulk-upload keys —
    so a caller was told it had access to a path that did not exist (Manus
    2026-08-09). Either build the route or drop it from the scope map; a listing
    is the more useful of the two.
    """
    # Walk the resolver properly and build the REAL path. The first version
    # glued '/api/v1/' onto patterns that already began with 'api/', so it
    # advertised '/api/v1/api/reports/' — a 200 pointing at a 404. The 404 moved
    # rather than going away (Manus 2026-08-09).
    from django.urls import get_resolver
    from django.urls.resolvers import URLPattern, URLResolver

    def _walk(patterns, prefix=''):
        for entry in patterns:
            here = prefix + str(entry.pattern)
            if isinstance(entry, URLResolver):
                yield from _walk(entry.url_patterns, here)
            elif isinstance(entry, URLPattern):
                yield here

    paths = sorted({
        '/' + full.lstrip('/')
        for full in _walk(get_resolver().url_patterns)
        if '/reports/' in '/' + full and '<' not in full
    })
    # Manus, 2026-08-09: all 58 of these refused the read-only key with a 401,
    # so the fix replaced a 404 with a directory of locked doors — which looks
    # like success and is the worse failure. Show the caller what it can open,
    # and say plainly what it cannot.
    from core.api_key_auth import _path_allowed_by_scopes
    from core.models import ApiKey
    auth = getattr(request, 'auth', None)
    if isinstance(auth, ApiKey) and not request.user.is_superuser:
        scopes = list(auth.allowed_scopes or ())
        openable = [p for p in paths if _path_allowed_by_scopes(p, scopes, 'GET')]
        return Response({
            'detail': 'Report endpoints your key can open.',
            'reports': openable,
            'hidden_from_you': len(paths) - len(openable),
            'note': ('Paths outside your key\'s scope are not listed — an index '
                     'of doors you cannot open is worse than no index.'),
        })
    return Response({
        'detail': 'Report endpoints available under /api/v1/reports/.',
        'reports': paths or [
            '/api/v1/reports/ma-profit-loss/', '/api/v1/reports/trial-balance/',
            '/api/v1/reports/equity/', '/api/v1/reports/po-outstanding/',
        ],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payee_bank_lookup(request):
    """GET /payment-requests/payee-bank/?payee=NAME

    What did we last pay this payee into? The form calls this as the supplier
    name is typed and fills the bank fields in, so the details are never keyed
    twice (CFO 2026-08-20).

    Deliberately a lookup and not a model. Which account we paid someone into is
    a fact we already hold, and a model asked to recall an account number can be
    confidently wrong on the one field that decides who gets the money. Same
    rule as the FNB reject explainer: code decides, AI explains.
    """
    payee = (request.query_params.get('payee') or '').strip()
    if len(payee) < 3:
        return Response({'found': False})
    # exact=True: prefill only when the typed name IS this payee — a fuzzy
    # neighbour must not hand its account to a new supplier (Fable 5.1 audit).
    known = last_known_bank(payee, exact=True)
    if known is None:
        return Response({'found': False})
    me = request.user
    # The full account number is for the people who approve payments (the CFO
    # and the finance approvers). Everyone else gets the last four digits —
    # enough to recognise the account, not enough to lift the supplier bank
    # book by typing payee names (Fable 5.1 audit 2026-09-02, H4). The form
    # then sends use_known_account=true and the create leg fills the digits in
    # server-side. Every full-number read is logged, as on the detail screen.
    full = bool(_is_cfo(me) or _is_first_approver(me))
    acct = known['account_number'] or ''
    if full and acct:
        try:
            from core.audit_reads import log_read
            log_read(me, 'PayeeBankLookup', payee[:64],
                     'viewed full beneficiary bank account (payee lookup)', request)
        except Exception:                                        # noqa: BLE001
            log.warning('payee lookup: could not write the read log', exc_info=True)
    return Response({
        'found': True,
        'source': known['source'],
        'source_label': known['source_label'],
        'account_name': known['account_name'],
        'account_number': acct if full else (('*' * (len(acct) - 4) + acct[-4:]) if len(acct) > 4 else acct),
        'account_masked': not full,
        'bank_name': known['bank_name'],
        'branch_code': known['branch_code'],
        'account_type': known['account_type'],
        'last_used': known['when'],
        'last_ref': known['ref'],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pop_recipient_options(request):
    """GET /payment-requests/pop-recipients/?claim_number=&payee=

    The POP Recipient dropdown for one payment line (Finance spec 2026-09-08).
    A lookup over records Omni already holds — the vendor register's remembered
    POP address, the contact book, the claimant named on the claim, and the
    Accounts default. No AI: where a proof of payment goes is a lookup, and a
    plausible invention here sends a payment confirmation to a stranger.

    Anything typed that is not on this list is not refused — it is flagged, and
    a second approver clears it at sign-off (PAY-POP-ACK).
    """
    options = pop_linked_recipients(
        claim_number=(request.query_params.get('claim_number') or '').strip(),
        payee=(request.query_params.get('payee') or '').strip())
    return Response({'options': options, 'default': pop_accounts_default()})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payee_bank_check(request):
    """POST /payment-requests/payee-bank-check/  {payee, account_number}

    Ask, before submitting, whether this account differs from the one we last
    paid that payee into — so the form can warn while the person is still
    looking at the screen rather than refusing them at the end.
    """
    body = request.data or {}
    payee = (body.get('payee') or '').strip()
    acct = (body.get('account_number') or '').strip()
    if not payee or not acct:
        return Response({'changed': False})
    warn = bank_change_warning(payee, acct)
    if warn is not None:
        return Response({'changed': True, **warn})
    # No change to report — but this may be a payee we have never paid at all,
    # which is its own control (PAY-BANK-03). Telling the form now lets it ask
    # for the confirmation while the person is still on the screen, instead of
    # the server refusing them at submit.
    first = first_payment_warning(payee, acct)
    if first is not None:
        return Response({'changed': False, 'first_payment': True, **first})
    return Response({'changed': False})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def payment_request_read_invoice(request):
    """POST /payment-requests/read-invoice/ — read an uploaded invoice and hand
    back the fields, so the raiser stops re-keying them (CFO 2026-09-01).

    The reading engine is NOT new: this reuses payments.invoice_read, the same
    reader already used by /payments/new, which the CFO commissioned on
    2026-08-22. It was simply never wired into THIS screen — the one finance
    actually raises payment authorisations on — so the team has been typing
    bank account numbers by hand next to a finished tool.

    SAVES NOTHING. It returns fields for a human to check; the request is only
    created by the normal create endpoint, with every existing control
    (duplicates PAY-DUP-01, supplier terms, claim numbers, PAY-BANK-01 account
    change and PAY-BANK-03 new payee) applied there exactly as before.

    Gated on IsAuthenticated, deliberately matching the gate on RAISING a
    payment request — NOT the stricter maker gate on /payments/read-invoice/.
    A maker gate here would 403 eight people who legitimately raise requests
    today (Finance Managers among them, verified against prod), and reading a
    file the user themselves uploaded to fill a form they are already allowed
    to fill by hand grants no authority they did not have.
    """
    f = request.FILES.get('file')
    if f is None:
        return Response({'detail': 'Attach the invoice as "file".'}, status=400)
    if getattr(f, 'size', 0) > 20 * 1024 * 1024:
        return Response({'detail': 'That file is larger than 20 MB.'}, status=400)
    from payments.invoice_read import read_invoice
    try:
        data = read_invoice(f.read(), mime=getattr(f, 'content_type', '') or '',
                            filename=f.name)
    except Exception:                                          # noqa: BLE001
        # Never surface a stack trace to a clerk, and never let a bad scan take
        # the form down — they can always still type it in.
        log.exception('payment-request read-invoice failed for %s', f.name)
        return Response({'ok': False,
                         'message': 'Could not read that invoice — please type '
                                    'the details in.'}, status=200)
    return Response(data, status=200)


# ═══════════════════════════════════════════════════════════════════════════
# Payment EXCEPTION committee (CFO 2026-09-02)
#
# A fraud-risk exception (a changed bank account; later, anything deemed
# possible fraud) NEVER blocks the raiser — the payment is entered and flagged.
# A committee of THREE of the six-member pool decides it (never the raiser,
# never the CFO): approve -> the payment rejoins the normal flow; reject -> it
# is rejected. An email tells the CFO the moment the committee decides. The CFO
# also gets a records-only "clear" that never holds the payment up. Money never
# moves here — FNB + the CFO's own 2-factor stay the real gate.
# ═══════════════════════════════════════════════════════════════════════════

def _committee_link() -> str:
    from django.conf import settings
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')
    # ?exceptions=1 opens the committee board directly (5-Sep-2026: a member
    # landed on Administration -> Exceptions instead and found nothing).
    return f'{base}/payment-requests?exceptions=1'


def _email_committee_exception(pr, pr_data: dict, *, raised_by: str) -> int:
    """On raise: tell the committee an exception is waiting for their decision.
    Best-effort — never raises, so a flaky mailer can never block a payment."""
    try:
        from core.notifications import send_html_with_cfo_cc
        to = sorted(_committee_emails())
        if not to:
            return 0
        html = (
            '<p>A payment has raised an <strong>exception</strong> that needs a committee decision.</p>'
            '<table style="border-collapse:collapse;font-size:14px;">'
            f'<tr><td style="padding:4px 8px;color:#6B7280;">Reference</td>'
            f'<td style="padding:4px 8px;font-weight:600;">{escape(pr.ref)}</td></tr>'
            f'<tr><td style="padding:4px 8px;color:#6B7280;">Payee</td>'
            f'<td style="padding:4px 8px;">{escape(pr_data.get("payee") or pr_data.get("subject") or "")}</td></tr>'
            f'<tr><td style="padding:4px 8px;color:#6B7280;">Amount</td>'
            f'<td style="padding:4px 8px;">{escape(str(pr_data.get("currency","")))} {pr.total:,.2f}</td></tr>'
            f'<tr><td style="padding:4px 8px;color:#6B7280;">Exception</td>'
            f'<td style="padding:4px 8px;">{escape(pr.exception_control)}</td></tr>'
            f'<tr><td style="padding:4px 8px;color:#6B7280;">Raised by</td>'
            f'<td style="padding:4px 8px;">{escape(raised_by)}</td></tr></table>'
            f'<p style="margin-top:10px;">{escape(pr.exception_reason)}</p>'
            '<p><strong>Three of the six of you must decide this</strong> in Omni -> Payment '
            'Requests -> Exceptions. The payment is not blocked; it proceeds once approved.</p>'
            f'<p><a href="{_committee_link()}">Open the exceptions in Omni</a></p>')
        text = (f'Payment exception needs a committee decision. Ref {pr.ref}, '
                f'{pr.exception_control}. Open Omni -> Payment Requests -> Exceptions.')
        return send_html_with_cfo_cc(
            subject=f'Omni — payment exception needs the committee: {pr.ref}'[:150],
            html=html, to=to, text_fallback=text, cc_cfo=True)
    except Exception:  # noqa: BLE001 — mail must never block a payment
        log.warning('committee-exception mail failed for %s', getattr(pr, 'ref', '?'), exc_info=True)
        return 0


# The amber CFO alert address. The CFO's auto-cc goes to excoboard@, NOT
# pganesharajah@ (delivery is unreliable there) — so the direct alert goes to the
# EXCO board address, the channel the CFO actually watches.
_CFO_ALERT_EMAIL = 'excoboard@alphadirect.co.bw'


def _alert_cfo_research(pr, pr_data: dict, *, raised_by: str) -> None:
    """🔴 CFO 2026-09-09: every payment exception raises an AMBER alert to the
    CFO — an Omni TASK on his account AND an email — telling him to research the
    payment (payee, invoice, claim, bank) BEFORE it is paid. The committee still
    decides; this only makes sure the CFO is looped in to do his own diligence.
    Best-effort — an alert must NEVER block or unwind a payment."""
    control = pr.exception_control or 'exception'
    amount = f'{pr_data.get("currency","")} {pr.total:,.2f}'
    payee = pr_data.get('payee') or pr_data.get('subject') or '—'
    # 1) An Omni task on the CFO's own account.
    try:
        cfo = _cfo_user()
        if cfo is not None:
            OmniTask.objects.create(
                assigner=cfo, assignee=cfo,
                title=f'⚠️ Research before paying — {pr.ref} ({control})'[:200],
                body=(f'A payment has raised an exception ({control}). The committee '
                      f'decides it, but please do your own research BEFORE it is paid.\n\n'
                      f'Reference : {pr.ref}\nPayee     : {payee}\nAmount    : {amount}\n'
                      f'Raised by : {raised_by}\n\n{pr.exception_reason}\n\n'
                      'Check the payee, the invoice/claim and the bank details against '
                      'the source before this money goes out. Omni moves no money — the '
                      'bank plus your phone approval is the gate.')[:5000],
                priority=OmniTask.Priority.HIGH,
                status=OmniTask.Status.PENDING,
                source='payment_request',
            )
    except Exception:  # noqa: BLE001 — the task must never block a payment
        log.warning('CFO research task failed for %s', getattr(pr, 'ref', '?'), exc_info=True)
    # 2) An amber email to the CFO/EXCO board.
    try:
        from core.notifications import send_html_with_cfo_cc
        html = (
            '<div style="border-left:4px solid #F4A623;padding:8px 12px;background:#FEF7EC;">'
            '<p style="margin:0;font-weight:700;color:#B45309;">⚠️ Payment exception — '
            'research before paying</p></div>'
            '<table style="border-collapse:collapse;font-size:14px;margin-top:8px;">'
            f'<tr><td style="padding:4px 8px;color:#6B7280;">Reference</td>'
            f'<td style="padding:4px 8px;font-weight:600;">{escape(pr.ref)}</td></tr>'
            f'<tr><td style="padding:4px 8px;color:#6B7280;">Payee</td>'
            f'<td style="padding:4px 8px;">{escape(payee)}</td></tr>'
            f'<tr><td style="padding:4px 8px;color:#6B7280;">Amount</td>'
            f'<td style="padding:4px 8px;">{escape(amount)}</td></tr>'
            f'<tr><td style="padding:4px 8px;color:#6B7280;">Exception</td>'
            f'<td style="padding:4px 8px;">{escape(control)}</td></tr>'
            f'<tr><td style="padding:4px 8px;color:#6B7280;">Raised by</td>'
            f'<td style="padding:4px 8px;">{escape(raised_by)}</td></tr></table>'
            f'<p style="margin-top:10px;">{escape(pr.exception_reason)}</p>'
            '<p>The committee decides this. Please research the payee, the invoice / claim '
            'and the bank details before it is paid. A task is waiting on your Omni account.</p>')
        text = (f'Payment exception — research before paying. Ref {pr.ref}, {control}, '
                f'{amount}. A task is on your Omni account.')
        send_html_with_cfo_cc(
            subject=f'⚠️ Omni — research before paying: {pr.ref}'[:150],
            html=html, to=[_CFO_ALERT_EMAIL], text_fallback=text, cc_cfo=False)
    except Exception:  # noqa: BLE001 — mail must never block a payment
        log.warning('CFO research alert email failed for %s', getattr(pr, 'ref', '?'), exc_info=True)


def _email_exception_decided(pr, decision: str, signoffs) -> int:
    """On decision: tell the CFO (and EXCO) the committee decided. His spec:
    'When the committee makes a decision, it will notify us by email.'"""
    try:
        from core.notifications import send_html_with_cfo_cc
        who = ', '.join(sorted({(s.signer_email or '') for s in signoffs if s.signer_email})) or '—'
        verdict = 'APPROVED' if decision == 'approve' else 'REJECTED'
        colour = '#166534' if decision == 'approve' else '#991B1B'
        onward = ('The payment has rejoined the normal flow and is waiting for finance sign-off.'
                  if decision == 'approve'
                  else 'The payment has been rejected and will not proceed.')
        # The RAISER is told too — the pop promised "you will be told the moment
        # they decide" and nothing kept that promise (Fable 5.1 audit, M1).
        raiser = ((pr.created_by.email or '').strip() if pr.created_by else '')
        to = [raiser] if raiser else []
        html = (
            '<p>The committee has made a decision on a payment exception.</p>'
            f'<p style="font-size:16px;font-weight:700;color:{colour};">Committee {verdict}</p>'
            '<table style="border-collapse:collapse;font-size:14px;">'
            f'<tr><td style="padding:4px 8px;color:#6B7280;">Reference</td>'
            f'<td style="padding:4px 8px;font-weight:600;">{escape(pr.ref)}</td></tr>'
            f'<tr><td style="padding:4px 8px;color:#6B7280;">Payee</td>'
            f'<td style="padding:4px 8px;">{escape(pr.payee or pr.subject or "")}</td></tr>'
            f'<tr><td style="padding:4px 8px;color:#6B7280;">Amount</td>'
            f'<td style="padding:4px 8px;">{escape(pr.currency)} {pr.total:,.2f}</td></tr>'
            f'<tr><td style="padding:4px 8px;color:#6B7280;">Exception</td>'
            f'<td style="padding:4px 8px;">{escape(pr.exception_control)}</td></tr>'
            f'<tr><td style="padding:4px 8px;color:#6B7280;">Decided by</td>'
            f'<td style="padding:4px 8px;">{escape(who)}</td></tr></table>'
            f'<p style="margin-top:10px;">{escape(onward)}</p>'
            '<p>CFO: for your records, you can sign it off and clear it in Omni -> Payment '
            'Requests -> Exceptions. That is records-only — it never holds the payment up.</p>'
            f'<p><a href="{_committee_link()}">Open it in Omni</a></p>')
        text = f'Committee {verdict} the exception on payment {pr.ref}. {onward}'
        return send_html_with_cfo_cc(
            subject=f'Omni — committee {verdict.lower()} a payment exception: {pr.ref}'[:150],
            html=html, to=to, text_fallback=text, cc_cfo=True)
    except Exception:  # noqa: BLE001 — mail must never block a payment
        log.warning('exception-decided mail failed for %s', getattr(pr, 'ref', '?'), exc_info=True)
        return 0


def _decide_exception(pr, decided: str, actor, signoffs, *,
                      note: str = 'Committee rejected the exception.') -> None:
    """Apply a decision to a LOCKED exception, inside the caller's transaction.

    approve -> the request rejoins the normal flow (pending_finance) and its
    task is retitled as the finance sign-off it now is; reject -> rejected, the
    task cancelled. Money never moves here.
    """
    now = timezone.now()
    pr.exception_decision = decided
    pr.exception_decided_at = now
    pr.status = (PaymentRequest.Status.PENDING_FINANCE if decided == 'approve'
                 else PaymentRequest.Status.REJECTED)
    fields = ['exception_decision', 'exception_decided_at', 'status', 'updated_at']
    task = pr.task
    if decided == 'reject':
        pr.rejected_by = actor
        pr.rejected_at = now
        pr.decision_notes = ((pr.decision_notes or '') + '\n' + note).strip()[:2000]
        fields += ['rejected_by', 'rejected_at', 'decision_notes']
        if task and task.status != OmniTask.Status.CANCELLED:
            task.status = OmniTask.Status.CANCELLED
            task.completed_at = now
            task.save(update_fields=['status', 'completed_at', 'updated_at'])
    elif task:
        # The finance approver's task now reads as what it is — a sign-off
        # waiting on them — not "committee decision needed" (Fable 5.1 audit, M1).
        task.title = _task_title(pr.entity, pr.category, pr.currency, pr.total,
                                 prefix='Payment authorisation (finance sign-off)')
        task.body = ((task.body or '')
                     + f'\n\nCOMMITTEE APPROVED the exception on {now:%d %b %Y %H:%M}. '
                       'This is now your finance sign-off.')[:5000]
        task.save(update_fields=['title', 'body', 'updated_at'])
    pr.save(update_fields=fields)


def _after_exception_decided(pr, decided: str, signoffs) -> None:
    """After commit: tell the CFO/EXCO and the raiser; on approve, tell the
    finance approver it is now theirs (the normal stage-1 email)."""
    _email_exception_decided(pr, decided, signoffs)
    if decided == 'approve' and pr.task:
        try:
            raised_by = ((pr.created_by.get_full_name() or pr.created_by.username)
                         if pr.created_by else '')
            _email_authorisation(pr.task, _pr_public_dict(pr), stage='finance',
                                 summary=pr.summary or '', raised_by=raised_by)
        except Exception:  # noqa: BLE001 — mail must never block a payment
            log.warning('finance sign-off mail after committee approve failed for %s',
                        getattr(pr, 'ref', '?'), exc_info=True)


def _exception_row(pr) -> dict:
    signoffs = list(pr.release_signoffs.all())
    return {
        'id': str(pr.id), 'ref': pr.ref, 'entity': pr.entity,
        'payee': pr.payee or pr.subject or '', 'currency': pr.currency,
        'total': str(pr.total), 'status': pr.status,
        'exception_control': pr.exception_control,
        'exception_reason': pr.exception_reason,
        # The raiser's own explanation of the change, so the committee does not
        # decide blind (Fable fix 1). It is already stored; only the finance
        # approver's drawer showed it until now.
        'bank_change_reason': pr.bank_change_reason or '',
        'exception_raised_at': pr.exception_raised_at.isoformat() if pr.exception_raised_at else None,
        'exception_decision': pr.exception_decision or '',
        'exception_decided_at': pr.exception_decided_at.isoformat() if pr.exception_decided_at else None,
        'exception_cleared_at': pr.exception_cleared_at.isoformat() if pr.exception_cleared_at else None,
        'raised_by': (pr.created_by.get_full_name() or pr.created_by.username) if pr.created_by else '—',
        'raised_by_id': str(pr.created_by_id) if pr.created_by_id else None,
        'signoffs': [{
            'signer': (s.signer.get_full_name() or s.signer_email) if s.signer else s.signer_email,
            'decision': s.decision, 'is_independent': s.is_independent,
            'at': s.created_at.isoformat(),
        } for s in signoffs],
        # Files on the request, so a committee member can see and pick the
        # bank-error proof before releasing a PAY-PREM-01 exception.
        'attachments': [{
            'id': str(a.id),
            'name': a.original_name or (a.file.name or '').rsplit('/', 1)[-1],
            'uploaded_by': (a.uploaded_by.get_full_name() or a.uploaded_by.username) if a.uploaded_by else '',
            'at': a.created_at.isoformat(),
        } for a in pr.attachments.all()],
        'approvals': sum(1 for s in signoffs if s.decision == 'approve'),
        'required': COMMITTEE_SIGNOFFS_REQUIRED,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payment_exceptions(request):
    """The exceptions board. A committee member sees exceptions still open (to
    decide); the CFO also sees decided ones he has not yet cleared (records)."""
    me = request.user
    is_member = _is_committee_member(me)
    is_cfo = _is_cfo(me)
    if not (is_member or is_cfo):
        return Response({'detail': 'Only a committee member or the CFO can see the exceptions board.'},
                        status=403)
    open_qs = (PaymentRequest.objects
               .filter(status=PaymentRequest.Status.EXCEPTION)
               .select_related('created_by').prefetch_related('release_signoffs', 'attachments')
               .order_by('-exception_raised_at'))
    to_clear = []
    if is_cfo:
        to_clear = (PaymentRequest.objects
                    .filter(exception_decision__in=('approve', 'reject'),
                            exception_cleared_at__isnull=True)
                    .select_related('created_by').prefetch_related('release_signoffs', 'attachments')
                    .order_by('-exception_decided_at'))
    return Response({
        'open': [_exception_row(p) for p in open_qs],
        'to_clear': [_exception_row(p) for p in to_clear],
        'required': COMMITTEE_SIGNOFFS_REQUIRED,
        'is_committee_member': is_member, 'is_cfo': is_cfo,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_exception_signoff(request, pk):
    """A committee member decides a payment exception (three of six decide).
    body: {decision: approve|reject, called_who?, called_number?, note?}"""
    me = request.user
    if not _is_committee_member(me):
        return Response({'detail': 'Only a payment committee member can decide an exception.'},
                        status=403)
    try:
        pr = PaymentRequest.objects.get(pk=pk)
    except PaymentRequest.DoesNotExist:
        return Response({'detail': 'Payment request not found.'}, status=404)
    if pr.status != PaymentRequest.Status.EXCEPTION:
        return Response({'detail': f'This request is {_status_label(pr.status)} — it is not an open exception.'},
                        status=409)
    if pr.created_by_id == me.id:
        return Response({'detail': 'You cannot decide an exception on a payment you raised.'},
                        status=403)

    body = request.data or {}
    decision = (body.get('decision') or '').strip().lower()
    if decision not in ('approve', 'reject'):
        return Response({'detail': 'decision must be approve or reject.'}, status=400)
    called_who = (body.get('called_who') or '').strip()
    called_number = (body.get('called_number') or '').strip()
    # Approving a changed bank account needs a real call-back on record — a tick
    # is not enough (invoice-redirection fraud).
    if decision == 'approve' and pr.exception_control == 'PAY-BANK-01' and not (called_who and called_number):
        return Response({'detail': 'To approve a changed bank account you must record who you phoned '
                                   'and the number you used — from our records, never the invoice.'},
                        status=400)

    # Approving a premium-lapse exception (PAY-PREM-01, Kago memo v2, GC 3.B) needs
    # the SPECIFIC bank-error proof designated — not just any file (the raiser may
    # have attached an invoice at raise). Mirrors the PAY-BANK-01 call-back: the
    # member names the exact proof and it goes on the release record.
    import uuid as _uuid
    from integrations.claim_insight import PREMIUM_EXCEPTION_CONTROL
    _has_premium_block = (pr.exception_control == PREMIUM_EXCEPTION_CONTROL
                          or f'[{PREMIUM_EXCEPTION_CONTROL}]' in (pr.exception_reason or ''))
    proof_att = None
    if decision == 'approve' and _has_premium_block:
        _raw = (body.get('proof_attachment_id') or '').strip()
        try:
            proof_att = pr.attachments.filter(pk=_uuid.UUID(_raw)).first() if _raw else None
        except (ValueError, TypeError, AttributeError):
            proof_att = None
        if proof_att is None:
            return Response({'detail': 'To release this claim, attach the bank-error proof to the '
                                       'payment and pick it here (GC 3.B) — the premium for the '
                                       'period of loss was not received.'},
                            status=400)

    from django.db import IntegrityError, transaction
    email = (getattr(me, 'email', '') or '').strip().lower()
    try:
        with transaction.atomic():
            # Lock the request for the whole decision so two members signing in
            # the same second cannot both count to three, or resolve an approve
            # and a reject each their own way (Fable 5.1 audit 2026-09-02).
            # No select_related here: FOR UPDATE cannot lock the nullable side
            # of an outer join in Postgres; task/created_by load lazily.
            pr = PaymentRequest.objects.select_for_update().get(pk=pk)
            if pr.status != PaymentRequest.Status.EXCEPTION:
                return Response({'detail': f'This request is {_status_label(pr.status)} — it is not an open exception.'},
                                status=409)
            _note = (body.get('note') or '').strip()
            if proof_att is not None:
                _note = (f'Bank-error proof: {proof_att.original_name or proof_att.id}. '
                         + _note).strip()
            PaymentReleaseSignoff.objects.create(
                request=pr, signer=me, signer_email=email, decision=decision,
                is_independent=_is_independent_committee(me),
                called_who=called_who[:120], called_number=called_number[:40],
                note=_note[:2000])
            signoffs = list(pr.release_signoffs.all())
            approvals = sum(1 for s in signoffs if s.decision == 'approve')
            # The CFO's rule: ANY reject ends it, at once; three approvals
            # release it. Waiting for three signatures after a reject left a
            # payment one member had called fraud sitting in limbo with no way
            # out (Fable 5.1 audit 2026-09-02, H2).
            decided = None
            if any(s.decision == 'reject' for s in signoffs):
                decided = 'reject'
            elif approvals >= COMMITTEE_SIGNOFFS_REQUIRED:
                decided = 'approve'
            if decided:
                _decide_exception(pr, decided, me, signoffs)
    except IntegrityError:
        return Response({'detail': 'You have already signed off this exception.'}, status=409)
    if decided:
        _after_exception_decided(pr, decided, signoffs)
    return Response({
        'status': pr.status,
        'exception_decision': pr.exception_decision or '',
        'approvals': approvals,
        'signoffs': len(signoffs),
        'required': COMMITTEE_SIGNOFFS_REQUIRED,
        'decided': bool(decided),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_exception_clear(request, pk):
    """The CFO signs off + clears a decided exception FOR HIS RECORDS ONLY.
    NON-BLOCKING — this never gates the payment (it already proceeded on the
    committee's decision)."""
    me = request.user
    if not _is_cfo(me):
        return Response({'detail': 'Only the CFO clears an exception for the records.'}, status=403)
    try:
        pr = PaymentRequest.objects.get(pk=pk)
    except PaymentRequest.DoesNotExist:
        return Response({'detail': 'Payment request not found.'}, status=404)
    if not pr.exception_decision:
        # The CFO may CLOSE an undecided exception (CFO 2026-09-02): before this
        # an exception with one or two signatures had no exit at all (Fable 5.1
        # audit, H2). Closing REJECTS the payment, with the reason on record.
        body = request.data or {}
        if not _truthy(body.get('close')):
            return Response({'detail': 'The committee has not decided this exception yet. '
                                       'To close it yourself (this rejects the payment), send '
                                       'close=true with a reason.'}, status=409)
        reason = (body.get('reason') or '').strip()
        if len(reason) < 5:
            return Response({'detail': 'Give a short reason for closing this exception.'}, status=400)
        from django.db import transaction
        with transaction.atomic():
            locked = PaymentRequest.objects.select_for_update().get(pk=pr.pk)
            if locked.status != PaymentRequest.Status.EXCEPTION:
                return Response({'detail': 'This exception has already been decided.'}, status=409)
            signoffs = list(locked.release_signoffs.all())
            _decide_exception(locked, 'reject', me, signoffs,
                              note=f'Closed by the CFO: {reason[:500]}')
            locked.exception_cleared_by = me
            locked.exception_cleared_at = timezone.now()
            locked.save(update_fields=['exception_cleared_by', 'exception_cleared_at', 'updated_at'])
        _after_exception_decided(locked, 'reject', signoffs)
        return Response({'cleared': True, 'closed': True, 'status': locked.status,
                         'cleared_at': locked.exception_cleared_at.isoformat()})
    if pr.exception_cleared_at:
        return Response({'detail': 'Already cleared.'}, status=200)
    pr.exception_cleared_by = me
    pr.exception_cleared_at = timezone.now()
    pr.save(update_fields=['exception_cleared_by', 'exception_cleared_at', 'updated_at'])
    return Response({'cleared': True, 'cleared_at': pr.exception_cleared_at.isoformat()})
