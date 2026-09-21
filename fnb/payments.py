"""
fnb/payments.py

Submit outbound EFT batches to FNB's Payment Execution API.

Replaces the manual BOL upload flow. Each maker-checker-approved
payments.Payment row is bundled into a CustomerCreditTransferInitiation
message (ISO 20022 / pain.001-style), POSTed to FNB with an
idempotency key, and the resulting FNB instructionId is stored on the
FNBBatchSubmission row.

Auth + transport + audit logging live in `client.py`.
Endpoint paths live in `endpoints.py`.
This file owns ONLY the payload shape + the submission service.
"""
from __future__ import annotations

import logging
import re
import string
import unicodedata
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import List

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .client import FNBClient, FNBAPIError, FNBAuthError, FNBNotConfigured
from .destination_bank import branch_code_problem, usable_branch_code
from .endpoints import PAYMENT_INITIATE, PAYMENT_STATUS
from .models import FNBBatchSubmission, FNBSyncLog

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FNB permitted character set  (RR10 guard)
# ---------------------------------------------------------------------------
# Every free-text field is validated by FNB against the set in
# API-EFT-Payments-Message-Specification-RMB-SA_V-03 §2.5 "Permitted Character
# Set". ONE character outside it rejects the WHOLE batch. Proven in production
# on 2026-08-19 — the penny test came back
#   groupStatus RJCT / transactionStatus VALIDATION_FAILED
#   statusReasonInformation RR10 "INVALID CHARACTER SET"
# purely because remittanceInformationUnstructured read `One-off - PAYEE NAME`
# with an EM DASH (U+2014). The credentials and the endpoint were fine — Omni
# writes that text itself, so the reject was ours.
#
# §2.5 lists A-Z a-z 0-9 and:  . - * , ( ) % + $ ; = @ ? :  space  ! " # & ' /
# < > [ \ ] ^ _   — i.e. printable ASCII EXCEPT ` { | } ~. Note it is WIDER
# than the SWIFT-x set: & @ # % * $ [ ] _ are all legal, so they must be left
# alone rather than "cleaned" into something else.
FNB_PERMITTED_PUNCTUATION = ' !"#$%&\'()*+,-./:;<=>?@[\\]^_'
FNB_ALLOWED_CHARS = frozenset(
    string.ascii_letters + string.digits + FNB_PERMITTED_PUNCTUATION
)

# Non-ASCII look-alikes that finance text picks up from Word / Excel / email,
# mapped to their permitted ASCII twin. Anything else outside the set is
# dropped after an accent-stripping NFKD pass.
_FNB_TRANSLIT = {
    '‐': '-', '‑': '-', '‒': '-', '–': '-',
    '—': '-', '―': '-', '−': '-',
    '‘': "'", '’': "'", '‚': "'", '‛': "'",
    '′': "'",
    '“': '"', '”': '"', '„': '"', '″': '"',
    '«': '"', '»': '"',
    '…': '...',
}
_FNB_TRANSLIT_TABLE = str.maketrans(_FNB_TRANSLIT)
_WHITESPACE_TABLE = str.maketrans(
    {c: ' ' for c in string.whitespace + '    '}
)


def fnb_text(value, limit: int | None = None) -> str:
    """Fold `value` into FNB's permitted character set (spec §2.5).

    Order matters: map look-alikes to their permitted twin FIRST, then strip
    accents, then drop whatever is still not permitted. Dropping first would
    turn `One-off - PAYEE NAME` into `One-off  PAYEE NAME` and `Andre` into
    `Andr`.

    `limit` truncates AFTER folding — truncating first can cut mid-substitution
    or leave a trailing space.
    """
    if value is None:
        return ''
    s = str(value).translate(_WHITESPACE_TABLE).translate(_FNB_TRANSLIT_TABLE)
    # NFKD splits an accented letter into base + combining mark; dropping only
    # the mark keeps `Andre Rene` instead of losing the whole letter.
    s = ''.join(
        ch for ch in unicodedata.normalize('NFKD', s)
        if not unicodedata.combining(ch)
    )
    s = ''.join(ch for ch in s if ch in FNB_ALLOWED_CHARS)
    s = re.sub(r' {2,}', ' ', s).strip()
    if limit is not None:
        s = s[:limit].strip()
    return s


def assert_fnb_charset(payload) -> None:
    """Refuse to POST a message FNB would reject with RR10.

    Checks every string in the built payload, so a field added later without a
    `fnb_text()` call fails loudly here instead of costing a rejected batch and
    another round-trip with the bank.
    """
    bad: list[str] = []

    def walk(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item, key)
        elif isinstance(node, str):
            offenders = sorted({ch for ch in node if ch not in FNB_ALLOWED_CHARS})
            if offenders:
                # Name the field and the code points, NEVER the value: this
                # message surfaces in API responses and logs, and the value is
                # a payee name or a remittance narrative (AD-POL-AI-GOV-001).
                # The code point is what the operator needs anyway.
                shown = ' '.join(f'U+{ord(ch):04X}' for ch in offenders)
                bad.append(f'{key} (position {node.index(offenders[0])}) '
                           f'contains {shown}')

    walk(payload)
    if bad:
        raise ValidationError(
            'Not sent to FNB: the message contains characters FNB rejects '
            '(RR10 INVALID CHARACTER SET). Permitted are letters, digits and '
            f'{FNB_PERMITTED_PUNCTUATION!r}. Offending fields: '
            + '; '.join(bad)
        )


# ---------------------------------------------------------------------------
# Omni origin marker — stamped on every endToEndId so Finance can tell an
# Omni-loaded payment from one keyed by hand in FNB Online Banking.
# ---------------------------------------------------------------------------
# ADIC payments keep the original `(O)`. Entity payments carry their company
# code: `(UNI)` for Unicoin, `(VCM)` for Veritas, etc. (CFO 2026-09-10).
OMNI_MARKER = '(O)'

_COMPANY_MARKER = {
    'UNI': '(UNI)', 'QIH': '(QIH)', 'RSA': '(RSA)',
    'VCM': '(VCM)', 'GCX': '(GCX)',
}


def omni_marker_for(payment) -> str:
    """Company-specific origin marker: ``(UNI)`` for Unicoin, ``(O)`` for ADIC."""
    code = getattr(getattr(payment, 'company', None), 'code', '') or ''
    return _COMPANY_MARKER.get(code, OMNI_MARKER)


# ---------------------------------------------------------------------------
# Idempotency-key generator
# ---------------------------------------------------------------------------
# CFO amendment 2026-09-08: a broker-commission batch read `BROKER COMMISSION AS
# 000124 (O)` on FNB's list and he could not tell WHICH broker was being paid.
# `AS` / `AU` / `DYN` were never broker codes: the free-text creditor name starts
# with the words "BROKER COMMISSION", and `fnb_text(who, 20)` below left only its
# first two or three letters — "BROKER COMMISSION AS" is exactly 20 characters,
# and so is the misspelt "BROKER COMMISION DYN", which is the only reason that
# one kept a third letter. He wants the broker spelled out, as it was last month.
#
# So a broker commission gets a SHORT label and takes its name from the broker's
# contact record instead of from the free text: `BKR COMM Spectrum 000124 (O)`.
# The long label is what ate the budget; dropping it is what buys the name room.
BROKER_COMMISSION_LABEL = 'BKR COMM'


