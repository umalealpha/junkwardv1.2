"""
hris/disciplinary_service.py

Business logic + authority for the disciplinary chain. Mirrors
leave_encash_service: transitions are @transaction.atomic and lock the row.

Chain:  raise (manager) -> inquiry issued to the employee -> employee responds
(or the deadline passes) -> HR review (Unami) -> [CFO sign-off if suspension /
dismissal] -> ISSUED. HR or CFO may reject at their stage. Segregation of
duties: the manager who raised a case cannot also HR-review it.

NATURAL JUSTICE (CFO directive 2026-08-11): hr_review REFUSES to issue until the
employee has been served an inquiry AND has either answered or run out of time.
Rejecting a case is always allowed — that outcome favours the employee, so it
needs no hearing.
"""
from __future__ import annotations

import os
from datetime import datetime, time

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date

from core.hris_access import _local_part
from .disciplinary_models import (DEFAULT_RESPONSE_DAYS, MIN_RESPONSE_DAYS,
                                  DisciplinaryCase)
# Reuse the EXACT CFO/HR predicates the encashment chain uses — one definition
# of "who is the CFO / who is HR" across HRIS approvals.
from .leave_encash_service import is_cfo, is_hr

MIN_ALLEGATION_WORDS = 50


def _word_count(s: str) -> int:
    return len([w for w in (s or '').split() if w])


def is_manager(user) -> bool:
    """A people-manager who may raise a disciplinary case. HR and the CFO always
    can. Otherwise: a title containing 'manager' (UserProfile.title or the
    employee job title), or an email local-part in the env allowlist
    OMNI_DISCIPLINARY_RAISERS (add people without a deploy)."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if is_hr(user) or is_cfo(user):
        return True
    extra = {p.strip().lower() for p in (os.environ.get('OMNI_DISCIPLINARY_RAISERS') or '').split(',') if p.strip()}
    if _local_part(getattr(user, 'email', None)) in extra:
        return True
    profile = getattr(user, 'profile', None)
    title = (getattr(profile, 'title', '') or '').lower()
    emp = getattr(user, 'employee_record', None)
    job = (getattr(emp, 'job_title', '') or '').lower()
    return 'manager' in title or 'manager' in job


def can_raise(user) -> bool:
    return is_manager(user)


def can_view_all(user) -> bool:
    """HR + CFO see every case; managers only their own (enforced in the view)."""
    return is_hr(user) or is_cfo(user)


def employee_for(user):
    """The caller's OWN payroll.Employee, via either of the two links HRIS
    self-service uses (Employee.user, or the legacy UserProfile.employee).
    Returns None when neither link is paired — a login with no staff record.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    emp = getattr(user, 'employee_record', None)
    if emp is None:
        emp = getattr(getattr(user, 'userprofile', None), 'employee', None)
    return emp


def is_subject(case, user) -> bool:
    """Whether `user` IS the employee this case is about.

    Keyed on the employee PRIMARY KEY and nothing else. Never on name or email:
    those have near-duplicates in this company (Pako Kago / Kago Tshutlhedi) and
    a name match would hand one person another's disciplinary file. A login with
    no employee record is REFUSED, never defaulted in — see the standing rule
    that an identity check must be a positive match with no fallback answer.
    """
    emp = employee_for(user)
    if emp is None or getattr(emp, 'id', None) is None:
        return False
    if case is None or getattr(case, 'subject_employee_id', None) is None:
        return False
    return emp.id == case.subject_employee_id


def can_issue_inquiry(case, user) -> bool:
    """Who may serve the inquiry letter: HR, the CFO, or the manager who raised
    the case. The subject can never serve it on themselves.

    A REJECTED case is included for the CFO only. Without it, the gate on
    cfo_override would be a dead end: the CFO would be told to ask the employee
    first with no way to do so, leaving forcing past the gate as the only option.
    Serving the inquiry revives the case; the rejection history is untouched.
    """
    if case.inquiry_issued_at:
        return False
    if is_subject(case, user):
        return False
    if case.status == DisciplinaryCase.Status.REJECTED:
        return is_cfo(user)
    if case.status == DisciplinaryCase.Status.ISSUED:
        # Inviting a late answer on a decided case — HR or the CFO only. Used for
        # warnings issued before this stage existed.
        return is_hr(user) or is_cfo(user)
    if case.status != DisciplinaryCase.Status.PENDING_HR:
        return False
    if is_hr(user) or is_cfo(user):
        return True
    return (can_raise(user)
            and (getattr(user, 'email', '') or '').lower() == (case.raised_by_email or '').lower())


