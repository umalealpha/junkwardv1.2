"""
taskboard/fnb_autoload.py

Turn a signed-off payment request into an FNB load, so nobody types it twice.

Why this exists (CFO 2026-08-20): "the team loads the payment request. and then
they load the payments again in FNB. So now create a link between payment
request, which will automatically translate into FNB loading."

What this does NOT do is move money, and that is the whole reason it is safe to
do automatically. The CFO, same day: "even in omni fnb area if I approve, money
doesn't leave — it goes to fnb actual banking system where I need to approve
again in the bank … I want to authorize all the payments through my phone, which
has dual factor authorization." So loading here fills his FNB queue; the payment
leaves only when he authorises it in the bank.

Design rules, in order of how much they matter:

1. **It can never break a sign-off.** The load runs AFTER the request is
   committed as signed-off, and every failure is caught and written to
   `fnb_load_error`. A bank hiccup must not cost finance their approval.
2. **A failure is never silent.** Anything that stops the load is recorded on
   the request in words a person can act on. The 50-day RR10 incident was
   exactly a silent bank refusal.
3. **It cannot load twice.** `fnb_batch` is the guard; a request that already
   has one is left alone.
4. **It guesses nothing about whose money it is.** `entity` on a payment request
   is a free-text NAME ("Alpha Direct Insurance Company South Africa") and the
   Company records use different ones ("Alpha Direct South Africa"), so a fuzzy
   match could pay from the wrong entity's account. Resolution is exact-only,
   with an explicit alias map, and it REFUSES rather than picks something close.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from .models import money_dec
from .narration_templates import PaymentNarrationType, build_defaults

log = logging.getLogger(__name__)


class AutoLoadNotPossible(Exception):
    """A readable reason the load did not happen. Never a 500."""


# ---------------------------------------------------------------------------
# Whose money, and from which account
# ---------------------------------------------------------------------------
def _resolve_company(entity: str):
    """Company for a payment request's entity, or None.

    Exact matches only, then an explicit alias map. Deliberately no fuzzy or
    prefix matching: 'Alpha Direct Insurance Company South Africa' would
    plausibly match Alpha Direct Insurance AND Alpha Direct South Africa, and
    picking one would mean paying from the wrong company's bank account.
    """
    from core.models import Company

    name = (entity or '').strip()
    if not name:
        return None

    aliases = getattr(settings, 'PAYMENT_REQUEST_ENTITY_ALIASES', {}) or {}
    mapped = aliases.get(name) or aliases.get(name.lower())
    if mapped:
        return Company.objects.filter(code__iexact=str(mapped)).first()

    return (Company.objects.filter(code__iexact=name).first()
            or Company.objects.filter(name__iexact=name).first())


def source_account_number_for(category: str) -> str:
    """Which of our accounts pays this KIND of request.

    CFO 2026-08-21: "claims payments will go through alpha Direct claims
    accounts." Claims leave the claims account; everything else leaves the
    operating account. One global paying account would have debited claims out
    of the operating account, which is wrong on the bank statement and wrong in
    the ledger.

    Configured as a map so a new category never silently inherits somebody
    else's account:

        PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS =
            {"claim": "<the claims account number>",
             "default": "<the cheque account number>"}

    The real numbers live only in the server's own settings file, never here.

    A category with no entry and no "default" resolves to nothing, and the
    caller refuses the load with a readable reason. Refusing beats guessing
    which account to debit.
    """
    raw = getattr(settings, 'PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS', None) or {}
    if isinstance(raw, str):
        # Someone set the old single-value setting. Honour it as the default
        # rather than silently ignoring their configuration.
        return raw.strip()
    cat = (category or '').strip().lower()
    return str(raw.get(cat) or raw.get('default') or '').strip()


def _resolve_source_account(company, category: str = ''):
    """The FNB account a request is paid FROM — right account, right company.

    Named by account number in settings rather than guessed: several accounts on
    record have no number set at all. Scoped hard to the company, because the
    existing EFT submit view already refuses to debit one company's FNB account
    for another company's payments and an automatic path must not be the loose
    one. Returns None when nothing matches; the caller turns that into a
    readable refusal.
    """
    from banking.models import BankAccount

    wanted = source_account_number_for(category)
    if not wanted or company is None:
        return None
    return (BankAccount.objects
            .filter(account_number=wanted,
                    gl_account__owner_company=company)
            .first())


def auto_load_enabled() -> bool:
    """Master switch. On by default — the CFO asked for the link — but a single
    setting turns it off without a deploy if it ever misbehaves."""
    return bool(getattr(settings, 'PAYMENT_REQUEST_AUTO_FNB', True))


# ---------------------------------------------------------------------------
# The payment behind the request
# ---------------------------------------------------------------------------
def build_payments_for_request(pr, company, source_account) -> list:
    """Raise the once-off Payment ROWS a signed-off request will be paid by.

    BULK -> one payment for the request total, which is what this module has
    always done. INDIVIDUAL -> one payment PER payable invoice line, each
    carrying its own invoice reference, so the bank file shows N rows and the
    CFO approving on his phone sees which invoice each one settles (CFO
    2026-09-08, answering Kelvin Kimani's open question: Individual must
    produce the separate bank rows, not only say so on the document).

    Cancelled lines are never paid — they are retained on the record and
    skipped here.

    `created_by` is the person who RAISED the request, not whoever is signing it
    off. That is both true and useful: the release check requires a different
    person to send it than prepared it, and a payment request already cannot be
    signed off by its own raiser — so the two-person rule is satisfied by the
    workflow itself rather than by an exemption.
    """
    from billing.models import Contact
    from payments.models import Payment

    acct = (pr.account_number or '').strip()
    if not acct:
        # F8: no edit endpoint and no second sign-off exist, so "fix it and
        # sign off again" would be an instruction nobody can follow.
        raise AutoLoadNotPossible(
            'No payee account number on the request, so there is nothing to '
            'load. This one has to be typed into FNB by hand.')

    # F7: a non-BWP request would write a wrong amount_bwp at rate 1 (the
    # ceiling and the reports both read that column) and FNB would refuse the
    # currency anyway. Refuse it here, visibly, rather than send it.
    if (pr.currency or 'BWP').upper() != 'BWP':
        raise AutoLoadNotPossible(
            f'{pr.currency} payments are not loaded automatically yet — this '
            'one has to be typed into FNB by hand.')

    # The GL comes WITH the paying account. Picking a bank GL by lowest code
    # was the same guess this module refuses everywhere else.
    bank_gl = getattr(source_account, 'gl_account', None)
    if bank_gl is None:
        raise AutoLoadNotPossible(
            'The paying FNB account has no GL account behind it, so the '
            'payment cannot be raised.')

    sentinel = (Contact.objects
                .filter(company=company, contact_type='vendor',
                        name='Ad-hoc / One-off Payee')
                .order_by('created_at').first())
    if sentinel is None:
        sentinel = Contact.objects.create(
            company=company, contact_type='vendor',
            name='Ad-hoc / One-off Payee',
            currency_code_id=(pr.currency or 'BWP'))

    payee = (pr.account_name or pr.payee or pr.subject or pr.ref)[:200]

    # ── which rows are we writing? ───────────────────────────────────────────
    from .models import PaymentRequest as _PR
    lines = _PR.payable_lines(pr.line_items)
    individual = (
        pr.processing_method == _PR.ProcessingMethod.INDIVIDUAL
        and _PR.show_processing_choice(pr.line_items)
    )

    # THE MONEY GUARD. The parts must equal TOTAL PAYABLE to the thebe before a
    # single row reaches the bank file — under either method, because bulk's one
    # row is the same sum. A request that does not reconcile is refused here
    # rather than sent and argued about afterwards. Same parser on both sides
    # (models.money_dec): two parsers is how the P90k cap was bypassed.
    recon = _PR.reconciliation_error(pr.line_items, pr.total,
                                     currency=(pr.currency or 'BWP'))
    if recon:
        raise AutoLoadNotPossible(recon)

    if individual:
        return [
            _once_off_payment(
                pr, company, bank_gl, sentinel, payee, acct,
                amount=money_dec(ln.get('amount')),
                line_no=i,
                invoice=str(ln.get('invoice_number') or '').strip(),
                narration_extra=str(ln.get('description') or '').strip(),
                line=ln,
            )
            for i, ln in enumerate(lines, start=1)
        ]

    return [_once_off_payment(
        pr, company, bank_gl, sentinel, payee, acct,
        amount=money_dec(pr.total or 0),
        line_no=0, invoice='', narration_extra='', line=None,
    )]


def _line_reference(base: str, invoice: str) -> str:
    """The reference the bank line carries, inside FNB's 35 characters.

    Under Individual every row needs its own, or N identical references make
    the CFO's approval list unreadable and the FNB email auto-reconcile cannot
    tell the rows apart. The INVOICE is the discriminator, so it is reserved
    first and only the base is trimmed — the same rule as the broker-commission
    reference. fnb_text folds anything FNB rejects (an em dash in a reference
    rejects the whole batch on RR10).
    """
    from fnb.payments import fnb_text
    invoice = fnb_text(invoice, 18).strip()
    if not invoice:
        return fnb_text(base, 35)
    room = 35 - len(invoice) - 1
    base = fnb_text(base, max(room, 0)).rstrip(' -/,.')
    return f'{base} {invoice}'.strip() if base else invoice


# The payment types whose bank wording is built ENTIRELY out of one line — its
# claim number and its invoice number. Those are the ones a per-line rebuild can
# reproduce faithfully. Client and internal refunds also need the policy number
# and the refund type, which the request does not store, and OTHER is typed by
# hand, so both keep the append path below.
_LINE_BUILT_TYPES = frozenset({
    PaymentNarrationType.SUPPLIER_INVOICE,
    PaymentNarrationType.REPAIR,
    PaymentNarrationType.AOL,
    PaymentNarrationType.CIL,
    PaymentNarrationType.THIRD_PARTY,
    PaymentNarrationType.EX_GRATIA,
})


def _request_wording(pr, line_items):
    """What the wording template produces for `line_items` on this request."""
    return build_defaults(
        payment_type=(pr.bank_payment_type or ''),
        line_items=line_items,
        payee=(pr.payee or ''),
        account_name=(pr.account_name or ''),
    )


def _operator_typed_wording(pr) -> bool:
    """True when a person typed the bank wording instead of taking the default.

    Their words must survive a per-line rebuild — Finance's rule is that anyone
    raising a payment may edit both fields and the control sits at approval. The
    test is whether what is stored is exactly what the template would have
    produced for the whole request: capture writes the template verbatim when
    the raiser leaves the fields blank, so anything else was typed.
    """
    stored = (pr.bank_narration or '').strip()
    if not stored:
        return False
    return stored != ((_request_wording(pr, pr.line_items)['narration'] or '').strip())


def _wording_for_line(pr, line):
    """The reference and narration for ONE line, built from THAT line's own
    claim and invoice number — or None when this line cannot be rebuilt.

    THE BUG THIS FIXES (Pako Kago / Kago Tshutlhedi, 9-Sep-2026, batch
    `EFT 2 payments 000149 (O)`). The request's wording is built once, from the
    FIRST payable line, and the per-line code below only swapped in the line's
    own INVOICE number. So a request covering two claims paid two rows that both
    quoted the first line's claim:

        line 1  claim G2026004782 inv 3522 -> ALPHA DIRECT G2026004782 3522 - 3522
        line 2  claim G2026004829 inv 3525 -> ALPHA DIRECT G2026004782 3522 - 3525

    Claim G2026004829 never reached the bank at all, the repairer could not tell
    the two rows apart (FNB shows the head of the reference, and the head was
    identical), and the statement cannot be matched back to the second claim.
    The same defect had already been fixed for the invoice number; the claim
    number was still taken from the request. Building each line's wording from
    that line is the fix for both, and for every other field the template reads.

    Returns None — leaving the append path in charge — when the type is not one
    the line alone can build, when the operator typed their own wording, or when
    the line is missing a number the template needs (a half-built reference such
    as a bare 'AOL' matches nothing, so the existing floor is better).
    """
    if not isinstance(line, dict):
        return None
    if (pr.bank_payment_type or '').strip().lower() not in _LINE_BUILT_TYPES:
        return None
    if _operator_typed_wording(pr):
        return None
    tpl = _request_wording(pr, [line])
    if tpl['missing'] or not tpl['narration'] or not tpl['our_reference']:
        return None
    return tpl


def _once_off_payment(pr, company, bank_gl, sentinel, payee, acct, *,
                      amount, line_no: int, invoice: str, narration_extra: str,
                      line=None):
    """One unsaved once-off Payment row. `line_no` 0 means the whole request."""
    from payments.models import Payment

    base_ref = (pr.bank_our_reference or pr.ref)
    base_narr = (pr.bank_narration or pr.subject or pr.ref)
    if line_no:
        # A per-line reference and narration, so the bank line names its own
        # CLAIM and its own invoice. `reference` stays unique per row too — it
        # is what the retry below matches on, so a second attempt reuses these
        # rows instead of raising a duplicate payment.
        tpl = _wording_for_line(pr, line)
        if tpl is not None:
            # The template's own reference carries the claim and the payment
            # type but not the invoice number, and the invoice is what tells two
            # rows on the same claim apart — so it is still appended, exactly as
            # test_individual_eft_rows pins it.
            our_ref = _line_reference(tpl['our_reference'], invoice)
            narration = tpl['narration']
        else:
            # Nothing rebuildable from the line itself: the operator's own
            # wording, or a type the line cannot build. Keep the request's
            # wording and add the line's invoice so the rows still differ.
            our_ref = _line_reference(base_ref, invoice)
            narration = ' - '.join(
                x for x in (base_narr, invoice or narration_extra) if x)
        internal_ref = f'{pr.ref}/L{line_no}'
    else:
        our_ref = base_ref
        narration = base_narr
        internal_ref = pr.ref

    # RETRY SAFETY. A failed load releases the claim so it can be tried again;
    # under Individual that retry must reuse these rows, not raise a second set
    # of N payments for the same invoices. `reference` is deterministic per row
    # ("<request ref>/L3"), so it is the key to match on.
    existing = Payment.objects.filter(
        company=company, payment_type=Payment.PaymentType.SENT,
        reference=internal_ref[:200], is_once_off=True,
    ).order_by('created_at').first()
    if existing is not None:
        return existing

    payment = Payment(
        payment_type=Payment.PaymentType.SENT,
        contact=sentinel, company=company, bank_account=bank_gl,
        payment_date=pr.payment_date or timezone.localdate(),
        currency_code_id=(pr.currency or 'BWP'),
        amount=amount,
        payment_method=Payment.PaymentMethod.BANK_TRANSFER,
        reference=internal_ref[:200],
        description=(narration or pr.subject or pr.ref)[:1000],
        is_once_off=True,
        payee_name=payee,
        payee_bank_name=(pr.bank_name or '')[:120],
        payee_account_number=acct[:40],
        payee_branch_code=(pr.branch_code or '')[:20],
        # What the bank shows. Set here so the words are chosen once, on the
        # request, instead of being derived into something nobody picked — the
        # "One-off - <payee>" the CFO objected to on 2026-08-20.
        bank_beneficiary_name=payee[:140],
        # The wording chosen on the request wins. The request ref is only the
        # FLOOR, for when nothing was chosen: FNB rejects an empty mandatory
        # field, and a unique value there beats an identical one on every
        # payment in the batch. Finance, 2026-08-20: "The Omni payment number
        # should come off the bank narration… it tells me nothing when I am
        # matching a bank line back to a claim or an invoice."
        bank_our_reference=our_ref[:35],
        bank_narration=narration[:140],
        created_by=pr.created_by,
    )
    payment.save(audit_user=pr.created_by)
    return payment


# ---------------------------------------------------------------------------
# The one entry point
# ---------------------------------------------------------------------------
def load_request_to_fnb(pr, releaser) -> dict:
    """Load a signed-off payment request into FNB. Never raises.

    Returns {'loaded': bool, 'reason': str, 'batch_id': str|None}. The reason is
    written to pr.fnb_load_error either way, so the screen can say what happened.
    """
    from django.core.exceptions import ValidationError
    from fnb.client import FNBAPIError, FNBAuthError, FNBNotConfigured
    from fnb.payments import submit_eft_batch
    from payments.models import Payment

    def _record(loaded: bool, reason: str, batch=None, hold_claim: bool = False):
        """Write the outcome. `hold_claim` keeps the request marked as attempted.

        A failure normally releases the claim so it can be tried again. But an
        INDETERMINATE bank error — timeout, 5xx, dropped connection — means the
        POST may have been received and accepted, and submit_eft_batch marks
        that batch UNKNOWN with the rule "verify with FNB before any resubmit".
        Releasing the claim there would mark the request retry-eligible while
        the bank might be holding an accepted batch: a double payment the day a
        retry button exists. So that case holds the claim and says why.
        """
        # The reason is stored whether or not the load succeeded. It used to be
        # `'' if loaded else reason`, which is right for a clean load (reason
        # is '') but wrong for a PARTIAL one: that outcome is loaded AND has
        # something Finance must act on, and the blanking swallowed it.
        pr.fnb_load_error = (reason or '')[:2000]
        fields = ['fnb_load_error', 'updated_at']
        if loaded:
            pr.fnb_batch = batch
            pr.fnb_loaded_at = timezone.now()
            fields += ['fnb_batch', 'fnb_loaded_at']
        elif not hold_claim:
            # Release the claim taken below, or a failed attempt would read as
            # loaded and block the manual retry.
            pr.fnb_loaded_at = None
            fields += ['fnb_loaded_at']
        # hold_claim: fnb_loaded_at is deliberately NOT in `fields`. The claim
        # was written with .update(), so this in-memory object still carries the
        # old None — saving it would wipe the very claim we are holding.
        try:
            pr.save(update_fields=fields)
        except Exception:                                      # noqa: BLE001
            log.exception('payment request %s: could not record the FNB load '
                          'outcome', getattr(pr, 'ref', '?'))
        return {'loaded': loaded, 'reason': reason,
                'batch_id': str(batch.id) if batch is not None else None}

    if not auto_load_enabled():
        return _record(False, 'Automatic FNB loading is switched off '
                              '(PAYMENT_REQUEST_AUTO_FNB).')
    if pr.fnb_batch_id:
        # Keep any partial-load message. This early return used to blank it,
        # which is the same hole the partial-message fix was written to close:
        # the message ends "Load the rest by hand or try again", and pressing
        # try-again lands HERE, sends nothing, and erases the only record of
        # which lines never went.
        return _record(True, pr.fnb_load_error or '', pr.fnb_batch)

    # F2: two concurrent sign-offs (a double-click) could both read fnb_batch as
    # empty and both load, putting two identical payments in the CFO's phone
    # queue. Claim the request with a conditional UPDATE first: exactly one
    # caller sees a row count of 1, the loser stops here.
    from .models import PaymentRequest as _PR
    claimed = (_PR.objects
               .filter(pk=pr.pk, fnb_batch__isnull=True, fnb_loaded_at__isnull=True)
               .update(fnb_loaded_at=timezone.now()))
    if not claimed:
        pr.refresh_from_db()
        return {'loaded': bool(pr.fnb_batch_id), 'reason': pr.fnb_load_error,
                'batch_id': str(pr.fnb_batch_id) if pr.fnb_batch_id else None}

    try:
        company = _resolve_company(pr.entity)
        if company is None:
            raise AutoLoadNotPossible(
                f'"{pr.entity}" does not match a company on record, so we cannot '
                'tell which account to pay it from. Add it to '
                'PAYMENT_REQUEST_ENTITY_ALIASES or rename the entity to match.')

        source_account = _resolve_source_account(company, pr.category)
        if source_account is None:
            wanted = source_account_number_for(pr.category)
            if not wanted:
                raise AutoLoadNotPossible(
                    f'No paying FNB account is set for {pr.category or "this"} '
                    'payments, so nothing was loaded. Add it to '
                    'PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS.')
            raise AutoLoadNotPossible(
                f'The account payments of this kind go out of (…{wanted[-4:]}) '
                f'does not belong to {getattr(company, "code", pr.entity)}, so '
                'this would debit another company. It has to be typed into FNB '
                'by hand, or that entity needs its own paying account set.')

        # ALWAYS rebuild the rows, even on a retry. There used to be a
        # short-circuit here — `if pr.payment_id: payments = [pr.payment]` —
        # and it silently dropped every line but the first: pr.payment is
        # stamped to payments[0] a few lines below, BEFORE the submit loop, so
        # a first attempt that fails at the bank leaves the request pointing at
        # line 1. The retry then sent that one line, reported loaded and
        # blanked the error. An eight-invoice INDIVIDUAL request for
        # P16,556.09 went to FNB as a single P2,000.00 payment and read as
        # fully loaded; P14,556.09 was never sent and nobody was told.
        #
        # The short-circuit bought nothing: build_payments_for_request is
        # idempotent — `reference` is deterministic per row ("<ref>/L3"), and
        # _once_off_payment returns the existing row when it finds one, which
        # test_individual_eft_rows pins ("building twice reuses the rows").
        payments = build_payments_for_request(pr, company, source_account)
        # pr.payment stays a single FK — it points at the FIRST row, which is
        # the whole request under Bulk and line 1 under Individual. Every row
        # goes into the batch below, which is what puts N lines in the bank
        # file.
        if payments and pr.payment_id != payments[0].pk:
            pr.payment = payments[0]
            pr.save(update_fields=['payment', 'updated_at'])

        # F6: pr.payment_date is the model's own "when the money leaves". A
        # Friday-dated request must not load for execution today. A past date
        # is dropped — FNB rejects it (DT01) — and defaults to today.
        exec_date = pr.payment_date
        if exec_date and exec_date < timezone.localdate():
            exec_date = None

        # Submit each payment INDIVIDUALLY so each one appears as a separate
        # item on the CFO's FNB approval screen with its own readable name
        # (e.g. "Grand RE-Radical 000044 (O)").  The CFO can then approve or
        # reject each one independently instead of all-or-nothing.
        # Bulk → still one payment row → one batch (unchanged).
        # Individual → N payment rows → N batches, one per line.
        batches = []
        failed = []
        for p in payments:
            try:
                b = submit_eft_batch(
                    Payment.objects.filter(pk=p.pk),
                    source_account=source_account,
                    user=releaser,
                    requested_execution_date=exec_date,
                    # Stamped at creation so the link survives a submit that
                    # never returns. The block below still runs for the
                    # callers that cannot pass it.
                    payment_request=pr,
                )
                batches.append(b)
            except Exception as exc:                              # noqa: BLE001
                failed.append((p, exc))

        # Stamp the originating request on EVERY batch, not just the one the
        # request's own `fnb_batch` FK can hold (CFO 2026-09-17, split-payment
        # lineage). Under Individual processing one request becomes N batches;
        # before this, N-1 of them had no way back to the request, so a reader
        # saw N unrelated bank failures instead of one supplier with one cause.
        #
        # Written with .update() on the batch rows rather than saving each
        # object: the batch has already been accepted by the bank at this
        # point, and a lineage stamp must never be able to raise and undo that.
        if batches:
            # Inside its OWN atomic block, which is the point. A bare
            # try/except around a database write does NOT make it safe: if a
            # caller ever wraps this in transaction.atomic(), a caught
            # DatabaseError leaves that outer transaction aborted, and the very
            # next statement — _record()'s pr.save() — fails too, so swallowing
            # the error would LOSE the load outcome rather than protect it.
            # transaction.atomic() here opens a savepoint that rolls back
            # alone. (Off-subscription panel, DeepSeek, 17-Sep-2026; same class
            # as the audit row written inside the transaction its own guard
            # aborts.)
            from django.db import transaction as _txn
            try:
                from fnb.models import FNBBatchSubmission
                with _txn.atomic():
                    FNBBatchSubmission.objects.filter(
                        pk__in=[b.pk for b in batches]).update(payment_request=pr)
                for b in batches:
                    b.payment_request = pr
            except Exception:                                     # noqa: BLE001
                log.exception('payment request %s: could not stamp batch '
                              'lineage', getattr(pr, 'ref', '?'))

        if not batches and failed:
            # Every single submission failed — re-raise the first error so the
            # existing handlers below (FNBAPIError, ValidationError, etc.) deal
            # with it exactly as before.
            raise failed[0][1]

        batch = batches[0]
        partial_reason = ''
        if failed:
            # Partial success: some went through, some didn't.  Record on the
            # request so Finance can see which lines need manual loading.
            #
            # Handed to _record below rather than saved here. Writing it here
            # and falling through erased it every time: _record's first line
            # blanked fnb_load_error on a loaded outcome and listed the field
            # in update_fields, so after failing 3 of 8 the stored message was
            # ''. Finance was told the load succeeded and never learned which
            # three lines still needed hand-keying.
            names = ', '.join(
                getattr(p, 'bank_our_reference', '') or getattr(p, 'payment_number', '?')
                for p, _ in failed
            )
            # "try again" is only safe when the bank gave a CLEAN refusal.
            # A timeout, a dropped connection or a 5xx means the POST MAY have
            # been accepted — _indeterminate() exists for exactly that — and
            # telling Finance to hand-key those lines is how a supplier gets
            # paid twice. This company has already paid P399,338.10 twice.
            from fnb.payments import _indeterminate
            unsure = any(isinstance(exc, FNBAPIError) and _indeterminate(exc)
                         for _, exc in failed)
            tail = ('Check FNB for these before re-sending or typing them by '
                    'hand — the bank may already have taken them.'
                    if unsure else
                    'Load the rest by hand or try again.')
            partial_reason = (
                f'{len(batches)} of {len(payments)} payments loaded to FNB. '
                f'Failed: {names[:1500]}. {tail}'
            )
    except AutoLoadNotPossible as e:
        return _record(False, str(e))
    except FNBNotConfigured as e:
        return _record(False, f'FNB is not configured on this server: {e}')
    except FNBAuthError as e:
        # Omni could not even sign in to the bank, so the payment was certainly
        # never sent. Release the claim so the load can be tried again once the
        # sign-in is fixed. Before this it fell into the catch-all below, which
        # HOLDS the claim — so one expired bank token locked the request out of
        # auto-loading for ever (Fable 5.1 audit 2026-09-02, M9).
        return _record(False, f'Omni could not sign in to FNB, so nothing was sent: '
                              f'{str(e)[:300]}. Fix the bank sign-in and load it again.')
    except ValidationError as e:
        msgs = getattr(e, 'messages', None) or [str(e)]
        return _record(False, '; '.join(str(m) for m in msgs))
    except FNBAPIError as e:
        # A clean 4xx reject means FNB refused it and nothing moved — safe to
        # try again. Anything indeterminate (timeout, 5xx, dropped connection)
        # means the POST MAY have been accepted, so this must not look
        # retryable until a person has checked with the bank.
        from fnb.payments import _indeterminate
        if _indeterminate(e):
            return _record(
                False,
                f'The load to FNB did not come back cleanly (HTTP '
                f'{e.status_code}), so we cannot tell whether the bank took it. '
                'Check for this payment in FNB BEFORE loading it again or '
                'typing it by hand — it may already be in the queue.',
                hold_claim=True)
        return _record(False, f'FNB refused the load (HTTP {e.status_code}): '
                              f'{str(e)[:300]}')
    except Exception as e:                                     # noqa: BLE001
        # A sign-off must never be lost to an unexpected error here, and the
        # reason must never be invisible. The claim is HELD: an error we did not
        # anticipate tells us nothing about whether the bank saw the payment.
        log.exception('payment request %s: unexpected error loading to FNB',
                      getattr(pr, 'ref', '?'))
        return _record(
            False,
            f'Something went wrong loading this to FNB ({type(e).__name__}: '
            f'{str(e)[:200]}). Check FNB before loading it again or typing it '
            'by hand.',
            hold_claim=True)

    return _record(True, partial_reason, batch)
