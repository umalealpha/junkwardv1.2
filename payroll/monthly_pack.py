# -*- coding: utf-8 -*-
"""
Monthly payroll pack + three-way control check (ADIC and any company).

Built 2026-08-24 for the CFO. One place that answers, for a chosen payroll
period vs the prior period and one company:

  1. Totals vs prior month  — headcount, per-component earnings, gross/paye/net.
  2. Incentive reconciliation — every INCENTIVE paid on a payslip is matched
     (by linked employee OR by name) against the Staff Incentive module
     (hris.IncentiveRequest / IncentiveLine). Flags: paid-not-approved,
     paid-more-than-approved, rejected-but-paid, approved-not-paid.
  3. Authority to Recruit / Regrade — every basic-salary change and new joiner
     is matched against an approved recruitment.AuthorityToRecruit. Flags:
     no-authority-on-file, paid-above-signed-amount, applied-before-effective.

Pure/read-only: builds a dict. The views layer renders JSON or an xlsx.
Reuses amendment_views._scoped_payslips (entity-grant clamp) so a scoped user
can never read another entity's pay.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from collections import defaultdict

ZERO = Decimal("0")

# Component codes that are NOT allowances (so allowances = earnings - these).
_NON_ALLOWANCE_EARN = {"BASIC", "COMMISSION", "INCENTIVE", "BONUS"}


def _dec(v) -> Decimal:
    try:
        return Decimal(str(v if v is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        return ZERO


def _prior_period_name(period_name: str) -> str:
    """'2026-08' -> '2026-07'. Handles the January roll-back."""
    m = re.match(r"^(\d{4})-(\d{2})$", period_name.strip())
    if not m:
        return ""
    y, mth = int(m.group(1)), int(m.group(2))
    mth -= 1
    if mth == 0:
        mth = 12
        y -= 1
    return f"{y:04d}-{mth:02d}"


def _name_tokens(name: str):
    n = re.sub(r"[^a-z ]", " ", (name or "").lower())
    stop = {"the", "team", "and"}
    return set(w for w in n.split() if len(w) > 2 and w not in stop)


def same_person(a: str, b: str) -> bool:
    """Two names refer to the same person if they share >= 2 name tokens.
    Deliberately conservative: 'Pako Lisley Kago' matches 'Pako Kago' (pako,
    kago) but 'Pako Kago' does NOT match 'Kago Tshutlhedi' (only 'kago')."""
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) >= 2


# ---------------------------------------------------------------------------
# per-employee component sums for a scoped period
# ---------------------------------------------------------------------------
def _employee_component_map(payslips):
    """{employee_id: {code: Decimal}} plus {employee_id: payslip} from a
    scoped Payslip queryset (lines prefetched)."""
    comp = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    slip = {}
    for p in payslips:
        slip[str(p.employee_id)] = p
        for l in p.lines.all():
            comp[str(p.employee_id)][l.component.code] += l.amount
    return comp, slip


def _totals_block(payslips):
    """Company/period totals: headcount + component sums + header gross/paye/net/ctc."""
    comp_tot = defaultdict(lambda: Decimal("0"))
    gross = paye = net = ctc = ZERO
    n = 0
    for p in payslips:
        n += 1
        gross += p.gross_amount
        paye += p.paye_amount
        net += p.net_amount
        ctc += (p.ctc_amount or ZERO)
        for l in p.lines.all():
            comp_tot[l.component.code] += l.amount
    allow = sum((v for k, v in comp_tot.items()
                 if k not in _NON_ALLOWANCE_EARN and v > 0
                 and k not in ("PAYE",)), ZERO)
    return {
        "headcount": n,
        "gross": gross, "paye": paye, "net": net, "ctc": ctc,
        "basic": comp_tot.get("BASIC", ZERO),
        "commission": comp_tot.get("COMMISSION", ZERO),
        "incentive": comp_tot.get("INCENTIVE", ZERO),
        "bonus": comp_tot.get("BONUS", ZERO),
        "allowances": allow,
        "by_component": {k: v for k, v in sorted(comp_tot.items())},
    }


# ---------------------------------------------------------------------------
# incentive reconciliation
# ---------------------------------------------------------------------------
def _incentive_recon(period_name, comp_cur, slip_cur):
    from hris.incentive_models import IncentiveLine

    # payroll incentive per employee (paid this period, in scope)
    pay = []  # (name, dept, amount)
    for eid, cmap in comp_cur.items():
        amt = cmap.get("INCENTIVE", ZERO)
        if amt and amt != 0:
            p = slip_cur[eid]
            pay.append((p.employee.full_name, (p.employee.department or ""), amt))

    # module lines for this period
    mod = []  # dict(status, proc, name, emp, amt, req)
    for il in IncentiveLine.objects.filter(request__period=period_name).select_related("request", "employee"):
        mod.append(dict(
            status=il.request.status,
            proc=bool(il.request.payroll_processed),
            name=il.name or "",
            emp=(il.employee.full_name if il.employee_id else ""),
            amt=il.amount,
            req=il.request.title,
        ))

    def is_lump(m):
        nm = (m["name"] or "").lower().strip()
        return (not m["emp"]) and m["amt"] >= Decimal("5000") and "claim" in nm

    lumps = [m for m in mod if is_lump(m)]
    named = [m for m in mod if not is_lump(m)]

    rows = []
    for name, dept, amt in sorted(pay, key=lambda x: -x[2]):
        match = None
        for m in named:
            cand = m["emp"] or m["name"]
            if same_person(name, cand):
                match = m
                break
        if match:
            if match["status"] == "rejected":
                flag, note = "REJECTED_BUT_PAID", f"Module request was REJECTED (module {match['amt']})"
            elif match["status"] != "approved":
                flag, note = "PAID_NOT_APPROVED", f"Module status={match['status']} (module {match['amt']})"
            elif match["amt"] != amt:
                flag, note = "AMOUNT_DIFF", f"Module approved {match['amt']}"
            elif not match["proc"]:
                flag, note = "APPROVED_NOT_PROCESSED", "Approved but not marked processed"
            else:
                flag, note = "OK", "In module (approved & processed)"
        elif lumps and "claim" in dept.lower():
            flag, note = "LUMP", "Likely inside a Motor-Claims lump approval (not itemised)"
        else:
            flag, note = "NOT_IN_MODULE", "No module request found for this person"
        rows.append(dict(name=name, dept=dept, amount=amt, flag=flag, note=note))

    # approved & processed module lines not paid in payroll (by name)
    not_paid = []
    for m in named:
        if m["status"] == "approved" and not any(same_person(m["emp"] or m["name"], nm) for nm, d, a in pay):
            not_paid.append(dict(name=(m["emp"] or m["name"]), amount=m["amt"], req=m["req"], processed=m["proc"]))

    return dict(
        rows=rows,
        lumps=[dict(name=m["name"], amount=m["amt"], status=m["status"]) for m in lumps],
        not_paid=not_paid,
        pay_total=sum((a for _, _, a in pay), ZERO),
        mod_approved_total=sum((m["amt"] for m in mod if m["status"] == "approved"), ZERO),
        mod_processed_total=sum((m["amt"] for m in mod if m["proc"]), ZERO),
        mod_pending_total=sum((m["amt"] for m in mod if m["status"] == "pending"), ZERO),
        mod_rejected_total=sum((m["amt"] for m in mod if m["status"] == "rejected"), ZERO),
    )


# ---------------------------------------------------------------------------
# Authority to Recruit / Regrade cross-check
# ---------------------------------------------------------------------------
def _atr_base_monthly(authority):
    """Signed monthly base salary from an authority's salary_lines."""
    for line in (authority.salary_lines or []):
        item = (line.get("item", "") or "").lower()
        if "base salary" in item or item.strip() in ("basic", "basic salary", "salary"):
            return _dec(line.get("monthly"))
    return _dec(authority.quoted_ctc_monthly)


