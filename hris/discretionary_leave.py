"""
hris/discretionary_leave.py — the hard gate on DISCRETIONARY leave
(CFO directive 2026-09-10).

The problem it exists to solve, in the CFO's words: *"compassionate, study and
other leave are not protected by law the way sick and annual leave are, and
people are using them so they can keep their leave days."*

So the three company-discretion types — compassionate, study, special — stop
behaving like annual leave:

  * a set of compulsory questions, different per type;
  * a written motivation of at least 50 words (annual/sick stay reason-free —
    an employee is not obliged to justify a statutory right, Unami 2026-07-27,
    and nothing here changes that);
  * the applicant must answer why their own ANNUAL leave cannot be used, with
    their live balance printed in the question;
  * no back-dating, except a death or an emergency hospitalisation;
  * supporting proof up front for study / special, and a written undertaking
    of what proof is coming and when for compassionate;
  * a signed acknowledgement that they stay AT WORK until it is approved;
  * the CFO countersigns — the manager's approve button stays locked until
    then (reuses hris.exec_signoff_*).

Everything user-facing is defined HERE so the web form, the phone app and the
server validate against one list — a question added here appears on the form
without a second edit.

NOT touched, deliberately: annual, sick, hospitalisation, maternity, paternity,
td_deduct. Those are statutory or already evidenced, and putting an
interrogation in front of a legal right is a discrimination exposure.
"""
from __future__ import annotations

import datetime as _dt
import re
from decimal import Decimal
from typing import Any

# The three company-discretion types. Membership is the whole switch: a type
# not in here is completely unaffected by this module.
DISCRETIONARY_TYPES: tuple[str, ...] = ('compassionate', 'study', 'special')

# The written motivation floor (CFO: "minimum 50 words").
MIN_WORDS = 50

# Compassionate may be applied for after the fact ONLY for these events —
# nobody fills in a form before a death.
BACKDATE_EVENTS = ('death', 'hospitalisation')

# Shown at the top of the form, in red, and repeated in the confirmation.
NOTICE_TITLE = 'This is not normal leave'
NOTICE_BODY = (
    'Compassionate, study and special leave are granted at the company\'s '
    'discretion. They are not an entitlement you have earned and they are not '
    'a substitute for your annual leave. Every question below must be answered, '
    'your motivation must be at least 50 words, and the CFO signs it off after '
    'your manager. Do NOT stay away from work until you have the approval — an '
    'absence taken before approval is unauthorised and is treated as such.'
)

ACK_TEXT = ('I confirm the above is true, and I understand I remain at work '
            'until this leave is approved.')


def _q(key, label, kind='text', *, options=None, min_words=0, help='', depends_on=None):
    return {'key': key, 'label': label, 'kind': kind, 'options': options or [],
            'min_words': min_words, 'help': help, 'depends_on': depends_on}


# Asked for every discretionary type. The annual-balance challenge is the one
# that actually bites: it is the reason the leave is being asked for.
def _common_questions(annual_available: Decimal | float | None) -> list[dict]:
    bal = '—' if annual_available is None else f'{float(annual_available):g}'
    return [
        _q('annual_alternative',
           f'You have {bal} day(s) of annual leave available. Why can this not be '
           f'taken as annual leave?',
           'longtext', min_words=15,
           help='Annual leave is the default. This leave type is not a way to keep '
                'your annual days.'),
        _q('notice', 'When did you first know about this?', 'choice',
           options=['Today', 'Within the last week', 'One to four weeks ago',
                    'More than a month ago']),
        _q('cover', 'Who is covering your work while you are away, and have they '
                    'agreed?', 'text',
           help='Give the person\'s full name.'),
        _q('contact', 'Can you be reached on your phone while you are away?',
           'choice', options=['Yes', 'No — explain below']),
    ]


