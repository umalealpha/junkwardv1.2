"""CEO Monitor — one-click escalation endpoint (stateless, no new tables).

Follows omni's emailed-link pattern (see hris.leave_explain / hris.leave_actions):
the SIGNED token IS the gate — no login, because omni's user session is a
front-end token, not a Django session, so a login_required page would just bounce
the CEO. The brief that carries these links is EYES-ONLY (CEO + CFO), and the
token is unforgeable (django.core.signing) and short-lived.

SAFETY — GET confirms, POST acts. Opening the link (GET) only renders a
"Escalate to X? [Confirm]" page and has NO side effect. The OmniTask is created
only on the POST from that Confirm button. This is deliberate: Microsoft 365
mail scanners (Defender SafeLinks etc.) pre-fetch links in delivered mail with
GET, and a side-effect-on-GET would let a scanner fire every escalate button in
a brief. Mutation lives under POST, matching hris.leave_actions.

On confirm we create an OmniTask assigned to the chosen exec — which fans out to
their task board AND emails them. Idempotent per (assignee, source).
"""

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt

from core.models import OmniTask, OmniTaskComment

User = get_user_model()

_SALT = "ceo-monitor-escalate"
_MAX_AGE = 60 * 60 * 24 * 14  # 14 days
_CEO_HANDLE = "aiyer"  # default brief owner — recorded as the assigner

#: Whose brief a signed link may act on behalf of, and how to name them in the
#: audit trail. The CFO brief is the CEO driver cloned, and every link it
#: rendered resolved the actor from _CEO_HANDLE — so the CFO pressing Approve in
#: his OWN brief was recorded as "Approved by the CEO", and a request for detail
#: went out assigned by Arun. The actor now travels inside the signed token.
#: Signed means it cannot be forged; the allowlist means a stale or hand-made
#: token can still only ever name a real brief owner.
BRIEF_OWNERS = {
    "aiyer": "the CEO",
    "pganesharajah": "the CFO",
}


def _owner(handle):
    """(handle, label) for the person whose brief this link came from."""
    h = (handle or "").strip().lower()
    if h in BRIEF_OWNERS:
        return h, BRIEF_OWNERS[h]
    return _CEO_HANDLE, BRIEF_OWNERS[_CEO_HANDLE]

_TARGETS = {
    "pbeka": "Paul Beka",
    "arjuniyer": "Arjun Iyer",
    "ubutale": "Unami Butale",
    "wmoses": "Wangu Moses",
    "bbalasubramanian": "Bharath Balasubramanian",
    "ktshutlhedi": "Kago Tshutlhedi",
    "gmachobane": "Gaolebale Machobane",
    "pganesharajah": "Prathap Ganesharajah",
}


def make_escalation_token(*, to, party, ref, matter, why, actor=None) -> str:
    """`actor` is the brief owner the link acts for; omitted means the CEO, so
    existing CEO-brief links keep working unchanged."""
    return signing.dumps(
        {"to": to, "party": party, "ref": ref, "matter": matter, "why": why,
         "actor": actor or _CEO_HANDLE},
        salt=_SALT)


def _resolve(handle: str):
    return (User.objects.filter(email__iexact=f"{handle}@alphadirect.co.bw",
                                is_active=True).first()
            or User.objects.filter(username__iexact=handle, is_active=True).first())


def _shell(inner: str, bar: str = "#0D1B2A") -> str:
    return (
        '<!doctype html><html><head><meta charset="utf-8"><title>CEO Monitor</title>'
        '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
        '<body style="font-family:Arial,Helvetica,sans-serif;background:#EEF1F5;padding:48px 16px;">'
        '<div style="max-width:560px;margin:0 auto;background:#fff;border-radius:9px;'
        f'padding:30px;border-top:6px solid {bar};box-shadow:0 2px 10px rgba(0,0,0,.06);">'
        '<div style="font-family:Georgia,serif;font-size:22px;font-weight:bold;color:#0D1B2A;">'
        f'CEO Monitor</div>{inner}</div></body></html>'
    )