#: The words Finance types in front of the broker's name, either spelling. The
#: single-S misspelling is real and frequent, and it is the only reason
#: 'BROKER COMMISION DYN' kept a third letter where 'BROKER COMMISSION AS' did
#: not — the label is one character shorter.
_BROKER_LABELS = ('BROKER COMMISSIONS', 'BROKER COMMISSION',
                  'BROKER COMMISIONS', 'BROKER COMMISION')

#: A month stamp inside the narration — 'AUG26', 'AUG 2026', 'JULY 2025',
#: 'AUG-26'. The CFO's format carries no month, so these come out.
_MONTH_TOKEN = re.compile(
    r'\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)'
    r'(UARY|RUARY|CH|IL|E|Y|UST|TEMBER|OBER|EMBER)?'
    r'[\s\-]*\d{0,4}\b',
    re.IGNORECASE,
)


def _broker_commission_name(payment) -> str:
    """The broker's own name for a broker-commission payment, else ''.

    WHERE THE NAME ACTUALLY LIVES (checked against production, 2026-09-08).
    It is in the free-text bank narration, in full, next to the month:
    'BROKER COMMISSION SPECTRUM AUG26', 'BROKER COMMISSION ASSURE WEALTH AUG26',
    'BROKER COMMISION DYNAMIC AUG26', 'BROKER COMMISSION AUG26 FINSEF' — note
    that last one puts the month FIRST. Nothing else marks these payments:
    every one of them is booked against the shared 'Ad-hoc / One-off Payee'
    contact, and `contact_type` on production only ever holds 'vendor' (9,409)
    or 'customer' (4,138) — there is no 'broker' value, so keying off the
    contact record would have left this fix silently dead on the live system.

    So the label in the narration IS the discriminator, and the words left over
    once the label and the month are removed are the broker's name.

    The source's own capitalisation is kept: several of these are acronyms
    (BOC, CIB, FINSEF) and title-casing them into Boc / Cib / Finsef would read
    as a different payee on the bank list.
    """
    text = (getattr(payment, 'bank_narration', '') or '').strip()
    upper = text.upper()
    for label in _BROKER_LABELS:
        if upper.startswith(label):
            rest = text[len(label):]
            break
    else:
        return ''
    rest = _MONTH_TOKEN.sub(' ', rest)
    rest = ' '.join(rest.split())
    return fnb_text(rest)


def _broker_commission_reference(name: str, seq: str, marker: str = OMNI_MARKER) -> str:
    """`BKR COMM <broker> <seq>`, inside the budget _compose_key will leave.

    Only the NAME may lose characters. The sequence and the marker that
    _compose_key appends are what the FNB email auto-reconcile matches on — it
    looks for the digits immediately before the marker
    (fnb_list_reconcile._ONUM) — so both are reserved out of the budget first
    and the name takes whatever is left (CFO amendment 2026-09-08).
    """
    room = 35 - len(marker) - 1               # the budget _compose_key allows
    if seq:
        room -= len(seq) + 1                  # the sequence and its space
    name = fnb_text(name, max(room - len(BROKER_COMMISSION_LABEL) - 1, 0))
    # A cut mid-name can end on punctuation or a space, which reads as a typo
    # on the bank list rather than as a shortened name.
    name = name.rstrip(' -,.&/')
    who = f'{BROKER_COMMISSION_LABEL} {name}' if name else BROKER_COMMISSION_LABEL
    return f'{who} {seq}'.strip()


# ---------------------------------------------------------------------------
# Petty cash reimbursement — the batch says what it IS, not just who was paid
# ---------------------------------------------------------------------------
# CFO 2026-09-09, on batch `Lefika Basotli 000153 (O)`: "this is petty cash
# reimbursement, so it should be Petty Cash - Lefika (O), not Lefika". A single
# payment otherwise reads as the person's name alone, and on FNB's batch list
# the CFO cannot tell a petty-cash float from any other payment to a member of
# staff.
PETTY_CASH_LABEL = 'Petty Cash'

# WHERE THE MARKER ACTUALLY LIVES (checked against production, 2026-09-09). The
# batch above was raised under category `other` with bank_payment_type `other`,
# so neither field marks it. The words are in the free-text narration —
# 'September Pettycash Reimbursement' — and the older ones spell it
# 'PETTY CASH REIMBURSMENT' or just 'Petty Cash'. So the words are the
# discriminator, exactly as they are for broker commission above, and keying
# off the category would have left this fix silently dead on the live system.
_PETTY_CASH = re.compile(r'PETTY[\s-]*CASH', re.I)


def _is_petty_cash(payment) -> bool:
    """True when this payment's own wording says it is a petty-cash float."""
    text = ' '.join(str(getattr(payment, f, '') or '') for f in
                    ('bank_narration', 'description', 'reference'))
    return bool(_PETTY_CASH.search(text))


def _petty_cash_reference(name: str, seq: str, marker: str = OMNI_MARKER) -> str:
    """`Petty Cash - <who> <seq>`, inside the budget _compose_key will leave.

    Only the NAME may lose characters. The sequence and the marker are
    what the FNB email auto-reconcile matches on — it looks for the digits
    immediately before the marker (fnb_list_reconcile._ONUM) — so both are
    reserved out of the budget first, the same rule as the broker reference.
    A full name that will not fit falls back to the FIRST name rather than being
    cut mid-word: 'Petty Cash - Lefika Basotl' reads as a typo on the bank list.
    """
    room = 35 - len(marker) - 1               # the budget _compose_key allows
    if seq:
        room -= len(seq) + 1                  # the sequence and its space
    head = f'{PETTY_CASH_LABEL} - '
    room -= len(head)
    name = fnb_text(name or '')
    if room <= 0:
        who = PETTY_CASH_LABEL
    else:
        first = name.split(' ')[0] if name else ''
        for candidate in (name, first):
            if candidate and len(candidate) <= room:
                return f'{head}{candidate} {seq}'.strip()
        cut = fnb_text(name, room).rstrip(' -,.&/')
        who = f'{head}{cut}' if cut else PETTY_CASH_LABEL
    return f'{who} {seq}'.strip()


def _batch_reference(payments=None, marker: str = OMNI_MARKER) -> str:
    """The batch reference the CFO reads on FNB's list: the payee name for a
    single payment (or a count for many), then the short sequence tail of the
    Omni payment reference — the part after the last dash, e.g. `000044` from
    `PAY-OUT-2026-000044` (CFO 2026-08-24).

    e.g. `Grand RE-Radical 000044`  or  `EFT 42 payments 000044`

    Two single-payment exceptions, both so the CFO can see WHAT he is paying
    rather than only who: a broker commission reads
    `BKR COMM Spectrum 000124` (CFO amendment 2026-09-08) and a petty-cash
    reimbursement reads `Petty Cash - Lefika 000153` (CFO amendment
    2026-09-09) — see _broker_commission_reference and _petty_cash_reference.
    """
    pmts = list(payments or [])
    if not pmts:
        return 'EFT batch'
    # Readable Omni reference from the payment number tail: PAY-OUT-2026-000153 → PAY-153
    seq = ''
    for p in pmts:
        num = (getattr(p, 'payment_number', '') or '').strip()
        if num:
            tail = num.rsplit('-', 1)[-1].lstrip('0') or '0'
            seq = tail
            break
    if len(pmts) == 1:
        # A single broker commission says which broker, spelled out.
        broker = _broker_commission_name(pmts[0])
        if broker:
            return _broker_commission_reference(broker, seq, marker)
        # A petty-cash float says so, then who it went to (CFO 2026-09-09).
        if _is_petty_cash(pmts[0]):
            return _petty_cash_reference(creditor_name_for(pmts[0]), seq, marker)
        who = creditor_name_for(pmts[0])
    else:
        who = f'EFT {len(pmts)} payments'
    who = fnb_text(who, 20) or 'EFT batch'
    return f'{who} {seq}'.strip()


