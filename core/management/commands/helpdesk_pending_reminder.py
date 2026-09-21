"""
python manage.py helpdesk_pending_reminder [--dry-run]

Twice-daily (cron 08:00 + 16:00 SAST) reminder for the IT Help Desk.

Reads the SharePoint 'IT Tickets' list via the omni Graph app. If any tickets
are still pending (Status in Open / In Progress / On Hold) it:
  * emails the Help Desk owners (Kelebogile Molefe + Sechele), and
  * upserts an OmniTask for each owner so it also shows in omni /tasks
    ("link this section to our tasks" — CFO directive 2026-06-29).

Idempotent: one open OmniTask per owner, updated each run and auto-closed when
nothing is pending. Sends NOTHING when there are no pending tickets.

REQUIRES: the omni Graph app must have READ access to the ITHelpDesk SharePoint
site (Sites.Selected 'read' on that site, or Sites.Read.All). Until that grant
is in place the list read returns empty and the command logs a clear notice and
exits without sending — it never crashes or spams.
"""
from __future__ import annotations

import logging
from html import escape

import requests
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.it_queue import IT_OWNERS
from licensing.services.graph import _token, GraphError

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
SITE = "alphadirectbw.sharepoint.com:/sites/ITHelpDesk"
HELPDESK_URL = "https://omni.alphadirect.co.bw/helpdesk/"
# Single-sourced in core/it_queue.py so this reminder and the omni-native IT
# queue can never disagree about who IT is.
OWNERS = IT_OWNERS
PENDING_STATUSES = {"Open", "In Progress", "On Hold"}
TASK_TITLE = "IT Help Desk — tickets pending"
# The real ticket list is named exactly "IT Tickets". The site ALSO carries an
# empty stock "Tickets" issue-tracking list, which sorts first — so the old
# fuzzy `"ticket" in displayName` pick silently matched the empty one and the
# reminder reported "Pending tickets: 0" for every run while real tickets sat
# On Hold (6 of them on 2026-08-03). Match the exact name first.
LIST_NAME = "IT Tickets"
_PRI_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
_OMNI_PRI = ["urgent", "high", "normal", "low"]


def _get(url: str, token: str) -> dict:
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def pick_list_id(lists):
    """The id of the REAL ticket list out of the site's lists.

    Exact "IT Tickets" wins. Otherwise any list whose name contains "ticket"
    EXCEPT the stock, empty "Tickets" issue-tracking list — matching that one is
    the bug this guards (it sorts first, so the old fuzzy pick always chose it
    and the reminder read an empty list). Returns None when nothing matches.
    """
    def name(l):
        return (l.get("displayName") or "").strip().lower()

    for l in lists:
        if name(l) == LIST_NAME.lower():
            return l["id"]
    for l in lists:
        if "ticket" in name(l) and name(l) != "tickets":
            return l["id"]
    return None


def _fetch_pending(token: str):
    """Return (rows, could_read). could_read=False means the app cannot see the
    list yet (permission not granted) — caller must NOT treat that as 'zero'."""
    site = _get(f"{GRAPH}/sites/{SITE}", token)
    sid = site["id"]
    lists = _get(f"{GRAPH}/sites/{sid}/lists?$select=id,displayName&$top=100", token).get("value", [])
    lid = pick_list_id(lists)
    if not lid:
        return [], False
    rows, url = [], f"{GRAPH}/sites/{sid}/lists/{lid}/items?expand=fields&$top=200"
    while url:
        page = _get(url, token)
        for it in page.get("value", []):
            f = it.get("fields", {}) or {}
            status = (f.get("Status") or "Open").strip()
            if status in PENDING_STATUSES:
                rows.append({
                    "title": f.get("Title") or "(untitled)",
                    "priority": f.get("Priority") or "Medium",
                    "status": status,
                    "category": f.get("Category") or "",
                })
        url = page.get("@odata.nextLink")
    return rows, True


