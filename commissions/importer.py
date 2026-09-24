"""commissions/importer.py — read an agent commission workbook's 'Data' tab
into submission lines.

The BDU agent workbooks (and the DIMPHO-style layout) carry the per-policy truth
on a sheet named 'Data' with these columns (order not assumed — matched by
header text): Policy Number, Policy Name, New Business/Renewal/Endorsement/
Previous Month, Annual/Monthly, Amount Collected, Annualised Premium, Commission
Rate, Collection Date, Is Policy Closed, Amount Applicable for Commission,
Commissions.

Runs server-side: values never leave the server. .xlsx/.xlsm via openpyxl,
.xlsb via pyxlsb, .xls/.ods via python-calamine — all lazy-imported so the app
doesn't hard-depend on any one reader.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction

from .models import (
    CommissionAgent, CommissionGroup, CommissionSubmission, CommissionSubmissionLine,
)

_TOTALS_RE = re.compile(r'^(grand\s+)?totals?$', re.I)


def _norm(v) -> str:
    return ' '.join(str(v).lower().split()) if v is not None else ''


# field -> predicate on the normalised header cell. Order = match priority.
_FIELD_TESTS = [
    # policy number: the standard "Policy Number" plus the broker-sheet short
    # forms "Policy #" / "Policy No" / a bare "Policy".
    ('policy_number',      lambda h: 'policy number' in h or 'policy no' in h
                                     or h in {'policy', 'policy #', 'policy#', 'policy no.'}),
    ('transaction_type',   lambda h: 'new business' in h or h in
                                     {'renewal', 'endorsement', 'previous month'}),
    ('frequency',          lambda h: 'annual' in h and 'monthly' in h),
    # amount collected: "Amount Collected", or a column named simply "Amount"
    # (the broker sheets carry the premium collected under that plain header).
    ('amount_collected',   lambda h: 'amount collected' in h or h == 'amount'),
    ('annualised_premium', lambda h: 'annualised premium' in h or 'annualized premium' in h),
    ('commission_rate',    lambda h: 'commission rate' in h),
    ('amount_applicable',  lambda h: 'amount applicable' in h),
    # collection date: "Collection Date", or the broker sheets' "Deposit Date".
    ('collection_date',    lambda h: ('collection date' in h or 'deposit date' in h)
                                     and not h.startswith('if not')),
    ('is_policy_closed',   lambda h: 'policy closed' in h),
    # commission amount: "Commissions", and the common misspelling "Commision"
    # (one S) seen on the broker sheets. Excludes rate/applicable/reason and the
    # per-class "Motor Commission" / "Non Motor Commission" split columns.
    ('commission_amount',  lambda h: (h.startswith('commission') or h.startswith('commision'))
                                     and not any(x in h for x in ('rate', 'applicable', 'reason'))),
    ('client_name',        lambda h: 'policy name' in h or 'client name' in h),
]


def map_headers(header_row) -> dict:
    """{field: column_index} — first unused column matching each field."""
    norm = [_norm(c) for c in header_row]
    used, mapping = set(), {}
    for field, test in _FIELD_TESTS:
        for i, h in enumerate(norm):
            if i in used or not h:
                continue
            if test(h):
                mapping[field] = i
                used.add(i)
                break
    return mapping


def find_header(rows, scan=60) -> tuple[int, dict]:
    """Row index + column map for the per-policy header. Requires BOTH a policy
    number AND a real Commissions column — so an older-template sheet that lacks
    a commission column is rejected (never imported as zero-commission)."""
    for i, row in enumerate(rows[:scan]):
        m = map_headers(row)
        if 'policy_number' in m and 'commission_amount' in m:
            return i, m
    raise ValueError("No per-policy header (Policy Number + Commissions) found.")


def _dec(v) -> Decimal:
    if v is None or v == '':
        return Decimal('0.00')
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    s = str(v).strip().replace('\xa0', '').replace(' ', '').replace(',', '').replace('P', '').replace('%', '')
    neg = s.startswith('(') and s.endswith(')')   # accounting-format negative
    if neg:
        s = s[1:-1]
    try:
        d = Decimal(s or '0')
        return -d if neg else d
    except InvalidOperation:
        return Decimal('0.00')


def _date(v, xlsb=False):
    if v is None or v == '':
        return None
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    if xlsb and isinstance(v, (int, float)):
        try:
            from pyxlsb import convert_date
            d = convert_date(v)
            return d.date() if isinstance(d, _dt.datetime) else d
        except Exception:
            return None
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%m/%d/%Y'):
        try:
            return _dt.datetime.strptime(str(v).strip()[:10], fmt).date()
        except (ValueError, TypeError):
            continue
    return None


_TXN = [('new business', 'new_business'), ('renewal', 'renewal'),
        ('endorsement', 'endorsement'), ('previous', 'previous_month'),
        ('prior', 'previous_month')]   # some sheets say "Prior Month"


def _txn(v) -> str:
    h = _norm(v)
    for needle, choice in _TXN:
        if needle in h:
            return choice
    return 'new_business'


def _closed(v) -> bool:
    return _norm(v) in ('yes', 'y', 'true', 'closed', '1')


def parse_rows(rows, xlsb=False):
    """Yield line dicts for every row with a policy number (skips blanks/totals)."""
    hi, m = find_header(rows)
    for row in rows[hi + 1:]:
        def cell(field):
            idx = m.get(field)
            return row[idx] if idx is not None and idx < len(row) else None
        pol = cell('policy_number')
        pols = '' if pol is None else str(pol).strip()
        # Skip blank rows AND a 'Total' / 'Grand Total' label parked in the
        # policy-number column (else the total row doubles the gross).
        if not pols or _TOTALS_RE.match(pols):
            continue
        yield {
            'policy_number': str(pol).strip()[:60],
            'client_name': str(cell('client_name') or '').strip()[:160],
            'transaction_type': _txn(cell('transaction_type')),
            'frequency': str(cell('frequency') or '').strip()[:10],
            'amount_collected': _dec(cell('amount_collected')),
            'annualised_premium': _dec(cell('annualised_premium')),
            'commission_rate': _dec(cell('commission_rate')),
            'amount_applicable': _dec(cell('amount_applicable')),
            'collection_date': _date(cell('collection_date'), xlsb=xlsb),
            'is_policy_closed': _closed(cell('is_policy_closed')),
            'commission_amount': _dec(cell('commission_amount')),
        }


def _iter_sheets(path):
    """Yield (sheet_name, rows, is_xlsb) for every sheet — the per-policy table
    lives under different names across agents (Data / New business / etc.)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.xlsb':
        from pyxlsb import open_workbook
        with open_workbook(path) as wb:
            for name in wb.sheets:
                with wb.get_sheet(name) as sh:
                    yield name, [[c.v for c in row] for row in sh.rows()], True
        return
    if ext in ('.xls', '.ods'):
        # Old binary Excel / OpenDocument — openpyxl reads neither. Use
        # python-calamine (one Rust path for xlsx/xlsb/xls/ods), the reader the
        # rest of the codebase already standardises on (core/doc_parse, payroll,
        # vendor upload). It returns real datetimes, so dates are NOT Excel
        # serials — is_xlsb=False, the same as the openpyxl path below.
        import io
        from python_calamine import CalamineWorkbook
        with open(path, 'rb') as fh:
            wb = CalamineWorkbook.from_filelike(io.BytesIO(fh.read()))
        for name in wb.sheet_names:
            yield name, wb.get_sheet_by_name(name).to_python(), False
        return
    import openpyxl   # .xlsx and .xlsm — both zip-based, openpyxl reads both
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for name in wb.sheetnames:
            yield name, [list(r) for r in wb[name].iter_rows(values_only=True)], False
    finally:
        wb.close()