def _atr_check(period_start, comp_cur, slip_cur, comp_prior, slip_prior):
    from recruitment.models import AuthorityToRecruit as ATR

    approved = list(ATR.objects.filter(status="approved"))

    def find_auth(name, kinds):
        for a in approved:
            if a.kind in kinds and (same_person(name, a.person_name)
                                    or (a.employee_id and same_person(name, getattr(a.employee, "get_full_name", lambda: "")() or ""))):
                return a
        return None

    rows = []
    ids_cur, ids_prior = set(comp_cur), set(comp_prior)

    # joiners: on this period, not the prior
    for eid in ids_cur - ids_prior:
        p = slip_cur[eid]
        name = p.employee.full_name
        auth = find_auth(name, ("recruit",))
        if auth:
            rows.append(dict(person=name, change="New joiner", authority=auth.reference,
                             kind="recruit", flag="OK", note="Approved Authority to Recruit on file"))
        else:
            rows.append(dict(person=name, change="New joiner", authority="", kind="recruit",
                             flag="NO_AUTHORITY", note="New joiner with no Authority to Recruit on file"))

    # salary changes: basic changed vs prior
    for eid in ids_cur & ids_prior:
        cur_basic = comp_cur[eid].get("BASIC", ZERO)
        prior_basic = comp_prior[eid].get("BASIC", ZERO)
        if cur_basic == prior_basic:
            continue
        p = slip_cur[eid]
        name = p.employee.full_name
        auth = find_auth(name, ("regrade",))
        if not auth:
            rows.append(dict(person=name, change=f"Basic {prior_basic} -> {cur_basic}",
                             authority="", kind="regrade", flag="NO_AUTHORITY",
                             note="Salary change with no Authority to Regrade on file"))
            continue
        signed = _atr_base_monthly(auth)
        notes = []
        flag = "OK"
        if signed and cur_basic > signed:
            flag = "ABOVE_SIGNED"
            notes.append(f"Paid {cur_basic} vs signed {signed} (+{cur_basic - signed})")
        if auth.effective_date and period_start and auth.effective_date > period_start:
            flag = "EARLY" if flag == "OK" else flag
            notes.append(f"Applied before effective date {auth.effective_date.isoformat()}")
        if flag == "OK":
            notes.append("Matches approved regrade")
        rows.append(dict(person=name, change=f"Basic {prior_basic} -> {cur_basic}",
                         authority=auth.reference, kind="regrade", flag=flag, note="; ".join(notes)))

    return sorted(rows, key=lambda r: (r["flag"] == "OK", r["person"]))


