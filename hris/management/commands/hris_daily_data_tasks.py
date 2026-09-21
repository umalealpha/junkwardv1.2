"""
hris/management/commands/hris_daily_data_tasks.py

Daily HRIS data-completeness nudge — CFO directives 2026-06-25.

Each morning it:
  1. Confirms Unami & Dorothy still hold direct HRIS edit access.
  2. ONLY asks about people who are on the CURRENT payroll (a payslip in their
     company's latest period) — no nagging about people not being paid.
  3. Detects LEAVERS by comparing each company's two latest payrolls (in the
     prior period, not the latest) + recently-terminated staff, and reminds HR
     to load their RESIGNATION + EXIT INTERVIEW into the HR vault — every day
     until BOTH are filed.
  4. Spreads the active-staff data tasks across many people (<=2 each), rotated
     by date; celebrates yesterday's progress and chases what wasn't actioned.
  5. Rotating company focus + a payroll-derived plan line.
  6. Emails Unami + Dorothy + CFO, then records the run for tomorrow's follow-up.

"Place to load" a resignation / exit interview = HR vault (/hris/documents),
categories 'Resignation' and 'Exit interview' (tagged to the person's name).
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict

from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from core.hris_access import user_can_access_hris
from core.privacy_notice import NOTICE_VERSION, outstanding_users
from hris.amendment_service import _can_self_apply
from hris.models import HRDocument, HRISDailyTaskRun, HRISProfile
from payroll.models import Employee, Payslip

EDITORS = [('Unami', 'ubutale@alphadirect.co.bw'), ('Dorothy', 'dikgopoleng@alphadirect.co.bw')]
# CFO 2026-08-12: this daily HRIS data-task nudge is HR's list — it goes to Unami
# & Dorothy only, no longer to the CFO's inbox (his email-reduction request).
NAVY, ORANGE = '#0D1B2A', '#F4A623'
DAILY_QUOTA = 10
MAX_PER_EMP = 2
MAX_CHASE = 5
MAX_LEAVERS_SHOWN = 12
MAX_UNSIGNED_SHOWN = 60

GAP_FIELDS = [
    ('e', 'national_id',     'National ID'),
    ('p', 'date_of_birth',   'Date of birth'),
    # Bank details are NOT chased here (CFO directive 2026-06-29 / 2026-07-15).
    # Account number + branch code come from the salary file via the bank-details
    # bulk upload (payroll/bank_import_views.py, approved by Dorothy), and the
    # bank NAME is DERIVED from the branch code (payroll/bank_codes.py) — so
    # neither the account nor the name is ever a manual daily task.
    ('p', 'grade_id',        'Pay grade'),
    ('e', 'hire_date',       'Hire date'),
    ('p', 'nationality',     'Nationality'),
    ('p', 'gender',          'Gender'),
    ('e', 'phone',           'Phone number'),
    ('p', 'location',        'Location'),
    ('e', 'job_title',       'Job title'),
    ('e', 'department',      'Department'),
    ('e', 'email',           'Work email'),
]

FOCUS_PROMPTS = [
    'Load the company HR policies into the HR Document vault (leave policy, disciplinary code, code of conduct).',
    'Upload current Job Descriptions (JDs) for each role into the HR vault.',
    'Load the FY26 strategy / business plan document into the vault.',
    'Confirm every active employee has a signed employment contract on file (HR vault -> Contract).',
    'Review the org structure & reporting lines (People -> each person -> "Reports to").',
    'Upload the staff Code of Conduct (ELRA-aligned) to the policy vault.',
    'Confirm leave entitlements & opening balances are current for all staff.',
]


def _missing(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _latest_two_per_company():
    """{company_id: (latest_period_name, prev_period_name_or_None)} from payslips.
    Per-company so a mis-named period in one entity can't flag another's staff."""
    pers = defaultdict(set)
    for cid, pname in Payslip.objects.values_list('company_id', 'period__period_name'):
        if cid:
            pers[cid].add(pname)
    out = {}
    for cid, names in pers.items():
        s = sorted(names)
        out[cid] = (s[-1], s[-2] if len(s) >= 2 else None)
    return out


def _payroll_member_ids():
    """Employee ids with a payslip in their company's LATEST period — the people
    actually being paid right now (CFO: only ask about people in the payroll)."""
    ids = set()
    for cid, (latest, _prev) in _latest_two_per_company().items():
        ids |= set(Payslip.objects.filter(company_id=cid, period__period_name=latest)
                   .values_list('employee_id', flat=True))
    return ids


def _leavers():
    """Employees who dropped from their company's latest payroll (in prev, not
    latest) OR are terminated but were on a recent payroll — i.e. real leavers."""
    pairs = _latest_two_per_company()
    leaver_ids = set()
    for cid, (latest, prev) in pairs.items():
        if not prev:
            continue
        in_latest = set(Payslip.objects.filter(company_id=cid, period__period_name=latest)
                        .values_list('employee_id', flat=True))
        in_prev = set(Payslip.objects.filter(company_id=cid, period__period_name=prev)
                      .values_list('employee_id', flat=True))
        leaver_ids |= (in_prev - in_latest)
    recent = sorted({p for p in Payslip.objects.values_list('period__period_name', flat=True)})[-3:]
    leaver_ids |= set(Payslip.objects.filter(period__period_name__in=recent,
                      employee__status=Employee.Status.TERMINATED)
                      .values_list('employee_id', flat=True))
    return list(Employee.objects.filter(id__in=leaver_ids).select_related('company'))


def _has_doc(emp, category) -> bool:
    """A leaver's resignation/exit-interview doc is on file — matched by the
    profile FK if set, else by the employee_name the vault page captures."""
    return HRDocument.objects.filter(category=category).filter(
        Q(employee__employee_id=emp.id) | Q(employee_name__iexact=emp.full_name)).exists()


def _field_gaps(member_ids):
    """Field gaps for CURRENT payroll members only -> ([items], outstanding, pct)."""
    profiles = (HRISProfile.objects.select_related('employee', 'grade')
                .filter(employee_id__in=member_ids)
                .exclude(employee__status=Employee.Status.TERMINATED)  # leavers handled separately
                .order_by('employee__full_name'))
    items, total, filled = [], 0, 0
    for p in profiles:
        emp = p.employee
        for src, attr, label in GAP_FIELDS:
            obj = emp if src == 'e' else p
            total += 1
            if _missing(getattr(obj, attr, None)):
                items.append({'key': f'{emp.id}:{attr}', 'employee': emp.full_name,
                              'label': label, 'emp_id': str(emp.id)})
            else:
                filled += 1
    pct = round(100 * filled / total, 1) if total else 100.0
    return items, total - filled, pct


def _leaver_items(leavers):
    """One/two tasks per leaver: missing resignation + missing exit interview."""
    items = []
    for emp in leavers:
        if not _has_doc(emp, HRDocument.Category.RESIGNATION):
            items.append({'key': f'{emp.id}:resignation_doc', 'employee': emp.full_name,
                          'label': 'resignation (HR vault -> Documents -> Resignation)', 'emp_id': str(emp.id)})
        if not _has_doc(emp, HRDocument.Category.EXIT):
            items.append({'key': f'{emp.id}:exit_doc', 'employee': emp.full_name,
                          'label': 'exit interview (HR vault -> Documents -> Exit interview)', 'emp_id': str(emp.id)})
    return items


def _spread(items, quota, day_ordinal, exclude_keys):
    by_emp = OrderedDict()
    for it in items:
        if it['key'] in exclude_keys:
            continue
        by_emp.setdefault(it['emp_id'], []).append(it)
    emp_ids = list(by_emp.keys())
    if not emp_ids:
        return []
    rot = day_ordinal % len(emp_ids)
    emp_ids = emp_ids[rot:] + emp_ids[:rot]
    picked = []
    for slot in range(MAX_PER_EMP):
        for eid in emp_ids:
            if len(picked) >= quota:
                break
            if len(by_emp[eid]) > slot:
                picked.append(by_emp[eid][slot])
        if len(picked) >= quota:
            break
    return picked[:quota]


MAX_NOTARGET_SHOWN = 15


def _missing_targets(member_ids):
    """Current-payroll, active employees with NO active performance target set —
    i.e. their job description / monthly target has not been captured yet. We ask
    HR for these on the daily email (CFO 2026-07-20: chase job descriptions, not
    bank details)."""
    from hris.performance_target_models import PerformanceTarget
    from payroll.models import Employee

    have = set(PerformanceTarget.objects.filter(active=True)
               .values_list('profile__employee_id', flat=True))
    return list(Employee.objects.filter(id__in=member_ids)
                .exclude(status=Employee.Status.TERMINATED)
                .exclude(id__in=have)
                .order_by('full_name'))


class Command(BaseCommand):
    help = 'Smart daily HRIS email — payroll-scoped tasks, leaver exit-paperwork reminders, follow-up.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--to', default='')

    def handle(self, *args, **opts):
        today = timezone.localdate()

        # Refresh the HRIS alert register first (contract expiry, leave excess,
        # ELRA s.219 mandatory-leave-take, reviews) so today's alerts exist for
        # the /hris/alerts page + this email. The nightly cron only runs this
        # command, so without this the alert generator never ran. Never let an
        # alert failure block the daily email.
        try:
            from django.core.management import call_command
            call_command('generate_hris_alerts')
        except Exception as e:  # noqa: BLE001
            self.stderr.write(f'[hris_daily] generate_hris_alerts failed: {e}')

        # Auto-attach new joiners in configured entities (UniCoin -> Bakang, Veritas
        # -> Bharath) to a default line manager. Fills BLANKS only, never overwrites,
        # so it is safe to run every night. A --dry-run must not write.
        if not opts.get('dry_run'):
            try:
                from django.core.management import call_command as _cc
                _cc('default_entity_manager', '--commit')
            except Exception as e:  # noqa: BLE001
                self.stderr.write(f'[hris_daily] default_entity_manager failed: {e}')

        # Monthly performance-feedback cycle: on the first working days of the
        # month, prompt managers (for the month that just ended) + refresh target
        # actuals. Idempotent — safe to run daily; only fires prompts once/period.
        # A --dry-run of this daily command must NOT write (it creates real tasks).
        if today.day <= 5 and not opts.get('dry_run'):
            try:
                from django.core.management import call_command as _cc
                _cc('monthly_feedback_cycle', '--commit')
            except Exception as e:  # noqa: BLE001
                self.stderr.write(f'[hris_daily] monthly_feedback_cycle failed: {e}')

        access = {}
        for name, email in EDITORS:
            u = User.objects.filter(email__iexact=email).first()
            access[name] = bool(u and user_can_access_hris(u) and _can_self_apply(u))

        member_ids = _payroll_member_ids()
        field_items, outstanding, pct = _field_gaps(member_ids)
        leavers = _leavers()
        leaver_items = _leaver_items(leavers)
        unsigned = outstanding_users()   # staff who have NOT signed the privacy notice
        no_target = _missing_targets(member_ids)   # employees with no job description / target

        current_open = {it['key'] for it in field_items} | {it['key'] for it in leaver_items}
        prior = HRISDailyTaskRun.objects.filter(run_date__lt=today).order_by('-run_date').first()
        done, still_open = [], []
        if prior:
            for t in (prior.tasks or []):
                (still_open if t['key'] in current_open else done).append(t)
        ignored = bool(prior) and (prior.tasks or []) and len(done) == 0

        leaver_shown = leaver_items[:MAX_LEAVERS_SHOWN]
        leaver_keys = {t['key'] for t in leaver_shown}
        active = _spread(field_items, DAILY_QUOTA, today.toordinal(), leaver_keys)
        focus = FOCUS_PROMPTS[today.toordinal() % len(FOCUS_PROMPTS)]
        todays = leaver_shown + active   # persisted -> chased until done

        recipients = ([x.strip() for x in opts['to'].split(',') if x.strip()]
                      or [e for _, e in EDITORS])  # HR only — CFO removed 2026-08-12
        subject = self._subject(leavers, active, ignored, done, prior)
        text, html = self._render(access, prior, done, ignored, leavers, leaver_shown,
                                  active, focus, pct, outstanding, len(member_ids), unsigned,
                                  no_target)

        if opts['dry_run']:
            self.stdout.write(subject)
            self.stdout.write(text)
            self.stdout.write(f"[dry-run] would send to: {recipients}")
            return

        msg = EmailMultiAlternatives(subject=subject, body=text, from_email=None, to=recipients)
        msg.attach_alternative(html, 'text/html')
        sent = msg.send(fail_silently=False)
        HRISDailyTaskRun.objects.update_or_create(
            run_date=today, defaults={'tasks': todays, 'completeness_pct': pct, 'focus': focus})
        self.stdout.write(f"sent={sent} recipients={recipients} payroll={len(member_ids)} "
                          f"leavers={len(leavers)} leaver_tasks={len(leaver_shown)} "
                          f"active={len(active)} done_since_yday={len(done)} pct={pct}")

    def _subject(self, leavers, active, ignored, done, prior):
        if leavers:
            return f"Omni HRIS - {len(leavers)} leaver(s) need exit paperwork + {len(active)} data task(s)"
        if not active:
            return "Omni HRIS - access active - payroll data COMPLETE"
        if ignored:
            return f"Omni HRIS - reminder: yesterday's tasks still outstanding + {len(active)} today"
        if prior and done:
            return f"Omni HRIS - {len(done)} done since yesterday, {len(active)} task(s) today"
        return f"Omni HRIS - access active + {len(active)} data task(s) today"

    def _render(self, access, prior, done, ignored, leavers, leaver_shown,
                active, focus, pct, outstanding, members, unsigned, no_target=None):
        no_target = no_target or []
        acc = " | ".join(f"{n}: {'ACTIVE' if ok else 'NOT ACTIVE - tell the CFO'}"
                         for n, ok in access.items())
        L = ["Omni HRIS - daily data tasks (current payroll only)", "",
             f"HRIS edit access - {acc}.",
             "You edit employee records directly in Omni; changes apply immediately and are audited.", ""]
        if prior and done:
            L.append(f"Since yesterday: {len(done)} item(s) done - thank you.")
        if ignored:
            L.append("Note: NONE of yesterday's list was actioned - please clear the items below.")

        if leaver_shown:
            L += ["",
                  f"LEAVERS - {len(leavers)} person(s) left the payroll. Load their resignation + "
                  "exit interview into the HR vault (/hris/documents). Reminder repeats until both are filed:"]
            L += [f"  - {t['employee']}: load {t['label']}" for t in leaver_shown]

        if active:
            L += ["",
                  f"Today's {len(active)} data tasks - current payroll, spread across the team "
                  "(Omni -> People -> Edit):"]
            L += [f"  {i}. Load {t['employee']}'s {t['label']}" for i, t in enumerate(active, 1)]
        elif not leaver_shown:
            L.append("Payroll data is COMPLETE - no tasks today. Sharp sharp!")

        if unsigned:
            L += ["",
                  f"PRIVACY NOTICE - {len(unsigned)} staff have NOT signed the staff privacy "
                  "notice yet (it pops up when they log into Omni). Please chase them to log "
                  "in and sign:"]
            L += [f"  - {(u.get_full_name() or u.username)} ({u.email})"
                  for u in unsigned[:MAX_UNSIGNED_SHOWN]]
            if len(unsigned) > MAX_UNSIGNED_SHOWN:
                L.append(f"  ... and {len(unsigned) - MAX_UNSIGNED_SHOWN} more")
        else:
            L += ["", "PRIVACY NOTICE - all staff have signed the privacy notice. Sharp sharp!"]

        if no_target:
            shown_nt = no_target[:MAX_NOTARGET_SHOWN]
            L += ["",
                  f"JOB DESCRIPTIONS / TARGETS - {len(no_target)} employee(s) have no monthly "
                  "target on file. Please send HR their job description + monthly target so we can "
                  "load it (this replaces asking staff for bank details):"]
            L += [f"  - {e.full_name}" + (f" ({e.job_title})" if e.job_title else "") for e in shown_nt]
            if len(no_target) > MAX_NOTARGET_SHOWN:
                L.append(f"  ... and {len(no_target) - MAX_NOTARGET_SHOWN} more")

        L += ["", f"This week's focus: {focus}", "",
              f"Plan: {members} people on the current payroll; {len(leavers)} leaver(s) awaiting exit "
              f"paperwork. Payroll data completeness {pct}% ({outstanding} field(s) outstanding)."]
        text = "\n".join(L)

        def esc(s):
            return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        P = [f"<div style=\"font-family:'Book Antiqua',Georgia,serif;color:{NAVY};max-width:780px\">",
             f"<h2 style='color:{NAVY};border-bottom:3px solid {ORANGE};padding-bottom:6px'>"
             f"Omni HRIS &mdash; Daily Data Tasks</h2>",
             f"<p>HRIS edit access &mdash; {esc(acc)}. <i>Current payroll only.</i></p>"]
        if prior and done:
            P.append(f"<p style='color:#1F5132'>&#10003; Since yesterday: <b>{len(done)}</b> done &mdash; thank you.</p>")
        if ignored:
            P.append("<p style='color:#8E1F12'><b>Nothing from yesterday was actioned</b> &mdash; please clear the items below.</p>")
        if leaver_shown:
            P.append(f"<div style='background:#FDECEA;border-left:3px solid #8E1F12;padding:8px 12px'>"
                     f"<b>Leavers &mdash; {len(leavers)} left the payroll.</b> Load each one's resignation + "
                     f"exit interview into the HR vault (/hris/documents). Reminder repeats until both are filed:"
                     f"<ul>" + "".join(f"<li>{esc(t['employee'])}: load {esc(t['label'])}</li>" for t in leaver_shown)
                     + "</ul></div>")
        if active:
            P.append(f"<p><b>Today's {len(active)} data tasks</b> &mdash; current payroll, spread across the team:</p><ol>"
                     + "".join(f"<li>Load {esc(t['employee'])}'s {esc(t['label'])}</li>" for t in active) + "</ol>")
        elif not leaver_shown:
            P.append("<p><b>Payroll data is COMPLETE</b> &mdash; no tasks today. Sharp sharp!</p>")
        if unsigned:
            shown = unsigned[:MAX_UNSIGNED_SHOWN]
            more = len(unsigned) - len(shown)
            P.append(
                f"<div style='background:#FDECEA;border-left:3px solid #8E1F12;padding:8px 12px'>"
                f"<b>Privacy notice &mdash; {len(unsigned)} staff have NOT signed yet.</b> "
                f"It pops up when they log into Omni. Please chase them to log in and sign:"
                f"<ul>" + "".join(f"<li>{esc(u.get_full_name() or u.username)} &mdash; "
                                  f"{esc(u.email)}</li>" for u in shown)
                + (f"<li>&hellip; and {more} more</li>" if more > 0 else "")
                + "</ul></div>")
        else:
            P.append("<div style='background:#ECFDF5;border-left:3px solid #059669;padding:8px 12px'>"
                     "<b>Privacy notice &mdash; all staff have signed.</b> Sharp sharp!</div>")
        if no_target:
            shown_nt = no_target[:MAX_NOTARGET_SHOWN]
            more_nt = len(no_target) - len(shown_nt)
            P.append(
                f"<div style='background:#FFF6E5;border-left:3px solid {ORANGE};padding:8px 12px'>"
                f"<b>Job descriptions / targets &mdash; {len(no_target)} employee(s) have no monthly target.</b> "
                f"Please send HR their job description + monthly target to load (instead of bank details):"
                f"<ul>" + "".join(f"<li>{esc(e.full_name)}"
                                  + (f" &mdash; {esc(e.job_title)}" if e.job_title else "") + "</li>"
                                  for e in shown_nt)
                + (f"<li>&hellip; and {more_nt} more</li>" if more_nt > 0 else "")
                + "</ul></div>")
        P.append(f"<p style='background:#FFF6E5;border-left:3px solid {ORANGE};padding:8px 12px'>"
                 f"<b>This week's focus:</b> {esc(focus)}</p>")
        P.append(f"<p style='color:#555;font-size:12px'>Plan: <b>{members}</b> on the current payroll; "
                 f"<b>{len(leavers)}</b> leaver(s) awaiting exit paperwork. "
                 f"Payroll data completeness <b>{pct}%</b> ({outstanding} outstanding).</p></div>")
        return text, "".join(P)
