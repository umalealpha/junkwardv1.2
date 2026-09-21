"""Alpha Aware engine — natural-language Q&A over the Graphite V2 read replica.

How it works (agentic text-to-SQL, read-only):
  question → LLM (reasoning_complete: Gemini → DeepSeek chain) plans a SELECT
  → we run it on the Graphite READ REPLICA (SELECT-only, guarded, LIMITed)
  → results (PII-masked) go back to the LLM → it either asks for another
  query (up to MAX_ROUNDS) or writes the final answer.

Safety:
  * Connection = the same read-replica creds as integrations/graphite_age.py;
    session forced READ ONLY and the -rpro endpoint rejects writes anyway.
  * SQL gate: single statement, must start with SELECT, destructive keywords
    rejected, LIMIT enforced.
  * PII mask before anything leaves for the LLM: ID/passport/bank/phone/
    email/address/DOB columns are redacted; person names are reduced to
    initials (business names pass — they are entities, not personal data).
  * Every question + every SQL round is audit-logged (AwareQuery).
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Tuple

from django.conf import settings

log = logging.getLogger(__name__)

MAX_ROUNDS = 4
ROW_CAP = 60          # rows fed back to the LLM per query
CELL_CAP = 160        # chars per cell fed to the LLM
HARD_LIMIT = 200      # LIMIT appended to every SQL

# ---------------------------------------------------------------------------
# Schema guide — curated working knowledge of Graphite_live (verified live,
# Jun 2026). This is what makes the agent query CORRECTLY first time.
# ---------------------------------------------------------------------------
SCHEMA_GUIDE = """
You are Graphite Aware, the executive data assistant for Alpha Direct Insurance
(Botswana). You answer questions by querying the Graphite V2 insurance
database (MariaDB, read-only replica). Currency is BWP (Pula).

KEY TABLES (verified):
* policies — one row per policy, CURRENT TERM ONLY. Cols: id, policyNumber
  (prefixes: MIS=retail store products ~206k, DOMG=domestic ~3.4k,
  COMG=commercial ~3.3k), customer_id, agent_id (FK users.id), product_id,
  premium, annual_premium, premium_freq (1=monthly,3=annual,5=quarterly),
  status (1=active, 2=inactive/other, 0=not activated), term_start_date,
  term_end_date, created_at (=policy inception, NOT the booking date),
  business_name, sum_assured (usually NULL — see SI rule).
* policy_actions — THE transaction history (the heart). One row per
  transaction: id, policy_id, premium, annual_premium, transaction_type
  (NEWBUSINESS/RENEW/ANNIVERSARY-RENEW/ENDORSE/CANCEL/REINSTATE),
  effective_from, effective_to, transaction_date (= the real booking date),
  status ('ISSUED' = valid), created_at. WARNING: re-rating writes NEW rows
  for the SAME period — the table keeps superseded duplicates. For written
  premium over a window, sum rows in the window but know CANCEL rows are
  negative and re-rates can double-count; say so if precision matters.
* claims — 4.1k rows: id, policy_id, claim_number (G<year><seq>), claim_type,
  status (Closed/Pending/Approved/Reopen/Rejected/blank), claim_allocated_to
  (handler user id, mostly NULL), incident_description, type_of_loss,
  vehicle_plate, reported_date, created_at, created_by. NOTE: incident_date
  is NULL on all rows — use created_at.
* new_claims — newer/partial claims table (2.3k): policy_id, policyNumber,
  claim_number, reserve_amount, paid_amount, date_of_loss,
  description_of_loss, status. Claims may exist in `claims` but NOT here
  (sync gap) — check BOTH tables when hunting a claim. THIS is the ONLY
  table with reserve_amount / paid_amount (see RESERVE & PAID rule below).
* claim_edit_log — field-change audit for claims (claim_id, field,
  old_value, new_value, changed_by_name, created_at).
* claim_reserves — claim PAYMENT/disbursement ledger (join by claim_id):
  id, date, transaction_type, transaction_sub_type, payee, invoice_no,
  invoice_date, invoice_due_date, memo, description, include_vat,
  credit_note, product_id. Despite the name it has NO reserve_amount and
  NO single paid figure — never SELECT reserve_amount/paid_amount from it.
