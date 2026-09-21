"""Reconciliation Hub — Phase 1 service layer.

Pure functions + a single ``run_reconciliation`` orchestrator. Read-only against
Graphite (via the already-wired replica helper) and the omni GL; the only writes
are the run/line/tie-out records this module creates.
"""

import logging
from decimal import Decimal

from django.conf import settings

from core.models import Company
from ledger.models import Account
from reporting.reports import _posted_lines, _signed_balance, _agg_je_lines
from reporting.reports import build_ar_aging
from reporting.graphite_age_analysis import build_graphite_age_analysis

from .constants import (
    ZERO,
    DEFAULT_TOLERANCE_PCT,
    DEFAULT_ENTITY_CODES,
    MetricKey,
    FlowType,
    SourceSystem,
    LineStatus,
    RunStatus,
    CANONICAL_BUCKETS,
    OMNI_BUCKET_MAP,
    GRAPHITE_BUCKET_MAP,
)
from .models import (
    MetricSourceMap,
    SourceFigure,
    ReconciliationRun,
    ReconciliationLine,
    AgeingTieOut,
)

log = logging.getLogger(__name__)

TWO = Decimal('0.01')

AGEING_SCOPE_NOTE = (
    'omni AR-aging includes the Instant book (account 78 etc.); the Graphite '
    'replica ageing table is Dom-Com only. Variance partly reflects this scope '
    'gap, not error. Instant ageing source pending (Arjun & Pramod).'
)


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #
def resolve_scope_company():
    """The ADIC company (Instant lives inside its ledger). Configurable via
    settings.RECON_ENTITY_CODES; falls back to the default (or is_default)."""
    codes = getattr(settings, 'RECON_ENTITY_CODES', DEFAULT_ENTITY_CODES)
    for code in codes:
        comp = Company.objects.filter(code__iexact=code).first()
        if comp:
            return comp
    return Company.objects.filter(is_default=True).first() or Company.objects.first()


# --------------------------------------------------------------------------- #
# GL (posted) side
# --------------------------------------------------------------------------- #
def _metric_accounts(metric):
    """(id, account_type) pairs for a metric's GL accounts, deduped."""
    pairs = set(metric.accounts.values_list('id', 'account_type'))
    if metric.include_all_receivable:
        pairs |= set(
            Account.objects.filter(is_receivable=True).values_list('id', 'account_type')
        )
    return list(pairs)


def posted_for_metric(metric, from_date, to_date, company_id):
    """Signed GL total for a metric over the period. None if the metric has no
    GL side (flow_type=none or no mapped accounts)."""
    if metric.flow_type == FlowType.NONE:
        return None
    accts = _metric_accounts(metric)
    if not accts:
        return None

    base = _posted_lines(company_id=company_id)
    if metric.flow_type == FlowType.PERIOD:
        if from_date is not None:
            base = base.filter(journal_entry__entry_date__gte=from_date)
        base = base.filter(journal_entry__entry_date__lte=to_date)
    else:  # BALANCE — cumulative up to as-of
        base = base.filter(journal_entry__entry_date__lte=to_date)

    ids = [a[0] for a in accts]
    agg = _agg_je_lines(base.filter(account_id__in=ids))
    total = ZERO
    for aid, atype in accts:
        dr, cr = agg.get(aid, (ZERO, ZERO))
        total += _signed_balance(dr, cr, atype)
    return total.quantize(TWO)


# --------------------------------------------------------------------------- #
# Source side
# --------------------------------------------------------------------------- #
def source_for_metric(metric, company, period_label, *, graphite_report=None):
    """Return (value, source_system, row_count, source_ref) or None.

    Priority: a captured SourceFigure for the period (register/portal/manual),
    then an automatic pull from the Graphite replica for the debtors/count
    metrics. ``graphite_report`` may be injected to avoid re-querying.
    """
    sf = (
        SourceFigure.objects
        .filter(company=company, metric_key=metric.metric_key, period_label=period_label)
        .order_by('-captured_at')
        .first()
    )
    if sf:
        return (sf.source_value, sf.source_system, sf.row_count, sf.source_ref)

    if metric.source_system == SourceSystem.GRAPHITE_RDS:
        try:
            rep = graphite_report if graphite_report is not None else build_graphite_age_analysis()
        except Exception as exc:  # replica offline etc. — never blocks the run
            log.warning('recon: graphite source unavailable for %s: %s', metric.metric_key, exc)
            return None
        summ = rep.get('summary', {}) or {}
        ref = rep.get('source', 'Graphite replica')
        if metric.metric_key == MetricKey.PREMIUM_DEBTORS:
            val = Decimal(str(summ.get('total_balance', 0) or 0)).quantize(TWO)
            return (val, SourceSystem.GRAPHITE_RDS, summ.get('policy_count'), ref)
        if metric.metric_key == MetricKey.POLICY_COUNT:
            cnt = int(summ.get('policy_count', 0) or 0)
            return (Decimal(cnt), SourceSystem.GRAPHITE_RDS, cnt, ref)
    return None


# --------------------------------------------------------------------------- #
# Classify
# --------------------------------------------------------------------------- #
def classify(source_total, omni_posted, tolerance_pct):
    """-> (status, variance, variance_pct). variance = omni_posted - source_total."""
    if source_total is None:
        return (LineStatus.NO_SOURCE, None, None)
    if omni_posted is None:
        return (LineStatus.NO_OMNI, None, None)

    variance = (omni_posted - source_total).quantize(TWO)
    if source_total != 0:
        pct = (abs(variance) / abs(source_total) * Decimal('100')).quantize(Decimal('0.0001'))
    else:
        pct = ZERO if variance == 0 else Decimal('100.0000')

    status = LineStatus.MATCHED if pct <= tolerance_pct else LineStatus.BREACH
    return (status, variance, pct)


