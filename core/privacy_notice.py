"""core/privacy_notice.py — the one-page staff privacy notice shown as a
login pop-up, its version, and helpers to record + report who has signed.

CFO directive 2026-07-09: every staff member signs a one-page privacy notice
before using Omni. Reason: Omni's task board records when people complete their
tasks and whether they are on time (interaction time + completion rate). Under
the Botswana Data Protection Act 2024 that is employee monitoring, so staff must
be told and must consent before it is switched on for them.

Bump NOTICE_VERSION whenever the wording materially changes — everyone is then
asked to sign again (the pop-up re-appears until they do). Keep the body plain:
a non-technical reader must understand every line.
"""
from __future__ import annotations

from django.contrib.auth.models import User

# ISO date = the version. Bump on any material wording change -> re-consent.
NOTICE_VERSION = "2026-09-01"
NOTICE_TITLE = "Omni — Declaration of Access, Fair Use & Data Protection Undertaking"

# Acceptance wording shown by the tick-box and the button. Kept here, with the
# document, so the wording and the recorded acceptance stay together. The modal
# falls back to its old "read and understood" text if these are ever absent.
ACK_CHECKBOX_LABEL = (
    "I have read this declaration, including the time-tracking integrity notice, and "
    "confirm the working time I record is genuine."
)
ACK_CTA_LABEL = "I Accept"

# One page, rendered as trusted HTML inside the pop-up (our own constant — never
# user input). Part A is HR's official access & data-protection undertaking
# (HRIS Declaration, ref ADI/HC/HRIS/2026, verbatim). Part B is the standing
# notice that Omni records task completion — the Data Protection Act 2024
# monitoring disclosure carried over from the earlier notice, kept so that
# transparency disclosure is not lost.
NOTICE_HTML = """
<div style="border:2px solid #1D3270;border-left:6px solid #F47C20;border-radius:8px;padding:14px 16px;margin:0 0 20px 0;background:#F7F9FC">
  <p style="margin:0 0 8px 0;color:#1D3270;font-size:16px;font-weight:700">&#9201; Time &amp; Attendance Integrity &mdash; please read carefully</p>
  <p style="margin:0 0 10px 0">Alpha Direct records working time through Time Doctor. That time drives real
  decisions: pay, leave, and how we share workload and support each other. It has to be honest.</p>
  <p style="margin:0 0 8px 0">We have found that a small number of staff artificially inflated their tracked
  hours &mdash; for example by resting a weight or object on a keyboard key to fake activity while away from the
  desk. We want to be open and fair with everyone about this:</p>
  <ul style="margin:8px 0 10px 18px;padding:0">
    <li style="margin-bottom:6px">We have put safeguards in place that detect artificially inflated activity.
    We do not describe how, so that it stays effective.</li>
    <li style="margin-bottom:6px">Deliberately faking, inflating or manipulating your tracked time, in any way,
    is dishonesty and a misuse of company systems.</li>
    <li style="margin-bottom:6px">It will be treated as <b>serious misconduct</b> under Alpha Direct&rsquo;s
    Conditions of Service and disciplinary code, and is grounds for <b>disciplinary action up to and including
    dismissal</b>, consistent with the <b>Employment Act (Cap. 47:01) of Botswana</b>. This is separate from
    recovering any pay wrongly claimed.</li>
  </ul>
  <p style="margin:0 0 10px 0">This is not aimed at honest work. Its purpose is to protect the many people who
  put in their hours properly, so no one is disadvantaged by those who don&rsquo;t. If you genuinely cannot work
  your hours on a given day, please say so through the normal &ldquo;explain your day&rdquo; process &mdash; that
  is exactly what it is for.</p>
  <p style="margin:0;color:#1D3270"><i>By accepting below, you confirm that you understand this and that the
  working time you record is genuine.</i></p>
</div>

<p>By ticking the box and clicking <b>&ldquo;I Accept&rdquo;</b>, I confirm and
undertake the following:</p>

<p><b>1. Authorised Use Only</b></p>
<p>I will access Omni solely for legitimate work purposes authorised by Alpha
Direct Insurance Co. (Pty) Ltd. I will not access, view, or extract records of
employees outside my authorised scope of duty.</p>

<p><b>2. Data Protection Compliance</b></p>
<p>I will handle all personal information in Omni in accordance with the Botswana
Data Protection Act. I will not disclose, share, copy, download, or transmit any
personal information (including but not limited to salary, ID/Omang numbers,
medical data, disciplinary records) to any third party or unauthorised person,
internal or external. I will not use Omni data for any purpose other than the
specific task assigned to me.</p>

<p><b>3. Accuracy and Honesty</b></p>
<p>Any information I input into Omni is true and accurate to the best of my
knowledge. I will report any error, discrepancy, or suspected data breach to HC
immediately upon discovery.</p>

<p><b>4. Credential Security</b></p>
<p>My login credentials are personal to me. I will not share my password or allow
another person to access Omni under my login.</p>

<p><b>5. Consequences of Breach</b></p>
<p>I understand that any breach of this declaration constitutes misconduct under
Alpha Direct's Conditions of Service and disciplinary code, and may result in
disciplinary action, independent of any civil or criminal liability arising under
the Data Protection Act.</p>

<p><b>Declaration</b></p>
<p>I have read and understood this declaration. I accept full responsibility for
my conduct on this system.</p>

<hr>

<p><b>Notice &mdash; how Omni uses your work information (please read)</b></p>
<p>Omni records your work tasks: what is assigned to you, when it is due, and when
you mark it done, including the time spent and whether it was on time. Under
Botswana's Data Protection Act (2024) this counts as monitoring at work, so you
have the right to be told &mdash; that is what this note does. We use it to help
teams support each other, not as a trap. You can ask to see the information Omni
holds about you and ask us to correct anything that is wrong. For any question,
speak to HR (Dorothy or Unami).</p>
"""


def has_acknowledged(user, version: str = NOTICE_VERSION) -> bool:
    """True if this user has already signed the given notice version."""
    from core.models import PrivacyNoticeAcknowledgement
    return PrivacyNoticeAcknowledgement.objects.filter(
        user=user, version=version).exists()


def record_ack(user, version: str = NOTICE_VERSION):
    """Idempotently record that `user` signed `version` (stamps their name)."""
    from core.models import PrivacyNoticeAcknowledgement
    obj, _ = PrivacyNoticeAcknowledgement.objects.get_or_create(
        user=user, version=version,
        defaults={"signed_name": (user.get_full_name() or user.username)[:200]})
    return obj


def outstanding_users(version: str = NOTICE_VERSION):
    """Active individual staff who have NOT signed the current notice.

    'Staff' = active users with a real Alpha Direct group human email (the same
    rule the staff login uses — excludes shared/service mailboxes). Returned as
    a list of User ordered by name, for the HR morning email."""
    from core.models import PrivacyNoticeAcknowledgement
    from core.staff_login_views import _is_human_ad_email

    signed = set(
        PrivacyNoticeAcknowledgement.objects.filter(version=version)
        .values_list("user_id", flat=True))
    out = []
    for u in (User.objects.filter(is_active=True).exclude(email="")
              .order_by("first_name", "last_name", "username")):
        if u.id in signed:
            continue
        if not _is_human_ad_email(u.email):
            continue
        out.append(u)
    return out
