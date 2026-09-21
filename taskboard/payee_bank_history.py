"""
taskboard/payee_bank_history.py

What bank account did we last pay this payee into, and is this one different?

Two CFO asks from 2026-08-20, and they are the same lookup seen from two sides:

  "create an AI helper here where the AI will automatically fill in the bank
   account details of the previous payment when the name of the supplier is
   loaded"

  "put additional controls where if a person is changing the bank account
   details it rejects, saying 'Why are you doing this because you paid this
   person with another bank account?'"

**Deliberately not an AI.** Remembering which account we paid someone into is a
lookup, and a lookup is exactly right and exactly repeatable. A model asked to
recall a bank account can be confidently wrong, and this is the field that
decides who receives the money — the one place in Omni where a plausible
invention is most expensive. Same rule as the reject-code explainer built earlier
today: deterministic code decides, AI only explains. If we later want help
matching *names* ("ABC Traders" vs "A.B.C. Traders (Pty) Ltd"), that is a
different, safe job for a model, because a human still confirms the account.

Where history comes from, in order:
  1. The supplier's approved bank account in the vendor register
     (procurement.VendorBankAccount) — the authoritative record when it exists.
  2. Otherwise the most recent payment request raised for the same payee.
     This carries the load today: the register holds 2 accounts against 9,403
     vendor contacts, while 81 of 99 payment requests already have an account
     number typed on them.
"""
from __future__ import annotations

import re

# A payee is typed by hand every time, so compare on a squashed form: case,
# punctuation and the company-suffix noise that varies between typists.
_NOISE = re.compile(r'\b(pty|ltd|limited|inc|cc|co|company|t/a|and|the)\b')
_NON_ALNUM = re.compile(r'[^a-z0-9]+')


def normalise_payee(name: str) -> str:
    """'ABC Traders (Pty) Ltd.' and 'abc traders pty ltd' become one key."""
    s = (name or '').strip().lower()
    s = _NOISE.sub(' ', s)
    s = _NON_ALNUM.sub('', s)
    return s


def _digits(account: str) -> str:
    """Compare accounts on digits only — people type spaces and dashes."""
    return re.sub(r'\D', '', account or '')


