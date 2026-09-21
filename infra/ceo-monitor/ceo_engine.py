#!/usr/bin/env python3
"""
CEO Monitor — Daily Exception Digest Generator (v6)
Alpha Direct Insurance Company (Pty) Ltd

v6 answers the CEO's Aug-2026 feedback on the daily brief. It sits ON TOP of the
proven v5 pipeline (noise gate, significance gate, party re-hydration, dedup,
same-customer merge) — those functions are IMPORTED from v5, not rewritten — and
adds four brain upgrades plus the new Claude-Design look:

  FIX 1/2  DEEP, SPECIFIC CARDS + a click-through "full story" link.
           Facts are extracted deterministically (amount, days-open, who, demand,
           what's at stake) and shown; "Why it matters" must carry a number and a
           name or it is rejected. Numbers/names come from the source text only —
           never invented (accuracy rule).

  FIX 3    RECOMMENDED ACTION per matter, from a fixed playbook (owner + what to
           do + by when). The recommended owner's escalate button is highlighted.

  FIX 4    PRIORITISATION that does not wobble. Two steps: (1) extract facts by
           rule, (2) score severity from the facts with a points rubric. Same
           inbox -> same ranking, every run. Top 5 only; the rest is a footnote.

  EXTRAS   "The one thing today" line, per-card change-tag (NEW / DAY N — NO REPLY),
           an accuracy footer, and feedback links. Meetings + future-events blocks
           are rendered here; the LIVE calendar feed is pending IT (ticket TKT-0105).

Brand: Dark Navy #0D1B2A, Orange #F4A623, serif headlines + sans body.

OWNER:    CFO Office, Alpha Direct Insurance
VERSION:  6.0 (12 Aug 2026) — IN BUILD, not yet wired to the live inbox.
"""

import argparse
import html
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


# ============================================================================
# CONFIG
# ============================================================================

NAVY = "#0D1B2A"
ORANGE = "#F4A623"
MAX_ITEMS = 5

SERIF = "Georgia, 'Times New Roman', serif"
SANS = "Arial, Helvetica, sans-serif"

# One-click escalation targets (label -> omni handle). Order = display order.
ESCALATION_TARGETS = [
    ("Paul", "pbeka"),
    ("Arjun", "arjuniyer"),
    ("Unami", "ubutale"),
    ("Wangu", "wmoses"),
    ("Bharath", "bbalasubramanian"),
    ("Kago", "ktshutlhedi"),
    ("Gao", "gmachobane"),
    ("Prathap", "pganesharajah"),
]
_HANDLE = {label: handle for label, handle in ESCALATION_TARGETS}
# CEO owns their own payroll-approval item; resolve the handle but keep "CEO"
# out of ESCALATION_TARGETS so no escalate pill is shown (nobody to escalate to).
_HANDLE["CEO"] = "aiyer"
ESCALATE_BASE = "https://omni.alphadirect.co.bw/api/ceo-monitor/matter"

# FIX 3 — recommendation playbook. Keyed by "at_stake"; owner must be a real
# escalation target. Text is a template; the deadline is appended when known.
# NOTE: owners are sensible defaults — CFO to confirm the standing assignments.
PLAYBOOK = {
    "regulatory": ("Prathap", "RSVP / respond within the statutory window, confirm the named delegate and compile the file."),
    "legal": ("Paul", "Acknowledge receipt without admitting liability; refer to legal to review the repudiation basis."),
    "reputational": ("Wangu", "Call the customer today before putting anything in writing; expedite the claim and log the resolution."),
    "money": ("Wangu", "Confirm the reserve and that reinsurance recovery is lodged; you review the figure before any payout."),
    "incident": ("Bharath", "Contain first, confirm the scope, then report back to the CEO the same day."),
    "approval": ("CEO", "CEO reviews & approves payroll sign-off."),
    "strategy": ("Prathap", ""),
    "other": ("Prathap", "Review and decide who owns this."),
}

# Scoring rubric (points). Bands: Critical >=70, High 40-69, Watch <40.
_REGULATOR_MARKERS = ("nbfira", "regulator", "burs", "esaamlg", "compliance office")
_LEGAL_MARKERS = ("letter of demand", "attorney", "litigation", "legal action",
                  "summons", "court order", "lawyer", "legal threat")
_WATCHDOG_MARKERS = ("ombudsman", "consumer watchdog", "media", "press", "journalist")
_INCIDENT_MARKERS = ("data breach", "breach", "incident", "phishing", "ranssomware",
                     "ransomware", "compromise", "exfiltration")
_CHASING_MARKERS = ("following up", "follow up", "chasing", "third time", "3rd time",
                    "still waiting", "awaiting", "no response", "yet to hear")

# CFO -> CEO payroll sign-off: an internal approval, NOT a customer complaint.
# from_addr is already lower-cased by the driver.
# EXACT addresses, never a substring (checklist L6, CFO 29-Jul-2026). The old
# `"pganesharajah" in from_addr` also matched pganesharajah@gmail.com and
# notpganesharajah@elsewhere.com, so an outsider could have mail routed as an
# internal CFO note. cfo@ and pganesharajah@ are the same person, two logins.
_CFO_ADDRESSES = ("pganesharajah@alphadirect.co.bw", "cfo@alphadirect.co.bw")


def is_cfo(from_addr):
    """True only for a positive match on a known CFO address."""
    addr = (from_addr or "").strip().lower()
    m = re.search(r"<([^>]+)>", addr)
    if m:
        addr = m.group(1).strip()
    return addr in _CFO_ADDRESSES
_PAYROLL_MARKERS = ("payroll", "pay run", "salary run", "salaries", "salary")

# CFO -> EXCO competitor / performance paper: an internal strategy note, NOT a
# customer complaint. Without this it scored as "other", the classifier labelled
# it a complaint, and recommend() handed it to the CLAIMS MANAGER with "call the
# customer today; expedite the claim" - advice she can do nothing with, on a
# competitor analysis (CFO, 16-Sep-2026).
_STRATEGY_MARKERS = ("competitor", "competitive", "market share", "market median",
                     "combined ratio", "loss ratio", "underwriting profit",
                     "benchmark", "peer group", "cession", "ceded")
_APPROVAL_MARKERS = ("sign-off", "sign off", "signoff", "approve", "approval",
                     "authorise", "authorize")

# THE REPLY CHAIN IS NOT NEW NEWS (CFO, 11-Sep-2026).
# Root cause of the "Thanks boss" fault: the gatherer scored subject + the WHOLE
# body, and a two-word reply carries the entire quoted original underneath it.
# So a courtesy thank-you was read as the payment request it was replying to —
# amounts, claim wording and all — and came out as a HIGH customer complaint
# recommending someone phone a customer. Only the words the sender actually
# typed today are news; the quote is history the CEO has already seen.
_QUOTE_START = re.compile(
    r"(?im)^\s*(?:-{2,}\s*original message\s*-{2,}"
    r"|_{5,}"
    r"|from:\s.{0,200}@"
    r"|on\s.{0,120}?wrote:\s*$"
    r"|>\s?\S)")


