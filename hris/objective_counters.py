"""
hris/objective_counters.py — the live numbers behind a weekly manager objective.

Each counter answers ONE question with ONE integer, read live at the moment it
is called. Nothing here writes. Nothing here is cached — a stale number would
either nag a manager who has done the work or let one off who has not.

Graphite counters run on the SAME read replica as integrations/graphite_age.py
(session forced READ ONLY; the -rpro endpoint rejects writes anyway).

A counter that cannot be read raises CounterUnavailable. The caller must treat
that as "not measured", never as "target missed" — see WeeklyObjectiveRun
.settle_error.

── The KYC three-table rule (burned the CFO on 2026-08-19) ─────────────────
KYC lives in THREE Graphite tables. `customer_kyc` holds INDIVIDUAL documents;
`customer_kyc_dom_com` holds COMMERCIAL/DOMESTIC ones. A company policy does not
populate the individual table at all, so reading only `customer_kyc` reports a
perfectly compliant commercial client as having no documents — 17 policies were
falsely flagged non-compliant that way. Every counter here reads BOTH and takes
whichever record exists (COALESCE), so a commercial client is judged on the
commercial table.
`compliance`: 1 = Compliant, 2 = Non-compliant, 0/absent = never assessed.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict

log = logging.getLogger(__name__)


class CounterUnavailable(RuntimeError):
    """The number could not be read. NOT the same as the number being bad."""


# ---------------------------------------------------------------------------
# Graphite read-replica plumbing (reuses integrations.graphite_age's config)
# ---------------------------------------------------------------------------

def _graphite_scalar(sql: str, params: tuple = ()) -> int:
    """Run one SELECT that returns a single number on the Graphite replica."""
    from integrations import graphite_age

    if not graphite_age.is_configured():
        raise CounterUnavailable('Graphite read replica is not configured.')
    try:
        import pymysql
        cfg = graphite_age._config()
        conn = pymysql.connect(
            host=cfg['host'], port=cfg['port'], user=cfg['user'],
            password=cfg['password'], database=cfg['database'],
            connect_timeout=cfg['timeout'], read_timeout=cfg['timeout'],
            cursorclass=pymysql.cursors.Cursor, charset='utf8mb4',
            init_command='SET SESSION TRANSACTION READ ONLY',
        )
    except Exception as exc:                       # noqa: BLE001 — surface as unavailable
        raise CounterUnavailable(f'Cannot reach Graphite: {exc}') from exc
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        if not row or row[0] is None:
            raise CounterUnavailable('Graphite returned no value.')
        return int(row[0])
    except CounterUnavailable:
        raise
    except Exception as exc:                       # noqa: BLE001
        raise CounterUnavailable(f'Graphite query failed: {exc}') from exc
    finally:
        conn.close()


# The KYC verdict for a policy's customer, commercial table included.
_KYC_JOIN = """
    LEFT JOIN customer_kyc k         ON k.customer_id = p.customer_id
    LEFT JOIN customer_kyc_dom_com d ON d.customer_id = p.customer_id