_TYPE_QUESTIONS: dict[str, list[dict]] = {
    'compassionate': [
        _q('event', 'What has happened?', 'choice',
           options=['Death', 'Funeral', 'Hospitalisation', 'Serious illness',
                    'Other family emergency']),
        _q('relationship', 'Your relationship to the person affected', 'choice',
           options=['Spouse', 'Child', 'Parent', 'Sibling', 'Grandparent',
                    'Parent-in-law', 'Other']),
        _q('relationship_other', 'If "Other", state the relationship', 'text',
           depends_on={'relationship': 'Other'}),
        _q('person', 'Name of the person affected', 'text'),
        _q('event_date', 'Date it happened', 'date'),
        _q('place', 'Where do you have to be? (town or village)', 'text'),
        _q('proof_promise',
           'What proof will you provide, and by what date?', 'text',
           help='For example: funeral programme by 20 September, or the death '
                'certificate once it is issued.'),
    ],
    'study': [
        _q('institution', 'Institution', 'text'),
        _q('programme', 'Programme or qualification', 'text'),
        _q('assessment', 'Exact exam or assessment dates', 'text'),
        _q('sponsored', 'Who is paying for this study?', 'choice',
           options=['Alpha Direct is sponsoring it', 'I am paying myself',
                    'Someone else is paying']),
        _q('job_link', 'How does this qualification help the job you do here?',
           'longtext', min_words=15),
        _q('days_before', 'How many study days have you already taken this year?',
           'text', help='Say "none" if this is your first.'),
    ],
    'special': [
        _q('circumstance', 'What exactly are the circumstances?', 'longtext',
           min_words=20),
        _q('why_not_other', 'Why does no other leave type fit this?', 'longtext',
           min_words=15),
        _q('one_off', 'Is this a one-off, or will it happen again?', 'choice',
           options=['One-off', 'It may happen again']),
        _q('proof_promise', 'What proof can you provide, and by when?', 'text'),
    ],
}

# Proof is demanded at the door for these. Compassionate is excused because the
# paperwork (death certificate, funeral programme) genuinely does not exist yet
# — hence its written proof_promise question instead.
PROOF_REQUIRED_AT_APPLY = ('study', 'special')

PROOF_LABEL = {
    'compassionate': 'Supporting document (funeral programme, medical letter) — '
                     'attach it now if you have it',
    'study': 'Proof of study — enrolment letter or exam timetable',
    'special': 'Supporting document for the circumstances',
}


def is_discretionary(type_code: str) -> bool:
    return (type_code or '').lower().strip() in DISCRETIONARY_TYPES


def questions_for(type_code: str, annual_available=None) -> list[dict]:
    """Every question for this type, common ones first, in display order."""
    code = (type_code or '').lower().strip()
    if not is_discretionary(code):
        return []
    return _common_questions(annual_available) + list(_TYPE_QUESTIONS.get(code, []))


def word_count(text: str) -> int:
    """Words, the way a person counts them — runs of letters/digits."""
    return len(re.findall(r"[A-Za-z0-9']+", text or ''))


def _answer_required(spec: dict, answers: dict) -> bool:
    """A question is skipped only when its depends_on condition is unmet."""
    dep = spec.get('depends_on')
    if not dep:
        return True
    return all(str(answers.get(k, '')).strip() == v for k, v in dep.items())


def validate(type_code: str, *, answers: dict, reason: str, ack: bool,
             start_date: _dt.date, today: _dt.date,
             annual_available=None, has_document: bool = False) -> list[str]:
    """Every reason this application cannot be filed, in plain English.

    Returns [] when it passes. All problems come back at once so the applicant
    fixes the form in one pass instead of being drip-fed errors.
    """
    code = (type_code or '').lower().strip()
    if not is_discretionary(code):
        return []

    errors: list[str] = []
    answers = answers if isinstance(answers, dict) else {}

    for spec in questions_for(code, annual_available):
        if not _answer_required(spec, answers):
            continue
        val = str(answers.get(spec['key'], '') or '').strip()
        if not val:
            errors.append(f'Answer required: {spec["label"]}')
            continue
        if spec['kind'] == 'choice' and spec['options'] and val not in spec['options']:
            errors.append(f'Pick one of the listed answers for: {spec["label"]}')
        if spec['kind'] == 'date':
            try:
                _dt.date.fromisoformat(val)
            except ValueError:
                errors.append(f'Give a real date for: {spec["label"]}')
        if spec['min_words'] and word_count(val) < spec['min_words']:
            errors.append(f'"{spec["label"]}" needs at least {spec["min_words"]} '
                          f'words — you wrote {word_count(val)}.')

    words = word_count(reason)
    if words < MIN_WORDS:
        errors.append(f'Your written motivation must be at least {MIN_WORDS} words. '
                      f'You wrote {words}. Explain the circumstances properly — '
                      f'this leave is granted at the company\'s discretion.')

    if not ack:
        errors.append(f'You must tick: "{ACK_TEXT}"')

    # No back-dating. A death or an emergency hospitalisation is the only
    # honest exception — everything else was known about in advance.
    if start_date < today:
        event = str(answers.get('event', '') or '').strip().lower()
        excused = code == 'compassionate' and any(e in event for e in BACKDATE_EVENTS)
        if not excused:
            errors.append(
                'This leave cannot start in the past. It must be applied for and '
                'approved before you are away. (Only a death or an emergency '
                'hospitalisation can be reported after the fact.)')

    if code in PROOF_REQUIRED_AT_APPLY and not has_document:
        errors.append(f'Attach the supporting document — {PROOF_LABEL[code]}.')

    return errors


