"""
taskboard/pop_recipients.py

Who receives the proof of payment for one payment line, and is that somebody we
already know?

Finance spec 2026-09-08: "add a new column: POP Recipient. Populate its dropdown
from contacts already linked to that claim (claimant, supplier, broker — the
same data already surfaced in the claim summary card above the line), with a
free-text fallback for name + email … If the value entered isn't one of the
existing linked contacts, require a second approver to clear it."

**Deliberately not an AI**, and deliberately not a new data path. Where a proof
of payment goes is a lookup over records Omni already holds:

  1. The vendor register (procurement.VendorBankAccount.email) — the POP address
     already remembered against a payee's approved account (CFO 2026-08-22).
  2. billing.Contact — the customer / vendor / broker / reinsurer book, name and
     email, matched to the claim's own customer and to the request payee.
  3. The claim itself (integrations.GraphiteClaim.customer_name) — the claimant
     named on the claim in the mirror the claim summary card already reads.
  4. The Accounts default (payments.models.default_pop_email), which is where a
     POP goes when nobody names anyone else.

⚠️ KNOWN GAP, for the CFO: the Graphite claim mirror carries the claimant's NAME
and the handler's name, but no email address for either, and nothing in the
mirror links a BROKER to a claim. So the claimant appears in the list only where
a billing.Contact of the same name carries an email, and brokers appear as the
broker book rather than "this claim's broker". Naming a recipient the list does
not hold is not blocked — it is flagged, and a second person clears it.

Payee names are typed by hand every time, so they are compared on the same
squashed key taskboard.payee_bank_history already uses — one normaliser, not two.
"""
from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from taskboard.payee_bank_history import normalise_payee

log = logging.getLogger(__name__)

#: A recipient's origin, so the screen can say WHERE the address came from
#: rather than presenting four different lookups as one anonymous list.
SOURCE_VENDOR_ACCOUNT = 'vendor_account'
SOURCE_CONTACT        = 'contact'
SOURCE_CLAIMANT       = 'claimant'
SOURCE_ACCOUNTS       = 'accounts_default'
#: Typed by hand and matching nothing we hold — the case a second person clears.
SOURCE_OFF_LIST       = 'off_list'

#: How many broker/vendor contacts to offer. The book runs to thousands of rows;
#: an unbounded dropdown is not a control, it is a scroll.
MAX_CONTACT_OPTIONS = 25


def is_valid_email(value: str) -> bool:
    try:
        validate_email((value or '').strip())
    except ValidationError:
        return False
    return True


def _option(kind: str, name: str, email: str, label: str) -> dict:
    return {'source': kind, 'name': (name or '').strip()[:200],
            'email': (email or '').strip().lower()[:254], 'source_label': label}


def accounts_default() -> dict:
    """Where a proof of payment goes when nobody names anyone else."""
    from payments.models import default_pop_email
    return _option(SOURCE_ACCOUNTS, 'Accounts', default_pop_email(),
                   'Accounts — the standing default')


def _vendor_account_options(payee: str) -> list[dict]:
    """The POP address already remembered against this payee's bank account."""
    if not payee:
        return []
    key = normalise_payee(payee)
    if not key:
        return []
    out = []
    try:
        from procurement.models import VendorBankAccount
        # No row cap, for the same reason last_known_bank has none: a cap
        # silently drops the oldest payees from the list, and a payee the list
        # never offers reads as off-list.
        rows = (VendorBankAccount.objects
                .select_related('contact')
                .exclude(email='')
                .exclude(email__isnull=True)
                .iterator())
        for vba in rows:
            holder = (vba.account_holder_name
                      or getattr(vba.contact, 'name', '') or '')
            if normalise_payee(holder) == key or normalise_payee(
                    getattr(vba.contact, 'name', '')) == key:
                out.append(_option(SOURCE_VENDOR_ACCOUNT, holder, vba.email,
                                   'the POP address on this payee’s bank account'))
    except Exception:                                            # noqa: BLE001
        # The vendor register is a nice-to-have here, never the gate. A missing
        # or broken register must not stop a payment being raised.
        log.warning('POP recipient: vendor register lookup failed for %r',
                    payee, exc_info=True)
    return out


