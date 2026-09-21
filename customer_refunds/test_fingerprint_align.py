"""Omni and Graphite must fingerprint a bank account IDENTICALLY.

Both systems store a keyed HMAC blind index of the customer's account number so
that one account paid out under two different customer names is caught across
both. That only works if both compute it the same way. On 2026-09-11 Pramod
Bisen showed they did not, and that neither failure raises anything — the check
just returns nothing and looks healthy.

The canonicalisation and re-key tests fail if their fix is reverted. A few
here are deliberately GUARDS instead (they must stay green either way) and
are marked as such, so nobody counts them as proof of a fix.
"""
from decimal import Decimal
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from .models import CustomerRefund, account_fingerprint


class SharedKeyIsReadableFromSettingsTests(TestCase):
    """The key has to be DECLARED in settings, not just present in the env.

    account_fingerprint() reads settings.REFUND_ACCOUNT_INDEX_KEY. Django only
    exposes what settings.py actually reads, so before this change an operator
    could set the environment variable on the server, see no error, and still be
    hashing with SECRET_KEY — keyed differently from Graphite, silently.
    """

    def test_setting_is_declared(self):
        self.assertTrue(
            hasattr(settings, 'REFUND_ACCOUNT_INDEX_KEY'),
            'REFUND_ACCOUNT_INDEX_KEY is not declared in settings.py, so the '
            'environment variable never reaches account_fingerprint().')

    def test_key_actually_changes_the_fingerprint(self):
        with override_settings(REFUND_ACCOUNT_INDEX_KEY='shared-with-graphite'):
            keyed = account_fingerprint('99900011122')
        with override_settings(REFUND_ACCOUNT_INDEX_KEY=''):
            fallback = account_fingerprint('99900011122')
        self.assertNotEqual(keyed, fallback)


@override_settings(REFUND_ACCOUNT_INDEX_KEY='shared-with-graphite')
class LeadingZeroCanonicalisationTests(TestCase):
    """Graphite strips leading zeros before hashing. Omni must too.

    0621... and 621... are one account at the bank. Graphite was changed to
    strip them precisely because they were fingerprinting as two.
    """

    def test_leading_zero_matches_without_it(self):
        self.assertEqual(account_fingerprint('0621234567'),
                         account_fingerprint('621234567'))

    def test_several_leading_zeros_match(self):
        self.assertEqual(account_fingerprint('00621234567'),
                         account_fingerprint('621234567'))

    def test_still_distinguishes_different_accounts(self):
        self.assertNotEqual(account_fingerprint('0621234567'),
                            account_fingerprint('0621234568'))

    def test_interior_zeros_are_kept(self):
        """Only LEADING zeros are formatting. An interior zero is the account."""
        self.assertNotEqual(account_fingerprint('620001'),
                            account_fingerprint('6201'))

    def test_all_zeros_is_treated_as_no_account(self):
        self.assertEqual(account_fingerprint('0000'), '')

    def test_spaces_still_ignored(self):
        self.assertEqual(account_fingerprint('0621 234 567'),
                         account_fingerprint('621234567'))


