"""
hris/amendment_notify.py — emails for the HRIS amendment workflow.

On submit: email the approver (please approve) + the maker (confirmation).
On decision: email the maker (approved & applied / rejected).

The plain-English summary is written by DeepSeek, but DeepSeek only ever sees
field LABELS and the maker/target names — never the actual values (salary,
national ID, dates). The concrete old→new values are rendered locally so no
employee PII is sent to the external model. If DeepSeek is unavailable, a
templated summary is used instead.
"""
from __future__ import annotations

import html
import logging

from django.conf import settings

from core.notifications import send_html_with_cfo_cc

log = logging.getLogger(__name__)

_BRAND_NAVY   = '#0D1B2A'
_BRAND_ORANGE = '#F4A623'


def _display_name(email: str, fallback: str = '') -> str:
    local = (email or '').split('@')[0]
    return fallback or (local.replace('.', ' ').title() if local else 'A team member')


def _changes_table(amendment) -> str:
    rows = []
    for change in amendment.changes.values():
        label = html.escape(str(change.get('label', '')))
        old   = html.escape(str(change.get('old', '')) or '—')
        new   = html.escape(str(change.get('new', '')) or '—')
        rows.append(
            f'<tr><td style="padding:6px 10px;border:1px solid #e3e6ea;">{label}</td>'
            f'<td style="padding:6px 10px;border:1px solid #e3e6ea;color:#6b7280;">{old}</td>'
            f'<td style="padding:6px 10px;border:1px solid #e3e6ea;color:{_BRAND_NAVY};"><strong>{new}</strong></td></tr>'
        )
    return (
        '<table style="border-collapse:collapse;font-size:13px;margin:10px 0;">'
        '<thead><tr style="background:%s;color:#fff;">'
        '<th style="padding:6px 10px;text-align:left;">Field</th>'
        '<th style="padding:6px 10px;text-align:left;">Was</th>'
        '<th style="padding:6px 10px;text-align:left;">Now</th></tr></thead>'
        '<tbody>%s</tbody></table>' % (_BRAND_NAVY, ''.join(rows))
    )


def _deepseek_summary(*, maker_name: str, target_kind: str, target_label: str,
                      field_labels: list[str], reason: str) -> str:
    """Plain-English intro from DeepSeek. Labels only — no values."""
    try:
        from core.ai_assist import deepseek_complete, DeepSeekUnavailable
    except Exception:                                   # noqa: BLE001
        return _fallback_summary(maker_name, target_kind, target_label, field_labels)
    try:
        fields = ', '.join(field_labels) or 'one or more fields'
        prompt = (
            f"{maker_name} has proposed changes to the {target_kind} record for "
            f"\"{target_label}\". The fields being changed are: {fields}. "
            f"Reason given: \"{reason or 'not stated'}\". "
            "Write 2-3 short, clear sentences for an approval email telling the "
            "approver what is being changed and asking them to review and approve. "
            "Do NOT invent any values. Plain text only, no greeting, no sign-off."
        )
        out = deepseek_complete(
            prompt,
            system_prompt=("You write concise, professional HR approval-request "
                           "summaries. Never fabricate data values."),
            timeout=20.0,
        )
        return (out or '').strip() or _fallback_summary(
            maker_name, target_kind, target_label, field_labels)
    except Exception:                                   # noqa: BLE001 — incl. DeepSeekUnavailable
        log.info("DeepSeek summary unavailable; using template.")
        return _fallback_summary(maker_name, target_kind, target_label, field_labels)


def _fallback_summary(maker_name, target_kind, target_label, field_labels) -> str:
    fields = ', '.join(field_labels) or 'one or more fields'
    return (f"{maker_name} has proposed amendments to the {target_kind} record for "
            f"\"{target_label}\", changing: {fields}. Please review the details "
            f"below and approve or reject.")