"""
# 2 = non-compliant, 0 = never assessed, -1 = no KYC record at all.
_KYC_FAILED = "COALESCE(k.compliance, d.compliance, -1) IN (2, 0, -1)"


# ---------------------------------------------------------------------------
# The counters
# ---------------------------------------------------------------------------

def gph_kyc_failed_active() -> int:
    """LIVE policies whose customer has failed or never had KYC.

    Baseline on 2026-09-09: 2,644 policies carrying BWP 12,091,734.61 of annual
    premium. status = 1 is active (0 = never activated, 2 = cancelled/lapsed).
    """
    return _graphite_scalar(
        f'SELECT COUNT(*) FROM policies p {_KYC_JOIN} '
        f'WHERE p.status = 1 AND p.is_test_policy = 0 AND {_KYC_FAILED}'
    )


def gph_paid_not_issued() -> int:
    """Policies that took the customer's money but were NEVER issued.

    status = 0 AND policyActivatedDate IS NULL means the cover never went live;
    a credit on policy_ledger means money came in anyway. Baseline on
    2026-09-09: 729 policies, BWP 4,816,736.95 received.

    Note `p.status = 0` alone is NOT this test — 44,401 of those 106,885 rows
    were activated at some point and later fell back; requiring a null
    activation date is what isolates 'paid but never covered'.
    """
    return _graphite_scalar(
        'SELECT COUNT(DISTINCT p.id) FROM policies p '
        'JOIN policy_ledger l ON l.policy_id = p.id '
        '  AND l.deleted_at IS NULL AND l.credit > 0 '
        'WHERE p.status = 0 AND p.policyActivatedDate IS NULL AND p.is_test_policy = 0'
    )


def gph_claims_paid_kyc_failed_7d() -> int:
    """Claims PAID in the last 7 days to a customer who failed KYC. Target: nil.

    Deliberately reads claim_reserves (the payment ledger) and NOT
    new_claims.paid_amount: that column is populated on only 29 claims in the
    whole database, so a counter built on it would read a comfortable zero
    every week while money went out the door.
    """
    return _graphite_scalar(
        'SELECT COUNT(DISTINCT nc.id) FROM claim_reserves cr '
        'JOIN new_claims nc ON nc.id = cr.claim_id '
        'JOIN policies p    ON p.id = nc.policy_id '
        f'{_KYC_JOIN} '
        'WHERE cr.created_at >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) '
        f'AND {_KYC_FAILED}'
    )


def omni_supplier_kyc_done() -> int:
    """Suppliers actually screened. Baseline on 2026-09-09: ZERO, ever.

    A row with sanctions_status 'unchecked' is a record someone opened, not a
    check someone ran — it does not count.
    """
    from procurement.kyc_models import VendorKYC
    try:
        return VendorKYC.objects.exclude(
            sanctions_status=VendorKYC.SanctionsStatus.UNCHECKED).count()
    except Exception as exc:                       # noqa: BLE001
        raise CounterUnavailable(f'Cannot read the vendor KYC register: {exc}') from exc


# ---------------------------------------------------------------------------
# Omni-side counters — the AML/CFT and market-conduct registers
# (CFO 2026-09-09; all four groups approved). None of these registers existed
# before this date, which is why every baseline below starts at or near zero.
# ---------------------------------------------------------------------------

def _local_today():
    from django.utils import timezone
    return timezone.localdate()


def omni_risk_register_complete() -> int:
    """Risks that are actually USABLE, not just typed in.

    iso_compliance.Risk already existed with ZERO rows, so the gap was never a
    modelling problem. A risk with no owner and no treatment plan is a heading,
    not a risk — counting those would let the register be filled with titles and
    still be worthless at a board meeting.
    """
    from iso_compliance.models import Risk
    try:
        return (Risk.objects
                .exclude(owner='')
                .exclude(treatment_plan='')
                .filter(status=Risk.STATUS_OPEN)
                .count())
    except Exception as exc:                       # noqa: BLE001
        raise CounterUnavailable(f'Cannot read the risk register: {exc}') from exc


def omni_sanctions_screened_total() -> int:
    """Parties screened against the sanctions lists, all time. Goes UP."""
    from iso_compliance.aml_models import SanctionsScreening
    try:
        return SanctionsScreening.objects.count()
    except Exception as exc:                       # noqa: BLE001
        raise CounterUnavailable(f'Cannot read the screening register: {exc}') from exc


def omni_sanctions_match_unreported() -> int:
    """Confirmed sanctions matches NOT yet reported to the FIA. Target: nil.

    The Financial Intelligence Act requires a positive match to reach the
    Financial Intelligence Agency without delay. This is the most serious number
    in the module — one is a regulatory incident, not a backlog.
    """
    from iso_compliance.aml_models import SanctionsScreening
    try:
        return SanctionsScreening.objects.filter(
            result=SanctionsScreening.Result.MATCH,
            reported_to_fia_at__isnull=True).count()
    except Exception as exc:                       # noqa: BLE001
        raise CounterUnavailable(f'Cannot read the screening register: {exc}') from exc


def omni_pep_unassessed() -> int:
    """Screenings where the PEP question was never answered. Comes DOWN."""
    from iso_compliance.aml_models import SanctionsScreening
    try:
        return SanctionsScreening.objects.filter(
            pep_status=SanctionsScreening.PEP.UNCHECKED).count()
    except Exception as exc:                       # noqa: BLE001
        raise CounterUnavailable(f'Cannot read the screening register: {exc}') from exc


def omni_str_unfiled() -> int:
    """Suspicious transactions detected but never filed with the FIA. Target: nil."""
    from iso_compliance.aml_models import SuspiciousTransactionReport
    try:
        return SuspiciousTransactionReport.objects.filter(
            status=SuspiciousTransactionReport.Status.DETECTED).count()
    except Exception as exc:                       # noqa: BLE001
        raise CounterUnavailable(f'Cannot read the STR register: {exc}') from exc


def omni_regulatory_breach_open() -> int:
    """Open regulatory breaches. Comes DOWN."""
    from iso_compliance.aml_models import RegulatoryBreach
    try:
        return RegulatoryBreach.objects.filter(
            status=RegulatoryBreach.Status.OPEN).count()
    except Exception as exc:                       # noqa: BLE001
        raise CounterUnavailable(f'Cannot read the breach register: {exc}') from exc


def omni_complaints_overdue() -> int:
    """Complaints unresolved past the 30-day service standard. Target: nil."""
    import datetime as dt

    from iso_compliance.aml_models import COMPLAINT_SLA_DAYS, CustomerComplaint
    try:
        cutoff = _local_today() - dt.timedelta(days=COMPLAINT_SLA_DAYS)
        return (CustomerComplaint.objects
                .exclude(status=CustomerComplaint.Status.RESOLVED)
                .filter(received_on__lt=cutoff)
                .count())
    except Exception as exc:                       # noqa: BLE001
        raise CounterUnavailable(f'Cannot read the complaints register: {exc}') from exc


def omni_aml_training_outstanding() -> int:
    """Active staff with no valid AML training in the last 12 months. Comes DOWN.

    Counts PEOPLE, not records — someone who sat the course three times is one
    person trained, and someone who never sat it is one person outstanding.
    """
    # ONE definition, shared with the officer's own screen — see
    # iso_compliance.aml_models.aml_training_outstanding_qs. Do not inline this
    # query back here: a second copy is how the screen and the scoreboard start
    # disagreeing about who still owes training.
    from iso_compliance.aml_models import aml_training_outstanding_qs
    try:
        return aml_training_outstanding_qs().count()
    except Exception as exc:                       # noqa: BLE001
        raise CounterUnavailable(f'Cannot read the training register: {exc}') from exc


def _pack_outstanding(kind: str) -> int:
    """1 if the quarter that just ENDED has no filed pack of `kind`, else 0.

    Deliberately shaped as a NIL counter so the CFO's "5th of every quarter
    ending" rule rides the SAME engine as the weekly numbers — same task, same
    self-closing, no second system to keep in step.

    It reads the quarter that just ended, not the current one: the task is
    raised on the first day of the new quarter and is asking about the old one.

    `kind` matters. Two officers now owe a quarterly report, and without it the
    AML pack landing would silently close the DPO's task.
    """
    import datetime as dt

    from iso_compliance.aml_models import ComplianceReport, quarter_of
    try:
        today = _local_today()
        first_of_this_quarter = dt.date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
        year, quarter = quarter_of(first_of_this_quarter - dt.timedelta(days=1))
        report = ComplianceReport.objects.filter(
            kind=kind, period_year=year, period_quarter=quarter).first()
        return 0 if (report and report.is_discharged) else 1
    except Exception as exc:                       # noqa: BLE001
        raise CounterUnavailable(f'Cannot read the compliance reports: {exc}') from exc


def omni_board_pack_outstanding() -> int:
    """The AML/CFT Officer's quarterly report to EXCO."""
    from iso_compliance.aml_models import ComplianceReport
    return _pack_outstanding(ComplianceReport.Kind.AML)


