"""Omni magic-action links — no-login, one-click ACTIONS from staff emails.

Same trust model as core/ceo_monitor_views.py (escalate) and hris/leave_actions.py:
the signed token IS the gate. NO Omni login is needed and NO session is minted —
the link authorises ONE scoped action for ONE user, then re-checks that user's
normal permission before doing anything. GET is inert (defeats Safe-Links
prefetch); the action happens only on the POST from the Confirm button.

This is the "focused-page" magic link (CFO decision 2026-08-12). Approvals that
live here are one-click. A DECLINE always needs a typed reason, so a decline is
sent to the item's own page in Omni rather than being one-tapped away.

On money (CFO 2026-09-12): "money goes out when i go to fnb with duel factor
authroisation and approve only." An Omni approval authorises; it never moves a
pula. Each handler below re-checks the SAME service gate the in-app page calls,
so a link can never grant authority the person does not already have.

ONE NAMED EXCEPTION (CFO decision 2026-09-12): the CEO's authorisation of a large
claim payment — the `lp_*` actions registered by large_payments/magic.py. The CFO
was told plainly that anyone who can read the CEO's mailbox can press Approve for
72 hours, and answered "LETS SKIP THIS, EMAIL IS ENOUGH". The reason it is not the
money approval the rule above excludes: the CEO will not use Omni at all, the
payments have ALREADY reached FNB before he is asked, so his click releases
nothing, and every payment is still released by the CFO himself in the FNB app
with two-factor. If a future change ever makes his click actually move money,
this exception dies with it. Recorded here so this file and
large_payments/ceo_email.py do not contradict each other.

Add a new action by registering a handler in ACTIONS: describe(user, ctx) for the
confirm page and act(user, ctx) to perform it (must re-check the same permission
the in-app path enforces).
"""
import json
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseBadRequest
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt

User = get_user_model()

_SALT = "omni-magic-action-v1"
_MAX_AGE = 60 * 60 * 72  # 72h
_BASE = getattr(settings, "PUBLIC_BASE_URL", "https://omni.alphadirect.co.bw").rstrip("/")

NAVY = "#0D1B2A"
ORANGE = "#F4A623"


def make_action_link(user, kind, *, label="", **ctx):
    """Signed no-login link for `user` to perform `kind`. Falls back to a plain
    app link when the recipient is not an active internal user (external-safe)."""
    if not user or not getattr(user, "id", None) or not getattr(user, "is_active", False):
        return f"{_BASE}/dashboard"
    payload = {"u": user.id, "k": kind, "l": (label or "")[:80], "j": uuid.uuid4().hex}
    payload.update({k: v for k, v in ctx.items()})
    return f"{_BASE}/api/magic/{signing.dumps(payload, salt=_SALT)}/"


def _shell(inner, ok=True, wide=False):
    bar = NAVY if ok else "#C53030"
    return (
        '<!doctype html><html><head><meta charset="utf-8"><title>Omni</title>'
        '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
        '<body style="font-family:Arial,Helvetica,sans-serif;background:#EEF1F5;margin:0;padding:48px 16px;">'
        f'<div style="max-width:{620 if wide else 480}px;margin:0 auto;background:#fff;border-radius:10px;padding:30px;'
        f'border-top:6px solid {bar};box-shadow:0 2px 10px rgba(0,0,0,.06);">'
        f'<div style="font-family:Georgia,serif;font-size:22px;font-weight:bold;color:{NAVY};">Omni</div>'
        f'{inner}</div></body></html>')


def _p(text, color="#1F2A37", size="15px"):
    return f'<p style="color:{color};font-size:{size};line-height:1.55;margin-top:14px;">{text}</p>'


def _confirm(title, detail, token, button, pack_html_block=""):
    """The inert confirm page. `pack_html_block` is the full detail pack (lines,
    rates, deductions, sanity checks) so someone who taps an approve link from a
    phone sees WHAT they are signing before the button, not after — CFO
    2026-09-20. Empty string when the stream has no pack; the page is unchanged."""
    return _shell(
        _p(escape(title)) + (_p(escape(detail), "#475467", "13px") if detail else "") +
        (pack_html_block or "") +
        f'<form method="post" style="margin-top:20px;"><input type="hidden" name="t" value="{escape(token)}">'
        f'<button type="submit" style="background:{NAVY};color:#fff;border:none;font-size:15px;font-weight:bold;'
        f'padding:12px 26px;border-radius:7px;cursor:pointer;">{escape(button)}</button></form>',
        wide=bool(pack_html_block))


# ---------------------------------------------------------------------------
# Action handlers. Each: describe(user, ctx)->(title, detail, button);
# act(user, ctx)->(ok, message). act MUST re-check the in-app permission.
# ---------------------------------------------------------------------------

def _dd_row(ctx):
    from hris.models import DevelopmentDialogue
    return DevelopmentDialogue.objects.filter(ref=ctx.get("ref", ""), is_current=True).first()


