"""integrations/graphite_lookup_views.py — staff lookup of claims and policies in
Graphite V2, from the phone (CFO 2026-09-04: "create a space where we reuse graphite
aware understanding claim status, policy status … search by client name, claim
number, policy number").

Nothing new is invented here. It REUSES:
  * integrations.graphite_ro.query — the read-only replica door (SELECT-only, the
    host must be a replica, the session is read-only);
  * the schema knowledge in aware/engine.py (policies status 1/2/0; claim paid /
    outstanding from claim_reserves_coverages exactly as aware/modes/claims_registry
    computes them);
  * the same joins as integrations.claim_status_views.claim_lookup.

GRANT-AWARE (CFO 5-Sep-2026): the replica login (brain_ro) is granted COLUMN-wise on
`claims` — today id, claim_number, incident_date, created_at, reported_date,
claim_type, status, policy_id — and has no grant on v_policy_kyc_status. The first
live search died on a column it may not read. So this module ASKS the database
what the login may see (information_schema, cached 10 min) and adds the optional
detail — claim sub-status, type of loss, vehicle plate, the per-policy KYC view —
only when the grant exists. The base set always works; the moment TheRiskCo widens
the grant, the extra detail appears without a deploy.

Two reads, both authenticated staff only, never public:
  GET /api/v1/graphite/search/?q=      claims + policies matching a number or a name
  GET /api/v1/graphite/policy/?policy= one policy's card: status, term, premium,
                                       balance, KYC, its claims

PII: customer NAMES are returned (staff see them in Graphite anyway); no Omang,
phone, email, address or bank column is ever selected. The read-only QC/screenshot
identities are refused so a nightly screenshot never carries a client list.
"""
from __future__ import annotations

import re
import time

from django.db.models import Q

from rest_framework import status as http
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .graphite_ro import query

POLICY_STATUS = {1: 'Active', 2: 'Inactive', 0: 'Not activated'}
CLAIM_STATUS_LABEL = {
    'Pending': 'Received, under review', 'Open': 'Open, being worked on', 'Approved': 'Approved',
    'Reopen': 'Reopened', 'Rejected': 'Not approved', 'Closed': 'Closed',
}
FREQ = {1: 'Monthly', 3: 'Annual', 5: 'Quarterly'}
_NUMBERISH = re.compile(r'^[A-Za-z]{1,5}\d{4,}')   # G2026004594, COMG2025189299, MIS…


def _refuse_read_only_identity(request):
    prof = getattr(request.user, 'profile', None)
    if prof is not None and getattr(prof, 'is_read_only_identity', False):
        return Response({'detail': 'Client lookups are not available to the read-only QC identity.'},
                        status=http.HTTP_403_FORBIDDEN)
    return None


def _d(v):
    return str(v)[:10] if v else None


def _money(v):
    try:
        return f'{float(v):.2f}' if v is not None else None
    except (TypeError, ValueError):
        return None


def _policy_row(r: dict) -> dict:
    st = r.get('status')
    try:
        st = int(st) if st is not None else None
    except (TypeError, ValueError):
        pass
    name = (r.get('customer_name') or '').strip() or (r.get('business_name') or '').strip()
    return {
        'policy_number': r.get('policyNumber'),
        'customer_name': name,
        'business_name': (r.get('business_name') or '').strip(),
        'status': st,
        'status_label': POLICY_STATUS.get(st, str(st) if st is not None else 'Unknown'),
        'premium': _money(r.get('premium')),
        'annual_premium': _money(r.get('annual_premium')),
        'premium_freq': FREQ.get(r.get('premium_freq') if isinstance(r.get('premium_freq'), int) else
                                 (int(r['premium_freq']) if str(r.get('premium_freq') or '').isdigit() else None), ''),
        'term_start': _d(r.get('term_start_date')),
        'term_end': _d(r.get('term_end_date')),
        'product': (r.get('product_name') or '').strip(),
        'agent': (r.get('agent_name') or '').strip(),
        'broker': (r.get('broker') or '').strip(),
    }


