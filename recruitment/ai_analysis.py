"""
recruitment/ai_analysis.py — AI CV assessment (CFO 2026-07-11).

Runs a real reasoning model over each CV to judge fit, strengths, gaps and to
suggest interview questions — on top of the deterministic skills score.

Privacy: the candidate's IDENTITY is stripped before the model sees anything —
the known name/email/phone (from the intake form) are removed, and the text is
then passed through core.ai_assist.is_safe_for_ai() (scrubs Omang/ID/bank/phone/
salary). Only anonymised skills/experience reach the model, via
reasoning_complete() (the CFO's failover chain Gemini→DeepSeek→…). If a CV is
mostly PII (unsafe) or no engine is configured, we skip AI and keep the
deterministic score.
"""
from __future__ import annotations

import json
import re

from core.ai_assist import DeepSeekUnavailable, is_safe_for_ai, reasoning_complete

SYSTEM = (
    "You are an experienced recruitment analyst for a Botswana insurance company "
    "(Alpha Direct). You receive an ANONYMISED CV (personal identity removed) and a "
    "job. Judge how well the candidate fits the role. Be specific and fair; do not "
    "invent facts not in the CV. Respond with ONLY valid JSON, no prose, no code "
    "fences, of exactly this shape: "
    '{"fit_score": <0-100 integer>, "summary": "<max 300 chars>", '
    '"strengths": ["<max 5>"], "gaps": ["<max 5>"], '
    '"interview_questions": ["<max 5 role-specific questions>"]}'
)


def _redact_identity(cv_text: str, name: str = "", email: str = "", phone: str = "") -> str:
    """Remove the known identity tokens before anything is sent to the model."""
    t = cv_text or ""
    for tok in (email, phone):
        if tok and len(tok) >= 4:
            t = re.sub(re.escape(tok), "[redacted]", t, flags=re.IGNORECASE)
    for part in (name or "").split():
        if len(part) >= 3:
            t = re.sub(rf"\b{re.escape(part)}\b", "[redacted]", t, flags=re.IGNORECASE)
    return t


def _parse(raw: str) -> dict | None:
    if not raw:
        return None
    s = raw.strip()
    # strip code fences if the model added them
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s.strip(), flags=re.IGNORECASE).strip()
    # grab the first {...} block
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if m:
        s = m.group(0)
    try:
        d = json.loads(s)
    except (ValueError, TypeError):
        return None

    def _list(v):
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()][:5]
        return []

    try:
        score = int(round(float(d.get("fit_score", 0))))
    except (ValueError, TypeError):
        score = 0
    return {
        "fit_score": max(0, min(100, score)),
        "summary": str(d.get("summary", "")).strip()[:400],
        "strengths": _list(d.get("strengths")),
        "gaps": _list(d.get("gaps")),
        "interview_questions": _list(d.get("interview_questions")),
    }


def analyse_cv(cv_text: str, *, job_title: str = "", jd_text: str = "",
               required_skills: list[str] | None = None,
               name: str = "", email: str = "", phone: str = "") -> dict | None:
    """Return the AI assessment dict, or None if the CV is empty/unsafe or no
    engine answers. Never raises — the caller keeps the deterministic score."""
    if not (cv_text or "").strip():
        return None
    # CFO design decision (recruitment brief): candidate CV text must NOT go to
    # external AI providers — this module was chosen over AuraHR / TalentMatch
    # precisely because those ship CVs to OpenAI/Gemini. Redaction strips known
    # identifiers, but free-text CVs still carry candidate PII (employers,
    # references, history). External analysis is therefore OFF unless expressly
    # enabled; the deterministic local match score stays the ranking engine.
    # (Fable review fix, 2026-07-13.)
    from django.conf import settings
    if not getattr(settings, "RECRUITMENT_EXTERNAL_AI", False):
        return None
    red = _redact_identity(cv_text, name, email, phone)
    report = is_safe_for_ai(red)
    if not report.safe:            # mostly PII → don't send
        return None
    clean = (report.redacted_text or red)[:6000]

    prompt = (
        f"JOB TITLE: {job_title or '(unspecified)'}\n"
        f"REQUIRED SKILLS: {', '.join(required_skills or []) or '(none listed)'}\n"
        f"JOB DESCRIPTION:\n{(jd_text or '')[:1500]}\n\n"
        f"ANONYMISED CANDIDATE CV:\n{clean}\n\n"
        "Return the JSON assessment now."
    )
    try:
        # max_tokens headroom: the CFO's first engine (Gemini) is a thinking
        # model — a small budget gets spent reasoning and truncates the JSON.
        raw = reasoning_complete(prompt, system_prompt=SYSTEM, max_tokens=2500)
    except DeepSeekUnavailable:
        return None
    except Exception:              # noqa: BLE001 — any transport error → skip AI
        return None
    return _parse(raw)