def omni_dpo_pack_outstanding() -> int:
    """The Data Protection Officer's quarterly report to EXCO."""
    from iso_compliance.aml_models import ComplianceReport
    return _pack_outstanding(ComplianceReport.Kind.DATA_PROT)


# ---------------------------------------------------------------------------
# Data protection — the DPO's registers (CFO 2026-09-09, "do oratile next")
#
# NO NEW MODELS. Every register below already existed in Omni; the gap was that
# nothing counted them, so ten tasks could be ticked done in July with no way to
# check. The ROPA register, policy library and vendor register were only
# DEPLOYED on the morning of 2026-09-09, so their zero baselines are the age of
# the tooling, not a measure of anyone's effort.
# ---------------------------------------------------------------------------

def omni_ropa_confirmed() -> int:
    """ROPA entries the DPO has CONFIRMED, with a lawful basis chosen.

    Both halves are required. An entry confirmed without a lawful basis is a
    row someone clicked past — the lawful basis IS the record's purpose under
    the Data Protection Act.
    """
    from iso_compliance.models import RopaEntry
    try:
        return (RopaEntry.objects
                .exclude(confirmed_at=None)
                .exclude(confirmed_lawful_basis='')
                .exclude(confirmed_lawful_basis=None)
                .count())
    except Exception as exc:                       # noqa: BLE001
        raise CounterUnavailable(f'Cannot read the ROPA register: {exc}') from exc