def _claim_row(r: dict) -> dict:
    raw = (r.get('status') or '').strip()
    sub = (r.get('claim_sub_status') or '').strip()      # present only when granted
    return {
        'claim_number': r.get('claim_number'),
        'policy_number': r.get('policyNumber'),
        'customer_name': (r.get('customer_name') or '').strip(),
        'status': raw,
        'status_label': CLAIM_STATUS_LABEL.get(raw, raw or 'Unknown') + (f' · {sub}' if sub else ''),
        'claim_type': r.get('claim_type'),
        'type_of_loss': r.get('type_of_loss'),
        'vehicle_plate': r.get('vehicle_plate'),
        'reported': _d(r.get('reported_date') or r.get('created_at')),
        'date_of_loss': _d(r.get('incident_date')),
        # From claim_reserves_coverages (the payment/reserve ledger). None = the
        # ledger could not be read — "not in the data", never 0.
        'paid': _money(r.get('paid_amt')),
        'outstanding': _money(r.get('outstanding_amt')),
    }


_POLICY_SELECT = (
    "SELECT p.id AS policy_id, p.customer_id, p.policyNumber, p.status, p.premium, p.annual_premium, "
    "p.premium_freq, p.term_start_date, p.term_end_date, p.business_name, pr.name AS product_name, "
    "TRIM(CONCAT(COALESCE(cu.firstName,''),' ',COALESCE(cu.lastName,''))) AS customer_name, "
    "TRIM(CONCAT(COALESCE(u.firstName,''),' ',COALESCE(u.lastName,''))) AS agent_name, "
    "a.name AS broker "
    "FROM policies p "
    "LEFT JOIN customer cu ON p.customer_id = cu.id "
    "LEFT JOIN products pr ON p.product_id = pr.id "
    "LEFT JOIN users u ON p.agent_id = u.id "
    "LEFT JOIN agencies a ON p.agency_id = a.id "
)

# The columns the login is granted today (base set); anything else is optional.
CLAIM_BASE_COLUMNS = ('id', 'claim_number', 'status', 'claim_type', 'incident_date', 'reported_date', 'created_at', 'policy_id')
CLAIM_OPTIONAL_COLUMNS = ('claim_sub_status', 'type_of_loss', 'vehicle_plate')
_GRANT_TTL = 600
_grant_cache: dict = {'at': 0.0, 'tables': set(), 'columns': {}}


def _grants() -> tuple[set, dict]:
    """{tables with full SELECT}, {table: {granted columns}} for the replica login,
    from information_schema (readable by anyone). Cached 10 min per process; on
    any failure returns empty sets, so only the base columns are used."""
    now = time.time()
    if now - _grant_cache['at'] < _GRANT_TTL:
        return _grant_cache['tables'], _grant_cache['columns']
    tables, columns = set(), {}
    try:
        # information_schema.*_privileges list only what CURRENT_USER holds — no
        # grantee filter needed (a quoted one broke the statement on 5-Sep).
        for r in query("SELECT table_name FROM information_schema.table_privileges "
                       "WHERE privilege_type = 'SELECT'", [], limit=500):
            tables.add(str(r.get('table_name') or '').lower())
        for r in query("SELECT table_name, column_name FROM information_schema.column_privileges "
                       "WHERE privilege_type = 'SELECT'", [], limit=2000):
            columns.setdefault(str(r.get('table_name') or '').lower(), set()).add(str(r.get('column_name') or ''))
    except Exception:                                              # noqa: BLE001
        tables, columns = set(), {}
    _grant_cache.update(at=now, tables=tables, columns=columns)
    return tables, columns


def _may_read(table: str, column: str | None = None) -> bool:
    tables, columns = _grants()
    if table in tables:
        return True
    return column is not None and column in columns.get(table, set())


def _claim_select() -> str:
    """The claims SELECT: the base columns, plus each optional column the login is
    granted. Never a column it may not read — that is a live 502 for everyone."""
    extra = ''.join(f"c.{col}, " for col in CLAIM_OPTIONAL_COLUMNS if _may_read('claims', col))
    return (
        "SELECT c.id AS claim_id, c.claim_number, c.status, c.claim_type, c.incident_date, "
        f"c.reported_date, c.created_at, {extra}p.policyNumber, "
        "TRIM(CONCAT(COALESCE(cu.firstName,''),' ',COALESCE(cu.lastName,''))) AS customer_name "
        "FROM claims c "
        "LEFT JOIN policies p ON c.policy_id = p.id "
        "LEFT JOIN customer cu ON p.customer_id = cu.id "
    )