def has_per_policy_table(path) -> bool:
    """True if ANY sheet carries a per-policy header (Policy Number + Commissions).

    Lets the upload auto-detect a DETAILED sheet from a per-agent GROSS summary:
    an in-house reviewer can drop a detailed workbook and keep the per-policy
    basis, instead of being forced into the summary path that stores 'just a
    number' (Bokani Makosha, bug f3a02295, 2026-08-21)."""
    for _name, rows, _xlsb in _iter_sheets(path):
        try:
            find_header(rows)
            return True
        except ValueError:
            continue
    return False


_SPLIT = ('domestic', 'commercial', 'new business')


def _three_figure_summary(rows):
    """The agent's own COMMISSION SUMMARY — {'domestic', 'commercial',
    'new business', 'total'} as Decimals — or None when the sheet has no
    Domestic / Commercial / New Business header row."""
    for i, row in enumerate(rows[:40]):
        norm = [_norm(c) for c in row]
        if not all(h in norm for h in _SPLIT):
            continue
        cols = {h: norm.index(h) for h in _SPLIT + ('total',) if h in norm}
        for nxt in rows[i + 1:i + 4]:
            # figures may be numbers or text like '1,200.00' — _dec reads both
            if any(_dec(nxt[c]) for c in cols.values() if c < len(nxt)):
                return {h: _dec(nxt[c] if c < len(nxt) else None) for h, c in cols.items()}
    return None