def _msg_page(msg: str, party: str, ok: bool = True) -> str:
    bar = "#1B7A3B" if ok else "#C53030"
    return _shell(
        f'<p style="font-size:15px;color:#1F2A37;line-height:1.5;margin-top:14px;">{escape(msg)}</p>'
        f'<p style="font-size:12px;color:#98A2B3;margin-top:18px;">{escape(party)}</p>',
        bar)


def _confirm_page(name: str, party: str, token: str) -> str:
    # GET landing — no side effect. The button POSTs the token back to act.
    return _shell(
        f'<p style="font-size:15px;color:#1F2A37;line-height:1.5;margin-top:14px;">'
        f'Escalate this matter to <strong>{escape(name)}</strong>? '
        'This creates a task for them and emails them.</p>'
        f'<p style="font-size:13px;color:#475467;margin:6px 0 18px;">{escape(party)}</p>'
        '<form method="post" action="/api/ceo-monitor/escalate/">'
        f'<input type="hidden" name="t" value="{escape(token)}">'
        '<button type="submit" style="background:#0D1B2A;color:#fff;border:none;'
        'font-family:Arial,Helvetica,sans-serif;font-size:14px;font-weight:bold;'
        'padding:11px 22px;border-radius:6px;cursor:pointer;">'
        f'Confirm &mdash; escalate to {escape(name)}</button></form>')


@csrf_exempt
def ceo_monitor_escalate(request):
    # Token may arrive on the GET link or the POST confirm form.
    token = request.POST.get("t") or request.GET.get("t", "")
    try:
        data = signing.loads(token, salt=_SALT, max_age=_MAX_AGE)
    except signing.SignatureExpired:
        return HttpResponseBadRequest("This escalation link has expired — open the latest brief.")
    except signing.BadSignature:
        return HttpResponseBadRequest("Invalid escalation link.")

    handle = (data.get("to") or "").strip()
    name = _TARGETS.get(handle)
    if not name:
        return HttpResponseBadRequest("Unknown escalation recipient.")

    assignee = _resolve(handle)
    if assignee is None:
        return HttpResponse(_msg_page(f"No active Omni user found for {name}.", "", ok=False),
                            status=400)

    party = (data.get("party") or data.get("ref") or "the matter").strip()

    # GET = confirm page only, NO side effect (safe against mail-scanner pre-fetch).
    if request.method != "POST":
        return HttpResponse(_confirm_page(name, party, token))

    # POST = the human clicked Confirm. Create the task, in the name of the
    # person whose brief carried the link — never a hardcoded principal.
    actor_handle, actor_label = _owner(data.get("actor"))
    assigner = _resolve(actor_handle)
    if assigner is None:
        # Fail loud rather than forging the assigner as the target exec.
        return HttpResponse(
            _msg_page(f"Account for {actor_label} not found — task NOT created. "
                      "Please contact IT.", party, ok=False), status=400)

    ref = (data.get("ref") or "").strip()
    # Scope the idempotency key to the actor: the CEO and the CFO escalating the
    # same matter to the same person are two different instructions.
    source = ("ceomon:" + actor_handle[:4] + ":" + (ref or party))[:30]
    title = f"Escalated by {actor_label}: {party}"[:200]
    body = "\n".join([
        f"{actor_label.capitalize()} has escalated this matter to you.",
        "",
        f"Matter: {data.get('matter', '')}",
        f"Why it matters: {data.get('why', '')}",
        f"Reference: {ref or '(none)'}",
    ])

    if OmniTask.objects.filter(assignee=assignee, source=source).exists():
        return HttpResponse(_msg_page(
            f"Already escalated to {name} — a task is already on their list.", party))

    with transaction.atomic():
        OmniTask.objects.create(
            assigner=assigner, assignee=assignee, title=title, body=body,
            priority=OmniTask.Priority.HIGH, status=OmniTask.Status.PENDING,
            source=source)

    # Creating the OmniTask does NOT itself email the assignee on this path, so
    # send it explicitly — that is the whole point of an escalation.
    emailed = _email_assignee(assignee, title, body)

    tail = "they have been emailed." if emailed else ("the task is on their board " +
           "(email notification could not be sent — check with IT).")
    return HttpResponse(_msg_page(
        f"Escalated to {name}. A task has been created for them and {tail}", party))


