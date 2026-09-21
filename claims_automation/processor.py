"""claims_automation/processor.py — what Omni does when Graphite tells it
something happened to a claim.

  claim_registered      record the case + premium light
  claim_form_submitted  screen for policy breaches (rule), read the claim (AI,
                        words only), triage (rule); a breach drafts a
                        repudiation for the Claims Manager, otherwise the
                        handler gets the summary and the next step
  assessment_received   total loss  -> Agreement of Loss drafted, awaiting
                                       the handler's authorisation
                        itemised    -> the two draft purchase orders, through
                                       the SAME claims-PO engine the upload
                                       screen uses; handler reminded to check
                        neither     -> handler asked to upload the report PDF
  write_off_flagged     Agreement of Loss drafted (once)
  decision_recorded     stage recorded

Nothing here declines a claim, approves a PO, sends a client letter or moves
money. Every output is a draft, a task or an internal email.
"""

from __future__ import annotations

import logging
import urllib.request
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from .models import ClaimAutomationEvent, ClaimCase, ClaimLetter

log = logging.getLogger(__name__)

class PossessionMissing(Exception):
    """The yard has not confirmed they hold the wreck."""


CLAIMS_SENIOR_TITLES = (
    "claims_manager",
    "claims_team_leader",
    "senior_claims_associate",
)

# Client-facing wording for each rule flag. Plain, factual, never accusatory.
_FLAG_REASON = {
    "intoxication": (
        "The information on this claim records that the driver was under the "
        "influence of alcohol at the time of the loss. The policy does not cover "
        "loss or damage while the driver is under the influence of alcohol or drugs."
    ),
    "unlicensed_driver": (
        "The information on this claim records that the driver did not hold a "
        "valid driving licence. The policy covers the vehicle only while it is "
        "driven by a person holding a valid licence."
    ),
    "unauthorised_driver": (
        "The information on this claim records that the vehicle was being "
        "driven without your permission. The policy covers only drivers "
        "driving with your permission."
    ),
    "loss_before_cover": "The loss happened before the cover on this policy started.",
    "loss_after_expiry": "The loss happened after the cover on this policy had ended.",
    "late_report": (
        "The claim was reported to us later than the policy allows after the date "
        "of the loss."
    ),
}


# ── small helpers ───────────────────────────────────────────────────────────


