"""
Management command: fnb_three_way_reconcile

Bring a payment request's status into line with what the bank actually did.

CFO, 21-Sep-2026, on the 23 disagreements in that morning's report: *"Can you
kindly clear these exceptions and start afresh since we started doing good job
yesterday only"*, and then explicitly, when asked what "clear" meant: **change
the Omni statuses to match the bank** — not hide them behind a date.

    python manage.py fnb_three_way_reconcile              # dry run, prints only
    python manage.py fnb_three_way_reconcile --apply      # writes

🔴 THIS MOVES NO MONEY. Omni has never moved money and does not here: money
leaves at FNB, authorised by a person with two-factor. Everything below is a
workflow record catching up with a fact the bank already settled days ago.

WHY IT IS SAFE TO WRITE, WHICH THE READ-ONLY CHECK DELIBERATELY WAS NOT
-----------------------------------------------------------------------
`three_way_check` reports and never writes, because the automatic version of
this would have rewritten a human's decision the moment it shipped. That
objection is answered here by a person: the CFO asked for it, by name, on a
list he was looking at. It is still written to be reversible and to leave a
trail — the before value is in the AuditLog row and in `decision_notes`, and
nothing is deleted.

THE RULES, AND WHY EACH TARGET STATUS
-------------------------------------
* bank SETTLED, Omni cancelled / rejected / pending → **paid**.
  The money left the account. Marking it paid is simply true, and it is also
  the safer of the two terminal states: `taskboard.payment_duplicates` treats a
  CANCELLED request as dead, so a genuinely-paid request left cancelled drops
  out of the duplicate-payment control and the same invoice can be raised and
  paid a second time (the same reasoning as `_mark_paid_from_bank`).

* bank REJECTED, Omni paid → **cancelled**, with a reason and a change-log row.
  The bank threw it out with a reason code; no money moved; Omni must stop
  claiming it was paid.

  🔴 THE COST OF THIS ONE, AND WHAT WAS DONE ABOUT IT. Both terminal states —
  `cancelled` and `rejected` — are in `payment_duplicates.DEAD_STATUSES`, so
  clearing these USED TO take the money out of PAY-DUP-01's sight: pay the
  supplier by hand, let the invoice be re-raised, and nothing clashed. That is
  the shape of the P399,000 that was paid twice.

  The CFO was shown that trade-off in those words on 21-Sep-2026 and chose
  "mark them cleared and move on". His call, made with the cost in front of
  him — not an oversight, and not to be quietly re-decided by a later session.

  He then asked for the gap itself to be closed, and it was, the same day:
  `payment_duplicates.still_owed()` keeps a CLOSED request inside the control
  when the bank never paid it. So these sixteen are cleared AND still watched;
  a re-raise clashes and routes to the exception committee. All sixteen are
  also listed by name and amount in the Word record on his Desktop, because a
  control is not a substitute for somebody knowing.

* bank UNCONFIRMED ('submitted' / 'acknowledged' / 'pending' / 'unknown'),
  Omni paid → **left exactly as it is**.
  The bank has not said. The money MAY have moved. Guessing either way here is
  how you get a double payment, so this command refuses to guess and says so.
"""
from __future__ import annotations

import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from fnb.three_way_check import FINDINGS, contradictions

#: finding key -> the status the bank's own answer says it should be.
#: 'paid_but_batch_unconfirmed' is absent ON PURPOSE — see the module docstring.
RULES = {
    'cancelled_but_settled': 'paid',
    'settled_but_not_paid': 'paid',
    'paid_but_batch_failed': 'cancelled',
}

#: A person has already adjudicated this payment and their ruling is the reason
#: it looks contradictory. Measured on the first live run, 21-Sep-2026: two of
#: the 23 carried exactly this, and the generic "bank settled -> paid" rule
#: overturned both.
#:
#:   PAY/ADIC/2026/09/14/0003  "Duplicate of PAY/ADIC/2026/09/11/0011 - PAYE
#:                              only paid once. CFO 2026-09-17."
#:   PAY/ADIC/2026/09/09/0010  "Duplicate of Petty Cash reimbursement
#:                              PCR-2026-000003 ... Cleared to prevent a second
#:                              bank payment to the custodian."
#:
#: Both were deliberately NOT paid so the money could not go out twice. Marking
#: them paid because a batch settled made Omni state that BWP 132,042 of PAYE
#: and BWP 13,764.51 of petty cash had each been paid twice. The module this
#: command sits beside warned about precisely that and shipped read-only for
#: the reason: "rewriting a human's decision because a machine thinks it knows
#: better is how you lose the audit trail."
_HUMAN_RULING = re.compile(
    r'duplicate|paid (?:only )?once|second (?:bank )?payment|already paid',
    re.I)

#: The same words with a NOT in front of them mean the opposite, and blocking
#: those is not the safe side: a genuinely-settled payment left on `cancelled`
#: drops out of PAY-DUP-01 exactly like the ones this command was written for.
_NOT_A_RULING = re.compile(
    r'not a duplicate|no duplicate|none found|not already paid|n[o\']t.{0,12}duplicate',
    re.I)

