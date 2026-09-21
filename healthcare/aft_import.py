"""
healthcare/aft_import.py — AFT weekly claims-paid importer.

CFO / Tlamelo 2026-06-13. Every Saturday (~01:00-02:00) AFA delivers a BInSol
AFT claims remittance: ADI_AFT_PmtRun_YYYYMMDD.xlsx. It carries a "Claim Lines"
detail sheet (member, product, group, practice, claim number, Charged/Paid
amounts, ICD-10 …) plus pre-computed summary tabs (Product / Group / Practice /
Beneficiary totals).

This module turns that file into the Healthcare dashboard's "claims paid"
figure, automatically and idempotently:

  * Headline totals (paid + charged) and a breakdown by product / practice /
    beneficiary — read from the file's own summary tabs.
  * IDEMPOTENT: keyed on a sha256 of the bytes, so re-importing the exact same
    run does nothing. A *corrected* re-send for the same Remit Date supersedes
    the old import (one live run per Remit Date) — so figures never inflate.
  * A DeepSeek plain-English commentary, built ONLY from non-PII aggregates.

DATA-PROTECTION HARD LINE (AD-POL-AI-GOV-001, non-waivable): this file is full
of customer PII — Member Number, Age, ICD-10 diagnosis, bank accounts. NONE of
that is ever sent to DeepSeek or any external API. Only the aggregate numbers +
plan/practice (business) names go to DeepSeek for the narrative. The member-level
rows are parsed in-process and stay inside omni.
"""
from __future__ import annotations

import hashlib
import io
import logging
from datetime import date, datetime
from decimal import Decimal

log = logging.getLogger('aft-import')
ZERO = Decimal('0')


def _d(v) -> Decimal:
    if v is None or v == '':
        return ZERO
    try:
        return Decimal(str(v).replace(',', '').strip())
    except Exception:
        return ZERO


def _read_summary_tab(wb, sheet_name: str, label_cols: tuple, amount_hdr='total amount'):
    """Read a '... Totals' tab into [{label, amount}] using its own header row."""
    if sheet_name not in wb.sheetnames:
        return []
    ws = wb[sheet_name]
    rows = [[c.value for c in r] for r in ws.iter_rows()]
    # header = first row containing the amount header
    hdr_i = None
    for i, r in enumerate(rows):
        low = [str(c).lower().strip() if c is not None else '' for c in r]
        if any(amount_hdr in c for c in low):
            hdr_i = i
            break
    if hdr_i is None:
        return []
    hdr = [str(c).lower().strip() if c is not None else '' for c in rows[hdr_i]]
    amt_idx = next((j for j, h in enumerate(hdr) if amount_hdr in h), None)
    lbl_idx = next((j for j, h in enumerate(hdr) if any(lc in h for lc in label_cols)), None)
    if amt_idx is None or lbl_idx is None:
        return []
    out = []
    for r in rows[hdr_i + 1:]:
        if lbl_idx >= len(r) or r[lbl_idx] in (None, ''):
            continue
        out.append({'label': str(r[lbl_idx]).strip(), 'amount': str(_d(r[amt_idx]))})
    return out


