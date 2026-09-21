"""Let the exception committee correct a branch code — and nothing else.

WHY THIS EXISTS. The branch-code control (PAY-BANK-05, 17-Sep-2026) finds a
missing or malformed branch code and sends the request to the committee. Then
the committee could see the problem and had no way to fix it: `branch_code` is
not in `payment_amend.REQUEST_FIELDS`, and `AMENDABLE_STATUSES` deliberately
excludes EXCEPTION. So the control detected the fault that caused ten AC08
rejects and BWP 677,284.93, and the only way out was to cancel the request and
raise it again from scratch. A control nobody can act on is half a control.

CFO, 18-Sep-2026, on the open items: *"why dont we fix it"*.

WHY IT IS A SEPARATE, NARROW ENDPOINT rather than widening `amend`.
`PaymentRequest.AMENDABLE_STATUSES` leaves EXCEPTION out ON PURPOSE, and the
reason written there is a good one:

    "a request sitting with the committee is being decided by three named
     people on the pack in front of them, and editing it under their feet would
     have them signing something else."

That reasoning holds for the amount, the payee, the lines — anything a signature
is about. It does NOT hold for the branch code, and the difference is the whole
justification for this module:

  * the branch code is the one field the committee was ASKED to look at;
  * it cannot move money to a different person — the ACCOUNT NUMBER identifies
    the payee, the branch code only says which branch holds that account, so a
    corrected branch cannot redirect a payment the way a changed account can
    (that remains PAY-BANK-01, untouched, and is not correctable here);
  * leaving it wrong guarantees the bank rejects the payment.

So: one field, one control, three of the existing safeguards kept — a named
actor, an immutable before/after log, and the shared rule re-run on the new
value so a second bad code is refused with the same plain sentence.

🔴 IT CANNOT BLOCK A PAYMENT. Refusing a correction leaves the request exactly
where it was — with the committee, still decidable. Nothing here dead-ends
anything (CFO 17-Sep-2026: *"you will never block a payment, if there is a
blocker the exception committe kicks in"*).

OMNI MOVES NO MONEY. This edits a workflow record. Money leaves at FNB, on the
CFO's own two-factor.
"""
from __future__ import annotations

import logging

from django.db import transaction

from fnb.destination_bank import _fold, _tokens, branch_code_problem, is_fnb_botswana
from payroll.bank_codes import derive_bank_name
from taskboard.models import PaymentRequest, PaymentRequestChange
from taskboard.payment_amend import AmendError, actor_bits

log = logging.getLogger(__name__)

#: The control this correction answers. Only a request carrying it may be
#: corrected — this is not a general edit door that happens to be narrow today.
CONTROL = 'PAY-BANK-05'

#: The one field. Named as a constant so a later "just add account_number while
#: we are here" has to argue with this module's docstring first.
FIELD = 'branch_code'


#: Every bank the branch-code table can name, and how to recognise that bank in
#: a free-text name a person typed. Keyed off `payroll.bank_codes
#: .BANK_BY_SORT_PREFIX` — the SAME list the code owner is derived from, so the
#: two can never drift.
#:
#: 🔴 The first version of this check used `fnb.destination_bank
#: ._OTHER_BANK_TOKENS`, which is a list of words that mean "not FNB" — a
#: different job. Four banks the code table names outright (Bank Gaborone,
#: First Capital, State Bank of India, Bank of Botswana) have no word in it, so
#: they fell through to "one side unrecognised, allow" and a Bank Gaborone
#: payee accepted an Absa branch code. The redirection door the guard was
#: written to close was still open for four banks (Fable 5.1, 18-Sep-2026).
#:
#: Multi-word names are matched as PHRASES on the folded string, because
#: 'gaborone' alone must not claim 'FNB Gaborone', which is an FNB branch.
_BANK_SIGNATURES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    # (canonical bank, single-word tokens, multi-word phrases)
    ('Bank Gaborone',                  (),               ('bank gaborone',)),
    ('First Capital Bank Botswana',    (),               ('first capital',)),
    ('State Bank of India (Botswana)', ('sbi',),         ('state bank of india',)),
    ('Bank of Botswana',               (),               ('bank of botswana',)),
    ('Bank of Baroda (Botswana)',      ('baroda',),      ()),
    ('Standard Chartered Bank Botswana', ('chartered',), ('standard chartered',)),
    ('Access Bank Botswana',           ('access',),      ('access bank',)),
    # Absa Botswana was Barclays; a request still spelt 'Barclays' is the same
    # bank, and refusing it would dead-end a legitimate correction.
    ('Absa Bank Botswana',             ('absa', 'barclays'), ()),
    ('Stanbic Bank Botswana',          ('stanbic',),     ()),
)


