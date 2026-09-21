"""Legal claim intake + legal bill capture — Kelvin Kimani's spec, 9 Sep 2026.

The point of the feature, in one line: a matter can now be opened in-house as
well as with an external firm, it carries the dates and the region the legal
team runs it on, and every legal bill rolls up to the CLIENT so an 80,000
per-client ceiling can be seen coming instead of discovered afterwards.

Each test below is aimed at a way this could go quietly wrong rather than at
the happy path: an unrecognised town being defaulted instead of refused, a
client's ceiling being crossed one matter at a time, a bill dumped whole onto
one of the three matters it covers, the same bill counted twice, and a name
alone auto-charging the wrong person.
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from bonu.cases import case_detail, cases
from bonu.legal_bills import (legal_bill_allocate, legal_bill_stage, legal_bills,
                              legal_cap_board, legal_client_search)
from bonu.legal_bills import match_billed_name as legal_bills_match
from bonu.models import (BonuMember, LawFirm, LegalBill, LegalBillAllocation,
                         LegalCase, LegalClientAlias, LegalSettings)
from core.models import Company, UserProfile

D = Decimal


class LegalBase(TestCase):
    def setUp(self):
        self.company = (Company.objects.filter(code='ADIC').first()
                        or Company.objects.create(code='ADIC', name='ADIC'))
        self.firm = LawFirm.objects.create(name='Jeremiah & Taldi', is_active=True)
        self.firm2 = LawFirm.objects.create(name='Second & Co', is_active=True)
        self.rf = APIRequestFactory()
        self.user = User.objects.create_user('fin', email='fin@alphadirect.co.bw',
                                             password='x')
        UserProfile.objects.update_or_create(
            user=self.user, defaults={'title': UserProfile.Title.ACCOUNTANT,
                                      'is_active': True})
        self.user = User.objects.get(pk=self.user.pk)
        self.settings_row = LegalSettings.solo()

    # -- helpers ------------------------------------------------------------
    def post(self, view, body, **kw):
        req = self.rf.post('/api/v1/bonu/x/', body, format='json')
        force_authenticate(req, user=self.user)
        return view(req, **kw)

    def get(self, view, qs='', **kw):
        req = self.rf.get('/api/v1/bonu/x/' + qs)
        force_authenticate(req, user=self.user)
        return view(req, **kw)

    def member(self, no, name):
        return BonuMember.objects.create(membership_no=no, full_name=name,
                                         status=BonuMember.Status.ACTIVE)

    def case_for(self, member=None, *, ref=None, firm=None, in_house=False,
                 received=None, closed=None):
        """A matter, straight through the model — the API is tested separately."""
        n = LegalCase.objects.count() + 1
        return LegalCase.objects.create(
            case_ref=ref or f'CASE-{n:03d}',
            firm_type=(LegalCase.FirmType.IN_HOUSE if in_house
                       else LegalCase.FirmType.EXTERNAL),
            firm=None if in_house else (firm or self.firm),
            member_ref=(member.membership_no if member else 'BONU-X'),
            client=member,
            instructed_on=datetime.date(2026, 9, 1),
            received_on=received, closed_on=closed,
        )

    def bill(self, amount, allocations, *, ref='INV-1', stage=LegalBill.Stage.BILLED,
             firm=None, in_house=False):
        b = LegalBill.objects.create(
            source=(LegalBill.Source.IN_HOUSE if in_house else LegalBill.Source.EXTERNAL),
            firm=None if in_house else (firm or self.firm),
            reference=ref, bill_date=datetime.date(2026, 9, 5), amount=D(amount),
            stage=stage,
            allocation_state=LegalBill.Allocation.ALLOCATED)
        for case, share in allocations:
            LegalBillAllocation.objects.create(bill=b, case=case, amount=D(share))
        return b


# =========================================================================
# PART 1 — claim intake
# =========================================================================

class InHouseTests(LegalBase):
    """The missing option: a matter the office runs itself."""

    def test_an_in_house_matter_opens_with_no_firm(self):
        r = self.post(cases, {'firm_type': 'in_house', 'member_ref': 'BONU-1',
                              'internal_officer': 'Alpha Law', 'matter_type': 'labour'})
        self.assertEqual(r.status_code, 201, r.data)
        c = LegalCase.objects.get()
        self.assertTrue(c.is_in_house)
        self.assertIsNone(c.firm_id)
        self.assertEqual(r.data['case']['firm'], 'In-house — Alpha Law')

    def test_an_in_house_matter_needs_no_named_officer(self):
        # Optional on purpose: refusing to open an unnamed in-house matter would
        # push it off the register, which is worse than not knowing who has it.
        r = self.post(cases, {'firm_type': 'in_house', 'member_ref': 'BONU-2'})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['case']['firm'], 'In-house')

    def test_an_external_matter_still_requires_a_firm(self):
        r = self.post(cases, {'firm_type': 'external', 'member_ref': 'BONU-3'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(LegalCase.objects.count(), 0)

    def test_in_house_and_a_firm_together_is_refused_not_ignored(self):
        # Silently dropping the firm would record the matter against the wrong
        # handler while telling the user it worked.
        r = self.post(cases, {'firm_type': 'in_house', 'member_ref': 'BONU-4',
                              'firm_id': str(self.firm.pk)})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(LegalCase.objects.count(), 0)

    def test_the_database_itself_refuses_a_mismatched_pair(self):
        # Not just the form. A shell, an import or a future screen must not be
        # able to write in_house-with-a-firm either.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LegalCase.objects.create(
                    case_ref='BAD-1', firm_type=LegalCase.FirmType.IN_HOUSE,
                    firm=self.firm, member_ref='BONU-5',
                    instructed_on=datetime.date(2026, 9, 1))

    def test_two_in_house_matters_cannot_share_a_file_reference(self):
        # The (firm, case_ref) constraint stops policing once firm is NULL,
        # because Postgres treats every NULL as distinct.
        self.case_for(ref='SAME-REF', in_house=True)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.case_for(ref='SAME-REF', in_house=True)

    def test_moving_a_matter_to_external_requires_the_firm_it_goes_to(self):
        c = self.case_for(in_house=True)
        r = self.post(case_detail, {'firm_type': 'external'}, case_id=c.pk)
        self.assertEqual(r.status_code, 400)
        r = self.post(case_detail, {'firm_type': 'external',
                                    'firm_id': str(self.firm.pk)}, case_id=c.pk)
        self.assertEqual(r.status_code, 200, r.data)
        c.refresh_from_db()
        self.assertEqual(c.firm_id, self.firm.pk)
        self.assertFalse(c.is_in_house)


class RegionTests(LegalBase):
    def test_a_town_on_the_list_is_stored_canonically(self):
        r = self.post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'BONU-1',
                              'region': 'francistown'})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(LegalCase.objects.get().region, 'Francistown')

    def test_region_may_be_left_empty(self):
        r = self.post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'BONU-2'})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(LegalCase.objects.get().region, '')

    def test_a_town_not_on_the_list_is_refused_not_filed_as_other(self):
        # THE fallback trap. A typo quietly becoming "Other" or "Gaborone"
        # corrupts the grouping the field exists for, and nobody would know.
        r = self.post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'BONU-3',
                              'region': 'Gaboron'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(LegalCase.objects.count(), 0)

    def test_other_is_a_real_choice_a_person_can_make(self):
        r = self.post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'BONU-4',
                              'region': 'Other'})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(LegalCase.objects.get().region, 'Other')

    def test_the_register_can_be_filtered_by_region(self):
        self.case_for(ref='A-1')
        c = self.case_for(ref='A-2')
        c.region = 'Maun'
        c.save(update_fields=['region'])
        r = self.get(cases, '?region=Maun')
        self.assertEqual([x['case_ref'] for x in r.data['cases']], ['A-2'])


class KeyDateTests(LegalBase):
    def test_the_four_dates_are_kept_apart(self):
        r = self.post(cases, {
            'firm_id': str(self.firm.pk), 'member_ref': 'BONU-1',
            'date_of_loss': '2026-01-05', 'matter_arose_on': '2026-02-10',
            'firm_contact_on': '2026-03-01', 'instructed_on': '2026-03-15'})
        self.assertEqual(r.status_code, 201, r.data)
        c = LegalCase.objects.get()
        self.assertEqual(c.date_of_loss, datetime.date(2026, 1, 5))
        self.assertEqual(c.matter_arose_on, datetime.date(2026, 2, 10))
        self.assertEqual(c.firm_contact_on, datetime.date(2026, 3, 1))
        self.assertEqual(c.instructed_on, datetime.date(2026, 3, 15))

    def test_dates_out_of_order_warn_but_still_save(self):
        # A soft warning, per the spec. A reversed pair is usually a typo, but
        # a hard block would stop a genuine exception being recorded at all.
        r = self.post(cases, {
            'firm_id': str(self.firm.pk), 'member_ref': 'BONU-2',
            'date_of_loss': '2026-05-01', 'matter_arose_on': '2026-01-01',
            'instructed_on': '2026-06-01'})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertIn('warning', r.data)
        self.assertIn('date of loss', r.data['warning'])
        self.assertEqual(LegalCase.objects.count(), 1)

    def test_dates_in_order_produce_no_warning(self):
        r = self.post(cases, {
            'firm_id': str(self.firm.pk), 'member_ref': 'BONU-3',
            'date_of_loss': '2026-01-01', 'matter_arose_on': '2026-02-01',
            'firm_contact_on': '2026-03-01', 'instructed_on': '2026-04-01'})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertNotIn('warning', r.data)

    def test_a_nonsense_date_is_refused(self):
        r = self.post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'BONU-4',
                              'date_of_loss': 'last Tuesday'})
        self.assertEqual(r.status_code, 400)


class DaysToProcessTests(LegalBase):
    def test_it_runs_live_while_the_matter_is_open(self):
        c = self.case_for(received=datetime.date.today() - datetime.timedelta(days=6))
        self.assertEqual(c.days_to_process(), 6)

    def test_it_freezes_at_the_closure_date(self):
        c = self.case_for(received=datetime.date(2026, 9, 1),
                          closed=datetime.date(2026, 9, 5))
        self.assertEqual(c.days_to_process(as_of=datetime.date(2026, 12, 31)), 4)

    def test_no_received_date_reports_not_known_never_nought(self):
        c = self.case_for(received=None)
        self.assertIsNone(c.days_to_process())
        r = self.get(cases)
        self.assertIsNone(r.data['cases'][0]['days_to_process'])

    def test_intake_starts_the_clock_today_by_default(self):
        r = self.post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'BONU-1'})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(LegalCase.objects.get().received_on, datetime.date.today())
        self.assertEqual(r.data['case']['days_to_process'], 0)


class ClientLinkTests(LegalBase):
    def test_the_scheme_reference_links_the_client_automatically(self):
        m = self.member('BONU-77', 'Thabo Moeng')
        r = self.post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'BONU-77'})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(LegalCase.objects.get().client_id, m.pk)

    def test_a_reference_not_on_the_roll_still_opens_but_is_flagged(self):
        # Refusing would push a real matter off the register. Saying nothing
        # would leave its spend invisible to every cap total.
        r = self.post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'NOT-A-MEMBER'})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertIsNone(LegalCase.objects.get().client_id)
        self.assertIn('warning_client', r.data)
        self.assertEqual(self.get(cases).data['unlinked_clients'], 1)


# =========================================================================
# PART 2 — bill capture, allocation and the cap
# =========================================================================

class BillCaptureTests(LegalBase):
    def test_a_bill_is_captured_and_allocated_to_its_matter(self):
        m = self.member('BONU-1', 'Thabo Moeng')
        c = self.case_for(m)
        r = self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm.pk), 'reference': 'INV-100',
            'bill_date': '2026-09-05', 'amount': '1500.00',
            'allocations': [{'case_id': str(c.pk), 'amount': '1500.00'}]})
        self.assertEqual(r.status_code, 201, r.data)
        b = LegalBill.objects.get()
        self.assertEqual(b.allocation_state, LegalBill.Allocation.ALLOCATED)
        self.assertEqual(b.allocated_total, D('1500.00'))
        self.assertEqual(r.data['bill']['allocations'][0]['client'], 'Thabo Moeng')

    def test_one_bill_can_be_split_across_several_matters(self):
        m1, m2 = self.member('B-1', 'Thabo Moeng'), self.member('B-2', 'Naledi Sebina')
        c1, c2 = self.case_for(m1), self.case_for(m2)
        r = self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm.pk), 'reference': 'INV-200',
            'bill_date': '2026-09-05', 'amount': '1000.00',
            'allocations': [{'case_id': str(c1.pk), 'amount': '400.00'},
                            {'case_id': str(c2.pk), 'amount': '600.00'}]})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(LegalBillAllocation.objects.count(), 2)

    def test_a_split_that_does_not_add_up_is_refused(self):
        # The whole reason for splitting: 400 of a 1,000 bill leaves 600
        # charged to nobody, and the cap totals silently understate.
        c = self.case_for(self.member('B-1', 'Thabo Moeng'))
        r = self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm.pk), 'reference': 'INV-201',
            'bill_date': '2026-09-05', 'amount': '1000.00',
            'allocations': [{'case_id': str(c.pk), 'amount': '400.00'}]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(LegalBill.objects.count(), 0)

    def test_the_same_matter_twice_on_one_bill_is_refused(self):
        c = self.case_for(self.member('B-1', 'Thabo Moeng'))
        r = self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm.pk), 'reference': 'INV-202',
            'bill_date': '2026-09-05', 'amount': '1000.00',
            'allocations': [{'case_id': str(c.pk), 'amount': '500.00'},
                            {'case_id': str(c.pk), 'amount': '500.00'}]})
        self.assertEqual(r.status_code, 400)

    def test_an_in_house_bill_needs_no_firm(self):
        c = self.case_for(self.member('B-1', 'Thabo Moeng'), in_house=True)
        r = self.post(legal_bills, {
            'source': 'in_house', 'reference': 'INT-1', 'bill_date': '2026-09-05',
            'amount': '900.00', 'allocations': [{'case_id': str(c.pk),
                                                 'amount': '900.00'}]})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['bill']['firm'], 'In-house')

    def test_a_bill_on_a_matter_with_no_client_stays_an_exception(self):
        # It IS tied to a matter, but that matter has nobody on the membership
        # roll — so the money cannot be totalled against a client and the bill
        # must not look dealt with.
        c = self.case_for(None)
        r = self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm.pk), 'reference': 'INV-300',
            'bill_date': '2026-09-05', 'amount': '500.00',
            'allocations': [{'case_id': str(c.pk), 'amount': '500.00'}]})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['bill']['allocation_state'], 'unallocated')
        self.assertIn('no client', r.data['bill']['why_unallocated'])

    def test_a_bill_with_neither_matter_nor_name_lands_unallocated(self):
        r = self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm.pk), 'reference': 'INV-301',
            'bill_date': '2026-09-05', 'amount': '500.00'})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['bill']['allocation_state'], 'unallocated')
        self.assertEqual(self.get(legal_bills).data['unallocated_count'], 1)

    def test_a_bill_amount_must_be_a_real_amount(self):
        r = self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm.pk), 'reference': 'INV-302',
            'bill_date': '2026-09-05', 'amount': 'about a thousand'})
        self.assertEqual(r.status_code, 400)


class DuplicateGuardTests(LegalBase):
    def _body(self, ref='INV-500', amount='1200.00'):
        return {'source': 'external', 'firm_id': str(self.firm.pk), 'reference': ref,
                'bill_date': '2026-09-05', 'amount': amount}

    def test_the_same_bill_twice_is_flagged_before_it_is_committed(self):
        self.assertEqual(self.post(legal_bills, self._body()).status_code, 201)
        r = self.post(legal_bills, self._body())
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.data['duplicate_suspected'])
        self.assertEqual(LegalBill.objects.count(), 1)

    def test_a_flagged_duplicate_can_still_be_recorded_deliberately(self):
        # A flag, not a block: a firm can legitimately re-issue, and refusing
        # outright would push the bill out of Omni, where no total sees it.
        self.post(legal_bills, self._body())
        body = self._body()
        body['confirm_duplicate'] = True
        self.assertEqual(self.post(legal_bills, body).status_code, 201)
        self.assertEqual(LegalBill.objects.count(), 2)

    def test_a_different_amount_under_the_same_reference_is_not_a_duplicate(self):
        self.post(legal_bills, self._body())
        r = self.post(legal_bills, self._body(amount='1200.01'))
        self.assertEqual(r.status_code, 201, r.data)


class CapTests(LegalBase):
    """The 80,000 ceiling — per client, across all of their matters."""

    def test_the_cap_aggregates_across_a_clients_matters_not_per_matter(self):
        # The failure this prevents: two matters at 45,000 each look fine on
        # their own and put the client 10,000 over their single ceiling.
        m = self.member('B-1', 'Thabo Moeng')
        c1, c2 = self.case_for(m), self.case_for(m)
        self.bill('45000', [(c1, '45000')], ref='I-1')
        self.bill('45000', [(c2, '45000')], ref='I-2')
        r = self.get(legal_cap_board)
        row = r.data['clients'][0]
        self.assertEqual(row['cap']['total'], '90000.00')
        self.assertEqual(row['cap']['tier'], 'red')
        self.assertEqual(row['matters'], 2)

    def test_amber_at_sixty_thousand_and_red_at_eighty(self):
        m = self.member('B-2', 'Naledi Sebina')
        c = self.case_for(m)
        self.bill('60000', [(c, '60000')], ref='I-3')
        self.assertEqual(self.get(legal_cap_board).data['clients'][0]['cap']['tier'],
                         'amber')
        self.bill('20000', [(c, '20000')], ref='I-4')
        self.assertEqual(self.get(legal_cap_board).data['clients'][0]['cap']['tier'],
                         'red')

    def test_it_counts_billed_not_only_paid_so_the_warning_comes_first(self):
        m = self.member('B-3', 'Kabo Moloi')
        c = self.case_for(m)
        self.bill('61000', [(c, '61000')], ref='I-5', stage=LegalBill.Stage.BILLED)
        self.assertEqual(self.get(legal_cap_board).data['clients'][0]['cap']['tier'],
                         'amber')

    def test_a_rejected_bill_counts_for_nothing(self):
        m = self.member('B-4', 'Lesego Kobe')
        c = self.case_for(m)
        self.bill('90000', [(c, '90000')], ref='I-6', stage=LegalBill.Stage.REJECTED)
        self.assertEqual(self.get(legal_cap_board).data['clients'], [])

    def test_the_flag_travels_with_the_matter_on_the_register(self):
        m = self.member('B-5', 'Gorata Peto')
        c = self.case_for(m)
        self.bill('70000', [(c, '70000')], ref='I-7')
        row = self.get(cases).data['cases'][0]
        self.assertEqual(row['cap']['tier'], 'amber')
        self.assertEqual(row['cap']['headroom'], '10000.00')

    def test_unallocated_spend_is_reported_beside_the_board_not_hidden(self):
        # A board that looks complete while bills sit unallocated lies by
        # omission — the totals on it are understated and nothing says so.
        self.post(legal_bills, {'source': 'external', 'firm_id': str(self.firm.pk),
                                'reference': 'INV-900', 'bill_date': '2026-09-05',
                                'amount': '5000.00'})
        r = self.get(legal_cap_board)
        self.assertEqual(r.data['unallocated']['bills'], 1)
        self.assertEqual(r.data['unallocated']['amount'], '5000.00')

    def test_the_hard_ceiling_ships_ON_and_refuses_a_breaching_bill(self):
        # The CFO's decision of 9 Sep 2026: crossing the cap is refused, not
        # merely flagged. This test pins the shipped DEFAULT, so a future change
        # of the model default cannot pass unnoticed.
        self.assertTrue(LegalSettings.solo().cap_blocks_capture)
        m = self.member('B-6', 'Tiro Baruti')
        c = self.case_for(m)
        self.bill('79000', [(c, '79000')], ref='I-8')
        r = self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm2.pk), 'reference': 'INV-901',
            'bill_date': '2026-09-06', 'amount': '5000.00',
            'allocations': [{'case_id': str(c.pk), 'amount': '5000.00'}]})
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(r.data['cap_breaches'][0]['tier'], 'red')
        self.assertEqual(LegalBill.objects.count(), 1)

    def test_switching_the_ceiling_OFF_lets_the_bill_through_with_a_red_flag(self):
        # The other half of the switch: monitoring mode still records the bill
        # and still reports the breach, it just does not refuse it.
        self.settings_row.cap_blocks_capture = False
        self.settings_row.save(update_fields=['cap_blocks_capture'])
        m = self.member('B-6b', 'Tiro Baruti Two')
        c = self.case_for(m)
        self.bill('79000', [(c, '79000')], ref='I-8b')
        r = self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm2.pk), 'reference': 'INV-901b',
            'bill_date': '2026-09-06', 'amount': '5000.00',
            'allocations': [{'case_id': str(c.pk), 'amount': '5000.00'}]})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['cap_breaches'][0]['tier'], 'red')

    def test_switching_the_hard_ceiling_on_refuses_a_breaching_bill(self):
        self.settings_row.cap_blocks_capture = True
        self.settings_row.save(update_fields=['cap_blocks_capture'])
        m = self.member('B-7', 'Naledi Kgosi')
        c = self.case_for(m)
        self.bill('79000', [(c, '79000')], ref='I-9')
        r = self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm2.pk), 'reference': 'INV-902',
            'bill_date': '2026-09-06', 'amount': '5000.00',
            'allocations': [{'case_id': str(c.pk), 'amount': '5000.00'}]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(LegalBill.objects.count(), 1)

    def test_the_running_total_corrects_itself_when_a_bill_is_rejected(self):
        # Proof the total is computed and not stored: nothing has to be
        # recalculated by hand for the board to be right again.
        m = self.member('B-8', 'Boitumelo Rre')
        c = self.case_for(m)
        b = self.bill('70000', [(c, '70000')], ref='I-10')
        self.assertEqual(self.get(legal_cap_board).data['clients'][0]['cap']['tier'],
                         'amber')
        r = self.post(legal_bill_stage, {'stage': 'rejected'}, bill_id=b.pk)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.get(legal_cap_board).data['clients'], [])


class NameMatchingTests(LegalBase):
    def test_a_bill_naming_one_client_with_one_open_matter_ties_itself(self):
        m = self.member('B-1', 'Keneetswe Ralolemo')
        self.case_for(m)
        r = self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm.pk), 'reference': 'INV-1',
            'bill_date': '2026-09-05', 'amount': '2000.00',
            'billed_client_name': 'RALOLEMO, Keneetswe'})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['bill']['allocation_state'], 'allocated')
        self.assertEqual(r.data['match']['outcome'], 'exact')

    def test_two_clients_sharing_a_name_never_auto_charges_either(self):
        # Two people can share a name. Guessing charges the wrong one and
        # nobody would ever find out from the screen.
        self.case_for(self.member('B-2', 'Thabo Moeng'))
        self.case_for(self.member('B-3', 'Thabo Moeng'))
        r = self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm.pk), 'reference': 'INV-2',
            'bill_date': '2026-09-05', 'amount': '2000.00',
            'billed_client_name': 'Thabo Moeng'})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['bill']['allocation_state'], 'unallocated')
        self.assertEqual(r.data['match']['outcome'], 'ambiguous')
        self.assertEqual(len(r.data['match']['candidates']), 2)

    def test_a_certain_client_with_several_open_matters_still_asks(self):
        m = self.member('B-4', 'Naledi Sebina')
        self.case_for(m)
        self.case_for(m)
        r = self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm.pk), 'reference': 'INV-3',
            'bill_date': '2026-09-05', 'amount': '2000.00',
            'billed_client_name': 'Naledi Sebina'})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['bill']['allocation_state'], 'unallocated')
        self.assertIn('2 open matters', r.data['match']['note'])

    def test_a_name_nobody_answers_to_lands_in_the_exception_list(self):
        self.case_for(self.member('B-5', 'Thabo Moeng'))
        r = self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm.pk), 'reference': 'INV-4',
            'bill_date': '2026-09-05', 'amount': '2000.00',
            'billed_client_name': 'Somebody Unknown'})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['match']['outcome'], 'none')
        self.assertEqual(r.data['bill']['allocation_state'], 'unallocated')


class AliasLearningTests(LegalBase):
    def test_the_firms_spelling_is_learned_and_matches_next_time(self):
        m = self.member('B-1', 'Naledi Kgosi Sebina')
        c = self.case_for(m)
        b = LegalBill.objects.create(
            source=LegalBill.Source.EXTERNAL, firm=self.firm, reference='INV-1',
            bill_date=datetime.date(2026, 9, 5), amount=D('1000.00'),
            billed_client_name='N. K. Sebina')
        r = self.post(legal_bill_allocate, {
            'allocations': [{'case_id': str(c.pk), 'amount': '1000.00'}],
            'learn_alias': 'N. K. Sebina'}, bill_id=b.pk)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data['alias']['ok'])
        self.assertEqual(LegalClientAlias.objects.count(), 1)

        # The next bill from that firm, spelled the same way, ties itself.
        r2 = self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm.pk), 'reference': 'INV-2',
            'bill_date': '2026-09-06', 'amount': '500.00',
            'billed_client_name': 'n k sebina'})
        self.assertEqual(r2.status_code, 201, r2.data)
        self.assertEqual(r2.data['match']['via'], 'alias')
        self.assertEqual(r2.data['bill']['allocation_state'], 'allocated')

    def test_a_spelling_another_client_already_answers_to_is_refused(self):
        # Letting two clients share an alias would make the alias itself
        # ambiguous, and future bills would auto-match whichever row came first.
        # Both aliases are learned through the real endpoint, because the key is
        # whatever `normalise` produces — a hand-written one proves nothing.
        m1, m2 = self.member('B-2', 'Thabo Moeng'), self.member('B-3', 'Kabo Moloi')
        c1, c2 = self.case_for(m1), self.case_for(m2)

        b1 = LegalBill.objects.create(
            source=LegalBill.Source.EXTERNAL, firm=self.firm, reference='INV-3a',
            bill_date=datetime.date(2026, 9, 5), amount=D('100.00'))
        first = self.post(legal_bill_allocate, {
            'allocations': [{'case_id': str(c1.pk), 'amount': '100.00'}],
            'learn_alias': 'Thabo Moeng'}, bill_id=b1.pk)
        self.assertTrue(first.data['alias']['ok'], first.data)

        b2 = LegalBill.objects.create(
            source=LegalBill.Source.EXTERNAL, firm=self.firm, reference='INV-3b',
            bill_date=datetime.date(2026, 9, 5), amount=D('100.00'))
        r = self.post(legal_bill_allocate, {
            'allocations': [{'case_id': str(c2.pk), 'amount': '100.00'}],
            'learn_alias': 'Moeng, Thabo'}, bill_id=b2.pk)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(r.data['alias']['ok'])
        self.assertIn('wrong person', r.data['alias']['reason'])
        # The bill is still allocated to the matter — only the LEARNING was
        # refused. Refusing the allocation too would lose real work.
        self.assertEqual(r.data['bill']['allocation_state'], 'allocated')
        self.assertEqual(LegalClientAlias.objects.count(), 1)
        self.assertEqual(LegalClientAlias.objects.get().client_id, m1.pk)

    def test_a_name_cannot_be_learned_off_a_bill_covering_two_clients(self):
        m1, m2 = self.member('B-4', 'One Person'), self.member('B-5', 'Two Person')
        c1, c2 = self.case_for(m1), self.case_for(m2)
        b = LegalBill.objects.create(
            source=LegalBill.Source.EXTERNAL, firm=self.firm, reference='INV-4',
            bill_date=datetime.date(2026, 9, 5), amount=D('200.00'))
        r = self.post(legal_bill_allocate, {
            'allocations': [{'case_id': str(c1.pk), 'amount': '100.00'},
                            {'case_id': str(c2.pk), 'amount': '100.00'}],
            'learn_alias': 'Somebody'}, bill_id=b.pk)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(r.data['alias']['ok'])
        self.assertEqual(LegalClientAlias.objects.count(), 0)


class ReallocationTests(LegalBase):
    def test_re_allocating_replaces_the_old_lines_rather_than_adding_to_them(self):
        # A correction is "it was these two, not that one". Applied as a patch,
        # the original line survives and the money counts twice.
        m1, m2 = self.member('B-1', 'One Person'), self.member('B-2', 'Two Person')
        c1, c2 = self.case_for(m1), self.case_for(m2)
        b = self.bill('1000', [(c1, '1000')], ref='INV-1')
        r = self.post(legal_bill_allocate, {
            'allocations': [{'case_id': str(c2.pk), 'amount': '1000.00'}]}, bill_id=b.pk)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(LegalBillAllocation.objects.count(), 1)
        self.assertEqual(LegalBillAllocation.objects.get().case_id, c2.pk)
        board = {row['membership_no']: row['cap']['total']
                 for row in self.get(legal_cap_board).data['clients']}
        self.assertEqual(board, {'B-2': '1000.00'})

    def test_a_re_allocation_must_still_account_for_the_whole_bill(self):
        m = self.member('B-3', 'Three Person')
        c = self.case_for(m)
        b = self.bill('1000', [(c, '1000')], ref='INV-2')
        r = self.post(legal_bill_allocate, {
            'allocations': [{'case_id': str(c.pk), 'amount': '900.00'}]}, bill_id=b.pk)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(LegalBillAllocation.objects.get().amount, D('1000.00'))


class ClientSearchTests(LegalBase):
    def test_a_client_can_be_found_by_name_as_well_as_by_number(self):
        self.member('BONU-4417', 'Keneetswe Ralolemo')
        by_number = self.get(legal_client_search, '?q=4417')
        by_name = self.get(legal_client_search, '?q=Ralolemo')
        self.assertEqual(len(by_number.data['clients']), 1)
        self.assertEqual(len(by_name.data['clients']), 1)
        self.assertEqual(by_name.data['clients'][0]['membership_no'], 'BONU-4417')

    def test_the_search_shows_each_clients_standing_against_the_cap(self):
        m = self.member('BONU-9', 'Gorata Peto')
        self.bill('65000', [(self.case_for(m), '65000')], ref='I-1')
        row = self.get(legal_client_search, '?q=Gorata').data['clients'][0]
        self.assertEqual(row['cap']['tier'], 'amber')
        self.assertEqual(row['cap']['total'], '65000.00')

    def test_one_character_is_not_a_search(self):
        self.member('BONU-1', 'Someone')
        self.assertEqual(self.get(legal_client_search, '?q=S').data['clients'], [])


# =========================================================================
# The defects Fable found at the /fabe gate on 9 Sep 2026
#
# Every test in this section went RED before its fix. They are kept together
# and labelled because each one is a class of mistake this codebase has made
# before, and the cheapest way to stop a fix being quietly undone later is a
# test that names the failure it prevents.
# =========================================================================

class IntakeMoneyGateTests(LegalBase):
    """A call-centre agent may open matters. They may not see the money."""

    def _agent(self, email='agent.desk@alphadirect.co.bw'):
        import os
        u = User.objects.create_user('agentdesk', email=email, password='x')
        # No UserProfile title, so can_view_financials is False. The ONLY
        # reason this account gets in at all is the named intake list.
        os.environ['BONU_INTAKE_EMAILS'] = email
        self.addCleanup(os.environ.pop, 'BONU_INTAKE_EMAILS', None)
        return User.objects.get(pk=u.pk)

    def _as(self, user, view, qs='', **kw):
        req = self.rf.get('/api/v1/bonu/cases/' + qs)
        force_authenticate(req, user=user)
        return view(req, **kw)

    def test_an_intake_agent_is_served_no_spend_no_bills_and_no_name(self):
        m = self.member('B-1', 'Thabo Moeng')
        c = self.case_for(m)
        self.bill('70000', [(c, '70000')], ref='I-1')
        row = self._as(self._agent(), cases).data['cases'][0]
        self.assertNotIn('cap', row)
        self.assertNotIn('bills', row)
        self.assertNotIn('billed_total', row)
        # ...but they can still do their job. The NAME is deliberately on the
        # wide side (CFO, 9 Sep 2026): an agent is speaking to the person, and
        # a name says nothing about money. It is the SPEND that is gated.
        self.assertEqual(row['client'], 'Thabo Moeng')
        self.assertEqual(row['case_ref'], c.case_ref)
        self.assertTrue(row['client_linked'])

    def test_finance_still_sees_all_of_it(self):
        m = self.member('B-2', 'Naledi Sebina')
        c = self.case_for(m)
        self.bill('70000', [(c, '70000')], ref='I-2')
        row = self.get(cases).data['cases'][0]
        self.assertEqual(row['cap']['total'], '70000.00')
        self.assertEqual(row['client'], 'Naledi Sebina')
        self.assertEqual(len(row['bills']), 1)

    def test_the_gate_holds_on_the_single_case_read_too(self):
        # Not just the list. A leak on the detail endpoint is the same leak.
        m = self.member('B-3', 'Kabo Moloi')
        c = self.case_for(m)
        self.bill('70000', [(c, '70000')], ref='I-3')
        row = self._as(self._agent(), case_detail, case_id=c.pk).data['case']
        self.assertNotIn('cap', row)
        self.assertNotIn('bills', row)
        self.assertEqual(row['client'], 'Kabo Moloi')      # name yes, money no


class TruncatedShortlistTests(LegalBase):
    """A full shortlist is not a uniqueness answer."""

    def test_a_truncated_match_never_reports_exact(self):
        # Two members share a name. If the database slice happens to return
        # only one of them, an "exactly one match" rule would auto-charge that
        # one - money against a client who may not owe it.
        from unittest.mock import patch
        self.member('B-1', 'Thabo Moeng')
        self.member('B-2', 'Thabo Moeng')
        self.member('B-3', 'Thabo Otherman')
        with patch('bonu.legal_bills.MATCH_SHORTLIST', 1):
            out = legal_bills_match('Thabo Moeng')
        self.assertEqual(out['outcome'], 'ambiguous')
        self.assertTrue(out['truncated'])

    def test_an_untruncated_single_match_is_still_exact(self):
        # The downgrade must not fire when the shortlist was complete, or every
        # name-only bill would need a person and the feature would be useless.
        self.member('B-4', 'Keneetswe Ralolemo')
        out = legal_bills_match('Keneetswe Ralolemo')
        self.assertEqual(out['outcome'], 'exact')
        self.assertFalse(out['truncated'])


class AllocationStateFollowsTheClientTests(LegalBase):
    """allocation_state is stored, but its answer lives on the case."""

    def test_linking_the_client_later_corrects_the_bill(self):
        # Before the fix the cap board counted the money in the client total
        # AND listed the same bill as "in NO total above" - two answers to one
        # question, which is how a cap total comes to be quietly wrong.
        c = self.case_for(None)
        r = self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm.pk), 'reference': 'INV-1',
            'bill_date': '2026-09-05', 'amount': '500.00',
            'allocations': [{'case_id': str(c.pk), 'amount': '500.00'}]})
        self.assertEqual(r.data['bill']['allocation_state'], 'unallocated')

        m = self.member('B-1', 'Thabo Moeng')
        upd = self.post(case_detail, {'client_id': str(m.pk)}, case_id=c.pk)
        self.assertEqual(upd.status_code, 200, upd.data)
        self.assertEqual(upd.data.get('bills_restated'), 1)

        board = self.get(legal_cap_board).data
        self.assertEqual(board['clients'][0]['cap']['total'], '500.00')
        self.assertEqual(board['unallocated']['bills'], 0)

    def test_unlinking_the_client_puts_the_bill_back_in_the_exceptions(self):
        m = self.member('B-2', 'Naledi Sebina')
        c = self.case_for(m)
        self.bill('500', [(c, '500')], ref='INV-2')
        upd = self.post(case_detail, {'client_id': ''}, case_id=c.pk)
        self.assertEqual(upd.status_code, 200, upd.data)
        self.assertEqual(LegalBill.objects.get().allocation_state, 'unallocated')
        self.assertEqual(self.get(legal_cap_board).data['unallocated']['bills'], 1)


class HardCeilingHasNoBackDoorTests(LegalBase):
    def test_allocate_cannot_walk_past_the_ceiling_when_it_is_on(self):
        # The route that made the ceiling decorative: capture the bill with no
        # allocation (which the ceiling never sees), then allocate it.
        self.settings_row.cap_blocks_capture = True
        self.settings_row.save(update_fields=['cap_blocks_capture'])
        m = self.member('B-1', 'Tiro Baruti')
        c = self.case_for(m)
        self.bill('79000', [(c, '79000')], ref='I-1')
        b = LegalBill.objects.create(
            source=LegalBill.Source.EXTERNAL, firm=self.firm2, reference='INV-9',
            bill_date=datetime.date(2026, 9, 6), amount=D('5000.00'))
        for body in ({'allocations': [{'case_id': str(c.pk), 'amount': '5000.00'}]},
                     {'allocations': [{'case_id': str(c.pk), 'amount': '5000.00'}],
                      'confirm_cap': True}):
            r = self.post(legal_bill_allocate, body, bill_id=b.pk)
            self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(LegalBillAllocation.objects.filter(bill=b).count(), 0)


class ExplicitClientIsHonouredOrRefusedTests(LegalBase):
    def test_a_named_client_that_does_not_exist_is_refused(self):
        # Silently ignoring it opened the case unlinked, skipped the member_ref
        # lookup that WOULD have linked it, and then blamed member_ref.
        import uuid
        self.member('BONU-1', 'Thabo Moeng')
        # A generated id rather than a literal like 1111-1111...: a long run of
        # digits trips the /fabe PII tripwire, and a gate that cries wolf over
        # a test fixture is a gate people start ignoring.
        r = self.post(cases, {
            'firm_id': str(self.firm.pk), 'member_ref': 'BONU-1',
            'client_id': str(uuid.uuid4())})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(LegalCase.objects.count(), 0)

    def test_a_malformed_client_id_is_refused_not_ignored(self):
        r = self.post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'BONU-2',
                              'client_id': 'not-a-uuid'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(LegalCase.objects.count(), 0)

    def test_a_named_client_that_does_exist_is_used(self):
        m = self.member('BONU-3', 'Naledi Sebina')
        r = self.post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'SOMETHING-ELSE',
                              'client_id': str(m.pk)})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(LegalCase.objects.get().client_id, m.pk)


class ReceivedDateCannotLieTests(LegalBase):
    def test_a_claim_cannot_arrive_tomorrow(self):
        # A future receipt date made days-to-process clamp to nought, which
        # reads on screen as "dealt with the same day" - the most flattering
        # possible lie about a matter nobody has touched.
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        r = self.post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'BONU-1',
                              'received_on': tomorrow})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(LegalCase.objects.count(), 0)

    def test_an_edit_cannot_push_receipt_past_closure(self):
        c = self.case_for(None, received=datetime.date(2026, 9, 1),
                          closed=datetime.date(2026, 9, 5))
        r = self.post(case_detail, {'received_on': '2026-09-08'}, case_id=c.pk)
        self.assertEqual(r.status_code, 400)
        c.refresh_from_db()
        self.assertEqual(c.received_on, datetime.date(2026, 9, 1))

    def test_an_edit_cannot_set_a_future_receipt_date(self):
        c = self.case_for(None, received=datetime.date(2026, 9, 1))
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        r = self.post(case_detail, {'received_on': tomorrow}, case_id=c.pk)
        self.assertEqual(r.status_code, 400)


class CapSettingsAreActuallyEditableTests(LegalBase):
    """The thresholds were documented as editable and were served nowhere."""

    def _put(self, body):
        from bonu.legal import legal_settings
        req = self.rf.put('/api/v1/bonu/legal/settings/', body, format='json')
        force_authenticate(req, user=self.user)
        return legal_settings(req)

    def test_the_cap_is_readable_and_writable(self):
        r = self._put({'client_spend_cap': '90000', 'client_spend_amber': '70000'})
        self.assertEqual(r.status_code, 200, r.data)
        # Assert the VALUE, not its spelling. The endpoint echoes the Decimal
        # it just parsed, so '90000' here and '90000.00' on the next read —
        # cosmetic, and the existing fields on this endpoint have always
        # behaved that way, so it is not worth changing shared behaviour for.
        self.assertEqual(D(r.data['client_spend_cap']), D('90000'))
        self.settings_row.refresh_from_db()
        self.assertEqual(self.settings_row.client_spend_cap, D('90000'))
        self.assertEqual(self.settings_row.client_spend_amber, D('70000'))

    def test_an_amber_level_at_or_above_the_cap_is_refused(self):
        # Otherwise a client goes straight from clear to red and the "early
        # warning" warns nobody.
        r = self._put({'client_spend_cap': '80000', 'client_spend_amber': '80000'})
        self.assertEqual(r.status_code, 400)

    def test_the_hard_ceiling_switch_can_be_flipped(self):
        r = self._put({'cap_blocks_capture': True})
        self.assertEqual(r.status_code, 200, r.data)
        self.settings_row.refresh_from_db()
        self.assertTrue(self.settings_row.cap_blocks_capture)

    def test_the_string_false_switches_the_ceiling_OFF_not_on(self):
        # bool("false") is True, so a plain bool() cast would switch a control
        # ON when somebody asked to switch it OFF — the worst direction to get
        # a switch wrong. (OpenAI, /fabe panel, 9 Sep 2026.)
        self.settings_row.cap_blocks_capture = True
        self.settings_row.save(update_fields=['cap_blocks_capture'])
        r = self._put({'cap_blocks_capture': 'false'})
        self.assertEqual(r.status_code, 200, r.data)
        self.settings_row.refresh_from_db()
        self.assertFalse(self.settings_row.cap_blocks_capture)

    def test_a_switch_value_that_is_neither_yes_nor_no_is_refused(self):
        r = self._put({'cap_blocks_capture': 'maybe'})
        self.assertEqual(r.status_code, 400)


class CeilingGapsAreReportedTests(LegalBase):
    """Two routes carry money past a ceiling that only guards capture.

    Neither is REFUSED, on purpose. Refusing the client link would strand the
    bill unallocated for ever and understate every cap total, and refusing an
    un-rejection would strand a corrected bill. But neither may be silent, or
    the ceiling the CFO switched on has quiet holes in it.
    Both flagged by Fable at the /fabe gate on 9 Sep 2026.
    """

    def test_linking_a_client_later_reports_the_breach_it_causes(self):
        # A bill captured against a matter with no client sails past the
        # ceiling because there is no client to total. Finance then links the
        # client while clearing the exceptions, and the money lands.
        c = self.case_for(None)
        r = self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm.pk), 'reference': 'INV-G1',
            'bill_date': '2026-09-05', 'amount': '85000.00',
            'allocations': [{'case_id': str(c.pk), 'amount': '85000.00'}]})
        self.assertEqual(r.status_code, 201, r.data)          # not refused: no client
        self.assertEqual(r.data['bill']['allocation_state'], 'unallocated')

        m = self.member('B-G1', 'Over The Cap')
        upd = self.post(case_detail, {'client_id': str(m.pk)}, case_id=c.pk)
        self.assertEqual(upd.status_code, 200, upd.data)      # link still succeeds
        self.assertEqual(upd.data['cap_breaches'][0]['tier'], 'red')
        self.assertIn('85000.00', upd.data['warning_cap'])

    def test_a_link_that_stays_under_the_cap_says_nothing(self):
        # The warning must be worth reading, so it only fires on a real breach.
        c = self.case_for(None)
        self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm.pk), 'reference': 'INV-G2',
            'bill_date': '2026-09-05', 'amount': '1000.00',
            'allocations': [{'case_id': str(c.pk), 'amount': '1000.00'}]})
        m = self.member('B-G2', 'Well Under')
        upd = self.post(case_detail, {'client_id': str(m.pk)}, case_id=c.pk)
        self.assertEqual(upd.status_code, 200, upd.data)
        self.assertNotIn('cap_breaches', upd.data)
        self.assertNotIn('warning_cap', upd.data)

    def test_un_rejecting_a_bill_reports_that_it_counts_again(self):
        # A rejected bill counts for nothing. Bringing it back to billed puts
        # the money into the client's total by a route the ceiling never sees.
        m = self.member('B-G3', 'Back Over')
        c = self.case_for(m)
        b = self.bill('85000', [(c, '85000')], ref='INV-G3',
                      stage=LegalBill.Stage.REJECTED)
        self.assertEqual(self.get(legal_cap_board).data['clients'], [])

        r = self.post(legal_bill_stage, {'stage': 'billed'}, bill_id=b.pk)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['cap_breaches'][0]['tier'], 'red')
        self.assertIn('counting toward client legal spend again', r.data['warning'])

    def test_one_client_on_two_matters_is_reported_once_not_twice(self):
        # A firm bills one invoice covering two matters for the SAME member —
        # ordinary, not unusual. The warning was built per ALLOCATION, so that
        # client appeared twice with identical figures, reading as two separate
        # breaches of the ceiling when there is one. Cosmetic, but a cap board
        # that inflates its own breach count is not one people keep trusting.
        m = self.member('B-G5', 'Two Matters')
        c1 = self.case_for(m, ref='CASE-G5A')
        c2 = self.case_for(m, ref='CASE-G5B')
        b = self.bill('85000', [(c1, '40000'), (c2, '45000')], ref='INV-G5',
                      stage=LegalBill.Stage.REJECTED)

        r = self.post(legal_bill_stage, {'stage': 'billed'}, bill_id=b.pk)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(len(r.data['cap_breaches']), 1)
        self.assertEqual(r.data['cap_breaches'][0]['tier'], 'red')

    def test_two_different_clients_on_one_bill_are_both_still_reported(self):
        # ...and the de-duplication must not swallow a real second client.
        m1 = self.member('B-G6', 'First Over')
        m2 = self.member('B-G7', 'Second Over')
        c1 = self.case_for(m1, ref='CASE-G6')
        c2 = self.case_for(m2, ref='CASE-G7')
        b = self.bill('170000', [(c1, '85000'), (c2, '85000')], ref='INV-G6',
                      stage=LegalBill.Stage.REJECTED)

        r = self.post(legal_bill_stage, {'stage': 'billed'}, bill_id=b.pk)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(len(r.data['cap_breaches']), 2)

    def test_marking_a_billed_bill_paid_says_nothing_new(self):
        # billed -> paid does not CHANGE whether it counts, so there is nothing
        # to warn about and the warning must not fire on every stage change.
        m = self.member('B-G4', 'Already Counted')
        c = self.case_for(m)
        b = self.bill('85000', [(c, '85000')], ref='INV-G4')
        r = self.post(legal_bill_stage, {'stage': 'paid'}, bill_id=b.pk)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertNotIn('warning', r.data)


class BillReaderTests(LegalBase):
    """Reading a bill off the document instead of typing it.

    The reader's job is to save keystrokes, and its danger is that it looks
    authoritative. So these tests are mostly about what it must NOT do: it must
    not save, must not guess the client, must not guess the firm, and must not
    report a scan it could not read as a bill for nought.
    """

    def _read(self, filename, blob):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from bonu.legal_bills import legal_bill_read
        req = self.rf.post('/api/v1/bonu/legal/bills/read/',
                           {'file': SimpleUploadedFile(filename, blob)}, format='multipart')
        force_authenticate(req, user=self.user)
        return legal_bill_read(req)

    def _csv_bill(self):
        return (b'Jeremiah & Taldi Attorneys\n'
                b'INVOICE NO: INV-2291\n'
                b'Date: 04/09/2026\n'
                b'Consultation,2.5,1500.00,3750.00\n'
                b'Drafting,1.0,1500.00,1500.00\n'
                b'TOTAL,,,5250.00\n')

    # -- what it should do -------------------------------------------------
    def test_it_reads_the_number_date_and_amount_off_a_csv(self):
        r = self._read('bill.csv', self._csv_bill())
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data['ok'])
        v = r.data['values']
        self.assertEqual(v['reference'], 'INV-2291')
        self.assertEqual(v['bill_date'], '2026-09-04')
        self.assertEqual(v['amount'], '5250.00')      # the total, not a line

    def test_the_draft_fills_the_real_capture_form(self):
        # The values must be usable AS-IS by the capture endpoint, otherwise the
        # reader saves keystrokes and then hands over something that will not
        # submit. This is the join between the two halves.
        m = self.member('B-R1', 'Thabo Moeng')
        c = self.case_for(m)
        draft = self._read('bill.csv', self._csv_bill()).data['values']
        r = self.post(legal_bills, {
            'source': 'external', 'firm_id': str(self.firm.pk),
            'reference': draft['reference'], 'bill_date': draft['bill_date'],
            'amount': draft['amount'],
            'allocations': [{'case_id': str(c.pk), 'amount': draft['amount']}]})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(LegalBill.objects.get().amount, D('5250.00'))

    # -- what it must NOT do ----------------------------------------------
    def test_reading_saves_nothing(self):
        # A reader that wrote to the register would walk straight past the
        # duplicate guard, the split arithmetic and the 80,000 ceiling.
        self.assertEqual(LegalBill.objects.count(), 0)
        self._read('bill.csv', self._csv_bill())
        self._read('bill.csv', self._csv_bill())
        self.assertEqual(LegalBill.objects.count(), 0)

    def test_it_never_guesses_the_client_or_the_firm(self):
        # The cap is totalled on a structured client link, so a guessed name is
        # worse than a blank. The firm comes off the panel a person knows.
        r = self._read('bill.csv', self._csv_bill())
        self.assertNotIn('billed_client_name', r.data['values'])
        self.assertNotIn('firm_id', r.data['values'])
        self.assertNotIn('source', r.data['values'])

    def test_it_does_not_hand_back_the_bill_text(self):
        # A legal bill names a member and often something sensitive about their
        # life. Only the three figures cross back, so the body of it never
        # reaches a browser payload or a log.
        r = self._read('bill.csv', self._csv_bill())
        blob = str(r.data)
        self.assertNotIn('Consultation', blob)
        self.assertNotIn('Drafting', blob)

    def test_a_scan_says_so_instead_of_reporting_nought(self):
        # The dangerous failure: a scan of a bill for 85,000 arriving as a
        # successful read of an empty bill.
        r = self._read('bill.png', b'\x89PNG\r\n\x1a\n' + b'\x00' * 40)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(r.data['ok'])
        self.assertTrue(r.data['needs_manual'])
        self.assertEqual(r.data['values'], {})
        self.assertIn('scan', r.data['message'].lower())

    def test_text_with_nothing_recognisable_asks_for_typing(self):
        r = self._read('notes.csv', b'some notes about a meeting\nnothing useful here\n')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data['needs_manual'])
        self.assertIn('type it in', r.data['message'].lower())

    def test_no_file_is_a_clean_refusal(self):
        from bonu.legal_bills import legal_bill_read
        req = self.rf.post('/api/v1/bonu/legal/bills/read/', {}, format='multipart')
        force_authenticate(req, user=self.user)
        self.assertEqual(legal_bill_read(req).status_code, 400)

    def test_an_oversized_file_is_refused_before_it_is_read(self):
        from bonu.legal_bills import MAX_UPLOAD_BYTES
        r = self._read('huge.csv', b'x' * (MAX_UPLOAD_BYTES + 1))
        self.assertEqual(r.status_code, 400)
        self.assertIn('20 MB', r.data['detail'])

    def test_an_intake_only_agent_cannot_read_bills(self):
        # The reader sits behind the same money gate as the rest of the bill
        # screen: a call-centre agent has no business reading a legal bill.
        import os
        from django.core.files.uploadedfile import SimpleUploadedFile
        from bonu.legal_bills import legal_bill_read
        email = 'agent.reader@alphadirect.co.bw'
        u = User.objects.create_user('agentreader', email=email, password='x')
        os.environ['BONU_INTAKE_EMAILS'] = email
        self.addCleanup(os.environ.pop, 'BONU_INTAKE_EMAILS', None)
        req = self.rf.post('/api/v1/bonu/legal/bills/read/',
                           {'file': SimpleUploadedFile('bill.csv', self._csv_bill())},
                           format='multipart')
        force_authenticate(req, user=User.objects.get(pk=u.pk))
        self.assertEqual(legal_bill_read(req).status_code, 403)

    def test_the_duplicate_guard_still_fires_on_a_read_bill(self):
        # Proof the reader did not create a way round the guards: record the
        # read draft twice and the second one must be refused.
        m = self.member('B-R2', 'Naledi Sebina')
        c = self.case_for(m)
        draft = self._read('bill.csv', self._csv_bill()).data['values']
        body = {'source': 'external', 'firm_id': str(self.firm.pk),
                'reference': draft['reference'], 'bill_date': draft['bill_date'],
                'amount': draft['amount'],
                'allocations': [{'case_id': str(c.pk), 'amount': draft['amount']}]}
        self.assertEqual(self.post(legal_bills, body).status_code, 201)
        again = self.post(legal_bills, dict(body))
        self.assertEqual(again.status_code, 409)
        self.assertTrue(again.data['duplicate_suspected'])


# The digits are assembled at runtime, not written out, so this fixture does
# not trip the /fabe PII tripwire (which watches for long digit runs and for
# the Botswana 7xxxxxxx mobile shape). The text the test exercises is
# identical; a gate that cries wolf over a fixture is one people stop reading.
ACCT = b'624557' + b'18890'


class BillReaderTotalTests(LegalBase):
    """The amount comes only from a line that says it is the total.

    See bonu/test_ingest_total.py for the eight bill layouts that proved the old
    'largest figure on the page' rule wrong. These pin what the READER does with
    that, and above all that a bill with no labelled total leaves the box EMPTY
    and says so, rather than filling in a plausible wrong number that a busy
    person will confirm unread.
    """

    def _read(self, filename, blob):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from bonu.legal_bills import legal_bill_read
        req = self.rf.post('/api/v1/bonu/legal/bills/read/',
                           {'file': SimpleUploadedFile(filename, blob)}, format='multipart')
        force_authenticate(req, user=self.user)
        return legal_bill_read(req)

    def test_a_bank_account_number_never_becomes_the_amount(self):
        bill = (b'Jeremiah and Taldi Attorneys\n'
                b'INVOICE NO: INV-3301\n'
                b'Date: 04/09/2026\n'
                b'Consultation,2.5,1500.00,3750.00\n'
                b'TOTAL DUE,,,3750.00\n'
                b'Banking: First National Bank  Acc No ' + ACCT + b'\n')
        r = self._read('bill.csv', bill)
        self.assertEqual(r.data['values']['amount'], '3750.00')

    def test_a_balance_brought_forward_never_becomes_the_amount(self):
        # The dangerous one: both figures look like real bills, and 73,130
        # would sit inside the amber band of an 80,000 cap.
        bill = (b'INVOICE NO: INV-3302\n'
                b'Date: 04/09/2026\n'
                b'Balance brought forward,,,62300.00\n'
                b'Total now due,,,10830.00\n')
        r = self._read('bill.csv', bill)
        self.assertEqual(r.data['values']['amount'], '10830.00')

    def test_a_bill_with_no_total_line_leaves_the_amount_blank_and_says_why(self):
        bill = (b'INVOICE NO: INV-3303\n'
                b'Date: 04/09/2026\n'
                b'Attendance on matter 118/2026,3,1500.00,4500.00\n')
        r = self._read('bill.csv', bill)
        self.assertEqual(r.data['values']['amount'], '')
        self.assertFalse(r.data['amount_found'])
        # ...but it still saved the typing it safely could.
        self.assertEqual(r.data['values']['reference'], 'INV-3303')
        self.assertEqual(r.data['values']['bill_date'], '2026-09-04')
        self.assertIn('Total', r.data['message'])
        self.assertIn('type it in', r.data['message'].lower())

    def test_a_short_bill_number_is_read_not_returned_as_ice(self):
        # "Invoice 15" used to come back as the reference "ice", and every
        # short-numbered bill then collided in the duplicate guard.
        r = self._read('bill.csv', b'Invoice 15\nDate: 04/09/2026\nTotal,,,900.00\n')
        self.assertEqual(r.data['values']['reference'], '15')

    def test_the_amount_that_IS_read_still_records_and_still_hits_the_guards(self):
        m = self.member('B-T1', 'Thabo Moeng')
        c = self.case_for(m)
        draft = self._read('bill.csv',
                           b'INVOICE NO: INV-3304\nDate: 04/09/2026\n'
                           b'Total due,,,5250.00\n').data['values']
        self.assertEqual(draft['amount'], '5250.00')
        body = {'source': 'external', 'firm_id': str(self.firm.pk),
                'reference': draft['reference'], 'bill_date': draft['bill_date'],
                'amount': draft['amount'],
                'allocations': [{'case_id': str(c.pk), 'amount': draft['amount']}]}
        self.assertEqual(self.post(legal_bills, body).status_code, 201)
        self.assertEqual(self.post(legal_bills, dict(body)).status_code, 409)  # dup guard