def parse_aft_run(blob: bytes) -> dict:
    """Parse an ADI_AFT_PmtRun xlsx into headline totals + non-PII breakdowns.

    Computed DIRECTLY from a non-read_only workbook. NOTE: the shared
    upload_views._parse_xlsx opens read_only, and AFA's files don't store sheet
    dimensions — so read_only misreports the Claim Lines sheet as 1 row and the
    headline totals come back 0. We read the Claim Lines sheet ourselves here.
    """
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(blob), data_only=True)   # NOT read_only

    total_paid = total_charged = ZERO
    line_count = 0
    members: set[str] = set()
    remit = None
    rows_keep: list[dict] = []

    if 'Claim Lines' in wb.sheetnames:
        ws = wb['Claim Lines']
        rws = [[c.value for c in r] for r in ws.iter_rows()]
        # NB: AFA's header cells carry newlines ("Remit\nDate", "Paid\nAmount"),
        # so normalise \n -> space before matching, here AND when building hdr.
        hdr_i = next((i for i, r in enumerate(rws)
                      if any(str(c).replace('\n', ' ').strip().lower() in
                             ('claim number', 'paid amount', 'remit date')
                             for c in r if c is not None)), None)
        if hdr_i is not None:
            hdr = [str(c).replace('\n', ' ').strip().lower() if c is not None else ''
                   for c in rws[hdr_i]]

            def _idx(name):
                return next((j for j, h in enumerate(hdr) if h == name), None)

            pi, ci = _idx('paid amount'), _idx('charged amount')
            mi, ri = _idx('member number'), _idx('remit date')
            for r in rws[hdr_i + 1:]:
                if not any(x not in (None, '') for x in r):
                    continue
                line_count += 1
                if pi is not None and pi < len(r):
                    total_paid += _d(r[pi])
                if ci is not None and ci < len(r):
                    total_charged += _d(r[ci])
                if mi is not None and mi < len(r) and r[mi] not in (None, ''):
                    members.add(str(r[mi]).strip())
                if ri is not None and ri < len(r):
                    v = r[ri]
                    if remit is None:
                        if isinstance(v, datetime):
                            remit = v.date()
                        elif isinstance(v, str) and v.strip():
                            try:
                                remit = datetime.fromisoformat(v.strip()[:19]).date()
                            except Exception:
                                pass
                # keep the member-level row (omni only; never sent externally)
                rows_keep.append({hdr[j]: (r[j] if not isinstance(r[j], datetime) else r[j].isoformat())
                                  for j in range(len(hdr)) if hdr[j] and j < len(r) and r[j] not in (None, '')})

    by_product     = _read_summary_tab(wb, 'Product Totals',          ('product',))
    by_practice    = _read_summary_tab(wb, 'Practice Totals',         ('practice name', 'practice'))
    by_group       = _read_summary_tab(wb, 'Group Totals',            ('group name', 'group'))
    by_beneficiary = _read_summary_tab(wb, 'Beneficiary Type Totals', ('beneficiary type', 'beneficiary'))

    return {
        'source_hash':    hashlib.sha256(blob).hexdigest(),
        'remit_date':     remit.isoformat() if remit else None,
        'period_label':   f"{remit:%B %Y}" if remit else '',
        'period_year':    remit.year if remit else None,
        'period_month':   remit.month if remit else None,
        'total_charged':  str(total_charged),
        'total_paid':     str(total_paid),
        'line_count':     line_count,
        'lives_count':    len(members),
        'by_product':     by_product,
        'by_practice':    by_practice,
        'by_group':       by_group,
        'by_beneficiary': by_beneficiary,
        'rows':           rows_keep,   # member-level, stays in omni only
    }


# --- DeepSeek narrative — NON-PII AGGREGATES ONLY -------------------------------
_DS_SYSTEM = (
    "You are a finance analyst writing a 3-4 sentence plain-English note for an "
    "insurance CFO about a weekly medical-claims payment run. You are given ONLY "
    "aggregate totals — no member, patient, account or diagnosis data. All money "
    "is BWP. State the total paid, the number of claim lines, the top product and "
    "top practice by amount, and flag anything that looks unusual. Be concise."
)


def _safe_aggregates(parsed: dict) -> dict:
    """Whitelist exactly the non-PII fields that may leave the building."""
    return {
        'remit_date':    parsed.get('remit_date'),
        'total_paid':    parsed.get('total_paid'),
        'total_charged': parsed.get('total_charged'),
        'line_count':    parsed.get('line_count'),
        'by_product':    parsed.get('by_product', [])[:20],
        'by_practice':   parsed.get('by_practice', [])[:20],
        'by_beneficiary': parsed.get('by_beneficiary', [])[:20],
    }


