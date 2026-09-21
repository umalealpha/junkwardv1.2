"""The Manus activity digest — it must report honestly, including 'nothing'."""
from __future__ import annotations

import datetime
from io import StringIO

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.models import AuditLog


class ManusDigestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manus = User.objects.create_user('manus', 'manus@example.invalid', 'x')
        cls.other = User.objects.create_user('someone', 'someone@example.invalid', 'x')
        cls.day = timezone.localtime().date() - datetime.timedelta(days=1)

    def _entry(self, user, table='HRISProfile', old=None, new=None):
        e = AuditLog.objects.create(
            table_name=table, record_id='abc', action='UPDATE', user=user,
            old_values=old or {'rating': 'Meets'}, new_values=new or {'rating': 'Exceeds'},
            description='test change')
        when = timezone.make_aware(datetime.datetime.combine(self.day, datetime.time(9, 0)))
        AuditLog.objects.filter(pk=e.pk).update(created_at=when)
        return e

    def _run(self):
        mail.outbox = []
        call_command('manus_activity_digest', '--date', self.day.isoformat(),
                     '--to', 'cfo@example.invalid', stdout=StringIO(), stderr=StringIO())
        return mail.outbox

    def test_it_reports_what_manus_changed(self):
        self._entry(self.manus)
        out = self._run()
        self.assertEqual(len(out), 1)
        body = out[0].alternatives[0][0]
        self.assertIn('1 change', out[0].subject)
        self.assertIn('rating', body)          # the field that changed
        self.assertIn('Exceeds', body)         # the new value

    def test_it_ignores_changes_made_by_everyone_else(self):
        self._entry(self.other)
        out = self._run()
        body = out[0].alternatives[0][0]
        self.assertIn('nothing', body.lower(),
                      "somebody else's change was reported as Manus activity")

    def test_it_still_sends_when_nothing_happened(self):
        # A digest that only arrives when there is news cannot be told apart from
        # one that has silently stopped running.
        out = self._run()
        self.assertEqual(len(out), 1)
        self.assertIn('nothing', out[0].alternatives[0][0].lower())

    def test_it_does_not_report_another_day(self):
        e = self._entry(self.manus)
        older = timezone.make_aware(
            datetime.datetime.combine(self.day - datetime.timedelta(days=3), datetime.time(9, 0)))
        AuditLog.objects.filter(pk=e.pk).update(created_at=older)
        out = self._run()
        self.assertIn('nothing', out[0].alternatives[0][0].lower())

    def test_dry_run_sends_no_mail(self):
        self._entry(self.manus)
        mail.outbox = []
        call_command('manus_activity_digest', '--date', self.day.isoformat(),
                     '--dry-run', stdout=StringIO(), stderr=StringIO())
        self.assertEqual(len(mail.outbox), 0)
