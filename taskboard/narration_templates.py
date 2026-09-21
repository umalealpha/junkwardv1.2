"""
taskboard/narration_templates.py

What a payment says on the bank statement, by payment type.

Written to the spec Finance gave on 2026-08-20, in reply to the CFO's question.
Their words are quoted against each rule so a future reader can tell what was
decided from what was inferred.

  "1. What the payee sees (their reference, 140 characters)
      - Operational Supplier invoices: ALPHA DIRECT + invoice number
      - Repairs: ALPHA DIRECT + claim number + Invoice Number
      - AOL, CIL, third party: ALPHA DIRECT + claim number + payment type
      - Client Refunds: ALPHA DIRECT REFUND + policy number
      - Internal Refunds: ALPHA DIRECT REFUND + Employee Initials

   2. Our own reference (35 characters)
      - Claims: claim number + payment type. Example: G2026004512 AOL
      - Client Refunds: REFUND + policy number + initials.
        Example: REFUND COMG2026004512 LN
      - Internal Refunds: REFUND + REFUND TYPE + INITIALS.
        Example: REFUND Starlink PB
      - Supplier invoices: supplier name + invoice number.
        Example: MOTOVAC INV45678"

Three constraints they were explicit about, and each is a real rule here:

  * "The insured's name does not fit… The claim number already resolves to the
    insured in Omni, so the name adds nothing when matching. Leave it out."
    -> no payee name in either reference for a claim.

  * "Supplier names must truncate at 20 characters so the invoice number always
    survives. Truncate the name, never the number."
    -> _supplier_ref() trims the NAME to fit; the invoice number is never cut.

  * "The Omni payment number should come off the bank narration… It tells me
    nothing when I am matching a bank line back to a claim or an invoice."
    -> nothing here ever emits a PAY-OUT number. Note that fnb.payments still
    falls back to the payment number when a field would otherwise be EMPTY,
    because FNB rejects an empty mandatory field — that is a last-resort floor,
    not a default.

And the gap they flagged, which is why nothing here is forced:

  "reinsurance, payroll and statutory payments (BURS, NBFIRA) are not covered
   above as they are not common enough which is why narration edits should be
   allowed and manually entered"
  -> type OTHER returns blank, so the person types it. A blank template is a
     deliberate answer, not a failure.
"""
from __future__ import annotations

import re

# Everything the bank sees is folded to FNB's permitted set at send time by
# fnb.payments.fnb_text, so templates can be written in plain readable text.
HOUSE = 'ALPHA DIRECT'
OUR_REF_MAX = 35            # ISO 20022 endToEndId
NARRATION_MAX = 140         # remittanceInformationUnstructured
SUPPLIER_NAME_MAX = 20      # Finance, 2026-08-20


class PaymentNarrationType:
    """The types Finance asked to drive the defaults: 'ex-gratia, AOL, CIL,
    repair, third party, refund, supplier invoice'. Split refund into client and
    internal because they gave those two different formats, and add OTHER for
    the reinsurance / payroll / statutory gap they named."""

    SUPPLIER_INVOICE = 'supplier_invoice'
    REPAIR           = 'repair'
    AOL              = 'aol'
    CIL              = 'cil'
    THIRD_PARTY      = 'third_party'
    EX_GRATIA        = 'ex_gratia'
    CLIENT_REFUND    = 'client_refund'
    INTERNAL_REFUND  = 'internal_refund'
    OTHER            = 'other'

    CHOICES = [
        (SUPPLIER_INVOICE, 'Supplier invoice'),
        (REPAIR,           'Repair'),
        (AOL,              'AOL'),
        (CIL,              'CIL'),
        (THIRD_PARTY,      'Third party'),
        (EX_GRATIA,        'Ex-gratia'),
        (CLIENT_REFUND,    'Client refund'),
        (INTERNAL_REFUND,  'Internal refund'),
        (OTHER,            'Other — type the narration yourself'),
    ]

    # What the payee sees for a claim, and what we match on. Finance wrote
    # "claim number + payment type", so the label is the wording on the
    # statement, not the internal key.
    CLAIM_LABEL = {
        REPAIR:      'REPAIR',
        AOL:         'AOL',
        CIL:         'CIL',
        THIRD_PARTY: 'THIRD PARTY',
        EX_GRATIA:   'EX-GRATIA',
    }

    CLAIM_TYPES = frozenset(CLAIM_LABEL)


def _squash(text: str) -> str:
    """One space between words, nothing at the ends."""
    return re.sub(r'\s+', ' ', (text or '')).strip()


def initials_of(name: str) -> str:
    """'Lorato Ntsima' -> 'LN'. Used where Finance asked for initials."""
    words = [w for w in re.split(r'[^A-Za-z]+', name or '') if w]
    return ''.join(w[0] for w in words[:3]).upper()


def _first(values) -> str:
    for v in values:
        v = _squash(str(v or ''))
        if v:
            return v
    return ''