def can_act_now(case, user) -> bool:
    """Whether `user` can action the case at its current stage (drives the UI
    button and any approvals queue)."""
    email = (getattr(user, 'email', '') or '').lower()
    if case.status in (DisciplinaryCase.Status.PENDING_HR,
                       DisciplinaryCase.Status.PENDING_RESPONSE):
        # HR can only take the case forward once natural justice is satisfied —
        # the employee answered, or their deadline ran out. Before that the only
        # action available is "issue the inquiry".
        return (is_hr(user) and email != (case.raised_by_email or '').lower()
                and case.natural_justice_satisfied)
    if case.status == DisciplinaryCase.Status.PENDING_CFO:
        return is_cfo(user)
    return False


@transaction.atomic
def raise_case(*, raised_by, subject_employee, category, incident_date,
               allegation, proposed_action=''):
    if not can_raise(raised_by):
        raise ValidationError('You are not authorised to raise a disciplinary case.')
    if subject_employee is None:
        raise ValidationError('Select the employee the case is about.')
    if category not in DisciplinaryCase.Category.values:
        raise ValidationError('Choose a valid disciplinary category.')
    if not incident_date:
        raise ValidationError('Enter the date the incident occurred.')
    # The API sends incident_date as an ISO string; assigning it straight to the
    # DateField leaves case.incident_date a str in memory (Django only coerces on
    # DB fetch), and the response serializer then calls .isoformat() on a str →
    # HTTP 500 AFTER the row is already committed. Coerce to a real date here so
    # every downstream consumer of the returned instance is correct.
    if isinstance(incident_date, str):
        parsed = parse_date(incident_date.strip())
        if parsed is None:
            raise ValidationError('Enter a valid incident date.')
        incident_date = parsed
    allegation = (allegation or '').strip()
    wc = _word_count(allegation)
    if wc < MIN_ALLEGATION_WORDS:
        raise ValidationError(f'Describe the incident in at least {MIN_ALLEGATION_WORDS} words '
                              f'(you wrote {wc}).')
    raiser_emp = getattr(raised_by, 'employee_record', None)
    if raiser_emp and getattr(raiser_emp, 'id', None) == getattr(subject_employee, 'id', None):
        raise ValidationError('You cannot raise a disciplinary case about yourself.')

    case = DisciplinaryCase(
        subject_employee=subject_employee,
        subject_name=(getattr(subject_employee, 'full_name', '') or ''),
        raised_by=raised_by,
        raised_by_email=(getattr(raised_by, 'email', '') or '').lower(),
        category=category,
        incident_date=incident_date,
        allegation=allegation,
        proposed_action=(proposed_action or '').strip(),
        status=DisciplinaryCase.Status.PENDING_HR,
    )
    case.save(audit_user=raised_by)
    return case


# What inquiry_sent_to records when the invitation is raised in omni only. It is
# a truthful statement of how the employee was served, not an address — the file
# must never imply a letter went out when none did.
SERVED_IN_OMNI_ONLY = 'in omni only — no email sent'