def omni_policies_real() -> int:
    """Policies that are real documents, not placeholders. Goes UP.

    All 29 rows seeded on 2026-09-09 are placeholders — a title reserving a slot
    in the library, not a policy anyone can follow.
    """
    from iso_compliance.models import InternalPolicy
    try:
        return InternalPolicy.objects.filter(is_placeholder=False, is_gap=False).count()
    except Exception as exc:                       # noqa: BLE001
        raise CounterUnavailable(f'Cannot read the policy library: {exc}') from exc


def omni_vendor_dpa_signed() -> int:
    """Vendors on the register WITH a signed data-processing agreement. Goes UP.

    A vendor row without the signed agreement is a list entry, not a control —
    the agreement is the thing that binds the processor.
    """
    from iso_compliance.models import VendorRegister
    try:
        return VendorRegister.objects.filter(dpa_contract_signed=True).count()
    except Exception as exc:                       # noqa: BLE001
        raise CounterUnavailable(f'Cannot read the vendor register: {exc}') from exc


def omni_dpia_unsigned() -> int:
    """DPIAs still awaiting a signature. Comes DOWN.

    Judged on the three signature flags, not on `status`: a status can be nudged
    to 'approved' by hand, whereas the DPO / compliance / CFO signatures are the
    actual sign-off the assessment needs. Closed assessments are excluded — a
    closed DPIA is finished business, not an outstanding one.
    """
    from iso_compliance.models import DPIA
    try:
        return (DPIA.objects
                .exclude(status=DPIA.STATUS_CLOSED)
                .exclude(dpo_signed=True, compliance_signed=True, cfo_signed=True)
                .count())
    except Exception as exc:                       # noqa: BLE001
        raise CounterUnavailable(f'Cannot read the DPIA register: {exc}') from exc


def omni_dsr_overdue() -> int:
    """Data-subject requests past the 30-day statutory clock. Target: nil."""
    from core.models import DataSubjectRequest
    try:
        today = _local_today()
        return (DataSubjectRequest.objects
                .exclude(status__in=[DataSubjectRequest.Status.COMPLETED,
                                     DataSubjectRequest.Status.REJECTED])
                .filter(due_date__lt=today)
                .count())
    except Exception as exc:                       # noqa: BLE001
        raise CounterUnavailable(f'Cannot read the data-subject requests: {exc}') from exc


