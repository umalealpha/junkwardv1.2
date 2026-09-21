"""
python manage.py omni_do --actor cfo --action task_close --id <task-uuid> --note "reviewed, approved all"
python manage.py omni_do --actor cfo --action po_approve  --id <po-uuid>   --note "confirmed with supplier"
python manage.py omni_do --actor cfo --action it_request  --subject "Laptop won't boot" --body "..." --priority high

Runs an Omni action AS a real user, from the backend, with no browser — the
CFO's 2026-08-12 ask (docs/plans/cfo-backend-actions-plan.md). Every action
calls the SAME service function the in-app button calls
(taskboard.services.complete_task / procurement.services.cfo_approve), so
every guard the UI enforces (segregation of duties, duplicate-payment gate,
status checks) still applies — this is not a raw ORM write and cannot bypass
them. See core/backend_actions.py for the registry.

it_request puts the request on omni's own IT queue (core/it_queue.py) — the
real Help Desk lives in an external SharePoint list omni can only READ, so an
omni-native queue is the honest option. IT sees it in /tasks, which is where
CFO directive 2026-06-29 already put Help Desk work.

HARD RAILS (do not soften): no access/permission changes, no credential entry,
no data deletion here — those need a human. Money only ever leaves via the
FNB app; task_close refuses payment_request tasks outright (see
core/backend_actions.py).

Every SUCCESSFUL run writes one AuditLog row (who, action, target, result) —
same trail as a UI action, plus a "via omni_do" marker so a backend action is
never invisible. A refusal (bad --id, guard tripped, ambiguous actor) writes
none — the SSM session log is the trail for those.

--actor resolves to ONE specific, named Omni user — never picked by title
alone, which can match more than one account. Discovered live 2026-08-15: two
active users held UserProfile.Title.CFO (pganesharajah, excoboard); the first
version of this refused rather than guess (Fable 5's fix). The CFO named
pganesharajah, 2026-08-17, as the identity this tool acts as. The title is
still re-verified below on every run — if that person's CFO title is ever
changed or removed, the tool refuses rather than act with stale authority.

Run via SSM on EC2 (af-south-1, i-02a5d76a61f4f09a5).
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from core.backend_actions import ACTIONS_WITH_TARGET_ID, BACKEND_ACTIONS
from core.models import AuditLog, UserProfile, get_user_profile

User = get_user_model()

# --actor resolves to a specific named user, never picked by title/role alone
# (see docstring). "cfo" is the only actor wired today; add more here as the
# CFO needs them — same lookup, no new plumbing.
_ACTORS = {
    "cfo": {"username": "pganesharajah", "title": UserProfile.Title.CFO},
}


class Command(BaseCommand):
    help = ("Run an Omni action from the backend as a real user, no Chrome. "
            "See docs/plans/cfo-backend-actions-plan.md.")

    def add_arguments(self, parser):
        parser.add_argument("--actor", required=True, choices=sorted(_ACTORS))
        parser.add_argument("--action", required=True, choices=sorted(BACKEND_ACTIONS))
        # Not required at the parser: it_request creates its target rather than
        # naming one. Each handler validates the arguments it actually needs, so
        # a missing --id still fails loudly with the action named.
        parser.add_argument("--id", default="", help="Target task or PO id (UUID pk)")
        parser.add_argument("--note", default="", help="Completion note / approval note")
        parser.add_argument("--subject", default="", help="it_request: what is broken")
        parser.add_argument("--body", default="", help="it_request: the detail")
        parser.add_argument("--priority", default="normal",
                            help="it_request: low | normal | high | urgent")

    def handle(self, *args, **opts):
        spec = _ACTORS[opts["actor"]]
        actor = User.objects.filter(username=spec["username"], is_active=True).first()
        if actor is None:
            raise CommandError(
                f"--actor {opts['actor']} (username={spec['username']!r}) not found or inactive."
            )
        profile = get_user_profile(actor)
        if profile is None or profile.title != spec["title"]:
            raise CommandError(
                f"--actor {opts['actor']} resolved to {actor.username!r}, but their title is "
                f"{getattr(profile, 'title', None)!r}, not {spec['title']!r} — refusing rather "
                f"than act with authority that may no longer be current."
            )

        action = opts["action"]
        fn = BACKEND_ACTIONS[action]
        try:
            result = fn(actor, id=opts["id"], note=opts["note"],
                        subject=opts["subject"], body=opts["body"],
                        priority=opts["priority"])
        except ValidationError as exc:
            raise CommandError(f"FAILED: {'; '.join(exc.messages)}")

        # it_request creates its target, so it has no caller-supplied id to
        # stamp — record the subject instead of an empty string.
        target = opts["id"] if action in ACTIONS_WITH_TARGET_ID else opts["subject"]
        AuditLog.objects.create(
            table_name="OmniBackendAction",
            record_id=str(target)[:255],
            action=(AuditLog.Action.APPROVE if action == "po_approve"
                    else AuditLog.Action.CREATE if action == "it_request"
                    else AuditLog.Action.UPDATE),
            new_values={"action": action, "note": opts["note"],
                        "subject": opts["subject"], "priority": opts["priority"]},
            user=actor,
            description=f"omni_do: {action} #{target} — via claude-backend, no Chrome. {result}",
        )
        self.stdout.write(self.style.SUCCESS(f"OK: {result}"))
