"""WHY: a DOWNLOAD is not a new pack, and the invoice runs the other way.

Three things Fable found in the merged B3 pack, each proved here:

  1. H-1 — every "Export Excel" / "Invoice PDF" press called the generator
     again. That wrote a second and a third ``GenricPackRun`` and burned the
     next invoice number each time: July generated as 025 came back as a PDF
     numbered 027 inside a file named 025, next month started at 028, and any
     collection charges keyed in at generate time came back as R0.
  2. The invoice was INVERTED. It printed "PAYABLE TO" over Alpha Direct's own
     FNB account 62403392335 — i.e. GENRIC pays us. This is a quota-share
     cession: the cedant pays the reinsurer (CFO, 13 Sep 2026). GENRIC's own
     account is never guessed.
  3. The pack is a regulatory submission, so it sits behind the NBFIRA gate
     (CFO directive 2026-08-17), not the wider financials gate.
"""
import base64
import re
import zlib
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import UserProfile
from genric import api_views as V
from genric import constants as K
from genric.exports import remit_to_block
from genric.models import GenricPackRun

_REMIT = 'Test Reinsurer Bank - Account 000000000 - Branch 000000 - SWIFT TESTZAJJ'


def pdf_text(blob: bytes) -> str:
    """Every text run in the PDF, decoded.

    reportlab writes its content streams ASCII85-then-Flate encoded, so a plain
    substring search over the raw bytes finds only the document metadata. Undo
    both filters, then strip the PDF punctuation so a string is findable however
    reportlab laid it out.
    """
    out = [blob.decode('latin-1', errors='replace')]
    for raw in re.findall(rb'stream\r?\n(.*?)endstream', blob, re.S):
        data = raw.strip()
        try:
            if data.endswith(b'~>'):
                data = base64.a85decode(data, adobe=True)
            out.append(zlib.decompress(data).decode('latin-1', errors='replace'))
        except (ValueError, zlib.error):
            continue
    return re.sub(r'[()\\\[\]]', '', '\n'.join(out))


def _profile(user, title):
    UserProfile.objects.update_or_create(
        user=user, defaults={'title': title, 'is_active': True})
    return User.objects.get(pk=user.pk)


def _run(invoice_number='GENRIC-1-025', charges='150.00'):
    return GenricPackRun.objects.create(
        period_year=2026, period_month=7,
        status=GenricPackRun.Status.COMPLETE,
        invoice_number=invoice_number,
        collection_charges=Decimal(charges),
        confirmed_gwp_incl_vat=Decimal('9207.00'),
    )


def _cfo():
    u = User.objects.create_user('cfo-genric', email='cfo@x.co', password='x')
    return _profile(u, UserProfile.Title.CFO)


