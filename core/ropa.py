"""
core/ropa.py — Records of Processing Activities (DPA audit S-2; Art. ROPA).

A drafted processing register: each activity → lawful basis, data, recipients,
retention and cross-border transfers. Served read-only on the Data Protection
dashboard for the DPO to review, complete and maintain. CFO directive 2026-07-20.
DRAFT — confirm lawful-basis determinations with legal.
"""
ROPA_VERSION = "2026-07-20"

ROPA = [
    {
        "key": "payroll", "activity": "Payroll & HR administration",
        "basis": "Employment contract + legal obligation (BURS/tax/labour law)",
        "data": "Name, national ID, bank account, salary, tax, DOB, leave, performance",
        "recipients": "HR & Finance; BURS; the employee's bank; audited IT providers",
        "retention": "Up to 6 years after employment ends (longer where a law requires)",
        "transfers": "SA / EU / USA (IT services) — safeguarded; AI paths tokenised",
    },
    {
        "key": "underwriting", "activity": "Policy underwriting & administration",
        "basis": "Insurance contract + legal obligation",
        "data": "Name, contact, national ID, policy, risk & premium details",
        "recipients": "Underwriting staff; reinsurers; regulators",
        "retention": "Policy term + the legal retention period after",
        "transfers": "South Africa (hosting, adequacy-listed)",
    },
    {
        "key": "claims", "activity": "Claims handling (incl. health/motor)",
        "basis": "Insurance contract + legal obligation",
        "data": "Claim details, incident, health data (health claims), bank for payout",
        "recipients": "Claims staff; assessors; panel repairers; medical providers; reinsurers",
        "retention": "The legal retention period for claims records",
        "transfers": "South Africa",
    },
    {
        "key": "kyc", "activity": "KYC / identity verification",
        "basis": "Legal obligation (AML/KYC) + insurance contract",
        "data": "Omang / passport, DOB, address, ID image",
        "recipients": "Verification staff only — ID compares run server-side, the number never leaves",
        "retention": "The legal KYC retention period",
        "transfers": "None in the clear; any AI check is tokenised first",
    },
    {
        "key": "telematics", "activity": "Telematics / driver scoring (Nexus)",
        "basis": "Consent + contract (customer opts in)",
        "data": "Location, trips, driving behaviour, vehicle",
        "recipients": "WebFleet (telematics processor)",
        "retention": "For the scoring/policy period",
        "transfers": "EU (WebFleet) — processor DPA to be documented (H-6)",
    },
    {
        "key": "marketing", "activity": "Marketing & rewards (Alpha Rewards)",
        "basis": "Consent",
        "data": "Contact details, preferences, points balance",
        "recipients": "Internal only",
        "retention": "Until the customer opts out",
        "transfers": "South Africa",
    },
    {
        "key": "ai", "activity": "AI assistance (Aware / document & claim help)",
        "basis": "Legitimate interest (staff efficiency) — no personal data required by the AI",
        "data": "Query/document content — personal data tokenised out before it leaves",
        "recipients": "AI engines (tokens only, via the PII firewall)",
        "retention": "Timing metadata only (speed log); no content stored",
        "transfers": "Tokenised only (no raw PII crosses a border)",
    },
]


def ropa_summary() -> dict:
    return {"total": len(ROPA), "version": ROPA_VERSION}