@override_settings(REFUND_ACCOUNT_INDEX_KEY='shared-with-graphite')
class RekeyCommandTests(TestCase):
    """Existing rows are keyed the OLD way until they are re-keyed.

    Without this the same-account check stops matching across the changeover
    line, with nothing in any log to say so.
    """

    def setUp(self):
        # Two refunds on ONE account, written the two different ways the bank
        # accepts. They are the same account and must end up fingerprinted alike.
        self.a = CustomerRefund.objects.create(
            segment='mis', graphite_ref='fp1', policy_number='MIS-FP-1',
            refund_amount=Decimal('100'))
        self.a.set_account_number('0621234567')
        self.a.save()
        self.b = CustomerRefund.objects.create(
            segment='mis', graphite_ref='fp2', policy_number='MIS-FP-2',
            refund_amount=Decimal('200'))
        self.b.set_account_number('621234567')
        self.b.save()
        # Simulate history written under the old key.
        CustomerRefund.objects.filter(pk__in=[self.a.pk, self.b.pk]).update(
            account_fingerprint='stale-old-key-value')

    def _run(self, *argv):
        out = StringIO()
        call_command('rekey_account_fingerprints', *argv, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        out = self._run()
        self.assertIn('DRY RUN', out)
        self.a.refresh_from_db()
        self.assertEqual(self.a.account_fingerprint, 'stale-old-key-value')

    def test_commit_rekeys_and_the_two_now_match(self):
        self._run('--commit')
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual(self.a.account_fingerprint,
                         account_fingerprint('621234567'))
        self.assertEqual(self.a.account_fingerprint, self.b.account_fingerprint)

    def test_safe_to_re_run(self):
        self._run('--commit')
        out = self._run('--commit')
        self.assertIn('Nothing to change.', out)

    def test_reports_account_sharing_groups(self):
        out = self._run()
        self.assertIn('shared-account groups', out)

    def test_row_with_no_account_number_is_left_alone(self):
        blank = CustomerRefund.objects.create(
            segment='mis', graphite_ref='fp3', policy_number='MIS-FP-3',
            refund_amount=Decimal('5'))
        self._run('--commit')
        blank.refresh_from_db()
        self.assertEqual(blank.account_fingerprint, '')


@override_settings(REFUND_ACCOUNT_INDEX_KEY='shared-with-graphite')
class UndecryptableRowTests(TestCase):
    """A row we cannot read must keep its old fingerprint, never be blanked.

    core.vault_crypto.decrypt() RETURNS '' on a bad or retired key, it does not
    raise. So an `except` around it never fires, and the obvious
    `account_fingerprint(raw) if raw else ''` writes '' — dropping the refund out
    of the fraud check entirely, which is the exact failure the command exists to
    prevent. Found by Fable, 2026-09-12.
    """

    def setUp(self):
        self.r = CustomerRefund.objects.create(
            segment='mis', graphite_ref='fpX', policy_number='MIS-FP-X',
            refund_amount=Decimal('42'))
        # Ciphertext we cannot decrypt, plus a fingerprint from the old key.
        CustomerRefund.objects.filter(pk=self.r.pk).update(
            account_number_enc='not-a-fernet-token',
            account_fingerprint='stale-old-key-value')

    def test_fingerprint_is_left_alone_not_blanked(self):
        err = StringIO()
        call_command('rekey_account_fingerprints', '--commit',
                     stdout=StringIO(), stderr=err)
        self.r.refresh_from_db()
        self.assertEqual(self.r.account_fingerprint, 'stale-old-key-value')
        self.assertIn('cannot decrypt', err.getvalue())

    def test_it_is_counted_and_reported(self):
        out = StringIO()
        call_command('rekey_account_fingerprints', stdout=out, stderr=StringIO())
        self.assertIn('could not decrypt      : 1', out.getvalue())


class WrongKeyRefusalTests(TestCase):
    """Never re-key 37 rows under SECRET_KEY while believing they match Graphite.

    Declaring the setting fixed "the environment variable never reached the
    code". It did NOT remove the silent fallback underneath it.
    """

    @override_settings(REFUND_ACCOUNT_INDEX_KEY='')
    def test_commit_refused_under_the_fallback(self):
        with self.assertRaises(CommandError):
            call_command('rekey_account_fingerprints', '--commit',
                         stdout=StringIO(), stderr=StringIO())

    @override_settings(REFUND_ACCOUNT_INDEX_KEY='')
    def test_dry_run_still_allowed_and_says_which_key(self):
        out = StringIO()
        call_command('rekey_account_fingerprints', stdout=out, stderr=StringIO())
        self.assertIn('FALLBACK to SECRET_KEY', out.getvalue())

    @override_settings(REFUND_ACCOUNT_INDEX_KEY='')
    def test_explicit_override_is_honoured(self):
        call_command('rekey_account_fingerprints', '--commit',
                     '--allow-secret-key-fallback',
                     stdout=StringIO(), stderr=StringIO())

    @override_settings(REFUND_ACCOUNT_INDEX_KEY='shared-with-graphite')
    def test_shared_key_needs_no_override(self):
        out = StringIO()
        call_command('rekey_account_fingerprints', '--commit',
                     stdout=out, stderr=StringIO())
        self.assertIn('shared with Graphite', out.getvalue())


@override_settings(REFUND_ACCOUNT_INDEX_KEY='shared-with-graphite')
class MatchingGotWorseWarningTests(TestCase):
    """The warning must count refunds inside shared groups, not group COUNT.

    Group count goes DOWN on the intended outcome (two formats of one account
    merging) and stays LEVEL on the damage case (one row stranded out of a group
    of four). Only membership tracks what the fraud check actually reads.
    """

    def test_merging_two_formats_is_not_reported_as_worse(self):
        for i, acct in enumerate(('0621234567', '621234567')):
            r = CustomerRefund.objects.create(
                segment='mis', graphite_ref=f'fpM{i}',
                policy_number=f'MIS-M{i}', refund_amount=Decimal('10'))
            r.set_account_number(acct)
            r.save()
        out = StringIO()
        call_command('rekey_account_fingerprints', stdout=out, stderr=StringIO())
        self.assertNotIn('got WORSE', out.getvalue())

    def test_stranding_a_row_out_of_a_group_is_reported(self):
        for i in range(3):
            r = CustomerRefund.objects.create(
                segment='mis', graphite_ref=f'fpS{i}',
                policy_number=f'MIS-S{i}', refund_amount=Decimal('10'))
            r.set_account_number('621234567')
            r.save()
        # One of the three becomes unreadable, so it cannot be re-keyed and
        # drops out of the group of three. Count stays at 1, membership 3 -> 2.
        stranded = CustomerRefund.objects.filter(graphite_ref='fpS0')
        stranded.update(account_number_enc='not-a-fernet-token')
        CustomerRefund.objects.all().update(account_fingerprint='stale')
        out = StringIO()
        call_command('rekey_account_fingerprints', stdout=out, stderr=StringIO())
        self.assertIn('got WORSE', out.getvalue())


class GraphiteKnownAnswerVectorTests(TestCase):
    """THE proof that Omni and Graphite agree. Everything else is self-consistency.

    Until this landed, both sides were only internally consistent: each could be
    wrong in the same place and every test would still pass. Pramod Bisen
    supplied this vector on 2026-09-12, run through Graphite's PHP and an
    equivalent Python implementation, agreeing byte for byte.

    The key is deliberately NOT the production value, so it can live in the repo.

    If this test ever fails, the two systems have drifted apart and the
    same-account fraud check is blind. It does not fail loudly in production --
    the lookup just returns nothing -- which is exactly why it is pinned here.
    """

    KEY = 'ADIC-REFUND-TESTVECTOR-2026'
    EXPECTED = '3f719fbd353092334bfc0eaaeb1f2c8a4f866d7d465224d8b30b5b01dfcdc9ba'

    def _fp(self, raw):
        with override_settings(REFUND_ACCOUNT_INDEX_KEY=self.KEY):
            return account_fingerprint(raw)

    def test_plain_account_matches_graphite(self):
        self.assertEqual(self._fp('62403392335'), self.EXPECTED)

    def test_one_leading_zero_matches_graphite(self):
        self.assertEqual(self._fp('062403392335'), self.EXPECTED)

    def test_two_leading_zeros_match_graphite(self):
        self.assertEqual(self._fp('0062403392335'), self.EXPECTED)

    def test_spaces_match_graphite(self):
        self.assertEqual(self._fp('6240 3392 335'), self.EXPECTED)

    def test_dashes_and_leading_zero_match_graphite(self):
        """Proves the ORDER of operations, not just the result.

        Strip non-digits FIRST, then strip leading zeros. Reversed, '0624-0339-2335'
        gives a different answer, because lstrip('0') on the raw string stops at
        the '6' and leaves the dashes to be removed afterwards -- so this single
        case is what pins the order.
        """
        self.assertEqual(self._fp('0624-0339-2335'), self.EXPECTED)

    def test_no_usable_digits_returns_empty_not_a_hash(self):
        """Not the HMAC of an empty input.

        Hashing empty would give every unusable account number ONE shared
        fingerprint, so they would all 'match' each other in the fraud check.
        """
        self.assertEqual(self._fp('000'), '')

    def test_hex_is_lower_case(self):
        fp = self._fp('62403392335')
        self.assertEqual(fp, fp.lower())
