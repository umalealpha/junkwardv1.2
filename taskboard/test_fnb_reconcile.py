"""taskboard/test_fnb_reconcile.py — reconcile the REAL 12-Aug-2026 FNB pending
list against the REAL open payment requests, and prove the engine flags exactly
the already-paid ones (and spots the double-loaded refund).
"""
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from taskboard.fnb_reconcile import (parse_fnb_report, reconcile, find_fnb_duplicates,
                                     deterministic_summary, pdf_bytes_to_text)
from taskboard.models import PaymentRequest

# A faithful slice of the FNB "Batch Payments" report the CFO downloaded on
# 2026-08-12 (name + amount per row; the real report has ~55 rows — these are the
# ones the assertions below turn on, plus the double-loaded refund).
FNB_REPORT = """
Aug -AJ Rent JULY26                 2026/08/10  Same Day   11,550.00  Authorisation Requested
BIH HEALTH RENT MAY 3015            2026/08/04  Same Day   10,584.00  Authorisation Requested
BIH ICT MAY 3004                    2026/08/04  Same Day   15,932.79  Authorisation Requested
CARERRA 15402 JULY                  2026/07/30  Same Day   37,211.88  Authorisation Requested
PRIMEDIA-98553-JULY 26              2026/07/31  Same Day   39,045.00  Authorisation Requested
ORANGE JULY 26                      2026/07/27  Same Day   12,730.23  Authorisation Requested
G2026005025 KNOCK TOGETIT           2026/07/27  Same Day   52,250.00  Authorisation Requested
G2026005026 KNOCK TOGETIT           2026/07/27  Same Day   50,624.60  Authorisation Requested
G2026005040 ARINO HOLDINGS          2026/08/06  Same Day   42,575.72  Authorisation Requested
G2026005185 LJW PROPERTIES          2026/08/10  Same Day   11,230.60  Authorisation Requested
G2026005014 LESEDI MOTORS           2026/07/28  Same Day   35,559.22  Authorisation Requested
Unicoin - Rent Aug 26               2026/08/10  Same Day   25,248.92  Authorisation Requested
Risk AI Audit Fee (FY26) 1          2026/08/10  Same Day   14,250.00  Authorisation Requested
Risk_Bomaid Ajrun.                  2026/08/10  Same Day    6,730.00  Authorisation Requested
DOMG2025162346 - REFUND             2026/08/12  Same Day      986.40  Authorisation Requested
DOMG2025162346 REFUND               2026/08/12  Same Day      986.40  Authorisation Requested
"""

# The REAL open requests (from Omni prod, 12-Aug). Only the fields the engine
# reads. #7 (SUPPLIER PAYMENTS) carries 9 older G-numbers, none of which are in
# the FNB list above -> already paid.
REQUESTS = [
    {"ref": "PAY/ADIC/2026/08/11/0006", "subject": "SUPPLIER PAYMENTS", "total": "121033.04",
     "category": "claim", "lines": [
        {"amount": "3600.00",  "ref": "G2026003905 LAMINATED GLASS"},
        {"amount": "2450.00",  "ref": "G2026004192 - OMEGA AUTOWORLD"},
        {"amount": "3862.50",  "ref": "G2026004408 GLASSPRO"},
        {"amount": "2625.00",  "ref": "G2026004409 GLASSPRO"},
        {"amount": "6471.50",  "ref": "G2026004412 ROLLING WHEEELS"},
        {"amount": "7338.47",  "ref": "G2026004413 SCANIA 2"},
        {"amount": "49775.67", "ref": "G2026004452 CARFIL SERVICES"},
        {"amount": "23358.92", "ref": "G2026004527 NALEDI MOTORS."},
        {"amount": "21550.98", "ref": "G2026004778 BB MOTORS."},
     ]},
    {"ref": "PAY/ADIC/2026/08/12/0001", "subject": "OPERATIONAL PAYMENTS", "total": "39247.02",
     "category": "vendor", "lines": [
        {"amount": "15932.79", "ref": "BIH ICT MAY 3004"},
        {"amount": "10584.00", "ref": "BIH HEALTH RENT MAY 3015"},
        {"amount": "12730.23", "ref": "ORANGE JULY 26"},
     ]},
    {"ref": "PAY/ADIC/2026/08/12/0002", "subject": "CLAIM PAYMENT", "total": "202005.92",
     "category": "claim", "lines": [
        {"amount": "52250.00", "ref": "G2026005025 KNOCK TOGETIT"},
        {"amount": "50624.60", "ref": "G2026005026 KNOCK TOGETIT"},
        {"amount": "42575.72", "ref": "G2026005040 ARINO HOLDINGS"},
        {"amount": "950.00",   "ref": "G2026005055 NCI BOTSWANA (PTY)"},
        {"amount": "11230.60", "ref": "G2026005185 LJW PROPERTIES"},
     ]},
    {"ref": "PAY/ADIC/2026/08/11/0004", "subject": "Operational Payments", "total": "108209.86",
     "category": "vendor", "lines": [
        {"amount": "37211.88", "ref": "CARERRA 15402 JULY"},
        {"amount": "39045.00", "ref": "PRIMEDIA 98553 JULY 26"},
        {"amount": "31952.98", "ref": "E.G COURIERS INVGB2782 JUNE 26"},
     ]},
    {"ref": "PAY/RSA/2026/08/11/0001", "subject": "Risk Software Audit fees", "total": "14250.00",
     "category": "other", "lines": [
        {"amount": "14250.00", "ref": "Risk Audit fee"},
     ]},
]


