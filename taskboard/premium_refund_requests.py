"""Raise the Omni payment request for ONE approved Graphite premium refund.

Extracted from `import_graphite_refunds` on 2026-09-11 because a second caller
appeared: the live Graphite -> Omni hand-off (`customer_refunds`), which until
now raised only the FNB money leg and so never showed on the Premium refunds tab
the CFO authorises from. Two implementations of "what a premium refund request
looks like" would drift, and the drift would be invisible — one refund rendering
two different covering tables depending on which pipe carried it.

So the shape lives here, once. The overnight importer and the hand-off both call
`raise_premium_refund_request`, and the UNIQUE `graphite_ref` means whichever one
gets there first wins and the other skips: a refund can never become two requests.
"""
from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from taskboard.models import OmniTask, PaymentRequest
from taskboard.narration_templates import PaymentNarrationType, build_defaults

#: Premium refunds belong to the licensed insurer.
ENTITY = 'ADIC'

#: Who the request is entered by. The CFO's instruction: "it will be entered by
#: Keetile, and I will be approving it" — so Keetile is the maker of record and
#: the request lands awaiting the CFO, not awaiting finance sign-off, because
#: Keetile IS the finance approver who signed the refund off in Graphite.
ENTERED_BY_EMAIL = 'kmokhendo@alphadirect.co.bw'


class PremiumRefundRequestError(RuntimeError):
    """Raised when a premium refund request cannot be built or saved."""