def _commercial_lines(rows):
    """The Commercial tab: 'Policy No.' per row, commission in the TOTAL column
    under the COMMISSION heading (motor 3.5% + non-motor 5%)."""
    for i, row in enumerate(rows[:40]):
        norm = [_norm(c) for c in row]
        m = map_headers(row)
        if 'policy_number' not in m or 'total' not in norm:
            continue
        com = norm.index('total')
        typ = norm.index('type') if 'type' in norm else None
        for r in rows[i + 1:]:
            pol = str(r[m['policy_number']] or '').strip() if m['policy_number'] < len(r) else ''
            if not pol or _TOTALS_RE.match(pol):
                continue
            yield {
                'policy_number': pol[:60],
                'client_name': str(r[m['client_name']] or '').strip()[:160] if 'client_name' in m else '',
                'transaction_type': _txn(r[typ]) if typ is not None and typ < len(r) else 'new_business',
                'amount_collected': _dec(r[norm.index('total premium')]) if 'total premium' in norm else Decimal('0.00'),
                'commission_amount': _dec(r[com] if com < len(r) else None),
            }
        return


def _domestic_lines(rows, domestic_commission):
    """The Details tab lists each Domestic policy but carries no commission per
    policy — the Domestic figure is one amount on the summary. Every policy is
    kept (commission 0) plus ONE line holding that summary figure, so all
    policies show and the total still equals what the agent verified."""
    for i, row in enumerate(rows):
        m = map_headers(row)
        if 'policy_number' not in m:
            continue
        for r in rows[i + 1:]:
            def cell(f):
                j = m.get(f)
                return r[j] if j is not None and j < len(r) else None
            pol = str(cell('policy_number') or '').strip()
            if not pol or _TOTALS_RE.match(pol):
                continue
            yield {
                'policy_number': pol[:60],
                'client_name': str(cell('client_name') or '').strip()[:160],
                'transaction_type': _txn(cell('transaction_type')),
                'amount_collected': _dec(cell('amount_collected')),
                'commission_amount': Decimal('0.00'),
            }
        break
    yield {'policy_number': '', 'client_name': 'Domestic commission (Details summary)',
           'transaction_type': 'renewal', 'commission_amount': domestic_commission}


def _parse_three_figure(sheets, summary_name, summary):
    """Bokani Makosha, bug db6c82e9 (19-Sep-2026): a workbook split into
    Domestic / Commercial / New Business was read as New Business only.
    New Business per policy (the usual per-policy tab), Commercial per policy,
    and Domestic from the Details tab — together they equal the summary TOTAL."""
    nb = []
    lines = []
    for name, rows, xlsb in sheets:
        if name == summary_name:
            lines += list(_domestic_lines(rows, summary['domestic']))
        elif 'commercial' in _norm(name):
            lines += list(_commercial_lines(rows))
        elif 'domestic' in _norm(name):
            continue   # Domestic is taken from the summary tab — never twice
        else:
            try:
                found = list(parse_rows(rows, xlsb=xlsb))
            except ValueError:
                continue
            if len(found) > len(nb):
                nb = found
    return nb + lines


