"""reporting/equity_data.py — Alpha Direct Insurtech "Capital Story" equity data.

Source: Legakwa Ntabeni (Group Senior Accountant), "The Capital Story"
(2026-06-19) + the backing xlsx (Timeline / Cap Table / Dilution Scenarios /
Odoo Recon). Structured along Open Cap Format (OCF) concepts
(Open-Cap-Table-Coalition/Open-Cap-Format-OCF): Issuer, StockClass, StockPlan,
Stakeholder, StockIssuance — so the omni Equity section speaks the industry
standard (the same model Carta/Pulley/LTSE use) and can export to OCF/OCX later.

This is reference/board data for the holding company — NOT customer PII. The API
that serves it is whitelist-gated (EXCO / Finance / admin) in equity_views.py.
All amounts USD unless noted.
"""
from __future__ import annotations

# ── OCF: Issuer ───────────────────────────────────────────────────────────────
ISSUER = {
    "legal_name": "Alpha Direct Insurtech Pte Ltd",
    "country": "Singapore",
    "formation_date": "2020-12-23",
    "uen": "202028406Z",
    "currency": "USD",
}

# ── KPI headline tiles ────────────────────────────────────────────────────────
KPIS = [
    {"key": "raised",     "label": "Total Capital Raised", "value": "$3.62M", "sub": "USD 3,617,728 across all rounds"},
    {"key": "fd_shares",  "label": "Fully Diluted Shares", "value": "22.0M",  "sub": "22,005,676 shares outstanding"},
    {"key": "valuation",  "label": "Current Valuation",    "value": "$40M",   "sub": "Pre-money (5-Year Model basis)"},
    {"key": "next_round", "label": "Next Round Target",    "value": "$5.0M",  "sub": "Botswana Tech Fund — Sep 2026"},
    {"key": "gwp",        "label": "GWP FY2025",           "value": "$9.31M", "sub": "+25% YoY growth"},
]

# ── OCF: StockClass + instruments (Capital Raised by Instrument) ───────────────
INSTRUMENTS = [
    {"name": "Ordinary Class B (ORB)", "class_type": "COMMON",    "label": "Common — Founders",  "cash_usd": 1783637, "shares": 16350000, "price": 0.109, "status": "Active"},
    {"name": "Ordinary Class A (ORA)", "class_type": "COMMON",    "label": "Common — External",  "cash_usd": 909091,  "shares": 4081634,  "price": 0.223, "status": "Active"},
    {"name": "Series A Preferred (PA)","class_type": "PREFERRED", "label": "Preferred Stock",    "cash_usd": 325000,  "shares": 282609,   "price": 1.150, "status": "Active"},
    {"name": "Convertible Notes (CN2)","class_type": "CONVERTIBLE","label": "Equity (reclassified)","cash_usd": 600000,"shares": 1291433, "price": 0.465, "status": "Converted Jun 2025"},
]
INSTRUMENTS_TOTAL = {"cash_usd": 3617728, "shares": 22005676}

# ── OCF: StockIssuance / transactions (Fundraising Timeline) ──────────────────
TIMELINE = [
    {"date": "2020-12",   "title": "Incorporation — Alpha Direct Insurtech Pte Ltd (Singapore)",
     "detail": "Holding company incorporated in Singapore. Certificate issued 23 Dec 2020. Initial $1 share to Arjun Parameswaran.", "tag": "$1 — Founding Share"},
    {"date": "2021-01/03","title": "Pre-Series A Convertible Notes (CN2) — $600,000",
     "detail": "Four investors subscribed 0% interest convertible notes at BWP 5.1106/share implied conversion. Launch Africa $250K, Century Oak/Pine Hill $250K, Ntja-Tharo $40K, Fexty $60K.", "tag": "$600,000 — 1,291,433 shares on conversion"},
    {"date": "2021-07-01","title": "QIHL Debenture Conversion — ORA Shares Issued — $909,091",
     "detail": "Botswana debenture notes converted to 4,081,634 Ordinary Class A shares at BWP 2.45/share. Seven investors including Vedanta Ventures, Balram, Harshadkumar, and others.", "tag": "$909,091 — 4,081,634 ORA shares"},
    {"date": "2021-07-22","title": "ESOP Plan Established",
     "detail": "First ESOP by an insurer in Botswana. Stock Ownership & Option Plan 2021 adopted — 4,086,328 shares reserved (20% of issued capital). RSUs for C-Suite on ORB, Options on ORA for employees.", "tag": "ESOP — 4,086,328 reserved"},
    {"date": "2021-07-29","title": "Series A — Tranche 1: Century Oak Capital — $125,000",
     "detail": "108,696 preferred shares at $1.15/share. First institutional preferred equity.", "tag": "$125,000 — 108,696 PA @ $1.15"},
    {"date": "2021-12-01","title": "Singapore Restructuring — ORB Shares Transfer",
     "detail": "16,350,000 ORB shares ($1,783,636 / BWP 18.6M) transferred from Botswana to Singapore. Six founders.", "tag": "$1,783,636 — 16,350,000 ORB"},
    {"date": "2022-10-21","title": "Series A — Tranche 2: Golden Deer / Pokorny — $100,000",
     "detail": "86,957 preferred shares at $1.15/share.", "tag": "$100,000 — 86,957 PA @ $1.15"},
    {"date": "2022-11-18","title": "Series A — Tranche 3 (Final Close): Gujadhur Family — $100,000",
     "detail": "Santosh & Deepti ($50K) + Tej Kumar & Saumya ($50K) = 86,956 PA shares. Series A total: $325,000.", "tag": "$100,000 — Series A FINAL CLOSE"},
    {"date": "2025-06-27","title": "CN2 Reclassified to Equity",
     "detail": "$600K convertible notes moved from liabilities to Share Equity (307000). Journal MISC/2025/0045 in Odoo.", "tag": "Reclass — acct 307000"},
    {"date": "2026-09 (target)","title": "Next Round — USD 5M (Botswana Tech Fund)",
     "detail": "$40M pre-money. Use the Scenario Modeller to explore different terms.", "tag": "$5,000,000 target"},
]

