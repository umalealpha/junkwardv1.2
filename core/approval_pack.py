"""core/approval_pack.py — ONE detail pack per approval, rendered on THREE surfaces.

CFO 2026-09-20, on the "Commission for your review" email: *"its easy for me to
approve when I receive these things if it comes with details or I click and see
it in the app, now expecting a busy cfo to go inside the web version to check is
unreasonable"*.

The approval emails carried four lines (who, period, gross, net) and a one-tap
Approve button. Everything a reviewer would actually check — the policy lines,
the rates, the deductions, the sanity flags that `commissions/review_flags.py`
ALREADY computes — lived only on the desktop page. So the only honest way to
approve was to stop, find a laptop and sign in.

This module is the single place that answers "what am I signing?". A stream
registers ONE builder; the same pack is then rendered:

  * in the approval EMAIL body            (`pack_html`)
  * on the no-login magic confirm page    (`pack_html`, core/magic_action.py)
  * in the phone app's decision sheet     (JSON, /api/v1/my-approvals/pack/)

so the three can never drift apart — which is the whole point of not writing the
detail three times.

Contract (every key always present, so no surface has to defend itself):

    {"stream": str, "title": str, "subtitle": str,
     "summary": [{"label": str, "value": str}, ...],
     "columns": [str, ...], "rows": [[str, ...], ...],
     "row_count": int, "shown_count": int, "row_total": str,
     "checks": {"level": "clean"|"check", "items": [str, ...]},
     "note": str}

Best-effort by design: a pack is extra context on top of an approval that already
works. `build_pack` never raises and returns None when it cannot build one — a
missing pack must never block a sign-off.
"""
from __future__ import annotations

import html as _html

NAVY = "#0D1B2A"
ORANGE = "#F47C20"

#: Rows shown inline. Beyond this the pack says "…and N more" rather than
#: sending a reviewer a 400-row email that no phone will render.
MAX_ROWS = 40


def money(v, cur: str = "BWP") -> str:
    """P-format a Decimal/float/None. Empty string for None so a blank field
    renders blank instead of a misleading 0.00 (feedback: a false zero reads as
    a real figure)."""
    if v is None or v == "":
        return ""
    try:
        return f"{cur} {float(v):,.2f}".strip()
    except (TypeError, ValueError):
        return str(v)


def pct(v) -> str:
    """Rate as a percentage, accepting either 12.5 or 0.125 — both spellings
    exist in this codebase (see review_flags' %/fraction ambiguity note)."""
    if v is None or v == "":
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if 0 < f < 1:
        f *= 100
    return f"{f:,.2f}".rstrip("0").rstrip(".") + "%"


def empty_pack(stream: str, title: str = "", subtitle: str = "") -> dict:
    return {"stream": stream, "title": title, "subtitle": subtitle,
            "summary": [], "columns": [], "rows": [], "row_count": 0,
            "shown_count": 0, "row_total": "",
            "checks": {"level": "clean", "items": []}, "note": ""}


def make_pack(stream, *, title="", subtitle="", summary=None, columns=None,
              rows=None, row_total="", checks=None, note="") -> dict:
    """Build a contract-shaped pack, truncating the rows to MAX_ROWS.

    `row_count` is the TRUE number of lines and `shown_count` how many are in
    `rows` — a reviewer must be able to see that 40 of 212 lines are displayed,
    never be shown 40 and left to assume that is all of them.
    """
    rows = [[("" if c is None else str(c)) for c in r] for r in (rows or [])]
    total = len(rows)
    # A builder may hand `checks` in as the bare list of sentences — normalise
    # here rather than in three renderers. Getting this wrong used to reach
    # _checks_html as a list and blow up the whole email body on .get().
    if isinstance(checks, (list, tuple)):
        checks = {"level": "check" if checks else "clean", "items": list(checks)}
    return {
        "stream": stream,
        "title": title or "",
        "subtitle": subtitle or "",
        "summary": [s for s in (summary or []) if s.get("value") not in (None, "")],
        "columns": list(columns or []),
        "rows": rows[:MAX_ROWS],
        "row_count": total,
        "shown_count": min(total, MAX_ROWS),
        "row_total": row_total or "",
        "checks": checks or {"level": "clean", "items": []},
        "note": note or "",
    }


# --------------------------------------------------------------------------
# Registry. A stream key here is the SAME key core/approvals_views.py uses for
# its bulk adapters, so the app, the email and the confirm page all name an
# approval the same way.
# --------------------------------------------------------------------------

def _builders():
    from core import approval_packs
    return approval_packs.BUILDERS


def build_pack(stream: str, pk) -> dict | None:
    """The pack for ONE item, or None. Never raises — see module docstring."""
    if not stream or pk in (None, ""):
        return None
    try:
        builder = _builders().get(stream)
    except Exception:  # noqa: BLE001 — a bad import must not break an approval
        return None
    if builder is None:
        return None
    try:
        return builder(pk)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# HTML rendering — email body AND the magic-link confirm page.
