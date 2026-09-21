"""API for the weekly failed-debits report and the recipient lists Finance keep.

CFO 2026-09-11: "Rose or Keetile or any accountant can put the email ids of the
people it should send automatically." So the gate is ``CanViewFinancials`` — the
same one that already lets accountants and bookkeepers into finance data — and
not the tighter administrator gate. Widening it later is easy; explaining to
Rose why the screen she was told to use rejects her is not.

The recipient endpoints are keyed by report slug, so the same screen serves the
weekly chase list and the existing daily monitoring reports whose distribution
lists were, until now, a Python dictionary that needed a deploy to change.

The preview endpoint exists so nobody has to wait until Monday to see what the
email will contain.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from core.permissions import CanViewFinancials
from realpay import failed_debits
from reporting.models import ReportRecipient

#: Slugs a caller may point this screen at. An allowlist, not free text: without
#: it a typo would create a recipient list for a report that does not exist,
#: which looks saved on screen and silently never sends.
ALLOWED_SLUGS = {
    'failed-debits-weekly': 'Failed debits — weekly chase list (Monday 07:30)',
    'failed-debits':        'Failed debits — daily monitoring summary',
    'payment-status':       'Payment status — daily monitoring summary',
    # B1, Bokani Makosha. Listed here so Finance can fill the list in on the
    # screen they already use. Without this line the report would install, the
    # cron would fire, the command would correctly refuse to send to nobody,
    # and it would never send once — which is exactly how the failed-debits
    # report went live on 11 September and delivered nothing.
    'weekly-claims-update': 'Weekly claims update (Monday 07:00)',
}
DEFAULT_SLUG = 'failed-debits-weekly'


def _ser(r: ReportRecipient) -> dict:
    return {
        'id': str(r.id),
        'report_slug': r.report_slug,
        'email': r.email,
        'name': r.name,
        'kind': r.kind,
        'active': r.active,
        'added_by': ((r.added_by.get_full_name() or r.added_by.username)
                     if r.added_by else ''),
        'created_at': r.created_at.isoformat() if r.created_at else None,
    }


def _slug(request) -> str:
    return (request.data.get('report_slug')
            or request.query_params.get('report_slug')
            or DEFAULT_SLUG)


@api_view(['GET', 'POST'])
@permission_classes([CanViewFinancials])
def report_recipients(request):
    """GET the list for a report; POST to add an address to it."""
    slug = _slug(request)
    if slug not in ALLOWED_SLUGS:
        return Response({'detail': f'Unknown report "{slug}".'},
                        status=status.HTTP_400_BAD_REQUEST)

    if request.method == 'GET':
        rows = ReportRecipient.objects.filter(report_slug=slug)
        return Response({
            'report_slug': slug,
            'report_label': ALLOWED_SLUGS[slug],
            'reports': [{'slug': s, 'label': l} for s, l in ALLOWED_SLUGS.items()],
            'results': [_ser(r) for r in rows],
        })

    email = (request.data.get('email') or '').strip()
    name = (request.data.get('name') or '').strip()[:120]
    kind = (request.data.get('kind') or ReportRecipient.Kind.TO).strip()

    if not email:
        return Response({'detail': 'An email address is required.'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        validate_email(email)
    except ValidationError:
        # Caught at the screen rather than at send time: a typo found on Monday
        # morning is a week of the report not reaching somebody who believed
        # they were on it.
        return Response({'detail': f'"{email}" is not a valid email address.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if kind not in dict(ReportRecipient.Kind.choices):
        return Response({'detail': 'kind must be "to" or "cc".'},
                        status=status.HTTP_400_BAD_REQUEST)

    # Refuse an address Omni will never actually send to. Without this the row
    # is saved, the screen shows it as "Receiving", and send_html_with_cfo_cc
    # strips it on the way out — so the person is told they are on the list and
    # never gets a single email. That is exactly the 2026-08-10 incident where
    # the CFO asked for Arun on a message, the send reported success, and he
    # simply was not on it. Reachable from a form is worse than reachable from
    # code, because nobody reviews a form entry.
    from django.conf import settings
    from core.notifications import _NEVER_CC
    never = {(a or '').strip().lower()
             for a in getattr(settings, 'NEVER_CC_EMAILS', None) or _NEVER_CC}
    if email.lower() in never:
        return Response(
            {'detail': f'Omni never sends automated mail to {email}. '
                       f'Adding it here would show as "Receiving" while the '
                       f'email was silently dropped, so it is refused.'},
            status=status.HTTP_400_BAD_REQUEST)

    existing = ReportRecipient.objects.filter(
        report_slug=slug, email_key=email.lower()).first()
    if existing:
        # Re-adding somebody who was switched off is what "add" means to the
        # person doing it, so do that — rather than refusing with a duplicate
        # error they have no way to resolve from the screen.
        existing.active = True
        existing.kind = kind
        if name:
            existing.name = name
        existing.save()
        return Response(_ser(existing), status=status.HTTP_200_OK)

    row = ReportRecipient.objects.create(
        report_slug=slug, email=email, name=name, kind=kind, active=True,
        added_by=request.user if request.user.is_authenticated else None)
    return Response(_ser(row), status=status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
@permission_classes([CanViewFinancials])
def report_recipient_detail(request, recipient_id):
    """PATCH to switch on/off or change to/cc. DELETE switches off — it does not
    erase: who received a financial report is worth keeping."""
    row = ReportRecipient.objects.filter(id=recipient_id).first()
    if not row:
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'DELETE':
        row.active = False
        row.save()
        return Response(_ser(row))

    if 'active' in request.data:
        row.active = bool(request.data['active'])
    if 'kind' in request.data:
        kind = (request.data.get('kind') or '').strip()
        if kind not in dict(ReportRecipient.Kind.choices):
            return Response({'detail': 'kind must be "to" or "cc".'},
                            status=status.HTTP_400_BAD_REQUEST)
        row.kind = kind
    if 'name' in request.data:
        row.name = (request.data.get('name') or '').strip()[:120]
    row.save()
    return Response(_ser(row))


@api_view(['GET'])
@permission_classes([CanViewFinancials])
def failed_debits_preview(request):
    """What Monday's email will contain, as of right now."""
    try:
        days = max(1, min(90, int(request.query_params.get('days')
                                  or failed_debits.DEFAULT_DAYS)))
    except (TypeError, ValueError):
        days = failed_debits.DEFAULT_DAYS

    try:
        rows = failed_debits.failed_rows(days)
    except failed_debits.GraphiteUnavailable:
        # "Could not check" is shown as exactly that. A page that renders an
        # empty table on an outage reads as "a clean week".
        return Response({
            'available': False,
            'detail': 'The live link to Graphite is not available right now, '
                      'so this could not be checked.',
        })

    # THE SAME GUARD THE MONDAY JOB USES. Without it this page renders a green
    # "No failed debits in the last 7 days" while the job itself is refusing to
    # send because not one debit result came back — a false all-clear on money
    # owed, on screen, which is the exact thing the job refuses to put in an
    # email. Caught by LOOKING at the QC screenshot after deploy, not by a test:
    # every test here mocks the data source, so all of them passed.
    try:
        health = failed_debits.book_is_reporting(days)
    except failed_debits.GraphiteUnavailable:
        # If the health check itself cannot run, the honest answer is "we do
        # not know" — NOT "all clear". Defaulting to reporting=True here would
        # have reproduced, one level down, the exact false all-clear this guard
        # exists to stop. Caught by the review panel; the first version of this
        # fix had that fallback.
        return Response({
            'available': False,
            'detail': 'We could not check whether the debit results have come '
                      'back, so this cannot say whether anything failed.',
        })
    if not health['reporting']:
        return Response({
            'available': False,
            'detail': f"No result has come back for any of the "
                      f"{health['no_outcome']} commercial and domestic debits "
                      f"due in this period, so we cannot tell whether any "
                      f"failed. This is not a clean week — the failure feed "
                      f"from RealPay stopped on 3 June 2026 and has to be "
                      f"restored at source. Nothing will be emailed until it is.",
        })

    summary = failed_debits.summarise(rows)
    start, end = failed_debits.window(days)
    route = ReportRecipient.route_for(DEFAULT_SLUG) or {'to': [], 'cc': []}
    return Response({
        'available': True,
        'days': days,
        'start': start.isoformat(),
        'end': end.isoformat(),
        'count': summary['count'],
        'amount': str(summary['amount']),
        'non_active': summary['non_active'],
        'repeat': summary['repeat'],
        'by_agent': [{'agent': a['agent'], 'count': a['count'],
                      'amount': str(a['amount'])} for a in summary['by_agent']],
        'recipients_to': route['to'],
        'recipients_cc': route['cc'],
        'rows': [{**r, 'amount': str(r['amount']), 'premium': str(r['premium'])}
                 for r in rows[:200]],
    })