@transaction.atomic
def issue_inquiry(case_id, user, *, deadline=None, send_email=True):
    """Serve the invitation to respond: record that the employee was told the
    allegation and given a deadline, and move the case to PENDING_RESPONSE.

    The email itself is sent by the caller (disciplinary_notify.notify_inquiry)
    so a mail failure cannot silently leave the case marked as served — the view
    rolls the state back if the letter does not go out.

    `send_email=False` raises the invitation IN OMNI ONLY (CFO instruction
    2026-08-12): the employee sees it and the response box on their own profile,
    and no letter is sent. inquiry_sent_to then records exactly that instead of an
    address, so nobody reading the file can mistake it for a letter that was sent.
    The employee's email address is not required in that case.

    An ISSUED case may also be served (CFO decision 2026-08-12), for the two live
    warnings that were decided before this stage existed. The status is left ALONE
    in that case: the decision stands and inviting a late answer must not un-issue
    a warning the employee has already received. Their reply lands on the file
    flagged as having arrived after the decision, which is the honest record.
    """
    case = DisciplinaryCase.objects.select_for_update().get(id=case_id)
    if case.status not in (DisciplinaryCase.Status.PENDING_HR,
                           DisciplinaryCase.Status.REJECTED,
                           DisciplinaryCase.Status.ISSUED):
        raise ValidationError('An inquiry can only be issued while the case is awaiting HR '
                              'review, to revive a rejected case, or to invite a late '
                              'response on a case already issued.')
    if case.inquiry_issued_at:
        raise ValidationError('An inquiry has already been issued on this case.')
    if not can_issue_inquiry(case, user):
        raise ValidationError('Only HR, the final reviewer, or the manager who raised '
                              'this case can issue the inquiry.')

    if isinstance(deadline, str) and deadline.strip():
        parsed = parse_date(deadline.strip())
        if parsed is None:
            raise ValidationError('Enter a valid response deadline.')
        deadline = parsed
    if not deadline:
        deadline = case.default_deadline

    today = timezone.localdate()
    if (deadline - today).days < MIN_RESPONSE_DAYS:
        raise ValidationError(
            f'Give the employee at least {MIN_RESPONSE_DAYS} days to respond '
            f'(the default is {DEFAULT_RESPONSE_DAYS}).')

    to_email = (getattr(case.subject_employee, 'email', '') or '').strip().lower()
    if send_email and not to_email:
        raise ValidationError(
            'This employee has no email address on their record, so the inquiry cannot be '
            'emailed. Raise it in omni only instead, or add their work email in HRIS first.')

    case.inquiry_issued_at = timezone.now()
    case.inquiry_issued_by = user
    case.inquiry_sent_to = (to_email[:254] if send_email else SERVED_IN_OMNI_ONLY)
    case.response_deadline = deadline
    # An issued case keeps its status: the outcome already stands. Anything else
    # would reverse a decision the employee has been given, by sending a letter.
    if case.status != DisciplinaryCase.Status.ISSUED:
        case.status = DisciplinaryCase.Status.PENDING_RESPONSE
    case.save(audit_user=user)
    return case


@transaction.atomic
def record_response(case_id, user, *, response=''):
    """The employee's own explanation, written by the employee themselves.

    Only the SUBJECT of the case may call this, and only while the case is
    waiting on them. One response per case — it is a legal record, not a draft.
    A late answer is still accepted while the case is open: refusing it would be
    the very unfairness this stage exists to prevent.

    An ISSUED case is included when an inquiry was served on it — the case of a
    warning decided before this stage existed, where the employee is invited to
    answer afterwards. Their words go on the file; the outcome is untouched.
    """
    case = DisciplinaryCase.objects.select_for_update().get(id=case_id)
    if not is_subject(case, user):
        raise ValidationError('You can only respond to a case about yourself.')
    invited_late = (case.status == DisciplinaryCase.Status.ISSUED
                    and case.inquiry_issued_at is not None)
    if case.status != DisciplinaryCase.Status.PENDING_RESPONSE and not invited_late:
        raise ValidationError('This case is not open for your response.')
    if case.employee_responded_at:
        raise ValidationError('You have already responded to this case. Speak to HR if you '
                              'need to add anything further.')
    response = (response or '').strip()
    if not response:
        raise ValidationError('Write your explanation before submitting.')

    case.employee_response = response
    case.employee_responded_at = timezone.now()
    if not invited_late:
        # Back onto HR's queue now that the employee has been heard. A decided
        # case stays decided — answering afterwards does not un-issue a warning.
        case.status = DisciplinaryCase.Status.PENDING_HR
    case.save(audit_user=user)
    return case