@override_settings(GENRIC_REINSURER_BANK_DETAILS=_REMIT)
class DownloadsDoNotGenerateTests(TestCase):
    """H-1. A download RE-RENDERS the stored run. Nothing is written."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = _cfo()
        self.run = _run()

    def _download(self, view, pk):
        req = self.factory.post('/api/v1/genric/runs/%s/' % pk)
        force_authenticate(req, user=self.user)
        return view(req, pk=pk)

    def test_two_downloads_leave_the_run_count_unchanged(self):
        before = GenricPackRun.objects.count()
        self.assertEqual(self._download(V.genric_export_xlsx, self.run.pk).status_code, 200)
        self.assertEqual(
            self._download(V.genric_export_invoice_pdf, self.run.pk).status_code, 200)
        self.assertEqual(
            GenricPackRun.objects.count(), before,
            'a download wrote a new pack run — it burned the next invoice number')

    def test_the_pdf_carries_the_runs_own_invoice_number(self):
        resp = self._download(V.genric_export_invoice_pdf, self.run.pk)
        text = pdf_text(resp.content)
        self.assertIn('GENRIC-1-025', text,
                      'the PDF is numbered differently from the run it came from')
        self.assertNotIn('GENRIC-1-026', text)
        self.assertNotIn('GENRIC-1-027', text)

    def test_the_filename_and_the_document_agree(self):
        resp = self._download(V.genric_export_invoice_pdf, self.run.pk)
        self.assertIn('GENRIC-1-025', resp['Content-Disposition'])
        self.assertIn('GENRIC-1-025', pdf_text(resp.content))

    def test_the_stored_collection_charges_come_back_on_a_download(self):
        """R150 keyed in at generate time must not re-render as R0."""
        req = self.factory.post('/api/v1/genric/runs/%s/' % self.run.pk)
        force_authenticate(req, user=self.user)
        result = V._rebuild(self.run, Request(req))
        self.assertEqual(result.context.cession.collection_charges,
                         Decimal('150.00'))

    def test_the_rendered_result_is_the_stored_run_not_a_new_one(self):
        req = self.factory.post('/api/v1/genric/runs/%s/' % self.run.pk)
        force_authenticate(req, user=self.user)
        result = V._rebuild(self.run, Request(req))
        self.assertEqual(result.run.pk, self.run.pk)


class InvoiceDirectionTests(TestCase):
    """Alpha Direct PAYS GENRIC. Flip it back and these go red."""

    def test_the_treaty_direction_is_cedant_to_reinsurer(self):
        self.assertIn('pays GENRIC', K.PAYMENT_DIRECTION)

    def test_alpha_directs_own_account_is_not_the_payee(self):
        self.assertFalse(hasattr(K, 'GENRIC_REMIT_TO'),
                         'the inverted hard-coded remit-to is back')
        direction, label, value = remit_to_block()
        self.assertNotIn('62403392335', '%s%s%s' % (direction, label, value),
                         "Alpha Direct's own FNB account is printed as the payee")

    @override_settings(GENRIC_REINSURER_BANK_DETAILS=_REMIT)
    def test_with_the_details_on_file_the_invoice_says_who_pays_whom(self):
        direction, label, value = remit_to_block()
        self.assertIn('pays GENRIC', direction)
        self.assertIn('PAYS GENRIC', label)
        self.assertEqual(value, _REMIT)

    @override_settings(GENRIC_REINSURER_BANK_DETAILS=None)
    def test_with_no_details_it_refuses_loudly_and_invents_nothing(self):
        _, label, value = remit_to_block()
        self.assertIn('NOT ON FILE', label)
        self.assertIn('GENRIC_REINSURER_BANK_DETAILS', value)
        self.assertIsNone(re.search(r'\d{9,}', value),
                          'an account number was invented for GENRIC')

    @override_settings(GENRIC_REINSURER_BANK_DETAILS=None)
    def test_the_missing_details_are_raised_as_an_open_question(self):
        req = APIRequestFactory().get('/api/v1/genric/config/')
        force_authenticate(req, user=_cfo())
        body = V.genric_config(req).data
        settings_asked = {q['setting'] for q in body['open_questions']}
        self.assertIn('GENRIC_REINSURER_BANK_DETAILS', settings_asked)
        self.assertIn('pays GENRIC', body['payment_direction'])

    @override_settings(GENRIC_REINSURER_BANK_DETAILS=_REMIT)
    def test_the_printed_invoice_never_names_the_alpha_direct_account(self):
        run = _run()
        req = APIRequestFactory().post('/api/v1/genric/runs/%s/' % run.pk)
        force_authenticate(req, user=_cfo())
        text = pdf_text(V.genric_export_invoice_pdf(req, pk=run.pk).content)
        self.assertNotIn('62403392335', text)
        self.assertIn('PAYS GENRIC', text)


@override_settings(GENRIC_REINSURER_BANK_DETAILS=_REMIT)
class AccessTierTests(TestCase):
    """CFO directive 2026-08-17 — the NBFIRA gate, not the wider financials one."""

    def _call(self, user):
        req = APIRequestFactory().get('/api/v1/genric/config/')
        force_authenticate(req, user=user)
        return V.genric_config(req)

    def test_an_accountant_with_financials_access_is_refused(self):
        u = User.objects.create_user('acc-genric', email='acc@x.co', password='x')
        u = _profile(u, UserProfile.Title.ACCOUNTANT)
        self.assertTrue(u.profile.can_view_financials,
                        'this test only discriminates if the accountant DOES '
                        'have plain financials access')
        self.assertEqual(self._call(u).status_code, 403)

    def test_the_finance_manager_gets_in(self):
        u = User.objects.create_user('fm-genric', email='fm@x.co', password='x')
        u = _profile(u, UserProfile.Title.FINANCE_MANAGER)
        self.assertEqual(self._call(u).status_code, 200)

    def test_the_financial_controller_gets_in(self):
        u = User.objects.create_user('fc-genric', email='fc@x.co', password='x')
        u = _profile(u, UserProfile.Title.FINANCIAL_CONTROLLER)
        self.assertEqual(self._call(u).status_code, 200)

    def test_every_genric_endpoint_is_behind_the_same_gate(self):
        from core.permissions import CanViewRegulatoryReturns
        for view in (V.genric_config, V.genric_generate, V.genric_runs,
                     V.genric_run_detail, V.genric_export_xlsx,
                     V.genric_export_invoice_pdf):
            classes = getattr(view.cls, 'permission_classes', [])
            self.assertIn(CanViewRegulatoryReturns, classes,
                          '%s is on the wrong gate' % view.__name__)


class OpenQuestionsTests(TestCase):
    """One question is answered. The other MUST stay open.

    The CFO fixed the pay window at 30 days on 14 Sep 2026, so it drops off the
    open-questions list. GENRIC's bank details were NOT supplied and were NOT
    invented: the invoice still refuses to name an account, and the question
    still has to be visible to whoever opens the page. Answering one question by
    quietly closing both is the failure this test exists to catch.
    """

    def _questions(self):
        req = APIRequestFactory().get('/api/v1/genric/config/')
        force_authenticate(req, user=_cfo())
        return V.genric_config(req).data['open_questions']

    @override_settings(GENRIC_REINSURER_BANK_DETAILS=None)
    def test_the_pay_window_is_no_longer_an_open_question(self):
        blocks = {q['blocks'] for q in self._questions()}
        self.assertNotIn('Cancellations report', blocks)

    @override_settings(GENRIC_REINSURER_BANK_DETAILS=None)
    def test_the_reinsurers_bank_details_are_still_an_open_question(self):
        qs = self._questions()
        self.assertEqual(len(qs), 1, 'answering the pay window closed both questions')
        self.assertEqual(qs[0]['blocks'], 'Invoice — where to pay')
        self.assertEqual(qs[0]['setting'], 'GENRIC_REINSURER_BANK_DETAILS')

    @override_settings(GENRIC_REINSURER_BANK_DETAILS=None)
    def test_the_invoice_still_refuses_to_name_an_account(self):
        # The refusal is the LABEL — the heading a reader's eye lands on —
        # and the value underneath explains it and names the setting.
        direction, label, value = remit_to_block()
        self.assertIn('NOT ON FILE', label)
        self.assertIn('DO NOT PAY AGAINST THIS DOCUMENT', label)
        self.assertIn('Nothing was assumed', value)
        self.assertIn('GENRIC_REINSURER_BANK_DETAILS', value)

    @override_settings(GENRIC_REINSURER_BANK_DETAILS=None)
    def test_no_account_number_was_invented_to_fill_the_gap(self):
        direction, label, value = remit_to_block()
        self.assertFalse(re.search(r'\d{6,}', value),
                         'something that looks like an account number appeared '
                         'on an invoice whose payee is still unknown')

    @override_settings(GENRIC_REINSURER_BANK_DETAILS=None)
    def test_the_pay_window_setting_is_named_so_finance_can_find_it(self):
        """The answered one has to be discoverable too — on the config payload."""
        from genric.config import UNPAID_DAYS_KEY
        req = APIRequestFactory().get('/api/v1/genric/config/')
        force_authenticate(req, user=_cfo())
        self.assertEqual(V.genric_config(req).data['pay_window'],
                         {'setting': UNPAID_DAYS_KEY, 'days': 30,
                          'plain': 'Policies unpaid for more than 30 days'})
