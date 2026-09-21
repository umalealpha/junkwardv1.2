"""[B1] The renewal report endpoint — JSON for the screen, CSV and Excel to work from.

One builder (`renewal_report.build`) feeds all three formats on purpose. The
spec's acceptance test is that the CSV and the Excel carry the same rows and the
same columns as the screen, and the only way to keep that true is to have one
place that decides what a row is.

The report carries insured names, so it is behind `CanViewFinancials` like the
rest of the Graphite reads. Finance's DPA position, recorded on board item [B2]:
the names stay visible because the report exists to action renewals, and it is
restricted to the staff who work them.
"""
from __future__ import annotations

import csv
from datetime import date

from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import CanViewFinancials
from reporting.xlsx_export import Sheet, build_sheets_xlsx_response

from .graphite_lookup_views import _refuse_read_only_identity

from . import renewal_report

# (key in the row, column heading). Order is the spec's, with the two columns
# Finance added on 14-Sep (broker, agent) and the next-renewal date a clerk
# actually works from.
COLUMNS = [
    ('policy_number', 'Policy number'),
    ('insured_name', 'Insured name'),
    ('broker_name', 'Broker name'),
    ('agent_name', 'Agent name'),
    ('product', 'Line of business / product'),
    ('renewal_effective_date', 'Renewal effective date'),
    ('renewal_expiry_date', 'Renewal expiry date'),
    ('next_renewal_date', 'Next renewal date'),
    ('payment_frequency', 'Payment frequency'),
    ('policy_status', 'Policy status'),
    ('sum_insured', 'Sum insured'),
    ('renewal_premium', 'Renewal premium (annual)'),
    ('inforce_premium', 'Inforce premium'),
    ('no_issued_anniversary', 'No issued anniversary on record'),
]

MONTH_NAMES = ('', 'January', 'February', 'March', 'April', 'May', 'June',
               'July', 'August', 'September', 'October', 'November', 'December')


def _params(request):
    """Month, line of business and the quote switch, or a complaint in English."""
    raw_month = request.GET.get('month', '')
    try:
        month = int(raw_month)
    except (TypeError, ValueError):
        return None, 'Choose a renewal month.'
    if month not in range(1, 13):
        return None, 'The renewal month must be between 1 and 12.'

    lob = (request.GET.get('lob') or '').strip().lower()
    if lob and lob not in renewal_report.LOB_CHOICES:
        return None, 'Line of business must be domestic, commercial, or left blank.'

    quotes = (request.GET.get('include_quotes') or '').lower() in ('1', 'true', 'yes')
    return {'month': month, 'lob': lob, 'include_quotes': quotes}, None


_DATE_KEYS = ('renewal_effective_date', 'renewal_expiry_date', 'next_renewal_date')


def _cell(row, key, *, as_date=False):
    value = row.get(key)
    if key == 'no_issued_anniversary':
        return 'Yes' if value else ''
    if as_date and key in _DATE_KEYS and value:
        # Excel gets a real date so a renewals clerk can sort and filter by
        # range; the CSV keeps the plain ISO string.
        return date.fromisoformat(value)
    return '' if value is None else value


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def renewal_list(request):
    """Every in-force domestic/commercial policy renewing in the chosen month.

    `?download=csv` and `?download=xlsx` return the same rows as a file.
    """
    # The nightly QC and screenshot accounts are superusers, so the permission
    # above lets them in and a scheduled run would capture a page of insured
    # names. `graphite_search` refuses them for the same reason.
    refused = _refuse_read_only_identity(request)
    if refused is not None:
        return refused

    params, problem = _params(request)
    if problem:
        return Response({'error': problem}, status=400)

    result = renewal_report.build(params['month'], lob=params['lob'],
                                  include_quotes=params['include_quotes'])
    download = (request.GET.get('download') or '').lower()
    if download and not result['available']:
        return Response({'error': result['reason']}, status=503)
    if download == 'csv':
        return _csv(result)
    if download == 'xlsx':
        return _xlsx(result)
    return Response({**result, 'columns': [h for _, h in COLUMNS]})


def _filename(result, extension):
    month = MONTH_NAMES[result['month']]
    lob = f"-{result['lob']}" if result['lob'] else ''
    return f'renewals-{month}{lob}.{extension}'


def _csv(result):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{_filename(result, "csv")}"'
    writer = csv.writer(response)
    writer.writerow([heading for _, heading in COLUMNS])
    for row in result['rows']:
        writer.writerow([_cell(row, key) for key, _ in COLUMNS])
    # Nothing else goes in this file. Notes and exceptions used to be appended as
    # trailing rows, which made the row count stop matching the policy count and
    # broke the acceptance test that the CSV and the Excel carry the same rows.
    # They are on the screen and on the Excel's second sheet.
    return response


def _xlsx(result):
    meta = [f"Renewals for {MONTH_NAMES[result['month']]}, any year",
            f"{result['count']} policies, {result['first_renewal_count']} of them "
            f"with no issued anniversary on record"]
    meta += result.get('notes', [])
    if result['include_quotes']:
        meta.append('Includes anniversaries still at quote stage.')

    sheets = [Sheet(
        title='Renewals',
        headers=[heading for _, heading in COLUMNS],
        rows=[[_cell(row, key, as_date=True) for key, _ in COLUMNS]
              for row in result['rows']],
        meta=meta,
        numeric_cols=[11, 12],
        date_cols=[5, 6, 7],
    )]
    if result['dropped']:
        sheets.append(Sheet(
            title='Left off',
            headers=['Policy number', 'Why'],
            rows=[[e['policy_number'], e['problem']] for e in result['dropped']],
            meta=['These policies are NOT on the Renewals sheet, and why. '
                  'Nothing is dropped silently.'],
        ))
    if result['flagged']:
        sheets.append(Sheet(
            title='On the list, needs a look',
            headers=['Policy number', 'What is missing'],
            rows=[[e['policy_number'], e['problem']] for e in result['flagged']],
            meta=['These policies ARE on the Renewals sheet. Something on the '
                  'row could not be worked out.'],
        ))
    return build_sheets_xlsx_response(_filename(result, 'xlsx'), sheets)