def new_text(body: str) -> str:
    """The part of an email the sender actually wrote, quote chain removed.

    Falls back to the whole body when stripping would leave nothing — a pure
    forward is all quote, and dropping it would blind the brief.
    """
    if not body:
        return ""
    m = _QUOTE_START.search(body)
    if not m:
        return body
    head = body[:m.start()].strip()
    return head if head else body


# COURTESY ACKNOWLEDGEMENT — never a matter (CFO, 11-Sep-2026).
# Belt and braces on top of the quote strip: even when the new text alone is
# scored, a bare "Thanks boss" must never be promoted by a stray keyword.
# Deliberately high precision — ANY sign of an ask cancels the gate.
_ACK_MARKERS = ("thanks", "thank you", "noted", "well received", "acknowledged",
                "much appreciated", "appreciated", "received with thanks")
_ACK_CANCEL = ("not happy", "dissatisf", "complaint", "escalat", "urgent",
               "overdue", "still waiting", "please", "kindly", "?")
_ACK_MAX_CHARS = 160


def is_courtesy_ack(item) -> bool:
    """True when what the sender actually wrote is nothing but a thank-you."""
    body = new_text((getattr(item, "summary", "") or "")).strip()
    if not body or len(body) > _ACK_MAX_CHARS:
        return False
    low = body.lower()
    if not any(m in low for m in _ACK_MARKERS):
        return False
    hard = (_REGULATOR_MARKERS + _LEGAL_MARKERS + _WATCHDOG_MARKERS
            + _INCIDENT_MARKERS)
    if any(m in low for m in hard):
        return False
    return not any(m in low for m in _ACK_CANCEL)


_AMOUNT_RE = re.compile(
    r"(?:BWP|Pula|P)\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(m|mn|million|k|thousand)?",
    re.IGNORECASE,
)


# ============================================================================
# DATA MODEL
# ============================================================================

@dataclass
class Item:
    id: int
    title: str
    from_addr: str
    category: str
    summary: str
    priority: str = ""
    confirmed: bool = False

    client: str = ""
    reference: str = ""
    deadline: str = ""
    action_needed: str = ""
    source_link: str = ""

    # v6 inputs
    days_open: int = 0
    last_reply_side: str = ""     # "us" | "them" | ""
    change_tag: str = ""          # NEW / DAY N — NO REPLY / MOVEMENT
    demand: str = ""              # what the sender is asking for, in their words

    # derived by v5 gates
    severity: str = ""
    party: str = ""
    polished_matter: str = ""
    polished_why: str = ""
    escalated_to: str = ""
    dropped_reason: str = ""

    # derived by v6
    amount_bwp: float = 0.0
    amount_display: str = ""
    at_stake: str = "other"
    score: int = 0
    rec_owner_label: str = ""
    rec_owner_handle: str = ""
    rec_text: str = ""
    is_one_thing: bool = False


@dataclass
class Digest:
    date: str
    raw_count: int
    distinct_count: int
    items: list = field(default_factory=list)
    overflow: int = 0
    filtered_noise: int = 0
    filtered_immaterial: int = 0




# --- prod interface + inlined v5 merge helpers (defined AFTER the class) ---
QueueItem = Item
SUBJECT_PREFIX = "CEO Omni Brief"

def digest_subject(d):
    return f"{SUBJECT_PREFIX} - {d.date}"

_SEVERITY_RANK = {"Critical": 0, "High": 1, "Watch": 2}

_ORG_MARKERS = ("pty", "ltd", "holdings", "(pty)", " co", "company", "attorneys",
                "& co", " inc", "transport", "group", "enterprises")
_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "prof"}
_ESCALATION_MARKERS = ("nbfira", "regulator", "ombudsman", "attorney",
                       "letter of demand", "legal", "litigation", "demand")


def _person_key(party: str):
    """A loose (surname, first-initial) key for a person; None for companies or
    generic parties so we never merge two unrelated things."""
    p = (party or "").lower()
    if not p or "unidentified" in p or any(m in p for m in _ORG_MARKERS):
        return None
    p = re.sub(r"\(.*?\)", " ", p)                 # drop "(Policy ...)"
    p = re.sub(r"\brep\.?\s+by\b.*$", " ", p)       # drop "rep. by ..."
    toks = [t for t in re.split(r"[\s.]+", re.sub(r"[^a-z\s.]", " ", p))
            if t and t not in _HONORIFICS]
    if len(toks) < 2:
        return None
    return (toks[-1], toks[0][0])                   # (surname, first initial)


def _is_escalation(item: QueueItem) -> bool:
    blob = (item.category + " " + item.title + " " + item.summary).lower()
    return any(m in blob for m in _ESCALATION_MARKERS)


def _escalated_to(item: QueueItem) -> str:
    blob = (item.category + " " + item.title + " " + item.summary).lower()
    if "nbfira" in blob:
        return "NBFIRA"
    if "ombudsman" in blob:
        return "the Ombudsman"
    if "attorney" in blob or "demand" in blob or "litigation" in blob or "legal" in blob:
        return "attorney (letter of demand)"
    if "regulator" in blob:
        return "the regulator"
    return "escalation"


def _combine_group(grp: list[QueueItem]) -> QueueItem:
    """Fold a same-customer group (with an escalation) into one card. No
    duplicated fields — references are unioned, one due, one action."""
    base = next((x for x in grp if not _is_escalation(x)), None)
    esc = next((x for x in grp if _is_escalation(x)), None) or grp[0]
    primary = base or esc

    primary.severity = min((x.severity for x in grp), key=lambda s: _SEVERITY_RANK[s])
    primary.escalated_to = _escalated_to(esc)

    # Party: clean personal name + a policy ref if any group member carries one.
    name = re.sub(r"\s*\(.*?\)\s*", "", (base or esc).party).strip()
    policy = ""
    for x in grp:
        m = re.search(r"\((Policy[^)]*)\)", x.party)
        if m:
            policy = m.group(1)
            break
    primary.party = f"{name} ({policy})" if policy else name

    # Matter = the base complaint's own account (the story); route under the
    # escalation category so it lands in the right, higher-authority section.
    primary.summary = (base or esc).summary
    primary.polished_matter = ""
    primary.category = esc.category

    refs = []
    for x in grp:
        if x.reference and x.reference not in refs:
            refs.append(x.reference)
    primary.reference = " · ".join(refs)
    primary.deadline = esc.deadline or (base.deadline if base else "")
    primary.action_needed = esc.action_needed or (base.action_needed if base else "")
    primary.source_link = esc.source_link or (base.source_link if base else "")
    return primary