def _shell(title: str, intro_html: str, body_html: str) -> str:
    return (
        f'<div style="font-family:Georgia,serif;color:{_BRAND_NAVY};max-width:640px;margin:0 auto;">'
        f'<div style="background:{_BRAND_NAVY};color:#fff;padding:16px 20px;border-radius:8px 8px 0 0;">'
        f'<h2 style="margin:0;font-size:18px;">{html.escape(title)}</h2></div>'
        f'<div style="background:#fff;border:1px solid #e3e6ea;border-top:none;'
        f'padding:18px 20px;border-radius:0 0 8px 8px;font-size:14px;line-height:1.6;">'
        f'{intro_html}{body_html}'
        f'<p style="font-size:12px;color:#9aa0a6;margin-top:16px;">Alpha Direct Insurance · omni HRIS</p>'
        f'</div></div>'
    )


def notify_submitted(amendment) -> None:
    """Email the approver (please approve) and the maker (confirmation)."""
    maker_name   = _display_name(amendment.maker_email,
                                 getattr(amendment.maker, 'get_full_name', lambda: '')() or '')
    field_labels = [c.get('label', '') for c in amendment.changes.values()]
    summary = _deepseek_summary(
        maker_name=maker_name, target_kind=amendment.get_target_kind_display().lower(),
        target_label=amendment.target_label, field_labels=field_labels,
        reason=amendment.reason,
    )
    table = _changes_table(amendment)
    reason_html = (f'<p style="font-size:13px;"><em>Reason:</em> {html.escape(amendment.reason)}</p>'
                   if amendment.reason else '')

    # 1) Approver — please approve
    approver_intro = (
        f'<p>{html.escape(summary)}</p>'
        f'<p style="background:#fff7e6;border-left:4px solid {_BRAND_ORANGE};padding:8px 12px;font-size:13px;">'
        f'<strong>Action needed:</strong> review and approve or reject in omni → HRIS → Pending amendments.</p>'
    )
    send_html_with_cfo_cc(
        subject=f"HRIS amendment awaiting your approval — {amendment.target_label}",
        html=_shell("HRIS amendment — approval needed", approver_intro, table + reason_html),
        to=[amendment.approver_email] if amendment.approver_email else [],
        text_fallback=summary,
    )

    # 2) Maker — confirmation
    maker_intro = (
        f'<p>Hi {html.escape(maker_name)}, your amendment to '
        f'<strong>{html.escape(amendment.target_label)}</strong> has been submitted and is '
        f'<strong>pending approval</strong> by {html.escape(_display_name(amendment.approver_email))}. '
        f'You will be notified once it is decided. The record is unchanged until then.</p>'
    )
    if amendment.maker_email:
        send_html_with_cfo_cc(
            subject=f"Your HRIS amendment is pending approval — {amendment.target_label}",
            html=_shell("HRIS amendment submitted", maker_intro, table + reason_html),
            to=[amendment.maker_email],
            text_fallback=f"Your amendment to {amendment.target_label} is pending approval.",
        )


def notify_decided(amendment, *, applied: bool) -> None:
    """Email the maker the outcome (approved & applied / rejected)."""
    if not amendment.maker_email:
        return
    maker_name = _display_name(amendment.maker_email,
                               getattr(amendment.maker, 'get_full_name', lambda: '')() or '')
    decided_by = _display_name(amendment.approver_email)
    if applied:
        title, verb = "HRIS amendment approved", "approved and applied to the live record"
    else:
        title, verb = "HRIS amendment rejected", "rejected — the record was not changed"
    intro = (f'<p>Hi {html.escape(maker_name)}, your amendment to '
             f'<strong>{html.escape(amendment.target_label)}</strong> was <strong>{verb}</strong> '
             f'by {html.escape(decided_by)}.</p>')
    notes = (f'<p style="font-size:13px;"><em>Note:</em> {html.escape(amendment.decision_notes)}</p>'
             if amendment.decision_notes else '')
    send_html_with_cfo_cc(
        subject=f"{title} — {amendment.target_label}",
        html=_shell(title, intro, _changes_table(amendment) + notes),
        to=[amendment.maker_email],
        text_fallback=f"Your amendment to {amendment.target_label} was {verb}.",
    )
