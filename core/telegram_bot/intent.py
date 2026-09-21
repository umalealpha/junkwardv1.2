"""
core/telegram_bot/intent.py

Natural-language intent parser for the CFO bot.

Strategy:
  1. Try a fast deterministic match (exact PO number, slash command,
     unambiguous keywords) — no AI needed.
  2. Fall back to DeepSeek for fuzzy English routing.
  3. Final fallback: keyword heuristics.

Per AD-POL-AI-GOV-001 + the 2026-05-09 CFO carve-out, DeepSeek is
authorised for non-sensitive reasoning on alpha-finance. The user's
text is filtered through `core.ai_assist.is_safe_for_ai()` before
send. We never send database content here — only the user's question.

The bot is read-only, so misclassification is harmless: the worst
case is the user sees a different report and re-asks.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict

from core.ai_assist import DeepSeekUnavailable, deepseek_complete, is_safe_for_ai


INTENTS = {
    'revenue':              'Gross Written Premium (insurance revenue)',
    'cash_balance':         'Total cash across active bank accounts',
    'pending_pos':          'Purchase orders awaiting approval',
    'po_detail':            'Details of a specific purchase order',
    'bills_to_pay':         'Vendor bills outstanding',
    'ar_outstanding':       'Receivables / premium debtors outstanding',
    'ap_outstanding':       'Payables outstanding',
    'open_exceptions':      'Open operational exceptions / fraud cues',
    'pnl_summary':          'Full insurance Profit & Loss statement',
    'management_accounts':  'Management accounts P&L (Alpha Direct FY25 New Format)',
    'balance_sheet':        'Statement of Financial Position (Balance Sheet)',
    'key_metrics':          'Loss ratio, expense ratio, combined ratio, acquisition ratio',
    'fy24_pnl':             'Prior-year P&L (FY 2023-24 comparative)',
    'fy24_bs':              'Prior-year Balance Sheet (FY 2023-24 comparative)',
    'fy24_metrics':         'Prior-year insurance ratios (FY 2023-24)',
    'system_status':        'Overall system status / queue counts',
    'payroll':              'What a specific employee was paid (net/gross/PAYE) in a period',
    'time_doctor':          'Staff hours / attendance / productivity (Time Doctor)',
    'fleet':                'Company vehicle details / live location (fleet)',
    'assets':               'Fixed-asset register — tag, custodian, location, cost',
    'leave':                'An employee\'s leave balances (annual, sick, etc.)',
    'claims':               'Insurance claims register / a specific claim',
    'help':                 'List what the bot can answer',
    'unknown':              'Could not classify the question',
}

# Entity-bearing intents resolved locally (a person NAME / plate / asset tag /
# claim number is extracted on-box) — kept OUT of the DeepSeek classifier so
# those never leave.
_LOCAL_ONLY = {'payroll', 'time_doctor', 'fleet', 'assets', 'leave', 'claims'}


_PO_RE = re.compile(r'\bPO-[A-Z]{2,4}-\d{4}-\d{4,8}\b', re.IGNORECASE)
_MONTHS_RE = re.compile(r'(\d{1,2})\s*month', re.IGNORECASE)

# ── Payroll (individual staff pay) — detected locally, never via DeepSeek ──
# The employee NAME must not leave the box, so we classify + extract it here
# with plain regex rather than shipping the question to an external model.
_PERIOD_RE = re.compile(r'\b(20\d{2})[-/](0[1-9]|1[0-2])\b')
_MONTHS = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
    'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
    'aug': 8, 'august': 8, 'sep': 9, 'sept': 9, 'september': 9, 'oct': 10,
    'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12,
}
_MONTH_YEAR_RE = re.compile(
    r'\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
    r'jul(?:y)?|aug(?:ust)?|sep(?:t)?(?:ember)?|oct(?:ober)?|nov(?:ember)?|'
    r'dec(?:ember)?)\s+(20\d{2})\b', re.IGNORECASE)


def _parse_period(text: str) -> str:
    """Extract a payroll period as 'YYYY-MM' from '2026-06' or 'June 2026'."""
    m = _PERIOD_RE.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = _MONTH_YEAR_RE.search(text)
    if m:
        mon = _MONTHS.get(m.group(1).lower(), 0)
        if mon:
            return f"{int(m.group(2)):04d}-{mon:02d}"
    return ''


_SALARY_KW = (
    'salary', 'salaries', 'payslip', 'pay slip', 'payroll', 'net pay',
    'take home', 'take-home', 'remuneration', 'wage', 'wages',
)
# Insurance / finance nouns that also collocate with "pay(ed)" — exclude so
# real premium/claims/RI questions don't get misrouted to payroll.
_PAYROLL_EXCLUDE = (
    'bill', 'invoice', 'purchase order', ' po ', 'vendor', 'supplier',
    'creditor', 'reinsur', 'premium', 'claim', 'commission', 'ceded',
    'dividend', 'interest', 'levy', 'reserve',
)
_NAME_AFTER_PAY_RE = re.compile(
    r"\b(?:pay|paid)\s+(?:to\s+)?([a-z][a-z.'-]+(?:\s+[a-z][a-z.'-]+){0,2})",
    re.IGNORECASE)
_NAME_POSSESSIVE_RE = re.compile(
    r"\b([a-z][a-z.'-]+(?:\s+[a-z][a-z.'-]+){0,2})[’']s\s+"
    r"(?:salary|pay|payslip|net|gross|wage|remuneration)",
    re.IGNORECASE)
_TIME_WORDS = {
    'last', 'this', 'next', 'month', 'months', 'year', 'week', 'ago',
    'previous', 'prior', 'current', 'the', 'in', 'for', 'of', 'period',
    'monthly', 'past',
} | set(_MONTHS)
_NOISE_WORDS = {
    'how', 'much', 'did', 'do', 'does', 'we', 'you', 'they', 'i', 'was',
    'were', 'is', 'are', 'get', 'got', 'him', 'her', 'them', 'to', 'a', 'an',
    'and', 'what', 'whats', 'tell', 'me', 'show', 'give', 'pay', 'paid',
    'salary', 'salaries', 'net', 'gross', 'take', 'home', 'wage', 'wages',
    'remuneration', 'payslip', 'payroll', 'earn', 'earned', 'earns', 'that',
    'their', 'his', 'our', 'total', 'amount',
}


def _detect_payroll(text: str):
    """Return a payroll intent dict if *text* is an individual-pay question,
    else None. Fires only when a name is extractable OR a salary keyword is
    present, so premium/claims/RI 'pay' questions fall through untouched."""
    t = text.lower()
    if any(x in t for x in _PAYROLL_EXCLUDE):
        return None

    has_salary_kw = any(k in t for k in _SALARY_KW)

    m = _NAME_POSSESSIVE_RE.search(text) or _NAME_AFTER_PAY_RE.search(text)
    name = ''
    if m:
        name = ' '.join(w for w in m.group(1).split()
                        if w.lower() not in _TIME_WORDS
                        and w.lower() not in _NOISE_WORDS)

    if not name and has_salary_kw:
        # "salary for pako" — no pay-verb, pull the leftover proper token(s).
        toks = re.findall(r"[a-z][a-z.'-]+", t)
        cand = [w for w in toks
                if w not in _NOISE_WORDS and w not in _TIME_WORDS]
        name = ' '.join(cand[:3])

    if not name and not has_salary_kw:
        return None   # neither a name nor a salary keyword → not payroll

    period_name = _parse_period(text)
    want_last_month = any(p in t for p in
                          ('last month', 'previous month', 'past month'))

    return _result('payroll', employee_query=name,
                   period_name=period_name, want_last_month=want_last_month)


# ── Generic entity extraction (person name / plate / asset tag) ────────────
_ENTITY_STOP = {
    'asset', 'assets', 'fixed', 'tag', 'serial', 'number', 'custodian',
    'equipment', 'car', 'cars', 'vehicle', 'vehicles', 'fleet', 'plate',
    'plates', 'registration', 'register', 'registered', 'reg', 'odometer',
    'driver', 'driving',
    'drive', 'drives', 'hours', 'hour', 'productivity', 'productive',
    'attendance', 'timesheet', 'timesheets', 'worked', 'working', 'work',
    'tracked', 'track', 'time', 'doctor', 'details', 'detail', 'about',
    'staff', 'member', 'records', 'record', 'who', 'has', 'have', 'had',
    'holds', 'hold', 'owns', 'own', 'using', 'use', 'where', 'which',
    'what', 'whats', 'is', 'are', 'on', 'of', 'the', 'a', 'an', 'many',
    'much', 'for', 'and', 'to', 'me', 'show', 'tell', 'give', 'list',
    'our', 'does', 'do', 'did', 'we', 'you', 'their', 'his', 'her', 'how',
    # leave vocabulary — stripped so "leave for pako" → "pako"
    'leave', 'annual', 'sick', 'days', 'day', 'off', 'balance', 'balances',
    'vacation', 'holiday', 'pto', 'remaining', 'left', 'entitlement',
} | set(_MONTHS)


def _entity_query(text: str) -> str:
    """Best-effort person/plate/tag string — leftover tokens after dropping
    structural + time words. Up to 3 tokens."""
    toks = re.findall(r"[a-z0-9][a-z0-9.'-]+", text.lower())
    cand = [w for w in toks
            if w not in _ENTITY_STOP and w not in _TIME_WORDS and len(w) > 1]
    return ' '.join(cand[:3])


_TD_KW = (
    'time doctor', 'timedoctor', 'hours worked', 'hours did', 'how many hours',
    'hours tracked', 'working hours', 'productivity', 'productive',
    'attendance', 'tracked time', 'timesheet', 'clock in', 'clocked',
    'worked today', 'online today', 'logged in today',
)
# Botswana civilian plate: 1 letter + 3 digits + 3 letters (e.g. "B 123 ABC").
# Kept tight so it can't swallow "FY 2024" style tokens.
_PLATE_RE = re.compile(r'\b[A-Z]\s?\d{3}\s?[A-Z]{3}\b', re.IGNORECASE)
_FLEET_KW = (
    'car', 'cars', 'vehicle', 'vehicles', 'fleet', 'number plate', 'plate',
    'registration', 'odometer', 'driving', 'driver',
)
_FLEET_EXCLUDE = (
    'cash', 'money', 'bank', 'premium', 'revenue', 'profit', 'payment',
    'invoice', 'bill', 'claim', ' po ', 'purchase', 'payable', 'receivable',
    'salary', 'payroll',
)
_ASSET_KW = (
    'asset', 'assets', 'laptop', 'equipment', 'tag number', 'serial number',
    'custodian', 'fixed asset', 'furniture', 'macbook', 'printer', 'monitor',
    'register',
)


def _detect_timedoctor(text: str):
    t = text.lower()
    if not (any(k in t for k in _TD_KW) or ('hours' in t and 'pay' not in t)):
        return None
    return _result('time_doctor', employee_query=_entity_query(text))


def _detect_fleet(text: str):
    t = text.lower()
    if any(x in t for x in _FLEET_EXCLUDE):
        return None
    if not (any(k in t for k in _FLEET_KW) or _PLATE_RE.search(text)):
        return None
    return _result('fleet', query=_entity_query(text))


def _detect_assets(text: str):
    t = text.lower()
    if not any(k in t for k in _ASSET_KW):
        return None
    return _result('assets', query=_entity_query(text))


# ── Leave balances — detected locally so the employee NAME never leaves box ──
_LEAVE_KW = (
    'leave', 'annual leave', 'sick leave', 'day off', 'days off', 'time off',
    'vacation', 'holiday', 'pto', 'leave balance', 'leave days',
)
# Avoid the everyday verb "leave" ("leave it", "leave the office") tripping this.
_LEAVE_EXCLUDE = ('leave it', 'leave the', 'leave me', 'leaves the', 'leaving')


def _detect_leave(text: str):
    t = text.lower()
    if any(x in t for x in _LEAVE_EXCLUDE):
        return None
    if not any(k in t for k in _LEAVE_KW):
        return None
    return _result('leave', employee_query=_entity_query(text))


# ── Claims — detected locally so a claim/policy number or customer name is
# extracted on-box rather than shipped to the classifier ──
_CLAIM_STOP = {
    'claim', 'claims', 'open', 'pending', 'closed', 'settled', 'register',
    'summary', 'all', 'show', 'list', 'me', 'the', 'a', 'for', 'of', 'on',
    'status', 'give', 'tell', 'what', 'whats', 'how', 'many', 'is', 'are',
    'outstanding', 'total', 'reserve', 'paid', 'and', 'to', 'our',
}


def _claims_query(text: str) -> str:
    toks = re.findall(r"[a-z0-9][a-z0-9./-]+", text.lower())
    cand = [w for w in toks if w not in _CLAIM_STOP and len(w) > 1]
    return ' '.join(cand[:3])


def _detect_claims(text: str):
    if 'claim' not in text.lower():
        return None
    return _result('claims', query=_claims_query(text))


_INTENT_SYSTEM = (
    "You are a routing classifier for the CFO of Alpha Direct Insurance — "
    "an INSURANCE COMPANY in Botswana. Map the user's question to ONE intent.\n\n"
    "CRITICAL — insurance accounting vocabulary:\n"
    "  - 'revenue' / 'premium' / 'GWP' / 'gross written premium' / "
    "'written premium' / 'top line' / 'premium income' ALL mean the SAME thing: "
    "Gross Written Premium. Route them ALL to intent=revenue.\n"
    "  - Reinsurance recoveries, RI commission, subrogations, salvages, "
    "interest, FX are NOT revenue. If the user asks for those by name, "
    "use intent=pnl_summary (the full insurance P&L breaks them out).\n"
    "  - 'P&L' / 'income statement' / 'net profit' / 'profit' / 'loss' / "
    "'gross profit' / 'underwriting result' → intent=pnl_summary.\n\n"
    "Available intents:\n"
    + "\n".join(f"- {k}: {v}" for k, v in INTENTS.items() if k not in _LOCAL_ONLY)
    + "\n\nQuestions about a NAMED person (their pay, their hours, what car "
    "they drive, what assets they hold), a vehicle, or an asset are handled "
    "elsewhere — for any of those use intent=unknown.\n"
    + "\n\nRespond ONLY with valid JSON of the shape: "
    '{"intent":"<key>","po_number":"<extracted or empty>","months":<int or null>}. '
    "Extract po_number when the user mentions one (PO-XXX-YYYY-NNNNNN format). "
    "Extract months when the user asks for revenue over a period (default 6 if vague). "
    "Use intent=unknown if nothing fits."
)


def parse_intent(text: str) -> Dict[str, Any]:
    """
    Return {'intent': str, 'po_number': str, 'months': int|None}.
    Always returns a dict — never raises.
    """
    text = (text or '').strip()
    if not text:
        return _result('unknown')

    lower = text.lower()

    # --- Fast deterministic paths ---------------------------------------
    if lower in ('help', '/help', 'what can you do', 'commands'):
        return _result('help')

    # Direct PO number — bypass AI unless user is asking for "pending"
    po_match = _PO_RE.search(text)
    if po_match and 'pending' not in lower:
        return _result('po_detail', po_number=po_match.group(0).upper())

    # Individual staff pay — resolved locally so the employee NAME never
    # leaves the box (never sent to DeepSeek). bot.py gates the ANSWER behind
    # the authorised-user whitelist (fail-closed).
    payroll_hit = _detect_payroll(text)
    if payroll_hit is not None:
        return payroll_hit

    # Other entity lookups — also resolved locally (name/plate/tag/claim stays
    # on-box). Claims + leave run BEFORE fleet/assets: _ASSET_KW contains
    # 'register', which would otherwise swallow "claims register" / "leave
    # register for <name>". fleet/assets already exclude the finance nouns.
    for detector in (_detect_timedoctor, _detect_claims, _detect_leave,
                     _detect_fleet, _detect_assets):
        hit = detector(text)
        if hit is not None:
            return hit

    # --- DeepSeek classifier --------------------------------------------
    safety = is_safe_for_ai(text)
    if safety.safe:
        try:
            raw = deepseek_complete(
                f'User question: "{safety.redacted_text}"\n\nReturn the JSON object.',
                system_prompt=_INTENT_SYSTEM,
                response_format='json_object',
                timeout=10.0,
            )
            parsed = json.loads(raw)
            intent = (parsed.get('intent') or '').strip().lower()
            if intent in INTENTS:
                months_val = parsed.get('months')
                return _result(
                    intent,
                    po_number=(parsed.get('po_number') or '').strip().upper(),
                    months=months_val if isinstance(months_val, int) else None,
                )
        except (DeepSeekUnavailable, json.JSONDecodeError, KeyError, AttributeError):
            pass  # fall through to keyword heuristics

    return _keyword_fallback(text)


def _keyword_fallback(text: str) -> Dict[str, Any]:
    t = text.lower()

    # ── FY24 / Prior-year comparatives ──
    # Detect "FY24" / "last year" / "prior year" + the type of report.
    is_fy24 = any(w in t for w in (
        'fy24', 'fy 24', 'fy2024', 'fy 2024',
        'last year', 'prior year', 'previous year', 'comparative',
        '2023-24', '2023-2024',
    ))
    if is_fy24:
        if any(w in t for w in ('balance sheet', 'bs', 'sfp', 'financial position')):
            return _result('fy24_bs')
        if any(w in t for w in ('loss ratio', 'combined', 'expense ratio',
                                  'ratio', 'metric', 'kpi')):
            return _result('fy24_metrics')
        return _result('fy24_pnl')   # default for FY24 = P&L

    # Management accounts / P&L / income statement first — "income statement"
    # must not be caught by the generic "income" keyword below.
    if any(w in t for w in (
        'management accounts', 'management account', 'mgmt accounts',
        ' ma ', 'results', 'fy25 results', 'fsli', 'new format',
    )) or t.strip() in ('ma',):
        return _result('management_accounts')
    if any(w in t for w in ('income statement', 'p&l', 'pnl', 'profit and loss',
                              'profit & loss')):
        return _result('pnl_summary')

    # Insurance vocabulary: revenue / premium / GWP / top line / written
    # premium / premium income all → Gross Written Premium (handled by the
    # `revenue` intent which now returns 100xxx only).
    if any(w in t for w in (
        'revenue', 'income', 'turnover', 'sales',
        'premium', 'gwp', 'gross written', 'written premium',
        'top line', 'topline',
    )):
        m = _MONTHS_RE.search(t)
        return _result('revenue', months=int(m.group(1)) if m else 6)

    if 'cash' in t or 'bank balance' in t or ('bank' in t and 'account' in t):
        return _result('cash_balance')

    po_match = _PO_RE.search(text)
    if po_match:
        return _result('po_detail', po_number=po_match.group(0).upper())

    if 'pending' in t and ('po' in t or 'purchase' in t or 'order' in t):
        return _result('pending_pos')

    if 'bill' in t and any(w in t for w in ('pay', 'unpaid', 'outstanding', 'due')):
        return _result('bills_to_pay')

    if any(w in t for w in ('receivable', 'debtor')) or 'ar ' in (' ' + t):
        return _result('ar_outstanding')

    if any(w in t for w in ('payable', 'creditor')) or 'ap ' in (' ' + t):
        return _result('ap_outstanding')

    if any(w in t for w in ('exception', 'fraud', 'alert', 'anomaly')):
        return _result('open_exceptions')

    # Balance sheet vocabulary
    if any(w in t for w in ('balance sheet', 'bs ', 'financial position',
                             'statement of position', 'sfp')) or t.strip() in ('bs',):
        return _result('balance_sheet')

    # Insurance KPIs / ratios
    if any(w in t for w in ('loss ratio', 'combined ratio', 'expense ratio',
                             'acquisition ratio', 'underwriting', 'key metrics',
                             'kpi', 'metric', 'ratio')):
        return _result('key_metrics')

    if any(w in t for w in ('p&l', 'pnl', 'profit', 'loss', 'income statement')):
        return _result('pnl_summary')

    if any(w in t for w in ('status', 'health', 'overview', 'dashboard', 'summary')):
        return _result('system_status')

    if any(w in t for w in ('help', 'what can')):
        return _result('help')

    return _result('unknown')


def _result(intent: str, *, po_number: str = '', months=None,
            employee_query: str = '', period_name: str = '',
            want_last_month: bool = False, query: str = '') -> Dict[str, Any]:
    return {'intent': intent, 'po_number': po_number, 'months': months,
            'employee_query': employee_query, 'period_name': period_name,
            'want_last_month': want_last_month, 'query': query}