def _compose_key(reference: str, marker: str = OMNI_MARKER) -> str:
    """`{reference} (CODE)`, always within FNB's 35-char messageId limit."""
    room = 35 - len(marker) - 1               # 1 char for the separating space
    return f'{fnb_text(reference, room)} {marker}'


def _next_idempotency_key(payments=None, marker: str = OMNI_MARKER) -> str:
    """A simple, unique, FNB-safe batch messageId (≤35 chars) ending in the
    Omni marker — the CFO sees a meaningful Omni reference on FNB's approval
    screen (CFO 2026-08-24: "no random hex — the omni reference is fine"), and
    the marker shows it came through Omni and which entity loaded it.

    Was `ALPHA-EFT-YYYYMMDD-<uuid>`: unique, but the CFO only ever saw the
    opaque uuid tail on FNB's batch list.

    Uniqueness (Fable H2 — the 16-Jul-2026 incident reused -000001 after a
    rolled-back batch and FNB rejected the duplicate messageIds): the payment
    reference is unique per payment, and a numeric suffix disambiguates the rare
    resubmit of the same payment set. The DB `unique=True` constraint is the
    final backstop.
    """
    from fnb.models import FNBBatchSubmission
    reference = _batch_reference(payments, marker)
    key = _compose_key(reference, marker)
    n = 1
    while FNBBatchSubmission.objects.filter(idempotency_key=key).exists():
        n += 1
        key = _compose_key(f'{reference}-{n}', marker)
    return key


def _fnb_iso_dt(dt) -> str:
    """FNB expects ISO 8601 with milliseconds and a 'Z'. Match the sample:
    `2026-05-18T07:30:00.000Z`."""
    return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def _q2(value: Decimal | float | int) -> float:
    """Force 2-decimal float — FNB rejects > 2dp. Decimal would be safer
    but the spec models `value` as `number/float`, so emit float."""
    return float(Decimal(str(value)).quantize(Decimal('0.01')))


