"""Graphite Aware — MODE B: KYC / ID check.

Three checks, each degrades on its own:
  5a. Is the policy KYC-compliant?         → v_policy_kyc_status (always works)
  5b. Does the uploaded ID number match the number on record?
        → OCR the ID, compare to customer_kyc server-side. The Omang NEVER
          enters the LLM prompt, the transcript, or the API response — only a
          match / no-match boolean + a last-2-digits hint.
  5c. Does the ID PHOTO match the on-record verified face? (fake-ID / swapped
        photo) → Rekognition CompareFaces. OFF by default (no AWS wired) →
        'manual_review', never a false pass.

DPA-safe: 5b uses aware.engine.kyc_select() which bypasses the LLM-facing PII
mask and returns ONLY to Python.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from ..engine import kyc_select
from ..extract import detect_id_numbers, detect_policy_numbers


def _san(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", '', s or '')[:32]


def _digits(s: str) -> str:
    return re.sub(r'\D', '', s or '')


def check_kyc(upload_texts: List[str], email_text: str, policy_no: str,
              user) -> Dict[str, Any]:
    from core.face_verify import rekognition

    id_text = '\n'.join([t for t in upload_texts if t])
    pol = policy_no.strip() if policy_no else ''
    if not pol:
        found = detect_policy_numbers(id_text, email_text or '')
        pol = found[0] if found else ''
    if not pol:
        return {'overall': 'indeterminate',
                'easy_summary': 'No policy number found. Type the policy number and upload the ID.'}

    result: Dict[str, Any] = {'policy_no': pol}

    # 5a — KYC compliant flag
    kyc = kyc_select(
        "SELECT kyc_status, kyc_compliance FROM v_policy_kyc_status "
        "WHERE policyNumber = %s LIMIT 1", [pol])
    if not kyc:
        return {'overall': 'indeterminate', 'policy_no': pol,
                'easy_summary': f'Policy {pol} not found.'}
    kyc_status = (kyc[0].get('kyc_status') or '')
    # Live Graphite values: Approve(160k), approved, Unapprove, Unchecked,
    # Recheck, Recheck(KYC Expired), rejected.
    compliant = str(kyc_status).strip().lower() in ('approve', 'approved', 'compliant', 'verified')
    result['policy_kyc_compliant'] = compliant
    result['kyc_status'] = kyc_status

    # 5b — ID number match (server-side only; omang never surfaced)
    id_match = None
    hint = None
    onrec = kyc_select(
        "SELECT ck.omangNumber, ck.passportNumber FROM customer_kyc ck "
        "JOIN policies p ON ck.customer_id = p.customer_id "
        "WHERE p.policyNumber = %s ORDER BY ck.id DESC LIMIT 1", [pol])
    if onrec and id_text.strip():
        rec_omang = _digits(onrec[0].get('omangNumber'))
        rec_pass = re.sub(r'[^A-Za-z0-9]', '', (onrec[0].get('passportNumber') or '')).upper()
        cand = detect_id_numbers(id_text)
        cand_omang = {_digits(x) for x in cand['omang']}
        cand_pass = {x.upper() for x in cand['passport']}
        # Only assert a MISMATCH (fraud) when we're confident the upload is an
        # actual ID document — i.e. it carries an ID label. Otherwise a stray
        # 9-digit token (invoice/serial) or an OCR misread must NOT accuse a
        # real customer of fraud → leave None ("couldn't confirm → manual").
        has_id_label = bool(re.search(
            r'omang|identity|national\s*id|id\s*(no|number)|passport|republic\s+of\s+botswana',
            id_text, re.I))
        if rec_omang and rec_omang in cand_omang:
            id_match = True; hint = f'…{rec_omang[-2:]}'
        elif rec_pass and rec_pass in cand_pass:
            id_match = True; hint = f'…{rec_pass[-2:]}'
        elif (cand_omang or cand_pass) and has_id_label:
            id_match = False  # a real ID doc, but the number is NOT the one on record
        # else: unclear / low-confidence read → leave None (manual review)
    result['id_number_match'] = id_match
    if hint:
        result['id_hint'] = hint

    # 5c — face compare (fake ID). Off until Rekognition is wired.
    result['face_check'] = {'available': rekognition.is_enabled(),
                            'similarity': None, 'match': None}

    # overall verdict (explicit — no dead code)
    fc = result['face_check']
    if id_match is False:
        overall = 'fail'                       # uploaded ID number != record → fraud
    elif compliant and id_match is True:
        if not fc['available']:
            overall = 'pass_face_unchecked'    # doc+number ok; photo needs manual eyeball
        else:
            overall = 'pass' if fc.get('match') else 'fail'   # face ran: mismatch = fraud
    else:
        overall = 'manual_review'              # not compliant, or ID number unreadable
    result['overall'] = overall

    result['easy_summary'] = _summary(result)
    return result


def _summary(r: Dict[str, Any]) -> str:
    pol = r.get('policy_no')
    comp = 'KYC compliant' if r.get('policy_kyc_compliant') else 'NOT KYC compliant'
    if r.get('overall') == 'fail':
        return (f'⚠️ Policy {pol}: the ID number on the uploaded document does NOT '
                f'match the ID on record. Possible fraud — do not proceed, escalate.')
    if r.get('id_number_match') is True:
        face = ' Face check not run (needs manual eyeball of the photo).' \
            if not r['face_check']['available'] else ''
        return (f'Policy {pol} is {comp}. The uploaded ID number matches the one on '
                f'record ({r.get("id_hint","")}).{face}')
    if r.get('id_number_match') is None:
        return (f'Policy {pol} is {comp}. Could not read an ID number from the upload '
                f'— send a clearer photo, or check manually.')
    return f'Policy {pol}: {comp}. Manual review needed.'