@transaction.atomic
def record_response_on_behalf(case_id, user, *, response='', received_on=None):
    """HR captures a reply that arrived OUTSIDE omni — emailed to Unami, or a
    signed letter handed in. Without this the reply stays in a mailbox, which is
    the problem this whole stage exists to fix.

    HR / the final reviewer only: the manager who raised the case must not author
    the defence. The case records WHO captured it (response_recorded_by), so the
    file always shows this was HR's transcription and not the employee's own
    submission — a control that could be satisfied by HR typing anything, with no
    trace of that, would be worse than no control.
    """
    case = DisciplinaryCase.objects.select_for_update().get(id=case_id)
    if not (is_hr(user) or is_cfo(user)):
        raise ValidationError('Only HR can record a response received outside omni.')
    # An ISSUED case is also allowed (CFO decision 2026-08-11). A reply that
    # arrives after the outcome was recorded must still land on the file — the
    # alternative is that it stays in a mailbox, which is the whole problem. It
    # does NOT reopen or alter the decision: the status is left alone and
    # response_arrived_after_decision makes the true sequence readable.
    if case.status not in (DisciplinaryCase.Status.PENDING_RESPONSE,
                           DisciplinaryCase.Status.ISSUED):
        raise ValidationError('This case is not awaiting the employee response.')
    if case.employee_responded_at:
        raise ValidationError('A response is already recorded on this case.')
    response = (response or '').strip()
    if not response:
        raise ValidationError('Enter the response the employee gave.')

    when = timezone.now()
    if received_on:
        if isinstance(received_on, str):
            parsed = parse_date(received_on.strip())
            if parsed is None:
                raise ValidationError('Enter a valid date for when the response was received.')
            received_on = parsed
        if received_on > timezone.localdate():
            raise ValidationError('The response cannot have been received in the future.')
        when = timezone.make_aware(datetime.combine(received_on, time.min))

    # A late reply filed against a decided case must NEVER re-open it: that would
    # un-issue a warning the employee has already been given, and would let the
    # decision be quietly reversed by filing a document. The outcome stands; only
    # the record gets more complete.
    if case.status == DisciplinaryCase.Status.ISSUED:
        # Compared as DATES, not datetimes. A reply received the same day the case
        # was decided is legitimate (issued 09:00, reply 15:00) and a midnight
        # timestamp would otherwise read as "before the decision" and be refused.
        # The consequence is that a same-day reply is not FLAGGED as late either:
        # we only know the date it arrived, so asserting it came after the
        # decision would be a claim the record cannot support. Both timestamps
        # stay visible for anyone reading the file.
        issued_date = timezone.localtime(case.issued_at).date()
        if timezone.localtime(when).date() < issued_date:
            raise ValidationError(
                f'This case was already issued on {issued_date:%d %b %Y}. A reply cannot be '
                'dated before that — enter the date it actually arrived.')
    else:
        # Still open — the employee has now been heard, so it goes back to HR.
        case.status = DisciplinaryCase.Status.PENDING_HR

    case.employee_response = response
    case.employee_responded_at = when
    case.response_recorded_by = user
    case.save(audit_user=user)
    return case


@transaction.atomic
def hr_review(case_id, user, *, notes=''):
    case = DisciplinaryCase.objects.select_for_update().get(id=case_id)
    if case.status not in (DisciplinaryCase.Status.PENDING_HR,
                           DisciplinaryCase.Status.PENDING_RESPONSE):
        raise ValidationError('This case is not awaiting HR review.')
    if not is_hr(user):
        raise ValidationError('Only HR can review at this stage.')
    if (getattr(user, 'email', '') or '').lower() == (case.raised_by_email or '').lower():
        raise ValidationError('You raised this case, so you cannot also review it (segregation of duties).')
    # NATURAL JUSTICE GATE (CFO directive 2026-08-11). No outcome may be recorded
    # against an employee who was never invited to answer.
    if not case.inquiry_issued_at:
        raise ValidationError(
            'Issue the inquiry to the employee first. A disciplinary outcome cannot be '
            'recorded before the employee has been told the allegation and given a '
            'chance to respond.')
    if not case.natural_justice_satisfied:
        raise ValidationError(
            f'The employee has until {case.response_deadline:%d %b %Y} to respond. Wait for '
            'their explanation or for that date to pass before reviewing.')
    case.hr_reviewer = user
    case.hr_reviewed_at = timezone.now()
    if notes:
        case.decision_notes = (notes or '').strip()
    if case.needs_cfo:
        case.status = DisciplinaryCase.Status.PENDING_CFO
    else:
        case.status = DisciplinaryCase.Status.ISSUED
        case.issued_at = timezone.now()
    case.save(audit_user=user)
    return case


@transaction.atomic
def cfo_signoff(case_id, user):
    case = DisciplinaryCase.objects.select_for_update().get(id=case_id)
    if case.status != DisciplinaryCase.Status.PENDING_CFO:
        raise ValidationError('This case is not awaiting final review.')
    if not is_cfo(user):
        raise ValidationError('Only the authorised final reviewer signs off at this stage.')
    case.cfo_approver = user
    case.cfo_approved_at = timezone.now()
    case.status = DisciplinaryCase.Status.ISSUED
    case.issued_at = timezone.now()
    case.save(audit_user=user)
    return case


