"""bonu/capture.py — guided capture forms for the BONU ERP module.

CFO 2026-08-17: Kutlo said he could not capture revenue, supplier bills, admin
fees or other expenses. He could not, because the only way in was the raw grid on
the Schedule tab, where the meaningful columns sit among blank 'Column N' labels
and nobody tells you which cell is which. So he passed revenue as a hand-typed
journal entry instead — and coded it to the wrong accounts.

This module turns the four things he needs to record into four plain-English
forms. Each form writes ONE row into the sheet that already backs it, through the
same `add_schedule_row` path the grid uses — so the schedule, its totals, its
P&L and its validator all pick the row up with no second code path.

It does NOT post to the general ledger. The BONU schedule is the sub-ledger; the
monthly GL journal stays a finance approval, with the correct BONU accounts. So
nothing here can move Gross Written Premium or any frozen figure.
"""
from __future__ import annotations

from decimal import Decimal

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import BonuScheduleSheet
from .schedule import sheet_total, _money
from .schedule_views import _deny, add_schedule_row, _row

_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
           'August', 'September', 'October', 'November', 'December']

# The sheet the "Other expenses" form writes to. Not in Kutlo's original workbook
# (he had no home for a non-legal, non-admin cost), so it is created the first
# time someone records one — clean, plain columns, no 'Column N' filler.
OTHER_KEY = 'other-expenses'
OTHER_TITLE = 'OTHER EXPENSES'
OTHER_COLUMNS = ['Date', 'Category', 'Description', 'Supplier',
                 'Payment Method', 'Amount (BWP)', 'Status']
OTHER_AMOUNT_COLUMN = 'Amount (BWP)'

STATUS_OPTIONS = ['Paid', 'Unpaid', 'To be captured']

#: Each BONU member carries a P90,000 annual legal-benefit limit. A supplier bill
#: that would take a member past it is blocked unless the accountant records an
#: explicit override reason (CFO / Manus 17 Aug 2026). The cap is measured across
#: the loaded claims schedule, which holds the benefit-year book.
from decimal import Decimal as _D
LEGAL_BENEFIT_CAP = _D('90000')

#: Columns on the claims sheet the supplier guards read.
_CLAIMS_INVOICE_COL = 'Invoice/Referance No:'
_CLAIMS_MEMBER_COL = 'Client Names'
_CLAIMS_AMOUNT_COL = 'Inv Amount (P)'


def _norm(s):
    return str(s or '').strip().lower()


def _is_valid_money(raw):
    """True if a non-blank value parses as a real amount the SAME way the sheet
    total reads it. Blank is valid (legacy rows carry no amount). This stops a
    typo like 'P15k' being silently read as 0 by the cap while the sheet total
    counts it — the two must never disagree (Fable H55, 17 Aug 2026)."""
    s = str(raw or '').strip()
    if not s:
        return True
    t = s.replace(',', '').replace('P', '').replace(' ', '').replace('\xa0', '')
    if t.startswith('(') and t.endswith(')'):
        t = t[1:-1]
    try:
        _D(t or '0')
        return True
    except ArithmeticError:
        return False


def _duplicate_invoice(sheet, invoice_ref):
    """The first existing claims row that already carries this invoice reference,
    or None. Blank references never collide (many legacy rows have none)."""
    ref = _norm(invoice_ref)
    if not ref:
        return None
    for r in sheet.rows.all():
        if _norm(r.cells.get(_CLAIMS_INVOICE_COL)) == ref:
            return r
    return None


def _member_billed_to_date(sheet, member, exclude_row_id=None):
    """Total already billed against a member across the claims schedule."""
    who = _norm(member)
    total = _D('0')
    for r in sheet.rows.all():
        if exclude_row_id and str(r.id) == str(exclude_row_id):
            continue
        if _norm(r.cells.get(_CLAIMS_MEMBER_COL)) == who:
            total += _money(r.cells.get(_CLAIMS_AMOUNT_COL))
    return total