# ---------------------------------------------------------------------------
# ISO 20022 CustomerCreditTransferInitiation builder
# ---------------------------------------------------------------------------
def build_batch_payload(
    payments,
    *,
    source_account,
    idempotency_key: str,
    service_level_code: str = 'SDVA',
    requested_execution_date: date | None = None,
) -> dict:
    """Map a list of `payments.Payment` rows into FNB's pain.001-style JSON.

    The shape is dictated by EFT-Payments-OpenAPI.yaml
    `CustomerCreditTransferInitiation`. Key fields:

      groupHeader.messageId             = idempotency_key
      groupHeader.initiatingPartyName   = settings.FNB_INITIATING_PARTY_NAME
      groupHeader.initiatingPartyBIC    = settings.FNB_DEBTOR_BIC
      groupHeader.totalControlSum       = sum of all transaction values
      paymentInformation[0].debtor*     = our company's FNB account
      creditTransferTransactionInformation[*]   one entry per Payment
        endToEndId                      = payee name + payment-number tail (payee-led, 2026-08-21)
        amount.value/currency           = Payment.amount + Payment.currency_code
        creditor + creditorAccount      = vendor bank account
        remittanceInformationUnstructured = payment description / invoice ref

    Service Level Codes (per FNB spec):
      SDVA  Same Day Value (intra-FNB)
      NURG  Non-urgent (next-day EFT)
      RGTS  Real-time gross settlement (high-value, between banks)
    Default SDVA for low-friction batches; the caller can override.

    Currency: every payment in a batch must share the same currency
    (FNB rejects mixed-currency batches). Caller is responsible.
    """
    if not payments:
        raise ValidationError('Cannot build an empty payment batch.')

    initiating_name = fnb_text(
        getattr(settings, 'FNB_INITIATING_PARTY_NAME', 'Alpha Direct Insurance'),
        140,
    )
    initiating_bic  = getattr(settings, 'FNB_DEBTOR_BIC', 'FIRNBWGX')
    debtor_acct_type = getattr(settings, 'FNB_DEBTOR_ACCOUNT_TYPE', 'CACC')

    # debtorAgent.branchId is MANDATORY per RMB EFT spec v03 §1.6
    # (1..1 R, max length 6, must not be only zeroes/spaces). Resolution
    # order: (1) env override, (2) source BankAccount.branch_code,
    # (3) the universal FNB-to-FNB branch 287867 (Kabelo Sekoto, RMB,
    # 2026-05-26). Final value is validated below.
    FNB_UNIVERSAL_BRANCH = getattr(
        settings, 'FNB_UNIVERSAL_BRANCH_ID', '287867',
    )
    debtor_branch = (
        (getattr(settings, 'FNB_DEBTOR_BRANCH_ID', '') or '').strip()
        or (getattr(source_account, 'branch_code', '') or '').strip()
        or FNB_UNIVERSAL_BRANCH
    )
    if not debtor_branch or debtor_branch.strip('0 ') == '':
        raise ValidationError(
            'debtorAgent.branchId is mandatory per RMB EFT spec but '
            'resolved to empty / zeroes only. Set FNB_DEBTOR_BRANCH_ID '
            'in /etc/alpha-finance/.env or populate BankAccount.branch_code.'
        )
    if len(debtor_branch) > 6:
        raise ValidationError(
            f'debtorAgent.branchId max length is 6; got {len(debtor_branch)}: '
            f'{debtor_branch!r}.'
        )

    currencies = {p.currency_code_id for p in payments}
    if len(currencies) != 1:
        raise ValidationError(
            f'Batch must be single-currency, got: {sorted(currencies)}'
        )
    batch_currency = next(iter(currencies))

    transactions: List[dict] = []
    total = Decimal('0')
    for p in payments:
        vba = p.vendor_bank_account
        if vba is None and not getattr(p, 'is_once_off', False):
            raise ValidationError(
                f'Payment {p.payment_number} has no vendor bank account — '
                'cannot submit to FNB.'
            )
        if vba is None and not (p.payee_account_number or '').strip():
            raise ValidationError(
                f'Once-off payment {p.payment_number} has no payee account '
                'number — cannot submit to FNB.'
            )
        # The cash that actually leaves the bank, NOT the gross. Withholding
        # tax on a non-exempt broker and any early-settlement discount are
        # credited away from the bank in the GL and never leave us, so
        # instructing `p.amount` overpaid the payee by the tax and left a BURS
        # liability standing against cash that had gone (2026-09-20).
        # Payment.bank_instruction_amounts derives it with the ledger's own
        # arithmetic, so the file and the journal cannot disagree.
        #
        # Read defensively, the same way creditor_name_for reads `contact`:
        # Express Pay and Quick Transfer hand in a duck-typed `_Stand` with no
        # DB row (fnb/express_pay.py, fnb/api_views.py). A stand-in has no
        # contact, so no withholding and no discount can apply to it and its
        # gross IS the cash leaving — falling back here keeps those two screens
        # working instead of 500ing, and cannot hide a withholding, because a
        # withholding needs a real Payment row to have been raised.
        _instruct = getattr(p, 'bank_instruction_amounts', None)
        amount = _instruct()[0] if callable(_instruct) else Decimal(p.amount)
        total += amount

        # creditorAgent.branchId is mandatory per RMB EFT spec v03 §1.6
        # (1..1 R, max 6, must not be only zeroes/spaces — except where
        # creditorAccount.accountType is GRCP in which case it must be
        # zero).
        # Once-off: destination captured inline on the payment.
        _branch_src = (vba.branch_code if vba else p.payee_branch_code) or ''
        _bank_nm = (vba.bank_name if vba else p.payee_bank_name) or ''
        # 🔴 The creditor branch used to be `_branch_src.strip() or
        # FNB_UNIVERSAL_BRANCH` with NO validation of any kind, while the
        # debtor branch three blocks up was checked carefully. That asymmetry
        # cost ten AC08 rejects and BWP 677,284.93 by 17-Sep-2026: a blank
        # branch on a Stanbic payee silently became FNB's own 287867, and
        # '6700' / an 11-digit account number / '64967' / '202-067' / '-'
        # were all passed
        # through untouched. fnb.destination_bank holds the rule, shared with
        # the capture screen (PAY-BANK-05) so the two cannot drift.
        #
        # This ValidationError does NOT block the payment. load_request_to_fnb
        # catches it, writes the sentence to pr.fnb_load_error and RELEASES the
        # claim, so the request stays open and loadable the moment the branch
        # code is corrected — which is the committee's job, not a guess made
        # here (CFO 2026-09-17: never block a payment; the exception committee
        # kicks in).
        creditor_branch = usable_branch_code(_bank_nm, _branch_src,
                                             universal=FNB_UNIVERSAL_BRANCH)
        if creditor_branch is None:
            raise ValidationError(
                f'Payment {p.payment_number}: '
                f'{branch_code_problem(_bank_nm, _branch_src)} '
                f'Nothing was sent to the bank. Correct the branch code on '
                f'the payment request (the exception committee, a finance '
                f'approver or the CFO can do it on the request itself) and '
                f'sign it off again.'
            )
        # VendorBankAccount may not have an explicit account_type column —
        # default to CACC (current). Suffix-match on bank_name for 'savings'.
        _vba_type = getattr(vba, 'account_type', '') or '' if vba else ''
        if not _vba_type and 'savings' in _bank_nm.lower():
            _vba_type = 'savings'
        creditor_acct_type = 'SVGS' if _vba_type.lower() == 'savings' else 'CACC'
        _cred_acct = vba.account_number if vba else p.payee_account_number

        # The operator's explicit choice wins over the derivation (CFO
        # 2026-08-20), and the derivation lives in bank_view_of — the same call
        # the preview screen makes, so the two cannot say different things.
        _view = bank_view_of(p)

        tx: dict = {
            'endToEndId': _view['our_reference'],
            'amount': {
                'currency': batch_currency,
                'value': _q2(amount),
            },
            'creditor': {
                # Fall back AFTER folding: an `or` on the raw value does not
                # fire for a name that is entirely outside the permitted set
                # (e.g. a non-Latin script), which would submit an empty
                # mandatory field. The payment number keeps it traceable.
                'name': _view['beneficiary_name'],
            },
            'creditorAccount': {
                'accountNumber': _cred_acct,
                'accountType':   creditor_acct_type,
            },
            'creditorAgent': {
                'branchId': creditor_branch,
            },
            'remittanceInformationUnstructured': _view['narration'],
        }

        # Email proof-of-payment is optional — only attach if vendor has one
        po_email = getattr(p, 'remittance_email', None) or getattr(vba, 'email', None)
        if po_email:
            tx['remittanceLocationMethod'] = 'EMAL'
            tx['remittanceLocationElectronicAddress'] = po_email

        transactions.append(tx)

    creation_dt = timezone.now()
    exec_date = (requested_execution_date or creation_dt.date()).isoformat()

    # RMB EFT spec v03 §1.6: debtorAgent is mandatory (1..1 R), and so is
    # debtorAgent.branchIdentification. Re-confirmed by Kabelo Sekoto
    # (RMB) 2026-05-26 after he reviewed RJCT submissions earlier today —
    # bicOrBEI stays optional, branchId is the required identifier.
    payment_info: dict = {
        'paymentInformationId': idempotency_key,
        'paymentInformationMethod': 'TRF',
        'batchBooking': True,
        'numberOfTransactions': len(transactions),
        'controlSum': _q2(total),
        'paymentTypeInformationServiceLevelCode': service_level_code,
        'requestedExecutionDate': exec_date,
        'debtor': {
            'name':     initiating_name,
            'bicOrBEI': initiating_bic,
        },
        'debtorAccount': {
            'accountNumber': source_account.account_number,
            'accountType':   debtor_acct_type,
        },
        'debtorAgent': {
            'branchId': debtor_branch,
        },
        'creditTransferTransactionInformation': transactions,
    }

    payload = {
        'groupHeader': {
            'messageId': idempotency_key,
            'creationDateTime': _fnb_iso_dt(creation_dt),
            'initiatingPartyName': initiating_name,
            'initiatingPartyBIC':  initiating_bic,
            'totalNumberOfTransactions': len(transactions),
            'totalControlSum': _q2(total),
        },
        'paymentInformation': [payment_info],
    }
    # RR10 guard: never hand FNB a message it will reject on characters.
    assert_fnb_charset(payload)
    return payload


def creditor_name_for(payment) -> str:
    """The beneficiary name, derived exactly once.

    This is the expression build_batch_payload used inline, kept in one place so
    the payload and the on-screen preview cannot drift: a vendor bank account
    contributes its holder name or its bank name, and if that yields nothing at
    all we fall back to 'Vendor' — NOT to the payee/contact name, which is only
    consulted for a once-off with no vendor bank account. `contact` is read
    defensively because the preview runs against unsaved stand-ins.
    """
    vba = getattr(payment, 'vendor_bank_account', None)
    if vba is not None:
        return (getattr(vba, 'account_holder_name', '')
                or getattr(vba, 'bank_name', '') or 'Vendor')
    contact = getattr(payment, 'contact', None)
    return (getattr(payment, 'payee_name', '')
            or getattr(contact, 'name', '') or 'Vendor')


def payee_led_reference(payment, limit: int = 35) -> str:
    """The payee name first, with the payment number's tail kept — within `limit`.

    CFO 2026-08-21: a payment to Grand RE - Radical Investments sat in FNB Online
    Banking pending authorisation and the Own Reference column read
    `PAY-OUT-2026-000044`, so he could not tell WHO was being paid without
    opening each payment. Slow, and a real risk of authorising the wrong one.

    Why not the bare name, which is what he asked for first: this string is the
    transaction's `endToEndId`, and endToEndId is how a pain.002 status report
    maps a rejection back to ONE payment — the lesson of the RR10 batch. Two
    payments to the same vendor would be indistinguishable in a reject. So the
    name LEADS (which is the readability he wants) and the number's tail follows
    (which keeps it traceable). He picked this shape on 2026-08-21.

    `limit` is the character budget (default the 35-char endToEndId). The caller
    passes a smaller budget when it will append the Omni marker (CFO 2026-08-22),
    so the number's tail is never cut to make room for the marker.

    Returns '' when the name folds to nothing (a non-Latin payee); the caller
    then falls back to the payment number, because endToEndId is mandatory.
    """
    number = (getattr(payment, 'payment_number', '') or '').strip()
    # year+seq, not seq alone: the sequence resets each year, so a bare
    # tail would collide PAY-OUT-2025-000044 with 2026-000044 to one payee —
    # breaking the single-payment traceability this helper exists for (Fable).
    tail = '-'.join(number.split('-')[-2:]) if number else ''
    name = fnb_text(creditor_name_for(payment), limit)
    if not name:
        return ''
    if not tail:
        return name
    room = limit - len(tail) - 1
    if room < 8:
        # A tail so long the name would be unreadable — keep the number whole
        # rather than ship two characters of a name.
        return fnb_text(number, limit)
    return fnb_text(f'{name[:room].strip()} {tail}', limit)