# --------------------------------------------------------------------------- #
# Ageing tie-out
# --------------------------------------------------------------------------- #
def _canonical_from_graphite(rep):
    buckets = (rep.get('summary', {}) or {}).get('buckets', {}) or {}
    out = {b: ZERO for b in CANONICAL_BUCKETS}
    for key, val in buckets.items():
        can = GRAPHITE_BUCKET_MAP.get(key)
        if can:
            out[can] += Decimal(str(val or 0))
    return out


def _canonical_from_omni(ar):
    totals = ar.get('totals', {}) or {}
    out = {b: ZERO for b in CANONICAL_BUCKETS}
    for omni_label, can in OMNI_BUCKET_MAP.items():
        out[can] += Decimal(str(totals.get(omni_label, '0') or '0'))
    return out


def build_ageing_tieouts(run, as_of, company_id, *, graphite_report=None, ar_report=None):
    """Create AgeingTieOut rows (one per canonical bucket + a 'total' row)."""
    try:
        gr = graphite_report if graphite_report is not None else build_graphite_age_analysis()
        g_can = _canonical_from_graphite(gr)
    except Exception as exc:
        log.warning('recon: graphite ageing unavailable: %s', exc)
        g_can = None

    ar = ar_report if ar_report is not None else build_ar_aging(as_of, company_id=company_id)
    o_can = _canonical_from_omni(ar)

    tol = run.tolerance_pct
    rows = []
    g_total = ZERO
    o_total = ZERO
    for bucket in CANONICAL_BUCKETS:
        g = g_can[bucket] if g_can is not None else None
        o = o_can[bucket]
        rows.append(_ageing_row(run, bucket, g, o, tol))
        if g is not None:
            g_total += g
        o_total += o

    g_tot = g_total if g_can is not None else None
    rows.append(_ageing_row(run, 'total', g_tot, o_total, tol))
    AgeingTieOut.objects.bulk_create(rows)
    return rows


def _ageing_row(run, bucket, graphite_total, omni_total, tol):
    if graphite_total is None:
        status, var, pct = LineStatus.NO_SOURCE, None, None
    else:
        gt = graphite_total.quantize(TWO)
        ot = omni_total.quantize(TWO)
        var = (ot - gt).quantize(TWO)
        if gt != 0:
            pct = (abs(var) / abs(gt) * Decimal('100')).quantize(Decimal('0.0001'))
        else:
            pct = ZERO if var == 0 else Decimal('100.0000')
        status = LineStatus.MATCHED if pct <= tol else LineStatus.BREACH
    return AgeingTieOut(
        run=run,
        bucket=bucket,
        graphite_total=graphite_total.quantize(TWO) if graphite_total is not None else None,
        omni_total=omni_total.quantize(TWO),
        portal_total=None,  # Phase 2
        variance_graphite_vs_omni=var,
        variance_pct=pct,
        status=status,
    )


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def run_reconciliation(*, period_label, period_end, period_start=None,
                       company=None, tolerance_pct=None, user=None):
    """Run a full reconciliation pass and persist a ReconciliationRun + lines +
    ageing tie-outs. Returns the ReconciliationRun."""
    company = company or resolve_scope_company()
    if company is None:
        raise ValueError('No company in scope — seed core.Company (code ADIC) first.')
    tol = tolerance_pct if tolerance_pct is not None else DEFAULT_TOLERANCE_PCT

    run = ReconciliationRun(
        company=company,
        period_label=period_label,
        period_start=period_start,
        period_end=period_end,
        tolerance_pct=tol,
        run_by=user,
        notes=AGEING_SCOPE_NOTE,
    )
    run.save(audit_user=user)

    # Pull Graphite once, reuse across metric-source + ageing.
    try:
        graphite_report = build_graphite_age_analysis()
    except Exception as exc:
        log.warning('recon: graphite report unavailable: %s', exc)
        graphite_report = None

    partial = False
    metrics = MetricSourceMap.objects.filter(is_active=True).prefetch_related('accounts')
    for m in metrics:
        gl = posted_for_metric(m, period_start, period_end, company.id)
        src = source_for_metric(m, company, period_label, graphite_report=graphite_report)
        src_val, src_sys, row_count, src_ref = src if src else (None, '', None, '')
        status, var, pct = classify(src_val, gl, tol)
        if status in (LineStatus.NO_SOURCE, LineStatus.NO_OMNI):
            partial = True
        note = ''
        if m.metric_key == MetricKey.PREMIUM_DEBTORS:
            note = AGEING_SCOPE_NOTE
        ReconciliationLine.objects.create(
            run=run,
            metric_key=m.metric_key,
            label=m.label,
            unit=m.unit,
            source_total=src_val,
            source_system=src_sys or '',
            omni_posted=gl,
            variance=var,
            variance_pct=pct,
            status=status,
            note=note,
        )

    try:
        build_ageing_tieouts(
            run, period_end, company.id, graphite_report=graphite_report,
        )
    except Exception as exc:  # ageing failure must not sink the metric bridge
        log.warning('recon: ageing tie-out failed for run %s: %s', run.id, exc)
        partial = True

    if partial:
        run.status = RunStatus.PARTIAL
        run.save(audit_user=user)
    return run