def _canonical_bank(name: str) -> str:
    """The bank a free-text name refers to, or '' when it cannot be placed.

    Another bank named in the string WINS over the FNB words, because
    'Bank Gaborone First National Bank' is three live payment requests and it
    is Bank Gaborone.
    """
    folded = _fold(name)
    if not folded.strip():
        return ''
    toks = set(_tokens(name))
    for canonical, words, phrases in _BANK_SIGNATURES:
        if any(ph in folded for ph in phrases) or (toks & set(words)):
            return canonical
    if is_fnb_botswana(name):
        return 'First National Bank Botswana'
    return ''


def _same_bank(code_owner: str, payee_bank: str) -> bool:
    """Is the bank a branch code belongs to the same bank the payee uses?

    Both sides are resolved to a canonical bank off the SAME table the code
    owner comes from. UNKNOWN on either side resolves to "cannot say" and is
    ALLOWED: the table names ten banks and Botswana has more (BSB, BBS,
    BancABC), so refusing what we cannot place would dead-end those payees, and
    this module may never dead-end anyone. What it catches is the dangerous
    case — both names placed, and they disagree.
    """
    if not (payee_bank or '').strip():
        return True                       # nothing recorded to contradict
    owner = _canonical_bank(code_owner)
    payee = _canonical_bank(payee_bank)
    if not owner or not payee:
        return True                       # one side unplaceable — cannot say
    return owner == payee


def may_correct(user, pr: PaymentRequest) -> bool:
    """Who may correct a branch code.

    The committee (they are the ones holding the request), the finance
    approvers, and the CFO. Deliberately NOT the raiser: the whole point of the
    exception is that someone other than the person who typed it looks at the
    bank details.
    """
    from taskboard.payment_views import (
        _is_cfo, _is_committee_member, _is_first_approver,
    )
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    # ABSOLUTE: the person who raised it never corrects its bank details, not
    # even a superuser. This read `and not _is_cfo(user)`, and _is_cfo() is true
    # for ANY superuser — and Pramod keeps Super Admin — so a superuser-raiser
    # could correct his own bank field while the docstring promised he could
    # not (Fable 5.1, 18-Sep-2026).
    if pr.created_by_id and user.id == pr.created_by_id:
        return False
    return bool(_is_committee_member(user) or _is_first_approver(user)
                or _is_cfo(user) or getattr(user, 'is_superuser', False))