def parse_workbook(path):
    """Find the per-policy table in ANY sheet (by header signature) and parse it.
    When several sheets match, the one with the most policy rows wins (detail
    beats a summary). Raises if no sheet has the Policy Number + Commissions
    header — i.e. an older-template workbook is rejected, not half-imported.
    A workbook with a Domestic / Commercial / New Business summary reads all
    three (_parse_three_figure)."""
    sheets = list(_iter_sheets(path))
    for name, rows, _xlsb in sheets:
        summary = _three_figure_summary(rows)
        if summary is not None:
            return _parse_three_figure(sheets, name, summary)
    best, best_sheet = [], None
    for name, rows, xlsb in sheets:
        try:
            lines = list(parse_rows(rows, xlsb=xlsb))
        except ValueError:
            continue
        if len(lines) > len(best):
            best, best_sheet = lines, name
    if not best:
        raise ValueError("No per-policy table (Policy Number + Commissions) in any sheet.")
    return best


_MONTHS = ['', 'january', 'february', 'march', 'april', 'may', 'june',
           'july', 'august', 'september', 'october', 'november', 'december']


def _period_month(period_label):
    try:
        return _MONTHS[int(period_label.split('-')[1])]
    except (ValueError, IndexError):
        return ''


def parse_inhouse_summary(path, period_label):
    """In-house sheets are a per-agent GROSS summary (a 'JUNE SUMMARY'-style tab
    with AGENT NAME + GROSS COMMISSION <MONTH>), not per-policy. Returns
    [(agent_name, gross), ...] — one entry per agent row for the period's month."""
    month = _period_month(period_label)
    # Read through _iter_sheets so an in-house summary is accepted in the same
    # spreadsheet formats a per-policy sheet is (xlsx/xlsm/xlsb/xls/ods) — no
    # second reader to drift. The is_xlsb flag is irrelevant here (no dates).
    candidates = []   # (is_month_match, rows, hi, agent_col, gross_col)
    for name, rows, _serial in _iter_sheets(path):
        for hi in range(min(15, len(rows))):
            cells = [_norm(c) for c in rows[hi]]
            if not any('agent' in c for c in cells):
                continue
            gross_cols = [i for i, c in enumerate(cells) if 'gross commission' in c]
            if not gross_cols:
                continue
            agent_col = next(i for i, c in enumerate(cells) if 'agent' in c)
            month_col = next((i for i in gross_cols if month and month in cells[i]), None)
            candidates.append((month_col is not None, rows, hi, agent_col,
                               month_col if month_col is not None else gross_cols[-1]))
            break   # one header per sheet
    if not candidates:
        raise ValueError("No in-house summary (AGENT NAME + GROSS COMMISSION) sheet found.")
    # prefer a sheet/column that actually names the period's month (JUNE),
    # not just the first summary tab (which was MAY).
    candidates.sort(key=lambda c: not c[0])
    _, rows, hi, agent_col, gcol = candidates[0]
    out = []
    for r in rows[hi + 1:]:
        nm = r[agent_col] if agent_col < len(r) else None
        s = '' if nm is None else str(nm).strip()
        if not s or _TOTALS_RE.match(s):
            continue
        out.append((s[:160], _dec(r[gcol]) if gcol < len(r) else Decimal('0.00')))
    return out