def _ensure_other_sheet():
    """Create the Other-expenses sheet on first use; return it. Idempotent."""
    s = BonuScheduleSheet.objects.filter(key=OTHER_KEY).first()
    if s:
        return s
    last = BonuScheduleSheet.objects.order_by('-order').first()
    return BonuScheduleSheet.objects.create(
        key=OTHER_KEY, title=OTHER_TITLE, columns=OTHER_COLUMNS,
        amount_column=OTHER_AMOUNT_COLUMN, order=(last.order + 1 if last else 0),
        source_note='Captured in Omni via the Record an expense form.')


def _field(column, label, *, required=False, kind='text', help='', options=None, default=''):
    """One form field. `column` is the real sheet column it writes to; `label`
    is what the accountant reads. `kind` drives the input: text / money / date /
    month / select."""
    f = {'column': column, 'label': label, 'required': required, 'kind': kind,
         'help': help, 'default': default}
    if options:
        f['options'] = options
    return f


# Each form: which sheet it writes to, and the fields in the order they should
# read on screen. `column` values are the exact live sheet column labels
# (confirmed against prod 2026-08-17) so a captured row lands in the right cell.
FORMS = {
    'revenue': {
        'title': 'Record revenue',
        'blurb': 'A premium / income line for BONU. Goes to the current-year '
                 'premium schedule — the same sheet the revenue total is read from.',
        'sheet': 'premium-2026-27',
        'icon': 'trending-up',
        'fields': [
            _field('Date', 'Date', required=True, kind='date'),
            _field('Invoice Month', 'Invoice month', required=True,
                   help='e.g. July, August — the month the income belongs to.'),
            _field('Policyholder Name', 'Policyholder', default='BONU'),
            _field('Gross Written Premium', 'Gross written premium (excl. VAT)',
                   required=True, kind='money',
                   help='The premium before VAT. This is the revenue figure.'),
            _field('Vat On Total Premium', 'VAT on premium', kind='money',
                   help='VAT charged on the premium. Botswana VAT rounds up to '
                        'the nearest thebe.'),
            _field('Commission', 'Commission', kind='money'),
            _field('Vat On Commission', 'VAT on commission', kind='money'),
            _field('Total Premium Amount', 'Total premium (incl. VAT)',
                   required=True, kind='money',
                   help='Premium plus VAT. This is the figure the schedule totals.'),
            _field('Payment Status', 'Payment status', kind='select',
                   options=['Paid', 'Unpaid', 'Part paid'], default='Unpaid'),
            _field('Date Received', 'Date received', kind='date',
                   help='Leave blank until the money is in.'),
        ],
    },
    'supplier': {
        'title': 'Record a supplier bill',
        'blurb': 'A fee note from a panel law firm (or any BONU supplier). Goes '
                 'to the claims schedule.',
        'sheet': 'claims',
        'icon': 'file-text',
        'fields': [
            _field('Law Firm Name', 'Supplier / law firm', required=True),
            _field('Inv Date', 'Invoice date', required=True, kind='date'),
            _field('Invoice/Referance No:', 'Invoice / reference no.', required=True,
                   help='The bill number. This is what stops the same bill being '
                        'paid twice — never leave it blank.'),
            _field('Client Names', 'Customer / member', required=True,
                   help='Every supplier bill must be tied to the member it was '
                        'incurred for. The reader does not fill this — attach it '
                        'yourself so the bill is never orphaned.'),
            _field('Case Matter', 'Case matter',
                   help='e.g. divorce, labour dispute, estate.'),
            _field('Invoice Month', 'Invoice month'),
            _field('Inv Amount (P)', 'Invoice amount (P)', required=True, kind='money'),
            _field('Discount', 'Discount', kind='money'),
            _field('Amount Paid (P)', 'Amount paid (P)', kind='money',
                   help='Leave blank if not yet paid.'),
            _field('Payment Date', 'Payment date', kind='date'),
            _field('Status', 'Status', kind='select', options=STATUS_OPTIONS,
                   default='Unpaid'),
        ],
    },
    'admin': {
        'title': 'Record an admin fee',
        'blurb': "BONU's own running costs — the administration fee and anything "
                 'booked against it. Goes to the admin-expenses schedule.',
        'sheet': 'admin-expenses',
        'icon': 'briefcase',
        'fields': [
            _field('Date', 'Date', required=True, kind='date'),
            _field('Category', 'Category', default='Admin Fee'),
            _field('Description', 'Description', required=True),
            _field('Invoice Issued', 'Invoice / reference'),
            _field('Payment Method', 'Payment method',
                   help='e.g. EFT, cheque, standing order.'),
            _field('Amount (BWP)', 'Amount (BWP)', required=True, kind='money'),
            _field('Status', 'Status', kind='select', options=STATUS_OPTIONS,
                   default='Paid'),
        ],
    },
    'other': {
        'title': 'Record another expense',
        'blurb': 'Any BONU cost that is not a law-firm bill and not the admin '
                 'fee. Creates and fills the Other-expenses schedule.',
        'sheet': OTHER_KEY,
        'icon': 'receipt',
        'fields': [
            _field('Date', 'Date', required=True, kind='date'),
            _field('Category', 'Category', required=True,
                   help='e.g. bank charges, printing, travel.'),
            _field('Description', 'Description', required=True),
            _field('Supplier', 'Supplier / payee'),
            _field('Payment Method', 'Payment method'),
            _field('Amount (BWP)', 'Amount (BWP)', required=True, kind='money'),
            _field('Status', 'Status', kind='select', options=STATUS_OPTIONS,
                   default='Paid'),
        ],
    },
}


