"""Evening ADH service-provider dashboard for EXCO + the CEO & COO (CFO 2026-09-01).

Sends ONE branded HTML email (a glance-and-act dashboard of the provider network:
ready / in-progress / to-chase, coverage by discipline, and new public
applications) plus a short WhatsApp summary to the exec contacts.

  python manage.py send_provider_dashboard                 # send (flag ON)
  python manage.py send_provider_dashboard --dry-run       # print, no send
  python manage.py send_provider_dashboard --preview-file out.html
  python manage.py send_provider_dashboard --no-whatsapp   # email only

Design: canonical Alpha Direct brand — Navy #1D3270 / Orange #F47C20, Montserrat
(email-safe fallback stack). Inline styles only (email clients strip <style>).
Recipients + on/off come from settings (PROVIDER_DASHBOARD_TO / _ENABLED) so they
change without a code deploy.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.html import escape

from healthcare import provider_registry as reg
from healthcare.models import ServiceProvider, ServiceProviderApplication

log = logging.getLogger(__name__)

NAVY = "#1D3270"
ORANGE = "#F47C20"
GREEN = "#1B8A5A"
AMBER = "#C77700"
RED = "#B42318"
INK = "#0F172A"
MUTE = "#64748B"
LINE = "#E7E9EE"
FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Montserrat,Roboto,"
        "Helvetica,Arial,sans-serif")


def _gather():
    """Return the numbers the dashboard shows. Never raises on empty data."""
    c = reg.dashboard_counts()
    providers = list(ServiceProvider.objects.filter(is_active=True))
    # chase-list: marked ready by the team but not fully AFA-registered
    chase = [p for p in providers if p.ready_mismatch][:12]
    # applications in the last 7 days
    week_ago = timezone.now() - timezone.timedelta(days=7)
    new_apps = list(ServiceProviderApplication.objects.filter(
        status="pending", created_at__gte=week_ago).order_by("-created_at")[:8])
    return c, chase, new_apps


def _pct(n, total):
    return round(100 * n / total) if total else 0


def build_email():
    """Return (subject, html, text, wa_params)."""
    c, chase, new_apps = _gather()
    total = c["total"]
    ready = c["adh_ready"]
    in_progress = c["afa_pending"] + c["registered_not_qc"]
    to_chase = c["mismatches"]
    new_count = len(new_apps)
    day = timezone.localtime().strftime("%d %b %Y")
    day_short = timezone.localtime().strftime("%d %b")

    # ---- traffic-light strip (widths proportional) --------------------------
    def seg(w, color, label, n):
        if w <= 0:
            return ""
        return (f'<td style="width:{w}%;background:{color};color:#fff;'
                f'padding:8px 6px;text-align:center;font-size:12px;font-weight:600;'
                f'font-family:{FONT};">{n} {label}</td>')
    strip = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-radius:8px;overflow:hidden;border-collapse:separate;">'
        f'<tr>'
        f'{seg(_pct(ready, total), GREEN, "ready", ready)}'
        f'{seg(_pct(in_progress, total), AMBER, "in progress", in_progress)}'
        f'{seg(_pct(to_chase, total), RED, "to chase", to_chase)}'
        f'</tr></table>')

    # ---- coverage-by-discipline bar (top 6) ---------------------------------
    disc = list(c["by_discipline"].items())[:6]
    disc_max = max((n for _, n in disc), default=1)
    disc_rows = ""
    for name, n in disc:
        w = round(100 * n / disc_max)
        disc_rows += (
            f'<tr>'
            f'<td style="padding:3px 8px 3px 0;font-size:12px;color:{INK};'
            f'font-family:{FONT};white-space:nowrap;">{escape(name.title())}</td>'
            f'<td style="padding:3px 0;width:100%;">'
            f'<div style="background:{LINE};border-radius:4px;">'
            f'<div style="width:{w}%;background:{NAVY};height:10px;border-radius:4px;"></div>'
            f'</div></td>'
            f'<td style="padding:3px 0 3px 8px;font-size:12px;color:{MUTE};'
            f'font-family:{FONT};text-align:right;">{n}</td>'
            f'</tr>')

    # ---- chase list ---------------------------------------------------------
    if chase:
        chase_rows = "".join(
            f'<tr>'
            f'<td style="padding:6px 8px;border-bottom:1px solid {LINE};font-size:13px;'
            f'color:{INK};font-family:{FONT};">{escape(p.name)}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid {LINE};font-size:12px;'
            f'color:{MUTE};font-family:{FONT};">{escape(p.town or "—")}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid {LINE};font-size:12px;'
            f'color:{AMBER};font-family:{FONT};">{escape(p.contract_status or "not registered")}</td>'
            f'</tr>'
            for p in chase)
        chase_html = (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse;">{chase_rows}</table>')
    else:
        chase_html = (f'<p style="margin:0;color:{GREEN};font-size:13px;font-family:{FONT};">'
                      f'Nothing to chase — every "ready" provider is fully AFA-registered.</p>')

    # ---- applications -------------------------------------------------------
    if new_apps:
        app_rows = "".join(
            f'<tr><td style="padding:5px 8px;border-bottom:1px solid {LINE};font-size:13px;'
            f'color:{INK};font-family:{FONT};">{escape(a.name)}'
            f'<span style="color:{MUTE};"> · {escape(a.discipline or "—")} · {escape(a.town or "—")}</span>'
            f'</td></tr>'
            for a in new_apps)
        apps_html = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                     f'style="border-collapse:collapse;">{app_rows}</table>')
    else:
        apps_html = (f'<p style="margin:0;color:{MUTE};font-size:13px;font-family:{FONT};">'
                     f'No new applications this week.</p>')

    def stat(label, value, color=NAVY):
        return (
            f'<td style="padding:0 6px;" width="25%">'
            f'<div style="background:#F6F7F9;border-radius:8px;padding:12px 10px;text-align:center;">'
            f'<div style="font-size:22px;font-weight:700;color:{color};font-family:{FONT};">{value}</div>'
            f'<div style="font-size:11px;color:{MUTE};font-family:{FONT};margin-top:2px;">{label}</div>'
            f'</div></td>')

    section = (f'font-size:13px;font-weight:700;color:{NAVY};font-family:{FONT};'
               f'text-transform:uppercase;letter-spacing:.04em;margin:0 0 8px;'
               f'padding-bottom:6px;border-bottom:2px solid {ORANGE};')

    html = f"""\
