"""bonu/legal_bills.py — Legal bill capture, allocation to a client, and the cap.

Part 2 of Kelvin Kimani's spec (9 Sep 2026). The 80,000 per-client legal-spend
ceiling in Part 1 is only as good as the numbers feeding it, so this is where a
legal bill is entered and tied to the client whose matter it is for.

FIVE THINGS IT HAS TO GET RIGHT
------------------------------
1. **Allocation is through the MATTER, never a typed name.** A bill is logged
   against a claim, and a claim belongs to a client, so the bill rolls up on
   its own. The client is never stored on the bill: it is read through
   `allocation.case.client`, so there is one answer to "whose spend is this"
   and no second copy to drift.

2. **One bill can cover several matters.** A firm sending one invoice for three
   files must split across the three, not dump the total on one — that charges
   the wrong client. Hence a bill total plus allocations that must account for
   it exactly (`legal_rules.check_split`).

3. **A name alone never auto-commits money when there is any doubt.** Bills
   arrive carrying the client's name rather than our number. One exact match is
   tied automatically; two clients sharing a name, or a partial match, is
   offered to a person to pick; nothing matched goes to the unallocated
   exception list. Two clients can share a name and firms misspell.

4. **The system learns a firm spelling once.** When a person ties a name-only
   bill to a client, that spelling is kept as an alias, so the next bill from
   that firm for that client matches without asking again.

5. **A suspected duplicate is flagged before it is committed**, because the
   same bill entered twice would push a client toward the cap on money that was
   only ever owed once.

NO MONEY MOVES HERE. Capturing a bill, allocating it, and marking it paid are
workflow records in Omni. Every real payment is authorised by the CFO in the
FNB app with two-factor, and nothing in this module posts to the general ledger
or changes a reported figure.

MEMBER PRIVACY: the firm spelling of a client name is stored, because a bill
arriving with a name and no number is the problem being solved. The CFO decided
that member names may be stored and read inside BONU (11 Aug 2026, confirmed
18 Aug 2026 as data controller). They never leave Omni and never reach an
external model — the matching here is plain string work with no AI in it
(AD-POL-AI-GOV-001).

Access is the standard BONU gate (`bonu.views._deny`): finance, management and
the named BONU team, which is where the legal office and Patience Phesodi sit.
No new grant is created here.
"""
from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from bonu import legal_rules as rules
from bonu.member_identity import normalise
from bonu.models import (BonuMember, LawFirm, LegalBill, LegalBillAllocation,
                         LegalCase, LegalClientAlias, LegalSettings)
from bonu.views import _deny

#: How many members are pulled into memory for a name match. The roll is 9,000+
#: rows, so the shortlist is narrowed in the DATABASE on the words in the billed
#: name first; this cap stops a one-word name like "Moeng" dragging the whole
#: roll through Python.
#:
#: A FULL SHORTLIST IS NOT A UNIQUENESS ANSWER. An earlier version of this note
#: claimed truncation could only make the answer more cautious. That was wrong,
#: and Fable proved it at the /fabe gate on 9 Sep 2026: with two members called
#: "Thabo Moeng" and a common first name matching hundreds of rows, only ONE of
#: the twins survives the slice, the match reads `exact`, and money is
#: auto-allocated to whichever twin the database happened to return first.
#: So when the slice comes back FULL, an exact match is downgraded to ambiguous
#: and a person decides — the same rule as everywhere else here: a control must
#: never be satisfied by an answer that came from a fallback.
MATCH_SHORTLIST = 400


class FieldError(ValueError):
    """A value the user sent that we will not guess at."""


def _dec(raw, field):
    """One money parser for the whole module.

    Two parsers is how a guard and a total come to disagree — a typo read as
    nought by one and as money by the other (the same reason `bonu/legal.py`
    keeps exactly one). A bad amount is a 400 here, never a silent nought.
    """
    s = str(raw if raw is not None else '').strip()
    if s == '':
        raise FieldError(f'"{field}" is required.')
    s = s.replace(',', '').replace('P', '').replace('\xa0', '').replace(' ', '')
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    try:
        return Decimal(s)
    except (InvalidOperation, ArithmeticError):
        raise FieldError(f'"{field}" must be an amount — got "{raw}".')


def _date(raw, field, *, required=True):
    s = str(raw if raw is not None else '').strip()
    if not s:
        if required:
            raise FieldError(f'"{field}" is required.')
        return None
    try:
        return datetime.date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        raise FieldError(f'"{field}" is not a valid date — got "{raw}".')


# ---------------------------------------------------------------------------
# The cap: a client running total, computed and never stored
# ---------------------------------------------------------------------------