def omni_breach_unnotified() -> int:
    """Reportable data breaches past 72 hours with the regulator not told. Nil.

    The 72-hour window is the whole point of the register. A breach discovered
    an hour ago is not yet a failure, so the clock is applied rather than simply
    counting un-notified rows.
    """
    import datetime as dt

    from django.utils import timezone as tz

    from core.models import BreachIncident
    try:
        cutoff = tz.now() - dt.timedelta(hours=72)
        return (BreachIncident.objects
                .filter(reportable=True, idpc_notified=False, discovered_at__lt=cutoff)
                .exclude(status=BreachIncident.Status.CLOSED)
                .count())
    except Exception as exc:                       # noqa: BLE001
        raise CounterUnavailable(f'Cannot read the breach register: {exc}') from exc


def omni_sop_ack_staff() -> int:
    """Active staff who have acknowledged at least one procedure document.

    Counts PEOPLE reached, not signatures collected. 229 documents against 160
    staff makes a raw signature count meaningless — 500 acknowledgements from
    ten keen people would look like progress while most of the company had read
    nothing.

    KNOWN LIMIT, stated rather than hidden: Omni has no mapping of which
    procedures apply to which role, so "at least one" is the strongest honest
    test available today. It moves in the right direction and cannot be gamed by
    one person signing everything. Tighten it once a role-to-procedure mapping
    exists.
    """
    from iso_compliance.models import SOPAcknowledgement
    try:
        return (SOPAcknowledgement.objects
                .values('user_id').distinct().count())
    except Exception as exc:                       # noqa: BLE001
        raise CounterUnavailable(f'Cannot read the SOP acknowledgements: {exc}') from exc


COUNTERS: Dict[str, Callable[[], int]] = {
    # Graphite — the live insurance book
    'gph_kyc_failed_active':           gph_kyc_failed_active,
    'gph_paid_not_issued':             gph_paid_not_issued,
    'gph_claims_paid_kyc_failed_7d':   gph_claims_paid_kyc_failed_7d,
    # Omni — supplier due diligence
    'omni_supplier_kyc_done':          omni_supplier_kyc_done,
    # Omni — AML/CFT and market-conduct registers
    'omni_risk_register_complete':     omni_risk_register_complete,
    'omni_sanctions_screened_total':   omni_sanctions_screened_total,
    'omni_sanctions_match_unreported': omni_sanctions_match_unreported,
    'omni_pep_unassessed':             omni_pep_unassessed,
    'omni_str_unfiled':                omni_str_unfiled,
    'omni_regulatory_breach_open':     omni_regulatory_breach_open,
    'omni_complaints_overdue':         omni_complaints_overdue,
    'omni_aml_training_outstanding':   omni_aml_training_outstanding,
    'omni_board_pack_outstanding':     omni_board_pack_outstanding,
    # Omni — the Data Protection Officer's registers
    'omni_ropa_confirmed':             omni_ropa_confirmed,
    'omni_policies_real':              omni_policies_real,
    'omni_vendor_dpa_signed':          omni_vendor_dpa_signed,
    'omni_dpia_unsigned':              omni_dpia_unsigned,
    'omni_dsr_overdue':                omni_dsr_overdue,
    'omni_breach_unnotified':          omni_breach_unnotified,
    'omni_sop_ack_staff':              omni_sop_ack_staff,
    'omni_dpo_pack_outstanding':       omni_dpo_pack_outstanding,
}


def read(counter: str) -> int:
    """Read one counter by registered name."""
    fn = COUNTERS.get(counter)
    if fn is None:
        raise CounterUnavailable(f'No counter registered as {counter!r}.')
    return fn()