#: Whose decision this was. Stamped on every row so the trail names a person
#: rather than reading as an anonymous machine edit — the change IS his, and an
#: auditor asks who first.
ON_WHOSE_INSTRUCTION = (
    'On the CFO\'s instruction, 21-Sep-2026: "clear these exceptions and start '
    'afresh".')


#: A line this command wrote itself is NOT a human ruling, or a second run would
#: read its own history and refuse to correct anything ever again. The marker
#: has to be part of this command's own sentence STRUCTURE, not the instruction
#: quoted inside it: `ON_WHOSE_INSTRUCTION` is dated and changes the next time
#: somebody runs this for a different instruction, and the moment it does every
#: previously stamped line stops being excluded. Those lines carry the bank's
#: own words — and FNB reject code AM05 reads "the bank treated this as a
#: DUPLICATE of a payment it has already received" — so the command would start
#: matching its own output and freeze.
_OUR_OWN_STAMP = ('status corrected from', ON_WHOSE_INSTRUCTION)


#: `decision_notes` is a TextField the serializer caps at 2000 characters.
_NOTES_MAX = 2000


def _append_note(existing: str, line: str) -> str:
    """Add `line` to `existing` without losing either end to the cap.

    A plain `f'{existing}\\n{line}'[:2000]` truncates from the RIGHT, which cuts
    off the line being added — leaving a request whose status moved with no
    record of why, while the old text it was supposed to preserve survives
    intact. Exactly backwards. When the two together do not fit, the NEW line
    is kept whole and the OLDEST text is trimmed, marked with a leading ellipsis
    so it is visible that something was dropped.
    """
    combined = f'{existing}\n{line}'.strip()
    if len(combined) <= _NOTES_MAX:
        return combined
    room = _NOTES_MAX - len(line) - 2      # the '…' and the newline
    if room <= 0:
        return line[:_NOTES_MAX]
    return f'…{existing[-room:]}\n{line}'


def _prior_ruling(ref: str) -> str:
    """The line a person already wrote about this payment being a duplicate, or
    ''.

    A contradiction can BE somebody's decision. Where it is, the bank batch is
    not new information and this command has nothing to correct — it would only
    undo the protection that ruling put there.
    """
    from taskboard.models import PaymentRequest

    notes = (PaymentRequest.objects.filter(ref=ref)
             .values_list('decision_notes', flat=True).first() or '')
    for line in notes.splitlines():
        if any(m in line for m in _OUR_OWN_STAMP):
            continue
        if _HUMAN_RULING.search(line) and not _NOT_A_RULING.search(line):
            return line.strip()
    return ''


def _reconcile_one(ref: str, target: str, *, expected: str, note: str,
                   user=None) -> str | None:
    """Move one request off `expected` and onto `target`. Returns the status it
    came from, or None if there was nothing to do.

    Re-reads and LOCKS the row inside the transaction and re-checks it is STILL
    on `expected` — the status this row was listed under when the report was
    built, outside the transaction. Matching on `expected` rather than merely
    "not already target" is the difference between idempotent and trampling: a
    person who moved this payment while the command was running would otherwise
    have their decision overwritten by a plan made before they made it. Same
    guard as `_mark_paid_from_bank`.
    """
    from core.models import AuditLog
    from taskboard.models import OmniTask, PaymentRequest, PaymentRequestChange
    from taskboard.payment_amend import _log

    with transaction.atomic():
        locked = (PaymentRequest.objects.select_for_update()
                  .filter(ref=ref, status=expected).first())
        if locked is None:
            return None

        was = locked.status
        was_notes = locked.decision_notes or ''
        line = (f'{timezone.localtime():%d %b %Y %H:%M} — status corrected from '
                f'"{was}" to "{target}" to match what the bank did. '
                f'{ON_WHOSE_INSTRUCTION} {note}')

        locked.status = target
        # APPEND. A person wrote what is already in here — a finance approver's
        # reason for rejecting, a note on how it was settled — and a machine
        # correction that assigns over it destroys the very record this command
        # exists to preserve.
        locked.decision_notes = _append_note(was_notes, line)
        fields = ['status', 'decision_notes', 'updated_at']

        if target == 'cancelled':
            # The model is explicit that a cancel is retained WITH its account:
            # "an auditor asks this first". A cancel with no reason is the one
            # shape this table is designed never to hold.
            locked.cancelled_reason = (
                f'The bank rejected this payment, so the money never moved. '
                f'{ON_WHOSE_INSTRUCTION} {note}')[:2000]
            locked.cancelled_at = timezone.now()
            locked.cancelled_by = user
            fields += ['cancelled_reason', 'cancelled_at', 'cancelled_by']

        locked.save(update_fields=fields)

        if target == 'cancelled':
            # Through the SAME helper every other amend and cancel uses, so the
            # attribution line is READ off the user like the model demands
            # ("pulled from the logged-in user, never typed") and carries the
            # department. A hand-built actor_name here was a typed attribution
            # — the one thing this table is documented never to hold.
            _log(locked, action=PaymentRequestChange.Action.CANCEL_REQUEST,
                 user=user, field='status', before=was, after=target,
                 reason=locked.cancelled_reason)

        # Both targets are terminal, so a task still sitting open on this
        # request is asking somebody to action a payment that is finished. The
        # original "a paid request stayed pending_cfo for ever" bug in reverse.
        task = locked.task
        if task and task.status not in (OmniTask.Status.DONE,
                                        OmniTask.Status.CANCELLED):
            task.status = OmniTask.Status.CANCELLED
            task.completed_at = timezone.now()
            task.save(update_fields=['status', 'completed_at', 'updated_at'])

        AuditLog.objects.create(
            table_name='taskboard.PaymentRequest',
            record_id=str(locked.id),
            action=AuditLog.Action.UPDATE,
            # Every field this overwrote, not just the headline one — an
            # old_values that names one of two changed fields is a trail that
            # cannot put the row back.
            old_values={'status': was, 'decision_notes': was_notes},
            new_values={'status': target, 'ref': locked.ref},
            user=user,
            description=(f'Payment request {locked.ref}: {was} -> {target}, '
                         f'reconciled to the bank. {ON_WHOSE_INSTRUCTION}')[:500],
        )
        return was