def client_spend(client_ids=None) -> dict:
    """{client_id: total legal spend} from the allocations, in one query.

    Summed from the allocations every time rather than kept as a number on the
    client, because a stored total drifts the moment a bill is corrected,
    re-allocated or rejected — and a cap total that is quietly wrong is worse
    than no cap at all.

    Counted at the BILLED stage, not paid, so the warning fires before the
    money goes out. A rejected bill counts for nothing; it was never a cost.
    """
    qs = (LegalBillAllocation.objects
          .filter(bill__stage__in=LegalBill.COUNTS_TOWARD_CAP,
                  case__client__isnull=False))
    if client_ids is not None:
        qs = qs.filter(case__client_id__in=list(client_ids))
    rows = qs.values('case__client_id').annotate(total=Sum('amount'))
    return {r['case__client_id']: (r['total'] or Decimal('0')) for r in rows}


def cap_state(total, settings=None) -> dict:
    """One client total turned into the tiered flag the screens draw."""
    s = settings or LegalSettings.solo()
    tier = rules.cap_tier(total, s.client_spend_amber, s.client_spend_cap)
    return {
        'total': str(total),
        'tier': tier,
        'label': rules.CAP_LABELS[tier],
        'amber_at': str(s.client_spend_amber),
        'cap': str(s.client_spend_cap),
        'headroom': str(s.client_spend_cap - total),
    }


def _client_json(member, total, settings):
    return {
        'id': str(member.pk),
        'membership_no': member.membership_no,
        'name': member.full_name,
        'district': member.district,
        'status': member.status,
        'status_label': member.get_status_display(),
        'on_current_list': member.is_on_current_list,
        'cap': cap_state(total, settings),
    }


# ---------------------------------------------------------------------------
# Finding a client from a NAME, not just a number
# ---------------------------------------------------------------------------

def _shortlist(billed_name):
    """Members worth comparing against a billed name, narrowed in the database."""
    words = [w for w in normalise(billed_name).split() if len(w) > 1]
    if not words:
        return []
    q = Q()
    for w in words:
        q |= Q(full_name__icontains=w)
    return list(BonuMember.objects.filter(q).exclude(full_name='')[:MATCH_SHORTLIST])