def _add_money(claims: list[dict]) -> list[dict]:
    """Paid so far and outstanding reserve per claim, from claim_reserves_coverages —
    the same arithmetic as aware/modes/claims_registry (paid = non-voided
    payment_amt; outstanding = the latest balance per coverage). A SEPARATE,
    guarded query: if the ledger cannot be read the figures show as "Not in the
    data" and the search still answers (live lesson, 5-Sep-2026)."""
    ids = [r.get('claim_id') for r in claims if r.get('claim_id') is not None]
    if not ids:
        return claims
    try:
        marks = ','.join(['%s'] * len(ids))
        extra = {x['claim_id']: x for x in query(
            "SELECT pd.claim_id, pd.paid AS paid_amt, COALESCE(os.os, 0) AS outstanding_amt FROM "
            "(SELECT claim_id, SUM(CASE WHEN is_payment_voided=0 THEN COALESCE(payment_amt,0) ELSE 0 END) paid "
            f"FROM claim_reserves_coverages WHERE claim_id IN ({marks}) GROUP BY claim_id) pd "
            "LEFT JOIN (SELECT latest.claim_id, SUM(latest.balance) os FROM ("
            "SELECT crc.claim_id, crc.balance FROM claim_reserves_coverages crc "
            "JOIN (SELECT claim_id, coverage_id, MAX(id) mid FROM claim_reserves_coverages "
            f"WHERE claim_id IN ({marks}) GROUP BY claim_id, coverage_id) m ON m.mid = crc.id) latest "
            "GROUP BY latest.claim_id) os ON os.claim_id = pd.claim_id", ids + ids)}
    except Exception:                                              # noqa: BLE001
        extra = {}
    for r in claims:
        e = extra.get(r.get('claim_id')) or {}
        r.setdefault('paid_amt', e.get('paid_amt'))
        r.setdefault('outstanding_amt', e.get('outstanding_amt'))
    return claims


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def graphite_search(request):
    """GET /api/v1/graphite/search/?q=<claim no | policy no | client name>"""
    refused = _refuse_read_only_identity(request)
    if refused:
        return refused
    q = (request.GET.get('q') or '').strip()
    if len(q) < 3:
        return Response({'detail': 'Type at least 3 characters.'}, status=http.HTTP_400_BAD_REQUEST)
    like = f'%{q}%'
    try:
        if _NUMBERISH.match(q):
            # A number: match the number columns first, exactly-ish.
            claims = query(_claim_select() + "WHERE c.claim_number LIKE %s OR p.policyNumber LIKE %s "
                           "ORDER BY c.id DESC LIMIT 20", [like, like])
            policies = query(_POLICY_SELECT + "WHERE p.policyNumber LIKE %s ORDER BY p.id DESC LIMIT 20", [like])
        else:
            # A name: person or business, on both claims and policies.
            claims = query(_claim_select() +
                           "WHERE CONCAT(COALESCE(cu.firstName,''),' ',COALESCE(cu.lastName,'')) LIKE %s "
                           "OR p.business_name LIKE %s ORDER BY c.id DESC LIMIT 20", [like, like])
            policies = query(_POLICY_SELECT +
                             "WHERE CONCAT(COALESCE(cu.firstName,''),' ',COALESCE(cu.lastName,'')) LIKE %s "
                             "OR p.business_name LIKE %s ORDER BY p.id DESC LIMIT 20", [like, like])
    except Exception:                                              # noqa: BLE001
        return Response({'detail': 'Graphite did not answer. Try again in a moment.'},
                        status=http.HTTP_502_BAD_GATEWAY)
    return Response({'q': q, 'claims': [_claim_row(r) for r in _add_money([dict(r) for r in claims])],
                     'policies': [_policy_row(r) for r in policies]})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def graphite_policy(request):
    """GET /api/v1/graphite/policy/?policy=<number> — one policy's card."""
    refused = _refuse_read_only_identity(request)
    if refused:
        return refused
    pol = (request.GET.get('policy') or '').strip()
    if not pol:
        return Response({'detail': 'A "policy" query parameter is required.'}, status=http.HTTP_400_BAD_REQUEST)
    try:
        rows = query(_POLICY_SELECT + "WHERE p.policyNumber = %s LIMIT 1", [pol])
        if not rows:
            return Response({'detail': 'No policy with that number.'}, status=http.HTTP_404_NOT_FOUND)
        card = _policy_row(rows[0])
        claims = query(_claim_select() + "WHERE p.policyNumber = %s ORDER BY c.id DESC LIMIT 10", [pol])
        card['claims'] = [_claim_row(r) for r in _add_money([dict(r) for r in claims])]
        try:
            if _may_read('v_policy_kyc_status'):
                kyc = query("SELECT kyc_status AS status FROM v_policy_kyc_status WHERE policyNumber = %s LIMIT 1", [pol])
            else:
                kyc = query("SELECT status FROM brain_customer_kyc WHERE customer_id = %s ORDER BY id DESC LIMIT 1",
                            [rows[0].get('customer_id')])
            card['kyc_status'] = (kyc[0].get('status') if kyc else None)
        except Exception:                                          # noqa: BLE001
            card['kyc_status'] = None
        try:
            bal = query("SELECT balance FROM policies WHERE policyNumber = %s LIMIT 1", [pol])
            card['balance'] = _money(bal[0].get('balance')) if bal else None
        except Exception:                                          # noqa: BLE001
            card['balance'] = None
    except Exception:                                              # noqa: BLE001
        return Response({'detail': 'Graphite did not answer. Try again in a moment.'},
                        status=http.HTTP_502_BAD_GATEWAY)
    return Response(card)