def merge_related(items: list[QueueItem]) -> list[QueueItem]:
    """Merge same-customer items into one card, but ONLY when at least one is an
    escalation (a complaint that went to a regulator/lawyer). Two unrelated
    matters for the same name, or two plain complaints, stay separate."""
    groups: dict = {}
    order: list = []
    for it in items:
        k = _person_key(it.party)
        if k is None:
            order.append(("solo", it))
            continue
        if k not in groups:
            groups[k] = []
            order.append(("group", k))
        groups[k].append(it)

    out: list[QueueItem] = []
    for kind, ref in order:
        if kind == "solo":
            out.append(ref)
            continue
        grp = groups[ref]
        if len(grp) > 1 and any(_is_escalation(x) for x in grp):
            out.append(_combine_group(grp))
        else:
            out.extend(grp)
    return out


# ============================================================================
# FIX 4a — DETERMINISTIC FACT EXTRACTION
# ============================================================================

def parse_amount(text: str) -> float:
    """Largest BWP amount in the text, as a float. 0.0 if none. Handles
    '2.3M', '500K', 'BWP 2,300,000', 'P2.5m'."""
    best = 0.0
    for num, unit in _AMOUNT_RE.findall(text or ""):
        try:
            val = float(num.replace(",", ""))
        except ValueError:
            continue
        u = (unit or "").lower()
        if u in ("m", "mn", "million"):
            val *= 1_000_000
        elif u in ("k", "thousand"):
            val *= 1_000
        best = max(best, val)
    return best


def _fmt_amount(v: float) -> str:
    if v <= 0:
        return ""
    if v >= 1_000_000:
        return f"P{v/1_000_000:.2f}".rstrip("0").rstrip(".") + "m"
    if v >= 1_000:
        return f"P{v/1_000:.0f}k"
    return f"P{v:,.0f}"


def extract_facts(item: Item) -> None:
    # Quoted history included, an amount from the email being REPLIED TO was
    # read as a new amount and pushed a courtesy note up the score.
    blob = " ".join([item.title, new_text(item.summary), item.demand,
                     item.reference])
    item.amount_bwp = parse_amount(blob)
    item.amount_display = _fmt_amount(item.amount_bwp)


# ============================================================================
# FIX 4b — POINTS-BASED SEVERITY (consistent run-to-run)
# ============================================================================

def score_item(item: Item) -> None:
    # Score from the ACTUAL email text (title/body/demand) — NOT the AI's own
    # category label. (DeepSeek labelling an item "regulatory" is a guess, not
    # evidence it came from a regulator; the word "regulatory" also contains
    # "regulator", which used to leak a false +40 and over-flag matters.)
    blob = " ".join([item.title, new_text(item.summary), item.demand]).lower()

    # A CFO -> CEO payroll sign-off is an internal approval, never a customer
    # matter. Sender = CFO (from_addr); recipient = CEO is implicit (the brief
    # reads only the CEO inbox). This runs FIRST so it beats the watchdog
    # false-positive ("media" inside "immediate", "press" inside "pressure")
    # and the amount->money path. Shows as a Payroll/Approvals card owned by CEO.
    if (is_cfo(getattr(item, "from_addr", ""))
            and any(p in blob for p in _PAYROLL_MARKERS)
            and any(s in blob for s in _APPROVAL_MARKERS)):
        item.score = 40
        item.at_stake = "approval"
        item.severity = "High"
        return

    # ...but never at the cost of a real regulator or legal matter he FORWARDS.
    # NBFIRA prudential correspondence routinely says "loss ratio" and "cession",
    # which would otherwise file a P3M regulator letter under Business
    # Performance and cap it at High (Fable, 16-Sep-2026). Watchdog markers are
    # deliberately NOT in this guard - "media"/"press" false-positive too often.
    if (is_cfo(getattr(item, "from_addr", ""))
            and any(s in blob for s in _STRATEGY_MARKERS)
            and not any(m in blob for m in _REGULATOR_MARKERS + _LEGAL_MARKERS)):
        item.score = max(item.score, 40)
        item.at_stake = "strategy"
        item.severity = "High"
        return

    if is_courtesy_ack(item):
        item.score = 0
        item.at_stake = "other"
        item.severity = "Watch"
        item.dropped_reason = "courtesy acknowledgement - no ask"
        return

    score = 0
    at_stake = "other"

    if any(m in blob for m in _REGULATOR_MARKERS):
        score += 40
        at_stake = "regulatory"
    if any(m in blob for m in _LEGAL_MARKERS):
        score += 40
        at_stake = "legal"
    if any(m in blob for m in _WATCHDOG_MARKERS):
        score += 40
        if at_stake == "other":
            at_stake = "reputational"
    if any(m in blob for m in _INCIDENT_MARKERS):
        score += 30
        if at_stake == "other":
            at_stake = "incident"

    amt = item.amount_bwp
    if amt >= 2_000_000:
        score += 50
    elif amt >= 500_000:
        score += 30
    elif amt >= 100_000:
        score += 15
    if amt >= 500_000 and at_stake == "other":
        at_stake = "money"

    # In our court and gone quiet — a real, chase-able fact.
    if item.days_open > 5 and item.last_reply_side == "us":
        score += 15
    # Counterparty is actively pushing.
    if any(m in blob for m in _CHASING_MARKERS):
        score += 10

    # Section labelling only (does NOT affect the score above): when no text
    # marker fired, fall back to the classifier's category so a plain complaint
    # lands under "Customer Complaints", a legal item under "Legal", etc.
    if at_stake == "other":
        cat = (item.category or "").lower()
        if any(w in cat for w in ("complaint", "grievance", "dissatisf")):
            at_stake = "reputational"
        elif any(w in cat for w in ("legal", "demand", "litigation")):
            at_stake = "legal"
        elif any(w in cat for w in ("regulat", "nbfira", "ombudsman", "burs")):
            at_stake = "regulatory"
        elif any(w in cat for w in ("incident", "breach", "fraud")):
            at_stake = "incident"

    item.score = score
    item.at_stake = at_stake

    if score >= 70:
        item.severity = "Critical"
    elif score >= 40:
        item.severity = "High"
    else:
        item.severity = "Watch"

    # Material floor: a genuine regulator/legal/complaint/incident matter is never
    # shown as "Watch". The evidence must be in the EMAIL TEXT (blob), not the AI's
    # category guess — otherwise a mislabelled item gets falsely promoted.
    _floor_signals = ("nbfira", "ombudsman", "consumer watchdog", "regulator",
                      "letter of demand", "attorney", "litigation", "summons",
                      "court order", "complaint", "not happy", "dissatisf",
                      "data breach", "breach of")
    if item.severity == "Watch" and any(m in blob for m in _floor_signals):
        item.severity = "High"
        item.score = max(item.score, 40)


# ============================================================================
# FIX 3 — RECOMMENDED ACTION
# ============================================================================

