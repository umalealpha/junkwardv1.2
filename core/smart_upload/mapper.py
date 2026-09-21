"""
core/smart_upload/mapper.py

Heuristic + DeepSeek-assisted column mapper.

Given a list of raw headers from the user's file and a SectionSpec, return
a mapping {raw_header -> canonical_field}. Strategy:

  1. Local regex / synonym match (fast, no network).
  2. If any required field is still unmapped, ask DeepSeek with ONLY:
       - the raw header list
       - the canonical field list
       - 3 sample rows with PII redacted by core.ai_assist.is_safe_for_ai
     DeepSeek replies with a JSON {raw_header -> canonical_field}. Local
     fallback wins on conflict.

PII guarantee: we never send the bulk row data. Three sample rows max,
redacted, just for column-content disambiguation.
"""

from __future__ import annotations

import json
import logging
import re

from core.ai_assist import deepseek_complete, DeepSeekUnavailable, is_safe_for_ai
from .sections import SectionSpec


log = logging.getLogger(__name__)


# Pre-baked synonyms — covers Odoo / SAP / Sage / CFO's xlsx conventions.
_SYNONYMS: dict[str, list[str]] = {
    # CoA
    'code':         ['code', 'account code', 'gl code', 'gl account', 'account', 'acct'],
    'name':         ['name', 'account name', 'description', 'account description',
                     'supplier name', 'customer name', 'vendor name', 'partner',
                     'partner name', 'asset name', 'employee name'],
    'account_type': ['type', 'account type', 'category'],
    'sub_type':     ['sub type', 'sub_type', 'subtype', 'category sub', 'detail type'],
    'parent_code':  ['parent', 'parent code', 'parent account'],
    'is_summary_only': ['summary', 'summary only', 'is_summary', 'is summary'],

    # TB / GL
    'account_code': ['account code', 'gl code', 'account', 'gl account', 'acct'],
    'account_name': ['account name', 'gl name'],
    'debit':        ['debit', 'dr', 'debit_zar', 'debit_bwp', 'debit amount'],
    'credit':       ['credit', 'cr', 'credit_zar', 'credit_bwp', 'credit amount'],
    'period_end':   ['period end', 'period_end', 'date', 'as of', 'as_of'],
    'period_label': ['period', 'period label', 'fiscal year', 'fy'],
    'entry_date':   ['date', 'posting date', 'entry date', 'transaction date'],
    'entry_ref':    ['ref', 'reference', 'entry ref', 'doc number', 'document', 'voucher'],
    'description':  ['description', 'memo', 'narration', 'narrative', 'detail'],
    'line_no':      ['line', 'line no', 'line number', 'seq'],
    'memo':         ['memo', 'note', 'comment'],

    # Vendors / Customers
    'tax_id':        ['vat', 'vat number', 'vat_number', 'tax id', 'tax_id', 'tin'],
    'email':         ['email', 'e-mail'],
    'phone':         ['phone', 'tel', 'mobile', 'contact number'],
    'payment_terms': ['payment terms', 'terms', 'days'],
    'default_gl_expense_code': ['default expense gl', 'expense gl', 'default_gl_expense_code'],
    'default_gl_ar_code':       ['default ar gl', 'ar gl', 'default_gl_ar_code'],
    'is_active':     ['active', 'is active', 'is_active', 'status'],

    # PP&E
    'tag_number':   ['tag', 'asset code', 'tag number', 'asset_code', 'asset id'],
    'gl_account':   ['gl account', 'gl_account', 'asset gl account', 'asset_gl_account',
                     'cost gl', 'cost_gl'],
    'cost':         ['cost', 'original value', 'original_value', 'open value', 'close_value_fy24_zar',
                     'close_value_fy25_zar', 'opening value', 'value', 'purchase value'],
    'salvage_value':['salvage', 'salvage value', 'salvage_value', 'residual', 'residual_value'],
    'opening_accumulated_depreciation':
                    ['accum depr', 'accumulated depreciation', 'opening accum depr',
                     'accum_depr', 'accumulated_depreciation', 'opening_accumulated_depreciation'],
    'purchase_date':['acquisition date', 'acquisition_date', 'purchase date', 'purchase_date',
                     'date acquired'],
    'in_service_date': ['in service date', 'first_depreciation_date',
                        'first depreciation date', 'placed in service'],
    'useful_life_months': ['useful life', 'useful_life', 'life', 'depreciation_method_periods',
                           'method_periods'],
    'method':       ['method', 'depr method', 'depreciation method'],
    'location':     ['location', 'site', 'branch'],
    'custodian':    ['custodian', 'owner', 'responsible'],

    # Bank
    'account_name': ['name', 'account name'],
    'bank_name':    ['bank', 'bank name'],
    'branch_code':  ['branch', 'branch code', 'sort code'],
    'account_number': ['account number', 'account no', 'acc no'],
    'currency_code':['currency', 'ccy', 'currency code'],

    # Employees
    # 'employee no' / 'emp no' must be listed: without them "Employee No"
    # falls through to the one-token 'employee' synonym and claims
    # employee_name, leaving the real "Employee" column unmapped (2026-07-29).
    'employee_code': ['employee code', 'emp code', 'emp_code', 'id', 'staff_id', 'staff id',
                      'employee no', 'employee number', 'emp no', 'staff no',
                      'payroll no', 'employee id'],
    'first_name':   ['first name', 'firstname', 'given name'],
    'last_name':    ['last name', 'lastname', 'surname', 'family name'],
    'job_title':    ['job title', 'title', 'role', 'position'],
    'department':   ['department', 'dept', 'cost centre'],
    'hire_date':    ['hire date', 'date of hire', 'start date'],
    'national_id':  ['id', 'national id', 'omang', 'sa id'],

    # Payroll — the CFO's register uses ABBREVIATED headers ("Med Aid EE",
    # "PO Allow", "Provident Fund ER"). Bug report Pako Kago 2026-07-29: the
    # only synonyms here were long-form, so 24 of 30 columns came back
    # "— unmapped —". Every abbreviation below carries at least TWO tokens so
    # the most-specific-match rule in heuristic_map() can separate the
    # near-identical families — "Med Aid EE" vs "Med Aid ER" vs "Med Aid
    # allowance", and "Pension EE" vs "Pension ER". A one-token synonym like
    # 'allow' would otherwise claim "Housing Allow" first and starve the rest.
    'employee_name':        ['employee', 'employee name', 'name', 'staff name',
                             'full name', 'employee full name'],

    # Earnings
    'basic':                ['basic', 'basic salary', 'basic pay', 'salary basic'],
    'commission':           ['commission', 'comm'],
    'incentive':            ['incentive', 'incentives'],
    'bonus':                ['bonus'],
    'po_allowance':         ['po allow', 'po allowance', 'principal officer allowance'],
    'allowance':            ['allow', 'allowance', 'other allowance', 'general allowance'],
    'housing_allowance':    ['housing allow', 'housing allowance', 'house allowance'],
    'leave_pay':            ['leave pay', 'leave', 'leave encashment'],
    'medical_aid_allowance':['med aid allowance', 'med aid allow',
                             'medical aid allowance', 'medical allowance'],
    'vehicle_allowance':    ['vehicle allow', 'vehicle allowance', 'car allowance',
                             'motor allowance'],
    'health_ins_allowance': ['health ins allow', 'health ins allowance',
                             'health insurance allowance', 'health allowance'],
    'fuel_allowance':       ['fuel allow', 'fuel allowance', 'petrol allowance'],
    'mobile_allowance':     ['mobile allow', 'mobile allowance', 'phone allowance',
                             'cell allowance'],
    'internet_allowance':   ['internet allow', 'internet allowance', 'data allowance'],
    'sales_allowance':      ['sales allow', 'sales allowance'],
    'severance':            ['severance', 'severence', 'severance pay'],
    'non_cash_benefit':     ['non cash benefit', 'non cash benefits',
                             'noncash benefit', 'benefit in kind'],

    # Employee deductions + tax
    'paye':                 ['paye', 'income tax', 'tax'],
    'loans_deduction':      ['loans ded', 'loan ded', 'loans deduction',
                             'loan deduction', 'loans', 'staff loan'],
    'housing_tax':          ['housing tax ded', 'housing tax', 'housing tax deduction'],
    'medical_aid_ee':       ['med aid ee', 'medical aid ee', 'med aid employee',
                             'medical aid employee contribution'],
    'pension_ee':           ['pension ee', 'pension employee',
                             'pension employee contribution'],
    'provident_ee':         ['provident fund ee', 'provident ee', 'provident employee',
                             'provident fund employee contribution'],

    # Company contributions
    'medical_aid_er':       ['med aid er', 'medical aid er', 'med aid company',
                             'medical aid company contribution'],
    'pension_er':           ['pension er', 'pension company',
                             'pension company contribution'],
    'provident_er':         ['provident fund er', 'provident er', 'provident company',
                             'provident fund company contribution'],

    # Totals (cross-check columns)
    'gross':                ['gross', 'gross pay', 'gross_salary', 'gross earnings'],
    'total_deductions':     ['total ded', 'tot ded', 'total deductions',
                             'total deduction'],
    'net':                  ['net', 'net pay', 'net_salary', 'take home'],
    'ctc':                  ['ctc', 'cost to company'],
    'status':               ['status', 'employment status', 'pay status'],
}