* claim_assessment — assessment details (join by claim_id): assessor_id,
  assessment_report, quotations_parts, valuation, notes, assessor_category.
  No paid_amount / reserve_amount column here.
* claim_review_notes — claims-team notes (claim_id, title, note,
  created_by_name, created_at).
* RESERVE & PAID rule (critical): reserve_amount and paid_amount live ONLY
  on new_claims — look them up there by claim_number or policy_id. A claim
  in `claims` may be ABSENT from new_claims (sync gap); if so, those figures
  are simply not in the data — say the reserve/paid is not available. Do NOT
  join claim_reserves or claim_assessment to get them (those columns do not
  exist and the query will error).
* customers — personal data; select ONLY id and initials-safe fields. Never
  select omang/id numbers, phone, email, address, bank columns.
* users — staff/agents: id, firstName, lastName, email, active. Agent name
  = CONCAT(firstName,' ',lastName) via policies.agent_id.
* vehicle — insured vehicles: policy_id, action_id, estimated_value, make,
  model, year, vehiclePlate, deleted_at. History is duplicated per renewal:
  the CURRENT vehicles = rows where action_id = (SELECT MAX(action_id) FROM
  vehicle v2 WHERE v2.policy_id = X AND v2.deleted_at IS NULL).
* policy_coverages (sections per action: id, policy_id, action_id,
  coverage_id, deleted_at) + policy_coverage_detail (policy_coverage_id,
  coverage_value = the section SUM INSURED, calculated_value = the section
  premium, rate).
* SUM INSURED RULE (critical): SI is NOT one field. SI(policy) =
  (SUM of policy_coverage_detail.coverage_value for the LATEST
   policy_coverages.action_id of that policy that HAS detail rows)
  + (SUM of vehicle.estimated_value at the latest vehicle action_id).
  Do NOT sum across all actions (re-rates multiply it); do NOT use
  mis_premium_data.totalSumInsured (garbage).
* policy_ledger — invoices/payments per policy. TRUE DEBTOR BALANCE =
  SUM(invoice headers) − (payments − reversed payments); the ledger also
  holds per-line VAT/premium rows, so naive SUM double-counts.
* summary_age_analyst_report_dom_com_2025 — pre-aggregated debtor aging
  (policyNumber, client_name, agent_name, invoice_total, payment_total,
  balance_outstanding, 30/60/90/120+ buckets, product_name, policy_status).
  Prefer this for "who owes us" questions.
* GetPoliciesDetailsView — handy view: policyNumber, customerName,
  agentName, productName, premium, policyStatus, premium_freq.
* mis_premium_data — reinsurance splits per action (actionId): netretention,
  quotasharing, surplus, facultative (+ _si columns). Use for RI splits
  only, never for SI totals.
* D_PREMIUM_PAYMENTS / v_policy_payments / payment_transactions — money
  received per policy.

RULES:
1. MariaDB syntax. SELECT only. Always LIMIT ≤ 200. One statement per round.
2. Never SELECT personal contact/ID columns (omang, id_number, passport,
   phone, mobile, email, address, bank, account_no, dob).
3. Business/company names are fine. Individual customer names will be
   masked to initials before you see them — that is expected; refer to
   customers by policy or claim number.
4. Dates: 'YYYY-MM-DD'. Financial year runs 1 Jul → 30 Jun.
5. If a first query returns nothing useful, refine and try again (you have
   up to 4 query rounds). Prefer 2 precise queries over 1 giant one.
6. Answer like a sharp analyst briefing the CFO: lead with the answer,
   thousands separators, BWP, short bullets, flag data-quality caveats
   (re-rate duplicates, missing SI, claims table sync gap) when they
   affect the number. Never invent data — if the tables don't have it,
   say so plainly.

RESPONSE PROTOCOL (strict): reply with ONE JSON object only, no prose
around it:
  {"action":"sql","sql":"SELECT ...","why":"one line"}     — to run a query
  {"action":"answer","text":"final answer, markdown"}       — when done
