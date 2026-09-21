"""B4 — the ADH health claims EFT settlement loader, reading a MAILBOX.

THE FOUR-EMAIL TEST. Four near-identical emails land every Saturday and exactly
one is ours. The subjects below are the REAL ones from the 12 September 2026
run. Each condition is tested on its OWN — an email where only that one
condition is wrong and everything else is right — because a filter that passes
only when all the faults happen together is a filter that has never been
checked. The whole-mailbox test then proves WHICH email was picked and WHY each
of the other three was not.

Note the real shape: the subject names a `.xls` and the attachment is a `.zip`.
Nothing in the selector may take the subject's extension for the attachment's.

NO REAL CLAIMANT DATA. Every name, claim number and account in this file is
invented. The real listing is medical and identifiable and none of it belongs
in a repository.
"""
from __future__ import annotations

import io
import urllib.parse
import zipfile
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone

from healthcare import afa_mailbox, claims_settlement as cs
from healthcare.management.commands.load_adh_settlements import Command as LoadCommand
from healthcare.models import AdhSettlementLine, AdhSettlementRun

# ── The four REAL subjects from the 12 September 2026 run ───────────────────
SUBJ_OURS = 'EFT FILE: ADI_AFT_20260912.xls *** MANUAL SUBMISSION REQUIRED ***'
SUBJ_RSA = 'EFT FILE: ADI_AFT_20260912_RSA.xls *** MANUAL SUBMISSION REQUIRED ***'
SUBJ_SUMMARY = 'ADI EFT Payments - Payment Run Summary'
SUBJ_MESSAGES = ('Payment run messages for ALPHA DIRECT INSURANCE on '
                 '12 September 2026')

GOOD_NAME = 'ADI_AFT_20260912.zip'
RSA_NAME = 'ADI_AFT_20260912_RSA.zip'


def dropped(**over) -> cs.DroppedFile:
    base = dict(name=GOOD_NAME, sender=cs.AFA_SENDER, subject=SUBJ_OURS,
                message_id='msg-1', attachment_id='att-1')
    base.update(over)
    return cs.DroppedFile(**base)


