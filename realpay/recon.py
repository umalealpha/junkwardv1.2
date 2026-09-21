"""realpay/recon.py — RealPay collections vs Graphite, reconciled and explained.

Keetile Mokhendo's request (handover note, 2026-08-17, report 1 of 6): a
RealPay-vs-Graphite reconciliation he does not have to build by hand every
month. The blocker on the live RealPay API stays a blocker — this path takes
the file he already exports and does the work off that, so the report is not
waiting on anyone else's connection.

What this does NOT do: post a journal, move money, or change a policy. It reads
what was collected, reads what Graphite says was owed, and reports the
differences. Every number traces to a row you can click.

THE JOIN — and it is NOT the contract number. RealPay's `ClientNumber` is the
Graphite policy number; its `ContractNumber` is RealPay's own internal id,
written as "{graphite policy id}/{sequence}". Graphite's own source does exactly
this join: `Policy::where('policyNumber', $row->clientNumber)`
(RealpayTransactions.php).

This was got wrong first time round, and the way it failed is worth keeping. The
recon joined on ContractNumber, so on Keetile's real 59,448-row August export
25,137 of 26,501 contracts — 95%, P5.68m — landed in "we took money for a policy
Graphite has never heard of". That is the single most alarming thing this report
can say, and it was pure artefact. Verified on the replica before rebuilding:
496 of 500 sampled ClientNumbers match a policyNumber; joining on ContractNumber
matched 6 of 400. Never present a bucket this loud without checking the join
that produced it.

We match on the normalised policy number, never on client name (two customers
share a name far more often than people expect, and the name is PII we do not
send anywhere).

FOUR BUCKETS, and each one is a different problem with a different owner:

  matched          — RealPay collected, Graphite has the policy. Amounts compared.
  amount_differs   — same contract, different money. Query the difference.
  only_in_realpay  — we took money for a contract Graphite does not know about.
                     This is the dangerous one: a debit against a policy that
                     may have lapsed or been cancelled.
  only_in_graphite — a policy Graphite expected to collect on, with no RealPay
                     line at all. Either the debit never went out, or the file
                     is short.

PII. `client_name` never leaves this module. The AI commentary is built from
counts and totals only — no names, no contract numbers, no bank details — and
is passed through `is_safe_for_ai()` before it goes anywhere
(AD-POL-AI-GOV-001, not waivable). The engine order is the standard cheap-first
chain via `reasoning_complete()`: local Ollama → DeepSeek → and up only if the
cheap tier cannot answer.
"""
from __future__ import annotations

import datetime as _dt
import io
import logging
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

log = logging.getLogger(__name__)

ZERO = Decimal('0.00')

#: Money difference below which two amounts are treated as agreeing. RealPay and
#: Graphite both hold 2dp, so anything under a thebe is float noise, not a break.
TOLERANCE = Decimal('0.01')


# ------------------------------------------------------------------ parsing --

def _dec(raw) -> Decimal:
    """A money cell as a Decimal. Tolerates thousands-commas, blanks, and the
    two reversal notations (parenthesised, trailing minus). Same rules as the
    CLI importer — kept identical on purpose so an upload and a command-line
    load of the same file cannot disagree."""
    s = ('' if raw is None else str(raw)).strip().replace(',', '').replace(' ', '')
    if not s or s in ('-', 'NULL', 'null', 'None', 'nan'):
        return ZERO
    neg = False
    if s.startswith('(') and s.endswith(')'):
        neg, s = True, s[1:-1]
    if s.endswith('-'):
        neg, s = True, s[:-1]
    s = s.replace('P', '').replace('BWP', '').strip()
    try:
        d = Decimal(s)
    except InvalidOperation:
        return ZERO
    return -d if neg else d


def _norm_contract(raw) -> str:
    """Contract numbers as the join key: upper-cased, stripped of spaces and
    punctuation. RealPay writes 'COMG 2025 189299', Graphite writes
    'COMG2025189299' — the same contract, and a raw string compare would call
    them two different things and report a false break on every row."""
    return re.sub(r'[^A-Z0-9]', '', str(raw or '').upper())