def _edit_distance(a: str, b: str, cap: int = 2) -> int:
    """Levenshtein distance between two keys, bailing out once it exceeds cap."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            cur.append(v)
            best = min(best, v)
        if best > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _same_payee(candidate: str, key: str) -> bool:
    """Exact key match, or NEAR enough that it is the same supplier respelled.

    Fable 5.1 audit (2026-09-02, H1): the changed-account check keyed on the
    exact typed spelling, so "Carfil Service" (one letter dropped) with a new
    account was treated as a brand-new payee — the raiser ticked the
    first-payment box and the request skipped the committee entirely. A near
    miss is therefore treated as the SAME payee. Being too generous sends a
    genuinely new supplier to the committee, which fails safe; being too strict
    is how invoice fraud gets paid. Keys are already normalised (lower-case,
    no punctuation, company suffixes dropped).
    """
    if not candidate or not key:
        return False
    if candidate == key:
        return True
    shorter, longer = sorted((candidate, key), key=len)
    if len(shorter) < 6:
        return False                    # too short to call a near miss safely
    # One name is the other plus a tail: "carfilservices" vs "carfilservicesbw"
    # or "carfilservicesrepair".
    if longer.startswith(shorter):
        return True
    cap = 1 if len(shorter) < 9 else 2
    return _edit_distance(candidate, key, cap) <= cap


def last_known_bank(payee: str, *, exclude_pk=None, exact=False) -> dict | None:
    """The bank details we last used for this payee, or None if never paid.

    Returns {'source', 'source_label', 'account_name', 'account_number',
             'bank_name', 'branch_code', 'account_type', 'when', 'ref',
             'matched_name', 'matched_exact'}.

    `matched_name` is the name on the record we matched, and `matched_exact`
    says whether it is the same name that was typed or only a NEAR miss that
    `_same_payee` accepted. Unopa bug cf6c042d (2026-09-17): a fuzzy hit made
    the change-challenge state "you paid <typed name> into an account ending
    1234 before" when in truth a DIFFERENT supplier with a similar name was
    paid into it, so the message read as a fraud accusation over two suppliers
    that merely rhyme. The callers use these two fields to say which record
    they matched; whether the warning fires at all is deliberately unchanged.

    `exact` (Fable 5.1 audit 2026-09-02): the fuzzy `_same_payee` match is right
    for the FRAUD checks — being generous there just routes more to the
    committee, which fails safe. But it is WRONG for AUTOFILL: a fuzzy hit would
    attach a different real supplier's account to this payment ("Gaborone
    Motels" borrowing "Gaborone Motors"' account), with the change/first-payment
    controls both suppressed. So the autofill and prefill callers pass
    exact=True and get an account back only on an EXACT name match.
    """
    key = normalise_payee(payee)
    if not key:
        return None

    def _matches(candidate_key: str) -> bool:
        return candidate_key == key if exact else _same_payee(candidate_key, key)

    # 1. The vendor register, if this payee is in it with an account.
    try:
        from procurement.models import VendorBankAccount
        # No row cap. A cap silently switches the change-challenge OFF for the
        # oldest payees — exactly the long-dormant supplier that invoice fraud
        # picks — and the tests stay green while the control is gone.
        for vba in (VendorBankAccount.objects
                    .select_related('contact')
                    .exclude(account_number='')
                    .order_by('-is_default', '-created_at')
                    .iterator()):
            contact_name = getattr(vba.contact, 'name', '') or ''
            if _matches(normalise_payee(contact_name)):
                return {
                    'source': 'vendor_register',
                    'source_label': 'the supplier bank register',
                    'account_name': vba.account_holder_name or contact_name,
                    'account_number': vba.account_number,
                    'bank_name': vba.bank_name or '',
                    'branch_code': vba.branch_code or '',
                    'account_type': '',
                    'when': vba.created_at,
                    'ref': '',
                    'matched_name': contact_name,
                    'matched_exact': normalise_payee(contact_name) == key,
                }
    except Exception:                                          # noqa: BLE001
        # A lookup helper must never break the form it is helping.
        pass

    # 2. The most recent payment request for the same payee.
    from .models import PaymentRequest
    # Newest first, streamed, break on the first match — no row cap, for the
    # same reason as above. Only the columns needed, so scanning the whole table
    # stays cheap on a debounced lookup.
    # Drafts are excluded: a Drop Box draft is an UNsubmitted, unverified row
    # carrying whatever account the reader saw. If it counted, a draft with a new
    # account would shadow the payee's real history and switch the bank-change
    # challenge OFF for its own submit — the exact fraud check it must not evade.
    qs = (PaymentRequest.objects
          .exclude(account_number='')
          .exclude(status=PaymentRequest.Status.DRAFT)
          .order_by('-created_at')
          .values('payee', 'account_name', 'account_number', 'bank_name',
                  'branch_code', 'account_type', 'created_at', 'ref', 'pk'))
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    for row in qs.iterator():
        row_name = row['payee'] or row['account_name'] or ''
        if _matches(normalise_payee(row_name)):
            return {
                'source': 'previous_request',
                'source_label': ('the last payment request for this payee '
                                 f'({row["ref"]})'),
                'account_name': row['account_name'] or row['payee'],
                'account_number': row['account_number'],
                'bank_name': row['bank_name'] or '',
                'branch_code': row['branch_code'] or '',
                'account_type': row['account_type'] or '',
                'when': row['created_at'],
                'ref': row['ref'],
                'matched_name': row_name,
                'matched_exact': normalise_payee(row_name) == key,
            }
    return None


def bank_change_warning(payee: str, account_number: str, *,
                        exclude_pk=None) -> dict | None:
    """None if this account matches history, or there is no history.

    Otherwise the CFO's question, ready to show:
      "Why are you doing this because you paid this person with another bank
       account?"

    Supplier bank-account substitution is the classic invoice fraud: the payee
    and the amount look right, only the account changed. Nobody typing a payment
    request should be able to move a supplier's account without saying why.
    """
    known = last_known_bank(payee, exclude_pk=exclude_pk)
    if known is None:
        return None
    if not _digits(account_number):
        return None
    if _digits(account_number) == _digits(known['account_number']):
        return None

    old_tail = _digits(known['account_number'])[-4:]
    new_tail = _digits(account_number)[-4:]
    matched_name = known.get('matched_name') or payee
    exact = known.get('matched_exact', True)
    # Name the record we actually matched. On a near miss it may be a DIFFERENT
    # supplier whose name merely rhymes (Unopa cf6c042d), and telling the raiser
    # "you paid THIS supplier into another account" is then simply untrue.
    if exact:
        opening = (f'Why are you doing this? You paid {payee} into an account '
                   f'ending {old_tail} before, and this request says {new_tail}. '
                   f'That is from {known["source_label"]}.')
        closing = ''
    else:
        opening = (f'Why are you doing this? Omni holds an account ending '
                   f'{old_tail} for "{matched_name}" — a close match to the name '
                   f'you typed ("{payee}"), not the same spelling — and this '
                   f'request says {new_tail}. That is from '
                   f'{known["source_label"]}.')
        closing = ('\n\nIf "' + matched_name + '" is a DIFFERENT supplier, the '
                   'two names are close enough that Omni cannot tell them apart. '
                   'Say so below and carry on — nothing is being changed on '
                   'either supplier.')
    return {
        'control': 'PAY-BANK-01',
        'payee': payee,
        'matched_name': matched_name,
        'matched_exact': exact,
        'known_account_tail': old_tail,
        'new_account_tail': new_tail,
        'known_source': known['source_label'],
        'detail': (
            f'{opening}\n\n'
            'If the supplier really has changed banks, say so below and who '
            'confirmed it — and confirm it by phoning a number you already had '
            'for them, never a number on the new invoice. A changed account '
            'number on a familiar supplier is the most common invoice fraud '
            f'there is.{closing}'
        ),
    }


def _same_bank_text(a: str, b: str) -> bool:
    """Compare a bank or branch on the same squashed key as a payee name, so
    'FNB Botswana' and 'fnb botswana' are one bank and only a REAL change
    registers. One normaliser for all three fields, never a second one."""
    return normalise_payee(a) == normalise_payee(b)


def bank_details_changed(payee: str, *, bank_name: str = '', branch_code: str = '',
                         account_number: str = '', exclude_pk=None) -> dict | None:
    """Which of Bank / Branch code / Account number differs from what we hold.

    Finance spec 2026-09-08: "For payees that already have a verified account on
    record, any change to Bank/Branch code/Account number on this form should
    re-trigger the same first-time-payee verification flow rather than silently
    overwriting the stored details."

    DELIBERATELY SEPARATE from bank_change_warning above, which compares the
    ACCOUNT NUMBER only and routes a mismatch to the fraud committee. Widening
    that one would send a request to a three-of-six committee because somebody
    respelled a bank name — heavy, and not what was asked. This one re-triggers
    the VERIFICATION flow: a named verifier who is not the preparer, and Finance
    confirming the details against the supporting document at sign-off.

    Returns None when nothing we hold has changed (or there is no history),
    otherwise {'control', 'payee', 'fields': [...], 'detail'}.
    """
    known = last_known_bank(payee, exclude_pk=exclude_pk)
    if known is None:
        return None                      # no history — first_payment_warning owns this

    changed = []
    # Only fields the form actually filled in are compared: a blank branch on a
    # request for an FNB payee is a legitimate omission (FNB falls back to the
    # FNB-to-FNB branch), not a change to something else.
    if _digits(account_number) and known.get('account_number') and (
            _digits(account_number) != _digits(known['account_number'])):
        changed.append('account number')
    if bank_name and known.get('bank_name') and not _same_bank_text(
            bank_name, known['bank_name']):
        changed.append('bank')
    if branch_code and known.get('branch_code') and not _same_bank_text(
            branch_code, known['branch_code']):
        changed.append('branch code')
    if not changed:
        return None

    matched_name = known.get('matched_name') or payee
    exact = known.get('matched_exact', True)
    held_for = (payee if exact
                else f'"{matched_name}" (a close match to "{payee}", not the '
                     f'same spelling)')
    return {
        'control': 'PAY-BANK-04',
        'payee': payee,
        'matched_name': matched_name,
        'matched_exact': exact,
        'fields': changed,
        'known_source': known['source_label'],
        'detail': (
            f'The {" and the ".join(changed)} on this request '
            f'{"differs" if len(changed) == 1 else "differ"} from what Omni holds '
            f'for {held_for} (from {known["source_label"]}).\n\n'
            'Changed bank details are checked the same way a brand-new payee is: '
            'somebody in Finance other than the person raising this must confirm '
            'them against the beneficiary details on the supporting document. '
            'They are never quietly overwritten.'
        ),
    }


def first_payment_warning(payee: str, account_number: str, *,
                          exclude_pk=None) -> dict | None:
    """The NEW-PAYEE control (PAY-BANK-03, CFO 2026-09-01).

    PAY-BANK-01 above only fires when we have paid this payee BEFORE — it
    compares the new account against the known one. A payee with no history
    returns None there, so until now a brand-new supplier could be paid into
    any account with nothing at all challenging it. That is precisely the hole
    a fabricated invoice from a fabricated supplier walks through, and reading
    the account off the invoice automatically (invoice OCR) widens it: nobody
    is forced to even look at the digits.

    So: no history + an account number = the raiser must tick that they checked
    these digits against the invoice itself and confirmed the supplier through
    a channel they already had. This asks for a DELIBERATE CONFIRMATION, not a
    written reason — there is no prior account to explain a change from, and
    demanding an essay for every genuinely new supplier would train people to
    paste nonsense to get past it.
    """
    if not _digits(account_number):
        return None
    if last_known_bank(payee, exclude_pk=exclude_pk) is not None:
        return None                      # known payee — PAY-BANK-01 owns this

    tail = _digits(account_number)[-4:]
    return {
        'control': 'PAY-BANK-03',
        'payee': payee,
        'new_account_tail': tail,
        'detail': (
            f'This is the first time Omni has seen a payment to {payee}, so '
            f'there is no previous account to check the number against.\n\n'
            f'Read the account ending {tail} off the invoice one more time and '
            'confirm it matches, digit for digit. Then confirm the supplier is '
            'genuine using a phone number or contact you already had — never a '
            'number printed on the invoice itself. A new supplier with a bank '
            'account nobody has verified is how invoice fraud gets paid.'
        ),
    }