def collect_line_refs(line_items) -> dict:
    """Claim and invoice numbers off the request's lines.

    Both spellings appear in live data ('claim_number' and 'claim_no'), so both
    are read. Duplicates are dropped but order is kept, because the first line
    is the one people quote.
    """
    claims, invoices = [], []
    for ln in (line_items or []):
        if not isinstance(ln, dict):
            continue
        c = _squash(ln.get('claim_number') or ln.get('claim_no') or '')
        i = _squash(ln.get('invoice_number') or ln.get('invoice_no') or '')
        if c and c not in claims:
            claims.append(c)
        if i and i not in invoices:
            invoices.append(i)
    return {'claims': claims, 'invoices': invoices}


def _supplier_ref(supplier: str, invoice: str) -> str:
    """'supplier name + invoice number', trimming the NAME to fit.

    Finance: "Supplier names must truncate at 20 characters so the invoice
    number always survives. Truncate the name, never the number." So the
    invoice number is placed first in the budget and the name takes what is
    left, never the other way round.
    """
    invoice = _squash(invoice)
    name = _squash(supplier).upper()[:SUPPLIER_NAME_MAX]
    if not invoice:
        return name[:OUR_REF_MAX]
    room = OUR_REF_MAX - len(invoice) - 1          # 1 for the space
    if room <= 0:
        # A pathological invoice number on its own still beats losing it.
        return invoice[:OUR_REF_MAX]
    return f'{name[:room]} {invoice}'.strip()


def build_defaults(*, payment_type: str, line_items=None, payee: str = '',
                   account_name: str = '', policy_number: str = '',
                   refund_type: str = '') -> dict:
    """The default narration and our-reference for one payment request.

    Returns {'narration', 'our_reference', 'missing'}. `missing` names the
    pieces the template wanted and did not get, so the screen can ask for them
    instead of quietly emitting a half-built reference.
    """
    t = (payment_type or '').strip().lower()
    refs = collect_line_refs(line_items)
    claim = _first(refs['claims'])
    invoice = _first(refs['invoices'])
    supplier = _first([account_name, payee])
    missing: list[str] = []

    narration = our_ref = ''

    if t == PaymentNarrationType.SUPPLIER_INVOICE:
        if not invoice:
            missing.append('invoice number')
        narration = _squash(f'{HOUSE} {" ".join(refs["invoices"])}')
        our_ref = _supplier_ref(supplier, invoice)
        if not supplier:
            missing.append('supplier name')

    elif t == PaymentNarrationType.REPAIR:
        if not claim:
            missing.append('claim number')
        if not invoice:
            missing.append('invoice number')
        narration = _squash(f'{HOUSE} {claim} {invoice}')
        our_ref = _squash(f'{claim} {PaymentNarrationType.CLAIM_LABEL[t]}')

    elif t in PaymentNarrationType.CLAIM_TYPES:
        if not claim:
            missing.append('claim number')
        label = PaymentNarrationType.CLAIM_LABEL[t]
        narration = _squash(f'{HOUSE} {claim} {label}')
        # Deliberately no insured name: it does not fit in 35 and the claim
        # number already resolves to them in Omni (Finance, 2026-08-20).
        our_ref = _squash(f'{claim} {label}')

    elif t == PaymentNarrationType.CLIENT_REFUND:
        policy = _squash(policy_number)
        if not policy:
            missing.append('policy number')
        narration = _squash(f'{HOUSE} REFUND {policy}')
        our_ref = _squash(f'REFUND {policy} {initials_of(supplier)}')

    elif t == PaymentNarrationType.INTERNAL_REFUND:
        who = initials_of(supplier)
        if not who:
            missing.append('employee name')
        narration = _squash(f'{HOUSE} REFUND {who}')
        kind = _squash(refund_type)
        if not kind:
            missing.append('refund type')
        our_ref = _squash(f'REFUND {kind} {who}')

    else:
        # OTHER, or an unknown type. Finance asked for manual entry here.
        return {'narration': '', 'our_reference': '', 'missing': []}

    return {
        'narration': narration[:NARRATION_MAX],
        # Hard cap. Anything over 35 is refused by FNB, and the truncation rule
        # above already protected the part Finance said must survive.
        'our_reference': our_ref[:OUR_REF_MAX],
        'missing': missing,
    }


def default_type_for(category: str, claim_payee_type: str = '') -> str:
    """A starting guess from what the request already carries.

    Only a starting point — the person picks the real type, because a claim
    could be any of five things and the category cannot tell us which.
    """
    cat = (category or '').strip().lower()
    # B8: all three refund kinds default to the client-refund wording, not just
    # the importer's premium refund. A refund that read as an ordinary supplier
    # payment on the bank statement is the misclassification this item exists to
    # end. Still only a starting point — the person picks the real type.
    if cat in ('premium_refund', 'erroneous_refund', 'excess_refund'):
        return PaymentNarrationType.CLIENT_REFUND
    if cat in ('supplier', 'vendor'):
        return PaymentNarrationType.SUPPLIER_INVOICE
    if cat == 'claim':
        # A provider claim is most often a repair; a client claim is not.
        return (PaymentNarrationType.REPAIR
                if (claim_payee_type or '').lower() == 'provider'
                else PaymentNarrationType.OTHER)
    return PaymentNarrationType.OTHER
