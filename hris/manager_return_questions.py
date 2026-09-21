"""
hris/manager_return_questions.py — role-specific questions for the Monthly
Manager Return (CFO 2026-07-26).

The CFO's correction: "we can't ask IT team and driver for sales targets".
So the return is BASE questions (every manager) + a DEPARTMENT block that
actually belongs to the work — IT gets uptime/tickets, AML gets KYC, Claims
gets SLA and settlement, Fleet gets vehicles.

Two sources, in this order:
  1. A curated per-department set below — deterministic, works offline, works
     TODAY with no job descriptions loaded. This is the floor.
  2. Aria (core.ai_assist.reasoning_complete) refines/extends the set from the
     manager's own job title + department + their JD once HR loads it.

Aria is strictly ADDITIVE and always optional: if the AI is unreachable, returns
junk, or trips the PII guard, the curated set is what the manager sees. The CFO's
instruction was "best to do something and say sorry than not doing at all" — so
this ships useful on day one and gets sharper as HR fills in the JDs.
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

# Questions EVERY people-manager answers, whatever their department.
BASE_QUESTIONS = [
    {'key': 'leave_action', 'kind': 'text', 'required': True,
     'label': 'Your people who lost hours without leave or an explanation — what did you do about it?'},
    {'key': 'work_finished_on_time', 'kind': 'bool', 'required': True,
     'label': 'Was the work given to your team finished on time?',
     'comment_key': 'work_on_time_comment'},
    {'key': 'dashboard_cleared_on_time', 'kind': 'bool', 'required': True,
     'label': 'Was your omni dashboard cleared on time?'},
    {'key': 'overstaffed', 'kind': 'bool', 'required': True,
     'label': 'Given the hours your team actually delivered, are you overstaffed?',
     'comment_key': 'overstaffed_comment',
     'help': 'The number above is your team\'s hours expressed as people. Answer against it.'},
    {'key': 'fy27_aligned', 'kind': 'bool', 'required': True,
     'label': 'Is your team aligned to deliver the FY27 targets?'},
    {'key': 'fy27_actions', 'kind': 'text', 'required': True,
     'label': 'What are you doing to hit those targets?'},
    {'key': 'innovation', 'kind': 'text', 'required': True,
     'label': 'What did you improve or automate this month?'},
]

# Sales is a DECLARATION and only for revenue-facing teams (CFO: "just ask the
# question, they will say yes or no and put a place for new sales amount").
SALES_BLOCK = [
    {'key': 'sales_target_met', 'kind': 'bool', 'required': True,
     'label': 'Did your team meet its sales target for the month?'},
    {'key': 'new_sales_amount', 'kind': 'money', 'required': False,
     'label': 'New sales written this month (Pula)'},
]

# SLA is a declaration too — omni has no SLA store to read yet.
SLA_BLOCK = [
    {'key': 'sla_breaches', 'kind': 'number', 'required': False,
     'label': 'How many service-level breaches did your team have?'},
    {'key': 'sla_explanation', 'kind': 'text', 'required': False,
     'label': 'Why did they happen, and what changed as a result?'},
]

# Which departments are revenue-facing. Matched case-insensitively as substrings
# against department AND job title, so "Sales & Marketing" and "Broker Relations"
# both catch. Everyone else is NOT asked about sales.
SALES_DEPARTMENTS = (
    'sales', 'marketing', 'business development', 'broker', 'distribution',
    'retail', 'commercial', 'agency', 'bancassurance', 'direct',
)
# Departments where a service-level promise is the core of the job.
SLA_DEPARTMENTS = (
    'claims', 'client service', 'customer', 'call centre', 'call center',
    'operations', 'underwriting', 'health', 'helpdesk', 'it', 'support',
)

# Curated department blocks. Keys are matched as substrings of department/title.
# Each question writes into `dept_answers` (a JSON blob), NOT its own column —
# so a new department needs no migration.
DEPARTMENT_BLOCKS: dict[str, list[dict]] = {
    'it': [
        {'key': 'it_uptime', 'kind': 'text',
         'label': 'Any system downtime this month? What caused it and what stops it recurring?'},
        {'key': 'it_tickets', 'kind': 'text',
         'label': 'Oldest unresolved help-desk ticket, and why is it still open?'},
        {'key': 'it_security', 'kind': 'text',
         'label': 'Any security or access issue you found or fixed this month?'},
        {'key': 'it_backups', 'kind': 'bool',
         'label': 'Were backups verified as restorable this month?'},
    ],
    'aml': [
        {'key': 'aml_kyc_backlog', 'kind': 'number',
         'label': 'How many policies are still missing KYC documents?'},
        {'key': 'aml_kyc_action', 'kind': 'text',
         'label': 'What are you doing to clear that backlog?'},
        {'key': 'aml_str', 'kind': 'number',
         'label': 'Suspicious transaction reports raised this month'},
        {'key': 'aml_screening', 'kind': 'bool',
         'label': 'Was sanctions / PEP screening run on all new business?'},
    ],
    'compliance': [
        {'key': 'comp_returns', 'kind': 'bool',
         'label': 'Were all regulatory returns (NBFIRA etc.) filed on time?'},
        {'key': 'comp_findings', 'kind': 'text',
         'label': 'Open audit or regulator findings, and where each one stands'},
        {'key': 'comp_breaches', 'kind': 'text',
         'label': 'Any compliance breach this month, and what you did about it'},
    ],
    'claims': [
        {'key': 'claims_turnaround', 'kind': 'text',
         'label': 'Average days from claim registered to settled — and is that moving the right way?'},
        {'key': 'claims_oldest', 'kind': 'text',
         'label': 'Oldest open claim and why it is still open'},
        {'key': 'claims_salvage', 'kind': 'bool',
         'label': 'Was every salvage in the yard before settlement was paid?'},
        {'key': 'claims_recoveries', 'kind': 'text',
         'label': 'Recoveries and reinsurance recoveries pursued this month'},
    ],
    'underwriting': [
        {'key': 'uw_loss_ratio', 'kind': 'text',
         'label': 'How is your loss ratio trending, and what are you re-rating?'},
        {'key': 'uw_ri', 'kind': 'bool',
         'label': 'Was facultative reinsurance confirmed on every risk over BWP 50M?'},
        {'key': 'uw_quotes', 'kind': 'text',
         'label': 'Quote turnaround time, and any quotes lost on speed'},
    ],
    'finance': [
        {'key': 'fin_close', 'kind': 'bool',
         'label': 'Did the month-end close finish on schedule?'},
        {'key': 'fin_recon', 'kind': 'text',
         'label': 'Any bank or ledger reconciliation still not clean, and why'},
        {'key': 'fin_debtors', 'kind': 'text',
         'label': 'Overdue premium debtors — amount and what you are doing about it'},
    ],
    'fleet': [
        {'key': 'fleet_condition', 'kind': 'text',
         'label': 'Vehicles off the road this month and why'},
        {'key': 'fleet_licences', 'kind': 'bool',
         'label': 'Are all vehicle licences and fitness certificates current?'},
        {'key': 'fleet_incidents', 'kind': 'text',
         'label': 'Any driver incident or traffic fine, and the action taken'},
    ],
    'human': [   # human capital / HR
        {'key': 'hr_vacancies', 'kind': 'text',
         'label': 'Open vacancies and days each has been open'},
        {'key': 'hr_exits', 'kind': 'text',
         'label': 'Who left this month and the real reason given'},
        {'key': 'hr_jds', 'kind': 'bool',
         'label': 'Are job descriptions loaded and current for everyone in your team?'},
    ],
    'health': [
        {'key': 'health_auth', 'kind': 'text',
         'label': 'Pre-authorisation turnaround, and any member complaint about it'},
        {'key': 'health_providers', 'kind': 'text',
         'label': 'Any provider or scheme dispute open this month'},
    ],
}


def _hay(department: str, job_title: str) -> str:
    return f'{department or ""} {job_title or ""}'.lower()


def wants_sales(department: str, job_title: str) -> bool:
    return any(k in _hay(department, job_title) for k in SALES_DEPARTMENTS)


def wants_sla(department: str, job_title: str) -> bool:
    return any(k in _hay(department, job_title) for k in SLA_DEPARTMENTS)


def department_block(department: str, job_title: str) -> tuple[str, list[dict]]:
    """Best-matching curated block. Longest key wins so 'human capital' beats a
    stray 'it' substring inside another word."""
    hay = _hay(department, job_title)
    best_key, best_len = '', 0
    for key in DEPARTMENT_BLOCKS:
        # word-ish match: avoids 'it' matching 'credit' / 'audit' / 'security'
        if re.search(rf'(?<![a-z]){re.escape(key)}(?![a-z])', hay) and len(key) > best_len:
            best_key, best_len = key, len(key)
    return best_key, list(DEPARTMENT_BLOCKS.get(best_key, []))


_AI_PROMPT = """You write monthly accountability questions for managers at a Botswana insurance company.