def recommend(item: Item) -> None:
    owner, text = PLAYBOOK.get(item.at_stake, PLAYBOOK["other"])
    # Complaints without a money/legal angle are reputational -> claims owner.
    if item.at_stake == "other" and ("complaint" in item.category.lower()
                                     or "grievance" in item.category.lower()):
        owner, text = PLAYBOOK["reputational"]
    if item.deadline:
        text = f"{text} (by {item.deadline})"
    item.rec_owner_label = owner
    item.rec_owner_handle = _HANDLE.get(owner, "pganesharajah")
    item.rec_text = text


# ============================================================================
# FIX 1 — FACT-BASED "WHY IT MATTERS" (must carry a number AND a name)
# ============================================================================

def _has_number(s: str) -> bool:
    return any(c.isdigit() for c in s)


def _has_proper_noun(s: str) -> bool:
    return bool(re.search(r"\b[A-Z][a-zA-Z]{2,}", s))


_VAGUE_WHY = ("may be significant", "requires attention", "claim handling",
              "regulator communication", "regulator correspondence", "claim-related",
              "may require attention", "for review", "flagged for review",
              "attention this week", "claim requires attention", "regulator engaging",
              "claim discussion", "discussion in progress", "claim mentioned",
              "high-value claim mentioned", "needs attention", "communication received",
              "matter for review")


def _is_vague(s: str) -> bool:
    """Block only the genuinely empty/boilerplate 'why' lines. Concrete qualitative
    lines (with a party, an ask, or a consequence) are kept even without a number —
    the classify prompt now demands that detail. Over-strict rejection (requiring a
    digit) wrongly binned good lines, so we rely on the boilerplate list + length."""
    t = (s or "").strip().lower()
    if len(t) < 22:
        return True
    return any(p in t for p in _VAGUE_WHY)


def why_line(item: Item) -> str:
    """Prefer the LLM's sentence ONLY when it is concrete; otherwise rebuild a
    fact-anchored line from what we actually know (party, amount, ref, deadline).
    Never ship a vague 'may be significant'."""
    if item.polished_why and not _is_vague(item.polished_why):
        return item.polished_why

    # Conservative, FACT-ONLY fallback. We never assert an unverified narrative
    # (e.g. "a letter of demand") off a single keyword, and we never just echo the
    # headline. Show the hard facts we actually parsed; otherwise point to the
    # full story. (Richer "why" needs body-fact extraction — the next enhancement.)
    bits = []
    if item.amount_display:
        bits.append(item.amount_display)
    if item.days_open and item.last_reply_side == "us":
        bits.append(f"{item.days_open} days, no reply from us")
    elif item.days_open:
        bits.append(f"open {item.days_open} days")
    if item.demand:
        bits.append(f"they want {item.demand}")
    ref = item.reference or ""
    if ref and len(ref) <= 24 and any(c.isdigit() for c in ref):
        bits.append(f"ref {ref}")
    if bits:
        s = " · ".join(bits)
        return s[0].upper() + s[1:]
    return "Open the full story for the detail."


# ============================================================================
# RENDER — matches the CFO's Claude Design (600px table-based email)
# ============================================================================

NAVY = "#0D1B2A"
ORANGE = "#F4A623"
GOLD = "#B8862E"
BODY = "#3D4852"
MUTE = "#6C757D"
FAINT = "#9AA5B1"
BORDER = "#E3E7EC"
ROWLINE = "#EEF1F4"
PANEL = "#F7F8FA"
SERIF = "Georgia, 'Times New Roman', serif"
SANS = "Arial, Helvetica, sans-serif"

_BADGE = {"Critical": (ORANGE, NAVY), "High": (NAVY, ORANGE), "Watch": (BORDER, MUTE)}
_ACCENT = {"Critical": ORANGE, "High": NAVY, "Watch": "#C9D2DB"}
_STAKE_SECTION = {
    "regulatory": "Regulatory Issues",
    "legal": "Legal",
    "reputational": "Customer Complaints",
    "money": "Critical & Large Claims",
    "incident": "Security Incidents",
    "approval": "Payroll / Approvals",
    "strategy": "Business Performance",
    "other": "Watching",
}


def _esc(s):
    return html.escape(s or "")


def _section_label(title):
    return (
        '<tr><td style="background-color:#FFFFFF; padding:0 36px 10px 36px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
        f'<tr><td style="border-top:1px solid {BORDER}; font-size:1px; line-height:1px;">&nbsp;</td></tr>'
        f'<tr><td style="font-family:{SERIF}; color:{NAVY}; font-size:13px; letter-spacing:4px; '
        f'text-transform:uppercase; padding-top:16px; line-height:18px;">{title}</td></tr>'
        '</table></td></tr>')


def _escalate_row(item):
    # Default placeholder escalate footer; the driver overrides this with real
    # signed-token buttons in the same style.
    pills = ""
    for label, handle in ESCALATION_TARGETS:
        if label == item.rec_owner_label:
            pills += (f'<a href="{ESCALATE_BASE}/{item.id}?assign={handle}" style="display:inline-block; '
                      f'color:{ORANGE}; text-decoration:none; border:1px solid {NAVY}; border-radius:14px; '
                      f'padding:0 14px; margin:0 6px 6px 0; background-color:{NAVY}; font-weight:bold; '
                      f'line-height:26px;">{_esc(label)}&nbsp;&middot;&nbsp;rec</a>')
        else:
            pills += (f'<a href="{ESCALATE_BASE}/{item.id}?assign={handle}" style="display:inline-block; '
                      f'color:{NAVY}; text-decoration:none; border:1px solid #C9D2DB; border-radius:14px; '
                      f'padding:0 14px; margin:0 6px 6px 0; background-color:#FFFFFF; line-height:26px;">{_esc(label)}</a>')
    return (
        f'<tr><td style="background-color:{PANEL}; border-top:1px solid {BORDER}; padding:14px 24px 16px 24px;">'
        f'<div style="font-family:{SANS}; color:{MUTE}; font-size:10px; letter-spacing:2px; '
        'text-transform:uppercase; line-height:14px; padding-bottom:10px;">Assign &amp; escalate to</div>'
        f'<div style="font-family:{SANS}; font-size:12px; line-height:30px;">{pills}</div></td></tr>')