@transaction.atomic
def cfo_override(case_id, user, *, notes='', issue_unheard_reason=''):
    """The CFO overturns a rejection and issues the case anyway.

    A rejection is otherwise terminal, and written warnings never reach the CFO
    at all — only suspensions and dismissals do. So there was no way to act on a
    case HR had turned down, which is what the CFO asked for on 2026-08-12.

    Three things are deliberate:
      * only the CFO can do this — not HR, not the raiser, not a manager;
      * a substantive reason is compulsory. Overturning HR without a recorded
        reason is exactly the decision an employee would later challenge, and a
        one-word note is no better than silence;
      * the rejection is NOT erased. rejected_by / rejected_at / rejected_stage
        and the rejecter's decision_notes all stay, so the file reads honestly:
        HR rejected it for this reason, the CFO overturned it for that reason.
    """
    case = DisciplinaryCase.objects.select_for_update().get(id=case_id)
    if case.status != DisciplinaryCase.Status.REJECTED:
        raise ValidationError(
            f'Only a rejected case can be overturned. Current status: '
            f'{case.get_status_display()}.'
        )
    if not is_cfo(user):
        raise ValidationError('Only the CFO can overturn a rejected disciplinary case.')
    reason = (notes or '').strip()
    if _word_count(reason) < 10:
        raise ValidationError(
            'Give a reason of at least 10 words for overturning the rejection. '
            'It goes on the employee file and is the justification if the case '
            'is ever challenged.'
        )
    # NATURAL JUSTICE (CFO decision 2026-08-12). This path also ISSUES a case, so
    # leaving it ungated would make the gate on hr_review decorative — the very
    # failure mode this control exists to prevent. Default is closed. The CFO may
    # force past it, but only deliberately and only on the record: forcing takes a
    # second, separate reason and permanently stamps the file. A control that can
    # be walked around silently is worse than no control.
    if not case.natural_justice_satisfied:
        forced = (issue_unheard_reason or '').strip()
        if not forced:
            raise ValidationError(
                'This employee has not been asked for their side, so issuing now can be '
                'challenged. Either ask them first (the case goes back to "awaiting HR '
                'review" so the inquiry can be served), or state your reason for issuing '
                'without hearing them — it is recorded permanently on the file.')
        if _word_count(forced) < 10:
            raise ValidationError(
                'Give a reason of at least 10 words for issuing without hearing the '
                'employee. This is the single most challengeable thing on a disciplinary '
                'file and a one-word note is no better than silence.')
        case.unheard_issue_reason = forced
        case.unheard_issued_by = user

    case.override_reason = reason
    case.cfo_approver = user
    case.cfo_approved_at = timezone.now()
    case.status = DisciplinaryCase.Status.ISSUED
    case.issued_at = timezone.now()
    case.save(audit_user=user)
    return case


def can_cfo_override(case, user) -> bool:
    """Drives the override button. Kept out of can_act_now on purpose so
    rejected cases do not reappear in everybody's approvals queue."""
    return (case.status == DisciplinaryCase.Status.REJECTED) and is_cfo(user)


@transaction.atomic
def reject(case_id, user, *, notes=''):
    case = DisciplinaryCase.objects.select_for_update().get(id=case_id)
    if case.status not in DisciplinaryCase.OPEN_STATUSES:
        raise ValidationError('This case is already closed.')
    # Positive match on the CFO stage — anything else is an HR-stage rejection.
    # (Written this way round so PENDING_RESPONSE cannot fall through to 'cfo'
    # and demand CFO authority to drop a case still sitting with the employee.)
    stage = 'cfo' if case.status == DisciplinaryCase.Status.PENDING_CFO else 'hr'
    if stage == 'hr' and not is_hr(user):
        raise ValidationError('Only HR can reject at this stage.')
    if stage == 'cfo' and not is_cfo(user):
        raise ValidationError('Only the authorised final reviewer can reject at this stage.')
    case.status = DisciplinaryCase.Status.REJECTED
    case.rejected_by = user
    case.rejected_at = timezone.now()
    case.rejected_stage = stage
    case.decision_notes = (notes or '').strip()
    case.save(audit_user=user)
    return case