class Command(BaseCommand):
    help = "Email + OmniTask reminder of pending IT Help Desk tickets (cron 08:00/16:00 SAST)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Read and print only; do not email or write OmniTasks.")
        parser.add_argument("--force", action="store_true",
                            help="Run even on a public holiday (bypass the holiday pause).")

    def handle(self, *args, **opts):
        # Public-holiday quiet time (CFO 2026-07-19): no Help Desk reminders on a
        # Botswana non-working public holiday — resumes next working day.
        from hris.workforce_brief import is_holiday_off
        if is_holiday_off() and not opts.get("force"):
            self.stdout.write(self.style.WARNING(
                "Public holiday (Botswana) — Help Desk reminder paused; "
                "resumes next working day."))
            return
        try:
            token = _token()
        except GraphError as e:
            self.stdout.write(self.style.WARNING(f"No Graph token: {e}"))
            return
        try:
            rows, could_read = _fetch_pending(token)
        except Exception as e:  # noqa: BLE001
            log.warning("helpdesk_pending_reminder: SharePoint read failed: %s", e)
            self.stdout.write(self.style.WARNING(f"SharePoint read failed: {e}"))
            return

        if not could_read:
            self.stdout.write(self.style.WARNING(
                "Cannot see the 'IT Tickets' list yet. Grant the omni Graph app READ on the "
                "ITHelpDesk SharePoint site (Sites.Selected 'read', or Sites.Read.All). "
                "No reminder sent."))
            return

        n = len(rows)
        self.stdout.write(f"Pending tickets: {n}")
        if opts["dry_run"]:
            for r in rows:
                self.stdout.write(f"  [{r['priority']}] {r['title']} ({r['status']})")
            return

        if n == 0:
            self._close_open_tasks()
            self.stdout.write(self.style.SUCCESS("Nothing pending — no email sent; closed any standing task."))
            return

        rows.sort(key=lambda r: _PRI_RANK.get(r["priority"], 2))
        self._email(rows)
        self._upsert_tasks(rows)
        self.stdout.write(self.style.SUCCESS(f"Reminder sent to owners + OmniTasks updated for {n} ticket(s)."))

    # ------------------------------------------------------------------ email
    def _email(self, rows):
        from core.notifications import send_html_with_cfo_cc
        n = len(rows)
        body_rows = "".join(
            f"<tr><td style='padding:6px 10px;border-bottom:1px solid #E5E7EB;'>{escape(r['title'])}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #E5E7EB;'>{escape(r['priority'])}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #E5E7EB;'>{escape(r['status'])}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #E5E7EB;'>{escape(r['category'])}</td></tr>"
            for r in rows
        )
        html = (
            "<div style=\"font-family:'Segoe UI',Arial,sans-serif;color:#1F2937;max-width:700px;\">"
            f"<h2 style=\"color:#0D1B2A;border-bottom:2px solid #F4A623;padding-bottom:6px;\">"
            f"IT Help Desk — {n} ticket(s) pending</h2>"
            "<p>These tickets are still open and need action:</p>"
            "<table style=\"border-collapse:collapse;width:100%;font-size:13px;\">"
            "<tr style=\"background:#0D1B2A;color:#fff;text-align:left;\">"
            "<th style='padding:7px 10px;'>Ticket</th><th style='padding:7px 10px;'>Priority</th>"
            "<th style='padding:7px 10px;'>Status</th><th style='padding:7px 10px;'>Category</th></tr>"
            f"{body_rows}</table>"
            f"<p style=\"margin-top:16px;\"><a href=\"{HELPDESK_URL}\" "
            "style=\"background:#F07F00;color:#fff;padding:10px 18px;border-radius:6px;"
            "text-decoration:none;font-weight:600;\">Open the Help Desk</a></p>"
            "<p style=\"color:#6B7280;font-size:12px;\">Automated reminder at 08:00 &amp; 16:00 daily. "
            "You receive this only while tickets are pending.</p></div>"
        )
        send_html_with_cfo_cc(
            subject=f"IT Help Desk — {n} ticket(s) pending",
            html=html,
            to=list(OWNERS),
            text_fallback=f"{n} IT Help Desk ticket(s) pending. Open {HELPDESK_URL}",
            cc_cfo=False,  # operational nudge to the owners only, not the CFO/EXCO inbox
        )

    # ------------------------------------------------------ OmniTask (/tasks)
    def _system_assigner(self):
        return (User.objects.filter(email__iexact="pganesharajah@alphadirect.co.bw").first()
                or User.objects.filter(is_superuser=True).order_by("id").first())

    def _owner_users(self):
        return [User.objects.filter(email__iexact=e, is_active=True).first() for e in OWNERS]

    def _upsert_tasks(self, rows):
        from core.models import OmniTask
        assigner = self._system_assigner()
        if not assigner:
            log.warning("helpdesk_pending_reminder: no system assigner user; skipping OmniTasks")
            return
        worst = min((_PRI_RANK.get(r["priority"], 2) for r in rows), default=2)
        pri = _OMNI_PRI[worst]
        body = "\n".join(f"- [{r['priority']}] {r['title']} ({r['status']})" for r in rows[:25])
        if len(rows) > 25:
            body += f"\n  ... and {len(rows) - 25} more"
        body += f"\n\nOpen: {HELPDESK_URL}"
        open_states = [OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS]
        for u in self._owner_users():
            if not u:
                continue
            existing = (OmniTask.objects
                        .filter(assignee=u, title=TASK_TITLE, status__in=open_states)
                        .order_by("-created_at").first())
            if existing:
                existing.body = body
                existing.priority = pri
                existing.save()
            else:
                OmniTask.objects.create(
                    assigner=assigner, assignee=u, title=TASK_TITLE,
                    body=body, priority=pri, status=OmniTask.Status.PENDING)

    def _close_open_tasks(self):
        from core.models import OmniTask
        open_states = [OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS]
        for u in self._owner_users():
            if not u:
                continue
            for t in OmniTask.objects.filter(assignee=u, title=TASK_TITLE, status__in=open_states):
                t.status = OmniTask.Status.DONE
                t.completed_at = timezone.now()
                t.save()
