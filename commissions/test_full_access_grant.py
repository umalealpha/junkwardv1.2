"""The people the CFO granted unlimited commission access.

Five on 2026-09-08; Tlamelo Chimidza added 2026-09-16 (see access.py).

Pinned by EMAIL, never by first name: "Pako" and "Kago" are two different
people — Pako Kago and Kago Tshutlhedi — and the CFO confirmed he means both.
"""
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase

from commissions.access import (
    FINAL, PAYROLL, STAGE1, STAGE2,
    can_export, can_review_stage, is_payroll, is_reviewer,
)

GRANTED = [
    ('rmokgware@alphadirect.co.bw', 'Rose Mokgware'),
    ('kmokhendo@alphadirect.co.bw', 'Keetile Mokhendo'),
    ('pkago@alphadirect.co.bw', 'Pako Kago'),
    ('ktshutlhedi@alphadirect.co.bw', 'Kago Tshutlhedi'),
    ('bmakosha@alphadirect.co.bw', 'Bokani Makosha'),
    ('tchimidza@alphadirect.co.bw', 'Tlamelo Chimidza'),
]


def _user(email, name):
    User = get_user_model()
    first, _, last = name.partition(' ')
    return User(username=email.split('@')[0], email=email,
                first_name=first, last_name=last, is_active=True)


class FullAccessGrantTests(SimpleTestCase):
    def test_each_granted_person_has_every_stage_payroll_and_export(self):
        for email, name in GRANTED:
            u = _user(email, name)
            with self.subTest(person=name):
                self.assertTrue(can_review_stage(u, STAGE1), f'{name} missing 1st review')
                self.assertTrue(can_review_stage(u, STAGE2), f'{name} missing 2nd review')
                self.assertTrue(can_review_stage(u, FINAL), f'{name} missing final approval')
                self.assertTrue(is_reviewer(u), f'{name} not a reviewer')
                self.assertTrue(can_export(u), f'{name} cannot export')

    def test_the_username_is_not_the_email(self):
        """Rose signs in as 'rose.mokgware' but her address is 'rmokgware@'.
        Granting on a username-shaped address gives her nothing."""
        self.assertFalse(is_reviewer(_user('rose.mokgware@alphadirect.co.bw', 'Rose Mokgware')))
        self.assertTrue(is_reviewer(_user('rmokgware@alphadirect.co.bw', 'Rose Mokgware')))

    def test_the_grant_is_by_email_not_by_first_name(self):
        """A different Pako must NOT inherit Pako Kago's access."""
        other = _user('pmampane@insurance.co.bw', 'Pako Mampane')
        self.assertFalse(is_reviewer(other))
        self.assertFalse(can_export(other))

    def test_an_unrelated_signed_in_user_still_has_nothing(self):
        self.assertFalse(is_reviewer(_user('nobody@alphadirect.co.bw', 'No Body')))
        self.assertFalse(can_export(_user('nobody@alphadirect.co.bw', 'No Body')))

    def test_the_cfo_keeps_final_approval(self):
        cfo = _user('pganesharajah@alphadirect.co.bw', 'Prathap Ganesharajah')
        self.assertTrue(can_review_stage(cfo, FINAL))
        self.assertTrue(can_export(cfo))

    def test_tlamelo_has_final_approval_not_just_first_review(self):
        """CFO decision 2026-09-16, after Bokani Makosha's bug report.

        He reviewed 20 of the 29 first reviews on the board while holding the
        NARROWEST access of the six — first review only, no final, no payroll,
        no export. This pins the decision to level him up; delete the grant and
        this goes red.
        """
        t = _user('tchimidza@alphadirect.co.bw', 'Tlamelo Chimidza')
        self.assertTrue(can_review_stage(t, FINAL), 'Tlamelo missing final approval')
        self.assertTrue(can_review_stage(t, STAGE2), 'Tlamelo missing 2nd review')
        self.assertTrue(can_export(t), 'Tlamelo cannot export')
        # Payroll is deliberately NOT here — see
        # test_payroll_is_not_part_of_the_full_access_grant (CFO 2026-09-16).

    def test_the_grant_is_still_pinned_to_his_address_not_his_name(self):
        """He signs in as 'tlamelo.chimidza'; a username-shaped address must
        grant nothing (the trap that silently gave Rose and Keetile nothing)."""
        self.assertFalse(
            can_review_stage(_user('tlamelo.chimidza@alphadirect.co.bw',
                                   'Tlamelo Chimidza'), FINAL))

    def test_payroll_is_not_part_of_the_full_access_grant(self):
        """CFO 2026-09-16: Keetile, Rose, Bokani and Tlamelo must NOT be able
        to process an approved commission into payroll. They keep every review
        stage and export; payroll alone is withheld. Delete the PAYROLL roster
        narrowing and this goes red.
        """
        for email, name in [('kmokhendo@alphadirect.co.bw', 'Keetile Mokhendo'),
                            ('rmokgware@alphadirect.co.bw', 'Rose Mokgware'),
                            ('bmakosha@alphadirect.co.bw', 'Bokani Makosha'),
                            ('tchimidza@alphadirect.co.bw', 'Tlamelo Chimidza')]:
            u = _user(email, name)
            with self.subTest(person=name):
                self.assertFalse(can_review_stage(u, PAYROLL), f'{name} still has payroll')
                self.assertFalse(is_payroll(u), f'{name} still counts as payroll')
                # and everything they SHOULD keep is untouched
                self.assertTrue(can_review_stage(u, FINAL), f'{name} lost final approval')
                self.assertTrue(can_export(u), f'{name} lost export')

    def test_the_two_finance_people_keep_payroll(self):
        """Somebody must still be able to process an approved submission."""
        for email, name in [('pkago@alphadirect.co.bw', 'Pako Kago'),
                            ('ktshutlhedi@alphadirect.co.bw', 'Kago Tshutlhedi')]:
            with self.subTest(person=name):
                self.assertTrue(is_payroll(_user(email, name)), f'{name} lost payroll')