#: Excel's day zero. Excel counts days from 1900-01-01 = 1 but wrongly treats
#: 1900 as a leap year, so the usable origin for every real date is 1899-12-30.
_EXCEL_EPOCH = _dt.date(1899, 12, 30)


def _parse_date(raw):
    """A date cell as a date, or None. Handles the four text shapes the RealPay
    exports use, whatever Excel hands back as a datetime, and Excel SERIAL
    numbers.

    The serial case is not theoretical. Keetile's own 18-Aug export is .xlsb,
    and the binary format stores dates as raw numbers — every one of his 59,448
    rows arrived as '46251.47694444445' and parsed to None, so the file had no
    usable date at all and could not be reported by period."""
    if raw in (None, ''):
        return None
    if isinstance(raw, _dt.datetime):
        return raw.date()
    if isinstance(raw, _dt.date):
        return raw
    s = str(raw).strip()[:19]
    # An Excel serial: days since 1899-12-30, time as the fraction. Bounded to a
    # sane window so a stray amount column can never be read as a date.
    try:
        serial = float(s)
    except ValueError:
        pass
    else:
        if 1 <= serial < 100000:
            return _EXCEL_EPOCH + _dt.timedelta(days=int(serial))
        return None
    for fmt in ('%Y/%m/%d %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M',
                '%Y-%m-%d', '%Y/%m/%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _header_map(header_row) -> dict:
    """Column label → index, lower-cased and space-stripped, so 'Client Number',
    'ClientNumber' and 'client number' all resolve to the same column. The
    RealPay export has changed its spacing at least twice."""
    out = {}
    for i, cell in enumerate(header_row):
        key = re.sub(r'[^a-z0-9]', '', str(cell or '').lower())
        if key and key not in out:
            out[key] = i
    return out


def _col(hmap: dict, *candidates) -> int:
    """Index of the first candidate column present, or -1. Candidates are given
    already normalised (lower, no punctuation)."""
    for c in candidates:
        if c in hmap:
            return hmap[c]
    return -1


def parse_realpay_file(data: bytes, filename: str) -> dict:
    """Read an uploaded RealPay export into plain rows.

    Accepts .xlsb (Keetile's own export format — his 18-Aug file was xlsb),
    .xlsx/.xlsm, and .csv. Returns {rows, sheet, columns, skipped} where every
    row is a dict with the fields the reconciliation needs. Raises ValueError
    with a sentence a person can act on — never a stack trace in the UI."""
    name = (filename or '').lower()
    if name.endswith('.csv'):
        rows_raw = _rows_from_csv(data)
        sheet = 'csv'
    elif name.endswith('.xlsb'):
        rows_raw, sheet = _rows_from_xlsb(data)
    elif name.endswith(('.xlsx', '.xlsm')):
        rows_raw, sheet = _rows_from_xlsx(data)
    else:
        raise ValueError('Upload a RealPay export as .xlsb, .xlsx or .csv.')

    if not rows_raw:
        raise ValueError('That file has no rows in it.')

    # The RealPay export sometimes carries a title line above the header, so the
    # header is the first row that actually names a contract column.
    header_i = 0
    for i, r in enumerate(rows_raw[:8]):
        hm = _header_map(r)
        if _col(hm, 'contractnumber', 'contract') >= 0:
            header_i = i
            break
    hmap = _header_map(rows_raw[header_i])

    i_contract = _col(hmap, 'contractnumber', 'contract', 'contractno')
    i_client   = _col(hmap, 'clientnumber', 'clientno')
    i_merch    = _col(hmap, 'merchant')
    i_name     = _col(hmap, 'clientname', 'client', 'customername')
    i_date     = _col(hmap, 'installmentdate', 'transactiondate', 'date')
    i_status   = _col(hmap, 'currentstatus', 'status')
    i_result   = _col(hmap, 'result', 'resultcode', 'responsecode')
    i_coll     = _col(hmap, 'collectedamount', 'amountcollected', 'collected')
    i_inst     = _col(hmap, 'installmentamount', 'amountrequested', 'amount')

    if i_contract < 0:
        raise ValueError(
            'No contract number column found. The reconciliation joins RealPay '
            'to Graphite on the contract number, so that column has to be in '
            'the file. Export the Transaction Report, not the summary.')

    rows, skipped = [], 0
    for raw in rows_raw[header_i + 1:]:
        if not any(str(c or '').strip() for c in raw):
            continue
        contract = _norm_contract(raw[i_contract] if i_contract < len(raw) else '')
        if not contract:
            skipped += 1
            continue
        get = lambda i: (raw[i] if 0 <= i < len(raw) else '')
        rows.append({
            'contract_number': contract,
            'contract_raw':    str(get(i_contract) or '').strip(),
            'client_number':   str(get(i_client) or '').strip(),
            'policy_number':   _norm_contract(get(i_client)),   # THE JOIN KEY — see below
            'merchant':        str(get(i_merch) or '').strip(),
            'client_name':     str(get(i_name) or '').strip(),   # PII — never sent to AI
            'txn_date':        _parse_date(get(i_date)),
            'current_status':  str(get(i_status) or '').strip().upper(),
            'result_code':     str(get(i_result) or '').strip(),
            'collected':       _dec(get(i_coll)),
            'installment':     _dec(get(i_inst)),
        })
    return {'rows': rows, 'sheet': sheet, 'skipped': skipped,
            'columns': list(hmap.keys())}


def _rows_from_csv(data: bytes) -> list:
    import csv
    text = data.decode('utf-8-sig', errors='replace')
    return [r for r in csv.reader(io.StringIO(text))]


def _rows_from_xlsx(data: bytes):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    try:
        ws = wb.worksheets[0]
        # Prefer a sheet that names a transaction report if the book has several
        # (Keetile's file carries the transaction sheet plus summary tabs).
        for cand in wb.worksheets:
            if 'transaction' in (cand.title or '').lower():
                ws = cand
                break
        return [list(r) for r in ws.iter_rows(values_only=True)], ws.title
    finally:
        wb.close()


def _rows_from_xlsb(data: bytes):
    """xlsb needs pyxlsb. If it is not installed we say so plainly rather than
    letting an ImportError surface as a 500 — the person can re-save as .xlsx
    and carry on in the meantime."""
    try:
        from pyxlsb import open_workbook
    except ImportError as e:  # noqa: F841
        raise ValueError(
            'This server cannot read .xlsb files yet. Open the file in Excel and '
            'save it as .xlsx, then upload that.')
    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix='.xlsb', delete=False)
    try:
        tmp.write(data)
        tmp.close()
        with open_workbook(tmp.name) as wb:
            sheet_name = wb.sheets[0]
            for s in wb.sheets:
                if 'transaction' in str(s).lower():
                    sheet_name = s
                    break
            with wb.get_sheet(sheet_name) as sh:
                rows = [[c.v for c in row] for row in sh.rows()]
        return rows, str(sheet_name)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