def _dec(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _merge(dst: dict, src: dict) -> dict:
    """Shallow-per-group merge: a later event never blanks a fact an earlier
    one carried (Graphite sends what it has at that moment)."""
    out = dict(dst or {})
    for key, val in (src or {}).items():
        if isinstance(val, dict):
            cur = dict(out.get(key) or {})
            cur.update({k: v for k, v in val.items() if v not in (None, "")})
            out[key] = cur
        elif val not in (None, ""):
            out[key] = val
    return out


def _system_user():
    return (
        User.objects.filter(is_superuser=True, is_active=True)
        .order_by("date_joined")
        .first()
    )


def _titled_users(titles):
    from core.models import UserProfile

    ids = UserProfile.objects.filter(title__in=titles, is_active=True).values_list(
        "user_id", flat=True
    )
    return list(User.objects.filter(id__in=list(ids), is_active=True).order_by("id"))


def _claims_manager():
    users = _titled_users(("claims_manager",))
    return users[0] if users else None


def _handler_user(case: ClaimCase):
    email = (case.handler_email or "").strip()
    if email:
        u = User.objects.filter(email__iexact=email, is_active=True).first()
        if u:
            return u
    return _claims_manager()


def case_link(case: ClaimCase) -> str:
    base = getattr(settings, "PUBLIC_BASE_URL", "https://omni.alphadirect.co.bw")
    return f"{base}/claims-automation?claim={case.claim_ref}"


def _notify(case: ClaimCase, user, title: str, body_html: str, *, high=False) -> str:
    """One owned task + one internal email. Returns a plain-words action line that
    says so when either the task or the email did NOT go (never a false success)."""
    from core.models import OmniTask
    from core.notifications import send_html_with_cfo_cc

    if user is None:
        log.warning("claims_automation: nobody to notify for %s (%s)", case.claim_ref, title)
        return f"NOT DELIVERED, nobody to notify — {title}"
    failed = []
    try:
        with transaction.atomic():  # savepoint: a failed insert never poisons the caller
            OmniTask.objects.create(
                assigner=_system_user() or user,
                assignee=user,
                title=title[:200],
                body=f"{title}\n\nOpen: {case_link(case)}",
                priority=OmniTask.Priority.HIGH if high else OmniTask.Priority.NORMAL,
            )
    except Exception as exc:  # noqa: BLE001 — recorded below, the email still goes
        log.warning("claims_automation task failed for %s: %s", case.claim_ref, exc)
        failed.append("task")
    if user.email:
        html = (
            f'<div style="font-family:Arial,Helvetica,sans-serif;color:#0D1B2A;'
            f'max-width:640px;line-height:1.55">'
            f'<div style="background:#0D1B2A;padding:14px 20px;border-radius:8px 8px 0 0">'
            f'<span style="color:#F4A623;font-weight:bold">{_esc(title)}</span></div>'
            f'<div style="border:1px solid #e4e7ec;border-top:none;padding:20px;'
            f'border-radius:0 0 8px 8px">{body_html}'
            f'<p><a href="{case_link(case)}" style="background:#F4A623;color:#0D1B2A;'
            f'padding:9px 16px;border-radius:6px;text-decoration:none;font-weight:bold">'
            f"Open in Omni</a></p></div></div>"
        )
        try:
            send_html_with_cfo_cc(title, html, [user.email], cc_cfo=False)
        except Exception as exc:  # noqa: BLE001 — recorded below
            log.warning("claims_automation email failed for %s: %s", case.claim_ref, exc)
            failed.append("email")
    else:
        failed.append("email (no address)")
    who = user.get_full_name() or user.username
    if failed:
        return f"{title} → {who} (NOT delivered: {', '.join(failed)})"
    return f"{title} → {who}"


def _esc(value) -> str:
    import html

    return html.escape(str(value if value is not None else ""))


# ── the letters ─────────────────────────────────────────────────────────────


def _open_letter(case: ClaimCase, kind: str):
    return case.letters.filter(
        kind=kind,
        status__in=(
            ClaimLetter.Status.AWAITING,
            ClaimLetter.Status.APPROVED,
            ClaimLetter.Status.SENT,
        ),
    ).first()


def _letter_context(case: ClaimCase) -> dict:
    f = case.facts or {}
    claim, insured, policy, vehicle = (
        (f.get("claim") or {}),
        (f.get("insured") or {}),
        (f.get("policy") or {}),
        (f.get("vehicle") or {}),
    )
    veh = " ".join(
        str(x)
        for x in (
            vehicle.get("make"),
            vehicle.get("model"),
            vehicle.get("registration"),
        )
        if x
    )
    return {
        "claim_number": case.claim_ref,
        "insured_name": insured.get("name") or "Valued client",
        "insured_email": insured.get("email") or "",
        "policy_number": policy.get("number") or "",
        "vehicle": veh,
        "date_of_loss": claim.get("date_of_loss") or "",
    }


def draft_aol(case: ClaimCase, why: str) -> str:
    if _open_letter(case, ClaimLetter.Kind.AOL):
        return "Agreement of Loss already drafted — left as is"
    from .aol_figures import settlement

    f = case.facts or {}
    policy, premium = (f.get("policy") or {}), (f.get("premium") or {})
    assessment = f.get("assessment") or {}
    si = _dec(policy.get("sum_insured"))
    handler = _handler_user(case)
    if si is None or si <= 0:
        return _notify(
            case,
            handler,
            f"Write-off on {case.claim_ref}: sum insured missing",
            f"<p>{_esc(why)}. The Agreement of Loss could not be drafted because the "
            f"sum insured is not recorded on the policy in Graphite. Please add it; the "
            f"draft will follow on the next update.</p>",
            high=True,
        )
    excess = (
        _dec(policy.get("excess"))
        or _dec((assessment.get("summary") or {}).get("Excess"))
        or 0
    )
    outstanding = 0
    bal = _dec(premium.get("balance"))
    if bal and bal > 0 and not premium.get("settled"):
        outstanding = bal
    fig = settlement(si, excess, outstanding_premium=outstanding)
    ctx = _letter_context(case) | {"figures": fig, "why": why}
    ClaimLetter.objects.create(
        case=case, kind=ClaimLetter.Kind.AOL, figures=fig, context=ctx
    )
    case.stage = ClaimCase.Stage.AOL_PENDING
    case.save(update_fields=["stage", "updated_at"])
    lines = "".join(
        f'<tr><td style="padding:3px 12px 3px 0">{_esc(l["label"])}</td>'
        f'<td style="text-align:right">{_esc(l["amount"])}</td></tr>'
        for l in fig["lines"]
    )
    return _notify(
        case,
        handler,
        f"Agreement of Loss prepared for {case.claim_ref} — awaiting your authorisation",
        f"<p>{_esc(why)}. The Agreement of Loss is prepared and waiting for your authorisation "
        f"to send it to the client, or to decline it with the reason.</p>"
        f'<table style="font-size:14px">{lines}<tr><td><b>Net settlement</b></td>'
        f'<td style="text-align:right"><b>{_esc(fig["net_display"])}</b></td></tr></table>'
        f'<p style="color:#6B7280">These figures are for review. Nothing is paid from this screen.'
        f"{(' ' + _esc(fig['note'])) if fig.get('note') else ''}</p>",
        high=True,
    )


def draft_repudiation(case: ClaimCase, flags: list) -> str:
    if _open_letter(case, ClaimLetter.Kind.REPUDIATION):
        return "Repudiation already drafted — left as is"
    reasons = [_FLAG_REASON.get(fl["code"], fl.get("label", "")) for fl in flags]
    ctx = _letter_context(case) | {"reasons": reasons}
    ClaimLetter.objects.create(
        case=case, kind=ClaimLetter.Kind.REPUDIATION, reasons=reasons, context=ctx
    )
    case.stage = ClaimCase.Stage.REPUDIATION_PENDING
    case.save(update_fields=["stage", "updated_at"])
    items = "".join(
        f"<li><b>{_esc(fl.get('label'))}</b> — {_esc(fl.get('detail'))}</li>"
        for fl in flags
    )
    return _notify(
        case,
        _claims_manager(),
        f"Repudiation drafted for {case.claim_ref} — your decision",
        f"<p>The system found the following on this claim. A decline letter with these reasons is "
        f"drafted. Nothing has been sent and the claim has not been declined — approve the letter "
        f"to send it, or decline it and the claim carries on.</p><ul>{items}</ul>",
        high=True,
    )


# ── purchase orders ─────────────────────────────────────────────────────────


def draft_pos(case: ClaimCase, assessment: dict) -> str:
    from core.models import Company
    from procurement.claims_api import ClaimsAssessmentViewSet, _BgReq
    from procurement.claims_models import ClaimsAssessment
    from .assessment_report import build_report

    import hashlib
    import json

    ref = case.claim_ref
    aid = str(assessment.get("assessmentId") or "")[:64]
    report = build_report(assessment)
    if not report.get("groups"):
        return "Assessment had no usable lines — nothing drafted"
    # One draft per distinct CONTENT, decided under a row lock: MotoLink re-pushes
    # the same assessment with a new timestamp (same content -> nothing new), and a
    # REVISED assessment (different lines) must not be silently ignored.
    digest = hashlib.sha256(
        json.dumps([report.get("groups"), report.get("summary")], sort_keys=True, default=str).encode()
    ).hexdigest()
    with transaction.atomic():
        locked = ClaimCase.objects.select_for_update().get(pk=case.pk)
        done = list((locked.facts or {}).get("po_report_hashes") or [])
        if digest in done:
            return "Purchase orders for this assessment already drafted — left as is"
        revised = bool(done)
        locked.facts = dict(locked.facts or {}) | {"po_report_hashes": done + [digest]}
        locked.save(update_fields=["facts", "updated_at"])
    case.refresh_from_db()
    company = Company.objects.filter(code="ADIC").first()
    user = _system_user()
    f = case.facts or {}
    policy, insured = (f.get("policy") or {}), (f.get("insured") or {})
    excess_amount = None
    if not (report.get("summary") or {}).get("Excess"):
        excess_amount = _dec(policy.get("excess"))
    a = ClaimsAssessment.objects.create(
        company=company,
        report_json=report,
        claim_number=ref,
        assessment_number=aid or None,
        policy_number=policy.get("number") or None,
        claims_type=case.claim_type or None,
        client_name=insured.get("name") or None,
        registration=report.get("vehicle_reg") or None,
        vehicle=report.get("vehicle") or None,
        excess_amount=excess_amount,
        created_by=user,
        status=ClaimsAssessment.Status.READY_FOR_REVIEW,
    )
    crashed = ""
    try:
        ClaimsAssessmentViewSet().create_pos(_BgReq(user), _assessment=a)
    except Exception as exc:  # noqa: BLE001 — surfaced below, never reported as success
        log.warning("claims_automation create_pos failed for assessment %s: %s", a.pk, exc)
        crashed = str(exc)[:300]
    a.refresh_from_db()
    made = [r for r in (a.po_results or []) if r.get("id")]
    problems = [r.get("error") for r in (a.po_results or []) if r.get("error")]
    if crashed:
        problems.insert(0, f"Automatic drafting stopped: {crashed}")
    case.po_assessment = a
    if made:
        case.stage = ClaimCase.Stage.PO_DRAFTED
    case.save(update_fields=["po_assessment", "stage", "updated_at"])
    base = getattr(settings, "PUBLIC_BASE_URL", "https://omni.alphadirect.co.bw")
    link = f'<p><a href="{base}/claims-po/{a.pk}">Open the assessment and its draft POs</a></p>'
    if not made:
        body = (
            f"<p>The assessment for {_esc(ref)} arrived from the assessor, but the purchase "
            f"orders could NOT be drafted automatically. Please open the assessment and "
            f"prepare them on the Claims PO screen.</p>" + link
        )
        title = f"Purchase orders NOT drafted for {ref} — needs you"
    else:
        body = (
            f"<p>The assessment for {_esc(ref)} arrived from the assessor. "
            f"{len(made)} draft purchase order(s) were prepared from it. Please check them and "
            f"press the button to send them for approval.</p>" + link
        )
        title = f"Purchase orders drafted for {ref} — check and send"
    if revised:
        title = f"REVISED assessment for {ref} — " + (
            "new draft POs, cancel the older drafts" if made else "POs NOT drafted, needs you")
        body = ("<p><b>The assessor sent a revised assessment.</b> Please cancel any drafts "
                "made from the earlier version and work from this one.</p>" + body)
    if problems:
        body += (
            "<p><b>Needs you:</b></p><ul>"
            + "".join(f"<li>{_esc(p)}</li>" for p in problems)
            + "</ul>"
        )
    return _notify(case, _handler_user(case), title, body, high=not made)


# ── event handlers ──────────────────────────────────────────────────────────


def _on_registered(case, payload):
    return [f"Claim recorded (premium: {case.premium_light or 'not checked'})"]


_DOC_HOST_SUFFIXES = (".amazonaws.com", ".cloudfront.net")


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 — never follow
        return None


_NO_REDIRECT = urllib.request.build_opener(_RefuseRedirect)
_DOC_MAX_BYTES = 10 * 1024 * 1024


def read_documents(docs: list, unread: list | None = None) -> str:
    """Read the customer's uploaded documents (police report, photos) with Omni's
    OWN local OCR — nothing is sent to an outside service. Graphite passes
    short-lived signed links to its own file store; any other host is refused so
    this can never be pointed at an arbitrary address. Failures read as empty."""
    from urllib.parse import urlparse

    texts = []
    for d in (docs or [])[:5]:
        url = str((d or {}).get("url") or "")
        host = (urlparse(url).hostname or "").lower()
        if not url.startswith("https://") or not host.endswith(_DOC_HOST_SUFFIXES):
            log.warning("claims_automation: refused document host %s", host)
            if unread is not None:
                unread.append(str((d or {}).get("name") or "a document"))
            continue
        try:
            with _NO_REDIRECT.open(url, timeout=20) as r:  # host allow-listed above; redirects refused
                data = r.read(_DOC_MAX_BYTES + 1)
            if len(data) > _DOC_MAX_BYTES:
                if unread is not None:
                    unread.append(str(d.get("name") or "a document"))
                continue
            from core.doc_parse.cascade import parse

            res = parse(
                data, mime=str(d.get("mime") or ""), filename=str(d.get("name") or "")
            )
            if res.text:
                texts.append(res.text[:20000])
        except Exception as exc:  # noqa: BLE001 — one unreadable file never blocks the claim
            log.warning("claims_automation: could not read a document: %s", exc)
            if unread is not None:
                unread.append(str(d.get("name") or "a document"))
    return "\n".join(texts)


def _on_form(case, payload):
    from .ai_reader import read_claim, triage
    from .repudiation_screen import screen

    f = case.facts or {}
    claim, policy = (f.get("claim") or {}), (f.get("policy") or {})
    answers = payload.get("answers") or {}
    unread: list = []
    docs_text = "\n".join(
        filter(
            None,
            [
                payload.get("documents_text") or "",
                read_documents(payload.get("documents") or [], unread),
            ],
        )
    )
    flags = screen(
        {
            "date_of_loss": answers.get("loss_date") or claim.get("date_of_loss"),
            "reported_on": claim.get("reported_on"),
            "policy_inception": policy.get("inception"),
            "policy_expiry": policy.get("expiry"),
            "answers": answers,
            "documents_text": docs_text,
        }
    )
    missing = [
        k
        for k in (payload.get("required_keys") or [])
        if not str(answers.get(k) or "").strip()
    ]
    verdict, reasons = triage(case.premium_light, flags, missing)
    if unread:
        verdict = "exception"
        reasons.append("Could not read automatically, check by hand: " + ", ".join(unread[:5]))
    summary, step, engine = read_claim(f | {"answers": answers}, flags)
    case.flags, case.triage, case.triage_reasons = flags, verdict, reasons
    case.ai_summary, case.ai_next_step, case.ai_engine = summary, step, engine
    case.ai_at = timezone.now() if summary else case.ai_at
    case.stage = ClaimCase.Stage.FORM_IN
    case.save()
    actions = [
        f"Read: {verdict.replace('_', ' ')}"
        + (f" ({'; '.join(reasons)})" if reasons else "")
    ]
    if flags:
        actions.append(draft_repudiation(case, flags))
        return actions
    head = (
        "Straight through — no issues found."
        if verdict == "straight_through"
        else "Needs you: " + "; ".join(reasons)
    )
    actions.append(
        _notify(
            case,
            _handler_user(case),
            f"Customer form received for {case.claim_ref}",
            f"<p><b>{_esc(head)}</b></p>"
            + (f"<p>{_esc(summary)}</p>" if summary else "")
            + (f"<p><b>Suggested next step:</b> {_esc(step)}</p>" if step else "")
            + '<p style="color:#6B7280">The summary is written by our AI from the claim facts, with '
            "the customer's details removed. It is advice only.</p>",
        )
    )
    return actions


def _on_assessment(case, payload):
    a = payload.get("assessment") or {}
    case.stage = ClaimCase.Stage.ASSESSED
    case.save(update_fields=["stage", "updated_at"])
    if int(a.get("totalLoss") or 0) == 1:
        return [draft_aol(case, "The assessor recommends writing the vehicle off")]
    if a.get("lines"):
        return [draft_pos(case, a)]
    cost = _dec(a.get("finalCost"))
    return [
        _notify(
            case,
            _handler_user(case),
            f"Assessment received for {case.claim_ref}",
            "<p>The assessor's report arrived"
            + (f" (final cost P{cost:,.2f})" if cost else "")
            + " but without the itemised lines. Please upload the report PDF on the "
            "Claims PO screen to draft the purchase orders.</p>",
        )
    ]


def _on_write_off(case, payload):
    return [draft_aol(case, "The claim was flagged as a write-off in Graphite")]


def _on_decision(case, payload):
    d = payload.get("decision") or {}
    case.stage = ClaimCase.Stage.DECIDED
    case.save(update_fields=["stage", "updated_at"])
    return [f"Decision recorded in Graphite: {d.get('decision') or 'unknown'}"]


HANDLERS = {
    ClaimAutomationEvent.Type.CLAIM_REGISTERED: _on_registered,
    ClaimAutomationEvent.Type.FORM_SUBMITTED: _on_form,
    ClaimAutomationEvent.Type.ASSESSMENT_RECEIVED: _on_assessment,
    ClaimAutomationEvent.Type.WRITE_OFF_FLAGGED: _on_write_off,
    ClaimAutomationEvent.Type.DECISION_RECORDED: _on_decision,
}


def receive(
    payload: dict, *, received_via: str = ""
) -> tuple[ClaimAutomationEvent, bool]:
    """Store + process one Graphite event. Returns (event, created). A repeated
    idempotency key returns the original event untouched."""
    key = str(payload.get("idempotency_key") or "").strip()[:128]
    etype = str(payload.get("event_type") or "")
    ref = str(payload.get("claim_ref") or "").strip().upper()[:64]
    if not key or not ref or etype not in HANDLERS:
        raise ValueError(
            "idempotency_key, claim_ref and a known event_type are required"
        )
    existing = ClaimAutomationEvent.objects.filter(idempotency_key=key).first()
    stuck = (
        existing is not None
        and existing.status == ClaimAutomationEvent.Status.RECEIVED
        and existing.updated_at < timezone.now() - timezone.timedelta(minutes=10)
    )
    if existing and existing.status != ClaimAutomationEvent.Status.FAILED and not stuck:
        return existing, False
    if existing:  # a redelivery of a FAILED event is a retry, not a duplicate
        try:
            existing.actions = HANDLERS[existing.event_type](existing.case, payload)
            existing.status, existing.error = ClaimAutomationEvent.Status.PROCESSED, ""
        except Exception as exc:  # noqa: BLE001 — stays FAILED and visible
            log.exception("claims_automation retry %s failed", key)
            existing.error = str(exc)[:2000]
        existing.save(update_fields=["actions", "status", "error", "updated_at"])
        return existing, False

    with transaction.atomic():
        case, _ = ClaimCase.objects.select_for_update().get_or_create(claim_ref=ref)
        facts = payload.get("facts") or {}
        if payload.get("assessment"):
            facts = facts | {"assessment": payload["assessment"]}
        case.facts = _merge(case.facts, facts)
        claim = case.facts.get("claim") or {}
        case.claim_type = str(claim.get("type") or case.claim_type or "")[:64]
        if "is_motor" in payload:
            case.is_motor = bool(payload.get("is_motor"))
        if payload.get("graphite_id"):
            case.graphite_id = int(payload["graphite_id"])
        if payload.get("handler_email"):
            case.handler_email = str(payload["handler_email"])[:254]
        light = (case.facts.get("premium") or {}).get("light") or ""
        case.premium_light = str(light)[:10]
        case.save()
        event = ClaimAutomationEvent.objects.create(
            case=case,
            event_type=etype,
            idempotency_key=key,
            payload=payload,
            received_via=received_via[:120],
        )

    try:
        event.actions = HANDLERS[etype](case, payload)
        event.status = ClaimAutomationEvent.Status.PROCESSED
    except Exception as exc:  # noqa: BLE001 — stored, visible, retryable
        log.exception("claims_automation event %s failed", key)
        event.status, event.error = ClaimAutomationEvent.Status.FAILED, str(exc)[:2000]
    event.save(update_fields=["actions", "status", "error", "updated_at"])
    return event, True


# ── the human decisions on letters ──────────────────────────────────────────


def can_decide(user, letter: ClaimLetter) -> bool:
    from core.models import get_user_profile

    p = get_user_profile(user)
    if not (p and p.is_active):
        return False
    if letter.kind == ClaimLetter.Kind.REPUDIATION:
        return p.title == "claims_manager"
    return p.title in CLAIMS_SENIOR_TITLES


def wording_approved() -> bool:
    return bool(getattr(settings, "CLAIMS_LETTER_WORDING_APPROVED", False))


def render_context(letter: ClaimLetter) -> dict:
    return dict(letter.context or {}) | {"draft": not wording_approved()}


def approve(letter: ClaimLetter, user, override_reason: str = '') -> str:
    """Record the approval; send to the client only when the wording is signed
    off AND we hold an address. Otherwise say plainly why it was not sent."""
    from . import veritas
    from .letters import render_html, render_pdf

    # An Agreement of Loss hands the wreck to Veritas, so it may not be authorised
    # until the yard confirms they hold it (CFO 19-Sep-2026). A claims manager may
    # override in writing; the override is recorded and reported, never silent.
    if letter.kind == ClaimLetter.Kind.AOL:
        ok, why = veritas.possession_ok(letter.case)
        if not ok:
            if not (override_reason or '').strip():
                raise PossessionMissing(why)
            from core.models import get_user_profile
            p = get_user_profile(user)
            if not (p and p.is_active and p.title == 'claims_manager'):
                raise PermissionError('Only the Claims Manager may authorise without possession.')
            veritas.record_override(letter.case, user, override_reason)

    with transaction.atomic():
        return _approve_locked(
            ClaimLetter.objects.select_for_update().select_related("case").get(pk=letter.pk),
            user, render_html, render_pdf, letter,
        )


def _approve_locked(letter, user, render_html, render_pdf, original):
    if letter.status != ClaimLetter.Status.AWAITING:
        raise ValueError("This letter has already been decided.")

    letter.status = ClaimLetter.Status.APPROVED
    letter.decided_by, letter.decided_at = user, timezone.now()
    to = (letter.context or {}).get("insured_email") or ""
    if not wording_approved():
        outcome = (
            "Approved. Not sent: the letter wording is still awaiting the Claims "
            "Manager's sign-off. Download the letter and send it yourself."
        )
    elif not to:
        outcome = (
            "Approved. Not sent: no email address for the client. Download and post it."
        )
    else:
        from core.notifications import send_html_with_cfo_cc

        ctx = render_context(letter)
        title = (
            "Agreement of Loss" if letter.kind == ClaimLetter.Kind.AOL else "Your claim"
        )
        send_html_with_cfo_cc(
            f"{title} — {letter.case.claim_ref}",
            render_html(letter.kind, ctx),
            [to],
            attachments=[
                (
                    f"{letter.kind}-{letter.case.claim_ref}.pdf",
                    render_pdf(letter.kind, ctx),
                    "application/pdf",
                )
            ],
            cc_cfo=False,
            no_reply=False,
            bcc=[user.email] if user.email else None,
        )
        letter.status, letter.sent_to, letter.sent_at = (
            ClaimLetter.Status.SENT,
            to,
            timezone.now(),
        )
        outcome = f"Approved and sent to {to}."
    if letter.kind == ClaimLetter.Kind.REPUDIATION:
        outcome += " Please also record the repudiation on the claim in Graphite."
    letter.decision_note = outcome
    letter.save()
    original.refresh_from_db()
    if letter.kind == ClaimLetter.Kind.AOL:
        from . import veritas
        extra = veritas.maybe_raise(letter.case, user)
        if extra:
            outcome = f'{outcome} {extra}.'
            letter.decision_note = outcome
            letter.save(update_fields=['decision_note', 'updated_at'])
            original.refresh_from_db()
    return outcome


def decline(letter: ClaimLetter, user, note: str) -> str:
    with transaction.atomic():
        locked = ClaimLetter.objects.select_for_update().get(pk=letter.pk)
        out = _decline_locked(locked, user, note)
    letter.refresh_from_db()
    return out


def _decline_locked(letter: ClaimLetter, user, note: str) -> str:
    if letter.status != ClaimLetter.Status.AWAITING:
        raise ValueError("This letter has already been decided.")
    if not (note or "").strip():
        raise ValueError("Please give the reason.")
    letter.status = ClaimLetter.Status.DECLINED
    letter.decided_by, letter.decided_at = user, timezone.now()
    letter.decision_note = note.strip()[:2000]
    letter.save()
    return "Declined — nothing was sent." + (
        " The claim carries on." if letter.kind == ClaimLetter.Kind.REPUDIATION else ""
    )