def import_inhouse(path, period_label, commit=True, submit=False, user=None, _pairs=None):
    """Load an in-house summary workbook: one submission per agent, a single line
    carrying their gross for the month (in-house has no per-policy detail).

    `user` is the person doing the upload — required when submit=True, because a
    submission must record who submitted it (b6ccaa38). `_pairs` is a test hook."""
    pairs = _pairs if _pairs is not None else parse_inhouse_summary(path, period_label)
    if not commit:
        return {'agents': len(pairs), 'gross': str(sum((g for _, g in pairs), Decimal('0.00')))}
    from . import service
    S = CommissionSubmission.Status
    created = 0
    with transaction.atomic():
        group = CommissionGroup.objects.get(key='in_house')
        for nm, gross in pairs:
            agent, _ = CommissionAgent.objects.get_or_create(name=nm, defaults={'group': group})
            sub, is_new = CommissionSubmission.objects.get_or_create(
                agent=agent, period_label=period_label, defaults={'group': agent.group})
            if not is_new and sub.status not in (S.DRAFT, S.REJECTED):
                continue   # don't clobber a reviewed/approved submission
            sub.group = agent.group
            if sub.status != S.REJECTED:   # see import_workbook — keep it reopened
                sub.status = S.DRAFT
            sub.save()
            sub.lines.all().delete()
            CommissionSubmissionLine.objects.create(
                submission=sub, policy_number='', client_name='(in-house gross)',
                transaction_type='new_business', commission_amount=gross)
            service.recompute_submission(sub)
            if submit:
                service.submit(sub, user)
            created += 1
    return {'agents': len(pairs), 'created': created}


def derive_agent_name(path):
    """Agent name from the workbook file name — the sheets are named e.g.
    'DIMPHO COMMISSION - JUNE 2026 verified.xlsb'. Shared by import + preview."""
    base = os.path.splitext(os.path.basename(path))[0]
    nm = re.split(r'\s+commission', base, flags=re.I)[0].strip()
    # drop filename noise like "verified of LOUIS" / "copy of X"
    nm = re.sub(r'^(verified of|verified|copy of|final|fw)\s+', '', nm, flags=re.I).strip()
    return nm or base


def import_workbook(path, group_key, period_label, agent_name=None, commit=True, submit=False, user=None):
    """Parse + (optionally) create the agent's submission for the month.
    agent_name defaults to the leading word of the file name. With submit=True
    the loaded submission is pushed into the review chain (→ 1st review) so a
    bulk load of already-verified sheets doesn't wait for each agent to log in."""
    lines = parse_workbook(path)
    if agent_name is None:
        agent_name = derive_agent_name(path)
    if not commit:
        return {'agent': agent_name, 'lines': len(lines),
                'gross': sum((l['commission_amount'] for l in lines), Decimal('0.00'))}
    with transaction.atomic():
        group = CommissionGroup.objects.get(key=group_key)
        agent, _ = CommissionAgent.objects.get_or_create(
            name=agent_name, defaults={'group': group})
        # The submission's group (which sets the withholding rate) must follow the
        # AGENT, not the CLI --group — else a mis-typed --group applies the wrong
        # rate. Flag the mismatch in the result.
        mismatch = agent.group_id != group.id
        eff_group = agent.group
        sub, created = CommissionSubmission.objects.get_or_create(
            agent=agent, period_label=period_label, defaults={'group': eff_group})
        S = CommissionSubmission.Status
        if not created and sub.status not in (S.DRAFT, S.REJECTED):
            raise ValueError(
                f"{agent_name} {period_label} is {sub.get_status_display()} — refusing to "
                f"overwrite a reviewed/approved submission.")
        sub.group = eff_group
        # A REJECTED sheet stays REJECTED when it is re-uploaded (Bokani Makosha,
        # bug e77ee875, 2026-09-18). Flipping it to DRAFT made the re-submit a
        # "first-time" submit, so past the 16th the agent's "I approve" was
        # refused with "you missed the deadline" straight after a good upload.
        # Amending by hand never changed the status; re-uploading now matches it.
        if sub.status != S.REJECTED:
            sub.status = S.DRAFT
        sub.save()
        sub.lines.all().delete()
        for ln in lines:
            CommissionSubmissionLine.objects.create(submission=sub, **ln)
        from . import service
        service.recompute_submission(sub)
        if submit:
            service.submit(sub, user)   # -> submitted (1st review); records the uploader (b6ccaa38)
    return {'agent': agent_name, 'lines': len(lines), 'group': eff_group.key,
            'group_mismatch': mismatch, 'status': sub.status,
            'submission_id': str(sub.id),
            'gross': str(sub.gross_commission), 'net': str(sub.net_payable)}