def match_billed_name(billed_name) -> dict:
    """Resolve a billed client name. Alias first, then the membership roll.

    The alias table is checked first and is authoritative when it hits, because
    an alias only exists because a PERSON already resolved that exact spelling
    once. That is stronger evidence than any string comparison.

    An alias is matched on the SPELLING ALONE and not per firm: `alias_key` is
    unique across all clients by design, so one spelling can only ever point at
    one client and there is nothing for a firm to disambiguate. (This used to
    take a `firm` argument that was never read, while the wording claimed
    firm-specific learning — Fable, /fabe gate, 9 Sep 2026.)
    """
    key = normalise(billed_name or '')
    if not key:
        return {'outcome': rules.NONE, 'candidates': [], 'via': 'none'}

    alias = (LegalClientAlias.objects.select_related('client')
             .filter(alias_key=key).first())
    if alias is not None:
        return {'outcome': rules.EXACT, 'candidates': [alias.client], 'via': 'alias'}

    members = _shortlist(billed_name)
    result = rules.match_client_name(
        billed_name, [{'id': str(m.pk), 'name': m.full_name} for m in members])
    by_id = {str(m.pk): m for m in members}
    outcome = result['outcome']
    truncated = len(members) >= MATCH_SHORTLIST
    if truncated and outcome == rules.EXACT:
        # The slice was full, so there may be a second holder of this name that
        # we never looked at. One match out of a truncated sample is not
        # uniqueness — hand it to a person rather than auto-charging somebody.
        outcome = rules.AMBIGUOUS
    return {
        'outcome': outcome,
        'candidates': [by_id[m['id']] for m in result['matches'] if m['id'] in by_id],
        'via': 'name',
        'truncated': truncated,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def legal_client_search(request):
    """GET /api/v1/bonu/legal/clients/?q= — find a client by NAME or by number.

    The spec: today records are found by number, but bills arrive carrying
    mostly the client name. So it is one search with two ways in — an inputter
    holding only a name can find the client and their number.
    """
    denied = _deny(request)
    if denied is not None:
        return denied

    q = (request.query_params.get('q') or '').strip()
    if len(q) < 2:
        return Response({'clients': [], 'detail': 'Type at least two characters.'})

    settings = LegalSettings.solo()
    members = list(BonuMember.objects
                   .filter(Q(full_name__icontains=q) | Q(membership_no__icontains=q))
                   .order_by('full_name')[:50])

    # A name-shaped search also gets the match verdict, so the screen can say
    # "this is the one" or "these two share the name" rather than only listing.
    verdict = match_billed_name(q) if len(q.split()) >= 2 else {'outcome': rules.NONE,
                                                                'candidates': [], 'via': 'none'}
    ids = {m.pk for m in members} | {m.pk for m in verdict['candidates']}
    totals = client_spend(ids)
    known = {m.pk: m for m in members}
    for m in verdict['candidates']:
        known.setdefault(m.pk, m)

    return Response({
        'query': q,
        'clients': [_client_json(m, totals.get(m.pk, Decimal('0')), settings)
                    for m in known.values()],
        'match': {
            'outcome': verdict['outcome'],
            'via': verdict['via'],
            'ids': [str(m.pk) for m in verdict['candidates']],
        },
    })


# ---------------------------------------------------------------------------
# Bill capture
# ---------------------------------------------------------------------------

def _bill_json(b, totals=None, settings=None):
    allocs = list(b.allocations.all())
    data = {
        'id': str(b.pk),
        'source': b.source, 'source_label': b.get_source_display(),
        'firm': b.biller, 'firm_id': str(b.firm_id) if b.firm_id else '',
        'reference': b.reference,
        'bill_date': b.bill_date.isoformat() if b.bill_date else '',
        'received_on': b.received_on.isoformat() if b.received_on else '',
        'amount': str(b.amount),
        'discount': str(b.discount),
        'amount_paid': str(b.amount_paid),
        'paid_on': b.paid_on.isoformat() if b.paid_on else '',
        'outstanding': str(b.outstanding),
        'source_row': b.source_row,
        'stage': b.stage, 'stage_label': b.get_stage_display(),
        'allocation_state': b.allocation_state,
        'allocation_label': b.get_allocation_state_display(),
        'billed_client_name': b.billed_client_name,
        'note': b.note,
        'captured_by': b.captured_by_email,
        'allocations': [{
            'id': str(a.pk),
            'case_id': str(a.case_id),
            'case_ref': a.case.case_ref,
            'client_id': str(a.case.client_id) if a.case.client_id else '',
            'client': a.case.client.full_name if a.case.client_id else '',
            'amount': str(a.amount),
        } for a in allocs],
        'allocated_total': str(sum((a.amount for a in allocs), Decimal('0'))),
    }
    # Why a bill is sitting in the exception list, in words, so nobody has to
    # work it out from the fields.
    if b.allocation_state == LegalBill.Allocation.UNALLOCATED:
        if not allocs:
            data['why_unallocated'] = ('Not tied to a matter yet.'
                                       if not b.billed_client_name else
                                       f'Arrived for "{b.billed_client_name}" with no '
                                       f'single certain client.')
        else:
            missing = [a.case.case_ref for a in allocs if not a.case.client_id]
            data['why_unallocated'] = (
                'Tied to ' + ', '.join(missing) + ', but that matter has no client on the '
                'membership roll, so the spend cannot be totalled against anybody.'
            ) if missing else (
                # Belt and braces: with restate_bills_for_case in place this
                # should be unreachable, but rendering an empty list as
                # "Tied to , but that matter has no client" was a real symptom
                # of the drift, so it must not be able to come back.
                'Marked unallocated but every matter on it has a client — reopen and save '
                'the allocation to refresh it.')
    return data


def _active_firm(fid):
    from django.core.exceptions import ValidationError
    if not fid:
        return None
    try:
        return LawFirm.objects.filter(pk=fid, is_active=True).first()
    except (ValidationError, ValueError):
        return None


def restate_bills_for_case(case) -> int:
    """Recompute `allocation_state` on every bill touching this matter.

    `allocation_state` is STORED on the bill, but its answer depends on
    `case.client` — a field that lives on another row and can be changed from
    the claim screen. Without this, linking a client after the bill was
    captured left the bill saying "unallocated" while the cap board already
    counted its money: the board showed the spend in the client's total AND
    listed it as "in NO total above" at the same time. Unlinking left the
    mirror image, a bill still marked allocated whose money reaches nobody.

    Found by Fable at the /fabe gate on 9 Sep 2026 with two live probes.
    Returns how many bills were corrected.
    """
    bills = (LegalBill.objects
             .filter(allocations__case=case).distinct()
             .prefetch_related('allocations__case'))
    changed = 0
    for b in bills:
        state = _resolve_state(b, list(b.allocations.all()))
        if state != b.allocation_state:
            b.allocation_state = state
            b.save(update_fields=['allocation_state', 'updated_at'])
            changed += 1
    return changed


def cap_after_restating(case, settings=None) -> dict | None:
    """The client's cap standing once this matter's bills count, or None.

    Why this exists: the hard ceiling can only refuse a bill at the moment it
    is captured or allocated. A bill captured against a matter with NO client
    passes the ceiling untouched (there is no client to total), and finance
    then links the client afterwards while clearing the exceptions — so the
    money arrives in the client's total by a route the ceiling never sees.
    Fable flagged it at the /fabe gate on 9 Sep 2026.

    Refusing the LINK is the wrong answer: the bill would stay unallocated for
    ever and every cap total would understate, which is exactly the failure the
    exceptions list exists to end. So the link succeeds and the breach is
    REPORTED, loudly, on the response.
    """
    if case.client_id is None:
        return None
    s = settings or LegalSettings.solo()
    total = client_spend([case.client_id]).get(case.client_id, Decimal('0'))
    state = cap_state(total, s)
    return state if state['tier'] != 'clear' else None


def _resolve_state(bill, allocs) -> str:
    """A bill is ALLOCATED only when every share of it reaches a real client.

    Partly-tied is not allocated. If any matter on the bill has no client on
    the membership roll, that share cannot be totalled against anybody, so the
    bill stays an exception rather than appearing to be dealt with.
    """
    if not allocs:
        return LegalBill.Allocation.UNALLOCATED
    if any(a.case.client_id is None for a in allocs):
        return LegalBill.Allocation.UNALLOCATED
    return LegalBill.Allocation.ALLOCATED


def _cases_for(ids):
    """The matters named on a bill, with their client, or a FieldError."""
    from django.core.exceptions import ValidationError
    try:
        found = {str(c.pk): c for c in
                 LegalCase.objects.select_related('client').filter(pk__in=list(ids))}
    except (ValidationError, ValueError):
        raise FieldError('A matter reference on this bill is not a valid id.')
    missing = [i for i in ids if i not in found]
    if missing:
        raise FieldError('A matter on this bill is not on file.')
    return found


def _read_allocations(data):
    """[(case_id, amount)] off the request, or [] when none were given."""
    raw = data.get('allocations') or []
    if not isinstance(raw, list):
        raise FieldError('Allocations must be a list of matters and amounts.')
    out = []
    for i, row in enumerate(raw, start=1):
        if not isinstance(row, dict):
            raise FieldError(f'Allocation {i} is not a matter and an amount.')
        case_id = str(row.get('case_id') or '').strip()
        if not case_id:
            raise FieldError(f'Allocation {i} does not say which matter it is for.')
        out.append((case_id, _dec(row.get('amount'), f'allocation {i} amount')))
    if len({c for c, _ in out}) != len(out):
        raise FieldError('The same matter appears twice on this bill. Combine those lines.')
    return out


def _cap_breaches(allocs_by_case, cases, settings):
    """Which clients this bill would take past the cap, and by how much."""
    per_client = {}
    for case_id, amount in allocs_by_case:
        client_id = cases[case_id].client_id
        if client_id is not None:
            per_client[client_id] = per_client.get(client_id, Decimal('0')) + amount
    if not per_client:
        return []
    existing = client_spend(per_client.keys())
    out = []
    for client_id, added in per_client.items():
        before = existing.get(client_id, Decimal('0'))
        after = before + added
        tier = rules.cap_tier(after, settings.client_spend_amber, settings.client_spend_cap)
        if tier != 'clear':
            out.append({'client_id': str(client_id), 'before': str(before),
                        'after': str(after), 'tier': tier,
                        'cap': str(settings.client_spend_cap)})
    return out


# ---------------------------------------------------------------------------
# Reading a bill off the document instead of typing it
#
# Kelvin Kimani's spec left this open ("keyed in by hand, or uploaded and read
# off — if uploaded, this reuses the same document-reading capability scoped for
# supplier statements rather than being built fresh"). It is built on exactly
# that: `bonu.ingest.extract_text` and `bonu.ingest.guess_header`, the same two
# functions `read_supplier_invoice` uses.
#
# NO AI IS INVOLVED, and that is not a compromise — it is the whole shape of the
# problem. A LegalBill has no line items of its own: its detail is the
# ALLOCATION across OUR matters, which the firm's document cannot know. So the
# only things worth reading off the page are the bill number, the date and the
# total, and `guess_header` gets those with local regular expressions. The
# supplier reader needs a model only because it structures line items. Sending
# a legal bill to one would put a member's name in front of an external model
# for no gain at all (AD-POL-AI-GOV-001).
#
# THREE THINGS IT DELIBERATELY DOES NOT DO
# 1. **It does not save anything.** It returns a draft that pre-fills the form.
#    Every guard on the way in — the duplicate check, the split arithmetic, the
#    80,000 ceiling — still runs when the person submits, untouched. A reader
#    that wrote straight to the register would walk around all three.
# 2. **It does not fill in the client.** The same rule the supplier reader
#    follows: the reader fills the figures, a person attaches the client. The
#    cap is aggregated on a structured client link, so a guessed name is worse
#    than a blank one. The billed name still goes through `match_billed_name`
#    once a person types or confirms it.
# 3. **It does not fill in the firm.** That is picked from the panel, which the
#    person knows and the document may spell differently.
#
# It also does not return the extracted TEXT. Only the three values cross back,
# so the body of a legal bill — which names a member and often something
# sensitive about their life — is not shipped into a browser payload or a log.
# ---------------------------------------------------------------------------

#: The largest file worth reading. Same ceiling as the supplier reader.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def read_legal_bill(filename: str, blob: bytes) -> dict:
    """Read a legal bill and return values pre-filled for the capture form.

    Saves nothing. Returns `needs_manual` when there is no text to read, rather
    than returning empty figures that look like a successful read of a blank
    bill — a scan of a bill for 85,000 must never arrive as 0.
    """
    from bonu.ingest import extract_text, guess_header

    text, method, err = extract_text(filename, blob)
    if not (text or '').strip():
        scan = method in ('pdf-scanned', 'image')
        return {
            'ok': False,
            'method': method,
            'needs_manual': True,
            'values': {},
            'message': ('This is a scan or a photo, so there is no text on it to read. '
                        'Type the bill in below.') if scan
                       else (err or 'Could not read any text from that file.'),
        }

    header = guess_header(text)          # local regular expressions, no model
    amount = header.get('total')
    values = {
        'reference': header.get('invoice_number', ''),
        'bill_date': header.get('invoice_date', ''),
        'amount': '' if amount is None else f'{amount:.2f}',
    }
    read_any = any(str(v).strip() for v in values.values())
    # The amount comes ONLY from a line the document labels as its total
    # (guess_header -> total_from_labelled_line). When a bill does not label
    # one, the box is left EMPTY on purpose rather than filled with the largest
    # figure on the page, which on a real bill is the firm's bank account
    # number. Say so plainly, or a blank amount reads as a fault in Omni.
    if not read_any:
        message = ('There is text on this file but no bill number, date or amount that '
                   'Omni recognised. Please type it in below.')
    elif not values['amount']:
        message = ('Read off the document, but it does not have a line saying "Total", so '
                   'Omni has not guessed the amount — please type it in. The rest is filled '
                   'in for you.')
    else:
        message = ('Read off the document. Check the amount against the bill, then choose '
                   'the firm and say who the client is.')
    return {
        'ok': True,
        'method': method,
        'needs_manual': not read_any,
        'values': values,
        'amount_found': bool(values['amount']),
        'message': message,
    }


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def legal_bill_read(request):
    """POST /api/v1/bonu/legal/bills/read/ — read an uploaded bill, save nothing.

    Returns a draft for the capture form. Recording the bill is still a separate,
    deliberate step, so the duplicate guard and the spend ceiling both stand.
    """
    denied = _deny(request)
    if denied is not None:
        return denied

    f = request.FILES.get('file')
    if f is None:
        return Response({'detail': 'Attach the bill as "file".'},
                        status=status.HTTP_400_BAD_REQUEST)
    if getattr(f, 'size', 0) > MAX_UPLOAD_BYTES:
        return Response({'detail': 'That file is over 20 MB. Send a smaller copy.'},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response(read_legal_bill(f.name or '', f.read()))


#: How many bills one page of the register returns. The register is 700 rows and
#: growing, so the screen says how many of how many it is showing rather than
#: letting the oldest fee notes fall off the end without saying so.
LEGAL_BILL_PAGE = 1000


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def legal_bills(request):
    """GET  /api/v1/bonu/legal/bills/  — the bill register + the form options.
    POST /api/v1/bonu/legal/bills/  — capture a bill and allocate it.
    """
    denied = _deny(request)
    if denied is not None:
        return denied

    settings = LegalSettings.solo()

    if request.method == 'GET':
        qs = (LegalBill.objects.select_related('firm')
              .prefetch_related('allocations__case__client'))
        state = (request.query_params.get('state') or '').strip()
        if state in {v for v, _ in LegalBill.Allocation.choices}:
            qs = qs.filter(allocation_state=state)
        stage = (request.query_params.get('stage') or '').strip()
        if stage in {v for v, _ in LegalBill.Stage.choices}:
            qs = qs.filter(stage=stage)
        bills = list(qs[:LEGAL_BILL_PAGE])
        # The money strip is aggregated over every bill the filters match, not
        # over the page of rows shown — a total that shrinks as you page is the
        # reconciliation bug this module has already been bitten by once, on the
        # Billed detail screen. It follows the filters rather than ignoring them,
        # so the strip always adds up the list underneath it.
        money = qs.aggregate(
            billed=Sum('amount'), discount=Sum('discount'), paid=Sum('amount_paid'))
        matching = qs.count()
        billed = money['billed'] or Decimal('0')
        discount = money['discount'] or Decimal('0')
        paid = money['paid'] or Decimal('0')
        return Response({
            'bills': [_bill_json(b) for b in bills],
            'shown': len(bills),
            'total': LegalBill.objects.count(),
            'money': {
                'billed': str(billed),
                'discount': str(discount),
                'paid': str(paid),
                'outstanding': str(billed - discount - paid),
                'paid_without_date': qs.filter(
                    amount_paid__gt=0, paid_on__isnull=True).count(),
                # The number of bills these totals are added from. The strip must
                # never quote a count from a wider set than the money beside it.
                'bills': matching,
            },
            'unallocated_count': LegalBill.objects.filter(
                allocation_state=LegalBill.Allocation.UNALLOCATED).count(),
            'meta': {
                'firms': [{'id': str(f.pk), 'name': f.name}
                          for f in LawFirm.objects.filter(is_active=True).order_by('name')],
                'sources': [{'value': v, 'label': l} for v, l in LegalBill.Source.choices],
                'stages': [{'value': v, 'label': l} for v, l in LegalBill.Stage.choices],
                'cap': str(settings.client_spend_cap),
                'amber_at': str(settings.client_spend_amber),
                'cap_blocks_capture': settings.cap_blocks_capture,
            },
        })

    # ---- POST: capture a bill -----------------------------------------------
    data = request.data or {}
    try:
        source = (data.get('source') or LegalBill.Source.EXTERNAL).strip()
        if source not in {v for v, _ in LegalBill.Source.choices}:
            raise FieldError('Say whether this bill is from an external firm or in-house.')

        firm = None
        if source == LegalBill.Source.EXTERNAL:
            firm = _active_firm(str(data.get('firm_id') or '').strip())
            if firm is None:
                raise FieldError('Choose the active law firm this bill came from.')
        elif str(data.get('firm_id') or '').strip():
            # Refused rather than ignored: silently dropping a field the user
            # filled in is how a bill ends up filed against nobody.
            raise FieldError('An in-house bill cannot also carry a law firm.')

        reference = str(data.get('reference') or '').strip()[:120]
        if not reference:
            raise FieldError('Enter the bill or invoice number.')
        bill_date = _date(data.get('bill_date'), 'Bill date')
        received_on = _date(data.get('received_on'), 'Date received', required=False)
        amount = _dec(data.get('amount'), 'Bill amount')
        if amount <= 0:
            raise FieldError('A bill amount must be more than nought.')

        allocs = _read_allocations(data)
        billed_client_name = str(data.get('billed_client_name') or '').strip()[:200]

        # If the firm gave us a name and no matter, try to resolve the client
        # and, when exactly one certain match exists, its OPEN matter. Anything
        # less certain than that is left for a person.
        match_info = None
        if not allocs and billed_client_name:
            verdict = match_billed_name(billed_client_name)
            match_info = {'outcome': verdict['outcome'], 'via': verdict['via'],
                          'candidates': [{'id': str(m.pk), 'membership_no': m.membership_no,
                                          'name': m.full_name, 'district': m.district}
                                         for m in verdict['candidates']]}
            if verdict['outcome'] == rules.EXACT:
                client = verdict['candidates'][0]
                open_cases = list(LegalCase.objects.filter(
                    client=client, status__in=LegalCase.OPEN_STATUSES))
                if len(open_cases) == 1:
                    allocs = [(str(open_cases[0].pk), amount)]
                else:
                    # The client is certain, the MATTER is not. Do not guess
                    # which of their files this bill belongs to.
                    match_info['note'] = (
                        f'{client.full_name} matched, but they have {len(open_cases)} open '
                        f'matters — pick the right one.')

        if allocs:
            problem = rules.check_split(amount, [a for _, a in allocs])
            if problem:
                raise FieldError(problem)

        dup_key = rules.bill_dup_key(firm.name if firm else 'IN-HOUSE', amount, reference)
        if not data.get('confirm_duplicate'):
            twin = LegalBill.objects.filter(dup_key=dup_key).first()
            if twin is not None:
                return Response({
                    'duplicate_suspected': True,
                    'detail': (f'{twin.biller} already has a bill {twin.reference} for '
                               f'{twin.amount} dated {twin.bill_date}. Entering it twice '
                               f'would count it twice against the client cap. Send '
                               f'confirm_duplicate to record it anyway.'),
                    'existing': _bill_json(twin),
                }, status=status.HTTP_409_CONFLICT)

        cases = _cases_for([c for c, _ in allocs]) if allocs else {}
        breaches = _cap_breaches(allocs, cases, settings) if allocs else []
        blocking = [b for b in breaches if b['tier'] == 'red']
        if settings.cap_blocks_capture and blocking:
            return Response({
                'detail': ('This bill would take a client past their legal-spend cap of '
                           f'{settings.client_spend_cap}, and the hard ceiling is switched '
                           'on. It needs authorisation before it can be recorded.'),
                'cap_breaches': blocking,
            }, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            bill = LegalBill.objects.create(
                source=source, firm=firm, reference=reference, bill_date=bill_date,
                received_on=received_on, amount=amount,
                stage=LegalBill.Stage.BILLED,
                billed_client_name=billed_client_name, dup_key=dup_key,
                note=str(data.get('note') or '').strip()[:4000],
                captured_by_email=(getattr(request.user, 'email', '') or '')[:200],
            )
            rows = [LegalBillAllocation(bill=bill, case=cases[c], amount=a) for c, a in allocs]
            LegalBillAllocation.objects.bulk_create(rows)
            bill.allocation_state = _resolve_state(bill, rows)
            bill.save(update_fields=['allocation_state', 'updated_at'])

    except FieldError as e:
        return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except IntegrityError:
        return Response({'detail': 'That bill clashes with one already on file.'},
                        status=status.HTTP_409_CONFLICT)

    bill = (LegalBill.objects.select_related('firm')
            .prefetch_related('allocations__case__client').get(pk=bill.pk))
    out = {'ok': True, 'bill': _bill_json(bill)}
    if match_info:
        out['match'] = match_info
    if breaches:
        out['cap_breaches'] = breaches
    return Response(out, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def legal_bill_allocate(request, bill_id):
    """POST /api/v1/bonu/legal/bills/<id>/allocate/ — tie a bill to matters.

    Replaces the whole allocation for the bill, because a correction is usually
    "it was these two matters, not that one" and applying that as a patch is
    how a stray line survives and double-counts.

    Send `learn_alias` to remember the firm spelling of the client name against
    the client this bill landed on, so the next one matches by itself.
    """
    denied = _deny(request)
    if denied is not None:
        return denied

    bill = (LegalBill.objects.select_related('firm')
            .prefetch_related('allocations__case__client').filter(pk=bill_id).first())
    if bill is None:
        return Response({'detail': 'That bill is not on file.'},
                        status=status.HTTP_404_NOT_FOUND)

    data = request.data or {}
    settings = LegalSettings.solo()
    try:
        allocs = _read_allocations(data)
        if not allocs:
            raise FieldError('Say which matter (or matters) this bill is for.')
        problem = rules.check_split(bill.amount, [a for _, a in allocs])
        if problem:
            raise FieldError(problem)
        cases = _cases_for([c for c, _ in allocs])

        breaches = _cap_breaches(allocs, cases, settings)
        blocking = [b for b in breaches if b['tier'] == 'red']
        # No `confirm_cap` escape hatch. There used to be one, and it made the
        # hard ceiling decorative: capture a bill unallocated, then allocate it
        # with confirm_cap, and any BONU user was past the cap with no
        # authorisation at all — while the capture path refused the same bill
        # outright. Nothing sent the flag, so nothing needed it. (Fable, /fabe
        # gate, 9 Sep 2026; same shape as the decorative proof gate found in
        # the PAY-PREM-01 review.)
        if settings.cap_blocks_capture and blocking:
            return Response({
                'detail': ('This allocation would take a client past their legal-spend '
                           'cap of '
                           f'{settings.client_spend_cap}, and the hard ceiling is '
                           'switched on. It needs authorisation before it can be '
                           'allocated.'),
                'cap_breaches': blocking,
            }, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            bill.allocations.all().delete()
            rows = [LegalBillAllocation(bill=bill, case=cases[c], amount=a) for c, a in allocs]
            LegalBillAllocation.objects.bulk_create(rows)
            bill.allocation_state = _resolve_state(bill, rows)
            bill.save(update_fields=['allocation_state', 'updated_at'])

            learned = None
            alias_name = (str(data.get('learn_alias') or '').strip()
                          or bill.billed_client_name)
            if data.get('learn_alias') is not None and alias_name:
                clients = {c.client_id for c in cases.values() if c.client_id}
                if len(clients) != 1:
                    # An alias must point at exactly one client or it is itself
                    # ambiguous, which is the problem it exists to solve.
                    learned = {'ok': False, 'reason': 'This bill covers more than one '
                                                      'client, so the name cannot be '
                                                      'learned against one of them.'}
                else:
                    key = normalise(alias_name)
                    existing = LegalClientAlias.objects.filter(alias_key=key).first()
                    if existing is not None and existing.client_id not in clients:
                        learned = {'ok': False,
                                   'reason': 'Another client already answers to that '
                                             'spelling, so learning it would make future '
                                             'bills match the wrong person.'}
                    elif existing is None:
                        LegalClientAlias.objects.create(
                            client_id=next(iter(clients)), alias_raw=alias_name[:200],
                            alias_key=key[:200], firm=bill.firm,
                            learned_by_email=(getattr(request.user, 'email', '') or '')[:200])
                        learned = {'ok': True, 'alias': alias_name}
                    else:
                        learned = {'ok': True, 'alias': alias_name, 'already_known': True}

    except FieldError as e:
        return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except IntegrityError:
        return Response({'detail': 'That allocation clashes with one already on file.'},
                        status=status.HTTP_409_CONFLICT)

    bill = (LegalBill.objects.select_related('firm')
            .prefetch_related('allocations__case__client').get(pk=bill.pk))
    out = {'ok': True, 'bill': _bill_json(bill)}
    if breaches:
        out['cap_breaches'] = breaches
    if learned is not None:
        out['alias'] = learned
    return Response(out)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def legal_bill_stage(request, bill_id):
    """POST /api/v1/bonu/legal/bills/<id>/stage/ — billed / paid / rejected.

    A workflow marker only. Marking a bill paid in Omni does not pay anybody;
    the CFO releases every payment in the FNB app with two-factor.
    """
    denied = _deny(request)
    if denied is not None:
        return denied

    bill = LegalBill.objects.filter(pk=bill_id).first()
    if bill is None:
        return Response({'detail': 'That bill is not on file.'},
                        status=status.HTTP_404_NOT_FOUND)

    stage = str((request.data or {}).get('stage') or '').strip()
    if stage not in {v for v, _ in LegalBill.Stage.choices}:
        return Response({'detail': 'Unknown bill stage.'}, status=status.HTTP_400_BAD_REQUEST)
    was_counted = bill.counts_toward_cap
    bill.stage = stage
    bill.save(update_fields=['stage', 'updated_at'])

    bill = (LegalBill.objects.select_related('firm')
            .prefetch_related('allocations__case__client').get(pk=bill.pk))
    out = {'ok': True, 'bill': _bill_json(bill)}
    # Bringing a REJECTED bill back to billed puts its money back into the
    # client's cap total, and the ceiling never sees that move — it only guards
    # capture and allocation. Not refused (un-rejecting a bill is a correction,
    # and refusing it would strand the bill), but never silent either.
    if bill.counts_toward_cap and not was_counted:
        settings = LegalSettings.solo()
        # One bill can be split across two MATTERS belonging to the SAME
        # client, and each allocation then reports that client's cap standing —
        # so the same client appeared twice, with identical figures, reading as
        # two separate breaches. Count each client once.
        breached, seen = [], set()
        for a in bill.allocations.all():
            client_id = a.case.client_id
            if client_id is None or client_id in seen:
                continue
            seen.add(client_id)
            state = cap_after_restating(a.case, settings)
            if state:
                breached.append(state)
        if breached:
            out['cap_breaches'] = breached
            out['warning'] = ('This bill is counting toward client legal spend again, '
                              'and it takes a client to or past their cap.')
    return Response(out)


# ---------------------------------------------------------------------------
# The monitoring board
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def legal_cap_board(request):
    """GET /api/v1/bonu/legal/cap/ — every client with a running total vs the cap.

    The spec asks for a view listing clients against the 80,000 ceiling so
    anyone can see who is approaching it before a new bill tips them over.
    Amber and red are listed first, because a board sorted by name buries the
    only two rows that matter.
    """
    denied = _deny(request)
    if denied is not None:
        return denied

    settings = LegalSettings.solo()
    totals = client_spend()
    members = {m.pk: m for m in BonuMember.objects.filter(pk__in=list(totals))}

    rows = []
    for client_id, total in totals.items():
        m = members.get(client_id)
        if m is None:
            continue
        row = _client_json(m, total, settings)
        row['matters'] = LegalCase.objects.filter(client_id=client_id).count()
        rows.append(row)

    order = {'red': 0, 'amber': 1, 'clear': 2}
    rows.sort(key=lambda r: (order[r['cap']['tier']], -Decimal(r['cap']['total'])))

    # Money that cannot be attributed to anybody. Reported next to the board on
    # purpose: a cap board that looks complete while bills sit unallocated is a
    # board that lies by omission.
    unalloc = LegalBill.objects.filter(
        allocation_state=LegalBill.Allocation.UNALLOCATED,
        stage__in=LegalBill.COUNTS_TOWARD_CAP)
    return Response({
        'clients': rows,
        'cap': str(settings.client_spend_cap),
        'amber_at': str(settings.client_spend_amber),
        'cap_blocks_capture': settings.cap_blocks_capture,
        'counts': {
            'red': sum(1 for r in rows if r['cap']['tier'] == 'red'),
            'amber': sum(1 for r in rows if r['cap']['tier'] == 'amber'),
            'clear': sum(1 for r in rows if r['cap']['tier'] == 'clear'),
        },
        'unallocated': {
            'bills': unalloc.count(),
            'amount': str(unalloc.aggregate(t=Sum('amount'))['t'] or Decimal('0')),
            'note': 'Legal spend not yet tied to a client, so it is in NO total above.',
        },
    })