def _email_assignee(assignee, title, body) -> bool:
    """Email the exec their escalation. Never let a mail error undo the task."""
    to = getattr(assignee, "email", "") or ""
    if not to:
        return False
    try:
        from django.conf import settings
        from django.core.mail import EmailMultiAlternatives
        frm = getattr(settings, "DEFAULT_FROM_EMAIL", "omni@alphadirect.co.bw")
        html = (
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#0D1B2A;">'
            '<p>The CEO has escalated a matter to you via the CEO Monitor.</p>'
            f'<pre style="font-family:Arial,Helvetica,sans-serif;white-space:pre-wrap;font-size:14px;">{escape(body)}</pre>'
            '<p>It is also on your Omni task board.</p></div>'
        )
        msg = EmailMultiAlternatives(subject=title, body=body, from_email=frm, to=[to])
        msg.attach_alternative(html, "text/html")
        msg.send()
        return True
    except Exception:
        import logging
        logging.getLogger(__name__).exception("ceo_monitor: escalation email failed")
        return False


# ---------------------------------------------------------------------------
# One-tap decisions on what is waiting on the CEO (CFO 2026-09-10).
#
# The brief lists the tasks sitting with the CEO but he could only ACT on them
# by opening Omni, so they aged. Each non-financial waiting item now carries
# Approve / Decline / Need detail, on the same signed-token + GET-confirms +
# POST-acts pattern as the escalation buttons above.
#
# PAYMENT TASKS USED TO BE EXCLUDED. They are not any more - CFO 2026-09-12:
# "dont talk shit about money decision etc and implement in email links as
# well, i have told u many times money goes out when i go to fnb with duel
# factor authroisation and approve only."
#
# He is right, and the code says so: the in-app CFO authorisation
# (taskboard.payment_bulk_views.payment_bulk_approve) calls exactly
# `services.complete_task(task, user, NOTE, 0)` - the same call this decide
# endpoint makes. PAY-DUP-01, the per-line holds and the audit record all live
# INSIDE complete_task, so they fire identically whichever button starts it.
# The email link is signed, single-use, GET-inert, and re-checks the same
# permission. Omni never moves money: the money leaves at FNB, under the bank's
# own two-factor, in a separate act.
#
# The task still has to be genuinely actionable - is_decidable_task() keeps
# failing CLOSED on anything that is not an OmniTask, is already finished, or
# whose shape cannot be read.
# ---------------------------------------------------------------------------

_DECIDE_SALT = "ceo-monitor-decide"

#: Task sources that must be decided in Omni, never from the email - for
#: everyone EXCEPT the actor(s) listed in MAY_DECIDE_MONEY_BY_EMAIL below.
FINANCIAL_TASK_SOURCES = {"payment_request", "petty_cash", "refund_request"}

#: Brief owners who may authorise a payment straight from their email. The CFO
#: only, on his own instruction (2026-09-12) and because he already does the
#: identical thing in Omni. The CEO's brief is deliberately NOT changed: he
#: never asked for it, and large-payment authorisation already reaches him by
#: its own purpose-built email (large_payments/ceo_email.py).
MAY_DECIDE_MONEY_BY_EMAIL = {"pganesharajah"}

DECISION_LABELS = (
    ("approve", "Approve"),
    ("decline", "Decline"),
    ("detail", "Need detail"),
)