def _sheet_for(form_key):
    """The live sheet a form writes to, creating the Other-expenses sheet if it
    is the target and does not exist yet. None if the form's sheet is missing."""
    spec = FORMS[form_key]
    if spec['sheet'] == OTHER_KEY:
        return _ensure_other_sheet()
    return BonuScheduleSheet.objects.filter(key=spec['sheet']).first()


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def capture_forms(request):
    """GET /bonu/capture/ — the four form specs plus each target sheet's current
    running total, so the screen can show 'recorded — new total P…'."""
    denied = _deny(request)
    if denied:
        return denied
    out = []
    for key, spec in FORMS.items():
        sheet = (BonuScheduleSheet.objects.filter(key=spec['sheet']).first()
                 if spec['sheet'] != OTHER_KEY
                 else BonuScheduleSheet.objects.filter(key=OTHER_KEY).first())
        out.append({
            'key': key, 'title': spec['title'], 'blurb': spec['blurb'],
            'icon': spec['icon'], 'sheet': spec['sheet'],
            'sheet_exists': sheet is not None,
            'sheet_total': str(sheet_total(sheet)) if sheet else '0',
            'row_count': sheet.rows.count() if sheet else 0,
            'fields': spec['fields'],
        })
    return Response({'forms': out})


def _month_name(iso_date):
    """'2026-08-01' -> 'August'. '' on anything unparseable."""
    try:
        return _MONTHS[int(str(iso_date).split('-')[1]) - 1]
    except (IndexError, ValueError):
        return ''


