"""realpay/billing_import.py — RealPay "Client Billing Period" report → billing rows.

WHY THIS EXISTS (Bokani Makosha, bug 6a48367f, 2026-09-15)
----------------------------------------------------------
The Collections Dashboard reads collection outcomes live from Graphite's
``realpay_contract_installments``. For August 2026 that feed never received the
results. Measured on the read-only replica against Bokani's own export of
RealPay's Client Billing report — 1,563 Instant and 966 Corporate/Domestic
policies that RealPay says collected:

  Instant          44.4% still 'W' processing · 28.9% 'A' raised-no-result ·
                   13.1% no August row at all · 10.9% 'I' cancelled · 0.3% 'S'
  Corporate/Dom    72.3% 'A' · 25.2% no August row · 0.2% 'S'

August COM/COMG/DOM/DOMG carry ZERO failed/processing/error rows — only 'A' and
'I'. So the money is real and the feed simply never reported it. No query change
on the Graphite side can recover it; the authoritative record is RealPay's own
Client Billing Period report, which Finance already downloads by hand.

THE TWO-REPORT COMBINE, AND WHY UPSERTING IS THE WHOLE TRICK
-------------------------------------------------------------
Bokani's manual process: take August's report, then take September's report
filtered to a tracking start date in August, and combine the two — because a
debit tracked in August often only clears in the following month's report.

Rather than model "two reports", every row is stamped with its OWN tracking
start date and written under RealPay's instalment identity
(ClientNumber + ContractSequence + InstallmentSequence). Load August's file and
September's file in either order, as many times as you like: each instalment
lands exactly once, in the month it was TRACKED, carrying its latest known
result. The combine falls out of the key, and re-loading can never double-count
a figure Finance reports on.

Verified against Bokani's file: RealPay's tracking start date agrees to the DAY
with Graphite's ``InstalmentActionDate``, so this keeps the same month basis the
dashboard already uses — it changes the SOURCE, not the calendar.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List

from django.db import transaction as db_transaction

from realpay.models import (
    RealPayImportControl,
    RealPayTransaction,
    normalize_response_code,
)
# Reuse the existing, already-tested export reader — it handles .csv/.xlsx/.xlsb,
# Excel serial dates, thousands-commas and a title line above the header. A
# second parser here would be a second set of bugs.
from realpay.recon import (
    _col,
    _dec,
    _header_map,
    _parse_date,
    _rows_from_csv,
    _rows_from_xlsb,
    _rows_from_xlsx,
)

log = logging.getLogger(__name__)

ZERO = Decimal('0.00')
CHUNK = 2000

#: Header spellings seen for the tracking start date. Bokani's own export writes
#: it three different ways across two sheets ("Tracking start Date",
#: "Tracking StartDate"), and ``_header_map`` lower-cases and strips spaces, so
#: all three collapse to ``trackingstartdate``. ``transactiondate`` is the
#: documented Client Billing header and stays as the fallback.
_TRACKING_ALIASES = ('trackingstartdate', 'trackingdate', 'trackingstart')
_DATE_FALLBACKS = ('transactiondate', 'installmentdate', 'actiondate', 'date')


class BillingParseError(ValueError):
    """A reason a person can act on — shown straight to the uploader."""


def parse_client_billing(data: bytes, filename: str) -> Dict[str, Any]:
    """Read a Client Billing Period export into normalised rows.

    Returns ``{rows, sheet, skipped, columns, used_date_column}``. ``rows`` are
    plain dicts, not model instances, so the caller can show a preview before
    anything is written.

    Raises :class:`BillingParseError` when the file cannot be used — never a
    stack trace, because the reader is a person uploading a spreadsheet.
    """
    name = (filename or '').lower()
    if name.endswith('.csv'):
        rows_raw, sheet = _rows_from_csv(data), 'csv'
    elif name.endswith('.xlsb'):
        rows_raw, sheet = _rows_from_xlsb(data)
    elif name.endswith(('.xlsx', '.xlsm')):
        rows_raw, sheet = _rows_from_xlsx(data)
    else:
        raise BillingParseError(
            'Upload the Client Billing report as .csv, .xlsx or .xlsb.')

    if not rows_raw:
        raise BillingParseError('That file has no rows in it.')

    # RealPay puts a title line above the header on some exports, so the header
    # is the first row that actually names a client-number column.
    header_i = 0
    for i, r in enumerate(rows_raw[:8]):
        if _col(_header_map(r), 'clientnumber', 'clientno', 'policy',
                'policynumber') >= 0:
            header_i = i
            break
    hmap = _header_map(rows_raw[header_i])

    i_client = _col(hmap, 'clientnumber', 'clientno', 'policynumber', 'policy')
    if i_client < 0:
        raise BillingParseError(
            'No client number column found. The Collections Dashboard groups by '
            'the policy prefix (MIS / COM / COMG / DOM / DOMG), so that column '
            'has to be in the file. Export Management Reports → Client Billing → '
            'Client Billing Period.')

    i_track = _col(hmap, *_TRACKING_ALIASES)
    i_fallback = _col(hmap, *_DATE_FALLBACKS)
    if i_track < 0 and i_fallback < 0:
        raise BillingParseError(
            'No tracking start date (or transaction date) column found. Every '
            'row has to carry its own date, otherwise August debits that only '
            'cleared in September cannot be put back into August.')
    used_date_column = ('tracking start date' if i_track >= 0
                        else 'transaction date (fallback)')

    i_prod = _col(hmap, 'product', 'productcode')
    i_ben = _col(hmap, 'beneficiarynumber', 'beneficiary')
    i_cseq = _col(hmap, 'contractsequence', 'contractseq')
    i_iseq = _col(hmap, 'installmentsequence', 'instalmentsequence',
                  'installmentseq', 'instseq')
    i_req = _col(hmap, 'amountrequested', 'requestedamount', 'installmentamount')
    i_coll = _col(hmap, 'amountcollected', 'collectedamount', 'sumofamountcollected',
                  'collected')
    i_res = _col(hmap, 'result', 'resultcode', 'responsecode', 'status')

    if i_coll < 0:
        raise BillingParseError(
            'No collected-amount column found. Without it the report cannot say '
            'what actually came in.')

    rows: List[Dict[str, Any]] = []
    skipped = 0
    for raw in rows_raw[header_i + 1:]:
        if not any(str(c or '').strip() for c in raw):
            continue

        def get(i):
            return raw[i] if 0 <= i < len(raw) else ''

        # Bokani's export carries a stray tab on some policy numbers
        # ('COM2019000027\t') — strip whitespace before anything keys on it, or
        # the same instalment lands twice under two spellings.
        client = str(get(i_client) or '').strip()
        if not client:
            skipped += 1
            continue

        tracked = _parse_date(get(i_track)) if i_track >= 0 else None
        if tracked is None and i_fallback >= 0:
            tracked = _parse_date(get(i_fallback))
        if tracked is None:
            # A row with no date cannot be put in a month. Counting it anywhere
            # would move a money figure into the wrong period.
            skipped += 1
            continue

        result = str(get(i_res) or '').strip()
        rows.append({
            'client_number': client[:40],
            'tracking_start_date': tracked,
            'product': str(get(i_prod) or '').strip()[:40],
            'beneficiary_number': str(get(i_ben) or '').strip()[:20],
            'contract_sequence': str(get(i_cseq) or '').strip()[:20],
            'inst_seq': str(get(i_iseq) or '').strip()[:20],
            'amount_requested': _dec(get(i_req)),
            'collected_amount': _dec(get(i_coll)),
            'result_code': result[:20],
        })

    if not rows:
        raise BillingParseError(
            'No usable billing rows were found — every row was missing a client '
            'number or a date.')

    return {
        'rows': rows,
        'sheet': sheet,
        'skipped': skipped,
        'columns': sorted(hmap.keys()),
        'used_date_column': used_date_column,
    }


def row_key(row: Dict[str, Any]) -> tuple:
    """RealPay's identity for one instalment.

    ClientNumber + ContractSequence + InstallmentSequence names a single debit,
    so the same instalment appearing in both the August and the September report
    resolves to ONE row. Where the export omits the sequence columns the key
    falls back to the date and amount, which is weaker but still stops a
    straight re-upload of the same file from doubling the total.
    """
    seq = (row.get('contract_sequence') or '', row.get('inst_seq') or '')
    if any(seq):
        return (row['client_number'], seq[0], seq[1])
    return (row['client_number'], '', '',
            row['tracking_start_date'], str(row['collected_amount']))


@db_transaction.atomic
def merge_rows(rows: List[Dict[str, Any]], batch: str) -> Dict[str, Any]:
    """Upsert parsed billing rows. Idempotent by :func:`row_key`.

    Returns a reconciliation the caller shows the uploader: how many rows were
    read, how many were new, how many already existed, and the collected total —
    so the figure on screen can be checked against the figure in the file.
    """
    # Collapse duplicates WITHIN the file first, keeping the last occurrence:
    # RealPay repeats an instalment across report pages, and two rows with the
    # same key must never both be written.
    deduped: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        deduped[row_key(r)] = r
    in_file_duplicates = len(rows) - len(deduped)

    existing = {}
    for obj in RealPayTransaction.objects.filter(
            source=RealPayTransaction.Source.BILLING,
            client_number__in={r['client_number'] for r in deduped.values()}):
        existing[row_key({
            'client_number': obj.client_number,
            'contract_sequence': obj.contract_sequence,
            'inst_seq': obj.inst_seq,
            'tracking_start_date': obj.tracking_start_date,
            'collected_amount': obj.collected_amount,
        })] = obj

    to_create: List[RealPayTransaction] = []
    to_update: List[RealPayTransaction] = []
    for key, r in deduped.items():
        obj = existing.get(key)
        if obj is None:
            to_create.append(RealPayTransaction(
                source=RealPayTransaction.Source.BILLING,
                # txn_date stays populated so every existing filter, index and
                # export that keys on it keeps working unchanged.
                txn_date=r['tracking_start_date'],
                tracking_start_date=r['tracking_start_date'],
                client_number=r['client_number'],
                client_name='',          # the billing detail carries no name
                product=r['product'],
                beneficiary_number=r['beneficiary_number'],
                contract_sequence=r['contract_sequence'],
                inst_seq=r['inst_seq'],
                amount_requested=r['amount_requested'],
                collected_amount=r['collected_amount'],
                result_code=r['result_code'],
                result_code_norm=normalize_response_code(r['result_code']),
                import_batch=batch,
            ))
            continue
        obj.txn_date = r['tracking_start_date']
        obj.tracking_start_date = r['tracking_start_date']
        obj.product = r['product'] or obj.product
        obj.beneficiary_number = r['beneficiary_number'] or obj.beneficiary_number
        obj.amount_requested = r['amount_requested']
        obj.collected_amount = r['collected_amount']
        obj.result_code = r['result_code']
        obj.result_code_norm = normalize_response_code(r['result_code'])
        obj.import_batch = batch
        to_update.append(obj)

    for i in range(0, len(to_create), CHUNK):
        RealPayTransaction.objects.bulk_create(to_create[i:i + CHUNK],
                                               batch_size=CHUNK)
    for i in range(0, len(to_update), CHUNK):
        RealPayTransaction.objects.bulk_update(
            to_update[i:i + CHUNK],
            ['txn_date', 'tracking_start_date', 'product', 'beneficiary_number',
             'amount_requested', 'collected_amount', 'result_code',
             'result_code_norm', 'import_batch'],
            batch_size=CHUNK)

    settled = [r for r in deduped.values() if (r['collected_amount'] or ZERO) > ZERO]
    collected_sum = sum((r['collected_amount'] for r in settled), ZERO)
    RealPayImportControl.objects.create(
        source=RealPayTransaction.Source.BILLING, batch=batch,
        row_count=len(deduped), settled_count=len(settled),
        collected_sum=collected_sum,
    )

    dates = sorted(r['tracking_start_date'] for r in deduped.values())
    return {
        'rows_read': len(rows),
        'in_file_duplicates': in_file_duplicates,
        'rows_merged': len(deduped),
        'created': len(to_create),
        'updated': len(to_update),
        'settled_rows': len(settled),
        'collected_sum': str(collected_sum),
        'tracking_start_from': dates[0].isoformat() if dates else None,
        'tracking_start_to': dates[-1].isoformat() if dates else None,
        'batch': batch,
    }
