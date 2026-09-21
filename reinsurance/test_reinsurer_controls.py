"""Reinsurer Controls & FAC Risk Register — the gates, proved.

Arun P. Iyer's control brief asks for a chain nobody can shortcut. These tests
exist to make a shortcut fail loudly:

  * every legal move works, and every illegal one is refused;
  * a person with the right role but the wrong name cannot give Paul Beka's
    approval or the CEO's;
  * a superuser cannot either, because identity is checked as well as permission;
  * nobody approves what they themselves submitted;
  * an unapproved, expired or suspended counterparty cannot be put on a
    placement;
  * unplaced capacity is reported as retained, never as ceded.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Permission, Role, UserRoleAssignment
from reinsurance import onboarding
from reinsurance.models import (
    FacReinsurerAllocation,
    FacRiskExposure,
    Reinsurer,
    ReinsurerSecurityAssessment,
)

S = Reinsurer.ApprovalStatus
TODAY = timezone.localdate()


def _role(code: str, *perm_codes: str, level: int = 3) -> Role:
    role, _ = Role.objects.get_or_create(
        code=code, defaults={'name': code.title(), 'level': level})
    for c in perm_codes:
        p, _ = Permission.objects.get_or_create(
            code=c, defaults={'category': c.split('.')[0], 'description': c})
        role.permissions.add(p)
    return role


def _user(username: str, email: str, *perm_codes: str, superuser=False) -> User:
    u = (User.objects.create_superuser(username, email, 'x') if superuser
         else User.objects.create_user(username, email, 'x'))
    if perm_codes:
        UserRoleAssignment.objects.create(
            user=u, role=_role(f'ROLE_{username.upper()}', *perm_codes))
    return u


# The KYC evidence register (control brief section 5, 16-Sep-2026) added a
# SECOND precondition for a placement: approved status is no longer enough on
# its own, the required documents must also be on file, verified and in date.
# The tests below were written under the old rule, so they now say the new one
# out loud with complete_kyc() rather than being weakened or deleted.
from reinsurance.test_kyc_documents import complete_kyc


class OnboardingChainTests(TestCase):
    def setUp(self):
        self.r = Reinsurer.objects.create(
            name='Test Re Limited', short_code='TEST_RE',
            legal_name='Test Re Limited', domicile='ZA',
            onboarding_purposes=['facultative'],
            expiry_date=TODAY + datetime.timedelta(days=365))
        complete_kyc(self.r)
        self.underwriter = _user('uw1', 'uw1@alphadirect.co.bw',
                                 'uw.counterparty.submit')
        self.uw_manager = _user('uwm', 'uwm@alphadirect.co.bw',
                                'uw.counterparty.approve')
        self.compliance = _user('comp', 'comp@alphadirect.co.bw',
                                'compliance.counterparty.approve')
        self.paul = _user('pbeka', onboarding.PRINCIPAL_EMAIL,
                          're.counterparty.approve_principal')
        self.ceo = _user('aiyer', onboarding.CEO_EMAIL,
                         're.counterparty.approve_ceo')

    def _assess(self):
        ReinsurerSecurityAssessment.objects.create(
            reinsurer=self.r, rating='A-', rating_agency='AM Best',
            rating_scale=ReinsurerSecurityAssessment.Scale.INTERNATIONAL,
            rating_date=TODAY, evidence_date=TODAY, verified=True)

    def _walk_to(self, status):
        onboarding.transition(self.underwriter, self.r, S.PENDING_UW_MANAGER)
        self.r.refresh_from_db()
        if status == S.PENDING_UW_MANAGER:
            return
        onboarding.transition(self.uw_manager, self.r, S.PENDING_COMPLIANCE)
        self.r.refresh_from_db()
        if status == S.PENDING_COMPLIANCE:
            return
        self._assess()
        onboarding.transition(self.compliance, self.r, S.PENDING_PRINCIPAL)
        self.r.refresh_from_db()
        if status == S.PENDING_PRINCIPAL:
            return
        onboarding.transition(self.paul, self.r, S.PENDING_CEO)
        self.r.refresh_from_db()
        if status == S.PENDING_CEO:
            return
        onboarding.transition(self.ceo, self.r, S.APPROVED)
        self.r.refresh_from_db()

    def test_the_full_chain_reaches_approved(self):
        self._walk_to(S.APPROVED)
        self.assertEqual(self.r.approval_status, S.APPROVED)
        self.assertIsNotNone(self.r.approved_at)
        self.assertTrue(self.r.may_be_placed())
        self.assertEqual(self.r.approval_transitions.count(), 5)

    def test_a_stage_cannot_be_skipped(self):
        """Straight from draft to approved is the move this whole module exists
        to prevent."""
        with self.assertRaises(PermissionDenied):
            onboarding.transition(self.ceo, self.r, S.APPROVED)
        self.r.refresh_from_db()
        self.assertEqual(self.r.approval_status, S.DRAFT)

    def test_only_paul_beka_can_give_the_principal_approval(self):
        self._walk_to(S.PENDING_PRINCIPAL)
        # Same permission, different person.
        impostor = _user('other', 'other@alphadirect.co.bw',
                         're.counterparty.approve_principal')
        with self.assertRaises(PermissionDenied):
            onboarding.transition(impostor, self.r, S.PENDING_CEO)
        self.r.refresh_from_db()
        self.assertEqual(self.r.approval_status, S.PENDING_PRINCIPAL)

    def test_only_the_ceo_can_give_the_final_approval(self):
        self._walk_to(S.PENDING_CEO)
        impostor = _user('other2', 'other2@alphadirect.co.bw',
                         're.counterparty.approve_ceo')
        with self.assertRaises(PermissionDenied):
            onboarding.transition(impostor, self.r, S.APPROVED)

    def test_paul_beka_cannot_also_give_the_final_ceo_approval(self):
        """CFO decision 19-Sep-2026: the two stages are DIFFERENT people —
        Paul Beka first, Arun Iyer final. Confirms fault 2 of QC 3885a3ec
        ('same-reviewer double approval') is not reachable here: the person
        who gave the principal sign-off cannot also give the CEO's, even
        though he holds `re.counterparty.approve_principal` and could be
        granted `re.counterparty.approve_ceo` too — REQUIRED_IDENTITY checks
        the account's own e-mail against PRINCIPAL_EMAIL/CEO_EMAIL, and the
        two are configured as different people."""
        self._walk_to(S.PENDING_CEO)
        self.assertNotEqual(onboarding.PRINCIPAL_EMAIL, onboarding.CEO_EMAIL)
        # Give Paul the CEO's permission too — a plain RBAC role grant is not
        # enough to let him also close it out.
        UserRoleAssignment.objects.create(
            user=self.paul, role=_role('ROLE_PAUL_ALSO_CEO', 're.counterparty.approve_ceo'))
        with self.assertRaises(PermissionDenied):
            onboarding.transition(self.paul, self.r, S.APPROVED)
        self.r.refresh_from_db()
        self.assertEqual(self.r.approval_status, S.PENDING_CEO)

    def test_a_superuser_cannot_stand_in_for_the_ceo(self):
        """user_has_permission lets a superuser through every permission check
        by design. The identity check is what makes that safe here."""
        self._walk_to(S.PENDING_CEO)
        root = _user('root', 'root@alphadirect.co.bw', superuser=True)
        with self.assertRaises(PermissionDenied):
            onboarding.transition(root, self.r, S.APPROVED)
        self.r.refresh_from_db()
        self.assertEqual(self.r.approval_status, S.PENDING_CEO)

    def test_nobody_approves_what_they_submitted(self):
        """The underwriter who submits it also happens to hold the manager's
        permission — segregation of duties still refuses."""
        submitter = _user('dual', 'dual@alphadirect.co.bw',
                          'uw.counterparty.submit', 'uw.counterparty.approve')
        onboarding.transition(submitter, self.r, S.PENDING_UW_MANAGER)
        self.r.refresh_from_db()
        with self.assertRaises(PermissionDenied):
            onboarding.transition(submitter, self.r, S.PENDING_COMPLIANCE)

    def test_a_return_needs_a_reason(self):
        self._walk_to(S.PENDING_UW_MANAGER)
        returner = _user('ret', 'ret@alphadirect.co.bw', 're.view')
        with self.assertRaises(ValidationError):
            onboarding.transition(returner, self.r, S.RETURNED, comment='')
        onboarding.transition(returner, self.r, S.RETURNED,
                              comment='Licence copy is out of date.')
        self.r.refresh_from_db()
        self.assertEqual(self.r.approval_status, S.RETURNED)

    def test_compliance_stage_needs_a_security_assessment(self):
        """Missing evidence blocks progression — it never defaults to fine."""
        onboarding.transition(self.underwriter, self.r, S.PENDING_UW_MANAGER)
        self.r.refresh_from_db()
        onboarding.transition(self.uw_manager, self.r, S.PENDING_COMPLIANCE)
        self.r.refresh_from_db()
        reason = onboarding.can_transition(self.compliance, self.r,
                                           S.PENDING_PRINCIPAL)
        self.assertIn('security assessment', reason)
        with self.assertRaises(PermissionDenied):
            onboarding.transition(self.compliance, self.r, S.PENDING_PRINCIPAL)

    def test_every_transition_is_recorded_with_who_and_why(self):
        self._walk_to(S.PENDING_UW_MANAGER)
        t = self.r.approval_transitions.first()
        self.assertEqual(t.from_status, S.DRAFT)
        self.assertEqual(t.to_status, S.PENDING_UW_MANAGER)
        self.assertEqual(t.actor_email, 'uw1@alphadirect.co.bw')


class PlacementGateTests(TestCase):
    """Only an approved, in-date counterparty may be put on a placement."""

    def setUp(self):
        self.exposure = FacRiskExposure.objects.create(
            reference='FAC-2026-0001', policy_number='COM2026000001',
            gross_sum_insured=Decimal('60000000.00'),
            net_retention=Decimal('10000000.00'),
            fac_placed_amount=Decimal('40000000.00'),
            unplaced_retained_amount=Decimal('10000000.00'),
            effective_date=TODAY - datetime.timedelta(days=10),
            expiry_date=TODAY + datetime.timedelta(days=300),
            status=FacRiskExposure.Status.PARTIALLY_PLACED)

    def _re(self, **kw):
        base = dict(name='Panel Re', short_code='PANEL_RE',
                    approval_status=S.APPROVED,
                    expiry_date=TODAY + datetime.timedelta(days=100))
        base.update(kw)
        return complete_kyc(Reinsurer.objects.create(**base))

    def test_an_approved_counterparty_may_be_allocated(self):
        r = self._re()
        a = FacReinsurerAllocation(exposure=self.exposure, reinsurer=r,
                                   share_percent=Decimal('25'),
                                   allocated_amount=Decimal('10000000.00'))
        a.full_clean()          # must not raise

    def test_a_counterparty_still_in_onboarding_cannot_be_allocated(self):
        r = self._re(short_code='PENDING_RE', name='Pending Re',
                     approval_status=S.PENDING_CEO)
        a = FacReinsurerAllocation(exposure=self.exposure, reinsurer=r,
                                   allocated_amount=Decimal('1.00'))
        with self.assertRaises(ValidationError):
            a.full_clean()

    def test_a_suspended_counterparty_cannot_be_allocated(self):
        r = self._re(short_code='SUSP_RE', name='Suspended Re',
                     approval_status=S.SUSPENDED)
        with self.assertRaises(ValidationError):
            FacReinsurerAllocation(exposure=self.exposure, reinsurer=r,
                                   allocated_amount=Decimal('1.00')).full_clean()

    def test_an_expired_approval_cannot_be_allocated(self):
        r = self._re(short_code='OLD_RE', name='Old Re',
                     expiry_date=TODAY - datetime.timedelta(days=1))
        self.assertFalse(r.may_be_placed())
        self.assertIn('expired', r.placement_block_reason())
        with self.assertRaises(ValidationError):
            FacReinsurerAllocation(exposure=self.exposure, reinsurer=r,
                                   allocated_amount=Decimal('1.00')).full_clean()

    def test_the_block_says_why(self):
        r = self._re(short_code='WHY_RE', name='Why Re',
                     approval_status=S.DRAFT)
        self.assertIn('not approved for placement', r.placement_block_reason())


class FacExposureTests(TestCase):
    def test_unplaced_capacity_is_retained_and_never_counted_as_ceded(self):
        e = FacRiskExposure.objects.create(
            reference='FAC-2026-0002',
            gross_sum_insured=Decimal('50000000.00'),
            net_retention=Decimal('10000000.00'),
            fac_placed_amount=Decimal('30000000.00'),
            unplaced_retained_amount=Decimal('10000000.00'),
            status=FacRiskExposure.Status.PARTIALLY_PLACED,
            effective_date=TODAY, expiry_date=TODAY + datetime.timedelta(days=90))
        # The retained amount is its own stored figure and is not folded into
        # anything that reads as ceded.
        self.assertEqual(e.unplaced_retained_amount, Decimal('10000000.00'))
        self.assertEqual(e.fac_placed_amount, Decimal('30000000.00'))
        self.assertEqual(e.allocation_variance(), Decimal('30000000.00'))

    def test_active_and_expired_are_date_driven(self):
        past = FacRiskExposure.objects.create(
            reference='FAC-OLD', status=FacRiskExposure.Status.PLACED,
            effective_date=TODAY - datetime.timedelta(days=400),
            expiry_date=TODAY - datetime.timedelta(days=30))
        live = FacRiskExposure.objects.create(
            reference='FAC-LIVE', status=FacRiskExposure.Status.PLACED,
            effective_date=TODAY - datetime.timedelta(days=10),
            expiry_date=TODAY + datetime.timedelta(days=30))
        self.assertTrue(past.is_expired())
        self.assertFalse(past.is_active())
        self.assertTrue(live.is_active())

    def test_a_person_can_override_the_date_and_the_override_stands(self):
        e = FacRiskExposure.objects.create(
            reference='FAC-OVR', status=FacRiskExposure.Status.EXPIRED,
            status_overridden=True,
            status_override_reason='Slip extended by endorsement, not yet loaded.',
            effective_date=TODAY - datetime.timedelta(days=10),
            expiry_date=TODAY + datetime.timedelta(days=300))
        self.assertTrue(e.is_expired())   # the person said expired; dates do not win
        self.assertFalse(e.is_active())

    def test_a_missing_share_stays_unknown_and_does_not_become_zero(self):
        r = complete_kyc(Reinsurer.objects.create(
            name='No Share Re', short_code='NOSHARE',
            approval_status=S.APPROVED,
            expiry_date=TODAY + datetime.timedelta(days=100)))
        e = FacRiskExposure.objects.create(reference='FAC-NS')
        a = FacReinsurerAllocation.objects.create(
            exposure=e, reinsurer=r, share_percent=None,
            allocated_amount=Decimal('5000.00'))
        self.assertIsNone(a.share_percent)


class ExpiryJobTests(TestCase):
    def test_expire_due_moves_only_approved_rows_past_their_date(self):
        stale = Reinsurer.objects.create(
            name='Stale Re', short_code='STALE', approval_status=S.APPROVED,
            expiry_date=TODAY - datetime.timedelta(days=1))
        fine = Reinsurer.objects.create(
            name='Fine Re', short_code='FINE', approval_status=S.APPROVED,
            expiry_date=TODAY + datetime.timedelta(days=1))
        suspended = Reinsurer.objects.create(
            name='Susp Re', short_code='SUSP2', approval_status=S.SUSPENDED,
            expiry_date=TODAY - datetime.timedelta(days=1))

        self.assertEqual(onboarding.expire_due(), 1)
        stale.refresh_from_db(); fine.refresh_from_db(); suspended.refresh_from_db()
        self.assertEqual(stale.approval_status, S.EXPIRED)
        self.assertEqual(fine.approval_status, S.APPROVED)
        self.assertEqual(suspended.approval_status, S.SUSPENDED)

        # Idempotent — running it again moves nothing.
        self.assertEqual(onboarding.expire_due(), 0)


class SecurityScaleTests(TestCase):
    def test_a_national_scale_rating_is_not_internationally_comparable(self):
        r = Reinsurer.objects.create(name='Nat Re', short_code='NAT')
        national = ReinsurerSecurityAssessment.objects.create(
            reinsurer=r, rating='AA', rating_agency='GCR',
            rating_scale=ReinsurerSecurityAssessment.Scale.NATIONAL)
        intl = ReinsurerSecurityAssessment.objects.create(
            reinsurer=r, rating='BBB', rating_agency='AM Best',
            rating_scale=ReinsurerSecurityAssessment.Scale.INTERNATIONAL)
        self.assertFalse(national.is_comparable_internationally)
        self.assertTrue(intl.is_comparable_internationally)

    def test_a_rating_with_no_scale_is_not_treated_as_international(self):
        r = Reinsurer.objects.create(name='Unk Re', short_code='UNK')
        a = ReinsurerSecurityAssessment.objects.create(reinsurer=r, rating='A')
        self.assertEqual(a.rating_scale,
                         ReinsurerSecurityAssessment.Scale.UNKNOWN)
        self.assertFalse(a.is_comparable_internationally)


class EvidenceGapKpiFreshnessTests(TestCase):
    """The control-centre evidence-gap counters must read CURRENT state.

    Reinsurance control QC 3885a3ec: "stale approved counts" — a dashboard
    summary count that does not reflect current status. `rating_not_verified`
    and `national_scale_only` were computed with
    `rs.filter(security_assessments__verified=False)` /
    `...rating_scale=NATIONAL).exclude(...=INTERNATIONAL)` — a plain filter
    across the REVERSE relation, which matches if ANY historical assessment
    row satisfies it. Assessments are kept as history and never overwritten
    (models.py, ReinsurerSecurityAssessment docstring), so a counterparty
    whose first (superseded) assessment was unverified / national-scale kept
    tripping these counters forever, even after Compliance verified a proper
    international-scale rating — exactly the figure `_latest_assessment()`
    (used everywhere else on this same screen) says is now fine.

    RED-PROOF: swap the fixed per-reinsurer loop back for the original
    `rs.filter(security_assessments__verified=False).distinct()` /
    `rs.filter(...NATIONAL...).exclude(...INTERNATIONAL...).distinct()` and
    both tests below fail.
    """
    ROUTE = '/api/v1/reinsurance/controls/'

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(_user('kpi', 'kpi@alphadirect.co.bw', 're.view'))

    def _centre(self):
        r = self.client.get(self.ROUTE)
        self.assertEqual(r.status_code, 200, r.data)
        return r.data['evidence_gaps']

    def test_a_reinsurer_verified_after_an_earlier_unverified_stub_is_not_counted_unverified(self):
        r = Reinsurer.objects.create(name='Was Unverified Re', short_code='WASUNV')
        ReinsurerSecurityAssessment.objects.create(
            reinsurer=r, rating='B', rating_agency='Self-reported',
            rating_date=TODAY - datetime.timedelta(days=400), verified=False)
        ReinsurerSecurityAssessment.objects.create(
            reinsurer=r, rating='A-', rating_agency='AM Best',
            rating_scale=ReinsurerSecurityAssessment.Scale.INTERNATIONAL,
            rating_date=TODAY, evidence_date=TODAY, verified=True)

        gaps = self._centre()
        self.assertEqual(
            gaps['rating_not_verified'], 0,
            'a reinsurer whose CURRENT rating is verified still counted as unverified')

    def test_a_reinsurer_now_international_after_an_earlier_national_rating_is_not_counted_national_only(self):
        r = Reinsurer.objects.create(name='Now International Re', short_code='NOWINTL')
        ReinsurerSecurityAssessment.objects.create(
            reinsurer=r, rating='AA', rating_agency='GCR',
            rating_scale=ReinsurerSecurityAssessment.Scale.NATIONAL,
            rating_date=TODAY - datetime.timedelta(days=400))
        ReinsurerSecurityAssessment.objects.create(
            reinsurer=r, rating='A-', rating_agency='AM Best',
            rating_scale=ReinsurerSecurityAssessment.Scale.INTERNATIONAL,
            rating_date=TODAY, evidence_date=TODAY, verified=True)

        gaps = self._centre()
        self.assertEqual(
            gaps['national_scale_only'], 0,
            'a reinsurer whose CURRENT rating is international still counted as national-only')

    def test_a_reinsurer_still_only_nationally_rated_is_correctly_flagged(self):
        """The tightening must not stop flagging a genuine gap."""
        r = Reinsurer.objects.create(name='Still National Re', short_code='STILLNAT')
        ReinsurerSecurityAssessment.objects.create(
            reinsurer=r, rating='AA', rating_agency='GCR',
            rating_scale=ReinsurerSecurityAssessment.Scale.NATIONAL,
            rating_date=TODAY, verified=True)

        gaps = self._centre()
        self.assertEqual(gaps['national_scale_only'], 1)

    def test_a_reinsurer_still_unverified_is_correctly_flagged(self):
        r = Reinsurer.objects.create(name='Still Unverified Re', short_code='STILLUNV')
        ReinsurerSecurityAssessment.objects.create(
            reinsurer=r, rating='A-', rating_agency='AM Best', rating_date=TODAY,
            verified=False)

        gaps = self._centre()
        self.assertEqual(gaps['rating_not_verified'], 1)


class ChainHoleTests(TestCase):
    """The three holes Fable's probe suite found on 15-Sep-2026, after 21 green
    tests. Every one of these was reachable before the fix."""

    def setUp(self):
        self.r = Reinsurer.objects.create(
            name='Hole Re', short_code='HOLE_RE', legal_name='Hole Re',
            domicile='ZA', onboarding_purposes=['facultative'],
            approval_status=S.APPROVED,
            expiry_date=TODAY + datetime.timedelta(days=365))
        self.nobody = _user('nobody', 'nobody@alphadirect.co.bw')
        self.viewer = _user('viewer', 'viewer@alphadirect.co.bw', 're.view')
        self.client = APIClient()

    def test_a_user_with_no_roles_cannot_expire_a_live_counterparty(self):
        """EXPIRED sat in the transition table with no permission entry, so the
        permission check was skipped and ANY signed-in account could knock a
        reinsurer off the panel."""
        reason = onboarding.can_transition(self.nobody, self.r, S.EXPIRED)
        self.assertTrue(reason, 'expiring was allowed with no permission at all')
        with self.assertRaises((PermissionDenied, ValidationError)):
            onboarding.transition(self.nobody, self.r, S.EXPIRED)
        self.r.refresh_from_db()
        self.assertEqual(self.r.approval_status, S.APPROVED)

    def test_expiring_is_the_systems_job_not_a_button(self):
        self.assertNotIn(S.EXPIRED, onboarding.TRANSITIONS[S.APPROVED])

    def test_a_plain_viewer_cannot_pull_back_an_approved_counterparty(self):
        """Returning something still in the queue is reviewer work. Undoing a
        CEO approval is not."""
        with self.assertRaises(PermissionDenied):
            onboarding.transition(self.viewer, self.r, S.RETURNED,
                                  comment='I disagree with this one.')
        self.r.refresh_from_db()
        self.assertEqual(self.r.approval_status, S.APPROVED)

    def test_a_viewer_may_still_return_something_in_the_queue(self):
        """The tightening must not break ordinary review."""
        pending = Reinsurer.objects.create(
            name='Queued Re', short_code='QUEUE_RE',
            approval_status=S.PENDING_UW_MANAGER)
        onboarding.transition(self.viewer, pending, S.RETURNED,
                              comment='Licence copy is out of date.')
        pending.refresh_from_db()
        self.assertEqual(pending.approval_status, S.RETURNED)

    def test_the_transition_endpoint_refuses_a_user_with_no_reinsurance_access(self):
        self.client.force_authenticate(self.nobody)
        resp = self.client.post(
            f'/api/v1/reinsurance/counterparties/{self.r.id}/transition/',
            {'target': S.SUSPENDED, 'comment': 'x'}, format='json')
        self.assertEqual(resp.status_code, 403)
        self.r.refresh_from_db()
        self.assertEqual(self.r.approval_status, S.APPROVED)

    def test_every_reachable_target_has_a_permission(self):
        """A state in the transition table with no permission entry is an
        unguarded door — that is exactly how EXPIRED got out."""
        for current, targets in onboarding.TRANSITIONS.items():
            for t in targets:
                self.assertIn(
                    t, onboarding.REQUIRED_PERMISSION,
                    f'{current} → {t} is reachable with no permission required')


class IdentityGateTests(TestCase):
    """An e-mail address is not an identity."""

    def setUp(self):
        self.r = Reinsurer.objects.create(
            name='Ident Re', short_code='IDENT_RE',
            approval_status=S.PENDING_CEO,
            expiry_date=TODAY + datetime.timedelta(days=365))

    def test_a_superuser_who_sets_the_ceos_email_is_still_refused(self):
        """user.email is neither unique nor immutable, and a Super Admin can
        write his own through the users API."""
        impostor = _user('impostor', onboarding.CEO_EMAIL, superuser=True)
        real_ceo = _user('realceo', onboarding.CEO_EMAIL,
                         're.counterparty.approve_ceo')
        self.assertEqual(
            User.objects.filter(email__iexact=onboarding.CEO_EMAIL,
                                is_active=True).count(), 2)
        for who in (impostor, real_ceo):
            with self.assertRaises(PermissionDenied):
                onboarding.transition(who, self.r, S.APPROVED)
        self.r.refresh_from_db()
        self.assertEqual(self.r.approval_status, S.PENDING_CEO)

    def test_the_refusal_names_the_duplicate_problem(self):
        _user('dupe1', onboarding.CEO_EMAIL, 're.counterparty.approve_ceo')
        dupe2 = _user('dupe2', onboarding.CEO_EMAIL,
                      're.counterparty.approve_ceo')
        reason = onboarding.can_transition(dupe2, self.r, S.APPROVED)
        self.assertIn('active Omni accounts carry that e-mail', reason)

    def test_a_deactivated_ceo_account_cannot_approve(self):
        ceo = _user('ceo-off', onboarding.CEO_EMAIL,
                    're.counterparty.approve_ceo')
        ceo.is_active = False
        ceo.save(update_fields=['is_active'])
        with self.assertRaises(PermissionDenied):
            onboarding.transition(ceo, self.r, S.APPROVED)

    def test_the_sole_active_ceo_can_still_approve(self):
        """The tightening must not lock the real person out."""
        ceo = _user('ceo-ok', onboarding.CEO_EMAIL,
                    're.counterparty.approve_ceo')
        onboarding.transition(ceo, self.r, S.APPROVED)
        self.r.refresh_from_db()
        self.assertEqual(self.r.approval_status, S.APPROVED)


class AllocationSaveGateTests(TestCase):
    """clean() only runs from a form. save() is what everything else calls."""

    def setUp(self):
        self.exposure = FacRiskExposure.objects.create(reference='FAC-SAVE-1')

    def test_save_refuses_an_unapproved_counterparty(self):
        draft = Reinsurer.objects.create(name='Draft Re', short_code='DRAFT_RE',
                                         approval_status=S.DRAFT)
        with self.assertRaises(ValidationError):
            FacReinsurerAllocation.objects.create(
                exposure=self.exposure, reinsurer=draft,
                allocated_amount=Decimal('1000.00'))
        self.assertEqual(FacReinsurerAllocation.objects.count(), 0)

    def test_save_allows_an_approved_counterparty(self):
        ok = complete_kyc(Reinsurer.objects.create(
            name='Good Re', short_code='GOOD_RE', approval_status=S.APPROVED,
            expiry_date=TODAY + datetime.timedelta(days=90)))
        FacReinsurerAllocation.objects.create(
            exposure=self.exposure, reinsurer=ok,
            allocated_amount=Decimal('1000.00'))
        self.assertEqual(FacReinsurerAllocation.objects.count(), 1)


class EndpointAccessTests(TestCase):
    """Every controls endpoint needs re.view — including the one that acts."""

    ROUTES = (
        '/api/v1/reinsurance/controls/',
        '/api/v1/reinsurance/counterparties/',
        '/api/v1/reinsurance/fac-risk/',
        '/api/v1/reinsurance/security-panel/',
    )

    def test_a_user_with_no_reinsurance_role_is_refused_everywhere(self):
        client = APIClient()
        client.force_authenticate(_user('noaccess', 'noaccess@alphadirect.co.bw'))
        for route in self.ROUTES:
            self.assertEqual(client.get(route).status_code, 403, route)

    def test_a_viewer_can_read(self):
        client = APIClient()
        client.force_authenticate(_user('canread', 'canread@alphadirect.co.bw',
                                        're.view'))
        for route in self.ROUTES:
            self.assertEqual(client.get(route).status_code, 200, route)


class FacRiskTruncationTests(TestCase):
    """A capped list must never report itself complete.

    The register reads at most 1,000 risks, then filters active/expired in
    Python. If the truncation flag is worked out AFTER that filter, it is false
    the moment the filter drops one row — so the screen shows a 1,000-row cut
    of the book with no warning, and every exposure total under it understates.

    This test fails without the fix: it asks for `state=active` on a book whose
    first 1,000 rows are nearly all expired.
    """

    def test_the_cap_is_reported_even_when_the_state_filter_hides_it(self):
        old_effective = TODAY - datetime.timedelta(days=400)
        old_expiry = TODAY - datetime.timedelta(days=30)
        FacRiskExposure.objects.bulk_create([
            FacRiskExposure(
                reference=f'FAC-BULK-{i:05d}',
                status=FacRiskExposure.Status.PLACED,
                effective_date=old_effective, expiry_date=old_expiry)
            for i in range(1000)
        ])
        FacRiskExposure.objects.create(
            reference='FAC-BULK-LIVE', status=FacRiskExposure.Status.PLACED,
            effective_date=TODAY - datetime.timedelta(days=10),
            expiry_date=TODAY + datetime.timedelta(days=30))

        client = APIClient()
        client.force_authenticate(_user('trunc', 'trunc@alphadirect.co.bw',
                                        're.view'))
        res = client.get('/api/v1/reinsurance/fac-risk/?state=active')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(
            res.data['truncated'],
            'the read was capped at 1,000 rows and the response said it was not')
        self.assertIsNotNone(res.data['truncated_note'])