def dec2(v) -> Decimal:
    try:
        return Decimal(str(v or 0)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0.00')


def entered_by_user():
    """Keetile, the maker of record. None if she has no Omni login."""
    from django.contrib.auth.models import User
    return User.objects.filter(email__iexact=ENTERED_BY_EMAIL).first()


def raise_premium_refund_request(r: dict, entered_by, *, origin: str = 'overnight'
                                 ) -> PaymentRequest:
    """Create the PENDING_CFO payment request for one approved refund.

    `r` carries graphite_ref / policy_number / customer_name / refund_amount /
    currency / reason. `origin` only changes the wording of the summary, never a
    figure — 'overnight' for the midnight importer, 'direct' for the live
    Graphite hand-off.

    Raises PremiumRefundRequestError if there is no CFO account or no free
    reference number. Lets IntegrityError on graphite_ref escape, so the caller
    can treat "already raised" as the expected, harmless outcome it is.
    """
    from taskboard.payment_views import (_cfo_user, _next_ref, _render_html,
                                         _render_plaintext, _task_title)

    cfo = _cfo_user()
    if cfo is None:
        raise PremiumRefundRequestError(
            'No CFO / approver account is configured, so a refund cannot be '
            'sent for authorisation. Nothing was raised.')
    ref_g = (r.get('graphite_ref') or '').strip()
    amount = dec2(r.get('refund_amount'))
    policy = str(r.get('policy_number') or '').strip()
    # The customer's name is the payee on a premium refund, so it belongs on
    # the request the CFO reads. It stays inside Omni — never in a log line,
    # never in an email subject, never sent to an external model.
    payee = str(r.get('customer_name') or policy or 'Policyholder').strip()
    reason = str(r.get('reason') or '').strip()

    # The exact shape _norm_lines produces — description / gl_code / ref /
    # amount. _render_html indexes gl_code and ref directly, so a line built
    # with different keys raises KeyError at render, not at validation.
    line = {
        'description': f'Premium refund {ref_g} — policy {policy}',
        'gl_code': '',          # Finance codes the refund when it is paid
        'ref': policy,
        'amount': amount,       # Decimal for the renderer; stringified below
    }
    # Finance, 2026-08-20: "Client Refunds: ALPHA DIRECT REFUND + policy
    # number" and, for our own reference, "REFUND + policy number +
    # initials". The create screen refuses this category, so these two
    # callers are the only places that format can be set.
    _wording = build_defaults(
        payment_type=PaymentNarrationType.CLIENT_REFUND,
        policy_number=policy, account_name=payee)
    how_it_got_here = (
        'Raised automatically from Graphite overnight — nobody re-typed it, '
        'so the figures match the refund record exactly.'
        if origin == 'overnight' else
        'Came straight across from the Graphite refund platform the moment it '
        'was approved — nobody re-typed it, so the figures match the refund '
        'record exactly.')
    pr_data = {
        'entity': ENTITY,
        'category': PaymentRequest.Category.PREMIUM_REFUND,
        'currency': str(r.get('currency') or 'BWP'),
        'subject': f'Premium refund — {policy} — {ref_g}',
        'payee': payee[:191],
        'line_items': [line],
        'total': amount,
        'opening_balance': Decimal('0.00'),
        'graphite_ref': ref_g,
        'bank_payment_type': PaymentNarrationType.CLIENT_REFUND,
        'bank_narration': _wording['narration'],
        'bank_our_reference': _wording['our_reference'],
        'inputter': entered_by.get_full_name() or entered_by.username,
        # _render_html indexes these directly, so every key it touches must be
        # present even when empty. Alpha Direct's own bank details are not on a
        # premium refund — the money goes OUT to a policyholder, and their
        # account sits in Graphite behind the encryption there, not here.
        'verifier': '',
        'account_name': '',
        'account_number': '',
        'bank_name': '',
        'summary': (
            f'Premium refund of {r.get("currency") or "BWP"} {amount} for policy '
            f'{policy}, approved in the Graphite refund platform as {ref_g}. '
            f'Reason recorded there: {reason or "not stated"}. '
            f'{how_it_got_here} Awaiting CFO authorisation.'),
    }
    # _render_html formats the amount with :,.2f so it needs the Decimal, but
    # line_items is a JSONField and Decimal is not JSON-serialisable — so the
    # stored copy is stringified, exactly as the create endpoint does it.
    for _attempt in range(6):
        # The ref has to exist before the render — the covering table prints it.
        pr_data['ref'] = _next_ref(ENTITY)
        html = _render_html(pr_data)
        save_data = {**pr_data,
                     'line_items': [{**ln, 'amount': str(ln['amount'])}
                                    for ln in pr_data['line_items']]}
        try:
            with transaction.atomic():
                # The ONLY route to PAID is completing the payment request's
                # linked task — the pay controls on the drawer, the bulk queue
                # and the last-line duplicate check all hang off `task`. A
                # request raised with task=None sits at PENDING_CFO for ever
                # and the CFO's only available action is to cancel it. So this
                # creates the same CFO authorisation task the manual sign-off
                # creates, and links it.
                task = OmniTask.objects.create(
                    assigner=entered_by, assignee=cfo,
                    title=_task_title(ENTITY, PaymentRequest.Category.PREMIUM_REFUND,
                                      pr_data['currency'], amount),
                    body=(pr_data['summary'] + '\n\n'
                          + _render_plaintext(pr_data))[:5000],
                    priority=OmniTask.Priority.HIGH,
                    status=OmniTask.Status.PENDING,
                    # Refunds are money owed back to a policyholder;
                    # authorisation is due the next working day.
                    due_at=timezone.now() + datetime.timedelta(days=1),
                    source='payment_request',
                )
                return PaymentRequest.objects.create(
                    created_by=entered_by,
                    status=PaymentRequest.Status.PENDING_CFO,
                    formatted_html=html,
                    task=task,
                    decision_notes='Approved in the Graphite refund platform.',
                    **save_data)
        except IntegrityError as exc:
            # Two different collisions share this exception: a duplicate ref
            # (retry with the next number) and a duplicate graphite_ref (give
            # up, the refund is already raised). Telling them apart matters —
            # retrying the second would loop six times and hide the real state.
            if 'graphite_refund' in str(exc) or 'graphite_ref' in str(exc):
                raise
            continue
    raise PremiumRefundRequestError(
        f'could not allocate a unique payment-request number for {ref_g} '
        f'after 6 attempts — nothing was raised for it.')