def read_supplier_invoice(filename, blob):
    """Read a supplier invoice (Excel or PDF) and return values pre-filled for the
    supplier form — WITHOUT saving anything.

    The reader fills the numbers; the human attaches the customer. Two safeguards
    the CFO's rules require:
      * Member names never reach an external model. The header (invoice no, date,
        total) is pulled by local regex with no AI at all; the line-structuring
        step runs on text already redacted by is_safe_for_ai(), so DeepSeek sees
        figures and codes, not who the matter is about (AD-POL-AI-GOV-001).
      * The customer field is left blank on purpose — the accountant must attach
        it, so a bill is never filed against nobody.
    """
    from bonu.ingest import extract_text, guess_header, structure_lines
    from core.ai_assist import is_safe_for_ai

    text, method, err = extract_text(filename, blob)
    if not (text or '').strip():
        # A scan/photo with no text layer — reading it needs the vision path, which
        # is a separate feature. Say so plainly rather than returning empty figures.
        scan = method in ('pdf-scanned', 'image')
        return {
            'ok': False, 'method': method,
            'needs_manual': True,
            'message': ('This looks like a scan or photo, so there is no text to read. '
                        'Please type the bill in below and attach the customer.') if scan
                       else (err or 'Could not read any text from that file.'),
            'values': {},
        }

    header = guess_header(text)                       # local, no AI, no PII
    safety = is_safe_for_ai(text)                     # redact before any model
    lines, notes, ai_err = structure_lines(safety.redacted_text)
    if ai_err:
        # The header figures come from local regex, so the read still helps — but
        # say the line-reader was down rather than look like it found no lines.
        notes = (notes + ' | ' if notes else '') + 'Line reading was unavailable; the ' \
                'header figures are from the document text.'
    matter = next((str(l.get('matter_type') or '') for l in lines if l.get('matter_type')), '')
    # Where the amount came from decides what we are allowed to claim about it.
    # A figure off a line the bill LABELS as its total is a read. A figure we
    # arrived at by adding up the model's line items is an ESTIMATE, and until
    # 9 Sep 2026 the two were indistinguishable on screen ("Read by Omni") —
    # worse, the sum never ran at all, because the old header rule took the
    # largest figure on the page and so was never None. Now that it can run,
    # say which one it is: a model that lists a sub-total or a VAT line as an
    # item double-counts, and this figure feeds the P90,000 member cap.
    amount = header.get('total')
    amount_is_a_labelled_total = amount is not None
    if amount is None and lines:
        amount = round(sum(float((l.get('amount') or 0)) for l in lines), 2)

    values = {
        'Inv Date': header.get('invoice_date', ''),
        'Invoice/Referance No:': header.get('invoice_number', ''),
        'Invoice Month': _month_name(header.get('invoice_date', '')),
        'Inv Amount (P)': '' if amount is None else f'{amount:.2f}',
        'Case Matter': matter,
        'Status': 'Unpaid',
        # Law Firm Name and Client Names are deliberately NOT auto-filled: the firm
        # is picked from the panel the accountant knows, and the customer must be
        # attached by hand.
    }
    if amount is None:
        money_note = ('It has no line saying "Total" and no readable line items, so the '
                      'amount is blank — please type it in. ')
    elif amount_is_a_labelled_total:
        money_note = 'Check the amount against the bill. '
    else:
        money_note = ('The bill has no line saying "Total", so the amount shown is its '
                      'line items ADDED UP — check it against the bill. ')
    return {
        'ok': True, 'method': method, 'needs_manual': False,
        'values': values,
        'lines_read': len(lines),
        'notes': notes,
        # False whenever the figure was not read off a labelled total line, so
        # the screen can treat an estimate differently from a read.
        'amount_found': bool(amount_is_a_labelled_total),
        'message': 'Read by Omni. ' + money_note + 'Choose the firm and attach the '
                   'customer before you record it.',
    }


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def capture_read_invoice(request):
    """POST /bonu/capture/supplier/read/ — read an uploaded invoice and return
    pre-filled supplier-form values. Saves nothing; the accountant confirms."""
    denied = _deny(request)
    if denied:
        return denied
    f = request.FILES.get('file')
    if f is None:
        return Response({'detail': 'Attach the invoice as "file".'}, status=400)
    if getattr(f, 'size', 0) > 20 * 1024 * 1024:
        return Response({'detail': 'File too large (max 20 MB).'}, status=400)
    result = read_supplier_invoice(f.name, f.read())
    return Response(result, status=200)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def capture_submit(request, form_key):
    """POST /bonu/capture/<form>/ — record one line. Body: {values: {column: v}}.

    Validates the required fields, writes one row to the form's sheet through the
    shared grid path, and returns the row plus the sheet's new total."""
    denied = _deny(request)
    if denied:
        return denied
    if form_key not in FORMS:
        return Response({'detail': 'Unknown form.'}, status=400)
    spec = FORMS[form_key]
    values = request.data.get('values') or {}
    if not isinstance(values, dict):
        return Response({'detail': 'values must be an object keyed by column label.'},
                        status=400)

    missing = [f['label'] for f in spec['fields']
               if f['required'] and not str(values.get(f['column'], '')).strip()]
    if missing:
        return Response({'detail': 'Please fill: ' + ', '.join(missing) + '.'},
                        status=400)

    # Money fields must be real numbers — a value the sheet total reads but the
    # cap guard would silently zero (or vice versa) makes the control bypassable.
    bad_money = [f['label'] for f in spec['fields']
                 if f['kind'] == 'money' and not _is_valid_money(values.get(f['column'], ''))]
    if bad_money:
        return Response({'detail': ', '.join(bad_money) + ' must be a number (e.g. 1500.00).'},
                        status=400)

    # Only the columns this form knows about — add_schedule_row blanks the rest.
    cells = {f['column']: values.get(f['column'], '') for f in spec['fields']}
    sheet = _sheet_for(form_key)
    if sheet is None:
        return Response({'detail': 'That schedule is not loaded — tell the CFO.'},
                        status=409)

    note = ''
    # Two guards on the supplier ledger (Manus QC 17 Aug 2026): never book the same
    # bill twice, and never let a member quietly pass the P90,000 legal-benefit cap.
    if form_key == 'supplier':
        dup = _duplicate_invoice(sheet, values.get(_CLAIMS_INVOICE_COL))
        if dup is not None:
            firm = dup.cells.get('Law Firm Name') or 'a firm'
            return Response({
                'code': 'DUPLICATE_INVOICE',
                'detail': f'Invoice {values.get(_CLAIMS_INVOICE_COL)} is already recorded '
                          f'(against {firm}). This is what stops a bill being paid twice — '
                          f'if it is genuinely a different bill, give it its own reference.',
            }, status=409)

        member = values.get(_CLAIMS_MEMBER_COL)
        this_bill = _money(values.get(_CLAIMS_AMOUNT_COL))
        used = _member_billed_to_date(sheet, member)
        would_be = used + this_bill
        override = str(request.data.get('override', '')).lower() in ('1', 'true', 'yes')
        reason = str(request.data.get('override_reason', '')).strip()
        if would_be > LEGAL_BENEFIT_CAP and not override:
            return Response({
                'code': 'CAP_EXCEEDED',
                'detail': f'{member} has P{used:,.2f} of legal fees already this year. '
                          f'This bill of P{this_bill:,.2f} takes them to P{would_be:,.2f}, '
                          f'over the P{LEGAL_BENEFIT_CAP:,.0f} member cap. Record it only with '
                          f'a reason.',
                'cap': str(LEGAL_BENEFIT_CAP), 'used': str(used),
                'this_bill': str(this_bill), 'over': str(would_be - LEGAL_BENEFIT_CAP),
                'remaining': str(max(_D('0'), LEGAL_BENEFIT_CAP - used)),
            }, status=409)
        if would_be > LEGAL_BENEFIT_CAP and override:
            if not reason:
                return Response({
                    'code': 'OVERRIDE_REASON_REQUIRED',
                    'detail': 'To record a bill over the P90,000 cap you must give a reason.',
                }, status=400)
            note = (f'CAP OVERRIDE by {getattr(request.user, "email", "") or "?"}: {reason} '
                    f'(member at P{would_be:,.2f} vs P{LEGAL_BENEFIT_CAP:,.0f} cap).')

    row = add_schedule_row(
        sheet, cells, request.user, note=note,
        audit=f'Captured a {form_key} line on the BONU schedule ({sheet.key})')
    return Response({'row': _row(row), 'sheet': sheet.key,
                     'sheet_total': str(sheet_total(sheet)),
                     'row_count': sheet.rows.count()}, status=201)