The manager's role: {title}
Their department: {dept}
Their job description (may be blank): {jd}

Write between 2 and 4 SHORT questions this manager should answer every month about
THEIR OWN AREA of work. Rules:
- Specific to this role. Never ask about sales unless the role actually sells.
- Answerable in one or two sentences.
- About outcomes and accountability, not feelings.
- Plain English, under 20 words each.
- Do NOT repeat these, which are already asked: team hours, leave, staff numbers,
  tasks on time, innovation, FY27 targets, omni dashboard.

Return ONLY a JSON array like:
[{{"key":"short_snake_case","label":"The question?","kind":"text"}}]
kind must be "text", "bool" or "number"."""

_KEY_RE = re.compile(r'^[a-z][a-z0-9_]{2,40}$')


def ai_questions(job_title: str, department: str, jd_text: str = '',
                 limit: int = 4) -> list[dict]:
    """Aria's extra role-specific questions. Never raises — returns [] on any
    problem so the curated set always stands on its own."""
    try:
        from core.ai_assist import is_safe_for_ai, reasoning_complete
    except Exception:
        return []

    prompt = _AI_PROMPT.format(title=job_title or 'Manager',
                              dept=department or 'Unknown',
                              jd=(jd_text or '')[:2000] or 'not loaded yet')
    try:
        report = is_safe_for_ai(prompt)
        if not getattr(report, 'safe', True):
            log.info('manager-return: AI questions skipped, PII guard tripped')
            return []
    except Exception:
        return []

    try:
        raw = reasoning_complete(prompt, feature='manager_return_questions',
                                 response_format={'type': 'json_object'})
    except Exception as exc:
        log.info('manager-return: AI questions unavailable (%s)', exc)
        return []

    return _parse_ai(raw, limit)


def _parse_ai(raw: str, limit: int) -> list[dict]:
    """Trust nothing the model returns: shape, types and keys are all validated."""
    if not raw:
        return []
    text = raw.strip()
    # Models sometimes wrap the array in an object or a ```json fence.
    text = re.sub(r'^```(?:json)?|```$', '', text, flags=re.M).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        m = re.search(r'\[.*\]', text, re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except (ValueError, TypeError):
            return []
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                data = v
                break
    if not isinstance(data, list):
        return []

    out, seen = [], set()
    reserved = {q['key'] for q in BASE_QUESTIONS} | {'sales_target_met',
                                                    'new_sales_amount',
                                                    'sla_breaches', 'sla_explanation'}
    for item in data:
        if not isinstance(item, dict):
            continue
        key = str(item.get('key') or '').strip().lower()
        label = str(item.get('label') or '').strip()
        kind = str(item.get('kind') or 'text').strip().lower()
        if not _KEY_RE.match(key) or not label or len(label) > 200:
            continue
        if key in seen or key in reserved:
            continue
        if kind not in ('text', 'bool', 'number'):
            kind = 'text'
        seen.add(key)
        out.append({'key': f'ai_{key}', 'kind': kind, 'label': label,
                    'source': 'aria'})
        if len(out) >= limit:
            break
    return out


def question_set(job_title: str, department: str, jd_text: str = '',
                 use_ai: bool = True) -> dict:
    """The full form spec for one manager."""
    dept_key, dept_qs = department_block(department, job_title)
    blocks = [{'title': 'Your team and your management', 'questions': BASE_QUESTIONS}]

    if wants_sales(department, job_title):
        blocks.append({'title': 'Sales (declaration)', 'questions': SALES_BLOCK})
    if wants_sla(department, job_title):
        blocks.append({'title': 'Service levels (declaration)', 'questions': SLA_BLOCK})

    extra = list(dept_qs)
    if use_ai:
        extra += [q for q in ai_questions(job_title, department, jd_text)
                  if q['key'] not in {d['key'] for d in dept_qs}]
    if extra:
        label = (dept_key or department or 'your area').title()
        blocks.append({'title': f'{label} — your own area', 'questions': extra})

    return {
        'department_matched': dept_key,
        'asks_sales': wants_sales(department, job_title),
        'asks_sla': wants_sla(department, job_title),
        'blocks': blocks,
    }