def render_card(item, is_one_thing=False):
    bg, fg = _BADGE.get(item.severity, _BADGE["Watch"])
    accent = _ACCENT.get(item.severity, "#C9D2DB")
    headline = _esc(item.polished_matter or item.title)
    why = _esc(why_line(item))
    story = f"{ESCALATE_BASE}/{item.id}"
    ref = (f'<tr><td style="padding:8px 24px 0 24px; font-family:{SANS}; color:{FAINT}; font-size:12px; '
           f'font-style:italic; line-height:18px;">Ref: {_esc(item.reference)}</td></tr>') if item.reference else ""
    rec = (f'<tr><td style="padding:8px 24px 0 24px; font-family:{SANS}; color:{BODY}; font-size:13px; '
           f'line-height:20px;"><strong style="color:{GOLD};">Recommended:</strong> {_esc(item.rec_owner_label)} '
           f'&mdash; {_esc(item.rec_text)}</td></tr>') if item.rec_text else ""
    onething = (f'<tr><td style="padding:16px 24px 0 24px; font-family:{SERIF}; font-style:italic; '
                f'color:{GOLD}; font-size:11px; letter-spacing:4px; text-transform:uppercase; '
                'line-height:16px;">The One Thing Today</td></tr>') if is_one_thing else ""
    return (
        '<tr><td style="background-color:#FFFFFF; padding:8px 36px 24px 36px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="border:1px solid {BORDER}; border-top:3px solid {accent};">'
        f'{onething}'
        '<tr><td style="padding:20px 24px 0 24px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>'
        f'<td style="font-family:{SANS}; color:{MUTE}; font-size:12px; letter-spacing:1px; '
        f'text-transform:uppercase; line-height:16px;">{_esc(item.party)}</td>'
        f'<td align="right"><span style="display:inline-block; background-color:{bg}; color:{fg}; '
        f'font-family:{SANS}; font-size:10px; font-weight:bold; letter-spacing:2px; padding:4px 10px; '
        f'border-radius:3px;">{_esc(item.severity.upper())}</span></td></tr></table></td></tr>'
        f'<tr><td style="padding:10px 24px 0 24px; font-family:{SERIF}; color:{NAVY}; font-size:17px; '
        f'font-weight:bold; line-height:24px;">{headline}</td></tr>'
        f'<tr><td style="padding:8px 24px 0 24px; font-family:{SANS}; color:{BODY}; font-size:13px; '
        f'line-height:20px;"><strong style="color:{NAVY};">Why it matters:</strong> {why}</td></tr>'
        f'{rec}{ref}'
        f'<tr><td style="padding:12px 24px 16px 24px; font-family:{SANS}; font-size:12px; line-height:18px;">'
        f'<a href="{story}" style="color:{GOLD}; font-weight:bold; text-decoration:none;">See the full story &rarr;</a>'
        f'&nbsp;&nbsp;&nbsp;<a href="{story}?action=nudge" style="color:{MUTE}; text-decoration:underline;">Ask handler for an update</a></td></tr>'
        f'{_escalate_row(item)}'
        '</table></td></tr>')



def render_header(digest, band="CEO Monitor"):
    return (
        f'<tr><td style="background-color:{NAVY}; padding:28px 36px 24px 36px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>'
        '<td width="200" valign="middle">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        '<td style="background-color:#FFFFFF; border-radius:6px; padding:8px 14px; '
        f'font-family:{SERIF}; font-weight:bold; font-size:18px; color:#1D3270;">Alpha<span style="color:{ORANGE};">Direct</span></td>'
        '</tr></table></td>'
        f'<td valign="middle" align="right" style="font-family:{SERIF}; color:#8FA0B3; font-size:11px; '
        'letter-spacing:2.5px; text-transform:uppercase; line-height:16px;">'+_esc(band)+'<br>'
        f'<span style="color:{ORANGE}; letter-spacing:2px;">Confidential</span></td>'
        '</tr></table></td></tr>'
        f'<tr><td style="background-color:{ORANGE}; height:4px; font-size:1px; line-height:4px;">&nbsp;</td></tr>')


def render_title_block(digest):
    n = digest.distinct_count
    headline = "No matters need your attention" if n == 0 else \
        f"{n} matter{' needs' if n == 1 else 's need'} your attention"
    crit = sum(1 for i in digest.items if i.severity == "Critical")
    high = sum(1 for i in digest.items if i.severity == "High")
    watch = sum(1 for i in digest.items if i.severity == "Watch")
    chips = []
    if crit:
        chips.append(f'<td style="background-color:{ORANGE}; border-radius:14px; padding:6px 16px; font-family:{SANS}; color:{NAVY}; font-size:12px; font-weight:bold; line-height:16px;">{crit}&nbsp;Critical</td>')
    if high:
        chips.append(f'<td style="background-color:{NAVY}; border-radius:14px; padding:6px 16px; font-family:{SANS}; color:{ORANGE}; font-size:12px; font-weight:bold; line-height:16px;">{high}&nbsp;High</td>')
    if watch:
        chips.append(f'<td style="background-color:{BORDER}; border-radius:14px; padding:6px 16px; font-family:{SANS}; color:{MUTE}; font-size:12px; font-weight:bold; line-height:16px;">{watch}&nbsp;Watch</td>')
    chip_html = '<td style="width:8px; font-size:1px;">&nbsp;</td>'.join(chips)
    return (
        '<tr><td style="background-color:#FFFFFF; padding:36px 36px 8px 36px;" align="center">'
        f'<div style="font-family:{SERIF}; font-style:italic; color:{MUTE}; font-size:12px; letter-spacing:5px; '
        'text-transform:uppercase; line-height:18px;">Daily Exception Digest</div>'
        f'<div style="font-family:{SERIF}; color:{NAVY}; font-size:28px; font-weight:bold; letter-spacing:0.5px; '
        f'line-height:36px; padding-top:8px;">{_esc(headline)}</div>'
        f'<div style="font-family:{SANS}; color:{MUTE}; font-size:13px; line-height:20px; padding-top:6px;">{_esc(digest.date)}</div>'
        '</td></tr>'
        '<tr><td style="background-color:#FFFFFF; padding:18px 36px 28px 36px;" align="center">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center"><tr>'
        f'{chip_html}</tr></table>'
        f'<div style="font-family:{SANS}; color:{FAINT}; font-size:12px; line-height:18px; padding-top:14px;">'
        f'{digest.filtered_noise} junk and {digest.filtered_immaterial} routine item'
        f'{"" if digest.filtered_immaterial == 1 else "s"} filtered out of {digest.raw_count} scanned</div></td></tr>')


def render_sections(digest):
    if not digest.items:
        return ('<tr><td style="background-color:#FFFFFF; padding:24px 36px 44px 36px;" align="center">'
                f'<div style="font-family:{SERIF}; color:{NAVY}; font-size:18px; font-weight:bold;">Nothing needs you today.</div>'
                f'<div style="font-family:{SANS}; color:{MUTE}; font-size:12px; padding-top:8px; line-height:18px;">'
                'No complaint, regulator, legal, incident or high-value claim came through. Routine and junk mail was held back.</div></td></tr>')
    out = ""
    last = None
    for i, it in enumerate(digest.items):
        section = _STAKE_SECTION.get(it.at_stake, "Watching")
        if section != last:
            out += _section_label(section)
            last = section
        out += render_card(it, is_one_thing=(i == 0))
    return out


