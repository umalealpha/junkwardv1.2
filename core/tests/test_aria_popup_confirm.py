"""The popup send gate — CFO directive: "it will double check with me the name".

Telling the model to confirm is a rule it can skip. These tests pin the gate
that it cannot: the first call NEVER creates a popup, no matter how complete
its arguments look, and the second call only lands if it carries an identifier
that could only have come from the first call's reply.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from core.aria.qc_tools import send_popup_message
from core.models import AriaPopup

User = get_user_model()


class PopupConfirmGateTests(TestCase):
    def setUp(self):
        self.cfo = User.objects.create_user(
            'pg-test', email='pganesharajah@alphadirect.co.bw', password='x')
        self.motlatsi = User.objects.create_user(
            'mmolefe-test', email='mmolefe@alphadirect.co.bw', password='x',
            first_name='Motlatsi', last_name='Molefe')

    def test_first_call_never_sends(self):
        """The whole point: a single call cannot deliver a message."""
        r = send_popup_message(recipient_name='Motlatsi', message='hello',
                               user=self.cfo)
        self.assertFalse(r['ok'])
        self.assertTrue(r['needs_confirmation'])
        self.assertEqual(AriaPopup.objects.count(), 0)
        # and it hands back exactly what the second call must quote
        self.assertEqual([m['confirm_recipient'] for m in r['matches']],
                         ['mmolefe@alphadirect.co.bw'])

    def test_second_call_with_confirmation_sends(self):
        send_popup_message(recipient_name='Motlatsi', message='hello', user=self.cfo)
        r = send_popup_message(recipient_name='Motlatsi', message='hello',
                               user=self.cfo,
                               confirm_recipient='mmolefe@alphadirect.co.bw')
        self.assertTrue(r['ok'], r)
        p = AriaPopup.objects.get()
        self.assertEqual(p.recipient, self.motlatsi)
        self.assertEqual(p.sender, self.cfo)
        self.assertEqual(p.message, 'hello')

    def test_confirming_someone_else_is_refused(self):
        """A confirmation must match the name that was searched, so a mistyped
        or invented identifier cannot redirect the message to another person."""
        other = User.objects.create_user(
            'kt-test', email='ktshutlhedi@alphadirect.co.bw', password='x',
            first_name='Kago', last_name='Tshutlhedi')
        r = send_popup_message(recipient_name='Motlatsi', message='hello',
                               user=self.cfo,
                               confirm_recipient=other.email)
        self.assertFalse(r['ok'])
        self.assertEqual(AriaPopup.objects.count(), 0)

    def test_non_cfo_cannot_send_even_with_confirmation(self):
        staff = User.objects.create_user(
            'someone', email='someone@alphadirect.co.bw', password='x')
        r = send_popup_message(recipient_name='Motlatsi', message='hello',
                               user=staff,
                               confirm_recipient='mmolefe@alphadirect.co.bw')
        self.assertFalse(r['ok'])
        self.assertEqual(AriaPopup.objects.count(), 0)