# ------------------------------------------------------------ the recon ------

def _graphite_side(policy_numbers: list[str]) -> tuple[dict, bool]:
    """What Graphite holds for these policies, and whether the read COMPLETED.

    Returns `(by_contract, complete)`. `complete` is the important half: a
    PARTIAL read must never be presented as a whole one. Fable review
    18-Aug-2026 caught the original returning partial results on a mid-read
    failure — every contract in the chunks that never ran would then have looked
    like "collected, but no policy found", i.e. a fabricated red alarm on
    perfectly good policies. That is the worst possible bug in this feature, so
    a failure now marks the whole read incomplete and the caller refuses to
    bucket anything.

    One read, not a scan per chunk. The original filtered on
    `REPLACE(REPLACE(UPPER(policyNumber),' ',''),'-','') IN (...)`, which is
    non-sargable — MySQL cannot use the index, so every 500-contract chunk
    full-scanned ~213k policies. On Keetile's real file (tens of thousands of
    rows) that is 60+ scans inside one web request: a guaranteed timeout. We now
    pull policyNumber/premium/status once and normalise in Python, where the
    join is a dict lookup.
    """
    from integrations import graphite_ro
    if not graphite_ro.is_configured():
        return {}, False
    wanted = sorted({p for p in policy_numbers if p})
    if not wanted:
        return {}, True

    out: dict[str, dict] = {}
    CHUNK = 1000
    try:
        for i in range(0, len(wanted), CHUNK):
            chunk = wanted[i:i + CHUNK]
            ph = ','.join(['%s'] * len(chunk))
            rows = graphite_ro.query(
                "SELECT policyNumber, status, term_end_date, "
                "COALESCE(premium, 0) AS premium, COALESCE(annual_premium, 0) AS annual_premium "
                f"FROM policies WHERE policyNumber IN ({ph})",
                tuple(chunk), limit=CHUNK)
            for r in rows:
                key = _norm_contract(r.get('policyNumber'))
                if key and key not in out:
                    out[key] = {
                        'policy_number': r.get('policyNumber'),
                        'status':  str(r.get('status') or ''),
                        'active':  str(r.get('status') or '') == '1',
                        'term_end_date': r.get('term_end_date'),
                        'premium': _dec(r.get('premium')),
                        'annual_premium': _dec(r.get('annual_premium')),
                    }
    except Exception:  # noqa: BLE001 — an outage must not 500 the page…
        log.exception('graphite side read failed after %s policies', len(out))
        return {}, False        # …but it must not look like a complete read either
    return out, True