def render_waiting(waiting):
    if not waiting:
        return ""
    rows = ""
    for w in waiting:
        age = int(w.get("age_days", 0))
        overdue = w.get("overdue")
        cbg, cfg = ("#FBEAEA", "#A6231F") if overdue else (BORDER, MUTE)
        agetxt = f'{age} day{"" if age == 1 else "s"}' + ("&nbsp;&middot;&nbsp;overdue" if overdue else "")
        amt = (f'<span style="display:inline-block; background-color:{BORDER}; color:{BODY}; font-size:11px; '
               f'padding:2px 8px; border-radius:3px; margin-right:6px;">{_esc(w["amount"])}</span>') if w.get("amount") else ""
        rows += (f'<tr><td style="padding:10px 0; border-bottom:1px solid {ROWLINE}; font-family:{SANS}; '
                 f'color:{BODY}; font-size:13px; line-height:18px;">{_esc(w.get("title",""))}</td>'
                 f'<td align="right" style="padding:10px 0; border-bottom:1px solid {ROWLINE}; white-space:nowrap;">'
                 f'{amt}<span style="display:inline-block; background-color:{cbg}; color:{cfg}; font-family:{SANS}; '
                 f'font-size:11px; font-weight:bold; padding:3px 9px; border-radius:3px;">{agetxt}</span></td></tr>')
        rows += _decide_row(w)
    return (
        '<tr><td style="background-color:#FFFFFF; padding:0 36px 24px 36px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        'style="background-color:#FFF9EE; border:1px solid #F0DFC0; border-left:4px solid #F4A623;">'
        f'<tr><td colspan="2" style="padding:16px 22px 2px 22px; font-family:{SERIF}; font-style:italic; color:{GOLD}; '
        'font-size:11px; letter-spacing:4px; text-transform:uppercase; line-height:16px;">Waiting on You</td></tr>'
        f'<tr><td colspan="2" style="padding:2px 22px 10px 22px; font-family:{SANS}; color:{GOLD}; font-size:12px; '
        f'line-height:17px;">Only you can clear these &mdash; {len(waiting)} item{"" if len(waiting)==1 else "s"}.</td></tr>'
        '<tr><td colspan="2" style="padding:0 22px 14px 22px;"><table role="presentation" cellpadding="0" '
        f'cellspacing="0" border="0" width="100%">{rows}</table></td></tr>'
        '</table></td></tr>')


def render_money_lens(lens):
    """The four business numbers, directly under the header.

    A tile with no data reads "no fresh data" in grey - never "P0". A silent
    zero on the premium tile would read as a collections collapse when in fact
    the query simply failed (CFO rule: never quote an Omni figure as fact).
    """
    if not lens or not lens.get("tiles"):
        return ""
    cells = ""
    for t in lens["tiles"]:
        ok = t.get("ok")
        val = t.get("value") if ok else "&mdash;"
        vcol = NAVY if ok else "#B6BFCA"
        d = t.get("delta")
        if d is None:
            move = ('<span style="color:#B6BFCA;">&nbsp;</span>' if ok else
                    f'<span style="color:#B6BFCA;">no fresh data</span>')
        else:
            up = d >= 0
            move = (f'<span style="color:{"#1B7A3B" if up else "#A6231F"};">'
                    f'{"&#9650;" if up else "&#9660;"} {abs(d):.0f}%</span>')
        note = (f'<div style="font-family:{SANS}; font-size:10px; color:#B6BFCA; '
                f'padding-top:2px;">{_esc(t["note"])}</div>') if t.get("note") else ""
        cells += (
            f'<td width="25%" valign="top" style="padding:0 6px;">'
            f'<div style="font-family:{SANS}; font-size:10px; color:{MUTE}; '
            f'letter-spacing:1px; text-transform:uppercase; line-height:14px;">'
            f'{_esc(t["label"])}</div>'
            f'<div style="font-family:{SERIF}; font-size:20px; font-weight:bold; '
            f'color:{vcol}; line-height:26px; padding-top:4px;">{val}</div>'
            f'<div style="font-family:{SANS}; font-size:11px; color:{MUTE}; '
            f'line-height:15px;">{_esc(t.get("sub") or "")}</div>'
            f'<div style="font-family:{SANS}; font-size:11px; font-weight:bold; '
            f'line-height:15px;">{move}</div>{note}</td>')
    # Large claims section (top 3 by reserve in the last 10 days)
    claims_html = ""
    # One source of truth for the floor: core.ceo_money_lens owns the number and
    # ships it in the lens, so the wording can never drift from what the query
    # actually filters on.
    floor = lens.get("claims_floor")
    large = lens.get("large_claims")
    if large is None or floor is None:
        # The query failed, or an older backend sent no floor. Say nothing is
        # known - NEVER "no claims above X", which states a fact nothing proved.
        claims_html = (
            f'<div style="font-family:{SANS}; font-size:11px; color:#B6BFCA; '
            'padding:14px 0 4px 0; line-height:17px;">'
            'Large claims: no fresh data</div>')
        large = []
    else:
        floor_label = "P%s" % format(int(floor), ",d")
        if not large:
            claims_html = (
                f'<div style="font-family:{SANS}; font-size:11px; color:{MUTE}; '
                'padding:14px 0 4px 0; line-height:17px;">'
                f'No claims above {floor_label} registered in the last 10 days.</div>')
    if large:
        claim_rows = ""
        for cl in large:
            claim_rows += (
                f'<tr><td style="padding:6px 8px; font-family:{SANS}; font-size:12px; '
                f'color:{NAVY}; font-weight:bold; border-bottom:1px solid #E6EBF1;">'
                f'{_esc(cl["claim"])}</td>'
                f'<td style="padding:6px 8px; font-family:{SANS}; font-size:11px; '
                f'color:{MUTE}; border-bottom:1px solid #E6EBF1;">'
                f'{_esc(cl["type"])} &middot; {_esc(cl["status"])}</td>'
                f'<td style="padding:6px 8px; font-family:{SERIF}; font-size:13px; '
                f'font-weight:bold; color:{NAVY}; text-align:right; border-bottom:1px solid #E6EBF1;">'
                f'{_esc(cl["reserve"])}</td></tr>'
                f'<tr><td colspan="3" style="padding:2px 8px 8px 8px; font-family:{SANS}; '
                f'font-size:10px; color:{MUTE}; border-bottom:1px solid #E6EBF1;">'
                f'{_esc(cl["description"])} &middot; Registered {_esc(cl["registered"])} '
                f'&middot; Paid {_esc(cl["paid"])}</td></tr>')
        claims_html = (
            f'<div style="font-family:{SANS}; font-size:10px; color:{MUTE}; letter-spacing:3px; '
            f'text-transform:uppercase; padding:14px 0 8px 0;">CLAIMS ABOVE {floor_label} (last 10 days)</div>'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
            f'{claim_rows}</table>')

    period_label = _esc(lens.get("period", lens.get("day", "")))
    basis_label = _esc(lens.get("basis", ""))
    return (
        '<tr><td style="background-color:#F7F9FB; border-bottom:1px solid #E6EBF1; '
        'padding:18px 30px 16px 30px;">'
        f'<div style="font-family:{SANS}; font-size:10px; color:{MUTE}; letter-spacing:3px; '
        'text-transform:uppercase; padding-bottom:12px;">'
        f'{period_label} &nbsp;&middot;&nbsp; {basis_label}</div>'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="table-layout:fixed;"><tr>{cells}</tr></table>'
        f'{claims_html}'
        '</td></tr>')


