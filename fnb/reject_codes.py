"""
fnb/reject_codes.py

Turn a bank reject code into something a person in Finance can act on.

Why this exists (2026-08-20): FNB rejected a payment batch with
`statusReasonInformation: [{reason: "RR10", additionalInformation: "INVALID
CHARACTER SET"}]`. Working out what that meant took reading the RMB message
specification. `RR10` is one of roughly a hundred ISO 20022 reason codes, so the
next reject will be a different one and nobody in Finance will know what it
means either. What lands in `failure_reason` today is the raw code, and a raw
code is only marginally better than silence.

The design is deliberately two-tier, and the order matters:

  1. KNOWN_REASONS — a fixed table. Deterministic, offline, auditable, and the
     answer for the codes we actually meet. This is the source of truth.
  2. AI — only for a code the table does not carry, through
     `reasoning_complete` (local/Gemini/DeepSeek, per Omni's usual chain). Its
     answer is clearly labelled as an unverified reading.

The character-set rule that caused the original incident is enforced in code
(`fnb.payments.assert_fnb_charset`), NOT here. Nothing in this module decides
whether a payment may be sent — it only explains a decision the bank already
made. Correctness on money stays deterministic.

PII: the prompt carries the CODE, plus a field NAME only when it matches a
known ISO 20022 field (see `field_hint`). The bank's free-text
`additionalInformation` is never forwarded, so payee names, account numbers and
remittance narratives have no path out of the box at all (AD-POL-AI-GOV-001).
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# A reject code is a short alphanumeric token. Anything else is not a
# code and is never forwarded to a model.
_CODE_SHAPE = re.compile(r'[A-Z0-9]{1,6}')
_AI_CACHE_PREFIX = 'fnb_reject_ai:'
_AI_CACHE_TTL = 7 * 24 * 3600      # a code's meaning does not change

# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------
# `ours` answers the only question that matters first: do we fix this, or does
# the bank? Getting that wrong cost 50 days on the RR10 incident, where we told
# FNB their credentials were broken and the fault was our own text.
#
# Sources: ISO 20022 ExternalStatusReason1Code, and for RR10 the reject FNB
# actually returned on 2026-08-19 with its own wording "INVALID CHARACTER SET".
# Codes we have not met and cannot source are deliberately absent — an invented
# meaning on a payment reject is worse than "unknown", because someone will act
# on it. Add a row only when the bank's own wording or the ISO list confirms it.
KNOWN_REASONS: dict[str, dict[str, str]] = {
    'RR10': {
        'plain': 'The text we sent contained a character the bank does not '
                 'accept — usually a long dash, a curly quote or an accented '
                 'letter pasted in from Word or Outlook.',
        'action': 'Omni now strips these automatically before sending. Resend '
                  'the payment; it should go through unchanged.',
        'ours': 'yes',
    },
    'AC01': {
        'plain': 'The bank says the account number is not a valid account.',
        'action': "Check the payee's account number against their bank "
                  'confirmation letter, correct it, then resend.',
        'ours': 'yes',
    },
    'AC04': {
        'plain': 'The account exists but has been closed.',
        'action': 'Ask the payee for current banking details. Do not resend to '
                  'the same account.',
        'ours': 'yes',
    },
    'AC06': {
        # The bank flags a block on the payee's account. This is the payee's
        # bank's decision and it may be lifted at any time, so the Omni message
        # must not read as a permanent refusal. Hold the payment and flag the
        # request to the exception committee for review — three of six named
        # people decide whether to wait, request updated details or release on
        # confirmation from the payee. (CFO wording rule, 18-Sep-2026: never
        # imply a permanent block; the committee path is always available.)
        'plain': 'The payee\'s bank has flagged a block on that account, so it '
                 'cannot receive money at the moment. The block may be lifted '
                 'by the payee\'s own bank.',
        'action': 'Hold this payment and flag the request to the exception '
                  'committee for review. The committee will decide whether to '
                  'wait for confirmation from the payee that the block has '
                  'been lifted, or ask for updated banking details.',
        'ours': 'no',
    },
    'AM04': {
        'plain': 'Not enough money in the account we are paying from.',
        'action': 'Check the balance on the paying account and resend once it '
                  'is funded. Nothing left our account.',
        'ours': 'yes',
    },
    'AM05': {
        'plain': 'The bank treated this as a duplicate of a payment it has '
                 'already received.',
        'action': 'STOP and check whether the earlier one was paid before you '
                  'resend, or the payee may be paid twice.',
        'ours': 'yes',
    },
    'DT01': {
        'plain': 'The payment date we asked for is not one the bank will '
                 'accept — usually a date in the past, or a weekend or public '
                 'holiday.',
        'action': 'Set the payment date to the next working day and resend.',
        'ours': 'yes',
    },
    'RC01': {
        'plain': 'The branch code or bank identifier is wrong.',
        'action': "Check the payee's branch code, correct it, then resend.",
        'ours': 'yes',
    },
    # ── Added 2026-09-17, each sourced from FNB'S OWN WORDING on a live
    # reject, not from a guess. Until now these four were absent, so
    # describe_rejection fell through to the AI, which invented a DIFFERENT
    # explanation every time and got the commonest one WRONG: AC08 was being
    # shown to staff as "the account number does not exist", when FNB's own
    # text on the same row says "BRANCH CODE IS INVALID OR MISSING". Ten
    # rejects worth BWP 677,284.93 were read as an account problem and the
    # branch codes were never corrected.
    'AC08': {
        # FNB's returned text: "AC08: BRANCH CODE IS INVALID OR MISSING".
        # ISO 20022 ExternalStatusReason1Code AC08 = InvalidBranchCode.
        'plain': "The payee's BRANCH CODE is missing or wrong. This is not the "
                 'account number — it is the six-digit code that says which '
                 'branch the account is held at.',
        'action': "Check the branch code on the payee's bank confirmation "
                  'letter. It is exactly six digits and a leading zero counts '
                  '(064967 is not 64967). If it was left blank for a bank other '
                  "than FNB, Omni used FNB's own branch, which never works. Fix "
                  'it on the payment request and load it again.',
        'ours': 'yes',
    },
    'AC03': {
        # FNB's returned text: "AC03: INVALID CREDITOR ACCOUNT NUMBER".
        # ISO 20022 AC03 = InvalidCreditorAccountNumber.
        'plain': 'The account number we gave for the payee is not a valid '
                 'account number at that bank.',
        'action': "Check the payee's account number against their bank "
                  'confirmation letter — digits only, no spaces or dashes — '
                  'then load it again.',
        'ours': 'yes',
    },
    'RR09': {
        # FNB's returned text: "RR09: INVALID STRUCTURED CREDITOR REFERENCE".
        # ISO 20022 RR09 = InvalidStructuredCreditorReference.
        'plain': 'The reference we put on the payment is not in a form the bank '
                 'accepts — usually it is too long, or carries characters the '
                 'bank will not take in a reference field.',
        'action': 'Shorten the payment reference and keep it to plain letters, '
                  'numbers and spaces, then load it again. The money never left '
                  'the account.',
        'ours': 'yes',
    },
    'FF10': {
        # FNB's returned text: "FF10: FILE OR TRANSACTION CANNOT BE PROCESSED
        # DUE TO TECHNICAL ISSUES AT THE BANK SIDE".
        # ISO 20022 FF10 = BankSystemProcessingError.
        'plain': 'A technical problem at the bank, not with our instruction. '
                 'FNB could not process the file.',
        'action': 'Nothing is wrong with the payment itself. Load it again '
                  'later, and if it keeps failing ask FNB to look at their own '
                  'processing.',
        'ours': 'no',
    },
    'AG01': {
        # TransactionForbidden, formerly NoAgreement. The missing agreement or
        # product permission is as often on OUR account as on the payee's, so
        # this must not claim a side — asserting the bank's fault when it is
        # ours is the exact misdirection that cost 50 days on RR10.
        'plain': 'The bank does not permit this type of transaction — either '
                 'on our account or on the payee\'s.',
        'action': 'Ask FNB which transaction types are allowed, and on which '
                  'of the two accounts the restriction sits.',
        'ours': 'unknown',
    },
    'DUPL': {
        'plain': 'The bank has already seen this exact instruction.',
        'action': 'STOP and confirm with FNB whether the original was paid '
                  'before resending.',
        'ours': 'yes',
    },
    'NARR': {
        'plain': 'The bank gave a free-text reason rather than a standard code.',
        'action': "Read the bank's own wording alongside this code — it carries "
                  'the real reason.',
        'ours': 'unknown',
    },
}

# Group-level statuses, so a batch is never left in a state nobody explains.
# ACWC is here because it was previously unhandled: FNB's specification (V-03,
# section 2.2) says it fires when the bank changes something about an accepted
# payment, and gives an auto-bumped execution date as the example — a routine
# event that used to leave a batch sitting in "Submitted" for good.
KNOWN_GROUP_STATUS: dict[str, str] = {
    'RJCT': 'The bank rejected it. No money moved.',
    'ACWC': 'The bank accepted it but changed something — most often it moved '
            'the payment date to the next working day. The money WILL go; '
            'check the date it settled on.',
    'PART': 'The bank took some of the payments in this batch and rejected '
            'others. Each one has to be checked individually.',
    'ACSC': 'Settled. The money has left the account.',
    'ACCC': 'Settled. The money has left the account.',
    'ACSP': 'Accepted and in progress at the bank.',
    'ACCP': 'Accepted by the bank, not yet settled.',
}

# additionalInformation is the bank's own free text. On 2026-08-19 it read
# "INVALID CHARACTER SET", but nothing stops FNB putting a payee name or an
# account number in it, and it is not ours to police. So it is never forwarded
# to a model: only a value that matches one of these known ISO 20022 field
# names is passed as a hint, and everything else is dropped. Redacting free
# text would be a mitigation; not sending it removes the risk (/fabe panel,
# OpenAI C5, 2026-08-20).
_ALLOWED_FIELD_HINTS = frozenset({
    'creditor', 'creditorAccount', 'creditorAgent', 'debtor', 'debtorAccount',
    'debtorAgent', 'endToEndId', 'messageId', 'paymentInformationId',
    'requestedExecutionDate', 'remittanceInformationUnstructured',
    'amount', 'currency', 'branchId', 'accountNumber', 'accountType',
    'paymentTypeInformationServiceLevelCode', 'controlSum',
})


def field_hint(raw: str) -> str:
    """The bank's field reference, but only when it is a field NAME we know.

    Anything else - prose, a value, an account number - is dropped rather than
    redacted, so free text has no path to an external model at all.
    """
    candidate = (raw or '').strip()
    return candidate if candidate in _ALLOWED_FIELD_HINTS else ''


_AI_SYSTEM = (
    'You explain bank payment reject codes to a Botswana insurance finance '
    'team. You are given an ISO 20022 status reason code and, at most, the '
    'NAME of the message field involved. You never receive payment values and '
    'must never invent any. Reply with exactly two lines:\n'
    'MEANING: one sentence, plain English, no jargon and no code names.\n'
    'ACTION: one sentence saying what the finance team should do next.\n'
    'If you do not recognise the code, say so plainly in the MEANING line '
    'rather than guessing.'
)


def _blank() -> dict:
    return {'plain': '', 'action': '', 'ours': 'unknown', 'source': 'none'}


def explain_reason(code: str, *, field: str = '', use_ai: bool = True) -> dict:
    """Explain one reject reason code.

    `field` is the bank's field reference. It is passed to a model only if it is
    a recognised ISO 20022 field name; see `field_hint`.

    Returns {'plain', 'action', 'ours', 'source'} where `source` is 'table' for
    a known code, 'ai' for a model reading, or 'none' when neither could answer.
    Never raises: this runs inside the batch-status poll, and a payment reject
    must still be recorded if the AI box is unreachable (an explanation is a
    convenience, the reject is the fact).
    """
    key = (code or '').strip().upper()
    if not key:
        return _blank()

    known = KNOWN_REASONS.get(key)
    if known:
        return {**known, 'source': 'table'}

    if not use_ai:
        return _blank()

    # `reason` is bank-controlled too. The argument for not forwarding
    # additionalInformation applies here as well, so a value that is not
    # code-shaped never reaches a model — dropped, not redacted. ISO 20022
    # reason codes are short alphanumerics.
    if not _CODE_SHAPE.fullmatch(key):
        log.warning('fnb reject explain: reason %r is not code-shaped; '
                    'not sending to AI', key[:40])
        return _blank()

    # A code's meaning does not change, and this runs inside the 5-minute batch
    # sweep as well as the operator's Refresh button. Without the cache, one
    # unknown code on a long-lived ACKNOWLEDGED batch re-runs the whole engine
    # chain every 5 minutes for up to 30 days, and rewrites failure_reason with
    # a slightly different sentence each time. fnb/ai_health.py caches for the
    # same reason.
    cache_key = f'{_AI_CACHE_PREFIX}{key}'
    try:
        from django.core.cache import cache
        cached = cache.get(cache_key)
    except Exception:                                          # noqa: BLE001
        cache, cached = None, None
    if cached is not None:
        return cached

    # Only the code, plus a field NAME from the allow-list. Never a value and
    # never the bank's free text.
    hint = field_hint(field)
    prompt = f'Reject code: {key}'
    if hint:
        prompt += f'\nMessage field involved: {hint}'
    prompt += '\n\nExplain it.'

    try:
        from core.ai_assist import is_safe_for_ai, reasoning_complete
        safety = is_safe_for_ai(prompt)
        if not safety.safe:
            # Should be impossible — a code and a field name carry no PII — so
            # if it ever fires, something unexpected reached this prompt and it
            # must not be sent.
            log.warning('fnb reject explain: prompt failed the PII gate for '
                        'code %s; not sending to AI', key)
            return _blank()
        out = reasoning_complete(
            safety.redacted_text, system_prompt=_AI_SYSTEM, max_tokens=180,
            feature='fnb_reject_explain',
        )
    except Exception as exc:                                   # noqa: BLE001
        # One clause, deliberately: DeepSeekUnavailable is imported inside this
        # try, so naming it in an `except` could itself raise UnboundLocalError
        # if the import failed — which would escape a function documented as
        # never raising. Both cases want the same outcome anyway.
        log.warning('fnb reject explain: failed for %s (%s: %s)',
                    key, type(exc).__name__, exc)
        return _blank()

    plain, action = '', ''
    for line in (out or '').splitlines():
        stripped = line.strip()
        if stripped.upper().startswith('MEANING:'):
            plain = stripped.split(':', 1)[1].strip()
        elif stripped.upper().startswith('ACTION:'):
            action = stripped.split(':', 1)[1].strip()
    if not plain:
        return _blank()
    result = {'plain': plain, 'action': action, 'ours': 'unknown',
              'source': 'ai'}
    if cache is not None:
        try:
            cache.set(cache_key, result, _AI_CACHE_TTL)
        except Exception:                                      # noqa: BLE001
            pass
    return result


def describe_rejection(group_status: str, reasons: list[dict], *,
                       use_ai: bool = True) -> str:
    """One human-readable paragraph for a batch the bank did not simply accept.

    `reasons` is the parsed statusReasonInformation list — dicts carrying
    'reason' and, where the bank supplied it, 'additionalInformation'.

    The returned text is appended to FNBBatchSubmission.failure_reason, so it
    shows up wherever that already displays. An AI reading is labelled as such;
    an unlabelled line always came from the table.
    """
    parts: list[str] = []

    status_line = KNOWN_GROUP_STATUS.get((group_status or '').strip().upper())
    if status_line:
        parts.append(status_line)

    seen: set[str] = set()
    for r in reasons or []:
        code = (r.get('reason') or '').strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        # additionalInformation is the bank's own wording; it is the field name
        # or a short note, never a payment value.
        field = (r.get('additionalInformation') or '').strip()
        exp = explain_reason(code, field=field, use_ai=use_ai)
        if not exp['plain']:
            parts.append(f'{code}: no explanation available for this code — '
                         'ask FNB what it means.')
            continue
        line = f'{code}: {exp["plain"]}'
        if exp['action']:
            line += f' What to do: {exp["action"]}'
        if exp['ours'] == 'yes':
            line += ' (This one is ours to fix.)'
        elif exp['ours'] == 'no':
            line += " (This one is on the bank's or the payee's side.)"
        if exp['source'] == 'ai':
            line += ' [AI reading — not from our verified list, confirm with FNB.]'
        parts.append(line)

    return ' '.join(parts).strip()