def is_decidable_task(task, actor=None) -> bool:
    """True only when a task is safe to decide from the email.

    `actor` is the brief owner the link would act for. Omitted means the CEO,
    who keeps the original rule: no money decisions from an email. An actor in
    MAY_DECIDE_MONEY_BY_EMAIL may decide payment tasks too (see above).

    Fails closed: anything that is not an OmniTask, anything already finished,
    and anything whose shape we cannot read stays in Omni.

    NOTE on `payment_request`: taskboard.PaymentRequest points at OmniTask with
    a ForeignKey whose related_name is 'payment_request', so the reverse
    accessor is a MANAGER, not an instance — and a manager is always truthy.
    Testing it for truth marked EVERY task financial and killed all the
    buttons. It has to be .exists().
    """
    if not isinstance(task, OmniTask):
        return False
    money_ok = (actor or _CEO_HANDLE) in MAY_DECIDE_MONEY_BY_EMAIL
    if not money_ok and (getattr(task, "source", "") or "") in FINANCIAL_TASK_SOURCES:
        return False
    if task.status in (OmniTask.Status.DONE, OmniTask.Status.CANCELLED):
        return False
    try:
        rel = task.payment_request
    except AttributeError:      # no such relation on this image
        return True
    except Exception:           # noqa: BLE001 - unreadable => not decidable
        return False
    try:
        linked = rel.exists() if hasattr(rel, "exists") else bool(rel)
    except Exception:           # noqa: BLE001 - unreadable => not decidable
        return False
    # For an actor allowed to decide money by email, a linked payment request
    # no longer disqualifies the task: complete_task applies PAY-DUP-01, the
    # per-line holds and the audit record identically either way.
    return money_ok or not linked


def _detail_source(task, actor=None) -> str:
    """Idempotency key for the "needs detail" follow-up task.

    OmniTask.source is max_length=30 and a UUID pk is 36 chars, so the obvious
    "ceodetail:<pk>"[:30] silently truncated the id. 'ceodetail:' (10) + 20 hex
    chars fits exactly and stays deterministic, so asking twice still resolves
    to the same task.
    """
    return "bnd:" + (actor or _CEO_HANDLE)[:4] + ":" + task.pk.hex[:20]


def make_decision_token(*, task_id, action, actor=None) -> str:
    """`actor` is the brief owner the link acts for; omitted means the CEO."""
    return signing.dumps({"task": str(task_id), "action": action,
                          "actor": actor or _CEO_HANDLE},
                         salt=_DECIDE_SALT)


def _decide_confirm_page(verb, title, token) -> str:
    return _shell(
        f'<p style="font-size:15px;color:#1F2A37;line-height:1.5;margin-top:14px;">'
        f'<strong>{escape(verb)}</strong> this item?</p>'
        f'<p style="font-size:13px;color:#475467;margin:6px 0 18px;">{escape(title)}</p>'
        '<form method="post" action="/api/ceo-monitor/decide/">'
        f'<input type="hidden" name="t" value="{escape(token)}">'
        '<button type="submit" style="background:#0D1B2A;color:#fff;border:none;'
        'font-family:Arial,Helvetica,sans-serif;font-size:14px;font-weight:bold;'
        'padding:11px 22px;border-radius:6px;cursor:pointer;">'
        f'Confirm &mdash; {escape(verb.lower())}</button></form>')