# ---------------------------------------------------------------------------
# major changes
# ---------------------------------------------------------------------------
def _major_changes(comp_cur, slip_cur, comp_prior, slip_prior):
    ids_cur, ids_prior = set(comp_cur), set(comp_prior)
    joiners = [dict(name=slip_cur[i].employee.full_name,
                    dept=(slip_cur[i].employee.department or ""),
                    basic=comp_cur[i].get("BASIC", ZERO)) for i in ids_cur - ids_prior]
    leavers = [dict(name=slip_prior[i].employee.full_name,
                    dept=(slip_prior[i].employee.department or ""),
                    basic=comp_prior[i].get("BASIC", ZERO)) for i in ids_prior - ids_cur]
    moves = []
    for i in ids_cur & ids_prior:
        pb, cb = comp_prior[i].get("BASIC", ZERO), comp_cur[i].get("BASIC", ZERO)
        pg, cg = slip_prior[i].gross_amount, slip_cur[i].gross_amount
        if abs(cb - pb) >= 500 or abs(cg - pg) >= 1500:
            moves.append(dict(name=slip_cur[i].employee.full_name,
                              dept=(slip_cur[i].employee.department or ""),
                              basic_prior=pb, basic_cur=cb, basic_change=cb - pb,
                              gross_prior=pg, gross_cur=cg, gross_change=cg - pg))
    moves.sort(key=lambda m: -abs(m["gross_change"]))
    return dict(joiners=sorted(joiners, key=lambda x: x["name"]),
                leavers=sorted(leavers, key=lambda x: x["name"]),
                moves=moves)


# ---------------------------------------------------------------------------
# top-level
# ---------------------------------------------------------------------------
def build_monthly_pack(period_name: str, company, user):
    """Return the full pack dict for `period_name` (e.g. '2026-08') and a
    Company instance `company`, scoped to `user`. Raises LookupError if the
    period does not exist."""
    from .models import PayrollPeriod
    from .amendment_views import _scoped_payslips

    try:
        period = PayrollPeriod.objects.get(period_name=period_name)
    except PayrollPeriod.DoesNotExist:
        raise LookupError(f"Payroll period {period_name} not found")

    prior_name = _prior_period_name(period_name)
    prior = PayrollPeriod.objects.filter(period_name=prior_name).first()

    ps_cur = list(_scoped_payslips(period, user, company=company))
    ps_prior = list(_scoped_payslips(prior, user, company=company)) if prior else []

    comp_cur, slip_cur = _employee_component_map(ps_cur)
    comp_prior, slip_prior = _employee_component_map(ps_prior)

    return dict(
        meta=dict(company=company.name, company_id=str(company.id),
                  period=period_name, prior_period=prior_name if prior else None,
                  period_start=period.start_date.isoformat() if period.start_date else None),
        totals=dict(current=_totals_block(ps_cur),
                    prior=_totals_block(ps_prior) if prior else None),
        incentive_recon=_incentive_recon(period_name, comp_cur, slip_cur),
        atr_check=_atr_check(period.start_date, comp_cur, slip_cur, comp_prior, slip_prior),
        major_changes=_major_changes(comp_cur, slip_cur, comp_prior, slip_prior),
    )
