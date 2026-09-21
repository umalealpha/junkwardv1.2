"""Bulk authorisation of clean payment packs (CFO 2026-08-04).

The CFO asked to have four packs approved on his behalf. That was refused: the
click that completes a payment task IS the release of the money. This screen
makes his own signature one press instead, and these tests pin the three things
that make it safe to exist at all:

  1. only the CFO can fire it,
  2. a pack with a duplicate is skipped, never swept along,
  3. the total the CFO was shown must still be the total being released.

Run in CI (needs a DB): manage.py test taskboard.test_payment_bulk_approve
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import OmniTask
from taskboard.models import PaymentRequest
from taskboard.test_helpers import window_always_open


@window_always_open
class BulkApproveTests(APITestCase):
    def setUp(self):
        self.cfo = User.objects.create_user('pganesharajah', email='pganesharajah@alphadirect.co.bw')
        self.kago = User.objects.create_user('ktshutlhedi', email='ktshutlhedi@alphadirect.co.bw')
        self.clerk = User.objects.create_user('lthebe', email='lthebe@alphadirect.co.bw')
        self.preview_url = reverse('v1-payment-bulk-preview')
        self.approve_url = reverse('v1-payment-bulk-approve')

    def _pack(self, ref, lines, *, total):
        task = OmniTask.objects.create(
            assigner=self.kago, assignee=self.cfo,
            title=f'Payment authorisation — {ref}',
            status=OmniTask.Status.PENDING, source='payment_request',
        )
        return PaymentRequest.objects.create(
            ref=ref, subject='Claims payable', category=PaymentRequest.Category.OTHER,
            line_items=lines, total=Decimal(total),
            status=PaymentRequest.Status.PENDING_CFO,
            created_by=self.clerk, first_approver=self.kago, task=task,
        )

    # ── who may fire it ─────────────────────────────────────────────────────
    def test_only_the_cfo_can_preview_or_approve(self):
        for who in (self.kago, self.clerk):
            self.client.force_authenticate(who)
            self.assertEqual(self.client.get(self.preview_url).status_code, 403)
            self.assertEqual(
                self.client.post(self.approve_url, {'confirm_total': '1'}, format='json').status_code,
                403)

    def _pack_cat(self, ref, category, *, total):
        task = OmniTask.objects.create(
            assigner=self.kago, assignee=self.cfo,
            title=f'Payment authorisation — {ref}',
            status=OmniTask.Status.PENDING, source='payment_request')
        return PaymentRequest.objects.create(
            ref=ref, subject='x', category=category,
            line_items=[{'description': ref, 'amount': str(total), 'ref': f'INV-{ref}'}],
            total=Decimal(total), status=PaymentRequest.Status.PENDING_CFO,
            created_by=self.clerk, first_approver=self.kago, task=task)

    # ── category filter — approve one category at a time (CFO 2026-08-31) ─────
    def test_category_filter_scopes_the_bulk_queue(self):
        self._pack_cat('PAY/C/1', PaymentRequest.Category.CLAIM, total='100.00')
        self._pack_cat('PAY/S/1', PaymentRequest.Category.SUPPLIER, total='200.00')
        self.client.force_authenticate(self.cfo)
        # No filter → both packs.
        self.assertEqual(self.client.get(self.preview_url).data['ready_count'], 2)
        # Claim only → just the claim pack + its total.
        cl = self.client.get(self.preview_url, {'categories': 'claim'})
        self.assertEqual(cl.data['ready_count'], 1)
        self.assertEqual(cl.data['ready_total'], '100.00')
        # Approving claim-only authorises just the claim pack; supplier untouched.
        r = self.client.post(self.approve_url,
                             {'confirm_total': '100.00', 'categories': ['claim']}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data['authorised_count'], 1)
        self.assertEqual(PaymentRequest.objects.get(ref='PAY/C/1').status,
                         PaymentRequest.Status.PAID)
        self.assertEqual(PaymentRequest.objects.get(ref='PAY/S/1').status,
                         PaymentRequest.Status.PENDING_CFO)

    # ── the happy path ──────────────────────────────────────────────────────
    def test_a_clean_pack_is_authorised_and_its_task_closed(self):
        p = self._pack('PAY/T/1', [{'description': 'AAA', 'amount': '100.00', 'ref': 'INV-AAA-1'}],
                       total='100.00')
        self.client.force_authenticate(self.cfo)
        pre = self.client.get(self.preview_url)
        self.assertEqual(pre.status_code, 200, pre.content)
        self.assertEqual(pre.data['ready_count'], 1)
        self.assertEqual(pre.data['ready_total'], '100.00')

        r = self.client.post(self.approve_url, {'confirm_total': '100.00'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data['authorised_count'], 1)
        p.refresh_from_db()
        self.assertEqual(p.status, PaymentRequest.Status.PAID)
        p.task.refresh_from_db()
        self.assertEqual(p.task.status, OmniTask.Status.DONE)

    # ── the duplicate must survive the bulk path ────────────────────────────
    def test_a_duplicate_pack_is_never_swept_along(self):
        """The whole risk of a bulk button: a dirty pack riding in on a clean one."""
        line = {'description': 'BBB', 'amount': '250.00', 'ref': 'INV-BBB-9'}
        paid = self._pack('PAY/T/PAID', [line], total='250.00')
        paid.status = PaymentRequest.Status.PAID
        paid.save(update_fields=['status'])
        dirty = self._pack('PAY/T/DIRTY', [dict(line)], total='250.00')
        clean = self._pack('PAY/T/CLEAN', [{'description': 'CCC', 'amount': '10.00', 'ref': 'INV-CCC-3'}],
                           total='10.00')

        self.client.force_authenticate(self.cfo)
        pre = self.client.get(self.preview_url)
        self.assertEqual(pre.data['ready_count'], 1, pre.data)
        self.assertEqual(pre.data['blocked_count'], 1, pre.data)
        self.assertEqual(pre.data['ready_total'], '10.00')

        r = self.client.post(self.approve_url, {'confirm_total': '10.00'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data['authorised_count'], 1)
        clean.refresh_from_db()
        dirty.refresh_from_db()
        self.assertEqual(clean.status, PaymentRequest.Status.PAID)
        self.assertEqual(dirty.status, PaymentRequest.Status.PENDING_CFO,
                         'a duplicate pack was authorised by the bulk button')

    # ── the money he saw is the money he releases ───────────────────────────
    def test_a_changed_total_refuses_the_whole_run(self):
        self._pack('PAY/T/2', [{'description': 'DDD', 'amount': '500.00', 'ref': 'INV-DDD-2'}],
                   total='500.00')
        self.client.force_authenticate(self.cfo)
        r = self.client.post(self.approve_url, {'confirm_total': '400.00'}, format='json')
        self.assertEqual(r.status_code, 409, r.content)
        self.assertIn('changed while you were reading it', r.data['detail'])
        self.assertEqual(PaymentRequest.objects.filter(status=PaymentRequest.Status.PAID).count(), 0)

    def test_confirm_total_is_required(self):
        self._pack('PAY/T/3', [{'description': 'EEE', 'amount': '5.00', 'ref': 'INV-EEE-3'}], total='5.00')
        self.client.force_authenticate(self.cfo)
        r = self.client.post(self.approve_url, {}, format='json')
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(PaymentRequest.objects.filter(status=PaymentRequest.Status.PAID).count(), 0)

    def test_an_empty_queue_refuses_rather_than_reporting_success(self):
        self.client.force_authenticate(self.cfo)
        r = self.client.post(self.approve_url, {'confirm_total': '0'}, format='json')
        self.assertEqual(r.status_code, 409, r.content)

    def test_a_pack_whose_task_is_already_done_is_not_offered(self):
        p = self._pack('PAY/T/4', [{'description': 'FFF', 'amount': '7.00', 'ref': 'INV-FFF-4'}], total='7.00')
        p.task.status = OmniTask.Status.DONE
        p.task.save(update_fields=['status'])
        self.client.force_authenticate(self.cfo)
        pre = self.client.get(self.preview_url)
        self.assertEqual(pre.data['ready_count'], 0, pre.data)
        self.assertEqual(pre.data['blocked_count'], 0, pre.data)

    def test_preview_flags_a_pack_that_was_edited_after_sign_off(self):
        """The CFO must be able to see which packs are not exactly what finance signed."""
        p = self._pack('PAY/T/5', [{'description': 'GGG', 'amount': '9.00', 'ref': 'INV-GGG-5'}], total='9.00')
        p.decision_notes = 'LINE REMOVED 2026-08-04 ...'
        p.save(update_fields=['decision_notes'])
        self.client.force_authenticate(self.cfo)
        row = self.client.get(self.preview_url).data['ready'][0]
        self.assertTrue(row['edited_after_signoff'])
        self.assertEqual(row['signed_off_by'], self.kago.get_full_name() or self.kago.username)