<div style="background:#F1F3F6;padding:24px 12px;font-family:{FONT};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;margin:0 auto;">
<tr><td style="background:{NAVY};border-radius:12px 12px 0 0;padding:20px 24px;">
  <div style="color:#fff;font-size:17px;font-weight:700;font-family:{FONT};">Alpha Direct Health</div>
  <div style="color:{ORANGE};font-size:13px;font-weight:600;font-family:{FONT};margin-top:2px;">
    Service Provider Network — evening dashboard</div>
  <div style="color:#AEB8CC;font-size:12px;font-family:{FONT};margin-top:2px;">{day}</div>
</td></tr>
<tr><td style="background:#fff;padding:24px;">

  <!-- hero -->
  <div style="text-align:center;margin-bottom:6px;">
    <div style="font-size:44px;line-height:1;font-weight:800;color:{GREEN};font-family:{FONT};">{ready}</div>
    <div style="font-size:13px;color:{MUTE};font-family:{FONT};margin-top:4px;">
      providers ready to accept ADH clients today</div>
  </div>
  <div style="margin:16px 0 20px;">{strip}</div>

  <!-- stat row -->
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:22px;">
    <tr>
      {stat("Total network", total)}
      {stat("AFA registered", c["afa_registered"])}
      {stat("AFA pending", c["afa_pending"], AMBER)}
      {stat("New applications", new_count, ORANGE)}
    </tr>
  </table>

  <!-- chase -->
  <p style="{section}">Needs chasing ({to_chase})</p>
  <p style="margin:0 0 8px;font-size:12px;color:{MUTE};font-family:{FONT};">
    Marked ready by the team, but AFA registration is not finished.</p>
  {chase_html}

  <!-- coverage -->
  <p style="{section}margin-top:22px;">Coverage by type</p>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
    {disc_rows}
  </table>

  <!-- applications -->
  <p style="{section}margin-top:22px;">New applications this week</p>
  {apps_html}

</td></tr>
<tr><td style="background:#fff;border-radius:0 0 12px 12px;padding:14px 24px;border-top:1px solid {LINE};">
  <div style="font-size:11px;color:{MUTE};font-family:{FONT};">
    Open <b>Omni &rarr; Health &rarr; Service Providers</b> for the full list and to update QC.
    Generated automatically each evening.</div>