def with_omni_marker(base: str, fallback: str, marker: str = OMNI_MARKER) -> str:
    """`base` + a space + the Omni marker, always within endToEndId's 35 chars.

    The marker must ALWAYS survive (it is the whole point), so the base is
    truncated to make room for it and falls back to `fallback` (the payment
    number) if `base` folds away to nothing.
    """
    room = 35 - len(marker) - 1               # 1 char for the separating space
    folded = fnb_text(base, room) or fnb_text(fallback, room)
    return f'{folded} {marker}'.strip()


def bank_view_of(payment) -> dict:
    """The three bank-facing strings for one payment, exactly as they will be
    sent — and the ONLY place they are derived.

    build_batch_payload calls this, so a screen showing this output is showing
    the payload itself rather than a second implementation of the same rule
    (H74/H55: the figure beside the document must equal the document's).
    """
    marker = omni_marker_for(payment)
    number_140 = fnb_text(payment.payment_number, 140)
    return {
        'beneficiary_name': fnb_text(
            getattr(payment, 'bank_beneficiary_name', '')
            or creditor_name_for(payment), 140,
        ) or number_140,
        # "Our reference" LEADS WITH THE PAYEE (CFO 2026-08-21) and always carries
        # the Omni-origin marker (CFO 2026-08-22). The payee reference is built to
        # a budget that leaves room for the marker, so the number's tail survives.
        'our_reference': with_omni_marker(
            getattr(payment, 'bank_our_reference', '')
            or payee_led_reference(payment, 35 - len(marker) - 1)
            or payment.payment_number,
            payment.payment_number,
            marker,
        ),
        'narration': fnb_text(
            getattr(payment, 'bank_narration', '')
            or getattr(payment, 'description', '')
            or getattr(payment, 'reference', '')
            or payment.payment_number, 140,
        ) or number_140,
    }


# ---------------------------------------------------------------------------
# Submission service
# ---------------------------------------------------------------------------
# Per-batch ceiling on what one submission may move. Enforced in
# submit_eft_batch because all four callers pass through it — the EFT submit
# view, quick transfer, customer refunds and payroll disbursement — so a cap on
# the view alone would leave three doors open.
#
# BWP 2,000,000, set by the CFO on 2026-08-20 (he rejected the 25,000 I had
# proposed). At this level the ceiling is a typo-catcher rather than a brake on
# ordinary business — the P62.4bn draft that prompted PAY-CAP-01 would still be
# stopped dead, and so would a fat-fingered extra zero on a real run — while a
# normal supplier batch and a payroll disbursement both pass.
#
# Raise or lower it with FNB_BATCH_MAX_BWP in /etc/alpha-finance/.env; no
# rebuild needed. Unset means the default below, never "no limit": a missing
# setting must not open the gate.
FNB_BATCH_MAX_BWP_DEFAULT = Decimal('2000000')


def _batch_ceiling() -> Decimal:
    """The per-batch ceiling.

    Unset means the documented default. A value that is present but not a
    positive number REFUSES the submission outright rather than substituting
    the default: this is a control on money, and quietly using 25,000 when the
    operator typed 100 would move more than they authorised. Guessing on a
    business control is the fail-open this guard exists to prevent.
    """
    raw = getattr(settings, 'FNB_BATCH_MAX_BWP', None)
    if raw in (None, ''):
        return FNB_BATCH_MAX_BWP_DEFAULT
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(
            f'FNB_BATCH_MAX_BWP is set to {raw!r}, which is not a number. No '
            'payment will be sent until it is corrected — the limit is not '
            'guessed.'
        ) from None
    if value <= 0:
        raise ValidationError(
            f'FNB_BATCH_MAX_BWP is set to {value}, which would block or '
            'un-limit every payment. Set a positive limit.'
        )
    return value


def _assert_release_is_a_second_person(payments, user, *,
                                       allow_single_person: bool = False) -> None:
    """Whoever created a payment may not be the one who releases it to the bank.

    CFO decision 2026-08-20. Until now one finance leader could commit a pay-run
    AND send it — the invoice approval was the only second pair of eyes, and it
    sits upstream of the batch, so nobody signed the release itself.

    Enforced here rather than in the view because every path to the bank comes
    through this function.

    Anything this cannot VERIFY it refuses, rather than waving through. A
    control that silently skips when it lacks information is not a control:
      * no releasing user  → refuse (nobody to be the second person)
      * a payment carrying no creator → refuse, unless the caller says
        explicitly that it has no creator to compare against
    `allow_single_person=True` is that explicit statement. It exists for the two
    paths whose "payments" are transient objects with no author — quick transfer
    and payroll disbursement — so the exemption is visible at the call site and
    greppable, instead of being an accident of a missing attribute.
    """
    if user is None or getattr(user, 'pk', None) is None:
        raise ValidationError(
            'A payment can only be released to the bank by a named person, so '
            'that it is not the same person who prepared it. No user was '
            'recorded for this release.'
        )
    user_id = user.pk

    clash, unverifiable = set(), set()
    for p in payments:
        number = getattr(p, 'payment_number', '?')
        created_by = getattr(p, 'created_by_id', None)
        if created_by is None:
            unverifiable.add(number)
        elif created_by == user_id:
            clash.add(number)

    def _shown(names):
        ordered = sorted(names)
        return ', '.join(ordered[:5]) + (' …' if len(ordered) > 5 else '')

    if clash:
        raise ValidationError(
            'You created these payments, so someone else has to release them '
            f'to the bank: {_shown(clash)}. Two different people are required '
            '— the one who prepares a payment and the one who sends it.'
        )
    if unverifiable and not allow_single_person:
        raise ValidationError(
            'These payments have nobody recorded as having created them, so we '
            'cannot check that a second person is releasing them: '
            f'{_shown(unverifiable)}.'
        )
    if unverifiable:
        log.warning(
            'FNB release: single-person release permitted for %s (the caller '
            'declared no creator is on record)', _shown(unverifiable))


def _indeterminate(e: 'FNBAPIError') -> bool:
    """True if an FNB error leaves the outcome UNKNOWN — the POST may have been
    received and executed (timeout / gateway 5xx / connection error), so the
    money might have moved. A clean 4xx reject (e.g. 400/422) means FNB refused
    it and no money moved."""
    sc = getattr(e, 'status_code', 0) or 0
    return sc == 0 or sc == 408 or sc >= 500


def _release_claim(real_ids, stamp) -> None:
    """Undo the pre-POST claim on payments that certainly did NOT reach the bank
    (a clean reject, a sign-in failure, FNB not configured). An indeterminate
    outcome keeps the claim — verify with FNB before any resubmit."""
    if not real_ids:
        return
    from payments.models import Payment as _Payment
    _Payment.objects.filter(pk__in=real_ids, bank_submitted_at=stamp).update(
        bank_submitted_at=None)


