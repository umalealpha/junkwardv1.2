"""
python manage.py helpdesk_add_comment --ticket TKT-0001 --text "..." [--author "Name"]

Posts a Django-side comment on an IT Help Desk ticket.
The comment is stored in the omni DB and surfaced in the Help Desk SPA
at /api/helpdesk/comments/?ticket=TKT-0001 (no auth required).

Run via SSM on EC2 (af-south-1, i-02a5d76a61f4f09a5):
  python manage.py helpdesk_add_comment --ticket TKT-0001 --text "Checked — issue is with the VPN config. Fix deployed."
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Add a comment to an IT Help Desk ticket (stored in omni DB, shown in Help Desk UI)."

    def add_arguments(self, parser):
        parser.add_argument("--ticket", required=True, help="Ticket ID e.g. TKT-0001")
        parser.add_argument("--text",   required=True, help="Comment text")
        parser.add_argument("--author", default="Prathap Ganesharajah", help="Author display name")

    def handle(self, *args, **opts):
        from core.models import HelpdeskComment
        c = HelpdeskComment.objects.create(
            ticket_id=opts["ticket"].upper(),
            author=opts["author"],
            text=opts["text"],
            source="claude_code",
        )
        self.stdout.write(self.style.SUCCESS(
            f"Comment #{c.id} posted on {c.ticket_id} by {c.author}"
        ))