class Command(BaseCommand):
    help = ('Correct payment request statuses that disagree with what the bank '
            'did. Dry run unless --apply is given. Moves no money.')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually write. Without this it only prints.')
        parser.add_argument('--as-user', default='',
                            help='Username or email of the person whose decision '
                                 'this is. Stamped on every row.')

    def handle(self, *args, **opts):
        from django.contrib.auth.models import User

        apply = opts['apply']
        user = None
        # A write with nobody behind it is not an anonymous write — the screen
        # renders `cancelled_by=None` as "Cancelled by a removed account",
        # which would be a false statement on all 16 rows. A correction this
        # size is somebody's decision; it has to name them.
        if apply and not opts['as_user']:
            raise CommandError(
                '--apply needs --as-user <username or email>: every row records '
                'whose decision this was.')
        if opts['as_user']:
            user = (User.objects.filter(username=opts['as_user']).first()
                    or User.objects.filter(email__iexact=opts['as_user']).first())
            if user is None:
                raise CommandError(f'No user matches "{opts["as_user"]}".')
            self.stdout.write(f'Acting for: {user.get_full_name() or user.get_username()}')

        found = contradictions()

        changed = skipped = 0
        for key, rows in found.items():
            if not rows:
                continue
            headline, _meaning = FINDINGS[key]
            target = RULES.get(key)

            self.stdout.write('')
            if target is None:
                self.stdout.write(self.style.WARNING(
                    f'{headline} — {len(rows)}: LEFT ALONE. The bank has not '
                    f'said whether the money moved, so nothing here can be '
                    f'corrected without guessing.'))
                skipped += len(rows)
                continue

            self.stdout.write(self.style.WARNING(
                f'{headline} — {len(rows)} -> "{target}"'))

            for r in rows:
                prior = _prior_ruling(r['ref'])
                if prior:
                    skipped += 1
                    self.stdout.write(self.style.WARNING(
                        f'    {r["ref"]}: LEFT ALONE — a person already ruled on '
                        f'this one. "{prior[:120]}"'))
                    continue
                # The bank's own text is raw and may carry newlines. An
                # embedded one splits the stamped line in two and leaves the
                # second half — FNB's words, unstamped — sitting in the notes
                # where this guard reads it back as somebody's ruling.
                note = ' '.join(
                    (f'Bank batch {r["batch_key"]} is "{r["batch_status"]}". '
                     f'{(r.get("failure_reason") or "")[:200]}').split())
                if not apply:
                    self.stdout.write(
                        f'    would set {r["ref"]}: {r["omni_status"]} -> {target}'
                        f'   BWP {float(r["total"] or 0):,.2f}  ({r["age_days"]}d)')
                    changed += 1
                    continue
                was = _reconcile_one(r['ref'], target,
                                     expected=r['omni_status'], note=note,
                                     user=user)
                if was is None:
                    skipped += 1
                    self.stdout.write(
                        f'    {r["ref"]}: no longer on "{r["omni_status"]}" — '
                        f'left alone')
                else:
                    changed += 1
                    self.stdout.write(self.style.SUCCESS(
                        f'    {r["ref"]}: {was} -> {target}'
                        f'   BWP {float(r["total"] or 0):,.2f}'))

        self.stdout.write('')
        verb = 'changed' if apply else 'would change'
        self.stdout.write(self.style.SUCCESS(
            f'{changed} payment request(s) {verb}; {skipped} left alone.'))
        if not apply:
            self.stdout.write('Dry run — nothing was written. Re-run with --apply.')
        self.stdout.write('No money moved. Omni does not move money.')