def make_listing(rows) -> bytes:
    """A zipped one-sheet listing, written with openpyxl (xlsx), which
    python-calamine reads through the same path as AFA's .xls."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Claim Number', 'Payee Name', 'Amount', 'Account Number',
               'Bank', 'Branch Code', 'Reference'])
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, 'w') as z:
        z.writestr('ADI_AFT_20260912.xlsx', buf.getvalue())
    return zbuf.getvalue()


#: Invented claimants and providers. Nothing here is a real person or account.
SAMPLE_ROWS = [
    ('ADH2026001', 'Maru Wellness Clinic', '1250.00', '62000000001',
     'First National Bank', '282267', 'ADH2026001'),
    ('ADH2026002', 'Kalahari Dental Rooms', '480.50', '62000000002',
     'First National Bank', '282267', 'ADH2026002'),
]


class FourEmailFilterTests(TestCase):
    """One condition wrong at a time. Three of the four must be refused."""

    def test_every_condition_met_is_accepted(self):
        self.assertEqual(cs.rejection_reason(dropped()), '')

    def test_the_subject_naming_xls_does_not_stop_a_zip_attachment(self):
        """The real shape: subject says .xls, attachment is .zip. Accepted."""
        f = dropped()
        self.assertIn('.xls', f.subject)
        self.assertTrue(f.name.endswith('.zip'))
        self.assertEqual(cs.rejection_reason(f), '')

    def test_no_attachment_only(self):
        f = dropped(name='', attachment_id='', has_attachment=False,
                    subject=SUBJ_MESSAGES)
        self.assertIn('no attachment', cs.rejection_reason(f))

    def test_an_attachment_we_cannot_open_only(self):
        """Everything else right; the attachment is not a listing."""
        f = dropped(name='signature.png')
        self.assertIn('not a zip or a spreadsheet', cs.rejection_reason(f))

    def test_wrong_sender_only(self):
        f = dropped(sender='accounts@afa.co.bw')
        reason = cs.rejection_reason(f)
        self.assertIn('sender', reason)
        self.assertIn(cs.AFA_SENDER, reason)

    def test_missing_eft_file_token_only(self):
        f = dropped(subject=SUBJ_SUMMARY, name='ADI_SUMMARY_20260912.zip')
        self.assertIn(cs.TOKEN_EFT_FILE, cs.rejection_reason(f))

    def test_missing_manual_submission_token_only(self):
        f = dropped(subject='EFT FILE: ADI_AFT_20260912.xls *** AUTOMATIC ***')
        self.assertIn(cs.TOKEN_MANUAL, cs.rejection_reason(f))

    def test_rsa_token_in_the_subject_only(self):
        f = dropped(subject=SUBJ_RSA)
        self.assertIn(cs.TOKEN_RSA, cs.rejection_reason(f))

    def test_rsa_in_the_attachment_name_alone_is_still_refused(self):
        """The subject was truncated but the file is still the RSA twin."""
        f = dropped(name=RSA_NAME)
        self.assertIn(cs.TOKEN_RSA, cs.rejection_reason(f))

    def test_the_real_saturday_mailbox_picks_exactly_one(self):
        """The four REAL emails. Prove which one won and why each other lost."""
        files = [
            dropped(),                                              # ours
            dropped(subject=SUBJ_RSA, name=RSA_NAME, message_id='msg-2'),
            dropped(subject=SUBJ_SUMMARY, name='ADI_SUMMARY_20260912.zip',
                    message_id='msg-3'),
            dropped(subject=SUBJ_MESSAGES, name='', attachment_id='',
                    has_attachment=False, message_id='msg-4'),
        ]
        chosen, rejected = cs.select_settlement_file(files)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.name, GOOD_NAME)
        self.assertEqual(len(rejected), 3)
        by_name = {r['name']: r['reason'] for r in rejected}
        self.assertIn(cs.TOKEN_RSA, by_name[RSA_NAME])
        self.assertIn(cs.TOKEN_EFT_FILE, by_name['ADI_SUMMARY_20260912.zip'])
        # The one with no attachment is NAMED by its subject, not dropped
        # silently and not identified by a message id nobody can read.
        no_att = [k for k in by_name if k.startswith('(no attachment)')]
        self.assertEqual(len(no_att), 1)
        self.assertIn('no attachment', by_name[no_att[0]])

    def test_two_matching_emails_are_refused_never_guessed(self):
        """The same file forwarded back in is a second match, not a free pick."""
        files = [dropped(), dropped(message_id='msg-fw', subject='FW: ' + SUBJ_OURS)]
        with self.assertRaises(cs.AmbiguousSettlementFile):
            cs.select_settlement_file(files)


class MailboxReaderTests(TestCase):
    """The mailbox leg: what it asks Graph for, and how it fails."""

    def _mailbox(self, messages):
        """Stand in for Graph: a message listing plus per-message attachments."""
        def fake_get(path, _token):
            if '/attachments?' in path:
                mid = path.split('/messages/')[1].split('/')[0]
                return {'value': next(m['_atts'] for m in messages
                                      if m['id'] == mid)}
            return {'value': [{k: v for k, v in m.items() if k != '_atts'}
                              for m in messages]}
        return fake_get

    @override_settings(ADH_SETTLEMENT_MAILBOX='health@alphadirect.co.bw')
    def test_it_reads_the_named_mailbox_and_keeps_the_no_attachment_email(self):
        messages = [
            {'id': 'msg-1', 'subject': SUBJ_OURS, 'hasAttachments': True,
             'from': {'emailAddress': {'address': cs.AFA_SENDER}},
             'receivedDateTime': '2026-09-11T23:20:00Z',
             '_atts': [{'id': 'att-1', 'name': GOOD_NAME, 'size': 4195}]},
            {'id': 'msg-4', 'subject': SUBJ_MESSAGES, 'hasAttachments': False,
             'from': {'emailAddress': {'address': cs.AFA_SENDER}},
             'receivedDateTime': '2026-09-11T23:21:00Z', '_atts': []},
        ]
        seen = {}

        def fake_get(path, token):
            seen.setdefault('first', path)
            return self._mailbox(messages)(path, token)

        with mock.patch('bonu.mailbox.graph_token', return_value='tok'), \
                mock.patch('bonu.mailbox.graph_get', side_effect=fake_get):
            candidates = cs.fetch_candidates()

        self.assertIn('health%40alphadirect.co.bw', seen['first'])
        # 🔴 No RAW SPACE may reach the request target. `$filter=receivedDateTime
        # ge ...` and `$orderby=... desc` both contain spaces and this string is
        # handed straight to urllib — unencoded, it is not a valid URL, Graph
        # answers 400, and that arrives worded exactly like the missing
        # permission. (Fable round 2, 14-Sep-2026.)
        self.assertNotIn(' ', seen['first'])
        self.assertIn('receivedDateTime%20ge%20', seen['first'])
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].name, GOOD_NAME)
        self.assertFalse(candidates[1].has_attachment)

    @override_settings(ADH_SETTLEMENT_MAILBOX='health@alphadirect.co.bw')
    def test_an_inline_signature_image_is_not_a_candidate(self):
        """Otherwise a logo on the RIGHT email makes the run ambiguous."""
        messages = [
            {'id': 'msg-1', 'subject': SUBJ_OURS, 'hasAttachments': True,
             'from': {'emailAddress': {'address': cs.AFA_SENDER}},
             'receivedDateTime': '2026-09-11T23:20:00Z',
             '_atts': [{'id': 'att-1', 'name': GOOD_NAME, 'size': 4195},
                       {'id': 'att-2', 'name': 'logo.png', 'size': 900,
                        'isInline': True}]},
        ]
        with mock.patch('bonu.mailbox.graph_token', return_value='tok'), \
                mock.patch('bonu.mailbox.graph_get',
                           side_effect=self._mailbox(messages)):
            candidates = cs.fetch_candidates()
        self.assertEqual([c.name for c in candidates], [GOOD_NAME])

    @override_settings(ADH_SETTLEMENT_MAILBOX='health@alphadirect.co.bw')
    def test_no_permission_says_so_loudly_and_names_mail_read(self):
        """🔴 The whole point. Not "no file this week" — "we may not read it"."""
        from bonu.mailbox import MailboxUnavailable

        with mock.patch('bonu.mailbox.graph_token', return_value='tok'), \
                mock.patch('bonu.mailbox.graph_get',
                           side_effect=MailboxUnavailable(
                               'We can send email but we are not allowed to READ '
                               'this mailbox yet.')):
            with self.assertRaises(afa_mailbox.MailboxNotReadable) as ctx:
                cs.fetch_candidates()
        message = str(ctx.exception)
        self.assertIn('Mail.Read', message)
        self.assertIn('health@alphadirect.co.bw', message)
        self.assertIn('application access policy', message)

    @override_settings(ADH_SETTLEMENT_MAILBOX='')
    def test_an_unset_mailbox_is_refused_not_guessed(self):
        with self.assertRaises(afa_mailbox.MailboxNotReadable):
            cs.fetch_candidates()

    @override_settings(ADH_SETTLEMENT_MAILBOX='health@alphadirect.co.bw')
    def test_an_empty_attachment_is_a_FILE_problem_not_a_permission_problem(self):
        """🔴 The remedy has to match the fault.

        Raised as MailboxNotReadable, an empty attachment reaches Keetile under
        the subject "Omni cannot read the mailbox" carrying the Mail.Read
        sentence — sending her to IT for a grant that would not help — and with
        no `rejected` list, because that class cannot carry one, so the screen
        says no other emails were recorded.
        """
        with mock.patch.object(cs, 'fetch_candidates', return_value=[
                dropped(),
                dropped(subject=SUBJ_RSA, name=RSA_NAME, message_id='msg-2')]):
            with mock.patch('healthcare.afa_mailbox.read_attachment',
                            return_value=b''):
                with self.assertRaises(cs.SettlementFileError) as caught:
                    cs.fetch_file()
        self.assertNotIsInstance(caught.exception, afa_mailbox.MailboxNotReadable)
        self.assertNotIn('Mail.Read', str(caught.exception))
        self.assertEqual([r['name'] for r in caught.exception.rejected],
                         [RSA_NAME])


class LookbackWindowMustNotReachLastWeekTests(TestCase):
    """🔴 A window LONGER than the feed's own period makes every run ambiguous.

    The job fires 00:00 Saturday UTC; AFA send ~23:20 the Friday night before.
    Nothing in the filter is date-aware — last week's subject passes all four
    conditions exactly as well as this week's — so a window that reaches back
    far enough to see the previous Friday hands the selector TWO matches, and
    it refuses to guess between two settlement listings. Loud, and permanently
    zero: the settlement would never load, any week, even after IT grant
    Mail.Read. (Fable, 14-Sep-2026.)
    """

    def _mailbox_honouring_the_filter(self, messages):
        """Graph applies `receivedDateTime ge`; so does this fake. A fake that
        ignores the filter cannot fail when the filter is wrong."""
        from datetime import datetime, timezone as dt_tz

        def fake_get(path, _token):
            if '/attachments?' in path:
                mid = path.split('/messages/')[1].split('/')[0]
                return {'value': next(m['_atts'] for m in messages
                                      if m['id'] == mid)}
            query = urllib.parse.parse_qs(path.split('?', 1)[1])
            raw = query['$filter'][0].split(' ge ')[1]
            cutoff = datetime.strptime(raw, '%Y-%m-%dT%H:%M:%SZ').replace(
                tzinfo=dt_tz.utc)
            return {'value': [{k: v for k, v in m.items() if k != '_atts'}
                              for m in messages
                              if datetime.strptime(m['receivedDateTime'],
                                                   '%Y-%m-%dT%H:%M:%SZ').replace(
                                  tzinfo=dt_tz.utc) >= cutoff]}
        return fake_get

    def _two_weeks_of_settlement_emails(self):
        """This Friday's settlement email and LAST Friday's. Both are ours."""
        from datetime import timedelta, timezone as dt_tz
        now = timezone.now()

        def stamp(dt):
            return dt.astimezone(dt_tz.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        return [
            {'id': 'msg-1', 'subject': SUBJ_OURS, 'hasAttachments': True,
             'from': {'emailAddress': {'address': cs.AFA_SENDER}},
             'receivedDateTime': stamp(now - timedelta(minutes=40)),
             '_atts': [{'id': 'att-1', 'name': GOOD_NAME, 'size': 4195}]},
            {'id': 'msg-0', 'subject': 'EFT FILE: ADI_AFT_20260905.xls '
                                       '*** MANUAL SUBMISSION REQUIRED ***',
             'hasAttachments': True,
             'from': {'emailAddress': {'address': cs.AFA_SENDER}},
             'receivedDateTime': stamp(now - timedelta(days=7, minutes=40)),
             '_atts': [{'id': 'att-0', 'name': 'ADI_AFT_20260905.zip',
                        'size': 4195}]},
        ]

    @override_settings(ADH_SETTLEMENT_MAILBOX='health@alphadirect.co.bw')
    def test_the_default_window_sees_only_this_weeks_email(self):
        messages = self._two_weeks_of_settlement_emails()
        with mock.patch('bonu.mailbox.graph_token', return_value='tok'), \
                mock.patch('bonu.mailbox.graph_get',
                           side_effect=self._mailbox_honouring_the_filter(messages)):
            candidates = cs.fetch_candidates()
            chosen, _rejected = cs.select_settlement_file(candidates)
        self.assertEqual([c.name for c in candidates], [GOOD_NAME])
        self.assertEqual(chosen.name, GOOD_NAME)

    @override_settings(ADH_SETTLEMENT_MAILBOX='health@alphadirect.co.bw',
                       ADH_SETTLEMENT_LOOKBACK_DAYS=8)
    def test_a_week_or_longer_window_makes_every_run_ambiguous(self):
        """Why the default is what it is: at 8 days nothing EVER loads."""
        messages = self._two_weeks_of_settlement_emails()
        with mock.patch('bonu.mailbox.graph_token', return_value='tok'), \
                mock.patch('bonu.mailbox.graph_get',
                           side_effect=self._mailbox_honouring_the_filter(messages)):
            candidates = cs.fetch_candidates()
        self.assertEqual(len(candidates), 2)
        with self.assertRaises(cs.AmbiguousSettlementFile):
            cs.select_settlement_file(candidates)

    def test_the_window_is_shorter_than_the_weekly_period(self):
        """The arithmetic, pinned. A 7-day window clears last week's email by
        forty minutes; anything at or above that is a coin toss on timing."""
        self.assertLess(afa_mailbox.lookback_days(), 7)

    def test_the_codes_own_fallback_agrees_with_the_setting(self):
        """🔴 The setting and the code's fallback must not disagree.

        `lookback_days()` falls back to DEFAULT_LOOKBACK_DAYS when the setting
        is absent — a slim test settings module, a future split. A fallback left
        at the old 8 would reinstate the every-week ambiguity by the back door,
        and the test above would never see it, because it reads the SETTING.
        """
        self.assertLess(afa_mailbox.DEFAULT_LOOKBACK_DAYS, 7)
        with override_settings():
            del settings.ADH_SETTLEMENT_LOOKBACK_DAYS
            self.assertEqual(afa_mailbox.lookback_days(),
                             afa_mailbox.DEFAULT_LOOKBACK_DAYS)

    def test_a_catch_up_on_the_monday_still_sees_fridays_email(self):
        """Two days was too short: from Sunday night the file was invisible and
        the run would have told Keetile AFA sent nothing when they had."""
        from datetime import timedelta
        self.assertGreater(
            timezone.now() - timedelta(days=afa_mailbox.lookback_days()),
            timezone.now() - timedelta(days=7))
        # Friday 23:20, read on the Monday morning after: still in the window.
        self.assertGreater(afa_mailbox.lookback_days(), 3)


class LoaderFailsLoudlyTests(TestCase):
    """A Saturday that cannot read the mailbox leaves a FAILED run + an email."""

    @override_settings(ADH_SETTLEMENT_MAILBOX='health@alphadirect.co.bw')
    def test_a_missing_permission_records_a_failed_run_and_emails_keetile(self):
        from django.core.management import call_command
        from bonu.mailbox import MailboxUnavailable

        sent = {}

        def fake_send(subject, html, to, **kw):
            sent['subject'] = subject
            sent['html'] = html
            sent['to'] = to

        # bonu's REAL 403 wording, verbatim from bonu/mailbox.py — the shared
        # Graph client is what Keetile's Saturday actually hits.
        bonu_403 = ('We can send email but we are not allowed to READ this '
                    'mailbox yet. Someone with Microsoft admin rights must grant '
                    'read access to the same mail connection (Mail.Read). Until '
                    'then, invoices have to be uploaded.')
        with mock.patch('bonu.mailbox.graph_token', return_value='tok'), \
                mock.patch('bonu.mailbox.graph_get',
                           side_effect=MailboxUnavailable(bonu_403)), \
                mock.patch('core.notifications.send_html_with_cfo_cc',
                           side_effect=fake_send):
            call_command('load_adh_settlements', stdout=io.StringIO(),
                         stderr=io.StringIO())

        run = AdhSettlementRun.objects.get()
        self.assertEqual(run.status, AdhSettlementRun.Status.FAILED)
        self.assertIn('Mail.Read', run.error)
        self.assertIn('cannot read the mailbox', sent['subject'])
        self.assertEqual(sent['to'], [cs.ENTERED_BY_EMAIL])
        self.assertIn('Mail.Read', sent['html'])
        # The fault is still described — we only drop the wrong INSTRUCTION.
        self.assertIn('not allowed to READ', sent['html'])
        # 🔴 The Graph client is shared with bonu's invoice waiting-room, and
        # its wording ends "invoices have to be uploaded". In Keetile's ADH
        # email that is not clumsy, it is a WRONG INSTRUCTION: "upload" is a
        # real button in bonu and nothing at all in ADH. This is the email she
        # gets every week until the grant lands. (Fable round 2, 14-Sep-2026.)
        self.assertNotIn('invoice', sent['html'].lower())
        self.assertNotIn('upload', sent['html'].lower())
        # No claim detail anywhere near the subject line.
        self.assertNotIn('ADH2026', sent['subject'])

    @override_settings(ADH_SETTLEMENT_MAILBOX='')
    def test_an_unconfigured_mailbox_is_recorded_and_emailed_too(self):
        """The other way the SOURCE itself is unreachable, as opposed to the
        file being wrong: no mailbox is configured at all.

        This is the shape that caught the SFTP build out (Fable, 13-Sep-2026) -
        "we could not reach the source" is a different exception class from "the
        file was wrong", and catching only SettlementFileError let it kill the
        command with a traceback: no run record, no email to Keetile, nothing.
        NEVER FAILS SILENTLY has to survive the failure most likely to happen.
        """
        from django.core.management import call_command

        with mock.patch.object(LoadCommand, '_notify') as notify:
            call_command('load_adh_settlements', stdout=io.StringIO(),
                         stderr=io.StringIO())

        run = AdhSettlementRun.objects.get()
        self.assertEqual(run.status, AdhSettlementRun.Status.FAILED)
        self.assertIn('ADH_SETTLEMENT_MAILBOX', run.error)
        self.assertTrue(notify.called, 'Keetile was never told')

    @override_settings(ADH_SETTLEMENT_MAILBOX='health@alphadirect.co.bw')
    def test_no_maker_login_fails_before_the_run_row_is_written(self):
        """🔴 The worst silent failure of the lot, and it LOOKED like success.

        The maker was asked for inside create_payment_requests, which the
        command calls AFTER creating the run row — outside every `except`. So a
        missing login ended the Saturday with a traceback, no email, and a row
        already carrying this file's UNIQUE fingerprint and the model's default
        status of LOADED. Every later run of the same file then found that
        fingerprint, printed "already loaded ... Nothing to do" and stopped, so
        one missing login blocked that settlement for good, in silence.
        """
        from django.core.management import call_command

        User.objects.filter(email__iexact=cs.ENTERED_BY_EMAIL).delete()
        blob = make_listing(SAMPLE_ROWS)
        with mock.patch.object(cs, 'fetch_candidates', return_value=[dropped()]), \
                mock.patch('healthcare.afa_mailbox.read_attachment',
                           return_value=blob), \
                mock.patch.object(LoadCommand, '_notify') as notify:
            call_command('load_adh_settlements', stdout=io.StringIO(),
                         stderr=io.StringIO())

        run = AdhSettlementRun.objects.get()
        self.assertEqual(run.status, AdhSettlementRun.Status.FAILED)
        self.assertIn(cs.ENTERED_BY_EMAIL, run.error)
        self.assertTrue(notify.called, 'Keetile was never told')
        # 🔴 The fingerprint must NOT have been banked. If it were, the same
        # file next Saturday would be waved through as "already loaded".
        self.assertEqual(run.file_sha256, '')


class ParseTests(TestCase):

    def test_unzip_and_parse(self):
        blob = make_listing(SAMPLE_ROWS)
        name, sheet = cs.unzip_listing(blob, GOOD_NAME)
        self.assertTrue(name.endswith('.xlsx'))
        lines = cs.parse_eft_xls(sheet, filename=name)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]['claim_number'], 'ADH2026001')
        self.assertEqual(lines[0]['amount'], Decimal('1250.00'))
        self.assertEqual(lines[0]['account_number'], '62000000001')

    def test_a_bare_spreadsheet_attachment_is_read_not_refused(self):
        """The selector accepts a .xlsx attachment, so the reader must too."""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['Claim Number', 'Payee Name', 'Amount', 'Account Number',
                   'Bank', 'Branch Code', 'Reference'])
        ws.append(list(SAMPLE_ROWS[0]))
        buf = io.BytesIO()
        wb.save(buf)
        # .xlsx is itself a PK archive, which is exactly why the reader
        # decides on the attachment's NAME and not on its first two bytes.
        name, sheet = cs.unzip_listing(buf.getvalue(), 'ADI_AFT_20260912.xlsx')
        self.assertTrue(len(cs.parse_eft_xls(sheet, filename=name)) >= 1)

    def test_a_total_row_does_not_become_a_payment(self):
        rows = SAMPLE_ROWS + [('Total', '', '1730.50', '', '', '', '')]
        _n, sheet = cs.unzip_listing(make_listing(rows), GOOD_NAME)
        lines = cs.parse_eft_xls(sheet)
        self.assertEqual([l['claim_number'] for l in lines],
                         ['ADH2026001', 'ADH2026002'])

    def test_an_unreadable_amount_is_a_problem_not_a_zero_request(self):
        rows = [('ADH2026003', 'Maru Wellness Clinic', '', '62000000003',
                 'First National Bank', '282267', 'ADH2026003')]
        _n, sheet = cs.unzip_listing(make_listing(rows), GOOD_NAME)
        lines = cs.parse_eft_xls(sheet)
        self.assertEqual(len(lines), 1)
        self.assertIsNone(lines[0]['amount'])
        self.assertTrue(lines[0]['problem'])

    def test_an_unrecognised_header_refuses_rather_than_half_loads(self):
        import openpyxl
        wb = openpyxl.Workbook()
        wb.active.append(['Something', 'Else', 'Entirely'])
        buf = io.BytesIO()
        wb.save(buf)
        with self.assertRaises(cs.SettlementFileError):
            cs.parse_eft_xls(buf.getvalue())

    def test_a_zip_with_no_spreadsheet_is_refused(self):
        z = io.BytesIO()
        with zipfile.ZipFile(z, 'w') as zf:
            zf.writestr('readme.txt', 'nothing here')
        with self.assertRaises(cs.SettlementFileError):
            cs.unzip_listing(z.getvalue(), GOOD_NAME)


class DedupeKeyTests(TestCase):

    def test_same_content_same_key_regardless_of_the_file_it_came_in(self):
        a = cs.compute_line_dedupe_key('ADH2026001', Decimal('1250.00'),
                                       'ADH2026001', 'Maru Wellness Clinic')
        b = cs.compute_line_dedupe_key('adh2026001', Decimal('1250.00'),
                                       'ADH2026001', 'maru wellness clinic')
        self.assertEqual(a, b)

    def test_two_identical_lines_in_one_listing_survive_as_two(self):
        lines = [
            {'claim_number': 'ADH2026001', 'amount': Decimal('100.00'),
             'reference': 'R1', 'payee': 'Maru Wellness Clinic'},
            {'claim_number': 'ADH2026001', 'amount': Decimal('100.00'),
             'reference': 'R1', 'payee': 'Maru Wellness Clinic'},
        ]
        keys = [k for _l, k in cs.with_occurrences(lines)]
        self.assertEqual(len(set(keys)), 2)

    def test_the_fingerprint_is_the_attachment_bytes_not_the_message(self):
        """🔴 A forwarded copy is a new message id and a new subject. Neither
        may make the same settlement look new."""
        blob = make_listing(SAMPLE_ROWS)
        original = dropped(body=blob)
        forwarded = dropped(message_id='msg-fw', attachment_id='att-fw',
                            subject='FW: ' + SUBJ_OURS,
                            name='ADI_AFT_20260912 (1).zip', body=blob)
        self.assertNotEqual(original.message_id, forwarded.message_id)
        self.assertNotEqual(original.subject, forwarded.subject)
        self.assertEqual(cs.file_fingerprint(original.body),
                         cs.file_fingerprint(forwarded.body))

    def test_the_database_refuses_the_same_key_twice(self):
        """The guard is a UNIQUE column, not a Python `if`."""
        run = AdhSettlementRun.objects.create(
            loaded_on=timezone.localdate(), file_name=GOOD_NAME,
            file_sha256='a' * 64)
        key = cs.compute_line_dedupe_key('ADH2026001', Decimal('1250.00'),
                                         'ADH2026001', 'Maru Wellness Clinic')
        AdhSettlementLine.objects.create(run=run, dedupe_key=key,
                                         claim_number='ADH2026001',
                                         amount=Decimal('1250.00'))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AdhSettlementLine.objects.create(
                    run=run, dedupe_key=key, claim_number='ADH2026001',
                    amount=Decimal('1250.00'))

    def test_a_forwarded_copy_cannot_become_a_second_run(self):
        """Same bytes, different message: the unique fingerprint says no."""
        sha = cs.file_fingerprint(make_listing(SAMPLE_ROWS))
        AdhSettlementRun.objects.create(loaded_on=timezone.localdate(),
                                        file_name=GOOD_NAME, file_sha256=sha)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AdhSettlementRun.objects.create(
                    loaded_on=timezone.localdate(),
                    file_name='ADI_AFT_20260912 (1).zip', file_sha256=sha)


class LoadThroughTheGatedPathTests(TestCase):
    """The loader creates requests through the ORDINARY create endpoint, and a
    file that arrives twice does not create the payments twice."""

    def setUp(self):
        from taskboard.test_helpers import seed_adic
        seed_adic()
        # Keetile is the maker of record — the loader enters in her name.
        User.objects.create_user('kmokhendo', email=cs.ENTERED_BY_EMAIL,
                                 password='x', first_name='Keetile')
        # A finance approver must exist or the create path cannot route.
        User.objects.create_user('kago', email='ktshutlhedi@alphadirect.co.bw',
                                 password='x', first_name='Kago')
        _n, self.sheet = cs.unzip_listing(make_listing(SAMPLE_ROWS), GOOD_NAME)

    def _run(self, sha):
        return AdhSettlementRun.objects.create(
            loaded_on=timezone.localdate(), file_name=GOOD_NAME,
            file_sha256=sha)

    def _load(self, run):
        lines = cs.parse_eft_xls(self.sheet)
        # The covering sentence is an LLM call in the request path; it falls
        # back on its own, but a test must not depend on a vendor being up.
        with mock.patch('taskboard.payment_views._ai_summary',
                        return_value='ADH settlement.'):
            return cs.create_payment_requests(run, lines)

    def test_one_payment_request_per_claim_line(self):
        from taskboard.models import PaymentRequest
        result = self._load(self._run('c' * 64))
        self.assertEqual(len(result.created), 2, result.failed)
        self.assertEqual(len(result.failed), 0, result.failed)
        prs = PaymentRequest.objects.filter(category='adh')
        self.assertEqual(prs.count(), 2)
        # It went through the ordinary create path, so it carries what that
        # path stamps — a real reference and the two-stage status.
        for pr in prs:
            self.assertTrue(pr.ref.startswith('PAY/'))
            self.assertIn(pr.status, (PaymentRequest.Status.PENDING_FINANCE,
                                      PaymentRequest.Status.EXCEPTION))
            # 🔴 The boundary: a request, never a payment.
            self.assertNotEqual(pr.status, PaymentRequest.Status.PAID)

    def test_the_same_file_twice_does_not_create_the_payments_twice(self):
        from taskboard.models import PaymentRequest
        first = self._load(self._run('d' * 64))
        self.assertEqual(len(first.created), 2)
        second = self._load(self._run('e' * 64))
        self.assertEqual(len(second.created), 0)
        self.assertEqual(len(second.skipped), 2)
        self.assertEqual(PaymentRequest.objects.filter(category='adh').count(), 2)
        self.assertEqual(AdhSettlementLine.objects.count(), 2)

    def test_no_login_for_the_maker_stops_the_whole_load(self):
        User.objects.filter(email__iexact=cs.ENTERED_BY_EMAIL).delete()
        with self.assertRaises(cs.SettlementFileError):
            self._load(self._run('f' * 64))
        self.assertEqual(AdhSettlementLine.objects.count(), 0)


class SenderIsParsedAsAHeaderTests(TestCase):
    """AFA send from the same job that always sent the mail, so the `from`
    arrives in header form — `Demi <demi@afa.co.bw>` — as often as it
    arrives bare. Comparing the whole header string to `demi@afa.co.bw` rejected
    EVERY such email for "wrong sender", which is the one rejection reason that
    reads like a security event rather than a formatting difference — and it
    rejects all four emails, so no settlement loads at all. (Fable, 13-Sep-2026.)
    """

    def test_a_header_style_from_is_accepted(self):
        self.assertEqual(
            cs.rejection_reason(dropped(sender='Demi <demi@afa.co.bw>')), '')

    def test_a_quoted_display_name_is_accepted(self):
        self.assertEqual(
            cs.rejection_reason(dropped(sender='"Demi, AFA" <Demi@AFA.co.bw>')), '')

    def test_a_bare_address_still_works(self):
        self.assertEqual(cs.rejection_reason(dropped(sender=cs.AFA_SENDER)), '')

    def test_a_genuinely_different_sender_is_still_refused(self):
        reason = cs.rejection_reason(dropped(sender='Someone <hacker@example.com>'))
        self.assertIn('not demi@afa.co.bw', reason)

    def test_a_display_name_carrying_the_real_address_does_not_sneak_through(self):
        """The address is what counts, never the label around it."""
        reason = cs.rejection_reason(dropped(sender='demi@afa.co.bw <evil@example.com>'))
        self.assertIn('not demi@afa.co.bw', reason)


def _give_title(user, title: str):
    """Attach a UserProfile with this title (the profile may already exist via
    a signal), so CanViewFinancials has a title to judge."""
    from core.models import UserProfile
    prof, _ = UserProfile.objects.get_or_create(user=user)
    prof.title = title
    prof.is_active = True
    prof.save()
    return prof


class SettlementRunsScreenIsGatedTests(TestCase):
    """The runs screen lists claim numbers and settlement amounts for
    identifiable medical claims. IsAuthenticated served all of it to every
    signed-in staffer in the company. Same gate as the AFA load-file screens —
    no new permission class. (Fable, 13-Sep-2026.)"""

    def _get(self, user):
        from rest_framework.test import APIRequestFactory, force_authenticate
        from healthcare.claims_settlement_views import adh_settlement_runs
        req = APIRequestFactory().get('/health/adh-settlements/runs/')
        force_authenticate(req, user=user)
        return adh_settlement_runs(req)

    def test_an_ordinary_signed_in_staffer_is_refused(self):
        u = User.objects.create_user('someone', 'someone@alphadirect.co.bw', 'x')
        self.assertEqual(self._get(u).status_code, 403)

    def test_an_authorised_operator_gets_in(self):
        u = User.objects.create_user('rtonkope', 'rtonkope@alphadirect.co.bw', 'x')
        self.assertEqual(self._get(u).status_code, 200)

    def test_a_finance_person_gets_in(self):
        """CFO directive 2026-09-13: "Health and Finance". The finance half is
        CanViewFinancials, composed with the health allow-list — not a new
        class. An accountant is finance but is on no healthcare allow-list."""
        u = User.objects.create_user('anaccountant', 'anaccountant@alphadirect.co.bw', 'x')
        _give_title(u, 'accountant')
        self.assertEqual(self._get(u).status_code, 200)

    def test_an_operational_title_is_still_refused(self):
        """A signed-in staffer with a non-finance title is neither Health nor
        Finance, so the medical claim numbers and amounts stay hidden."""
        u = User.objects.create_user('anofficer', 'anofficer@alphadirect.co.bw', 'x')
        _give_title(u, 'operations')
        self.assertEqual(self._get(u).status_code, 403)

    def test_the_sidebar_probe_matches_the_gate(self):
        """The menu-hiding probe must answer exactly what the page enforces —
        a probe that says yes where the page says 403 is the refusal we set out
        to stop."""
        from rest_framework.test import APIRequestFactory, force_authenticate
        from healthcare.claims_settlement_views import adh_settlement_access
        for username, title, expected in (
            ('probe_kmokhendo', None, True),
            ('probe_acct', 'accountant', True),
            ('probe_officer', 'operations', False),
        ):
            email = ('kmokhendo@alphadirect.co.bw' if title is None
                     else f'{username}@alphadirect.co.bw')
            u = User.objects.create_user(username, email, 'x')
            if title:
                _give_title(u, title)
            req = APIRequestFactory().get('/health/adh-settlements/access/')
            force_authenticate(req, user=u)
            resp = adh_settlement_access(req)
            self.assertEqual(resp.data['allowed'], expected,
                             f'{username} probe said {resp.data["allowed"]}')


class KeetileMokhendoIsAnAfaOperatorTests(TestCase):
    """CFO added Keetile Mokhendo (kmokhendo) on 2026-09-13 — he owns the ADH
    settlement feed. Proves she is admitted AND that someone off the list is
    still refused, so the test would fail if the allow-list were emptied."""

    def _get(self, user):
        from rest_framework.test import APIRequestFactory, force_authenticate
        from healthcare.afa_views import afa_runs
        req = APIRequestFactory().get('/health/afa/runs/')
        force_authenticate(req, user=user)
        return afa_runs(req)

    def test_kmokhendo_is_admitted(self):
        u = User.objects.create_user('kmokhendo', 'kmokhendo@alphadirect.co.bw', 'x')
        self.assertEqual(self._get(u).status_code, 200)

    def test_someone_not_on_the_list_is_still_refused(self):
        u = User.objects.create_user('kmokhendox', 'kmokhendox@alphadirect.co.bw', 'x')
        self.assertEqual(self._get(u).status_code, 403)


class FailedRunStillExplainsItselfTests(TestCase):
    """A run that loaded NOTHING is the Saturday nobody can explain from the
    outside, so it is the run that most needs the reasons stored.

    The settlement screen renders the structured `rejected` list, not the prose
    message. Before this, a failed run wrote the message and dropped the list —
    so the red "nothing was loaded" box quoted the reasons while the section
    underneath, the whole reason the screen exists, said no other files were
    recorded. The row contradicted itself.
    """

    def test_nothing_matched_keeps_every_reason_on_the_exception(self):
        files = [
            dropped(name='ADI_AFT_RSA_20260912.zip',
                    subject='EFT FILE: ADI_AFT_RSA 20260912 - MANUAL SUBMISSION REQUIRED'),
            dropped(name='ADI_REMIT_20260912.zip',
                    subject='REMITTANCE ADVICE - MANUAL SUBMISSION REQUIRED'),
        ]
        with mock.patch.object(cs, 'fetch_candidates', return_value=files):
            with self.assertRaises(cs.SettlementFileError) as caught:
                cs.fetch_file()
        self.assertEqual(len(caught.exception.rejected), 2)
        by_name = {r['name']: r['reason'] for r in caught.exception.rejected}
        self.assertIn(cs.TOKEN_RSA, by_name['ADI_AFT_RSA_20260912.zip'])
        self.assertIn(cs.TOKEN_EFT_FILE, by_name['ADI_REMIT_20260912.zip'])

    def test_two_matching_files_still_report_what_else_arrived(self):
        files = [dropped(), dropped(name='ADI_AFT_20260912_copy.zip'),
                 dropped(name='ADI_REMIT_20260912.zip',
                         subject='REMITTANCE ADVICE - MANUAL SUBMISSION REQUIRED')]
        with self.assertRaises(cs.AmbiguousSettlementFile) as caught:
            cs.select_settlement_file(files)
        self.assertEqual([r['name'] for r in caught.exception.rejected],
                         ['ADI_REMIT_20260912.zip'])

    def test_an_oversized_file_still_reports_what_else_arrived(self):
        """The LAST raise in fetch_file, after the bytes are read.

        `rejected` is already computed by then, so dropping it here loses the
        other files' reasons on exactly the Saturday something went wrong —
        and the screen, reading the row's empty list, says no other files were
        recorded. The honest-looking default is what hides the miss.
        """
        files = [dropped(),
                 dropped(name='ADI_AFT_RSA_20260912.zip',
                         subject='EFT FILE: ADI_AFT_RSA 20260912 - MANUAL SUBMISSION REQUIRED')]
        # The cap is shrunk rather than allocating 64MB of 'x' in a test.
        with mock.patch.object(cs, 'fetch_candidates', return_value=files):
            with mock.patch.object(cs, 'MAX_ZIP_BYTES', 8):
                with mock.patch('healthcare.afa_mailbox.read_attachment',
                                return_value=b'x' * 9):
                    with self.assertRaises(cs.SettlementFileError) as caught:
                        cs.fetch_file()
        self.assertIn('far larger', str(caught.exception))
        self.assertEqual([r['name'] for r in caught.exception.rejected],
                         ['ADI_AFT_RSA_20260912.zip'])

    def test_the_failed_run_row_carries_the_reasons_the_screen_renders(self):
        cmd = LoadCommand()
        cmd.stderr = io.StringIO()
        cmd._fail(timezone.localdate(), 'An unrecognised column heading.',
                  notify=False, dry=False, file_name=GOOD_NAME,
                  rejected=[{'name': 'ADI_AFT_RSA_20260912.zip',
                             'reason': 'contains "_RSA" — that is the South African file'}])
        run = AdhSettlementRun.objects.get()
        self.assertEqual(run.status, AdhSettlementRun.Status.FAILED)
        # The message alone is not enough — the screen reads this list.
        self.assertEqual(len(run.rejected), 1)
        self.assertIn('_RSA', run.rejected[0]['reason'])

    def test_a_dry_run_writes_no_row_at_all(self):
        cmd = LoadCommand()
        cmd.stderr = io.StringIO()
        cmd._fail(timezone.localdate(), 'nothing on the drop',
                  notify=False, dry=True, rejected=[{'name': 'x', 'reason': 'y'}])
        self.assertEqual(AdhSettlementRun.objects.count(), 0)