# ---------------------------------------------------------------------------
# Claim brief — "ask DeepSeek about this claim" (CFO 5-Sep-2026: "a deepseek
# connector … deep analysis and give 200 word feedback of a claim like
# /largepayment")
# ---------------------------------------------------------------------------
# What leaves Omni: claim/policy status, dates, money, coverage lines, Omni
# payment requests for the claim, and the staff member's question. Individual
# names are reduced to initials (Aware's rule), business names stay, and the
# whole prompt passes core.ai_assist.is_safe_for_ai() before it is sent — the
# same guard the rest of Omni's DeepSeek carve-out relies on. Money is written
# as whole pula without separators so the PII sweep does not eat the figures.
BRIEF_SYSTEM = (
    "You are a senior short-term insurance claims assessor at Alpha Direct Insurance, "
    "Botswana, briefing the CFO before he authorises a claim payment in the bank. "
    "Use ONLY the facts given. Never invent a fact; where the data is missing say "
    "'not in the data'. Currency is BWP. Write about 200 words, plain English, in "
    "this order with short bold headings: Claim · Insured & cover · Loss · Money "
    "(reserve, paid so far, still reserved, payments requested in Omni) · Flags "
    "(anything odd: paid above reserve, duplicate requests, closed/rejected but a "
    "payment pending, policy inactive or in arrears, KYC not compliant, payee not "
    "the insured) · Recommendation (PAY / HOLD / QUERY, one line why). If the reader "
    "asked a question, answer it inside the brief. No preamble, no sign-off."
)
BRIEF_MAX_WORDS = 230


def _initials(name: str) -> str:
    parts = [w for w in (name or '').replace('.', ' ').split() if w]
    return ' '.join(f'{w[0].upper()}.' for w in parts) if parts else ''


def _pula(v) -> str:
    """Whole pula, no separators: 'BWP 12500'. is_safe_for_ai redacts figures with
    two-plus thousand separators or 5+ digits with decimals — this shape survives."""
    try:
        return f'BWP {int(round(float(v)))}' if v is not None else 'not in the data'
    except (TypeError, ValueError):
        return 'not in the data'


def _cap_words(text: str, limit: int = BRIEF_MAX_WORDS) -> str:
    words = (text or '').split()
    if len(words) <= limit:
        return (text or '').strip()
    cut = ' '.join(words[:limit])
    dot = max(cut.rfind('. '), cut.rfind('.\n'))
    return (cut[:dot + 1] if dot > len(cut) * 0.6 else cut).strip()


