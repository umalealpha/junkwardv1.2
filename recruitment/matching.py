"""
recruitment/matching.py — LOCAL, deterministic CV↔job matching.

No external AI, no network — so no candidate data ever leaves Omni (honours
AD-POL-AI-GOV-001 and what HR was told). It detects skills in free text against
a curated taxonomy (with common synonyms), then scores a CV's skills against a
requisition's required skills. Explainable: the score is the share of required
skills found, and we return exactly which were matched and which are missing.

A future upgrade can add semantic matching via a LOCAL model; the interface
(extract_skills / score_candidate) stays the same.
"""
from __future__ import annotations

import re

# Curated skill taxonomy. canonical -> list of surface forms/synonyms (lowercase).
# Insurance/finance-weighted for Alpha Direct, plus general professional skills.
SKILL_SYNONYMS: dict[str, list[str]] = {
    "underwriting": ["underwriting", "underwriter", "risk assessment"],
    "claims": ["claims", "claims handling", "claims assessment", "loss adjusting"],
    "reinsurance": ["reinsurance", "treaty", "facultative", "retrocession"],
    "actuarial": ["actuarial", "actuary", "reserving", "pricing model"],
    "insurance": ["insurance", "short-term insurance", "life insurance", "assurance"],
    "accounting": ["accounting", "accountant", "bookkeeping", "general ledger", "gl"],
    "ifrs": ["ifrs", "ifrs 17", "ifrs17", "gaap"],
    "audit": ["audit", "auditing", "internal audit", "external audit"],
    "tax": ["tax", "taxation", "vat", "paye", "burs"],
    "financial reporting": ["financial reporting", "management accounts", "financial statements"],
    "budgeting": ["budgeting", "budget", "forecasting", "forecast"],
    "compliance": ["compliance", "nbfira", "aml", "cft", "kyc", "regulatory"],
    "sales": ["sales", "business development", "broker", "distribution"],
    "customer service": ["customer service", "client service", "call centre", "call center"],
    "excel": ["excel", "spreadsheets", "pivot table", "vlookup"],
    "power bi": ["power bi", "powerbi", "tableau", "data visualisation", "data visualization"],
    "sql": ["sql", "postgres", "mysql", "database query"],
    "python": ["python", "pandas", "numpy"],
    "django": ["django", "drf", "django rest"],
    "javascript": ["javascript", "typescript", "react", "node"],
    "project management": ["project management", "prince2", "pmp", "agile", "scrum"],
    "hr": ["human resources", "human capital", "hr", "recruitment", "payroll"],
    "communication": ["communication", "presentation", "report writing"],
    "leadership": ["leadership", "team lead", "management", "supervisor"],
    "negotiation": ["negotiation", "negotiate"],
    "analysis": ["analysis", "analytical", "data analysis", "financial analysis"],
    "marketing": ["marketing", "digital marketing", "social media"],
    "legal": ["legal", "law", "contract", "litigation"],
    "it support": ["it support", "helpdesk", "network", "systems administration"],
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower())


def extract_skills(text: str) -> list[str]:
    """Canonical skills detected in free text (whole-word / phrase match)."""
    hay = _norm(text)
    found = []
    for canonical, forms in SKILL_SYNONYMS.items():
        for form in forms:
            # word-boundary for single tokens; substring for multi-word phrases
            if " " in form:
                if form in hay:
                    found.append(canonical)
                    break
            elif re.search(rf"\b{re.escape(form)}\b", hay):
                found.append(canonical)
                break
    return sorted(set(found))


def normalise_required(required: list[str]) -> list[str]:
    """Map free-typed required skills onto canonical skills where possible; keep
    unknown ones as-is (lowercased) so HR can require anything."""
    out = []
    for raw in required or []:
        r = _norm(str(raw)).strip()
        if not r:
            continue
        mapped = None
        for canonical, forms in SKILL_SYNONYMS.items():
            if r == canonical or r in forms:
                mapped = canonical
                break
        out.append(mapped or r)
    # de-dup preserving order
    seen, res = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            res.append(s)
    return res


def score_candidate(required_skills: list[str], cv_text: str, cv_skills: list[str] | None = None) -> dict:
    """Score a CV against a requisition's required skills.

    Returns {score 0..100, matched:[...], missing:[...], candidate_skills:[...]}.
    Score = share of required skills present in the CV. If the requisition lists
    no required skills, fall back to how many taxonomy skills the CV shows
    (capped) so a ranking still exists.
    """
    cv_skills = cv_skills if cv_skills is not None else extract_skills(cv_text)
    req = normalise_required(required_skills)
    cv_norm_text = _norm(cv_text)
    cv_set = set(cv_skills)

    if not req:
        # No required list — rank by breadth of detected skills (0..100, cap 12).
        score = min(100, round(len(cv_set) / 12 * 100))
        return {"score": score, "matched": sorted(cv_set), "missing": [],
                "candidate_skills": sorted(cv_set)}

    matched, missing = [], []
    for r in req:
        # matched if the required skill is a detected canonical skill, OR the raw
        # required phrase literally appears in the CV text (covers custom skills).
        if r in cv_set or (" " in r and r in cv_norm_text) or re.search(rf"\b{re.escape(r)}\b", cv_norm_text):
            matched.append(r)
        else:
            missing.append(r)

    score = round(len(matched) / len(req) * 100) if req else 0
    return {"score": int(score), "matched": matched, "missing": missing,
            "candidate_skills": sorted(cv_set)}