def _decide_row(w):
    """Approve / Decline / Need detail for one waiting item.

    Financial authorisations get a plain link into Omni instead - completing a
    payment task IS the payment, and that keeps its own duplicate checks and
    typed completion note.
    """
    if not w.get("decidable"):
        return ('<tr><td colspan="2" style="padding:0 0 12px 0;">'
                f'<span style="font-family:{SANS}; font-size:11px; color:{MUTE};">'
                'Financial authorisation &mdash; decide this one in Omni.</span></td></tr>')
    btns = ""
    for label, href in w.get("actions", []):
        btns += (f'<a href="{href}" style="display:inline-block; text-decoration:none; '
                 f'background-color:#F1F4F8; color:{NAVY}; font-family:{SANS}; font-size:11px; '
                 'font-weight:bold; padding:6px 14px; border:1px solid #D4DBE4; '
                 'border-radius:14px; margin:0 6px 0 0;">' + _esc(label) + '</a>')
    return ('<tr><td colspan="2" style="padding:0 0 12px 0;">' + btns + '</td></tr>')


def render_notes(notes, *, heading="From your team", blurb=None):
    """The morning note space (CFO 2026-09-10).

    Each note is one person, 25 words, once per morning. Rendered as a quiet
    block rather than a card list: it is meant to be read in five seconds and
    is deliberately NOT a place for a conversation.

    `notes` is a list of {"author", "body", "role"} dicts, already capped and
    ordered by the driver. An empty list renders nothing at all — a standing
    empty "no messages" panel trains the reader to skip the region.
    """
    if not notes:
        return ""
    rows = ""
    for n in notes:
        who = _esc(n.get("author") or "")
        role = _esc(n.get("role") or "")
        meta = who + (f' &middot; {role}' if role else "")
        rows += (
            f'<tr><td style="padding:11px 0 0 0;">'
            f'<div style="font-family:{SANS}; font-size:11px; color:{MUTE}; '
            f'letter-spacing:0.4px; text-transform:uppercase; line-height:15px;">{meta}</div>'
            f'<div style="font-family:{SERIF}; font-size:14px; color:{BODY}; '
            f'line-height:21px; padding-top:3px;">&ldquo;{_esc(n.get("body") or "")}&rdquo;</div>'
            f'</td></tr>')
    tail = (f'<div style="font-family:{SANS}; font-size:11px; color:{MUTE}; '
            f'line-height:16px; padding-top:14px;">{_esc(blurb)}</div>') if blurb else ""
    return (
        '<tr><td style="background-color:#FFFFFF; padding:0 36px 24px 36px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        'style="background-color:#F7F9FB; border:1px solid #E6EBF1; border-left:4px solid '
        f'{NAVY};"><tr><td style="padding:16px 22px 4px 22px;">'
        f'<div style="font-family:{SERIF}; font-style:italic; color:{NAVY}; font-size:11px; '
        f'letter-spacing:4px; text-transform:uppercase; line-height:16px;">{_esc(heading)}</div>'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
        f'{rows}</table>{tail}'
        '</td></tr></table></td></tr>')


#: Printed instead of a diary when Microsoft refused the mailbox (403).
UNAVAILABLE_PENDING = "Calendar access still enabling"
#: Printed instead of a diary when this brief never asked for that calendar.
#: "No meetings" would be a claim about someone's day that nobody checked.
UNAVAILABLE_NOT_READ = "Calendar not shared"
#: Printed when the read was attempted and Microsoft did not answer properly.
#: Deliberately NOT the "still enabling" wording — that sends the reader to IT
#: over a network blip IT has already dealt with.
UNAVAILABLE_ERROR = "Calendar could not be read"


def unavailable_reason(err):
    """Why a diary could not be read, or "" when the read came back clean.

    The drivers used to branch on `err == 403` alone, so ANY other failure —
    a timeout, a 5xx, Microsoft throttling — fell through to an empty event
    list and the column printed "No meetings". A blip is not a free day.
    """
    if not err:
        return ""
    return UNAVAILABLE_PENDING if err == 403 else UNAVAILABLE_ERROR


