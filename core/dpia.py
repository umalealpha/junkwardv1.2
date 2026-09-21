"""
core/dpia.py — Data Protection Impact Assessment register (DPA s.65; audit S-1).

A drafted DPIA per high-risk processing activity, for the DPO/legal to review and
sign off. Served read-only on the Data Protection dashboard. CFO directive 2026-07-20.
These are DRAFTS — confirm and formally approve with the DPO/legal (status flips to
'approved' via the register once signed off).
"""
DPIA_VERSION = "2026-07-20"

DPIAS = [
    {
        "key": "kyc",
        "module": "KYC / identity verification (Omang, passport, selfies)",
        "risk": "high",
        "status": "draft",
        "purpose": "Verify customer identity and prevent fraud, as required for insurance and by law.",
        "data": "National ID (Omang), passport, date of birth, address, ID photo/selfie.",
        "necessity": "Necessary for the insurance contract and legal (AML/KYC) obligations; no less-intrusive alternative for identity assurance.",
        "risks": [
            "Special-category / high-value ID data exposed if access is too broad.",
            "ID numbers or images sent to an external AI in the clear.",
            "ID images retained longer than needed.",
        ],
        "mitigations": [
            "Access role-gated; ID compares run server-side, the number never returned to the caller.",
            "PII firewall tokenises ID data before any external-LLM call (LIVE).",
            "Biometric face-match gated behind this DPIA + consent (currently OFF).",
            "Retention policy to enforce deletion after the legal period (in progress).",
        ],
        "residual_risk": "medium — closes to low once retention purge + field encryption land.",
    },
    {
        "key": "payroll",
        "module": "Payroll & HR",
        "risk": "high",
        "status": "draft",
        "purpose": "Pay employees and meet tax/labour obligations.",
        "data": "National ID, bank account, salary, tax, DOB, leave, performance.",
        "necessity": "Necessary for the employment contract and legal (BURS/tax/labour) duties.",
        "risks": [
            "Bank/ID data readable by too many staff.",
            "Data at rest not encrypted.",
        ],
        "mitigations": [
            "Payroll role-gated; bank number masked in the API; full-record views now audit-logged.",
            "Field-level encryption of ID/bank being rolled out (staged); DB-volume encryption planned.",
        ],
        "residual_risk": "medium — closes to low on field + volume encryption.",
    },
    {
        "key": "ai",
        "module": "AI assistance (Graphite Aware / DeepSeek chain)",
        "risk": "high",
        "status": "draft",
        "purpose": "Help staff query data, assess documents/claims and KYC.",
        "data": "Whatever a query/document contains — potentially names, IDs, claims.",
        "necessity": "Efficiency aid; personal data is not required by the AI and is tokenised out.",
        "risks": ["Personal data leaving to an external/cross-border AI in the clear."],
        "mitigations": [
            "PII firewall tokenises personal data before any external-LLM call (LIVE, tokenize mode).",
            "DeepSeek kill-switch → firewalled Gemini fallback; local model server planned to keep it in-country.",
            "Every AI call timed + logged (speed log) for audit evidence.",
        ],
        "residual_risk": "low — while the firewall stays in tokenize.",
    },
    {
        "key": "telematics",
        "module": "Telematics / driver scoring (Nexus)",
        "risk": "medium",
        "status": "draft",
        "purpose": "Usage-based motor pricing and driver feedback.",
        "data": "Location, trips, driving behaviour, vehicle.",
        "necessity": "For the telematics product the customer opts into.",
        "risks": ["Location data is sensitive; processed via a cross-border provider (WebFleet, EU)."],
        "mitigations": ["Opt-in; processor DPA to be documented (H-6); data minimised to scoring."],
        "residual_risk": "medium — closes on the processor DPA.",
    },
]


def dpia_summary() -> dict:
    return {
        "total": len(DPIAS),
        "approved": sum(1 for d in DPIAS if d["status"] == "approved"),
        "draft": sum(1 for d in DPIAS if d["status"] == "draft"),
        "high_risk": sum(1 for d in DPIAS if d["risk"] == "high"),
        "version": DPIA_VERSION,
    }