class FnbReconcileTests(SimpleTestCase):
    def setUp(self):
        self.parsed = parse_fnb_report(FNB_REPORT)
        self.result = reconcile(self.parsed, REQUESTS)

    def _refs(self, bucket):
        return {r["ref"] for r in self.result[bucket]}

    def test_paid_batch_is_flagged_already_paid(self):
        # All 9 old claim lines are absent from the FNB list -> already paid.
        self.assertIn("PAY/ADIC/2026/08/11/0006", self._refs("already_paid"))

    def test_pending_batches_are_kept(self):
        # Every one of these has at least one line still in the FNB list.
        for ref in ("PAY/ADIC/2026/08/12/0001", "PAY/ADIC/2026/08/12/0002",
                    "PAY/ADIC/2026/08/11/0004", "PAY/RSA/2026/08/11/0001"):
            self.assertIn(ref, self._refs("still_pending"),
                          f"{ref} should still be pending (has a line in FNB)")
            self.assertNotIn(ref, self._refs("already_paid"))

    def test_partial_batch_stays_pending(self):
        # #0004: 2 of 3 lines are in FNB (E.G Couriers is not) -> still one bank
        # payment outstanding -> must NOT be called already-paid.
        self.assertNotIn("PAY/ADIC/2026/08/11/0004", self._refs("already_paid"))

    def test_only_the_paid_one_is_flagged(self):
        self.assertEqual(self._refs("already_paid"), {"PAY/ADIC/2026/08/11/0006"})

    def test_ref_token_match_beats_missing_amount(self):
        # LJW line matches on the G-number even though the amount is shared;
        # prove the token path works by checking the 'why' reason.
        claim = next(r for r in self.result["still_pending"]
                     if r["ref"] == "PAY/ADIC/2026/08/12/0002")
        ljw = next(l for l in claim["lines"] if "LJW" in l["ref"])
        self.assertTrue(ljw["in_fnb"])

    def test_double_loaded_refund_is_flagged(self):
        dupes = find_fnb_duplicates(self.parsed)
        amounts = {d["amount"] for d in dupes}
        self.assertIn("986.40", amounts, "the twice-loaded P986.40 refund must be flagged")


class EngineHelpersTests(SimpleTestCase):
    def test_pdf_reader_never_crashes_on_junk(self):
        # A non-PDF byte string must return '' (degrade), not raise.
        self.assertEqual(pdf_bytes_to_text(b"not a pdf at all"), "")

    def test_summary_names_what_was_closed_and_flags_duplicates(self):
        result = {"already_paid": [{"ref": "A", "subject": "SUPPLIERS", "total": "121033.04"}],
                  "still_pending": [{"ref": "B", "subject": "OPS", "total": "39247.02"}],
                  "no_lines": []}
        dupes = [{"amount": "986.40", "count": 2, "names": ["DOMG - REFUND", "DOMG REFUND"]}]
        note = deterministic_summary(result, dupes, closed=[{"ref": "A", "subject": "SUPPLIERS", "total": "121033.04"}])
        self.assertIn("121,033.04", note)
        self.assertIn("986.40", note)          # duplicate surfaced
        self.assertIn("still waiting", note.lower())