def _dd_describe(user, ctx):
    row = _dd_row(ctx)
    who = (row.name if row and getattr(row, "name", "") else ctx.get("ref", "the dialogue"))
    role = ctx.get("role", "manager")
    verb = "your own review" if role == "employee" else f"{who}'s Development Dialogue"
    return (f"Sign off {verb}?", "A manager sign-off locks the period.", "Confirm & sign")


def _dd_act(user, ctx):
    from django.utils import timezone
    from hris.talent_cockpit_views import _scope
    row = _dd_row(ctx)
    if row is None:
        return False, "Dialogue not found — it may have moved to a new period."
    role = ctx.get("role", "manager")
    ref = ctx.get("ref", "")
    email = (getattr(user, "email", "") or "").strip().lower()
    # Re-check the SAME permission the in-app sign endpoint enforces.
    if role == "employee":
        if (row.email or "").lower() != email:
            return False, "You can only sign your own review."
    else:
        scope = _scope(user)
        if scope is None or (scope != "all" and ref not in scope):
            return False, "This dialogue is out of your scope."
    who = (getattr(user, "get_full_name", lambda: "")() or email or "user")
    payload = dict(row.payload or {})
    signoff = dict(payload.get("signoff") or {})
    signoff[role] = {"by": who, "at": timezone.now().isoformat(timespec="minutes")}
    payload["signoff"] = signoff
    row.payload = payload
    if role in ("manager", "moderator"):
        row.locked = True
    row.save(audit_user=user)
    try:
        from core import notifications
        if role == "employee":
            notifications.notify_dialogue_submitted(row, user)
        else:
            notifications.close_dialogue_review_tasks(row, signer=user)
    except Exception:
        pass
    return True, "Signed. Thank you — it is recorded on the dialogue."


def _ping_describe(user, ctx):
    return ("Magic-link self-test.",
            "Confirms the no-login link works. It performs NO action.",
            "Confirm test")


def _ping_act(user, ctx):
    who = (getattr(user, "get_full_name", lambda: "")() or user.get_username())
    return (True, f"It works. You opened this with no password as {who}. No action was performed.")


def _tc_task(ctx):
    from core.models import OmniTask
    return (OmniTask.objects.select_related("assignee")
            .filter(id=ctx.get("task_id")).first())


def _tc_describe(user, ctx):
    t = _tc_task(ctx)
    who = (t.assignee.get_full_name() or t.assignee.username) if (t and t.assignee_id) else ""
    title = t.title if t else "this task"
    detail = (f"Confirms {who} finished it. It then counts toward their Alpha League "
              f"score and monthly reward." if who else "")
    return (f"Confirm done: “{title}”?", detail, "Confirm done")


def _tc_act(user, ctx):
    """One-tap manager confirmation of a finished task (no login). Re-checks the
    SAME rule the dashboard enforces: only the manager who assigned it (or an
    admin) may confirm, and the confirmation is a non-assignee TaskFeedback — the
    exact row the anti-gaming reward counts. Idempotent."""
    from core.models import OmniTask, TaskFeedback
    t = _tc_task(ctx)
    if t is None:
        return False, "Task not found — it may have been removed."
    if not (user.is_superuser or t.assigner_id == user.id):
        return False, "Only the manager who assigned this task can confirm it."
    if t.status != OmniTask.Status.DONE:
        return False, "This task isn't marked done yet — ask them to finish it first."
    # Already manager-confirmed (a non-assignee left feedback)? Idempotent.
    if t.feedback.exclude(from_user_id=t.assignee_id).exists():
        return True, "Already confirmed — thank you. It counts toward their league."
    TaskFeedback.objects.create(task=t, from_user=user, to_user_id=t.assignee_id,
                                body="Confirmed done ✓ (one-tap)")
    who = (t.assignee.get_full_name() or t.assignee.username) if t.assignee_id else "them"
    return True, f"Confirmed. It now counts toward {who}'s Alpha League score + reward."


def _loan(ctx):
    from staff_loans.models import StaffLoanApplication
    return (StaffLoanApplication.objects.select_related("employee")
            .filter(id=ctx.get("loan_id")).first())


def _loan_describe(user, ctx):
    app = _loan(ctx)
    if app is None:
        return ("Staff loan not found.", "It may have been withdrawn.", "Close")
    who = getattr(getattr(app, "employee", None), "full_name", "") or "an employee"
    amount = f"BWP {app.amount_requested:,.2f}" if app.amount_requested is not None else ""
    term = f"over {app.term_months_requested} months" if app.term_months_requested else ""
    return (f"Approve this staff loan for {who}?",
            " · ".join(b for b in (amount, term, app.get_loan_type_display()) if b),
            "Approve loan")


