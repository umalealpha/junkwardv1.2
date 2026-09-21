"""taskboard/test_whatsapp_delivery.py — the WhatsApp traps that hid a months-long
outage in Graphite V2 (Arjun Iyer's handover, 2026-08-03).

Meta accepts messages it will never deliver, returns HTTP 200, and tells you
nothing. These tests pin the three places Omni could inherit that blindness:

  1. template parameters must never carry a newline/tab/space-run  -> Meta (#100)
  2. template parameters must never exceed 1024 chars              -> Meta rejects
  3. a free-form send must be logged ACCEPTED, never SENT          -> the log must
     not assert a delivery we cannot prove
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from taskboard import whatsapp_service as wa
from taskboard.models import WhatsAppContact, WhatsAppMessage

User = get_user_model()


class CleanTemplateParamTests(TestCase):
    """Trap 1 + 2 — the exact bug that broke 100% of Graphite's template sends."""

    def test_newlines_and_tabs_are_flattened(self):
        self.assertEqual(wa.clean_template_param('line one\nline two'), 'line one line two')
        self.assertEqual(wa.clean_template_param('a\tb'), 'a b')
        self.assertEqual(wa.clean_template_param('a\r\nb'), 'a b')

    def test_no_run_of_more_than_four_spaces_survives(self):
        out = wa.clean_template_param('a' + ' ' * 12 + 'b')
        self.assertEqual(out, 'a b')
        self.assertNotIn('     ', out)

    def test_meta_forbidden_characters_never_survive(self):
        dirty = 'Submit\nthe\tJune  \n\n  claims   report'
        out = wa.clean_template_param(dirty)
        for bad in ('\n', '\r', '\t'):
            self.assertNotIn(bad, out)
        self.assertNotIn('     ', out)

    def test_truncated_at_1024_and_visibly_marked(self):
        out = wa.clean_template_param('x' * 5000)
        self.assertEqual(len(out), wa.TEMPLATE_PARAM_MAX)
        self.assertTrue(out.endswith('…'), 'truncation must be visible, not silent')

    def test_short_values_pass_through_untouched(self):
        self.assertEqual(wa.clean_template_param('Kago'), 'Kago')
        self.assertEqual(wa.clean_template_param(3), '3')
        self.assertEqual(wa.clean_template_param(None), '')

    def test_send_template_sanitises_before_calling_meta(self):
        """The regression guard: a multi-line task title must not reach Meta raw."""
        captured = {}

        class _Resp:
            status_code = 200
            @staticmethod
            def json():
                return {'messages': [{'id': 'wamid.TEST'}]}

        def _fake_post(url, json=None, headers=None, timeout=None):
            captured['payload'] = json
            return _Resp()

        with patch.object(wa, 'get_llm_key', side_effect=lambda n: 'tok' if n == 'WHATSAPP_TOKEN' else '123'), \
             patch.object(wa.requests, 'post', _fake_post):
            ok, pid, err = wa.send_template(
                '26771234567', 'task_due_today', ['Kago', 'Submit\nthe June  report'],
            )

        self.assertTrue(ok, err)
        sent = captured['payload']['template']['components'][0]['parameters']
        self.assertEqual(sent[1]['text'], 'Submit the June report')
        for p in sent:
            self.assertNotIn('\n', p['text'])
            self.assertLessEqual(len(p['text']), wa.TEMPLATE_PARAM_MAX)


class FreeFormIsNotDeliveryTests(TestCase):
    """Trap 4/5 — the audit log must not claim a delivery Meta never promised."""

    def setUp(self):
        self.cfo = User.objects.create_user(
            username='cfo-wa-test', password='x', is_superuser=True, is_staff=True,
        )
        self.contact = WhatsAppContact.objects.create(name='Test Person', phone='26771234567')
        self.client = APIClient()
        self.client.force_authenticate(self.cfo)

    def test_accepted_status_exists_and_is_distinct_from_sent(self):
        self.assertNotEqual(WhatsAppMessage.Status.ACCEPTED, WhatsAppMessage.Status.SENT)
        self.assertLessEqual(len(WhatsAppMessage.Status.ACCEPTED.value), 8)

    def test_free_form_send_is_logged_accepted_not_sent(self):
        with patch.object(wa, 'send_message', return_value=(True, 'wamid.X', '')):
            r = self.client.post('/api/v1/whatsapp/send/', {
                'contact_ids': [str(self.contact.id)],
                'message': 'please update your tasks',
            }, format='json')

        self.assertEqual(r.status_code, 200, r.content)
        msg = WhatsAppMessage.objects.get(to_phone='26771234567')
        self.assertEqual(
            msg.status, WhatsAppMessage.Status.ACCEPTED,
            'a free-form send outside the 24h window is silently discarded by Meta — '
            'logging it as SENT is the exact blindness that hid Graphite\'s outage',
        )
        self.assertIs(r.data['delivery_confirmed'], False)
        self.assertIn('24 hours', r.data['delivery_warning'])

    def test_a_real_failure_is_still_logged_failed(self):
        with patch.object(wa, 'send_message', return_value=(False, '', '401: bad token')):
            r = self.client.post('/api/v1/whatsapp/send/', {
                'contact_ids': [str(self.contact.id)],
                'message': 'hello',
            }, format='json')

        self.assertEqual(r.status_code, 200, r.content)
        msg = WhatsAppMessage.objects.get(to_phone='26771234567')
        self.assertEqual(msg.status, WhatsAppMessage.Status.FAILED)
        self.assertEqual(r.data['failed'], 1)

    def test_template_send_is_logged_sent(self):
        """Templates DO deliver outside the window — those keep the honest SENT."""
        with patch.object(wa, 'send_template', return_value=(True, 'wamid.T', '')):
            r = self.client.post('/api/v1/whatsapp/send/', {
                'template': 'task_overdue_reminder',
                'recipients': [{'contact_id': str(self.contact.id), 'params': ['Kago', '3']}],
            }, format='json')

        self.assertEqual(r.status_code, 200, r.content)
        msg = WhatsAppMessage.objects.get(to_phone='26771234567')
        self.assertEqual(msg.status, WhatsAppMessage.Status.SENT)