def submit_eft_batch(
    payments_qs,
    *,
    source_account,
    user: User,
    service_level_code: str = 'SDVA',
    requested_execution_date: date | None = None,
    allow_single_person: bool = False,
    payment_request=None,
) -> FNBBatchSubmission:
    """Bundle the given payments and POST to FNB — TWO-PHASE for money safety.

    Fable C1 (CFO/Kago 2026-07-16 incident): the FNB POST is a real, external,
    NON-transactional money movement. It must NOT sit inside a DB transaction —
    if a later DB write or exception rolled back, the money left the bank while
    every local record (batch row + sync log) vanished. So:

      Phase 1 — persist the PENDING batch + link its payments, COMMITTED, before
                the POST. A record always survives.
      Phase 2 — POST to FNB with NO open transaction around it.
      Phase 3 — record the outcome in a short, separate transaction.

    A timeout / 5xx / connection error is NOT a definitive failure: the batch is
    marked UNKNOWN (verify with FNB before any resubmit), never silently retried.

    Caller preconditions (enforced in the view): every Payment CONFIRMED +
    approved (maker-checker), single currency, source_account in the caller's
    company. Idempotency key = the readable batch name (`_next_idempotency_key`:
    payee/count + Omni reference seq tail + (O)) — FNB dedupes on it.

    Enforced HERE, before anything is persisted or sent: the batch total must
    be within FNB_BATCH_MAX_BWP. All four callers reach the bank through this
    function, so this is the only place a ceiling holds for every one of them.
    """
    payments = list(payments_qs)
    if not payments:
        raise ValidationError('No payments selected to submit.')

    _assert_release_is_a_second_person(
        payments, user, allow_single_person=allow_single_person)

    ceiling = _batch_ceiling()
    total_bwp = sum((p.amount_bwp or Decimal('0') for p in payments),
                    Decimal('0'))
    if total_bwp > ceiling:
        raise ValidationError(
            f'This batch totals BWP {total_bwp:,.2f}, over the per-batch limit '
            f'of BWP {ceiling:,.2f}. Split it, or have the limit raised '
            f'(FNB_BATCH_MAX_BWP) if a run this size is intended.'
        )

    _marker = omni_marker_for(payments[0]) if payments else OMNI_MARKER
    key = _next_idempotency_key(payments, marker=_marker)
    payload = build_batch_payload(
        payments,
        source_account=source_account,
        idempotency_key=key,
        service_level_code=service_level_code,
        requested_execution_date=requested_execution_date,
    )
    real_ids = [pk for pk in (getattr(p, 'pk', None) for p in payments) if pk]
    from payments.models import Payment as _Payment
    claim_stamp = timezone.now()

    # ── Phase 1: committed PENDING record BEFORE the money moves ──────────
    with transaction.atomic():
        if real_ids:
            # Lock the payments and CLAIM them before the bank call. The stamp
            # used to be written only after FNB answered, so two submits inside
            # that window both passed the view's "not yet sent" filter and the
            # same payment was posted twice, under two batch names FNB could not
            # dedupe (Fable 5.1 audit 2026-09-02, H5). The loser now stops here.
            locked = list(_Payment.objects.select_for_update()
                          .filter(pk__in=real_ids)
                          .values_list('pk', 'bank_submitted_at'))
            already = [pk for pk, stamped in locked if stamped is not None]
            if already:
                raise ValidationError(
                    f'{len(already)} of these payments have already been sent to '
                    'the bank, or are being sent right now. Refresh the list — '
                    'nothing was posted.')
            _Payment.objects.filter(pk__in=real_ids).update(bank_submitted_at=claim_stamp)
        batch = FNBBatchSubmission.objects.create(
            idempotency_key  = key,
            source_account   = source_account,
            payment_count    = len(payments),
            total_amount_bwp = sum((p.amount_bwp or Decimal('0') for p in payments),
                                   Decimal('0')),
            currency_code    = payments[0].currency_code_id,
            status           = FNBBatchSubmission.Status.PENDING,
            payload_snapshot = payload,
            submitted_by     = user,
            # Stamped HERE, in phase 1, not after the POST returns.
            # `fnb_autoload` stamps lineage once all batches come back, which
            # covers every SUCCESSFUL submit and nothing else. On an
            # indeterminate submit (timeout/5xx) this function flips the batch
            # to UNKNOWN and re-raises, so that stamp never runs — and
            # `PaymentRequest.fnb_batch` is likewise only written on the
            # success path. Both links stayed NULL on the one outcome where
            # the money MAY have moved.
            #
            # The bank-balances screen counts UNKNOWN batches as money on its
            # way out and excludes their requests from the Omni bucket to
            # avoid subtracting the same payment twice. With neither link
            # written, that request was counted in BOTH — the exact double
            # count that overstated "going out" by P486,835.61 on 20-Sep.
            # A lineage stamp is only worth having if it survives the failure.
            payment_request  = payment_request,
        )
        if real_ids:
            batch.payments.set(real_ids)

    # ── Phase 2: the POST — deliberately OUTSIDE any transaction ──────────
    client = FNBClient(user=user)
    try:
        resp = client.post(
            PAYMENT_INITIATE,
            service        = FNBSyncLog.Service.PAYMENT_BATCH,
            json_body      = payload,
            request_summary= f'Submit EFT batch {key} ({len(payments)} payments)',
            extra_headers  = {'X-Request-ID': str(uuid.uuid4())},
        )
    except FNBNotConfigured:
        FNBBatchSubmission.objects.filter(pk=batch.pk).update(
            status=FNBBatchSubmission.Status.CANCELLED,
            failure_reason='FNB not configured — no POST attempted.')
        _release_claim(real_ids, claim_stamp)
        raise
    except FNBAuthError as e:
        # Omni could not sign in to the bank, so nothing was sent. Before this
        # the error escaped uncaught: the batch stayed PENDING with no reason,
        # the stuck-batch watch ignores PENDING, and nobody was told (Fable 5.1
        # audit 2026-09-02, M9).
        FNBBatchSubmission.objects.filter(pk=batch.pk).update(
            status=FNBBatchSubmission.Status.CANCELLED,
            failure_reason=f'FNB sign-in failed — nothing was sent: {str(e)[:400]}')
        _release_claim(real_ids, claim_stamp)
        raise
    except FNBAPIError as e:
        # Reject (money did NOT move) → FAILED. Timeout/5xx (money MAY have
        # moved) → UNKNOWN, so nobody blind-resubmits and double-pays.
        FNBBatchSubmission.objects.filter(pk=batch.pk).update(
            status=(FNBBatchSubmission.Status.UNKNOWN if _indeterminate(e)
                    else FNBBatchSubmission.Status.FAILED),
            failure_reason=str(e)[:500])
        if not _indeterminate(e):
            _release_claim(real_ids, claim_stamp)
        raise

    # ── Phase 3: record success + stamp payments, separate transaction ────
    body = resp.json or {}
    instruction_id = (body.get('instructionId')
                      or body.get('reference')
                      or body.get('batchId')
                      or body.get('id') or '')
    with transaction.atomic():
        FNBBatchSubmission.objects.filter(pk=batch.pk).update(
            status        = FNBBatchSubmission.Status.SUBMITTED,
            submitted_at  = timezone.now(),
            fnb_reference = str(instruction_id)[:100],
        )
        if real_ids:
            _Payment.objects.filter(pk__in=real_ids).update(
                bank_submitted_at=timezone.now())
    batch.refresh_from_db()
    return batch


