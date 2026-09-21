"""
healthcare/dashboard_views.py — the live Alpha Direct Health dashboard feed.

GET /api/v1/health/dashboard/?asof=YYYY-MM-DD

Built to the Health feature request of 8-Sep-2026 (from the Projects Lead,
Health). Every figure is READ from Omni's own tables on each call; the endpoint
holds no stored numbers and no cached snapshot, so a changed bordereaux, claim
run or register row shows up on the next load with no code change.

WHERE EACH FIGURE COMES FROM
  premium (GWP)   HealthcareUpload kind=revenue  — the monthly health bordereaux
  claims paid     HealthcareUpload kind=claims   — the AFT remittance files
  lives / groups  the ADH group register on the Graphite read replica,
                  counted by healthcare.register_counts (counts only, no PII)
  quote pipeline  HealthQuote.status
A superseded or failed upload is excluded — /health/summary/ does not do that,
which is why this endpoint aggregates its own rows rather than calling it.

THE VAT DIRECTION — the one trap here
  HealthcareUpload.gross_amount on a REVENUE upload is stored INCLUSIVE of VAT.
  Proven on prod 8-Sep-2026: August 2026 stores 57,753.54 and the bordereaux
  excl-VAT figure is 50,661.00 (57,753.54 / 1.14). So:
      gwp_incl = SUM(gross_amount)          gwp_excl = gwp_incl / 1.14
  Multiplying the stored figure by 1.14 would overstate premium by 30%.
  VAT rounds HALF UP — a tax decision, never a language default.

THREE KNOWN DEFECTS THE REQUEST ASKED US TO CORRECT, AND HOW
  1. "GWP booked · FY to date" was inception-to-date. Here gwp_ytd_* is bounded
     to the financial year of `asof` (Jul-Jun); inception-to-date is still
     returned, but only under its own `itd` key so it can never be mislabelled.
  2. The headline loss ratio was inception-to-date. Here the headline is
     loss_ratio_ytd_pct (FY claims / FY premium excl VAT); the ITD figure sits
     under `itd`.
  3. The on-cover book (quote-derived) and the register disagree. Both are
     returned side by side under `gap`, with the difference stated, so the
     mismatch is visible instead of averaged away.

DATA PROTECTION (AD-POL-AI-GOV-001): nothing in this response identifies a
person. Lives are counts; groups are employer-group names. No Omang, no bank
detail, no address, no medical data is selected or returned.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from healthcare.models import HealthcareUpload, HealthQuote, HealthQuoteMember
from healthcare import register_counts as rc

VAT_RATE = Decimal("0.14")
VAT_MULT = Decimal("1") + VAT_RATE  # 1.14

# The two FY27 targets in the request. Kept here, named, so nobody has to read
# them out of the page markup.
GWP_OBJECTIVE = Decimal("16000000")
LIVES_TARGET = 7500

MONTH_ABBR = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def _q2(v: Decimal) -> Decimal:
    """Money / percentage to 2dp, HALF UP."""
    return Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _num(v: Optional[Decimal]) -> Optional[float]:
    return None if v is None else float(_q2(v))


def _excl(incl: Decimal) -> Decimal:
    """Premium excluding VAT from the stored VAT-inclusive figure."""
    return _q2(Decimal(incl) / VAT_MULT)


def _fy_end_year(year: int, month: int) -> int:
    """FY runs Jul-Jun and is named by the year it ends in: Jul 2026 -> FY27."""
    return year + 1 if month >= 7 else year


def _fy_bounds(fy_end: int) -> Tuple[date, date]:
    return date(fy_end - 1, 7, 1), date(fy_end, 6, 30)


def _parse_asof(raw: Optional[str]) -> date:
    if raw:
        try:
            y, m, d = (int(p) for p in str(raw).strip()[:10].split("-"))
            return date(y, m, d)
        except (ValueError, TypeError):
            pass
    return timezone.localdate()


def _month_key(year: int, month: int) -> int:
    return year * 12 + month


def _upload_months(kind: str, max_key: int) -> Dict[int, Dict[str, Decimal]]:
    """{month_key: {'gross': Decimal, 'paid': Decimal}} for one upload kind.

    Superseded and failed uploads are dropped — a corrected AFT re-send marks
    the earlier row superseded, and counting both double-counts the claim.
    """
    out: Dict[int, Dict[str, Decimal]] = {}
    qs = (
        HealthcareUpload.objects.filter(
            kind=kind, status=HealthcareUpload.Status.PARSED, superseded=False
        )
        .exclude(period_year__isnull=True)
        .exclude(period_month__isnull=True)
        .only("period_year", "period_month", "gross_amount", "paid_amount")
    )
    for up in qs:
        key = _month_key(up.period_year, up.period_month)
        if key > max_key:
            continue
        slot = out.setdefault(key, {"gross": Decimal("0"), "paid": Decimal("0")})
        slot["gross"] += up.gross_amount or Decimal("0")
        slot["paid"] += up.paid_amount or Decimal("0")
    return out


def _pipeline() -> Dict[str, int]:
    """Live quote counts. 'renewals' = invoiced quotes whose billing period is
    not the current month, matching /health/quotes/dashboard/ exactly so the
    two screens can never disagree."""
    from django.db.models import Count

    S = HealthQuote.Status
    counts = {
        row["status"]: row["n"]
        for row in HealthQuote.objects.values("status").annotate(n=Count("id"))
    }
    month = f"{timezone.now():%B %Y}"
    renewals = (
        HealthQuote.objects.filter(status=S.INVOICED)
        .exclude(billing_period=month)
        .count()
    )
    return {
        "draft": counts.get(S.DRAFT, 0),
        "inReview": counts.get(S.SUBMITTED, 0),
        "approved": counts.get(S.APPROVED, 0),
        "invoiced": counts.get(S.INVOICED, 0),
        "renewals": renewals,
    }


def _quote_book_lives() -> int:
    """Lives on the quote-derived 'on-cover book' — approved + invoiced quotes.
    This is the 38 that disagrees with the register; returned for the gap
    panel, never used as the headline."""
    S = HealthQuote.Status
    ids = list(
        HealthQuote.objects.filter(status__in=[S.APPROVED, S.INVOICED]).values_list(
            "id", flat=True
        )
    )
    return HealthQuoteMember.objects.filter(quote_id__in=ids).count()


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def health_dashboard(request):
    asof = _parse_asof(request.query_params.get("asof"))
    max_key = _month_key(asof.year, asof.month)
    fy_end = _fy_end_year(asof.year, asof.month)
    fy_start_date, fy_end_date = _fy_bounds(fy_end)

    revenue = _upload_months(HealthcareUpload.Kind.REVENUE, max_key)
    claims = _upload_months(HealthcareUpload.Kind.CLAIMS, max_key)

    # ---- monthly series -------------------------------------------------
    monthly: List[Dict[str, Any]] = []
    for key in sorted(set(revenue) | set(claims)):
        year, month = divmod(key - 1, 12)
        month += 1
        incl = revenue.get(key, {}).get("gross", Decimal("0"))
        excl = _excl(incl) if incl else Decimal("0")
        paid = claims[key]["paid"] if key in claims else None
        lr = _q2(paid / excl * 100) if (paid is not None and excl > 0) else None
        monthly.append(
            {
                "month": f"{year:04d}-{month:02d}",
                "label": f"{MONTH_ABBR[month]} {year % 100:02d}",
                "fy": _fy_end_year(year, month),
                "gwpExcl": _num(excl),
                "gwpIncl": _num(incl),
                "claims": _num(paid),
                "lossRatioPct": _num(lr),
            }
        )

    def _sum(rows: List[Dict[str, Any]], field: str) -> Decimal:
        return sum(
            (Decimal(str(r[field])) for r in rows if r[field] is not None), Decimal("0")
        )

    fy_rows = [r for r in monthly if r["fy"] == fy_end]
    revenue_rows = [r for r in monthly if r["gwpIncl"]]

    gwp_ytd_incl = _sum(fy_rows, "gwpIncl")
    gwp_ytd_excl = _sum(fy_rows, "gwpExcl")
    claims_ytd = _sum(fy_rows, "claims")
    lr_ytd = _q2(claims_ytd / gwp_ytd_excl * 100) if gwp_ytd_excl > 0 else None

    gwp_itd_incl = _sum(monthly, "gwpIncl")
    gwp_itd_excl = _sum(monthly, "gwpExcl")
    claims_itd = _sum(monthly, "claims")
    lr_itd = _q2(claims_itd / gwp_itd_excl * 100) if gwp_itd_excl > 0 else None

    latest = revenue_rows[-1] if revenue_rows else None
    prior = revenue_rows[-2] if len(revenue_rows) > 1 else None
    month_incl = Decimal(str(latest["gwpIncl"])) if latest else Decimal("0")
    month_excl = Decimal(str(latest["gwpExcl"])) if latest else Decimal("0")
    mom = None
    if latest and prior and Decimal(str(prior["gwpIncl"])) > 0:
        mom = _q2((month_incl / Decimal(str(prior["gwpIncl"])) - 1) * 100)
    lr_month = (
        Decimal(str(latest["lossRatioPct"]))
        if (latest and latest["lossRatioPct"] is not None)
        else None
    )

    # ---- lives, from the register (counts only) -------------------------
    reg = rc.register_counts()
    reg_ok = bool(reg.get("available"))
    quote_lives = _quote_book_lives()

    # ---- response -------------------------------------------------------
    return Response(
        {
            "asOf": asof.isoformat(),
            "lastUpdated": timezone.now().isoformat(),
            "fy": {
                "label": f"FY{fy_end % 100:02d}",
                "start": fy_start_date.isoformat(),
                "end": fy_end_date.isoformat(),
            },
            "kpi": {
                "gwpYtdIncl": _num(gwp_ytd_incl),
                "gwpYtdExcl": _num(gwp_ytd_excl),
                "gwpMonthIncl": _num(month_incl),
                "gwpMonthExcl": _num(month_excl),
                "gwpMonthLabel": latest["label"] if latest else None,
                "gwpMonMoMPct": _num(mom),
                "annualisedIncl": _num(month_incl * 12),
                "objective": _num(GWP_OBJECTIVE),
                "activeLives": reg.get("active_lives") if reg_ok else None,
                "policyholders": reg.get("policyholders") if reg_ok else None,
                "dependants": reg.get("dependants") if reg_ok else None,
                "livesTarget": LIVES_TARGET,
                "lossRatioYtdPct": _num(lr_ytd),
                "lossRatioMonthPct": _num(lr_month),
            },
            # Inception-to-date, separately labelled so it can never be read as FY.
            "itd": {
                "gwpIncl": _num(gwp_itd_incl),
                "gwpExcl": _num(gwp_itd_excl),
                "claims": _num(claims_itd),
                "lossRatioPct": _num(lr_itd),
                "firstMonth": monthly[0]["month"] if monthly else None,
            },
            "monthly": monthly,
            "fyTotals": [
                {
                    "fy": fy,
                    "label": f"FY{fy % 100:02d}",
                    "gwpExcl": _num(
                        _sum([r for r in monthly if r["fy"] == fy], "gwpExcl")
                    ),
                    "gwpIncl": _num(
                        _sum([r for r in monthly if r["fy"] == fy], "gwpIncl")
                    ),
                    "claims": _num(
                        _sum([r for r in monthly if r["fy"] == fy], "claims")
                    ),
                    "isCurrent": fy == fy_end,
                }
                for fy in sorted({r["fy"] for r in monthly})
            ],
            "cancellations": {
                "reportMonth": f"{asof.year:04d}-{asof.month:02d}",
                "available": reg_ok,
                "totalLives": reg.get("cancelled_lives") if reg_ok else None,
                "inMonth": None,
                "reasonsAvailable": False,
                "reasons": [],
                "byGroup": reg.get("by_group", []) if reg_ok else [],
                "note": (
                    "Graphite records no cancellation reason and no cancellation "
                    "date for group health, so the reason split and the "
                    '"dated in this month" count cannot be read live. Cancelled '
                    "lives per employer group are live."
                ),
            },
            "pipeline": _pipeline(),
            # The register / on-cover-book disagreement, stated rather than blended.
            "gap": {
                "registerLives": reg.get("active_lives") if reg_ok else None,
                "quoteBookLives": quote_lives,
                "difference": ((reg.get("active_lives") or 0) - quote_lives)
                if reg_ok
                else None,
                "reconciled": bool(reg_ok and reg.get("active_lives") == quote_lives),
                "note": (
                    "Headline lives are the ADH group register. The on-cover book "
                    "is derived from approved and invoiced quotations. They are "
                    "shown separately and never blended."
                ),
            },
            "sources": {
                "premium": "Health premium bordereaux uploads (Revenue)",
                "claims": "AFT claim remittance uploads (Claims)",
                "lives": (
                    "ADH group register, Graphite read replica"
                    if reg_ok
                    else "ADH group register — unavailable"
                ),
                "pipeline": "Health quotations",
                "vatRate": float(VAT_RATE),
                "registerGroups": reg.get("groups") if reg_ok else None,
                "registerUnavailableReason": None if reg_ok else reg.get("reason"),
                "registerLagSeconds": reg.get("replica_lag_seconds"),
            },
        }
    )
