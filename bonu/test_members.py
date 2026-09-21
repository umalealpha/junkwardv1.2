"""The membership roll — the gate that decides whether a claim may be paid.

CFO 2026-08-18: *"we only pay claims for members who pay premiums. It's very important."*

The tests that matter here are the refusals, and one in particular: a member who was on
last month's list and is NOT on this month's. That is the shape of the money leak — the
scheme keeps paying for someone who left the union, because nothing ever re-checked.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from bonu import members as roll
from bonu.models import BonuMember, BonuMemberListLoad

JULY = dt.date(2026, 7, 31)
AUGUST = dt.date(2026, 8, 31)


def rows(*specs):
    """The union's own shape: a membership number, a name, a station and a status."""
    return [{'Membership No': s[0], 'Full Name': s[1], 'Work Station': s[2],
             'District': s[3], 'Status': s[4]} for s in specs]


class MembershipRollTests(TestCase):

    def test_loading_the_union_list_creates_the_roll(self):
        load = roll.load_rows(rows(('BNU001', 'Kefilwe Moremi', 'Princess Marina', 'Gaborone',
                                    'Active'),
                                   ('BNU002', 'Tshepo Diteko', 'Nyangabgwe', 'Francistown',
                                    'In arrears')),
                             as_at=JULY, source_name='BONU members July.xlsx')
        self.assertEqual(load.members_loaded, 2)
        self.assertTrue(load.is_current)
        self.assertEqual(BonuMember.objects.count(), 2)
        m = BonuMember.objects.get(membership_no='BNU001')
        self.assertEqual(m.full_name, 'Kefilwe Moremi')
        self.assertEqual(m.district, 'Gaborone')
        self.assertEqual(m.status, BonuMember.Status.ACTIVE)
        self.assertEqual(BonuMember.objects.get(membership_no='BNU002').status,
                         BonuMember.Status.ARREARS)

    def test_a_paid_up_member_is_allowed(self):
        roll.load_rows(rows(('BNU001', 'Kefilwe Moremi', 'Marina', 'Gaborone', 'Active')),
                       as_at=JULY)
        v = roll.check('BNU001', on_date=dt.date(2026, 8, 3))
        self.assertEqual(v['verdict'], roll.ALLOW)
        self.assertIn('2026-07-31', v['reason'])

    def test_a_member_in_arrears_is_refused(self):
        roll.load_rows(rows(('BNU002', 'Tshepo Diteko', 'Nyangabgwe', 'Francistown',
                             'In arrears')), as_at=JULY)
        v = roll.check('BNU002')
        self.assertEqual(v['verdict'], roll.REFUSE)
        self.assertIn('arrears', v['reason'].lower())

    def test_someone_who_is_not_a_member_at_all_is_refused(self):
        roll.load_rows(rows(('BNU001', 'Kefilwe Moremi', 'Marina', 'Gaborone', 'Active')),
                       as_at=JULY)
        v = roll.check('BNU999')
        self.assertEqual(v['verdict'], roll.REFUSE)
        self.assertIn('does not appear', v['reason'])

    def test_dropping_off_the_new_list_is_a_refusal_AND_the_member_is_kept(self):
        """The money leak this whole register exists to stop.

        BNU002 is on July's list and NOT on August's. The scheme must stop paying for
        them — but the record must SURVIVE, because "was on July's list, not on August's"
        is the only evidence that makes the refusal defensible to the union.
        """
        roll.load_rows(rows(('BNU001', 'Kefilwe Moremi', 'Marina', 'Gaborone', 'Active'),
                            ('BNU002', 'Tshepo Diteko', 'Nyangabgwe', 'Francistown', 'Active')),
                       as_at=JULY)
        roll.load_rows(rows(('BNU001', 'Kefilwe Moremi', 'Marina', 'Gaborone', 'Active')),
                       as_at=AUGUST)

        self.assertEqual(BonuMember.objects.count(), 2, 'the dropped member must not be deleted')
        v = roll.check('BNU002')
        self.assertEqual(v['verdict'], roll.REFUSE)
        self.assertIn('2026-07-31', v['reason'])
        self.assertIn('2026-08-31', v['reason'])
        self.assertEqual(roll.check('BNU001')['verdict'], roll.ALLOW)

    def test_no_list_loaded_says_so_instead_of_guessing(self):
        """Neither a yes nor a no. Refusing everything would stop every genuine claim;
        approving would defeat the point of the register."""
        v = roll.check('BNU001')
        self.assertEqual(v['verdict'], roll.UNKNOWN)
        self.assertIn('No membership list has been loaded', v['reason'])

    def test_a_status_the_union_did_not_state_is_never_guessed_into_active(self):
        roll.load_rows([{'Membership No': 'BNU005', 'Full Name': 'Lorato Seleka'}], as_at=JULY)
        self.assertEqual(BonuMember.objects.get(membership_no='BNU005').status,
                         BonuMember.Status.UNKNOWN)
        v = roll.check('BNU005')
        self.assertEqual(v['verdict'], roll.UNKNOWN)
        self.assertIn('did not state', v['reason'])

    def test_work_done_before_the_member_joined_is_refused(self):
        roll.load_rows(rows(('BNU006', 'Neo Kgosi', 'Marina', 'Gaborone', 'Active')), as_at=JULY)
        BonuMember.objects.filter(membership_no='BNU006').update(joined_on=dt.date(2026, 6, 1))
        v = roll.check('BNU006', on_date=dt.date(2026, 5, 30))
        self.assertEqual(v['verdict'], roll.REFUSE)
        self.assertIn('not on cover', v['reason'])

    def test_premium_paid_only_to_an_earlier_month_is_refused(self):
        roll.load_rows(rows(('BNU007', 'Mpho Tau', 'Marina', 'Gaborone', 'Active')), as_at=JULY)
        BonuMember.objects.filter(membership_no='BNU007').update(paid_up_to=dt.date(2026, 6, 30))
        v = roll.check('BNU007', on_date=dt.date(2026, 8, 1))
        self.assertEqual(v['verdict'], roll.REFUSE)
        self.assertIn('paid only to', v['reason'])

    def test_a_file_with_no_membership_number_column_loads_nothing_and_says_why(self):
        with self.assertRaises(ValueError) as ctx:
            roll.load_rows([{'Name': 'Kefilwe Moremi', 'Ward': 'A3'}], as_at=JULY)
        self.assertIn('membership-number column', str(ctx.exception))
        self.assertEqual(BonuMember.objects.count(), 0)

    def test_columns_are_matched_by_meaning_not_position(self):
        cols = roll.map_columns(['District', 'Status', 'Membership Number', 'Names'])
        self.assertEqual(cols['membership_no'], 'Membership Number')
        self.assertEqual(cols['district'], 'District')

    def test_excel_serial_dates_are_read_not_stored_as_nonsense(self):
        self.assertEqual(roll.read_date('46234'), dt.date(2026, 7, 31))
        self.assertEqual(roll.read_date('2026-07-31'), dt.date(2026, 7, 31))
        self.assertIsNone(roll.read_date(''))

    def test_reloading_the_same_month_does_not_duplicate_a_member(self):
        roll.load_rows(rows(('BNU001', 'Kefilwe Moremi', 'Marina', 'Gaborone', 'Active')),
                       as_at=JULY)
        roll.load_rows(rows(('BNU001', 'Kefilwe Moremi', 'Marina', 'Gaborone', 'Active')),
                       as_at=JULY)
        self.assertEqual(BonuMember.objects.filter(membership_no='BNU001').count(), 1)
        self.assertEqual(BonuMemberListLoad.objects.count(), 2)

    def test_the_same_member_gets_the_same_token_as_the_invoice_side(self):
        """Reusing the existing salt matters: a second salt would produce tokens that
        never join to the invoices, and the per-member spend would silently read zero."""
        from bonu.member_identity import get_salt, token
        roll.load_rows(rows(('BNU001', 'Kefilwe Moremi', 'Marina', 'Gaborone', 'Active')),
                       as_at=JULY)
        m = BonuMember.objects.get(membership_no='BNU001')
        self.assertTrue(m.member_token)
        self.assertEqual(m.member_token, token('Kefilwe Moremi', get_salt()))


class MembershipApiTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.cfo = User.objects.create_superuser('pg2', email='pganesharajah@alphadirect.co.bw',
                                                password='x')
        cls.outsider = User.objects.create_user('nobody', email='nobody@alphadirect.co.bw',
                                                password='x')

    def setUp(self):
        roll.load_rows(rows(('BNU001', 'Kefilwe Moremi', 'Marina', 'Gaborone', 'Active'),
                            ('BNU002', 'Tshepo Diteko', 'Nyangabgwe', 'Francistown',
                             'In arrears')), as_at=JULY)

    def test_the_roll_is_not_open_to_any_logged_in_account(self):
        """It carries 9,000 members' names. The BONU gate, not IsAuthenticated."""
        self.client.force_login(self.outsider)
        r = self.client.get(reverse('v1-bonu-members'))
        self.assertIn(r.status_code, (401, 403))

    def test_the_roll_lists_with_a_summary(self):
        self.client.force_login(self.cfo)
        r = self.client.get(reverse('v1-bonu-members'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['total'], 2)
        self.assertEqual(r.data['summary']['list_as_at'], '2026-07-31')
        self.assertEqual(r.data['summary']['on_current_list'], 2)

    def test_search_finds_a_member_by_name_or_number(self):
        self.client.force_login(self.cfo)
        r = self.client.get(reverse('v1-bonu-members'), {'q': 'Tshepo'})
        self.assertEqual([x['membership_no'] for x in r.data['rows']], ['BNU002'])

    def test_the_check_endpoint_answers_allow_and_refuse(self):
        self.client.force_login(self.cfo)
        ok = self.client.get(reverse('v1-bonu-member-check'),
                            {'membership_no': 'BNU001', 'on_date': '2026-08-03'})
        self.assertEqual(ok.data['verdict'], 'allow')
        no = self.client.get(reverse('v1-bonu-member-check'), {'membership_no': 'BNU002'})
        self.assertEqual(no.data['verdict'], 'refuse')
        self.assertIn('arrears', no.data['reason'].lower())

    def test_upload_refuses_without_an_as_at_date(self):
        """The register hangs off which list is current, so the date is never guessed."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_login(self.cfo)
        r = self.client.post(reverse('v1-bonu-member-upload'),
                             {'file': SimpleUploadedFile('list.xlsx', b'not a real workbook')})
        self.assertEqual(r.status_code, 400)
        self.assertIn('as_at', r.data['detail'])

    def test_a_hand_correction_does_not_pose_as_a_union_list(self):
        """A typed fix must not advance last_seen — that would fake the union's evidence."""
        self.client.force_login(self.cfo)
        m = BonuMember.objects.get(membership_no='BNU002')
        before = m.last_seen_id
        r = self.client.patch(reverse('v1-bonu-member-edit', args=[m.pk]),
                             data={'status': 'active', 'note': 'union emailed a correction'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 200)
        m.refresh_from_db()
        self.assertEqual(m.status, BonuMember.Status.ACTIVE)
        self.assertEqual(m.last_seen_id, before)

    def test_a_hand_correction_rejects_an_invented_status(self):
        self.client.force_login(self.cfo)
        m = BonuMember.objects.get(membership_no='BNU002')
        r = self.client.patch(reverse('v1-bonu-member-edit', args=[m.pk]),
                             data={'status': 'probably fine'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_the_annual_benefit_limit_still_bites_on_a_paid_up_member(self):
        """P90,000 per member per CALENDAR year is a breach of cover, not a judgement."""
        from bonu.models import BonuInvoice, BonuInvoiceLine, LawFirm
        m = BonuMember.objects.get(membership_no='BNU001')
        firm = LawFirm.objects.create(name='Test Attorneys')
        inv = BonuInvoice.objects.create(firm=firm, invoice_number='I-1',
                                         invoice_date=dt.date(2026, 3, 1))
        BonuInvoiceLine.objects.create(invoice=inv, matter_description='fees',
                                       amount=Decimal('95000'), member_token=m.member_token)
        v = roll.check('BNU001', on_date=dt.date(2026, 8, 3))
        self.assertEqual(v['verdict'], roll.REFUSE)
        self.assertIn('annual legal benefit', v['reason'])