# Table layout with inline styles only: Outlook, Gmail and the Samsung mail app
# in the CFO's screenshot all strip <style> blocks.
# --------------------------------------------------------------------------

def _e(v) -> str:
    return _html.escape("" if v is None else str(v))


def _summary_html(summary) -> str:
    if not summary:
        return ""
    rows = "".join(
        f'<tr><td style="padding:3px 14px 3px 0;color:#64748b;white-space:nowrap;">{_e(s["label"])}</td>'
        f'<td style="padding:3px 0;font-weight:600;">{_e(s["value"])}</td></tr>'
        for s in summary)
    return f'<table style="border-collapse:collapse;margin:10px 0;font-size:14px;">{rows}</table>'


def _lines_html(pack) -> str:
    if not pack["rows"]:
        return ""
    # Right-align every column but the first — these are money and rate columns,
    # and a ragged right edge is unreadable on a phone.
    head = "".join(
        f'<th style="padding:5px 8px 5px 0;text-align:{"left" if i == 0 else "right"};'
        f'color:#64748b;font-weight:600;border-bottom:1px solid #e2e8f0;">{_e(c)}</th>'
        for i, c in enumerate(pack["columns"]))
    body = "".join(
        "<tr>" + "".join(
            f'<td style="padding:4px 8px 4px 0;text-align:{"left" if i == 0 else "right"};'
            f'border-bottom:1px solid #f1f5f9;">{_e(c)}</td>'
            for i, c in enumerate(r)) + "</tr>"
        for r in pack["rows"])
    more = ""
    if pack["row_count"] > pack["shown_count"]:
        more = (f'<p style="color:#64748b;font-size:12px;margin:6px 0 0;">Showing the first '
                f'{pack["shown_count"]} of {pack["row_count"]} lines. The rest are in Omni.</p>')
    total = ""
    if pack["row_total"]:
        total = (f'<p style="margin:6px 0 0;font-size:13px;font-weight:700;">'
                 f'{pack["row_count"]} lines · {_e(pack["row_total"])}</p>')
    return (f'<table style="border-collapse:collapse;width:100%;margin:12px 0;font-size:12px;">'
            f'<tr>{head}</tr>{body}</table>{total}{more}')


def _checks_html(checks) -> str:
    if not checks:
        return ""
    level = checks.get("level")
    items = checks.get("items") or []
    # 'unknown' means the checks CRASHED (review_flags returns it deliberately).
    # Branching "not 'check' → green" turned that into a green tick in the CFO's
    # email — a control that failed reading as a control that passed.
    if level not in ("clean", "check"):
        return ('<p style="margin:12px 0;padding:8px 12px;background:#F1F5F9;border-left:3px solid #94A3B8;'
                'font-size:13px;color:#334155;">The sanity checks could not be run on this one — '
                'worth opening it in Omni before you approve.</p>')
    if level != "check" or not items:
        return ('<p style="margin:12px 0;padding:8px 12px;background:#F0FDF4;border-left:3px solid #16A34A;'
                'font-size:13px;color:#166534;">Checked — nothing looks out of place.</p>')
    lis = "".join(f'<li style="margin:2px 0;">{_e(i)}</li>' for i in items)
    return ('<div style="margin:12px 0;padding:8px 12px;background:#FFFBEB;border-left:3px solid #F59E0B;'
            'font-size:13px;color:#92400E;"><b>Worth a look before you approve</b>'
            f'<ul style="margin:6px 0 0;padding-left:18px;">{lis}</ul></div>')


def pack_html(pack) -> str:
    """The detail block. Returns '' for a missing pack so a caller can always
    drop it into an f-string without a conditional."""
    if not pack:
        return ""
    sub = (f'<p style="color:#64748b;font-size:12px;margin:0 0 4px;">{_e(pack["subtitle"])}</p>'
           if pack.get("subtitle") else "")
    note = (f'<p style="color:#64748b;font-size:12px;margin:8px 0 0;">{_e(pack["note"])}</p>'
            if pack.get("note") else "")
    return (f'<div style="margin:14px 0;padding:14px 16px;border:1px solid #e2e8f0;border-radius:8px;'
            f'color:#1e293b;">{sub}{_summary_html(pack["summary"])}'
            f'{_checks_html(pack.get("checks"))}{_lines_html(pack)}{note}</div>')


def app_link_html(base: str, label: str = "Or open it in the Omni app") -> str:
    """Deep link to the phone app's approvals inbox — the CFO's second route:
    read it in the email, or tap through and see it in the app."""
    url = f'{(base or "https://omni.alphadirect.co.bw").rstrip("/")}/app/approvals'
    return (f'<p style="margin:10px 0;font-size:13px;">'
            f'<a href="{_e(url)}" style="color:{NAVY};font-weight:600;">{_e(label)} &rarr;</a></p>')