</td></tr>
</table></div>"""

    text = (f"Alpha Direct Health — Service Provider network ({day})\n"
            f"Ready to accept ADH clients: {ready} of {total}\n"
            f"AFA registered: {c['afa_registered']} | AFA pending: {c['afa_pending']} | "
            f"To chase: {to_chase} | New applications: {new_count}\n"
            f"Open Omni > Health > Service Providers for the full list.")

    subject = f"ADH provider network — {ready} ready · {to_chase} to chase ({day_short})"
    wa_params = [day_short, str(ready), str(to_chase), str(new_count)]
    return subject, html, text, wa_params


def _send_whatsapp(wa_params) -> str:
    """Send the WhatsApp summary to exec contacts. Uses the approved template when
    Meta has approved it; otherwise reports what is pending. Never raises."""
    try:
        from taskboard import whatsapp_service as wa
        from taskboard.models import WhatsAppContact
    except Exception as e:  # noqa: BLE001
        return f"WhatsApp module unavailable ({e})"
    if not wa.is_configured():
        return "WhatsApp not configured (no key in the vault) — skipped."

    tmpl = "exec_provider_report"
    approved = wa.template_status().get(tmpl) == "APPROVED"
    contacts = list(WhatsAppContact.objects.filter(is_manager=True)) \
        or list(WhatsAppContact.objects.all())
    if not contacts:
        return "No WhatsApp exec contacts in the phonebook — skipped."

    sent, failed = 0, 0
    for ct in contacts:
        if approved:
            ok, _pid, _err = wa.send_template(ct.phone, tmpl, wa_params)
        else:
            # Template not yet approved by Meta — free-form only reaches contacts
            # inside the 24h window, but never raises. Reliable push waits on approval.
            ok, _pid, _err = wa.send_message(
                ct.phone,
                f"Alpha Direct — provider network evening update ({wa_params[0]}). "
                f"{wa_params[1]} ready, {wa_params[2]} to chase, {wa_params[3]} new "
                f"application(s). Full dashboard in your Omni email.")
        sent += int(ok)
        failed += int(not ok)
    state = "approved template" if approved else "PENDING template approval (free-form fallback)"
    return f"WhatsApp: {sent} accepted, {failed} failed to {len(contacts)} contact(s) [{state}]."


class Command(BaseCommand):
    help = "Evening ADH service-provider dashboard email + WhatsApp for EXCO / CEO / COO."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--preview-file", dest="preview_file", default="")
        parser.add_argument("--no-whatsapp", action="store_true")
        parser.add_argument("--force", action="store_true",
                            help="Send even if the in-app switch is OFF (a manual test send).")
        parser.add_argument("--to", nargs="*", default=None)

    def handle(self, *args, **opts):
        subject, html, text, wa_params = build_email()

        if opts["preview_file"]:
            with open(opts["preview_file"], "w", encoding="utf-8") as fh:
                fh.write(html)
            self.stdout.write(f"Wrote preview HTML to {opts['preview_file']}.")
            return

        recipients = opts["to"] or list(
            getattr(settings, "PROVIDER_DASHBOARD_TO", None)
            or ["pganesharajah@alphadirect.co.bw"])

        if opts["dry_run"]:
            self.stdout.write(f"DRY-RUN -> {recipients} (CC excoboard@): {subject}")
            self.stdout.write(text)
            if not opts["no_whatsapp"]:
                self.stdout.write("WhatsApp: would send exec summary " + str(wa_params))
            return

        # Master hard kill (env) — normally left ON; the day-to-day control is the
        # in-app switch below, which the ADH team flips once the latest file is in.
        if not getattr(settings, "PROVIDER_DASHBOARD_ENABLED", True):
            self.stdout.write("PROVIDER_DASHBOARD_ENABLED master kill is OFF — skipping.")
            return

        from healthcare.models import ProviderDashboardConfig
        if not opts["force"] and not ProviderDashboardConfig.current().enabled:
            self.stdout.write("Evening dashboard switch is OFF — skipping send. "
                              "Turn it on in Omni once the latest file is imported.")
            return

        from core.notifications import send_html_with_cfo_cc
        # The CEO (aiyer@) and COO (arjuniyer@) are on the _NEVER_CC safety list,
        # so without allow_named_exec they would be SILENTLY stripped from TO and
        # only excoboard@ (the auto-CC) would receive it. The CFO named them
        # explicitly as recipients, which is exactly what this flag is for.
        send_html_with_cfo_cc(subject, html, recipients, text_fallback=text,
                              allow_named_exec=True)
        msg = f"Provider dashboard emailed to {recipients} (CC excoboard@)."
        if not opts["no_whatsapp"]:
            msg += " " + _send_whatsapp(wa_params)
        self.stdout.write(self.style.SUCCESS(msg))