# ── OCF: Stakeholder + holdings (Fully Diluted Cap Table) ─────────────────────
CAP_TABLE = [
    {"holder": "Ramaa Parameswaran",        "shares": 5972992, "pct": 27.14, "klass": "ORB",     "usd": 628417, "note": "Founder — largest holder"},
    {"holder": "Balram Ottapathu",          "shares": 3700386, "pct": 16.82, "klass": "ORB+ORA", "usd": 482997, "note": "Founder + external"},
    {"holder": "Harshadkumar Patel",        "shares": 3700386, "pct": 16.82, "klass": "ORB+ORA", "usd": 482997, "note": "Founder + external"},
    {"holder": "Dr. Kiran Chhotubhai Patel","shares": 2686667, "pct": 12.21, "klass": "ORB",     "usd": 274545, "note": "Founder"},
    {"holder": "Vedanta Ventures",          "shares": 1346939, "pct": 6.12,  "klass": "ORA",     "usd": 300000, "note": "External investor"},
    {"holder": "Arjun Parameswaran",        "shares": 961113,  "pct": 4.37,  "klass": "ORB",     "usd": 92794,  "note": "Founder"},
    {"holder": "Kamya Arun Iyer",           "shares": 961112,  "pct": 4.37,  "klass": "ORB",     "usd": 92794,  "note": "Founder"},
    {"holder": "Winterhold / Century Oak",  "shares": 646793,  "pct": 2.94,  "klass": "PA+CN2",  "usd": 375000, "note": "Series A + CN2"},
    {"holder": "Fexty Investments",         "shares": 618939,  "pct": 2.81,  "klass": "ORA+CN2", "usd": 169091, "note": "ORA + CN2"},
    {"holder": "Launch Africa Ventures",    "shares": 538097,  "pct": 2.45,  "klass": "CN2",     "usd": 250000, "note": "CN2 $250K"},
    {"holder": "Tarsem Kumar",              "shares": 408163,  "pct": 1.85,  "klass": "ORA",     "usd": 90909,  "note": "External"},
    {"holder": "Ntja-Tharo Holdings",       "shares": 167729,  "pct": 0.76,  "klass": "ORA+CN2", "usd": 58182,  "note": "ORA + CN2"},
    {"holder": "Khumo Maatla Holdings",     "shares": 122449,  "pct": 0.56,  "klass": "ORA",     "usd": 27273,  "note": "External"},
    {"holder": "Golden Deer Establishment", "shares": 86957,   "pct": 0.40,  "klass": "PA",      "usd": 100000, "note": "Series A (Oct 2022)"},
    {"holder": "Santosh & Deepti Gujadhur", "shares": 43478,   "pct": 0.20,  "klass": "PA",      "usd": 50000,  "note": "Series A (Nov 2022)"},
    {"holder": "Tej Kumar & Saumya Gujadhur","shares": 43478,  "pct": 0.20,  "klass": "PA",      "usd": 50000,  "note": "Series A (Nov 2022)"},
]
CAP_TABLE_TOTAL = {"shares": 22005676, "pct": 100.00, "usd": 3524999}

