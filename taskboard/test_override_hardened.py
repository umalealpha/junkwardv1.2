"""A duplicate override needs more than 25 characters of typing.

Until 2026-08-09 it did not. Claim G2026004368 (ARCON CRAFTS, BWP 13,662.67) was
correctly detected on PAY/ADIC/2026/08/07/0004 as already carried on
PAY/ADIC/2026/07/28/0001 — and both are marked paid. The loader who created the
duplicate cleared their own warning with free text.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase

from taskboard.models import PaymentRequest
from taskboard.services import _override_is_complete


class OverrideCompletenessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.loader = User.objects.create_user('loader', 'loader@example.invalid', 'x')
        cls.other = User.objects.create_user('checker', 'checker@example.invalid', 'x')

    def _pr(self, **kw):
        base = dict(ref='PAY/TEST/0001', currency='BWP', created_by=self.loader,
                    duplicate_override_reason='The bank rejected the first attempt on 28 July.')
        base.update(kw)
        return PaymentRequest(**base)

    def test_free_text_alone_no_longer_lets_a_duplicate_through(self):
        # This is exactly what happened on 7 August.
        self.assertFalse(_override_is_complete(self._pr()))

    def test_a_reason_and_a_category_are_still_not_enough(self):
        pr = self._pr(duplicate_override_category='bank_rejected')
        self.assertFalse(_override_is_complete(pr), 'no document was attached')

    def test_the_loader_cannot_approve_their_own_override(self):
        pr = self._pr(duplicate_override_category='bank_rejected')
        pr.duplicate_override_evidence_id = 1
        pr.duplicate_override_approved_by_id = self.loader.id      # the raiser
        self.assertFalse(_override_is_complete(pr),
                         'the person who created the duplicate cleared it themselves')

    def test_a_complete_override_by_a_second_person_is_accepted(self):
        pr = self._pr(duplicate_override_category='bank_rejected')
        pr.duplicate_override_evidence_id = 1
        pr.duplicate_override_approved_by_id = self.other.id
        self.assertTrue(_override_is_complete(pr))

    def test_a_short_reason_still_fails_even_when_everything_else_is_there(self):
        pr = self._pr(duplicate_override_reason='ok',
                      duplicate_override_category='bank_rejected')
        pr.duplicate_override_evidence_id = 1
        pr.duplicate_override_approved_by_id = self.other.id
        self.assertFalse(_override_is_complete(pr))

    def test_it_fails_closed_when_nothing_is_filled_in(self):
        pr = self._pr(duplicate_override_reason='')
        self.assertFalse(_override_is_complete(pr))
