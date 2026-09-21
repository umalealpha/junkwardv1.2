"""Graphite Aware — MODE A: claim payability.

User uploads the assessment report (and/or pastes the email); we extract it,
pull the claim + policy facts from Graphite, and let the model apply a fixed
payability rubric. Output: payable / not_payable / refer + an easy summary.
No customer PII is sent — only claim/policy status, dates and money.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from ..engine import run_select, _parse_step
from ..extract import detect_policy_numbers

RUBRIC = """You are a Botswana short-term insurance claims assessor at Alpha
Direct. Decide if a claim is PAYABLE from (1) the assessment/report text the
user gave and (2) the policy + claim facts pulled from the live system.

Apply this rubric, in order:
  1. Is the policy IN FORCE on the loss date? (status active, loss between
     term_start and term_end)
  2. Are premiums current / not in arrears at loss date?
  3. Is the peril/cause of loss a COVERED event (not an exclusion)?
  4. Is the claimed amount within the sum insured, net of excess?
  5. Any block — KYC non-compliant, salvage not in yard, fraud indicators,
     policy cancelled/lapsed before loss?

Return ONE json object ONLY, in THIS field order, and KEEP IT SHORT (it must
fit in a small response — verdict and easy_summary first so they always land):
{"verdict":"payable"|"not_payable"|"refer",
 "easy_summary":"1-2 plain sentences a manager reads first",
 "confidence":"high"|"medium"|"low",
 "reasons":["<=12-word bullet", ...max 4],
 "checks":{"in_force":true,"premiums_current":true,"peril_covered":true,
           "within_si":true,"no_block":true}}
If a fact is missing, say so in one reason and lean to "refer" — never invent."""


def _claim_facts(policy_no: str) -> Dict[str, Any]:
    """Pull claim + policy facts for the policy (masked path is fine — no PII
    needed, just status/dates/money)."""
    pol = run_select(
        "SELECT policyNumber, status AS policy_status, premium, annual_premium, "
        "premium_freq, term_start_date, term_end_date, balance "
        f"FROM policies WHERE policyNumber = '{_san(policy_no)}' LIMIT 1")
    # NB: no free-text fields (incident_description etc.) — those can carry
    # inline customer PII and are never sent to the LLM. The incident detail
    # comes from the user's uploaded assessment, not from Graphite.
    claims = run_select(
        "SELECT c.claim_number, c.claim_type, c.status AS claim_status, "
        "c.type_of_loss, c.reported_date, c.created_at "
        "FROM claims c JOIN policies p ON c.policy_id = p.id "
        f"WHERE p.policyNumber = '{_san(policy_no)}' ORDER BY c.id DESC LIMIT 5")
    reserves = run_select(
        "SELECT nc.claim_number, nc.reserve_amount, nc.paid_amount, nc.status, "
        "nc.date_of_loss "
        "FROM new_claims nc JOIN policies p ON nc.policy_id = p.id "
        f"WHERE p.policyNumber = '{_san(policy_no)}' ORDER BY nc.id DESC LIMIT 5")
    kyc = run_select(
        "SELECT kyc_status FROM v_policy_kyc_status "
        f"WHERE policyNumber = '{_san(policy_no)}' LIMIT 1")
    return {
        'policy': pol[1][0] if pol[1] else None,
        'claims': claims[1],
        'reserves': reserves[1],
        'kyc_status': (kyc[1][0].get('kyc_status') if kyc[1] else None),
    }


def _san(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", '', s or '')[:32]


def assess_payability(upload_texts: List[str], email_text: str,
                      policy_no: str, user) -> Dict[str, Any]:
    from core.ai_assist import reasoning_complete

    report = ('\n\n'.join([t for t in upload_texts if t]) + '\n\n' + (email_text or '')).strip()
    if not report:
        return {'verdict': 'indeterminate',
                'easy_summary': 'No report text found. Upload the assessment report or paste the email.'}

    pol = policy_no.strip() if policy_no else ''
    if not pol:
        found = detect_policy_numbers(report)
        pol = found[0] if found else ''
    if not pol:
        return {'verdict': 'indeterminate',
                'easy_summary': 'Could not find a policy number in the document. Type it in and try again.'}

    facts = _claim_facts(pol)
    if not facts['policy']:
        return {'verdict': 'indeterminate', 'policy_no': pol,
                'easy_summary': f'Policy {pol} was not found in the system.'}

    prompt = (
        f"POLICY NUMBER: {pol}\n\n"
        f"ASSESSMENT / REPORT TEXT (from the user's upload):\n{report[:8000]}\n\n"
        f"LIVE SYSTEM FACTS:\n{json.dumps(facts, default=str)[:5000]}\n\n"
        "Apply the rubric and return the json verdict.")
    raw = reasoning_complete(prompt, system_prompt=RUBRIC, timeout=60.0)
    step = _parse_step(raw)
    if not step.get('verdict'):
        step = _regex_verdict(raw)
    step['policy_no'] = pol
    return step


def _regex_verdict(raw: str) -> Dict[str, Any]:
    """Deterministic field extraction when json.loads can't handle the model's
    output (truncation, stray control chars). Always yields a usable verdict."""
    def grab(key):
        m = re.search(r'"' + key + r'"\s*:\s*"((?:[^"\\]|\\.)*)"', raw or '', re.S)
        return m.group(1).replace('\\"', '"').replace('\\n', ' ').strip() if m else ''
    verdict = (grab('verdict') or 'refer').lower()
    if verdict not in ('payable', 'not_payable', 'refer'):
        verdict = 'refer'
    rm = re.search(r'"reasons"\s*:\s*\[(.*?)\]', raw or '', re.S)
    reasons = re.findall(r'"((?:[^"\\]|\\.)*)"', rm.group(1)) if rm else []
    summary = grab('easy_summary')
    if not summary:
        # synthesise a clean line from the verdict — never dump raw JSON
        base = {'payable': 'This claim looks payable.',
                'not_payable': 'This claim is not payable.',
                'refer': 'Refer this claim for manual review.'}[verdict]
        first = next((r for r in reasons if r), '')
        summary = (base + (' ' + first if first else '')).strip()
    return {'verdict': verdict, 'confidence': grab('confidence') or 'low',
            'reasons': [r for r in reasons if r][:6], 'easy_summary': summary}