def deepseek_run_commentary(parsed: dict) -> str:
    """Best-effort DeepSeek narrative from non-PII aggregates. '' if unavailable."""
    import json
    from core.ai_assist import deepseek_complete, DeepSeekUnavailable
    aggs = _safe_aggregates(parsed)
    try:
        return deepseek_complete(
            "Weekly AFT claims-payment run (aggregates only, BWP):\n"
            + json.dumps(aggs, indent=2),
            system_prompt=_DS_SYSTEM,
            timeout=30.0,
        ).strip()
    except DeepSeekUnavailable as e:
        log.info('DeepSeek summary skipped: %s', e)
        return ''
    except Exception as e:    # noqa: BLE001 — never let the summary break the import
        log.warning('DeepSeek summary error: %s', e)
        return ''


def import_aft_run(blob: bytes, file_name: str, *, uploaded_by=None,
                   dry_run: bool = False, with_ai: bool = True) -> dict:
    """Idempotently import one AFT PmtRun file. Returns a report dict."""
    from .models import HealthcareUpload

    parsed = parse_aft_run(blob)
    src_hash = parsed['source_hash']
    remit = parsed['remit_date']

    # Exact same bytes already imported (and live) → no-op.
    existing = (HealthcareUpload.objects
                .filter(kind=HealthcareUpload.Kind.CLAIMS, source_hash=src_hash,
                        superseded=False)
                .first())
    if existing:
        return {'status': 'duplicate', 'action': 'skipped (identical file already imported)',
                'upload_id': str(existing.id), 'remit_date': remit,
                'total_paid': parsed['total_paid'], 'line_count': parsed['line_count']}

    ai_summary = deepseek_run_commentary(parsed) if (with_ai and not dry_run) else ''

    if dry_run:
        return {'status': 'dry_run', 'remit_date': remit,
                'total_paid': parsed['total_paid'], 'total_charged': parsed['total_charged'],
                'line_count': parsed['line_count'], 'lives_count': parsed['lives_count'],
                'by_product': parsed['by_product'], 'ai_summary_preview': ai_summary[:300]}

    # A corrected re-send for the same Remit Date supersedes the old import.
    superseded_n = 0
    if remit:
        superseded_n = (HealthcareUpload.objects
                        .filter(kind=HealthcareUpload.Kind.CLAIMS, remit_date=remit,
                                superseded=False)
                        .update(superseded=True))

    rd = None
    if remit:
        try:
            rd = date.fromisoformat(remit)
        except Exception:
            rd = None

    row = HealthcareUpload.objects.create(
        kind=HealthcareUpload.Kind.CLAIMS,
        direction=HealthcareUpload.Direction.INBOUND,
        file_name=file_name,
        file_size=len(blob),
        uploaded_by=uploaded_by,
        period_label=parsed.get('period_label', '') or '',
        period_year=parsed.get('period_year'),
        period_month=parsed.get('period_month'),
        total_rows=parsed['line_count'],
        total_lives_count=parsed['lives_count'],
        gross_amount=_d(parsed['total_charged']),
        paid_amount=_d(parsed['total_paid']),
        source_hash=src_hash,
        remit_date=rd,
        superseded=False,
        ai_summary=ai_summary,
        raw_payload={
            'headline':   {k: parsed[k] for k in
                           ('total_paid', 'total_charged', 'line_count', 'lives_count')},
            'by_product': parsed['by_product'],
            'by_practice': parsed['by_practice'],
            'by_group':   parsed['by_group'],
            'by_beneficiary': parsed['by_beneficiary'],
            'rows':       parsed['rows'],     # member-level — omni only, never external
        },
        status=HealthcareUpload.Status.PARSED,
    )
    return {'status': 'imported', 'upload_id': str(row.id), 'remit_date': remit,
            'total_paid': parsed['total_paid'], 'total_charged': parsed['total_charged'],
            'line_count': parsed['line_count'], 'superseded_prior': superseded_n,
            'ai_summary': ai_summary}