# Not a question — the annual balance the applicant was holding when they
# applied, frozen onto the record. The approver has to see the days this leave
# type let them keep, and by the time they look the live balance has moved.
ANNUAL_AT_APPLY_KEY = '_annual_at_apply'


def display_answers(type_code: str, answers: dict) -> list[dict]:
    """[{label, value}] for the approver, the CFO page and the email."""
    answers = answers if isinstance(answers, dict) else {}
    # Re-render the annual-balance question with the figure the applicant was
    # actually shown, not a dash and not today's number.
    try:
        frozen = float(answers.get(ANNUAL_AT_APPLY_KEY))
    except (TypeError, ValueError):
        frozen = None
    out = []
    for spec in questions_for(type_code, frozen):
        val = str(answers.get(spec['key'], '') or '').strip()
        if not val:
            continue
        out.append({'label': spec['label'], 'value': val})
    return out


def signoff_reason(type_code: str, employee_name: str, days) -> str:
    """The one-line 'why is this on the CFO's desk' (ExecSignoff.reason, 200 chars)."""
    name = (type_code or 'discretionary').capitalize()
    return (f'{name} leave is discretionary, not an entitlement — '
            f'{employee_name} asked for {float(days or 0):g} day(s). '
            f'CFO signature required before the manager can approve.')[:200]


def approval_blocked_reason(lr) -> str | None:
    """Refuse to approve discretionary leave that carries no CFO signature.

    Belt to the ExecSignoff braces. `blocking_signoff` can only see a row that
    was actually written — if raising it ever fails, the request would look
    like ordinary leave in the manager's queue and the control would have
    switched itself off with nobody the wiser. This asks the opposite
    question: is there a SIGNED countersignature? No signature, no approval.
    """
    code = lr.leave_type.code if lr.leave_type_id else ''
    if not is_discretionary(code):
        return None
    from hris.exec_signoff_models import ExecSignoff
    if ExecSignoff.objects.filter(module=ExecSignoff.Module.LEAVE,
                                  object_id=lr.pk,
                                  status=ExecSignoff.Status.APPROVED).exists():
        return None
    return ('This is discretionary leave, so the CFO signs it off before it can '
            'be approved. There is no signature on it yet.')


def history_for(profile, type_code: str, *, months: int = 12) -> dict[str, Any]:
    """How often this person has used THIS leave type in the last 12 months.

    Shown on the form (so they know we can see it), on the approver's queue row
    and on the CFO's sign-off page — the pattern is the point, not one request.
    """
    from django.utils import timezone
    from hris.models import LeaveRequest

    since = timezone.localdate() - _dt.timedelta(days=months * 30)
    qs = (LeaveRequest.objects
          .filter(profile=profile,
                  leave_type__code__iexact=(type_code or ''),
                  start_date__gte=since,
                  status__in=[LeaveRequest.Status.APPROVED,
                              LeaveRequest.Status.PENDING])
          .order_by('start_date'))
    rows = list(qs)
    return {
        'count': len(rows),
        'days': float(sum(Decimal(str(r.days or 0)) for r in rows)),
        'months': months,
        'recent': [{'start': str(r.start_date), 'end': str(r.end_date),
                    'days': float(r.days or 0), 'status': r.status}
                   for r in rows[-5:]],
    }