def _claim_facts(claim_number: str) -> dict | None:
    """Everything the brief may use, from the replica (granted columns only) and
    from Omni's own payment requests."""
    from taskboard.models import PaymentRequest

    rows = query(_claim_select() + "WHERE c.claim_number = %s ORDER BY c.id DESC LIMIT 1", [claim_number])
    if not rows:
        return None
    c = dict(rows[0])
    _add_money([c])
    claim = _claim_row(c)

    policy, other_claims, kyc, coverages = None, [], None, []
    if c.get('policyNumber'):
        prow = query(_POLICY_SELECT + "WHERE p.policyNumber = %s LIMIT 1", [c['policyNumber']])
        if prow:
            policy = _policy_row(prow[0])
            try:
                bal = query("SELECT balance FROM policies WHERE policyNumber = %s LIMIT 1", [c['policyNumber']])
                policy['balance'] = _money(bal[0].get('balance')) if bal else None
            except Exception:                                      # noqa: BLE001
                policy['balance'] = None
            try:
                if _may_read('v_policy_kyc_status'):
                    k = query("SELECT kyc_status AS status FROM v_policy_kyc_status WHERE policyNumber = %s LIMIT 1", [c['policyNumber']])
                else:
                    k = query("SELECT status FROM brain_customer_kyc WHERE customer_id = %s ORDER BY id DESC LIMIT 1",
                              [prow[0].get('customer_id')])
                kyc = k[0].get('status') if k else None
            except Exception:                                      # noqa: BLE001
                kyc = None
            try:
                others = query(_claim_select() + "WHERE p.policyNumber = %s AND c.claim_number <> %s "
                               "ORDER BY c.id DESC LIMIT 10", [c['policyNumber'], claim_number])
                other_claims = [{'claim_number': o.get('claim_number'), 'status': o.get('status'),
                                 'reported': _d(o.get('reported_date') or o.get('created_at'))} for o in others]
            except Exception:                                      # noqa: BLE001
                other_claims = []
    try:
        coverages = [dict(r) for r in query(
            "SELECT coverage_name, reserve_amt, payment_amt, balance, write_off, is_payment_voided, created_at "
            "FROM claim_reserves_coverages WHERE claim_id = %s ORDER BY id DESC LIMIT 30", [c.get('claim_id')])]
    except Exception:                                              # noqa: BLE001
        coverages = []

    requests = []
    for pr in (PaymentRequest.objects
               .filter(category=PaymentRequest.Category.CLAIM)
               .exclude(status=PaymentRequest.Status.DRAFT)
               .filter(Q(ref__icontains=claim_number) | Q(graphite_ref__icontains=claim_number)
                       | Q(subject__icontains=claim_number))
               .order_by('-created_at')[:10]):
        payee = pr.payee or ''
        if (pr.claim_payee_type or '') == 'client':
            payee = _initials(payee)                 # a person; a repairer/provider stays
        requests.append({'ref': pr.ref, 'payee': payee, 'amount': str(pr.total), 'status': pr.status,
                         'raised': timezone_date(pr.created_at), 'subject': (pr.subject or '')[:80],
                         'fnb': bool(getattr(pr, 'fnb_batch_id', None))})
    return {'claim': claim, 'policy': policy, 'kyc': kyc, 'other_claims': other_claims,
            'coverages': coverages, 'requests': requests}


def timezone_date(dt) -> str | None:
    from django.utils import timezone as _tz
    return _tz.localtime(dt).date().isoformat() if dt else None