def _loan_act(user, ctx):
    """One-tap CFO approval of a staff loan. staff_loans.services.cfo_decide is
    the SAME call the Omni page makes and it re-checks status, that the signer
    is the final approver, and segregation of duties."""
    from django.core.exceptions import ValidationError
    from staff_loans.models import StaffLoanApplication
    from staff_loans.services import cfo_decide
    app = _loan(ctx)
    if app is None:
        return False, "Staff loan not found — it may have been withdrawn."
    if app.status != StaffLoanApplication.Status.PENDING_CFO:
        return True, f"Already {app.get_status_display().lower()} — no change made."
    try:
        cfo_decide(app, user, approve=True, notes="Approved from the email link.")
    except ValidationError as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        return False, msgs[0]
    return True, "Approved. The employee has been told and HR can proceed."


def _comm(ctx):
    from commissions.models import CommissionSubmission
    return (CommissionSubmission.objects.select_related("agent")
            .filter(id=ctx.get("sub_id")).first())


def _comm_describe(user, ctx):
    sub = _comm(ctx)
    if sub is None:
        return ("Commission submission not found.", "", "Close")
    agent = getattr(getattr(sub, "agent", None), "name", "") or "an agent"
    gross = f"BWP {sub.gross_commission:,.2f}" if sub.gross_commission is not None else ""
    return (f"Approve this commission for {agent}?",
            " · ".join(b for b in (sub.period_label or "", gross,
                                   sub.get_status_display()) if b),
            "Approve commission")


def _comm_act(user, ctx):
    """One-tap review of a commission submission. commissions.service.review is
    the SAME call the Omni page makes: it re-checks the stage, that this user
    reviews that stage, and the maker-checker / pays-you rules."""
    from commissions import service as _csvc
    from commissions.models import CommissionSubmission
    sub = _comm(ctx)
    if sub is None:
        return False, "Commission submission not found."
    if sub.status in (CommissionSubmission.Status.APPROVED,
                      CommissionSubmission.Status.REJECTED,
                      CommissionSubmission.Status.PAID):
        return True, f"Already {sub.get_status_display().lower()} — no change made."
    try:
        _csvc.review(sub, user, approve=True)
    except ValueError as exc:
        return False, str(exc)
    return True, f"Done — it is now {sub.get_status_display().lower()}."


ACTIONS = {
    "dd_sign": {"describe": _dd_describe, "act": _dd_act},
    "ping": {"describe": _ping_describe, "act": _ping_act},
    "task_confirm": {"describe": _tc_describe, "act": _tc_act},
    # "pack": ctx -> (stream, id) for core.approval_pack. An action without one
    # keeps the plain confirm page it has always had.
    "loan_approve": {"describe": _loan_describe, "act": _loan_act,
                     "pack": lambda ctx: ("staff_loans", ctx.get("loan_id"))},
    "commission_approve": {"describe": _comm_describe, "act": _comm_act,
                           "pack": lambda ctx: ("commissions", ctx.get("sub_id"))},
}


def _pack_block(handler, ctx) -> str:
    """Rendered detail pack for this action, or '' — never raises. A pack is
    context on top of an approval that already works; a failure here must leave
    the confirm page working, not blank it."""
    try:
        resolve = handler.get("pack")
        if resolve is None:
            return ""
        stream, pk = resolve(ctx)
        from core.approval_pack import build_pack, pack_html
        return pack_html(build_pack(stream, pk))
    except Exception:  # noqa: BLE001
        return ""


@csrf_exempt
def magic_action(request, token):
    token = request.POST.get("t") or token
    try:
        data = signing.loads(token, salt=_SALT, max_age=_MAX_AGE)
    except signing.SignatureExpired:
        return HttpResponseBadRequest(_shell(_p("This link has expired — open the latest email."), ok=False))
    except signing.BadSignature:
        return HttpResponseBadRequest(_shell(_p("This link is invalid."), ok=False))

    user = User.objects.filter(id=data.get("u"), is_active=True).first()
    if user is None:
        return HttpResponseBadRequest(_shell(_p("Account not found or inactive."), ok=False))

    handler = ACTIONS.get(data.get("k"))
    if handler is None:
        return HttpResponseBadRequest(_shell(_p("Unknown action."), ok=False))

    # GET = inert confirm page (Safe-Links safe): NO side effect.
    if request.method != "POST":
        title, detail, button = handler["describe"](user, data)
        return HttpResponse(_confirm(title, detail, token, button,
                                     _pack_block(handler, data)))

    # POST = the human clicked Confirm. Single-use, then act (with its own re-check).
    jti = data.get("j") or ""
    if jti:
        ck = f"magic_act_used:{jti}"
        if cache.get(ck):
            return HttpResponse(_shell(_p("This link was already used."), ok=False))
        cache.set(ck, 1, _MAX_AGE)

    ok, message = handler["act"](user, data)
    return HttpResponse(_shell(_p(escape(message), "#1B7A3B" if ok else "#C53030"), ok=ok))
