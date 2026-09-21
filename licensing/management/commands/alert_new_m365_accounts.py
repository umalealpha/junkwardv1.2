import logging
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.html import escape

from core.notifications import send_html_with_cfo_cc
from licensing.models import M365AccountSeen
from licensing.services.graph import GraphError, fetch_active_licensed_humans
from payroll.models import Employee

logger = logging.getLogger(__name__)

HR_RECIPIENTS = [
    "dikgopoleng@alphadirect.co.bw",
    "ubutale@alphadirect.co.bw",
]
AGENT_DOMAIN = "@insurance.co.bw"


class Command(BaseCommand):
    help = "Alert HR for new Microsoft 365 licensed human accounts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Write rows and send the alert email. Default is dry-run.",
        )

    def handle(self, *args, **options):
        commit = options["commit"]
        cutoff = timezone.now() - timedelta(days=3650)

        try:
            result = fetch_active_licensed_humans(cutoff)
            humans = result[0] if isinstance(result, tuple) else result
        except GraphError as exc:
            raise CommandError(f"Graph error fetching licensed humans: {exc}") from exc

        existing_ids = set(
            M365AccountSeen.objects.values_list("object_id", flat=True)
        )

        if not existing_ids:
            if commit:
                for acct in humans:
                    self._create_seen(
                        acct,
                        alerted=True,
                        is_agent=self._is_agent(acct),
                    )
                self.stdout.write(self.style.SUCCESS(f"seeded {len(humans)}"))
            else:
                self.stdout.write(f"dry-run: would seed {len(humans)}")
            return

        new_accounts = [
            acct for acct in humans if acct.get("object_id") not in existing_ids
        ]
        if not new_accounts:
            self.stdout.write("No new Microsoft 365 accounts")
            return

        new_agents = []
        new_humans = []
        for acct in new_accounts:
            if self._is_agent(acct):
                new_agents.append(acct)
            else:
                new_humans.append(acct)

        if not commit:
            self.stdout.write(f"dry-run: would create {len(new_accounts)} rows")
            if new_humans:
                self.stdout.write(
                    f"dry-run: would email {len(new_humans)} non-agent account(s)"
                )
            return

        payroll_map = {
            (acct.get("email") or "").lower(): self._is_on_payroll(acct.get("email"))
            for acct in new_humans
        }

        with transaction.atomic():
            for acct in new_agents:
                self._create_seen(
                    acct,
                    alerted=False,
                    is_agent=True,
                )

            created_humans = {}
            for acct in new_humans:
                row = self._create_seen(
                    acct,
                    alerted=False,
                    is_agent=False,
                )
                created_humans[(acct.get("object_id") or "").strip()] = row

            if new_humans:
                subject = self._subject(new_humans)
                html = self._build_html(new_humans, payroll_map)
                try:
                    sent = send_html_with_cfo_cc(
                        subject=subject,
                        html=html,
                        to=HR_RECIPIENTS,
                        cc_cfo=True,
                    )
                except Exception as exc:
                    raise CommandError(f"Email send failed: {exc}") from exc

                if sent >= 1:
                    for row in created_humans.values():
                        row.alerted = True
                        row.save(update_fields=["alerted"])

        for acct in new_agents:
            self.stdout.write(self.style.SUCCESS(f"Recorded agent {acct.get('email')}"))
        for acct in new_humans:
            self.stdout.write(self.style.SUCCESS(f"Recorded human {acct.get('email')}"))

    def _is_agent(self, acct):
        return (acct.get("email") or "").lower().endswith(AGENT_DOMAIN)

    def _is_on_payroll(self, email):
        if not email:
            return False
        return Employee.objects.filter(email__iexact=email).exists()

    def _create_seen(self, acct, *, alerted, is_agent):
        return M365AccountSeen.objects.create(
            object_id=acct["object_id"],
            email=acct.get("email") or "",
            display_name=acct.get("display_name") or "",
            alerted=alerted,
            is_agent=is_agent,
        )

    def _subject(self, accounts):
        if len(accounts) == 1:
            name = (
                accounts[0].get("display_name")
                or accounts[0].get("email")
                or "unknown"
            )
            return f"New Microsoft account: {name}"
        return f"{len(accounts)} new Microsoft accounts"

    def _build_html(self, accounts, payroll_map):
        rows = []
        for acct in accounts:
            name = escape(acct.get("display_name") or "")
            email = escape(acct.get("email") or "")
            job_title = escape(acct.get("job_title") or "")
            department = escape(acct.get("department") or "")

            job_dept = " / ".join(
                item for item in (job_title, department) if item
            ) or "—"

            key = (acct.get("email") or "").lower()
            payroll_status = "Yes" if payroll_map.get(key, False) else "No"

            rows.append(
                "<tr>"
                f'<td style="padding:8px;border-bottom:1px solid #E0E4EA;">{name}</td>'
                f'<td style="padding:8px;border-bottom:1px solid #E0E4EA;">{email}</td>'
                f'<td style="padding:8px;border-bottom:1px solid #E0E4EA;">{job_dept}</td>'
                f'<td style="padding:8px;border-bottom:1px solid #E0E4EA;">{payroll_status}</td>'
                "</tr>"
            )

        rows_html = "\n".join(rows)
        return f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#F5F7FA;">
  <table width="640" border="0" cellpadding="0" cellspacing="0" align="center" style="background:#ffffff;font-family:Arial,Helvetica,sans-serif;">
    <tr>
      <td style="background:#0D1B2A;padding:20px 30px;">
        <div style="color:#ffffff;font-size:11px;letter-spacing:2px;font-variant:small-caps;text-transform:uppercase;">ALPHA DIRECT · OMNI</div>
        <div style="color:#F4A623;font-size:22px;font-weight:bold;margin-top:8px;">New Microsoft account</div>
      </td>
    </tr>
    <tr>
      <td style="padding:24px 30px;">
        <p style="font-size:14px;color:#222;">Please start their onboarding in Omni (People → Onboard) so payroll matches Microsoft 365. Without it they cannot sign in to Omni.</p>
        <table width="100%" border="0" cellpadding="8" cellspacing="0" style="border-collapse:collapse;font-size:13px;color:#222;">
          <tr style="background:#F2F4F7;color:#0D1B2A;">
            <th align="left" style="border-bottom:1px solid #D8DEE9;">Name</th>
            <th align="left" style="border-bottom:1px solid #D8DEE9;">Email</th>
            <th align="left" style="border-bottom:1px solid #D8DEE9;">Job title / department</th>
            <th align="left" style="border-bottom:1px solid #D8DEE9;">On Omni payroll?</th>
          </tr>
          {rows_html}
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
""".strip()