# ── OCF: StockPlan (ESOP) ─────────────────────────────────────────────────────
ESOP = {
    "pool_size": 4086328,
    "granted": 508737,
    "available": 3577591,
    "pct_of_capital": 20,
    "pct_utilized": 12.5,
    "option_holders": 19,
    "fd_ownership_pct": 15.7,
    "fd_shares_basis": 26092005,
    "tiers": [
        {"name": "C-Suite RSUs (ORB)", "pct": 15, "detail": "Restricted Stock Units on Class B shares"},
        {"name": "Employee Options (ORA)", "pct": 5, "detail": "Stock Options on Class A shares"},
    ],
    "plan_terms": [
        {"k": "Plan Name", "v": "Alpha Direct Insurtech Stock Ownership and Option Plan 2021", "note": "Effective 1 July 2021. First ESOP by an insurer in Botswana."},
        {"k": "Expiry", "v": "10 Years from Grant Date", "note": "Per Section 8.2(b). Options expire if unexercised by the expiry date."},
        {"k": "Vesting", "v": "Per Letter of Grant", "note": "Board/Committee determines vesting schedule. Requires continued employment (Section 7.3)."},
        {"k": "Exercise Price", "v": "Board-Determined", "note": "Set by the Board at grant. Cashless exercise permitted (Section 9)."},
        {"k": "Eligibility", "v": "Employees, Advisors, Consultants", "note": "All group subsidiaries: Botswana, South Africa, Zambia, India, Singapore."},
        {"k": "Lock-In", "v": "Per Grant Terms", "note": "Lock-in per Section 10. Malus/clawback in Section 13."},
        {"k": "Liquidity Events", "v": "IPO / Strategic Sale / Offer / Buyback", "note": "Exercisable on listing, >50% sale, investor offer, or buyback."},
        {"k": "Governing Law", "v": "Singapore", "note": "UEN 202028406Z. Plan governed by Singapore law."},
    ],
    # Individual option holders — Carta (as at 31 Mar 2026)
    "grants": [
        {"grantee": "Prathap Ganesharajah", "units": 167021, "pct_pool": 4.09, "pct_fd": 0.640},
        {"grantee": "Arjun Iyer",           "units": 148410, "pct_pool": 3.63, "pct_fd": 0.569},
        {"grantee": "Arun Iyer",            "units": 137196, "pct_pool": 3.36, "pct_fd": 0.526},
        {"grantee": "Unami Butale",         "units": 20612,  "pct_pool": 0.50, "pct_fd": 0.079},
        {"grantee": "Paul Beka",            "units": 14429,  "pct_pool": 0.35, "pct_fd": 0.055},
        {"grantee": "Bonno Ben",            "units": 2748,   "pct_pool": 0.07, "pct_fd": 0.011},
        {"grantee": "Kakale Botana",        "units": 2748,   "pct_pool": 0.07, "pct_fd": 0.011},
        {"grantee": "Bharath Balasubramanian","units": 2290, "pct_pool": 0.06, "pct_fd": 0.009},
        {"grantee": "Meduduetso Tlagae",    "units": 2199,   "pct_pool": 0.05, "pct_fd": 0.008},
        {"grantee": "Segolame Masilo",      "units": 2199,   "pct_pool": 0.05, "pct_fd": 0.008},
        {"grantee": "Wangu Moses",          "units": 1832,   "pct_pool": 0.04, "pct_fd": 0.007},
        {"grantee": "Ikanyeng Sechele",     "units": 1466,   "pct_pool": 0.04, "pct_fd": 0.006},
        {"grantee": "Moses Ncube",          "units": 1283,   "pct_pool": 0.03, "pct_fd": 0.005},
        {"grantee": "Goitsemang Ngwako",    "units": 824,    "pct_pool": 0.02, "pct_fd": 0.003},
        {"grantee": "Moemedi Mositiemang",  "units": 824,    "pct_pool": 0.02, "pct_fd": 0.003},
        {"grantee": "Galaletsang Dipitso",  "units": 824,    "pct_pool": 0.02, "pct_fd": 0.003},
        {"grantee": "Christopher Kelefatse","units": 733,    "pct_pool": 0.02, "pct_fd": 0.003},
        {"grantee": "Patience Phesodi",     "units": 733,    "pct_pool": 0.02, "pct_fd": 0.003},
        {"grantee": "Bakang Valela",        "units": 366,    "pct_pool": 0.01, "pct_fd": 0.001},
    ],
    "granted_total": {"units": 508737, "pct_pool": 12.45, "pct_fd": 1.950},
}

# ── Scenario Modeller defaults (Dilution Scenarios sheet) ─────────────────────
SCENARIO = {
    "current_fd_shares": 22005676,
    "pre_money_usd": 40000000,
    "round_size_usd": 5000000,
    "esop_topup_pct": 0.0,   # optional pool top-up as % of post-round FD
}

# ── Odoo equity reconciliation (Odoo Recon sheet) ─────────────────────────────
ODOO_RECON = {
    "equity_account": "307000 — Share Equity",
    "note": "CN2 $600K reclassified liabilities → Share Equity via journal MISC/2025/0045 (27 Jun 2025).",
}

SOURCES = [
    "Share register & certificates (Alpha Direct Insurtech Pte Ltd, Singapore)",
    "Stock Ownership and Option Plan 2021 (Plan document)",
    "Carta cap table (as at 31 Mar 2026)",
    "Odoo equity ledger — account 307000 Share Equity",
    "5-Year Model (valuation basis)",
]


def capital_story() -> dict:
    """Full Capital Story payload for the omni Equity section."""
    return {
        "issuer": ISSUER,
        "kpis": KPIS,
        "instruments": INSTRUMENTS,
        "instruments_total": INSTRUMENTS_TOTAL,
        "timeline": TIMELINE,
        "cap_table": CAP_TABLE,
        "cap_table_total": CAP_TABLE_TOTAL,
        "esop": ESOP,
        "scenario": SCENARIO,
        "odoo_recon": ODOO_RECON,
        "sources": SOURCES,
        "ocf_aligned": True,
    }
