"""Authority to Recruit — the signatories actually get told (CFO 2026-08-11).

The five-signature instrument shipped with no notification of any kind. Both
records on prod on 11-Aug-2026 (ARG-2026-0001 Kago Tshutlhedi, ATR-2026-0001
Bokang Bobby Mothibi) were still 0-of-5 signed because nobody was ever told there
was anything to sign.

Two of these tests exist to catch silent failures rather than loud ones:

* `test_ceo_and_coo_are_really_on_the_message` — the CEO and COO are on
  `core.notifications._NEVER_CC`. Without `allow_named_exec=True` they are stripped
  from the recipients while the send still reports success, so 2 of 5 signatories
  never hear about a document they must sign. This is the same bug that dropped
  Arun from an IT ticket on 2026-08-10. Remove the flag and this test goes red.

* `test_email_carries_no_pay_figures` — the authority carries an individual's full
  package and only the five may see it. Putting the number in the email widens that
  boundary to every inbox and forward. Add the salary to the body and this goes red.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.test import override_settings
from rest_framework.test import APITestCase

from recruitment import authority_notify
from recruitment.models import AuthorityToRecruit

CEO = 'aiyer@alphadirect.co.bw'
COO = 'arjuniyer@alphadirect.co.bw'
HC = 'ubutale@alphadirect.co.bw'
HRBP = 'dikgopoleng@alphadirect.co.bw'
CFO = 'pganesharajah@alphadirect.co.bw'

LINES = [
    {'sn': 1, 'item': 'Basic salary', 'monthly': '8000', 'annual': '96000', 'note': ''},
    {'sn': 2, 'item': 'Performance incentive', 'monthly': '2000', 'annual': '24000', 'note': ''},
]


def _atr(**kw):
    base = dict(
        kind=AuthorityToRecruit.Kind.RECRUIT,
        person_name='Lorato Molosiwa',
        position='Manager - Sales & Operations',
        entity='UniCoin',
        salary_lines=LINES,
        quoted_ctc_monthly=Decimal('10000'),
        quoted_ctc_annual=Decimal('120000'),
    )
    base.update(kw)
    return AuthorityToRecruit.objects.create(**base)


def _recipients(msg):
    return {a.lower() for a in (list(msg.to) + list(msg.cc))}


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class NotifyTests(APITestCase):
    def setUp(self):
        mail.outbox = []

    # ── the silent-strip bug ────────────────────────────────────────────────
    def test_ceo_and_coo_are_really_on_the_message(self):
        """They are on the never-auto-CC list; without allow_named_exec they are
        dropped silently and the send still looks successful. One private email
        per recipient now (CFO 2026-08-18), so check across the batch."""
        a = _atr()
        sent = authority_notify.notify_raised(a)
        self.assertTrue(mail.outbox, 'no email was sent at all')
        self.assertTrue(sent)
        everyone = set().union(*[_recipients(m) for m in mail.outbox])
        self.assertIn(CEO, everyone, 'the CEO was stripped from a document he must sign')
        self.assertIn(COO, everyone, 'the COO was stripped from a document he must sign')

    def test_all_five_signatories_are_notified_when_raised(self):
        a = _atr()
        authority_notify.notify_raised(a)
        # One email per signatory — a login-free button token is a credential and
        # must never travel to anyone but its owner (CFO 2026-08-18).
        self.assertEqual(len(mail.outbox), 5, 'expected one private email per signatory')
        for m in mail.outbox:
            self.assertEqual(len(m.to), 1, 'a signature email must go to one recipient only')
        everyone = set().union(*[_recipients(m) for m in mail.outbox])
        for addr in (CEO, COO, HC, HRBP, CFO):
            self.assertIn(addr, everyone, f'{addr} was not told')

    # ── confidentiality ─────────────────────────────────────────────────────
    def test_email_carries_no_pay_figures(self):
        a = _atr()
        authority_notify.notify_raised(a)
        body = mail.outbox[0].body + ''.join(
            alt[0] for alt in mail.outbox[0].alternatives)
        for leak in ('8000', '8,000', '10000', '10,000', '120000', '120,000'):
            self.assertNotIn(leak, body,
                             f'the package figure {leak} leaked into the email body')
        # but it must still identify the document
        self.assertIn(a.reference, body)
        self.assertIn('Lorato Molosiwa', body)

    # ── only the people who still owe a decision ────────────────────────────
    def test_a_signatory_who_signed_is_not_chased_again(self):
        a = _atr(approvals={'cfo': {'decision': 'approved', 'by': 'Prathap',
                                    'at': '2026-08-11T10:00:00', 'notes': ''}})
        got = set(authority_notify.outstanding_recipients(a))
        self.assertNotIn(CFO, got, 'a signatory who already signed was chased again')
        self.assertIn(CEO, got)
        self.assertEqual(len(got), 4)

    def test_nothing_is_sent_when_everyone_has_signed(self):
        approvals = {slug: {'decision': 'approved', 'by': 'x', 'at': 'y', 'notes': ''}
                     for slug, _l, _e in AuthorityToRecruit.SIGNATORIES}
        a = _atr(approvals=approvals)
        self.assertEqual(authority_notify.notify_outstanding(a, lead='x'), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_after_signature_only_chases_while_pending(self):
        approvals = {slug: {'decision': 'approved', 'by': 'x', 'at': 'y', 'notes': ''}
                     for slug, _l, _e in AuthorityToRecruit.SIGNATORIES}
        a = _atr(approvals=approvals)
        a.recompute_status()
        self.assertEqual(a.status, AuthorityToRecruit.Status.APPROVED)
        self.assertEqual(authority_notify.notify_after_signature(a, by='x'), 0)
        self.assertEqual(len(mail.outbox), 0)

    # ── decline ─────────────────────────────────────────────────────────────
    def test_decline_tells_the_raiser_and_every_signatory(self):
        raiser = User.objects.create_user('raiser', email='ktshutlhedi@alphadirect.co.bw')
        a = _atr(created_by=raiser)
        authority_notify.notify_declined(a, by='Arun Iyer', notes='Budget not approved')
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        got = _recipients(msg)
        self.assertIn('ktshutlhedi@alphadirect.co.bw', got, 'the raiser was not told')
        for addr in (CEO, COO, HC, HRBP, CFO):
            self.assertIn(addr, got)
        body = ''.join(alt[0] for alt in msg.alternatives)
        self.assertIn('Budget not approved', body, 'the reason was not carried')
        self.assertIn('DECLINED', msg.subject)

    # ── a mail failure must never break the signing action ──────────────────
    def test_mail_failure_does_not_raise(self):
        a = _atr()
        with override_settings(EMAIL_BACKEND='nonexistent.backend.Boom'):
            self.assertEqual(authority_notify.notify_raised(a), 0)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ReminderCommandTests(APITestCase):
    def setUp(self):
        mail.outbox = []

    def _run(self, **kw):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('authority_signature_reminders', stdout=out, **kw)
        return out.getvalue()

    def test_dry_run_sends_nothing(self):
        _atr()
        out = self._run(dry_run=True, min_age_days=0)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('would chase', out)

    def test_chases_a_pending_authority(self):
        a = _atr()
        self._run(min_age_days=0)
        # One private email per outstanding signatory (CFO 2026-08-18).
        self.assertEqual(len(mail.outbox), 5)
        for m in mail.outbox:
            self.assertIn(a.reference, m.subject)

    def test_does_not_chase_an_authority_younger_than_the_cutoff(self):
        _atr()
        self._run(min_age_days=2)     # just created, so under the cutoff
        self.assertEqual(len(mail.outbox), 0)

    def test_does_not_chase_declined_or_approved(self):
        _atr(status=AuthorityToRecruit.Status.DECLINED)
        _atr(person_name='Someone Else', status=AuthorityToRecruit.Status.APPROVED)
        self._run(min_age_days=0)
        self.assertEqual(len(mail.outbox), 0)