"""

# ---------------------------------------------------------------------------
# SQL guard + execution (read replica)
# ---------------------------------------------------------------------------
# Non-word boundaries (underscore-safe): catches LOAD_FILE, SLEEP, etc. that
# a plain \b lets through because '_' is a word char.
_FORBIDDEN = re.compile(
    r'(?<![A-Za-z0-9_])('
    r'INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|GRANT|REVOKE|TRUNCATE|'
    r'REPLACE|RENAME|LOCK|UNLOCK|CALL|HANDLER|LOAD|OUTFILE|DUMPFILE|'
    r'INTO\s+OUTFILE|FOR\s+UPDATE|SET\s+|LOAD_FILE|BENCHMARK|SLEEP|'
    r'GET_LOCK|RELEASE_LOCK|IS_FREE_LOCK|SYS_EXEC|INFORMATION_SCHEMA|'
    r'PERFORMANCE_SCHEMA|MYSQL\.)(?![A-Za-z0-9_])', re.I)

# PII source columns — the LLM/chat path (run_select) must NEVER select these,
# by any alias. Fail CLOSED: reject the query outright. (The KYC mode reads
# omang via the separate kyc_select() channel, which never reaches the LLM.)
_PII_COLS = re.compile(
    r'(?<![A-Za-z0-9_])('
    r'omang|passport|national_?id|id_number|idnumber|cellphone|phone|mobile|'
    r'msisdn|email|residential|postal|address|bank|account_?no|account_number|'
    r'iban|swift|dob|date_of_birth|birth|driver|licen[cs]e|next_of_kin|'
    r'beneficiary|selfie|id_front|id_back|omangnumber|passportnumber)'
    r'(?![A-Za-z0-9_])', re.I)


def _guard_sql(sql: str) -> str:
    s = (sql or '').strip().rstrip(';').strip()
    if not re.match(r'^SELECT\b', s, re.I):
        raise ValueError('Only SELECT statements are allowed.')
    if ';' in s:
        raise ValueError('One statement only.')
    # SECURITY (2026-07-17 audit): reject wildcard column lists. `SELECT *` /
    # `SELECT tbl.*` name no columns, so the _PII_COLS guard below can't see
    # them and PII columns would slip through to the LLM. Require explicit
    # columns. COUNT(*) etc. are still allowed (the '*' is inside parentheses).
    if re.match(r'^SELECT\s+(DISTINCT\s+)?\*', s, re.I) or re.search(r'\w+\.\*', s):
        raise ValueError('SELECT * is not allowed — list explicit columns.')
    if _FORBIDDEN.search(s):
        raise ValueError('Statement contains a forbidden keyword.')
    if _PII_COLS.search(s):
        raise ValueError('Query references a personal-data column and was blocked.')
    if not re.search(r'\bLIMIT\s+\d+', s, re.I):
        s += f' LIMIT {HARD_LIMIT}'
    else:
        s = re.sub(r'\bLIMIT\s+(\d+)',
                   lambda m: f'LIMIT {min(int(m.group(1)), HARD_LIMIT)}', s, flags=re.I)
    return s


def _connect():
    import pymysql
    host = getattr(settings, 'GRAPHITE_RO_DB_HOST', '') or ''
    if not host:
        raise RuntimeError('Graphite read replica not configured (GRAPHITE_RO_DB_HOST).')
    return pymysql.connect(
        host=host,
        port=int(getattr(settings, 'GRAPHITE_RO_DB_PORT', 3306) or 3306),
        user=getattr(settings, 'GRAPHITE_RO_DB_USER', '') or '',
        password=getattr(settings, 'GRAPHITE_RO_DB_PASSWORD', '') or '',
        database=getattr(settings, 'GRAPHITE_RO_DB_NAME', 'Graphite_live') or 'Graphite_live',
        connect_timeout=15, read_timeout=25,
        cursorclass=pymysql.cursors.DictCursor, charset='utf8mb4',
        init_command='SET SESSION TRANSACTION READ ONLY',
    )


def run_select(sql: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    guarded = _guard_sql(sql)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(guarded)
            rows = list(cur.fetchall())
        cols = list(rows[0].keys()) if rows else []
        return cols, rows
    finally:
        conn.close()


def run_select_params(sql: str, params: List[Any]) -> List[Dict[str, Any]]:
    """Parametrised SELECT behind the SAME fail-closed PII guard as run_select.

    For callers that need placeholders (e.g. a policy-number lookup fed by user
    input) but must stay behind the PII-column block — unlike kyc_select, which
    is the separate KYC-only raw channel. _guard_sql still rejects any PII column,
    forbidden keyword, multiple statements or SELECT *, and caps the LIMIT.
    """
    guarded = _guard_sql(sql)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(guarded, params)
            return list(cur.fetchall())
    finally:
        conn.close()


def kyc_select(sql: str, params: List[Any]) -> List[Dict[str, Any]]:
    """DPA-safe channel for the KYC mode ONLY. Parametrised, read-only, and the
    result is returned STRAIGHT to Python — it never passes through mask_rows,
    never reaches the LLM, never enters an API response un-reduced. Used to
    compare an on-record Omang/passport to the OCR'd upload server-side; only a
    match/no-match boolean is surfaced. SELECT-guarded here (NOT via _guard_sql,
    which fails-closed on PII columns — this is the ONE sanctioned channel that
    is allowed to read them, because the value never leaves Python)."""
    s = (sql or '').strip().rstrip(';')
    if not re.match(r'^SELECT\b', s, re.I) or _FORBIDDEN.search(s):
        raise ValueError('kyc_select: SELECT-only.')
    if not re.search(r'\bLIMIT\s+\d+', s, re.I):
        s += ' LIMIT 5'
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(s, params)
            return list(cur.fetchall())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PII masking (before results reach the LLM)
# ---------------------------------------------------------------------------
# SECURITY (2026-07-17 audit): kept a SUPERSET of _PII_COLS so that if a PII
# column ever reaches results (e.g. via an alias), it is still masked. Any
# column added to _PII_COLS above must also be reflected here.
_REDACT_COL = re.compile(
    r'(omang|national_?id|id_number|idnumber|passport|passportnumber|omangnumber|'
    r'phone|mobile|cellphone|msisdn|contact|email|residential|address|postal|'
    r'bank|account_?no|accountno|account_number|iban|swift|dob|date_of_birth|'
    r'birth|driver|licen[cs]e|next_of_kin|beneficiary|selfie|id_front|id_back)', re.I)
_NAME_COL = re.compile(
    r'(customer.?name|client.?name|insured|full.?name|firstname|lastname|'
    r'sender|receiver|claimant|holder)', re.I)
_BIZ = re.compile(
    r'\b(PTY|LTD|LIMITED|ENTERPRISES|INVESTMENTS|HOLDINGS|COMPANY|CC|INC|'
    r'CHURCH|SCHOOL|COUNCIL|CLINIC|PHARMA|MOTORS|GROUP|FARM|STORES?|'
    r'DISTRIBUTION|LOGISTICS|COURIERS?|T/A)\b', re.I)

# Content-level PII scrub — catches inline PII in FREE-TEXT columns
# (e.g. claims.incident_description) that column-name masking can't see.
# Targeted to avoid nuking legit numbers (amounts have decimals/commas;
# policy numbers carry a prefix; a bare 9-digit run is an Omang).
_PII_CONTENT = [
    (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), '▇@▇'),                 # email
    (re.compile(r'(?<!\d)(?:\+?267[\s-]?)?[74]\d(?:[\s-]?\d){6}(?!\d)'), '▇▇▇'),  # BW phone
    (re.compile(r'(?<![\d,.])\d{9}(?![\d,.])'), '▇▇▇'),              # Omang (bare 9-digit)
]


def _scrub_content(v: str) -> str:
    for pat, repl in _PII_CONTENT:
        v = pat.sub(repl, v)
    return v


def _mask_value(col: str, val: Any) -> Any:
    if val is None or not isinstance(val, str):
        if _REDACT_COL.search(col):
            return '▇▇▇'
        return val
    if _REDACT_COL.search(col):
        return '▇▇▇'
    if _NAME_COL.search(col):
        v = val.strip()
        if not v or _BIZ.search(v):
            return _scrub_content(v)  # business entity — allowed, still scrub
        parts = [p for p in re.split(r'\s+', v) if p]
        return '. '.join(p[0].upper() for p in parts) + '.' if parts else v
    return _scrub_content(val)


def mask_rows(cols: List[str], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in rows[:ROW_CAP]:
        m = {}
        for c in cols:
            v = _mask_value(c, r.get(c))
            if isinstance(v, str) and len(v) > CELL_CAP:
                v = v[:CELL_CAP] + '…'
            m[c] = v
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# The agent loop
# ---------------------------------------------------------------------------
def _extract_json_obj(t: str) -> str | None:
    """Return the first complete top-level {...} by brace-balancing (ignores
    braces inside strings). Robust to trailing prose / code fences / commentary
    the LLM adds around the object."""
    start = t.find('{')
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return t[start:i + 1]
    return None


def _parse_step(raw: str) -> Dict[str, Any]:
    """LLM must return one JSON object; tolerate fenced/dirty output."""
    t = (raw or '').strip()
    t = re.sub(r'^```(?:json)?\s*|\s*```$', '', t, flags=re.S).strip()
    blob = _extract_json_obj(t)
    if blob:
        try:
            return json.loads(blob)
        except Exception:
            # last-ditch: collapse literal newlines inside the object
            try:
                return json.loads(re.sub(r'(?<!\\)\n', ' ', blob))
            except Exception:
                pass
    return {'action': 'answer', 'text': t or 'No answer produced.'}


def ask(question: str, user, file_texts: List[str] = None, email_text: str = '') -> Dict[str, Any]:
    from core.ai_assist import reasoning_complete
    from .models import AwareQuery

    t0 = time.time()
    transcript = f'QUESTION from an Alpha Direct executive: {question.strip()}'
    attached = [t for t in (file_texts or []) if t and t.strip()]
    if attached:
        joined = '\n\n---\n\n'.join(a[:6000] for a in attached)
        transcript += ('\n\nThe user ATTACHED document(s). Extracted text follows '
                       '(use it + query Graphite to answer / cross-check policy '
                       f'numbers found in it):\n{joined[:14000]}')
    if email_text and email_text.strip():
        transcript += f'\n\nThe user PASTED an email:\n{email_text.strip()[:6000]}'
    rounds: List[Dict[str, Any]] = []
    answer, ok = '', True

    for i in range(MAX_ROUNDS + 1):
        force = i == MAX_ROUNDS
        prompt = transcript + (
            '\n\nYou have used all query rounds. Reply now with '
            '{"action":"answer","text":"..."} using what you have.' if force else '')
        try:
            raw = reasoning_complete(prompt, system_prompt=SCHEMA_GUIDE, timeout=60.0)
        except Exception as e:
            ok, answer = False, f'The reasoning engine is unavailable right now ({e}).'
            break
        step = _parse_step(raw)

        if step.get('action') == 'sql' and not force:
            sql = str(step.get('sql') or '')
            t1 = time.time()
            try:
                cols, rows = run_select(sql)
                masked = mask_rows(cols, rows)
                result_txt = json.dumps(masked, default=str)[:6000]
                rounds.append({'sql': sql, 'rows': len(rows),
                               'ms': int((time.time() - t1) * 1000)})
                transcript += (
                    f'\n\nROUND {i + 1} SQL: {sql}\n'
                    f'RESULT ({len(rows)} rows, first {min(len(rows), ROW_CAP)} shown, '
                    f'personal names masked to initials): {result_txt}\n'
                    'Continue: another {"action":"sql"} if needed, or '
                    '{"action":"answer"}.')
            except Exception as e:
                rounds.append({'sql': sql, 'error': str(e)[:300]})
                transcript += (f'\n\nROUND {i + 1} SQL FAILED: {e}. '
                               'Fix the query or answer with what you have.')
            continue

        answer = str(step.get('text') or '').strip() or 'No answer produced.'
        break
    else:  # pragma: no cover
        answer = 'Could not complete within the query budget.'

    duration = int((time.time() - t0) * 1000)
    try:
        AwareQuery.objects.create(user=user, question=question[:2000],
                                  rounds=rounds, answer=answer[:20000],
                                  ok=ok, duration_ms=duration)
    except Exception:
        log.exception('AwareQuery audit write failed')
    return {'answer': answer, 'rounds': rounds, 'ok': ok, 'duration_ms': duration}