def _brief_prompt(facts: dict, question: str) -> str:
    cl, po = facts['claim'], facts['policy'] or {}
    insured = _initials(po.get('customer_name') or cl.get('customer_name') or '')
    business = po.get('business_name') or ''
    lines = [
        f"CLAIM {cl['claim_number']} · status {cl['status_label']} · type {cl.get('claim_type') or 'not in the data'}"
        + (f" · loss {cl['type_of_loss']}" if cl.get('type_of_loss') else '')
        + (f" · vehicle {cl['vehicle_plate']}" if cl.get('vehicle_plate') else ''),
        f"Reported {cl.get('reported') or 'not in the data'} · date of loss {cl.get('date_of_loss') or 'not in the data'}",
        f"Paid so far {_pula(cl.get('paid'))} · still reserved {_pula(cl.get('outstanding'))}",
        f"POLICY {po.get('policy_number') or 'not in the data'} · {po.get('status_label', 'not in the data')} · "
        f"product {po.get('product') or 'not in the data'} · premium {_pula(po.get('premium'))} {po.get('premium_freq') or ''} · "
        f"term {po.get('term_start') or '?'} to {po.get('term_end') or '?'} · balance owing {_pula(po.get('balance'))}",
        f"Insured {business or insured or 'not in the data'}" + (f" (contact {insured})" if business and insured else ''),
        f"Broker/agent {po.get('broker') or po.get('agent') or 'direct'} · KYC {facts.get('kyc') or 'not recorded'}",
    ]
    if facts['coverages']:
        lines.append("COVERAGE LINES (latest first):")
        for r in facts['coverages'][:12]:
            lines.append(f" - {r.get('coverage_name') or 'cover'}: reserve {_pula(r.get('reserve_amt'))}, "
                         f"paid {_pula(r.get('payment_amt'))}{' (VOIDED)' if r.get('is_payment_voided') else ''}, "
                         f"balance {_pula(r.get('balance'))}, write-off {_pula(r.get('write_off'))}")
    else:
        lines.append("COVERAGE LINES: none in the data")
    if facts['requests']:
        lines.append("OMNI PAYMENT REQUESTS FOR THIS CLAIM:")
        for r in facts['requests']:
            lines.append(f" - {r['ref']} · {r['payee'] or 'payee not given'} · {_pula(r['amount'])} · {r['status']} · raised {r['raised']}"
                         + (' · loaded to FNB' if r['fnb'] else ''))
    else:
        lines.append("OMNI PAYMENT REQUESTS FOR THIS CLAIM: none")
    if facts['other_claims']:
        lines.append("OTHER CLAIMS ON THIS POLICY: " + '; '.join(
            f"{o['claim_number']} {o['status'] or '?'} ({o['reported'] or '?'})" for o in facts['other_claims']))
    else:
        lines.append("OTHER CLAIMS ON THIS POLICY: none")
    if question:
        lines.append(f"QUESTION FROM THE READER: {question}")
    return '\n'.join(lines)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def graphite_claim_brief(request):
    """POST /api/v1/graphite/claim-brief/  {claim_number, question?}
    → {brief, engine, words, facts}. Read-only; the model sees initials, not names."""
    refused = _refuse_read_only_identity(request)
    if refused:
        return refused
    body = request.data if isinstance(request.data, dict) else {}
    claim_number = str(body.get('claim_number') or '').strip()
    question = str(body.get('question') or '').strip()[:400]
    if not claim_number:
        return Response({'detail': 'claim_number is required.'}, status=http.HTTP_400_BAD_REQUEST)
    try:
        facts = _claim_facts(claim_number)
    except Exception:                                              # noqa: BLE001
        return Response({'detail': 'Graphite did not answer. Try again in a moment.'},
                        status=http.HTTP_502_BAD_GATEWAY)
    if facts is None:
        return Response({'detail': 'No claim with that number.'}, status=http.HTTP_404_NOT_FOUND)

    from core.ai_assist import DeepSeekUnavailable, deepseek_complete, is_safe_for_ai, reasoning_complete
    prompt = _brief_prompt(facts, question)
    report = is_safe_for_ai(prompt)
    if not report.safe:
        return Response({'detail': 'The claim facts could not be cleared for the AI review.', 'notes': report.notes},
                        status=http.HTTP_400_BAD_REQUEST)
    engine = 'DeepSeek'
    try:
        text = deepseek_complete(report.redacted_text, system_prompt=BRIEF_SYSTEM, timeout=45.0, max_tokens=600)
    except DeepSeekUnavailable:
        try:
            text = reasoning_complete(report.redacted_text, system_prompt=BRIEF_SYSTEM, timeout=45.0,
                                      max_tokens=600, feature='claim_brief')
            engine = 'backup engine'
        except Exception:                                          # noqa: BLE001
            return Response({'detail': 'The AI reviewer is not answering right now. The facts below are still good.',
                             'facts': facts}, status=http.HTTP_503_SERVICE_UNAVAILABLE)
    except Exception:                                              # noqa: BLE001
        log_brief_failure = __import__('logging').getLogger(__name__)
        log_brief_failure.exception('claim brief failed')
        return Response({'detail': 'The AI reviewer failed. Try again.'}, status=http.HTTP_502_BAD_GATEWAY)
    brief = _cap_words(text)
    return Response({'brief': brief, 'engine': engine, 'words': len(brief.split()), 'facts': facts})