def reconcile(rows: list[dict], *, graphite: dict | None = None,
              graphite_available: bool | None = None) -> dict:
    """Bucket every RealPay row against Graphite and total each bucket.

    `graphite` / `graphite_available` are injectable so the tests can run the
    whole reconciliation without a database — the buckets are the logic worth
    testing, and pinning them to a live replica would make the test slow and
    flaky.

    Availability is its OWN flag, never inferred from `bool(graphite)`. Fable
    review 18-Aug-2026: an empty dict means two completely different things —
    "the replica is down" and "the replica answered, and it has never heard of
    ANY of these contracts". The second is the exact crisis the red bucket
    exists to shout about (a whole file of debits against unknown policies), and
    inferring availability from emptiness rendered it as a bland "policy system
    not reachable"."""
    if graphite is None:
        graphite, complete = _graphite_side([r.get('policy_number') for r in rows])
        if graphite_available is None:
            graphite_available = complete
    elif graphite_available is None:
        graphite_available = True        # caller injected a real answer

    # RealPay can carry several debits for one policy in a period (monthly
    # instalments, or a retry after a failure) — group before comparing, or a
    # legitimate two-instalment policy reads as a break.
    by_contract: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_contract[r.get('policy_number') or r['contract_number']].append(r)

    matched, differs, only_rp, unverified = [], [], [], []
    for contract, group in by_contract.items():
        collected = sum((r['collected'] for r in group), ZERO)
        asked     = sum((r['installment'] for r in group), ZERO)
        successful = [r for r in group if r['current_status'] == 'SUCCESSFUL']
        failed     = [r for r in group if r['current_status'] in ('FAILED', 'ERROR')]
        # Collections grouped by MONTH, because the premium is a monthly figure.
        #
        # Both halves of this matter. Sum the whole group and you compare three
        # months of debits against one month's premium — on Keetile's June-August
        # file that reported P4.06m collected against P1.07m expected, a 3.8x
        # "over-collection" that was only ever the number of months in the
        # export. Compare row by row instead and you break every policy that
        # RealPay splits into two debits inside one month.
        #
        # Grouping by month is right for both. A file with no usable dates falls
        # back to a single group, which is exactly the old behaviour.
        by_period: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for r in group:
            key = r['txn_date'].strftime('%Y-%m') if r['txn_date'] else ''
            by_period[key] += r['collected']
        per_instalment = max(by_period.values(), default=ZERO)
        item = {
            'contract_number': contract,
            'contract_raw':    group[0]['contract_raw'],
            'client_name':     group[0]['client_name'],     # PII — display only
            'merchant':        group[0].get('merchant', ''),
            'lines':           len(group),
            'collected':       collected,
            'asked':           asked,
            'shortfall':       (asked - collected).quantize(Decimal('0.01')),
            'per_instalment':  per_instalment,
            'successful':      len(successful),
            'failed':          len(failed),
            'last_date':       max((r['txn_date'] for r in group if r['txn_date']),
                                   default=None),
        }
        g = graphite.get(contract)
        if not graphite_available:
            # NOT "matched". Fable review 18-Aug-2026: these rows were being
            # appended to `matched`, so the screen's "Agrees" tile asserted an
            # agreement that had never been tested. Unverified is its own bucket
            # and its own count — the one thing worse than no answer is a
            # confident wrong one.
            item['graphite'] = None
            unverified.append(item)
            continue
        if g is None:
            only_rp.append(item)
            continue
        item['graphite'] = g
        item['expected'] = g['premium']
        # Debited against a policy that is not active. Not a money break, so it
        # does not change the bucket — but it is the thing Finance most wants to
        # see, and it would be invisible if we only ever compared amounts.
        # Defaults to active when the caller injected a dict without the flag:
        # a missing field must never manufacture an alarm about real money.
        item['inactive_policy'] = not g.get('active', True)
        # Every month is checked; the one reported is the worst, because that is
        # the one worth opening. A policy that agreed in June and broke in
        # August is a break, not an average.
        gaps = {p: (amt - g['premium']).quantize(Decimal('0.01'))
                for p, amt in by_period.items()}
        worst = max(gaps.values(), key=abs, default=ZERO)
        item['difference'] = worst
        item['periods'] = len(gaps)
        item['periods_breaking'] = sum(1 for x in gaps.values() if abs(x) > TOLERANCE)
        (differs if abs(worst) > TOLERANCE else matched).append(item)

    # "Policy expected a collection, no debit line" — the missed-debit half of
    # Keetile's report. Fable review 18-Aug-2026 caught that this bucket was
    # STRUCTURALLY DEAD: _graphite_side only ever returned contracts that were
    # already in the upload, so a policy absent from the file could not appear
    # here by construction. The old test passed only because it injected a row
    # the query could never have produced. Answering it properly needs the set
    # of policies that were DUE to collect in the period, which the upload alone
    # cannot tell us (it carries no period, and premium is not a schedule).
    # Rather than keep a bucket that silently always reads zero, it is declared
    # unchecked so nobody mistakes silence for a clean result.
    only_g: list[dict] = []
    only_g_checked = False

    total = lambda items, key: sum((i.get(key) or ZERO for i in items), ZERO)
    return {
        'graphite_available': graphite_available,
        'counts': {
            'realpay_rows':     len(rows),
            'realpay_contracts': len(by_contract),
            'matched':          len(matched),
            'amount_differs':   len(differs),
            'only_in_realpay':  len(only_rp),
            'only_in_graphite': len(only_g),
            'unverified':       len(unverified),
        },
        'totals': {
            # Every bucket, so the headline equals the file no matter which way
            # the rows fell — including the unverified ones when the replica is
            # down. A "collected" total that quietly excluded a bucket would not
            # tie back to the export the reader is holding.
            'collected':        str(total(matched, 'collected') + total(differs, 'collected')
                                    + total(only_rp, 'collected')
                                    + total(unverified, 'collected')),
            'unverified':       str(total(unverified, 'collected')),
            'matched':          str(total(matched, 'collected')),
            'differs_collected': str(total(differs, 'collected')),
            # Per-instalment, so this compares one debit against one premium.
            # `differs_collected` above is the file total for those policies and
            # covers however many months the export spans — the two are
            # deliberately different questions and must not be subtracted.
            'differs_worst_month': str(total(differs, 'per_instalment')),
            'differs_expected': str(total(differs, 'expected')),
            'differs_gap':      str(total(differs, 'difference')),
            'shortfall':        str(total(matched, 'shortfall') + total(differs, 'shortfall')
                                    + total(only_rp, 'shortfall')
                                    + total(unverified, 'shortfall')),
            'inactive_policies': sum(1 for i in matched + differs if i.get('inactive_policy')),
            'only_realpay':     str(total(only_rp, 'collected')),
            'only_graphite':    str(total(only_g, 'expected')),
        },
        # Capped for the screen; the CSV export carries every row.
        'matched':          _slim(matched[:200]),
        'amount_differs':   _slim(sorted(differs, key=lambda i: abs(i['difference']),
                                         reverse=True)[:200]),
        'only_in_realpay':  _slim(sorted(only_rp, key=lambda i: i['collected'],
                                         reverse=True)[:200]),
        'unverified':       _slim(sorted(unverified, key=lambda i: i['collected'],
                                         reverse=True)[:200]),
        'only_in_graphite': [{**g, 'expected': str(g['expected'])} for g in only_g[:200]],
        # False = this half of the reconciliation was NOT run, so its zero is
        # "not looked at", not "nothing missing". The screen must say so.
        'only_in_graphite_checked': only_g_checked,
        'truncated': any(len(x) > 200 for x in (matched, differs, only_rp, unverified)),
    }