@csrf_exempt
def ceo_monitor_decide(request):
    """Approve / decline / ask for detail on a task waiting on the CEO.

    GET renders a confirm page and has NO side effect (mail scanners pre-fetch
    links with GET); the POST from that button acts.
    """
    token = request.POST.get("t") or request.GET.get("t", "")
    try:
        data = signing.loads(token, salt=_DECIDE_SALT, max_age=_MAX_AGE)
    except signing.SignatureExpired:
        return HttpResponseBadRequest("This link has expired — open the latest brief.")
    except signing.BadSignature:
        return HttpResponseBadRequest("Invalid link.")

    action = (data.get("action") or "").strip()
    verbs = dict(DECISION_LABELS)
    if action not in verbs:
        return HttpResponseBadRequest("Unknown decision.")

    task = OmniTask.objects.filter(pk=data.get("task")).first()
    if task is None:
        return HttpResponse(_msg_page("That task no longer exists.", "", ok=False),
                            status=400)

    # The person whose brief carried this link — never a hardcoded principal.
    actor_handle, actor_label = _owner(data.get("actor"))
    ceo = _resolve(actor_handle)
    if ceo is None:
        return HttpResponse(_msg_page(
            f"Account for {actor_label} not found — nothing was changed. "
            "Please contact IT.", task.title, ok=False), status=400)

    # actor_handle, NOT the default. The brief RENDERS the buttons with
    # is_decidable_task(task, actor=ACTOR); if the click re-checks without the
    # actor it falls back to the CEO handle, which is not in
    # MAY_DECIDE_MONEY_BY_EMAIL — so the CFO saw Approve on his own payment
    # task, tapped it, and got refused. Fails closed, so nothing unsafe
    # happened; the feature was simply dead. (/fabe 2026-09-13.)
    if not is_decidable_task(task, actor=actor_handle):
        return HttpResponse(_msg_page(
            "This one has to be done in Omni — it is a financial authorisation "
            "(or it is already closed), and those keep their own checks and "
            "completion note. Nothing was changed.",
            task.title, ok=False), status=400)

    if request.method != "POST":
        return HttpResponse(_decide_confirm_page(verbs[action], task.title, token))

    stamp = timezone.localdate().strftime("%d %B %Y")

    if action == "approve":
        # Go through the real completion gate so the completion note, the
        # notification acknowledgement and the audit row all still happen.
        from taskboard.services import complete_task
        note = (f"Approved by {actor_label} from the daily Omni brief on {stamp}. "
                f"Recorded by a one-tap decision in that brief.")
        try:
            complete_task(task, ceo, note, 0)
        except Exception as ex:  # noqa: BLE001 - surface the real reason
            from django.core.exceptions import ValidationError
            reason = (ex.messages[0] if isinstance(ex, ValidationError)
                      and getattr(ex, "messages", None) else str(ex))
            return HttpResponse(_msg_page(
                f"Not approved — Omni refused it: {reason}", task.title, ok=False),
                status=400)
        return HttpResponse(_msg_page(
            "Approved and closed. It is off your list.", task.title))

    if action == "decline":
        with transaction.atomic():
            task.status = OmniTask.Status.CANCELLED
            if not task.completed_at:
                task.completed_at = timezone.now()
            task.save(update_fields=["status", "completed_at", "updated_at"])
            OmniTaskComment.objects.create(
                task=task, author=ceo,
                body=(f"Declined by {actor_label} from the daily Omni brief "
                      f"on {stamp}."),
                new_status=OmniTask.Status.CANCELLED)
        _notify_assigner(task, f"declined by {actor_label}",
                         f"{actor_label.capitalize()} has declined this. "
                         "It is closed in Omni.")
        return HttpResponse(_msg_page(
            "Declined and closed. The person who raised it has been told.",
            task.title))

    # detail — leave the task open, put the question back to whoever raised it.
    source = _detail_source(task, actor_handle)
    if OmniTask.objects.filter(assignee=task.assigner, source=source).exists():
        return HttpResponse(_msg_page(
            "You have already asked for detail on this one.", task.title))
    with transaction.atomic():
        OmniTask.objects.create(
            assigner=ceo, assignee=task.assigner,
            title=f"{actor_label.capitalize()} needs more detail: {task.title}"[:200],
            body=(f"{actor_label.capitalize()} has asked for more detail before "
                  "deciding.\n\n"
                  f"Original item: {task.title}\n"
                  f"Asked on: {stamp}\n\n"
                  "Please add the detail to the original task in Omni."),
            priority=OmniTask.Priority.HIGH,
            status=OmniTask.Status.PENDING, source=source)
    _notify_assigner(task, f"{actor_label} needs more detail",
                     f"{actor_label.capitalize()} has asked for more detail "
                     "before deciding. A task has been created for you.")
    return HttpResponse(_msg_page(
        "Asked for more detail. It stays on your list until they come back.",
        task.title))


def _notify_assigner(task, subject_tail, line) -> bool:
    """Tell whoever raised the item what the CEO decided. Never let a mail
    failure undo a decision that is already recorded."""
    to = getattr(task.assigner, "email", "") or ""
    if not to:
        return False
    try:
        from django.conf import settings
        from django.core.mail import EmailMultiAlternatives
        frm = getattr(settings, "DEFAULT_FROM_EMAIL", "omni@alphadirect.co.bw")
        html = ('<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
                'color:#0D1B2A;"><p>' + escape(line) + '</p>'
                '<p style="color:#475467;">' + escape(task.title) + '</p></div>')
        msg = EmailMultiAlternatives(
            subject=f"{task.title} — {subject_tail}"[:200],
            body=f"{line}\n\n{task.title}", from_email=frm, to=[to])
        msg.attach_alternative(html, "text/html")
        msg.send()
        return True
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("ceo_monitor: decision email failed")
        return False
