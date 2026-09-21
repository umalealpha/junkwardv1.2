"""Position tiers, salary bands, tier-driven signatories, and the over-band
exception gate (Unami Hiring-SOP, CFO 2026-09-02).

These tests pin the behaviour that made omni "not according to our model":
* who signs now depends on the role's TIER, not a fixed five;
* the COO is no longer a recruitment signatory;
* a basic-salary offer above the tier ceiling is blocked without a written
  justification (below the floor is not flagged).
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from recruitment.models import AuthorityToRecruit, JobTitleTier, PositionTier


def _users():
    return {
        'ceo':             User.objects.create_user('aiyer', email='aiyer@alphadirect.co.bw'),
        'coo':             User.objects.create_user('arjuniyer', email='arjuniyer@alphadirect.co.bw'),
        'human_capital':   User.objects.create_user('ubutale', email='ubutale@alphadirect.co.bw'),
        'hr_bp':           User.objects.create_user('dikgopoleng', email='dikgopoleng@alphadirect.co.bw'),
        'finance_manager': User.objects.create_user('ktshutlhedi', email='ktshutlhedi@alphadirect.co.bw'),
        'cfo':             User.objects.create_user('pganesharajah', email='pganesharajah@alphadirect.co.bw'),
    }


class TierSeedTests(APITestCase):
    def test_five_tiers_are_seeded_by_the_migration(self):
        self.assertEqual(list(PositionTier.objects.values_list('tier', flat=True)), [1, 2, 3, 4, 5])
        self.assertEqual(PositionTier.objects.get(tier=3).name, 'Controller')

    def test_financial_controller_is_seeded_to_tier_3(self):
        jt = JobTitleTier.objects.get(title__iexact='Financial Controller')
        self.assertEqual(jt.tier.tier, 3)

    def test_bands_start_empty_for_human_capital_to_fill(self):
        for t in PositionTier.objects.all():
            self.assertIsNone(t.basic_salary_min)
            self.assertIsNone(t.basic_salary_max)


class TierChainTests(APITestCase):
    def _atr(self, tier_no, **kw):
        base = dict(person_name='Test Person', position='Whatever',
                    tier=PositionTier.objects.get(tier=tier_no))
        base.update(kw)
        return AuthorityToRecruit.objects.create(**base)

    def test_junior_chain_has_hr_bp_hiring_manager_hcm_fm_cfo(self):
        a = self._atr(1, hiring_manager_email='mgr@alphadirect.co.bw', hiring_manager_name='A Manager')
        slugs = [s for s, _l, _e in a.signatory_chain()]
        self.assertEqual(slugs, ['hr_bp', 'hiring_manager', 'human_capital', 'finance_manager', 'cfo'])

    def test_controller_chain_has_hcm_hiring_manager_ceo_cfo(self):
        a = self._atr(3, hiring_manager_email='mgr@alphadirect.co.bw')
        slugs = [s for s, _l, _e in a.signatory_chain()]
        self.assertEqual(slugs, ['human_capital', 'hiring_manager', 'ceo', 'cfo'])

    def test_csuite_chain_has_hcm_ceo_cfo_board_chair(self):
        a = self._atr(5)
        slugs = [s for s, _l, _e in a.signatory_chain()]
        self.assertEqual(slugs, ['human_capital', 'ceo', 'cfo', 'board_chair'])

    def test_coo_is_not_a_signatory_on_any_tier(self):
        for n in (1, 2, 3, 4, 5):
            a = self._atr(n, hiring_manager_email='mgr@alphadirect.co.bw')
            self.assertNotIn('coo', [s for s, _l, _e in a.signatory_chain()], f'tier {n}')

    def test_hiring_manager_resolves_to_the_authority_person(self):
        a = self._atr(2, hiring_manager_email='Boss@AlphaDirect.co.bw', hiring_manager_name='The Boss')
        chain = dict((s, e) for s, _l, e in a.signatory_chain())
        self.assertEqual(chain['hiring_manager'], 'boss@alphadirect.co.bw')

    def test_a_tierless_authority_keeps_the_legacy_five(self):
        a = AuthorityToRecruit.objects.create(person_name='Legacy', position='Old')
        slugs = [s for s, _l, _e in a.signatory_chain()]
        self.assertEqual(slugs, ['ceo', 'coo', 'human_capital', 'hr_bp', 'cfo'])


class SalaryBandTests(APITestCase):
    def setUp(self):
        self.t3 = PositionTier.objects.get(tier=3)
        self.t3.basic_salary_min = Decimal('20000')
        self.t3.basic_salary_max = Decimal('40000')
        self.t3.save()

    def _atr(self, basic, **kw):
        return AuthorityToRecruit(person_name='X', position='Controller', tier=self.t3,
                                  proposed_basic_salary=Decimal(basic), **kw)

    def test_within_band_is_not_an_exception(self):
        self.assertFalse(self._atr('30000').is_salary_exception())

    def test_exactly_at_the_ceiling_is_not_an_exception(self):
        self.assertFalse(self._atr('40000').is_salary_exception())

    def test_above_the_ceiling_is_an_exception(self):
        self.assertTrue(self._atr('40000.01').is_salary_exception())

    def test_below_the_floor_is_not_flagged(self):
        a = self._atr('10000')
        self.assertFalse(a.is_salary_exception())

    def test_no_ceiling_set_means_no_exception(self):
        t1 = PositionTier.objects.get(tier=1)  # band still empty
        a = AuthorityToRecruit(person_name='X', position='Junior', tier=t1,
                               proposed_basic_salary=Decimal('999999'))
        self.assertFalse(a.is_salary_exception())

    def test_exception_signers_are_hcm_and_cfo_for_tiers_1_to_4(self):
        a = self._atr('50000')
        self.assertEqual([s for s, _l, _e in a.exception_signers()], ['human_capital', 'cfo'])

    def test_exception_signers_are_ceo_for_tier_5(self):
        t5 = PositionTier.objects.get(tier=5)
        a = AuthorityToRecruit(person_name='X', position='COO', tier=t5,
                               proposed_basic_salary=Decimal('1'))
        self.assertEqual([s for s, _l, _e in a.exception_signers()], ['ceo'])

    def test_validate_offer_blocks_over_ceiling_without_justification(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self._atr('50000').validate_offer()

    def test_validate_offer_allows_over_ceiling_with_justification(self):
        self._atr('50000', justification='Scarce skill, market rate.').validate_offer()  # no raise


class BandExceptionApiTests(APITestCase):
    def setUp(self):
        self.people = _users()
        self.t3 = PositionTier.objects.get(tier=3)
        self.t3.basic_salary_min = Decimal('20000')
        self.t3.basic_salary_max = Decimal('40000')
        self.t3.save()
        self.url = reverse('v1-recruitment-authorities')
        self.client.force_authenticate(self.people['human_capital'])

    def _payload(self, **kw):
        base = dict(person_name='Jane Doe', position='Controller', tier=3,
                    hiring_manager_name='Line Boss', hiring_manager_email='boss@alphadirect.co.bw',
                    proposed_basic_salary='30000')
        base.update(kw)
        return base

    def test_within_band_creates(self):
        r = self.client.post(self.url, self._payload(), format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertFalse(r.data['is_salary_exception'])

    def test_over_ceiling_without_justification_is_blocked(self):
        r = self.client.post(self.url, self._payload(proposed_basic_salary='55000'), format='json')
        self.assertEqual(r.status_code, 400, r.content)
        self.assertTrue(r.data.get('salary_exception'))

    def test_over_ceiling_with_justification_creates_and_is_flagged(self):
        r = self.client.post(self.url, self._payload(proposed_basic_salary='55000',
                                                     justification='Board-approved scarce skill.'),
                             format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.data['is_salary_exception'])
        self.assertEqual([s['slug'] for s in r.data['exception_signers']], ['human_capital', 'cfo'])

    def test_a_tier_needing_a_hiring_manager_requires_one(self):
        r = self.client.post(self.url, self._payload(hiring_manager_email=''), format='json')
        self.assertEqual(r.status_code, 400, r.content)

    def test_a_tiered_authority_requires_a_basic_salary(self):
        r = self.client.post(self.url, self._payload(proposed_basic_salary='0'), format='json')
        self.assertEqual(r.status_code, 400, r.content)

    def test_hiring_manager_cannot_be_another_signatory_on_the_chain(self):
        # Tier 3 chain has the CFO; making the CFO the hiring manager would
        # deadlock (one email, two slots) — must be refused.
        r = self.client.post(self.url,
                             self._payload(hiring_manager_email='pganesharajah@alphadirect.co.bw'),
                             format='json')
        self.assertEqual(r.status_code, 400, r.content)

    def test_a_hiring_manager_may_view_but_not_raise(self):
        # A hiring manager on an existing authority can open the area (view) but
        # must not raise new authorities.
        AuthorityToRecruit.objects.create(
            person_name='X', position='Y', tier=PositionTier.objects.get(tier=1),
            hiring_manager_email='linemgr@alphadirect.co.bw', hiring_manager_name='M')
        mgr = User.objects.create_user('linemgr', email='linemgr@alphadirect.co.bw')
        self.client.force_authenticate(mgr)
        self.assertEqual(self.client.get(self.url).status_code, 200)          # may view
        r = self.client.post(self.url, self._payload(), format='json')        # may NOT raise
        self.assertEqual(r.status_code, 403, r.content)


class Tier5BoardChairApiTests(APITestCase):
    def setUp(self):
        self.people = _users()
        self.url = reverse('v1-recruitment-authorities')
        self.client.force_authenticate(self.people['human_capital'])

    def _payload(self, **kw):
        base = dict(person_name='A Chief', position='Chief Something', tier=5,
                    proposed_basic_salary='90000')
        base.update(kw)
        return base

    def test_tier5_is_blocked_when_board_chair_not_set_up(self):
        r = self.client.post(self.url, self._payload(), format='json')
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn('Board Chair', str(r.data))

    @override_settings(RECRUIT_BOARD_CHAIR={'name': 'Board Chair', 'email': 'chair@alphadirect.co.bw'})
    def test_tier5_allowed_once_board_chair_has_an_account(self):
        User.objects.create_user('chair', email='chair@alphadirect.co.bw')
        r = self.client.post(self.url, self._payload(), format='json')
        self.assertEqual(r.status_code, 201, r.content)


class MorningBriefTierTests(APITestCase):
    """The morning-brief 'authorities awaiting you' block must resolve each
    person's slot per-authority, so the Finance Manager (a tier signatory, not
    on the legacy five) still gets their row."""
    def test_finance_manager_gets_a_brief_row_for_a_tiered_authority(self):
        import types
        from hris.workforce_brief import outstanding_authorities_brief
        User.objects.create_user('ktshutlhedi', email='ktshutlhedi@alphadirect.co.bw')
        AuthorityToRecruit.objects.create(
            person_name='Junior', position='Associate', tier=PositionTier.objects.get(tier=1),
            hiring_manager_email='linemgr@alphadirect.co.bw', hiring_manager_name='M')
        profile = types.SimpleNamespace(
            employee=types.SimpleNamespace(email='ktshutlhedi@alphadirect.co.bw'))
        rows = outstanding_authorities_brief(profile)
        self.assertEqual([r['person_name'] for r in rows], ['Junior'])


class TierSigningTests(APITestCase):
    """A tier-1 authority is approved only when its OWN five sign — including the
    Finance Manager, and NOT the COO."""
    def setUp(self):
        self.people = _users()
        self.mgr = User.objects.create_user('linemgr', email='linemgr@alphadirect.co.bw')
        self.a = AuthorityToRecruit.objects.create(
            person_name='Junior Hire', position='Associate',
            tier=PositionTier.objects.get(tier=1),
            hiring_manager_name='Line Mgr', hiring_manager_email='linemgr@alphadirect.co.bw')
        self.sign_url = reverse('v1-recruitment-authority-sign', args=[self.a.id])

    def test_finance_manager_is_a_signatory(self):
        self.client.force_authenticate(self.people['finance_manager'])
        r = self.client.post(self.sign_url, {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)

    def test_the_coo_cannot_sign_a_tiered_authority(self):
        self.client.force_authenticate(self.people['coo'])
        r = self.client.post(self.sign_url, {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 403, r.content)

    def test_approved_only_when_all_five_tier_signers_sign(self):
        signers = [self.people['hr_bp'], self.mgr, self.people['human_capital'],
                   self.people['finance_manager'], self.people['cfo']]
        for i, u in enumerate(signers, start=1):
            self.client.force_authenticate(u)
            r = self.client.post(self.sign_url, {'decision': 'approve'}, format='json')
            self.assertEqual(r.status_code, 200, r.content)
            self.a.refresh_from_db()
            expect = AuthorityToRecruit.Status.APPROVED if i == 5 else AuthorityToRecruit.Status.PENDING
            self.assertEqual(self.a.status, expect, f'after {i} signatures')


class TierAccessTests(APITestCase):
    """Full-view roles see everything; a junior-tier signer never sees a C-suite
    package they do not sign."""
    def setUp(self):
        self.people = _users()
        self.t3 = PositionTier.objects.get(tier=3)
        self.t5 = PositionTier.objects.get(tier=5)
        self.tier1 = AuthorityToRecruit.objects.create(
            person_name='Junior', position='Associate',
            tier=PositionTier.objects.get(tier=1),
            hiring_manager_email='linemgr@alphadirect.co.bw', hiring_manager_name='M')
        self.csuite = AuthorityToRecruit.objects.create(
            person_name='A Chief', position='Chief X', tier=self.t5)
        self.list_url = reverse('v1-recruitment-authorities')

    def test_finance_manager_sees_only_authorities_they_sign(self):
        self.client.force_authenticate(self.people['finance_manager'])
        r = self.client.get(self.list_url)
        self.assertEqual(r.status_code, 200, r.content)
        names = {x['person_name'] for x in r.data['authorities']}
        self.assertIn('Junior', names)          # FM is on the tier-1 chain
        self.assertNotIn('A Chief', names)      # FM is NOT on the tier-5 chain

    def test_cfo_sees_everything(self):
        self.client.force_authenticate(self.people['cfo'])
        r = self.client.get(self.list_url)
        names = {x['person_name'] for x in r.data['authorities']}
        self.assertEqual(names, {'Junior', 'A Chief'})

    def test_hiring_manager_sees_their_own_authority(self):
        mgr = User.objects.create_user('linemgr', email='linemgr@alphadirect.co.bw')
        self.client.force_authenticate(mgr)
        r = self.client.get(self.list_url)
        self.assertEqual(r.status_code, 200, r.content)
        names = {x['person_name'] for x in r.data['authorities']}
        self.assertEqual(names, {'Junior'})


@override_settings(RECRUIT_BOARD_CHAIR={'name': 'Board Chair', 'email': 'chair@alphadirect.co.bw'})
class BoardChairTests(APITestCase):
    def test_board_chair_email_comes_from_settings(self):
        a = AuthorityToRecruit.objects.create(
            person_name='A Chief', position='Chief X', tier=PositionTier.objects.get(tier=5))
        chain = dict((s, e) for s, _l, e in a.signatory_chain())
        self.assertEqual(chain['board_chair'], 'chair@alphadirect.co.bw')

    def test_board_chair_can_be_matched_as_signatory(self):
        chair = User.objects.create_user('chair', email='chair@alphadirect.co.bw')
        a = AuthorityToRecruit.objects.create(
            person_name='A Chief', position='Chief X', tier=PositionTier.objects.get(tier=5))
        self.assertEqual(a.signatory_for(chair), 'board_chair')


class TierAdminApiTests(APITestCase):
    def setUp(self):
        self.people = _users()
        self.list_url = reverse('v1-recruitment-position-tiers')

    def test_cfo_can_set_a_band(self):
        self.client.force_authenticate(self.people['cfo'])
        url = reverse('v1-recruitment-position-tier-update', args=[3])
        r = self.client.put(url, {'basic_salary_min': '20000', 'basic_salary_max': '40000'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        PositionTier.objects.get(tier=3).refresh_from_db()
        self.assertEqual(PositionTier.objects.get(tier=3).basic_salary_max, Decimal('40000'))

    def test_min_above_max_is_rejected(self):
        self.client.force_authenticate(self.people['cfo'])
        url = reverse('v1-recruitment-position-tier-update', args=[3])
        r = self.client.put(url, {'basic_salary_min': '50000', 'basic_salary_max': '40000'}, format='json')
        self.assertEqual(r.status_code, 400, r.content)

    def test_hr_business_partner_can_set_a_band(self):
        # Dorothy (HR Business Partner) maintains the bands day to day (CFO 2026-09-02).
        self.client.force_authenticate(self.people['hr_bp'])
        url = reverse('v1-recruitment-position-tier-update', args=[3])
        r = self.client.put(url, {'basic_salary_min': '20000', 'basic_salary_max': '40000'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)

    def test_a_non_manager_cannot_set_a_band(self):
        # The Finance Manager is NOT a bands editor.
        self.client.force_authenticate(self.people['finance_manager'])
        url = reverse('v1-recruitment-position-tier-update', args=[3])
        r = self.client.put(url, {'basic_salary_max': '40000'}, format='json')
        self.assertEqual(r.status_code, 403, r.content)

    def test_cfo_can_tag_a_job_title(self):
        self.client.force_authenticate(self.people['cfo'])
        url = reverse('v1-recruitment-job-title-tiers')
        r = self.client.post(url, {'title': 'Underwriting Manager', 'tier': 4}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(JobTitleTier.objects.get(title__iexact='Underwriting Manager').tier.tier, 4)
