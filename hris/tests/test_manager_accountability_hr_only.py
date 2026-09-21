"""Manager Accountability — a people matter says Human Resources, not the CFO.

CFO 2026-08-03. The escalation that went out on 2 August told a manager that
his answer about a named employee's attendance "goes to the CFO". People
matters are HR matters: the copy must say Human Resources, and the recipients
must BE Human Resources — an email that says HR while copying the CEO and the
CFO is a lie to the person answering it.

These tests pin the wording and the routing together, because fixing only one
of them is how the original mistake reads to a manager.
"""
from __future__ import annotations

from django.test import SimpleTestCase

from hris import manager_accountability as ma
from hris import manager_accountability_views as mav

REPORTS = [{'name': 'Oratile Ria Tlhomelang', 'last_tracked': 'Tue 28 Jul', 'days_dark': 3}]
HR_EMAILS = {'ubutale@alphadirect.co.bw', 'dikgopoleng@alphadirect.co.bw'}
CFO_EMAIL = 'pganesharajah@alphadirect.co.bw'


class PeopleMattersSayHRTests(SimpleTestCase):
    def _manager_email(self) -> str:
        return ma.build_manager_email(
            manager_name='Kakale Botana', reports=REPORTS,
            answer_url='https://omni.alphadirect.co.bw/hris/api/x?t=tok',
            deadline_str='17:00, Sun 02 Aug')

    # ── the copy ────────────────────────────────────────────────────────────
    def test_the_note_never_tells_a_manager_it_goes_to_the_cfo(self):
        html = self._manager_email()
        self.assertNotIn('CFO', html)
        self.assertIn('it goes to Human Resources', html)

    def test_the_note_escalates_to_hr_not_the_c_suite(self):
        html = self._manager_email()
        self.assertIn('escalates to Human Resources', html)
        for word in ('CEO', 'COO'):
            self.assertNotIn(word, html)

    def test_the_escalation_email_names_no_finance_destination(self):
        html = ma.build_escalation_email(
            manager_name='Kakale Botana', reports=REPORTS,
            sent_on='2026-08-01', deadline_str='17:00, Sun 02 Aug')
        self.assertNotIn('CFO', html)

    def test_the_answer_page_says_human_resources(self):
        class _Note:
            reports = REPORTS
            responded_at = None
        html = mav._form('Kakale', _Note())
        self.assertNotIn('CFO', html)
        self.assertIn('This goes straight to Human Resources.', html)

    # ── the routing must match the copy ─────────────────────────────────────
    def test_escalation_goes_to_hr_only(self):
        self.assertEqual(set(ma.ESCALATION_EMAILS), HR_EMAILS)
        self.assertNotIn(CFO_EMAIL, ma.ESCALATION_EMAILS)

    def test_the_thin_register_path_is_still_a_quiet_cfo_note(self):
        """That path is a DATA-quality signal, not an accusation about a person,
        so it stays with the CFO and names nobody to HR."""
        self.assertEqual(ma.QUIET_ESCALATION_EMAILS, [CFO_EMAIL])