# ---------------------------------------------------------------------------
# Status polling
# ---------------------------------------------------------------------------
# Statuses that mean accepted or settled — mirrors the mapping in
# refresh_batch_status below, including the COMPLETED/SETTLED/PROCESSED
# synonyms. Kept as one name so the two cannot drift apart.
_ACCEPTED_STATUSES = frozenset({
    'ACSC', 'ACCC', 'ACSP', 'ACCP', 'COMPLETED', 'SETTLED', 'PROCESSED',
})


# Transaction statuses in a pain.002 that mean this ONE payment did not go.
# RJCT is the ISO code; VALIDATION_FAILED is what RMB actually emitted on the
# 2026-08-19 RR10 batch (see fnb/tests.py).
_TXN_REJECTED_STATUSES = frozenset({'RJCT', 'VALIDATION_FAILED', 'REJECTED'})


def _txn_rows(body: dict) -> list[dict]:
    """Every transactionInfoAndStatus row in a status report, flattened."""
    rows: list[dict] = []
    for opi in (body.get('originalPaymentInformation') or []):
        rows.extend(opi.get('transactionInfoAndStatus') or [])
    return rows


def _payment_by_end_to_end_id(payments: list, wanted: str):
    """The ONE payment in this batch whose endToEndId is `wanted`, else None.

    Two passes, both fail-closed — 0 or 2+ candidates returns None and the
    caller leaves the batch alone for a person to look at. Guessing which
    payment a bank reject belongs to is exactly the mistake the RR10 incident
    cost 50 days on; a wrong guess here would release the wrong payment for
    re-sending.

      1. exact match on the endToEndId we actually sent, rebuilt with the same
         `bank_view_of` builder that produced it, so the two cannot drift;
      2. the payment-number tail (`year-seq`), which `payee_led_reference`
         deliberately keeps for precisely this purpose — used when the bank
         echoes a reformatted or truncated reference.
    """
    wanted = (wanted or '').strip()
    if not wanted:
        return None

    exact = [p for p in payments
             if bank_view_of(p)['our_reference'].strip() == wanted]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None

    def _tail(number: str) -> str:
        return '-'.join((number or '').split('-')[-2:])

    tailed = [p for p in payments
              if _tail(p.payment_number) and _tail(p.payment_number) in wanted]
    return tailed[0] if len(tailed) == 1 else None


def _reconcile_transactions(batch: FNBBatchSubmission, body: dict) -> dict:
    """Release the payments the bank rejected inside a PARTLY-accepted batch.

    Manus nine-area retest P2 (2026-08-25). A whole-batch RJCT already releases
    every payment (see refresh_batch_status below, Fable H1). PART and ACWC did
    not: they landed the batch in ACKNOWLEDGED with the comment "needs per-txn
    reconcile" and nothing ever did that reconcile — so a payment the bank
    refused inside an otherwise-accepted batch kept `bank_submitted_at` set and
    could never be re-sent. Claims payments reach FNB through the same path
    (taskboard/fnb_autoload.py), so they were affected too.

    Only the REJECTED transactions are released. An accepted one is left exactly
    as it is — the money moved. Returns a summary for the caller to log.
    """
    from payments.models import Payment as _Payment

    rows = _txn_rows(body)
    if not rows:
        return {'rows': 0, 'released': 0, 'unmatched': 0, 'skipped': 0,
                'notes': []}

    payments = list(batch.payments.all())
    released, unmatched, skipped, notes = [], 0, 0, []

    for row in rows:
        txn_status = (row.get('transactionStatus') or '').upper()
        if txn_status not in _TXN_REJECTED_STATUSES:
            continue                        # accepted → money moved, leave it
        ref = (row.get('originalEndToEndId') or row.get('endToEndId') or '')
        payment = _payment_by_end_to_end_id(payments, ref)
        if payment is None:
            unmatched += 1
            log.warning('batch %s: rejected transaction %r matched no single '
                        'payment — left for manual review',
                        batch.idempotency_key, ref)
            continue

        reasons = row.get('statusReasonInformation') or []
        detail = '; '.join(
            f"{r.get('reason','')}: {(r.get('additionalInformation') or '')[:140]}"
            for r in reasons
        )[:500]
        try:
            from .reject_codes import describe_rejection
            explanation = describe_rejection(txn_status, reasons)
        except Exception as exc:                            # noqa: BLE001
            log.warning('per-txn reject explanation failed for %s (%s: %s)',
                        payment.payment_number, type(exc).__name__, exc)
            explanation = ''
        if explanation:
            detail = f'{detail} — {explanation}' if detail else explanation

        # The note describes the REPORT, so build it on every poll regardless of
        # whether anything is released this time. refresh_batch_status rewrites
        # failure_reason from the report each poll, so a note built only from
        # "what I released just now" would appear once and then vanish on the
        # next refresh — taking with it the one line that says WHICH payment the
        # bank refused and why.
        notes.append(f'{payment.payment_number}: {detail}'[:400])

        # RELEASE GUARDS (Fable H90, 2026-08-25). This function runs on EVERY
        # poll of a PART/ACWC batch, not only the first transition — and
        # ACKNOWLEDGED is precisely the state a person comes back and refreshes.
        if payment.bank_submitted_at is None:
            continue                        # already released; nothing to do

        # Never unlock a payment that has since been RESUBMITTED. Without this,
        # "rejected in batch 1 → released → cause fixed → resubmitted in batch 2
        # → somebody refreshes batch 1" clears bank_submitted_at on an IN-FLIGHT
        # payment, and that flag is the only thing stopping a third send. This
        # company has already paid P399,338.10 twice; that is the class of bug.
        # The old report simply does not speak for the payment any more.
        if payment.fnb_batches.filter(created_at__gt=batch.created_at).exists():
            skipped += 1
            log.warning('batch %s: payment %s was rejected here but has since '
                        'been resubmitted in a newer batch — leaving it locked',
                        batch.idempotency_key, payment.payment_number)
            continue

        released.append(payment.pk)

    if released:
        # Clear bank_submitted_at ONLY on the rejected ones, so an approved
        # payment can go again once the cause is fixed. `bank_submitted_at__isnull=False`
        # is belt-and-braces against a concurrent poll: the loop already skipped
        # released and resubmitted payments, and this makes the write itself
        # conditional rather than trusting the read.
        _Payment.objects.filter(
            pk__in=released, bank_submitted_at__isnull=False,
        ).update(bank_submitted_at=None)

    return {'rows': len(rows), 'released': len(released),
            'unmatched': unmatched, 'skipped': skipped, 'notes': notes}


