"""
company_cards/api_views.py — upload a card spend, code it, load the statement.

Upload is deliberately tiny: card, date, amount, one line of "what for", and a
photo. No approval, no GL account, no 50-word essay — every extra field is a
receipt that never gets uploaded.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.http import FileResponse
from django.utils import timezone
from rest_framework import status as http
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core import notifications
from core.models import AuditLog
from company_cards import services as svc
from company_cards.models import (
    EXPLANATION_WORDS, CardSpend, CardStatement, CardStatementLine, CompanyCard,
    normalise_description)

# Receipts come straight off a phone camera, so bound size and type — same caps
# as the refund flow (hris.expense_api._check_uploads).
_MAX_UPLOAD = 10 * 1024 * 1024
_OK_TYPES = ('image/', 'application/pdf')


def _spend_dict(s: CardSpend) -> dict:
    return {
        'id': str(s.id),
        'card': str(s.card_id),
        'card_label': str(s.card),
        'spent_on': s.spent_on.isoformat(),
        'merchant': s.merchant,
        'amount': str(s.amount),
        'currency': s.currency,
        'what_for': s.what_for,
        'has_receipt': s.has_receipt,
        'receipt_url': (f'/api/v1/company-cards/spends/{s.id}/receipt/'
                        if s.has_receipt else None),
        'is_explained': s.is_explained,
        'word_count': s.word_count,
        'status': s.status,
        'status_label': s.get_status_display(),
        'gl_account_label': (f'{s.gl_account.code} — {s.gl_account.name}'
                             if s.gl_account_id else None),
        'uploaded_by': s.uploaded_by.get_full_name() or s.uploaded_by.username,
        'finance_note': s.finance_note,
        'created_at': s.created_at.isoformat(),
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_cards(request):
    """The cards this person may post against — drives the upload screen."""
    if not svc.user_is_cardholder(request.user) and not svc.user_is_finance(request.user):
        return Response({'detail': 'You do not hold a company card.'},
                        status=http.HTTP_403_FORBIDDEN)
    return Response({'cards': [
        {'id': str(c.id), 'label': c.label, 'last4': c.last4,
         'currency': c.currency,
         'holder': c.holder.get_full_name() or c.holder.username}
        for c in svc.cards_for(request.user)]})


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def card_spends(request):
    """GET  — my spends (Finance sees everything).
    POST — record one, with the receipt. Multipart:
           card, spent_on, amount, what_for, merchant (optional), receipt (file)
    """
    is_fin = svc.user_is_finance(request.user)
    if not is_fin and not svc.user_is_cardholder(request.user):
        return Response({'detail': 'You do not hold a company card.'},
                        status=http.HTTP_403_FORBIDDEN)

    if request.method == 'GET':
        qs = (CardSpend.objects
              .select_related('card', 'uploaded_by', 'gl_account')
              .order_by('-spent_on', '-created_at'))
        if not is_fin:
            qs = qs.filter(card__holder=request.user)
        state = (request.query_params.get('status') or '').strip()
        if state in dict(CardSpend.Status.choices):
            qs = qs.filter(status=state)
        rows = [_spend_dict(s) for s in qs[:300]]
        return Response({'count': len(rows), 'is_finance': is_fin, 'spends': rows})

    d = request.data
    card = CompanyCard.objects.filter(pk=d.get('card'), is_active=True).first()
    if card is None:
        return Response({'detail': 'Pick a card.'}, status=http.HTTP_400_BAD_REQUEST)
    # Post only against your OWN card, unless you are Finance fixing a mis-file.
    if card.holder_id != request.user.id and not is_fin:
        return Response({'detail': 'That is not your card.'},
                        status=http.HTTP_403_FORBIDDEN)

    what_for = (d.get('what_for') or '').strip()
    if len(what_for) < 3:
        return Response({'detail': 'Say in a few words what it was for — '
                                   'Finance codes the account from this.'},
                        status=http.HTTP_400_BAD_REQUEST)
    try:
        amount = Decimal(str(d.get('amount') or '0'))
        if not amount.is_finite():
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        return Response({'detail': 'Amount is not a valid number.'},
                        status=http.HTTP_400_BAD_REQUEST)
    if amount <= 0:
        return Response({'detail': 'Amount must be more than zero.'},
                        status=http.HTTP_400_BAD_REQUEST)

    raw_date = str(d.get('spent_on') or '')[:10]
    try:
        import datetime as _dt
        spent_on = _dt.date.fromisoformat(raw_date)
    except ValueError:
        return Response({'detail': 'Pick the date of the spend.'},
                        status=http.HTTP_400_BAD_REQUEST)
    if spent_on > timezone.localdate():
        return Response({'detail': 'That date is in the future.'},
                        status=http.HTTP_400_BAD_REQUEST)

    receipt = request.FILES.get('receipt')
    if receipt is not None:
        if receipt.size > _MAX_UPLOAD:
            return Response({'detail': f'"{receipt.name}" is too large — 10 MB max.'},
                            status=http.HTTP_400_BAD_REQUEST)
        ctype = (getattr(receipt, 'content_type', '') or '').lower()
        if ctype and not ctype.startswith(_OK_TYPES):
            return Response({'detail': f'"{receipt.name}" is not a photo or PDF.'},
                            status=http.HTTP_400_BAD_REQUEST)

    spend = CardSpend.objects.create(
        card=card, uploaded_by=request.user, spent_on=spent_on,
        merchant=(d.get('merchant') or '').strip()[:120],
        amount=amount, currency=card.currency, what_for=what_for[:1000],
    )
    if receipt is not None:
        spend.receipt.save(receipt.name, receipt, save=True)

    # A receipt that answers a known statement gap should clear it at once, and
    # the holder's bills-to-explain task must reflect this new item (it may need
    # 25 words still, or may have just cleared the last gap).
    svc.rematch_card(card)
    notifications.refresh_card_task(card.holder)

    msg = ('Saved. Finance will code it — nothing else for you to do.'
           if spend.is_explained else
           'Photo saved. Add a few words on what it was for when you have a '
           'moment — it will wait for you under "Bills to explain".')
    return Response({**_spend_dict(spend), 'message': msg},
                    status=http.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def code_spend(request, pk):
    """Finance sets the GL account. Body: {gl_account, note?, queried?}"""
    if not svc.user_is_finance(request.user):
        return Response({'detail': 'Finance only.'}, status=http.HTTP_403_FORBIDDEN)
    spend = CardSpend.objects.filter(pk=pk).first()
    if spend is None:
        return Response({'detail': 'Not found.'}, status=http.HTTP_404_NOT_FOUND)

    if request.data.get('queried'):
        spend.status = CardSpend.Status.QUERIED
        spend.finance_note = (request.data.get('note') or '').strip()[:2000]
        spend.save(update_fields=['status', 'finance_note', 'updated_at'])
        # Reach the cardholder: email the question + raise/refresh their task.
        notifications.notify_card_query(spend, request.user)
        return Response(_spend_dict(spend))

    from ledger.models import Account
    acct = Account.objects.filter(pk=request.data.get('gl_account'),
                                  is_active=True).first()
    if acct is None:
        return Response({'detail': 'Pick a valid expense account.'},
                        status=http.HTTP_400_BAD_REQUEST)
    spend.gl_account = acct
    spend.status = CardSpend.Status.CODED
    spend.coded_by = request.user
    spend.coded_at = timezone.now()
    if request.data.get('note'):
        spend.finance_note = str(request.data['note'])[:2000]
    spend.save(update_fields=['gl_account', 'status', 'coded_by', 'coded_at',
                              'finance_note', 'updated_at'])
    # Coding closes it for the exec — their task may now be lighter or gone.
    notifications.refresh_card_task(spend.card.holder)
    return Response(_spend_dict(spend))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_statement(request):
    """Finance loads a month's card statement, then Omni names what has no
    receipt. Multipart: card, year, month, file (csv / xlsx).

    Reuses banking.statement_files, which sniffs the header row — Finance does
    NOT have to pre-configure a format per bank (bug 713d6218).
    """
    if not svc.user_is_finance(request.user):
        return Response({'detail': 'Finance only.'}, status=http.HTTP_403_FORBIDDEN)
    card = CompanyCard.objects.filter(pk=request.data.get('card')).first()
    if card is None:
        return Response({'detail': 'Pick a card.'}, status=http.HTTP_400_BAD_REQUEST)
    try:
        year, month = int(request.data.get('year')), int(request.data.get('month'))
        if not (1 <= month <= 12):
            raise ValueError
    except (TypeError, ValueError):
        return Response({'detail': 'Pick the statement month.'},
                        status=http.HTTP_400_BAD_REQUEST)
    upload = request.FILES.get('file')
    if upload is None:
        return Response({'detail': 'Attach the statement file.'},
                        status=http.HTTP_400_BAD_REQUEST)

    # Same FILE, already loaded? Say so BEFORE processing it a second time
    # (Laone Thebe 2026-09-11). Overridable with force=1 — a bank can legitimately
    # re-issue an identical file, and the line-level check below still guards the
    # data either way.
    digest = hashlib.sha256(upload.read()).hexdigest()
    upload.seek(0)
    forced = str(request.data.get('force') or '').strip().lower() in ('1', 'true', 'yes')
    twin = (CardStatement.objects.filter(card=card, file_hash=digest)
            .exclude(period_year=year, period_month=month).first())
    if twin is not None and not forced:
        return Response(
            {'detail': f'This exact file has already been loaded for '
                       f'{twin.period_year}-{twin.period_month:02d}. Review that '
                       f'statement first, or choose "load it anyway".',
             'duplicate_statement': str(twin.id),
             'duplicate_period': f'{twin.period_year}-{twin.period_month:02d}'},
            status=http.HTTP_409_CONFLICT)

    from company_cards.statement_import import parse_card_statement
    try:
        rows = parse_card_statement(upload)
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=http.HTTP_400_BAD_REQUEST)
    if not rows:
        return Response({'detail': 'No transactions found in that file.'},
                        status=http.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        stmt, _ = CardStatement.objects.update_or_create(
            card=card, period_year=year, period_month=month,
            defaults={'uploaded_by': request.user,
                      'source_name': upload.name[:200],
                      'file_hash': digest})
        stmt.lines.filter(matched_spend__isnull=True, waived=False).delete()

        # DUPLICATE TRANSACTIONS: date + description + amount, on this CARD,
        # across EVERY statement of it — not just this one. That is what stops a
        # reallocated, re-uploaded or re-processed statement importing the same
        # charge twice (Laone Thebe 2026-09-11). All THREE must match; two out of
        # three is a different transaction and must still come in.
        seen = {
            (l.posted_on, normalise_description(l.description), l.amount)
            for l in CardStatementLine.objects.filter(statement__card=card)
            .only('posted_on', 'description', 'amount')
        }
        fresh, dupes = [], []
        for r in rows:
            key = (r['date'], normalise_description(r['description']), r['amount'])
            if key in seen:
                dupes.append(r)
                continue
            seen.add(key)
            fresh.append(CardStatementLine(
                statement=stmt, posted_on=r['date'], amount=r['amount'],
                description=r['description'][:250]))
        CardStatementLine.objects.bulk_create(fresh)

    result = svc.match_statement(stmt)
    AuditLog.objects.create(
        table_name='company_cards_cardstatement', record_id=str(stmt.id),
        action=AuditLog.Action.CREATE, user=request.user,
        new_values={'card': str(card), 'period': f'{year}-{month:02d}',
                    'file': upload.name[:200], 'rows': len(rows),
                    'new': len(fresh), 'duplicates': len(dupes)},
        description=(f'Card statement loaded for {year}-{month:02d}: '
                     f'{len(fresh)} new, {len(dupes)} duplicate(s) skipped.'))
    return Response({'statement': str(stmt.id),
                     'lines': len(rows),
                     'total_transactions': len(rows),
                     'new_transactions': len(fresh),
                     'duplicate_transactions': len(dupes),
                     'duplicates': [{'posted_on': d['date'].isoformat(),
                                     'description': d['description'][:250],
                                     'amount': str(d['amount'])}
                                    for d in dupes[:100]],
                     **result,
                     'message': (f'{len(fresh)} new, {len(dupes)} already loaded '
                                 f'(skipped). {result["matched"]} matched to a '
                                 f'receipt, {result["unmatched"]} still with no '
                                 f'receipt.')},
                    status=http.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reallocate_statement(request, pk):
    """Move a statement to the month it actually belongs to. Body: {year, month}.

    The file and its lines are untouched — nothing is re-imported, so this can
    never create a duplicate transaction (Laone Thebe 2026-09-11). Only one
    statement per card per month is kept, so a clash is refused with a reason,
    never silently overwritten.
    """
    if not svc.user_is_finance(request.user):
        return Response({'detail': 'Finance only.'}, status=http.HTTP_403_FORBIDDEN)
    stmt = CardStatement.objects.select_related('card').filter(pk=pk).first()
    if stmt is None:
        return Response({'detail': 'Not found.'}, status=http.HTTP_404_NOT_FOUND)
    try:
        year, month = int(request.data.get('year')), int(request.data.get('month'))
        if not (1 <= month <= 12) or not (2000 <= year <= 2100):
            raise ValueError
    except (TypeError, ValueError):
        return Response({'detail': 'Pick the month to move it to.'},
                        status=http.HTTP_400_BAD_REQUEST)
    was = f'{stmt.period_year}-{stmt.period_month:02d}'
    if (year, month) == (stmt.period_year, stmt.period_month):
        return Response({'detail': f'It is already filed under {was}.'},
                        status=http.HTTP_400_BAD_REQUEST)
    clash = (CardStatement.objects
             .filter(card=stmt.card, period_year=year, period_month=month)
             .exclude(pk=stmt.pk).first())
    if clash is not None:
        return Response(
            {'detail': f'{stmt.card} already has a statement for '
                       f'{year}-{month:02d}. Delete that one first — only one '
                       f'statement per card per month is kept.'},
            status=http.HTTP_409_CONFLICT)

    stmt.period_year, stmt.period_month = year, month
    stmt.save(update_fields=['period_year', 'period_month', 'updated_at'])
    AuditLog.objects.create(
        table_name='company_cards_cardstatement', record_id=str(stmt.id),
        action=AuditLog.Action.UPDATE, user=request.user,
        old_values={'period': was},
        new_values={'period': f'{year}-{month:02d}'},
        description=f'Card statement moved from {was} to {year}-{month:02d}.')
    return Response({'id': str(stmt.id),
                     'period': f'{year}-{month:02d}',
                     'message': f'Moved from {was} to {year}-{month:02d}.'})


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_statement(request, pk):
    """Delete a statement loaded against the wrong card or month.

    Its statement LINES go with it — a line is only Omni's reading of the bank's
    file. The cardholders' RECEIPTS (CardSpend) are never touched: a receipt is
    evidence that stands whether or not the statement it answered is still
    loaded. How many lines had a receipt against them is reported back, so the
    deletion is a decision and not a surprise.
    """
    if not svc.user_is_finance(request.user):
        return Response({'detail': 'Finance only.'}, status=http.HTTP_403_FORBIDDEN)
    stmt = CardStatement.objects.select_related('card').filter(pk=pk).first()
    if stmt is None:
        return Response({'detail': 'Not found.'}, status=http.HTTP_404_NOT_FOUND)
    card = stmt.card
    period = f'{stmt.period_year}-{stmt.period_month:02d}'
    lines = stmt.lines.count()
    matched = stmt.lines.filter(matched_spend__isnull=False).count()
    with transaction.atomic():
        stmt.lines.all().delete()      # explicit — never leans on a CASCADE
        stmt.delete()
    AuditLog.objects.create(
        table_name='company_cards_cardstatement', record_id=str(pk),
        action=AuditLog.Action.DELETE, user=request.user,
        old_values={'card': str(card), 'period': period, 'lines': lines,
                    'matched_to_a_receipt': matched},
        description=(f'Card statement {card} {period} deleted '
                     f'({lines} line(s), {matched} had a receipt). '
                     f'No receipt was deleted.'))
    return Response({'deleted': str(pk), 'lines_removed': lines,
                     'receipts_kept': matched,
                     'message': (f'Statement for {period} deleted. '
                                 f'{lines} line(s) removed; no receipt was '
                                 f'deleted.')})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def statement_gaps(request, pk):
    """The point of the whole module: which transactions have no receipt."""
    if not svc.user_is_finance(request.user):
        return Response({'detail': 'Finance only.'}, status=http.HTTP_403_FORBIDDEN)
    stmt = CardStatement.objects.filter(pk=pk).select_related('card').first()
    if stmt is None:
        return Response({'detail': 'Not found.'}, status=http.HTTP_404_NOT_FOUND)
    gaps = svc.missing_receipts(stmt)
    return Response({
        'card': str(stmt.card),
        'period': f'{stmt.period_year}-{stmt.period_month:02d}',
        'total_lines': stmt.lines.count(),
        'missing_count': len(gaps),
        'missing': [{'id': str(l.id), 'posted_on': l.posted_on.isoformat(),
                     'description': l.description, 'amount': str(l.amount)}
                    for l in gaps],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def statements(request):
    """Every loaded statement with its outstanding count — the Finance list."""
    if not svc.user_is_finance(request.user):
        return Response({'detail': 'Finance only.'}, status=http.HTTP_403_FORBIDDEN)
    out = []
    for st in (CardStatement.objects.select_related('card', 'card__holder')
               .prefetch_related('lines')[:60]):
        lines = list(st.lines.all())
        out.append({
            'id': str(st.id),
            'card': str(st.card),
            'holder': st.card.holder.get_full_name() or st.card.holder.username,
            'period': f'{st.period_year}-{st.period_month:02d}',
            'total_lines': len(lines),
            'missing_count': sum(1 for l in lines if l.needs_receipt),
            'uploaded_at': st.created_at.isoformat(),
        })
    return Response({'count': len(out), 'statements': out})


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def card_register(request):
    """The card register. Finance sets the last-four digits and the label.

    GET returns every card; PATCH takes {id, last4?, label?, is_active?}.
    Only the last FOUR digits are ever stored (see CompanyCard).
    """
    if not svc.user_is_finance(request.user):
        return Response({'detail': 'Finance only.'}, status=http.HTTP_403_FORBIDDEN)

    if request.method == 'PATCH':
        card = CompanyCard.objects.filter(pk=request.data.get('id')).first()
        if card is None:
            return Response({'detail': 'Card not found.'}, status=http.HTTP_404_NOT_FOUND)
        fields = []
        if 'last4' in request.data:
            digits = ''.join(ch for ch in str(request.data['last4']) if ch.isdigit())
            if len(digits) != 4:
                return Response(
                    {'detail': 'Give exactly four digits — the LAST four only. '
                               'Omni never stores a full card number.'},
                    status=http.HTTP_400_BAD_REQUEST)
            card.last4 = digits
            fields.append('last4')
        if 'label' in request.data:
            label = str(request.data['label']).strip()[:60]
            if not label:
                return Response({'detail': 'The card needs a name.'},
                                status=http.HTTP_400_BAD_REQUEST)
            card.label = label
            fields.append('label')
        if 'is_active' in request.data:
            card.is_active = bool(request.data['is_active'])
            fields.append('is_active')
        if fields:
            card.save(update_fields=fields + ['updated_at'])

    return Response({'cards': [
        {'id': str(c.id), 'label': c.label, 'last4': c.last4,
         'currency': c.currency, 'is_active': c.is_active,
         'holder': c.holder.get_full_name() or c.holder.username,
         'spend_count': c.spends.count()}
        for c in CompanyCard.objects.select_related('holder').order_by('label')]})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def waive_line(request, pk):
    """Mark a statement line as needing no receipt (a card fee, a reversal)."""
    if not svc.user_is_finance(request.user):
        return Response({'detail': 'Finance only.'}, status=http.HTTP_403_FORBIDDEN)
    line = CardStatementLine.objects.filter(pk=pk).first()
    if line is None:
        return Response({'detail': 'Not found.'}, status=http.HTTP_404_NOT_FOUND)
    note = (request.data.get('note') or '').strip()
    if not note:
        return Response({'detail': 'Say why this one needs no receipt.'},
                        status=http.HTTP_400_BAD_REQUEST)
    line.waived = True
    line.waived_note = note[:200]
    line.save(update_fields=['waived', 'waived_note', 'updated_at'])
    return Response({'id': str(line.id), 'waived': True})


def _line_dict(l: CardStatementLine) -> dict:
    return {
        'id': str(l.id),
        'kind': 'line',
        'card': str(l.statement.card_id),
        'card_label': str(l.statement.card),
        'posted_on': l.posted_on.isoformat(),
        'description': l.description,
        'amount': str(l.amount),
    }


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def explain_spend(request, pk):
    """The cardholder (or Finance on their behalf) answers a query / completes a
    shoebox spend: a real explanation of >=EXPLANATION_WORDS words, and optionally
    the receipt. Multipart: what_for, receipt (optional). Clears a QUERIED flag,
    closes the bills-to-explain task, and re-matches the statement."""
    spend = CardSpend.objects.select_related('card', 'card__holder').filter(pk=pk).first()
    if spend is None:
        return Response({'detail': 'Not found.'}, status=http.HTTP_404_NOT_FOUND)
    is_fin = svc.user_is_finance(request.user)
    if spend.card.holder_id != request.user.id and not is_fin:
        return Response({'detail': 'That is not your card.'},
                        status=http.HTTP_403_FORBIDDEN)

    what_for = (request.data.get('what_for') or '').strip()
    if len(what_for.split()) < EXPLANATION_WORDS:
        return Response(
            {'detail': f'Please give at least {EXPLANATION_WORDS} words on what it '
                       f'was for — you can tap the mic and just say it.'},
            status=http.HTTP_400_BAD_REQUEST)

    receipt = request.FILES.get('receipt')
    if receipt is not None:
        if receipt.size > _MAX_UPLOAD:
            return Response({'detail': f'"{receipt.name}" is too large — 10 MB max.'},
                            status=http.HTTP_400_BAD_REQUEST)
        ctype = (getattr(receipt, 'content_type', '') or '').lower()
        if ctype and not ctype.startswith(_OK_TYPES):
            return Response({'detail': f'"{receipt.name}" is not a photo or PDF.'},
                            status=http.HTTP_400_BAD_REQUEST)

    spend.what_for = what_for[:1000]
    fields = ['what_for', 'updated_at']
    if spend.status == CardSpend.Status.QUERIED:
        spend.status = CardSpend.Status.UNCODED
        fields.append('status')
    spend.save(update_fields=fields)
    if receipt is not None:
        spend.receipt.save(receipt.name, receipt, save=True)

    svc.rematch_card(spend.card)
    notifications.refresh_card_task(spend.card.holder)
    return Response({**_spend_dict(spend), 'message': 'Thank you — Finance has it.'})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_open_items(request):
    """The cardholder's own 'bills to explain': queried or unexplained spends,
    plus statement lines on their cards with no receipt yet."""
    if not svc.user_is_cardholder(request.user) and not svc.user_is_finance(request.user):
        return Response({'detail': 'You do not hold a company card.'},
                        status=http.HTTP_403_FORBIDDEN)
    spends = [_spend_dict(s) for s in svc.open_spends_for(request.user)]
    lines = [_line_dict(l) for l in svc.open_lines_for(request.user)]
    return Response({'count': len(spends) + len(lines),
                     'spends': spends, 'lines': lines})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def nudge_holders(request):
    """Finance: nudge every cardholder who has something outstanding — the button
    that replaces the monthly chase email. One bundled message per holder."""
    if not svc.user_is_finance(request.user):
        return Response({'detail': 'Finance only.'}, status=http.HTTP_403_FORBIDDEN)
    out = []
    for h in svc.holders_with_open_items():
        n = notifications.nudge_card_holder(h, request.user)
        if n:
            out.append({'holder': h.get_full_name() or h.username, 'open': n})
    return Response({'nudged': len(out), 'holders': out,
                     'message': (f'Nudged {len(out)} cardholder(s).' if out
                                 else 'Nobody has anything outstanding. Sharp sharp!')})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def spend_receipt(request, pk):
    """Stream a spend's receipt for preview. Finance, or the cardholder who owns
    the card. Receipts can carry personal items, so it is never world-readable."""
    spend = CardSpend.objects.select_related('card').filter(pk=pk).first()
    if spend is None or not spend.has_receipt:
        return Response({'detail': 'Not found.'}, status=http.HTTP_404_NOT_FOUND)
    if spend.card.holder_id != request.user.id and not svc.user_is_finance(request.user):
        return Response({'detail': 'Not allowed.'}, status=http.HTTP_403_FORBIDDEN)
    return FileResponse(spend.receipt.open('rb'),
                        filename=spend.receipt.name.rsplit('/', 1)[-1])