def correct_branch_code(pr: PaymentRequest, user, *, branch_code: str) -> dict:
    """Write a corrected branch code. Returns what changed.

    Raises AmendError with a message meant for the person on the screen.
    """
    new = (branch_code or '').strip()
    old = (pr.branch_code or '').strip()

    if not may_correct(user, pr):
        raise AmendError(
            'Only the exception committee, a finance approver or the CFO may '
            'correct a branch code — and never the person who raised the '
            'request. That is the whole point of the check.',
            control=CONTROL, status=403)

    if pr.status not in (PaymentRequest.Status.EXCEPTION,
                         PaymentRequest.Status.PENDING_FINANCE,
                         PaymentRequest.Status.DRAFT):
        raise AmendError(
            f'This request is {pr.get_status_display().lower()}, so the branch '
            f'code can no longer be corrected here. If it has already gone to '
            f'the bank, type it into FNB by hand with the right code, or cancel '
            f'and raise it again.',
            control=CONTROL, status=409)

    if new == old:
        raise AmendError('That is the branch code already on the request — '
                         'nothing to change.', control=CONTROL, status=400)

    # The SAME rule the capture screen and the batch builder use. A correction
    # that is still wrong must be refused HERE, with the reason, rather than
    # accepted and discovered again by the bank.
    problem = branch_code_problem(pr.bank_name or '', new)
    if problem:
        raise AmendError(
            f'That branch code still will not work: {problem}',
            control=CONTROL, status=400)

    # 🔴 THE CODE CHOOSES THE BANK. I built this module believing a branch code
    # "cannot redirect money to a different person, because the ACCOUNT NUMBER
    # identifies the payee". THAT IS FALSE IN BOTSWANA, and Omni's own
    # payroll/bank_codes.py says so in its first paragraph: the FIRST TWO DIGITS
    # of a six-digit sort code identify the BANK (28 = FNB, 06 = Stanbic,
    # 29 = Absa, 20 = Bank Gaborone). Account numbers are per bank, so the same
    # number routed to a different bank can be a different account holder.
    # Changing the branch code therefore CAN move the money, which makes an
    # unchecked correction a redirection door — the exact fraud PAY-BANK-01
    # exists to stop. Caught by Fable 5.1 attacking that judgement, 18-Sep-2026.
    #
    # So: the corrected code must belong to the bank the payee actually banks
    # with. An unrecognised prefix is ALLOWED (a new bank must be payable) but
    # the answer says which bank we could not place, so a person can see it.
    owner = derive_bank_name(new) or ''
    if owner and not _same_bank(owner, pr.bank_name or ''):
        raise AmendError(
            f'"{new}" is a {owner} branch code, but this payee banks with '
            f'{(pr.bank_name or "").strip() or "a bank that is not recorded"}. '
            f'The first two digits of a branch code choose the BANK, so this '
            f'would send the money somewhere else. Check it against the '
            f"supplier's own bank document.",
            control=CONTROL, status=400)

    with transaction.atomic():
        locked = (PaymentRequest.objects.select_for_update()
                  .filter(pk=pr.pk).first())
        if locked is None:
            raise AmendError('That payment request no longer exists.',
                             control=CONTROL, status=404)
        # Re-read inside the lock: two committee members on the same request at
        # the same time must not both write, or the log shows a change from a
        # value that was never there.
        # Status inside the lock too, not just the value: finance sign-off
        # commits PENDING_CFO and the FNB load then reads branch_code, so a
        # correction landing in that gap would change what goes to the bank
        # after the pack was signed (Fable 5.1, 18-Sep-2026).
        if locked.status not in (PaymentRequest.Status.EXCEPTION,
                                 PaymentRequest.Status.PENDING_FINANCE,
                                 PaymentRequest.Status.DRAFT):
            raise AmendError(
                f'This request moved to {locked.get_status_display().lower()} '
                f'while you were typing, so it can no longer be corrected here.',
                control=CONTROL, status=409)
        old = (locked.branch_code or '').strip()
        if old == new:
            raise AmendError('Somebody else corrected it to the same value a '
                             'moment ago — nothing to change.',
                             control=CONTROL, status=409)
        locked.branch_code = new[:20]
        locked.save(update_fields=['branch_code', 'updated_at'])

        name, dept = actor_bits(user)
        PaymentRequestChange.objects.create(
            request=locked, action=PaymentRequestChange.Action.AMEND,
            field=FIELD, value_before=old or '(blank)', value_after=new,
            reason=(f'Branch code corrected for the bank (PAY-BANK-05). '
                    f'{new} is a '
                    f'{_canonical_bank(derive_bank_name(new) or "") or "bank we could not place"} '
                    f'code; the payee is recorded as '
                    f'"{(locked.bank_name or "").strip() or "(no bank recorded)"}".'),
            actor=user, actor_name=name, actor_department=dept)

    log.info('payment request %s: branch code corrected %r -> %r by %s',
             locked.ref, old, new, getattr(user, 'username', '?'))
    pr.branch_code = locked.branch_code
    return {
        'ref': locked.ref,
        'field': FIELD,
        'before': old or '',
        'after': new,
        'by': name,
        'bank_for_code': owner or '(could not place this branch code to a bank)',
        'note': ('The branch code is corrected and recorded. The request stays '
                 'with the committee — this changes the bank details only, not '
                 'the decision.'),
    }