def refresh_batch_status(batch: FNBBatchSubmission, *, user: User | None = None) -> dict:
    """Hit FNB's retrieveReport endpoint for this batch's instructionId
    and persist the latest groupStatus + statusReasonInformation on the row.

    Called by:
      - the post-submit hook (immediately after submit_eft_batch)
      - a cron every 5 min for batches in SUBMITTED state
      - the operator clicking 'Refresh' in the FNB Batches UI

    The mapping from FNB groupStatus → FNBBatchSubmission.Status:
      ACSC, ACCC (+ COMPLETED / SETTLED / PROCESSED)  → SETTLED (money debited)
      ACCP, ACSP                                      → SUBMITTED (in-flight)
      RJCT                                            → FAILED (no money moved)
      PART, ACWC                                      → ACKNOWLEDGED (a person
                                                         must look: partly paid,
                                                         or accepted with a
                                                         change such as a bumped
                                                         execution date)
      anything else                                   → left unchanged

    A non-accepted status also gets a plain-English explanation appended to
    failure_reason (see fnb/reject_codes.py).
    """
    if not batch.fnb_reference:
        raise ValidationError('Batch has no FNB reference to look up.')

    client = FNBClient(user=user)
    resp = client.get(
        PAYMENT_STATUS.format(instruction_id=batch.fnb_reference),
        service        = FNBSyncLog.Service.PAYMENT_BATCH,
        request_summary= f'Status poll for batch {batch.idempotency_key}',
    )

    body = resp.json or {}
    group_status = (body.get('groupStatus') or '').upper()

    # Collect reasons across all three possible ISO 20022 nesting levels.
    # The pain.002-style status report places statusReasonInformation at
    # (a) the message root, (b) each originalPaymentInformation block, and
    # (c) each transactionInfoAndStatus row. RMB's sandbox typically leaves
    # the root array empty and emits the actual reject reason at level (b).
    reasons: list[dict] = []
    reasons.extend(body.get('statusReasonInformation') or [])
    for opi in (body.get('originalPaymentInformation') or []):
        reasons.extend(opi.get('statusReasonInformation') or [])
        for txn in (opi.get('transactionInfoAndStatus') or []):
            reasons.extend(txn.get('statusReasonInformation') or [])

    # ISO 20022 pain.002 groupStatus (Fable M1 — old mapping never reached
    # SETTLED and treated a partial as a plain fail):
    #   ACSC / ACCC = settlement completed → SETTLED (money debited)
    #   ACCP / ACSP = accepted, in-flight   → SUBMITTED
    #   RJCT        = rejected (whole batch, no money moved) → FAILED
    #   PART        = partially accepted (some txns paid)     → its own state
    #   ACWC        = accepted WITH A CHANGE (e.g. date bumped) → same state
    if group_status in {'ACSC', 'ACCC'}:
        new_status = FNBBatchSubmission.Status.SETTLED
    elif group_status in {'ACCP', 'ACSP'}:
        new_status = FNBBatchSubmission.Status.SUBMITTED
    elif group_status == 'RJCT':
        new_status = FNBBatchSubmission.Status.FAILED
    elif group_status in {'PART', 'ACWC'}:
        # PART = some paid, some rejected. ACWC = accepted but the bank changed
        # something (spec V-03 §2.2 gives an auto-bumped execution date as the
        # example, so it is routine). Both need a person to look, and ACWC used
        # to fall through to the `else` below and leave the batch sitting in
        # SUBMITTED for ever — the money moves and nobody is told.
        new_status = FNBBatchSubmission.Status.ACKNOWLEDGED  # needs per-txn reconcile
    elif group_status in {'COMPLETED', 'SETTLED', 'PROCESSED'}:
        new_status = FNBBatchSubmission.Status.SETTLED
    else:
        new_status = batch.status   # leave as-is

    failure_reason = ''
    if reasons:
        failure_reason = '; '.join(
            # additionalInformation can be JSON null → guard the slice (Fable M1)
            f"{r.get('reason','')}: {(r.get('additionalInformation') or '')[:140]}"
            for r in reasons
        )[:500]

    # Say in plain English what the bank's codes mean, so `RR10` is not the
    # whole story Finance gets. Deterministic table first, AI only for a code
    # the table does not carry (fnb/reject_codes.py). Explaining a reject must
    # never be able to stop it being recorded, hence the broad except.
    explanation = ''
    # Every status that means accepted or settled, including the synonyms
    # handled above — a settled batch must not be decorated with
    # reject-flavoured text (or trigger an AI call) just because its
    # report carried a reason entry.
    if group_status and group_status not in _ACCEPTED_STATUSES:
        try:
            from .reject_codes import describe_rejection
            explanation = describe_rejection(group_status, reasons)
        except Exception as exc:                               # noqa: BLE001
            log.warning('reject explanation failed for batch %s (%s: %s)',
                        batch.idempotency_key, type(exc).__name__, exc)
    if explanation:
        failure_reason = (f'{failure_reason} — {explanation}' if failure_reason
                          else explanation)[:2000]

    # Stamp the moment a batch reaches a state, not every time we poll it —
    # otherwise the column reads "last polled" instead of "settled" (CFO
    # 2026-08-20: a batch showing `settled` had no settlement date at all,
    # because nothing ever wrote one).
    stamps = {}
    now = timezone.now()
    if (new_status == FNBBatchSubmission.Status.SETTLED
            and batch.settled_at is None):
        stamps['settled_at'] = now
    if (new_status == FNBBatchSubmission.Status.ACKNOWLEDGED
            and batch.acknowledged_at is None):
        stamps['acknowledged_at'] = now

    with transaction.atomic():
        FNBBatchSubmission.objects.filter(pk=batch.pk).update(
            status=new_status,
            failure_reason=failure_reason or batch.failure_reason,
            **stamps,
        )
        # Terminal whole-batch reject → the money never moved, so release the
        # payments (clear bank_submitted_at) or they are locked forever and the
        # approved payment can never be re-sent (Fable H1). Uses the real
        # batch↔payment link (H3).
        if new_status == FNBBatchSubmission.Status.FAILED:
            from payments.models import Payment as _Payment
            ids = list(batch.payments.values_list('pk', flat=True))
            if ids:
                # Fable H90 (2026-08-25): PRE-EXISTING flaw, same class as the
                # per-transaction path below. refresh_batch_status runs on every
                # poll, so re-reading an old FAILED batch after its payments were
                # fixed and RESUBMITTED would clear bank_submitted_at on the
                # in-flight ones — and that flag is the only thing stopping a
                # second send. Exclude anything that has since gone out in a
                # newer batch; the old report no longer speaks for it.
                resubmitted = set(
                    _Payment.objects.filter(pk__in=ids)
                    .filter(fnb_batches__created_at__gt=batch.created_at)
                    .values_list('pk', flat=True)
                )
                if resubmitted:
                    log.warning('batch %s: %d payment(s) rejected here have '
                                'since been resubmitted — leaving them locked',
                                batch.idempotency_key, len(resubmitted))
                releasable = [i for i in ids if i not in resubmitted]
                if releasable:
                    _Payment.objects.filter(
                        pk__in=releasable, bank_submitted_at__isnull=False,
                    ).update(bank_submitted_at=None)
        # PART / ACWC — some transactions went, some did not. Release only the
        # rejected ones (see _reconcile_transactions). Fail-closed: anything the
        # report cannot pin to exactly one payment is logged and left alone.
        elif new_status == FNBBatchSubmission.Status.ACKNOWLEDGED:
            summary = _reconcile_transactions(batch, body)
            if summary['notes'] or summary['unmatched']:
                tail = (f"per-transaction: {summary['released']} released, "
                        f"{summary['skipped']} already sent again, "
                        f"{summary['unmatched']} unmatched")
                joined = ' | '.join(summary.get('notes') or [])
                extra = f'{tail} — {joined}' if joined else tail
                FNBBatchSubmission.objects.filter(pk=batch.pk).update(
                    failure_reason=(
                        f'{failure_reason} — {extra}' if failure_reason else extra
                    )[:2000],
                )
    batch.refresh_from_db()
    return body