def _mtg_col(name, role, count, evs, unavailable, accent):
    """`unavailable` is the sentence to print INSTEAD of a diary, or "".

    It used to be a bare true/false meaning "still enabling". A column can be
    blank for three different reasons and they must never read the same: the
    calendar is being switched on, the read failed, or it was never asked for.
    """
    header_extra = (f'<br><span style="color:{ORANGE}; letter-spacing:1px; font-weight:normal;">{count} today</span>') if (count is not None and not unavailable) else ""
    head = (f'<tr><td style="background-color:{NAVY}; padding:8px 12px; font-family:{SANS}; color:#FFFFFF; '
            'font-size:10px; font-weight:bold; letter-spacing:1.5px; text-transform:uppercase; line-height:14px;">'
            f'{name} <span style="color:#8FA0B3; font-weight:normal;">({role})</span>{header_extra}</td></tr>')
    if unavailable:
        body = (f'<tr><td style="padding:10px 12px; font-family:{SANS}; color:{FAINT}; font-size:11px; '
                f'font-style:italic; line-height:16px;">{_esc(unavailable)}</td></tr>')
    else:
        body = ""
        if not evs:
            body = (f'<tr><td style="padding:10px 12px; font-family:{SANS}; color:{FAINT}; font-size:11px; '
                    'font-style:italic; line-height:16px;">No meetings</td></tr>')
        for idx, m in enumerate(evs):
            border = "" if idx == len(evs) - 1 else f" border-bottom:1px solid {ROWLINE};"
            ext = (f'&nbsp;<span style="display:inline-block; background-color:#FFF4DE; color:{GOLD}; font-size:8px; '
                   'font-weight:bold; letter-spacing:1.5px; padding:1px 5px; border-radius:3px;">EXT</span>') if m.get("ext") else ""
            body += (f'<tr><td style="padding:8px 12px;{border}">'
                     f'<div style="font-family:{SANS}; color:{NAVY}; font-size:11px; font-weight:bold; line-height:15px;">{_esc(m["time"])}{ext}</div>'
                     f'<div style="font-family:{SANS}; color:{BODY}; font-size:11px; line-height:15px; padding-top:2px;">{_esc(m["title"])}</div></td></tr>')
    return (f'<td width="170" valign="top"><table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'width="170" style="width:170px; border:1px solid {BORDER}; border-top:3px solid {accent};">{head}{body}</table></td>')


#: (data key, role label) per meetings column, in display order. The first
#: column is always the reader, so it is labelled "You" and takes the accent.
#: The CFO brief passes its own list - without this the columns silently read
#: "You / Arun" and looked for a "Prathap" key the CFO driver never produces.
MEETING_COLUMNS = [("You", "Arun"), ("Arjun", "COO"), ("Prathap", "CFO")]


def render_meetings(meetings, columns=None):
    if not meetings:
        return ""
    columns = columns or MEETING_COLUMNS
    tday = meetings.get("today", {})
    pending = meetings.get("pending", {})
    # Today's count comes from the driver UNCAPPED. `tday` holds only the first
    # six rows a column can show, so counting it printed "6 today" on a
    # nine-meeting day — the cap, read as a fact about that person's diary.
    # A half-synced host can still serve an older driver with no such key (see
    # test_an_older_driver_without_the_count_still_renders_its_rows); counting
    # the rows is then exactly what this did before, so nothing gets worse.
    tcount_today = meetings.get("today_counts", {})
    cols = []
    for i, (key, role) in enumerate(columns[:3]):
        label = "You" if i == 0 else key
        # A key the driver never produced is NOT an empty day. The pending test
        # used to be pinned to column 1, so a refusal in any other column drew
        # "still enabling" next to a real count of zero.
        reason = pending.get(key)
        if reason:
            # A driver may hand back the sentence itself; an older one hands
            # back a bare True, which has only ever meant "still enabling".
            unavailable = reason if isinstance(reason, str) else UNAVAILABLE_PENDING
        elif key not in tday:
            unavailable = UNAVAILABLE_NOT_READ
        else:
            unavailable = ""
        count = None if unavailable else tcount_today.get(key, len(tday.get(key, [])))
        cols.append(_mtg_col(label, role, count, tday.get(key, []),
                             unavailable, ORANGE if i == 0 else NAVY))
    while len(cols) < 3:
        cols.append(_mtg_col("", "", None, [], "", NAVY))
    you, arjun, prathap = cols[0], cols[1], cols[2]
    tc = meetings.get("tomorrow_counts", {})

    def numcell(key, label, first=False):
        v = tc.get(key)
        disp = "&mdash;" if v is None else str(v)
        col = "#C9D2DB" if v is None else NAVY
        bl = "" if first else f" border-left:1px solid {BORDER};"
        return (f'<td width="176" valign="middle" align="center" style="padding:14px 0;{bl}">'
                f'<div style="font-family:{SERIF}; color:{col}; font-size:22px; font-weight:bold; line-height:24px;">{disp}</div>'
                f'<div style="font-family:{SANS}; color:{MUTE}; font-size:9px; letter-spacing:2px; text-transform:uppercase; line-height:13px; padding-top:2px;">{label}</div></td>')
    headline = meetings.get("tomorrow_headline", "")
    hl = (f'<tr><td colspan="3" style="border-top:1px solid {BORDER}; padding:9px 16px; font-family:{SANS}; color:{MUTE}; '
          f'font-size:11px; line-height:16px;"><span style="color:{GOLD}; font-weight:bold;">Headline:</span> {headline}</td></tr>') if headline else ""
    quip = (f'<tr><td colspan="3" style="border-top:1px solid {BORDER}; padding:9px 16px; font-family:{SERIF}; '
            f'font-style:italic; color:{MUTE}; font-size:12px; line-height:18px;">{_esc(meetings["quip"])}</td></tr>') if meetings.get("quip") else ""
    return (
        _section_label("Today's Meetings") +
        '<tr><td style="background-color:#FFFFFF; padding:8px 36px 0 36px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>'
        f'{you}<td width="9" style="font-size:1px;">&nbsp;</td>{arjun}<td width="9" style="font-size:1px;">&nbsp;</td>{prathap}</tr>'
        '<tr><td colspan="5" style="padding-top:18px;"><table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
        f'<tr><td style="border-top:1px solid {BORDER}; font-size:1px; line-height:1px;">&nbsp;</td></tr>'
        f'<tr><td style="font-family:{SERIF}; color:{NAVY}; font-size:13px; letter-spacing:4px; text-transform:uppercase; padding:14px 0 8px 0; line-height:18px;">Tomorrow</td></tr>'
        '</table></td></tr>'
        '<tr><td colspan="5"><table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background-color:{PANEL}; border:1px solid {BORDER}; border-left:3px solid {ORANGE};">'
        # Same column list as the Today row — hardcoding "Prathap" here made the
        # CFO brief show an em-dash for a person who was not in its data.
        f'<tr>' + "".join(
            numcell(key, "You" if i == 0 else key, i == 0)
            for i, (key, _role) in enumerate(columns[:3])) + '</tr>'
        f'{hl}{quip}'
        '</table></td></tr>'
        '</table></td></tr>')


def render_footer():
    return (
        '<tr><td style="background-color:#FFFFFF; padding:24px 36px 28px 36px;" align="center">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
        f'<tr><td style="border-top:1px solid {BORDER}; font-size:1px; line-height:1px;">&nbsp;</td></tr></table>'
        f'<div style="font-family:{SANS}; color:{FAINT}; font-size:10px; line-height:16px; padding-top:14px;">'
        'CONFIDENTIAL &mdash; Internal use only &mdash; DPA Act 18 of 2024 controlled<br>'
        'Every amount and name here is taken from the email, Graphite or Omni &mdash; never AI-guessed.</div></td></tr>')


def render_html(digest, meetings=None, events=None, waiting=None, lens=None,
                notes=None, notes_heading="From your team", notes_blurb=None,
                band="CEO Monitor", meeting_columns=None):
    # ONE PLACE ONLY (CFO, 16-Sep-2026 - he reported this on 11-Sep and it came
    # back). That fix stood the separate box down only when there was exactly ONE
    # matter; with two matters the box returned AND the same story still printed
    # as its own card underneath. Counting the items was the bug. The story now
    # renders in exactly one place - its own card, badged - so there is no second
    # place for it to appear and no condition left to get wrong.
    rows = (
        render_header(digest, band=band)
        + render_title_block(digest)
        + render_money_lens(lens)
        + render_sections(digest)
        + render_notes(notes, heading=notes_heading, blurb=notes_blurb)
        + render_waiting(waiting)
        + render_meetings(meetings, columns=meeting_columns)
        + render_footer()
    )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        'style="background-color:#EDEFF2;"><tr><td align="center" style="padding:0;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" '
        'style="width:600px; max-width:600px;">'
        f'{rows}'
        '</table></td></tr></table>')