def _contact_options(names: list, kinds: tuple) -> list[dict]:
    """billing.Contact rows matching any of these names, or of these types."""
    out = []
    try:
        from billing.models import Contact
        keys = {normalise_payee(n) for n in names if n}
        keys.discard('')
        qs = (Contact.objects.filter(is_active=True)
              .exclude(email='').exclude(email__isnull=True))
        if keys:
            # Streamed, not sliced. A row cap here would silently stop offering
            # the oldest contacts — and a name the list never offers reads as
            # off-list. That fails SAFE (a second approver is asked to clear
            # it) rather than dangerous, but it would also train people to tick
            # past the warning, which is the real cost. Only name and email are
            # loaded, so a full scan on a debounced lookup stays cheap.
            for c in qs.only('name', 'email', 'contact_type').iterator():
                if normalise_payee(c.name) in keys:
                    out.append(_option(SOURCE_CONTACT, c.name, c.email,
                                       f'{c.get_contact_type_display()} on file'))
        if kinds:
            for c in qs.filter(contact_type__in=kinds)[:MAX_CONTACT_OPTIONS]:
                out.append(_option(SOURCE_CONTACT, c.name, c.email,
                                   f'{c.get_contact_type_display()} on file'))
    except Exception:                                            # noqa: BLE001
        log.warning('POP recipient: contact lookup failed', exc_info=True)
    return out


def _claim_names(claim_number: str) -> list:
    """The names the claim summary card already shows: the claimant, and the
    handler. Names only — the mirror holds no email for either."""
    if not (claim_number or '').strip():
        return []
    try:
        from integrations.models import GraphiteClaim
        claim = (GraphiteClaim.objects
                 .filter(claim_number__iexact=claim_number.strip())
                 .order_by('-detail_synced_at', '-id').first())
    except Exception:                                            # noqa: BLE001
        log.warning('POP recipient: claim lookup failed for %r', claim_number,
                    exc_info=True)
        return []
    if claim is None:
        return []
    return [n for n in (claim.customer_name, claim.claim_handler) if n]


def linked_recipients(*, claim_number: str = '', payee: str = '',
                      include_brokers: bool = True) -> list[dict]:
    """The dropdown for one payment line, most specific option first.

    De-duplicated on the email address, because the same address reached three
    ways is one recipient — a list offering it three times reads as three
    different choices.
    """
    options = []
    options += _vendor_account_options(payee)
    claimant_names = _claim_names(claim_number)
    # The claimant / handler named on the claim, where the contact book holds an
    # address for them; plus the payee, and the broker book.
    for opt in _contact_options(claimant_names + ([payee] if payee else []),
                               ('broker',) if include_brokers else ()):
        if opt['name'] and normalise_payee(opt['name']) in {
                normalise_payee(n) for n in claimant_names}:
            opt = {**opt, 'source': SOURCE_CLAIMANT,
                   'source_label': 'named on this claim'}
        options.append(opt)
    options.append(accounts_default())

    seen, out = set(), []
    for opt in options:
        if not opt['email'] or not is_valid_email(opt['email']):
            continue
        if opt['email'] in seen:
            continue
        seen.add(opt['email'])
        out.append(opt)
    return out


def classify(name: str, email: str, options: list) -> str:
    """Which source an entered recipient came from — SOURCE_OFF_LIST if none.

    Matched on the EMAIL, because that is what actually receives the proof; a
    name is a label on it. Case and surrounding space are ignored, since the
    address is typed.
    """
    addr = (email or '').strip().lower()
    if not addr:
        return SOURCE_OFF_LIST
    for opt in options or []:
        if opt.get('email', '').strip().lower() == addr:
            return opt.get('source') or SOURCE_CONTACT
    return SOURCE_OFF_LIST


def off_list_lines(line_items, *, payee: str = '') -> list[dict]:
    """Which lines name a recipient we do not hold — the second-approver case.

    Returns [{line, name, email}] with the 1-based line number, so the message
    can say WHICH line rather than "somewhere on this request". A cancelled line
    is not a payment, so it raises nothing.

    Always re-derived, never read off the stored source: this runs on the
    sign-off path, and a hand-crafted request that could label its own recipient
    "linked" would walk straight past the gate.
    """
    out = []
    # The option list depends only on (claim number, payee), and an eight-line
    # request usually shares one of each — so it is resolved once per distinct
    # claim rather than once per line. This is called on every sign-off attempt
    # and on every read of the request detail.
    cache: dict = {}
    for i, ln in enumerate(line_items or [], start=1):
        if not isinstance(ln, dict) or ln.get('cancelled'):
            continue
        email = (ln.get('pop_recipient_email') or '').strip()
        if not email:
            continue
        claim = (ln.get('claim_number') or '').strip()
        if claim not in cache:
            cache[claim] = linked_recipients(claim_number=claim, payee=payee)
        if classify(ln.get('pop_recipient_name') or '', email,
                    cache[claim]) == SOURCE_OFF_LIST:
            out.append({'line': i, 'name': (ln.get('pop_recipient_name') or '').strip(),
                        'email': email})
    return out