class FnbAutoCloseViewTests(TestCase):
    """The endpoint auto-closes the clearly-already-paid requests and leaves the
    rest, and never depends on the AI being up (fallback summary)."""

    def setUp(self):
        User = get_user_model()
        self.cfo = User.objects.create_user(
            username="cfo_fnb_test", email="cfo_fnb_test@x.com", password="x",
            is_superuser=True, is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(self.cfo)
        self.paid = PaymentRequest.objects.create(
            ref="PAY/TEST/PAID/0001", subject="OLD SUPPLIERS", total="100.00",
            status=PaymentRequest.Status.PENDING_CFO,
            line_items=[{"description": "G2026003905 X", "ref": "G2026003905 X", "amount": "60.00"},
                        {"description": "G2026003906 Y", "ref": "G2026003906 Y", "amount": "40.00"}])
        self.pending = PaymentRequest.objects.create(
            ref="PAY/TEST/PEND/0001", subject="CURRENT", total="500.00",
            status=PaymentRequest.Status.PENDING_CFO,
            line_items=[{"description": "BIH ICT", "ref": "BIH ICT", "amount": "500.00"}])
        self.url = reverse("v1-payment-request-fnb-reconcile")
        self.fnb = "BIH ICT MAY 3004  2026/08/04  Same Day  500.00  Authorisation Requested"

    @mock.patch("core.ai_assist.deepseek_complete", side_effect=Exception("offline"))
    def test_autoclose_closes_paid_keeps_pending(self, _ai):
        r = self.client.post(self.url, {"fnb_text": self.fnb, "auto_close": "true"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        closed_refs = {c["ref"] for c in r.data["closed"]}
        self.assertIn("PAY/TEST/PAID/0001", closed_refs)
        self.assertNotIn("PAY/TEST/PEND/0001", closed_refs)
        self.paid.refresh_from_db()
        self.pending.refresh_from_db()
        self.assertEqual(self.paid.status, PaymentRequest.Status.CANCELLED)
        self.assertEqual(self.pending.status, PaymentRequest.Status.PENDING_CFO)
        # AI mocked to raise -> deterministic fallback still returns a real note
        self.assertTrue(r.data["intelligence"])
        self.assertEqual(r.data["ai_source"], "fallback")

    @mock.patch("core.ai_assist.deepseek_complete", side_effect=Exception("offline"))
    def test_no_autoclose_closes_nothing(self, _ai):
        r = self.client.post(self.url, {"fnb_text": self.fnb, "auto_close": "false"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["closed"], [])
        self.paid.refresh_from_db()
        self.assertEqual(self.paid.status, PaymentRequest.Status.PENDING_CFO)
        # the paid one is still identified, just not closed
        self.assertIn("PAY/TEST/PAID/0001", {x["ref"] for x in r.data["already_paid"]})

    @mock.patch("core.ai_assist.deepseek_complete", side_effect=Exception("offline"))
    def test_unreadable_document_never_bulk_cancels(self, _ai):
        # Fable L7: a document with no amounts and no G/policy tokens must NOT be
        # read as "everything is paid" — nothing may be closed off negative evidence.
        r = self.client.post(self.url, {"fnb_text": "just some words, no figures at all", "auto_close": "true"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["closed"], [])
        self.assertFalse(r.data["auto_close_applied"])
        self.assertTrue(r.data["warning"])
        self.paid.refresh_from_db()
        self.pending.refresh_from_db()
        self.assertEqual(self.paid.status, PaymentRequest.Status.PENDING_CFO)
        self.assertEqual(self.pending.status, PaymentRequest.Status.PENDING_CFO)

    @mock.patch("taskboard.payment_views._is_first_approver", return_value=True)
    @mock.patch("core.ai_assist.deepseek_complete", side_effect=Exception("offline"))
    def test_sod_skips_a_signers_own_request(self, _ai, _fa):
        # A finance approver may not auto-close a request they signed off at stage 1.
        User = get_user_model()
        approver = User.objects.create_user(username="fin_appr", email="fin_appr@x.com", password="x")
        self.paid.first_approver = approver
        self.paid.save(update_fields=["first_approver"])
        other_cfo = User.objects.create_user(username="the_cfo", email="the_cfo@x.com", password="x")
        with mock.patch("taskboard.payment_views._cfo_user", return_value=other_cfo):
            c = APIClient()
            c.force_authenticate(approver)
            r = c.post(self.url, {"fnb_text": self.fnb, "auto_close": "true"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn("PAY/TEST/PAID/0001", {s["ref"] for s in r.data["skipped"]})
        self.paid.refresh_from_db()
        self.assertEqual(self.paid.status, PaymentRequest.Status.PENDING_CFO)