def _slim(items: list[dict]) -> list[dict]:
    """Decimals to strings so the response serialises, dates to ISO."""
    out = []
    for i in items:
        d = dict(i)
        for k in ('collected', 'expected', 'difference', 'asked', 'shortfall',
                  'per_instalment'):
            if k in d and d[k] is not None:
                d[k] = str(d[k])
        if d.get('last_date'):
            d['last_date'] = d['last_date'].isoformat()
        if d.get('graphite'):
            g = dict(d['graphite'])
            for k in ('premium', 'annual_premium'):
                if g.get(k) is not None:
                    g[k] = str(g[k])
            if g.get('term_end_date') is not None:
                g['term_end_date'] = str(g['term_end_date'])
            d['graphite'] = g
        out.append(d)
    return out


# ------------------------------------------------------- AI commentary -------

def commentary(result: dict) -> dict:
    """A short plain-English read of the reconciliation.

    Built from COUNTS AND TOTALS ONLY. No client name, no contract number, no
    bank detail is put in the prompt — there is nothing in it that identifies a
    customer, and it still goes through `is_safe_for_ai()` before it leaves.
    Returns {'ok': bool, 'text': str, 'reason': str} and never raises: a recon
    that cannot reach an AI engine is still a perfectly good recon, and must
    render."""
    from core.ai_assist import DeepSeekUnavailable, is_safe_for_ai, reasoning_complete

    c, t = result['counts'], result['totals']
    if not result['graphite_available']:
        return {'ok': False, 'text': '',
                'reason': 'Graphite was not reachable, so there is nothing to compare against yet.'}

    prompt = (
        "You are a Botswana insurance finance analyst. Below is a reconciliation "
        "of a RealPay debit-order collection file against the policy system. "
        "Amounts are Botswana Pula. Write 3 to 5 short sentences for a CFO: what "
        "the numbers say, which bucket he should look at first, and why. No "
        "preamble, no bullet points, no headings. If nothing is wrong, say so "
        "plainly rather than inventing a concern.\n\n"
        f"Debit lines in the file: {c['realpay_rows']}\n"
        f"Distinct contracts: {c['realpay_contracts']}\n"
        f"Total collected: {t['collected']}\n"
        f"Contracts agreeing with the policy system: {c['matched']} "
        f"(total {t['matched']})\n"
        f"Contracts where the amount differs: {c['amount_differs']} "
        f"(collected {t['differs_collected']} vs expected {t['differs_expected']}, "
        f"net difference {t['differs_gap']})\n"
        f"Collected but no policy found: {c['only_in_realpay']} contracts "
        f"(total {t['only_realpay']})\n"
        # Deliberately NOT reporting the missed-debit bucket as zero: it is not
        # computed yet, and a model told "0 missed debits" will reassure the
        # reader about something nobody checked.
        "Missed debits (policies that should have collected but have no line): "
        "not checked in this run — do not comment on it.\n"
    )

    safety = is_safe_for_ai(prompt)
    if not getattr(safety, 'safe', True):
        return {'ok': False, 'text': '',
                'reason': 'The summary was held back by the data-protection check.'}
    try:
        text = reasoning_complete(prompt, feature='realpay-recon', max_tokens=400)
    except DeepSeekUnavailable as e:
        log.warning('realpay recon commentary unavailable: %s', e)
        return {'ok': False, 'text': '',
                'reason': 'No AI engine was reachable just now. The figures above are complete.'}
    except Exception:  # noqa: BLE001 — commentary is a nice-to-have, never a blocker
        log.exception('realpay recon commentary failed')
        return {'ok': False, 'text': '',
                'reason': 'The written summary could not be produced. The figures above are complete.'}
    return {'ok': True, 'text': (text or '').strip(), 'reason': ''}