def _norm(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', s.lower()).strip('_')


def heuristic_map(headers: list[str], section: SectionSpec) -> dict[str, str]:
    """
    Local-only mapping. Returns {raw_header -> canonical_field}. Unmapped
    headers are omitted.
    """
    valid_fields = {f.name for f in section.fields}
    out: dict[str, str] = {}
    used_canonical: set[str] = set()

    # Build a normalized synonym table restricted to this section's fields
    syn_norm: dict[str, str] = {}   # _norm(syn) -> canonical
    for canonical in valid_fields:
        syn_norm[_norm(canonical)] = canonical
        for syn in _SYNONYMS.get(canonical, []):
            syn_norm.setdefault(_norm(syn), canonical)

    for raw in headers:
        if not raw:
            continue
        nh = _norm(raw)
        if nh in syn_norm:
            canon = syn_norm[nh]
            if canon not in used_canonical:
                out[raw] = canon
                used_canonical.add(canon)
                continue
        # Token-boundary fallback. Match a synonym only on WHOLE tokens, never a
        # raw substring — otherwise 'net' matches 'internet allowance' (the
        # net→Internet Allowance bug, Legakwa 2026-06-25) and 'id' matches
        # 'paid'. A synonym matches when all its tokens appear as whole tokens in
        # the header; the most specific (most tokens, then longest) match wins.
        nh_tokens = set(t for t in nh.split('_') if t)
        best = None   # (token_count, length, canonical)
        for s, canon in syn_norm.items():
            if not s or canon in used_canonical:
                continue
            s_tokens = [t for t in s.split('_') if t]
            if s_tokens and all(t in nh_tokens for t in s_tokens):
                score = (len(s_tokens), len(s))
                if best is None or score > best[:2]:
                    best = (len(s_tokens), len(s), canon)
        if best:
            out[raw] = best[2]
            used_canonical.add(best[2])

    return out


def _redact_sample(rows: list[list[str]], limit: int = 3) -> list[list[str]]:
    out = []
    for r in rows[:limit]:
        scrubbed = []
        for cell in r:
            rep = is_safe_for_ai(str(cell or ''))
            scrubbed.append(rep.redacted_text)
        out.append(scrubbed)
    return out


def deepseek_map(headers: list[str], sample_rows: list[list[str]],
                 section: SectionSpec,
                 prior: dict[str, str] | None = None,
                 company_code: str = '') -> dict[str, str]:
    """
    Ask DeepSeek to fill in any missing column mappings. Returns the
    merged dict (prior wins on conflict).
    """
    field_list = [
        {'name': f.name, 'kind': f.kind, 'required': f.required,
         'label': f.label, 'hint': f.hint}
        for f in section.fields
    ]
    payload = {
        'section':        section.key,
        'section_label':  section.label,
        'company_code':   company_code,
        'company_hint':   _company_hint(company_code),
        'headers':        headers,
        'sample_rows':    _redact_sample(sample_rows),
        'fields':         field_list,
        'already_mapped': prior or {},
    }

    system = (
        'You are an Alpha Direct Insurance Group finance assistant. The '
        'group has several entities (ADIC, ADSA, UNI, GCX, RSA, VCM, AIZ). '
        'Each entity uses prefixed GL codes (e.g. ADSA_400000) except '
        'ADIC which uses raw codes. You map spreadsheet column headers '
        'to a canonical schema for the *selected* company. '
        'Reply ONLY with valid JSON of the form '
        '{"mapping": {"<raw_header>": "<canonical_field>", ...}}. '
        'Use only canonical field names from the supplied list. Omit any '
        'header you are uncertain about. NEVER invent new fields. Sample '
        'rows have PII redacted to tokens like [ID-REDACTED] — use them '
        'only as a tiebreaker, not as actual data.'
    )
    user = json.dumps(payload, ensure_ascii=False)

    try:
        # 8s timeout so a stalled DeepSeek API can't lock the upload UI.
        # If DeepSeek is healthy, headers + 3 sample rows fits comfortably
        # in 8s; if not, fall back to whatever the heuristic produced.
        raw = deepseek_complete(user, system_prompt=system,
                                response_format='json_object', timeout=8.0)
    except DeepSeekUnavailable as e:
        log.info('smart_upload: DeepSeek unavailable, heuristic only (%s)', e)
        return prior or {}

    try:
        data = json.loads(raw)
        ai_map = data.get('mapping') or {}
    except (ValueError, TypeError) as e:
        log.warning('smart_upload: DeepSeek returned non-JSON: %s', e)
        return prior or {}

    valid_fields = {f.name for f in section.fields}
    merged = dict(prior or {})
    used_canonical = set(merged.values())
    for raw_h, canon in ai_map.items():
        if not isinstance(raw_h, str) or not isinstance(canon, str):
            continue
        if canon not in valid_fields:
            continue
        if raw_h in merged:
            continue
        if canon in used_canonical:
            continue
        merged[raw_h] = canon
        used_canonical.add(canon)
    return merged


def map_columns(headers: list[str], sample_rows: list[list[str]],
                section: SectionSpec,
                company_code: str = '') -> tuple[dict[str, str], list[str], bool]:
    """
    Full pipeline:
      1. Heuristic map first (zero-network, instant).
      2. ONLY if a required field is still missing → call DeepSeek as a
         best-effort refinement, with a short timeout so a stalled
         DeepSeek API can't hang an upload (CFO bug report 2026-05-19:
         Charmaine's clean schema hung indefinitely because the previous
         "always-on" mode blocked on DeepSeek every time).

    Returns (mapping, unresolved_required_fields, ai_used).
    """
    mapping = heuristic_map(headers, section)
    required = [f.name for f in section.fields if f.required]
    mapped_canonical = set(mapping.values())
    missing = [r for r in required if r not in mapped_canonical]
    ai_used = False

    if missing:
        # Heuristic didn't fill every required slot — ask DeepSeek to fill in.
        # DeepSeek client already has its own 20s timeout; that's enough.
        # Wrap in try/except so any unexpected exception still returns a
        # usable mapping rather than hanging the request.
        try:
            refined = deepseek_map(
                headers, sample_rows, section,
                prior=mapping, company_code=company_code,
            )
            if refined and refined != mapping:
                ai_used = True
                mapping = refined
        except Exception as e:    # noqa: BLE001
            log.warning('smart_upload: DeepSeek refinement raised (%s); '
                        'falling back to heuristic mapping', e)

        mapped_canonical = set(mapping.values())
        missing = [r for r in required if r not in mapped_canonical]

    return mapping, missing, ai_used


def _company_hint(code: str) -> str:
    """
    Short reminder string the AI uses to disambiguate per-entity conventions.
    """
    code = (code or '').upper()
    if code == 'ADIC':
        return ('Alpha Direct Insurance Company Botswana. Raw GL codes (no '
                'prefix). Functional currency BWP. Largest entity.')
    if code == 'ADSA':
        return ('Alpha Direct South Africa. GL codes prefixed ADSA_. '
                'Functional currency ZAR.')
    if code == 'UNI':
        return ('Unicoin. GL codes prefixed UNI_. Currency BWP.')
    if code == 'GCX':
        return ('Gaborone Coin Exchange. GL codes prefixed GCX_. Currency BWP.')
    if code == 'RSA':
        return ('Alpha Direct Roadside Assist. GL codes prefixed RSA_. '
                'Currency BWP.')
    if code == 'VCM':
        return ('VCM. GL codes prefixed VCM_. Currency BWP.')
    if code == 'AIZ':
        return ('Alpha Insurance Zambia. GL codes prefixed AIZ_. Currency ZMW.')
    return ('Multi-entity group. Use the section schema verbatim — no '
            'special per-entity conventions.')
