"""UniCoin Finance & Debtors Automation — pure business-rule tests.

These cover the genuinely-new logic from the CFO's 7-Sep-2026 handover pack that
does NOT touch the database, so they run as SimpleTestCase (no DB, fast in CI):

  * reporting.unicoin_status          — RealPay InstalmentStatus normalisation
  * reporting.unicoin_perpetual_debit — A2, the perpetual-debit / consent rule
  * reporting.unicoin_bank_codes      — A3, plain-language debit-failure reasons
  * core.unicoin_income_posting       — C2, the income-sheet posting guard

Each test fails if the specific rule from the pack is broken. The DB-bound legs
(the A2 report query, the C2 capture model/view, the scheduled sends and the D1
agent lookup) are integrated and proven on the running system at deploy.
"""
import datetime as _dt

from django.test import SimpleTestCase

from reporting.unicoin_status import (
    normalise_instalment_status, is_collection_failure, is_collection_success,
    SUCCESS_SET,
)
from reporting.unicoin_perpetual_debit import flag_perpetual_debit, PLAN_SIZES
from reporting.unicoin_bank_codes import plain_language, is_mapped
from reporting.unicoin_failed_debit_triage import (
    triage_failed_debit, FINANCE, CALL_CENTRE,
)
from core.unicoin_income_posting import (
    CATEGORIES, validate_category, posting_targets, CategoryError,
)


class InstalmentStatusTests(SimpleTestCase):
    """Pack §4.7: success set is {S, SUCCESS, SUCCESSFUL}; F and E both fail."""

    def test_stray_text_normalises_to_letters(self):
        for raw, exp in [("S", "S"), (" s ", "S"), ("SUCCESS", "S"),
                         ("Successful", "S"), ("failed", "F"), ("FAILED", "F"),
                         ("error", "E"), ("ERROR", "E"), ("processing", "W"),
                         ("cancelled", "I"), ("E", "E"), ("R", "R"),
                         ("", ""), (None, "")]:
            self.assertEqual(normalise_instalment_status(raw), exp, raw)

    def test_unknown_is_never_invented(self):
        self.assertEqual(normalise_instalment_status("xy"), "XY")

    def test_success_set_matches_pack(self):
        self.assertEqual(SUCCESS_SET, frozenset({"S", "SUCCESS", "SUCCESSFUL"}))

    def test_only_F_and_E_are_collection_failures(self):
        # The regression Fable caught: the whole word ERROR must count.
        for raw in ("F", "f", "FAILED", "E", "e", "ERROR", "error"):
            self.assertTrue(is_collection_failure(raw), raw)
        for raw in ("S", "SUCCESS", "SUCCESSFUL", "R", "W", "A", "I", ""):
            self.assertFalse(is_collection_failure(raw), raw)

    def test_is_collection_success(self):
        for raw in ("S", "SUCCESS", "SUCCESSFUL", "s"):
            self.assertTrue(is_collection_success(raw), raw)
        for raw in ("F", "E", "ERROR", "R", ""):
            self.assertFalse(is_collection_success(raw), raw)


class PerpetualDebitTests(SimpleTestCase):
    """Pack Task 4 (A2): a debit past the agreed plan is a consent issue."""

    PLAN_END = _dt.date(2026, 3, 31)
    BEFORE = _dt.date(2026, 3, 1)
    AFTER = _dt.date(2026, 5, 1)

    def test_plan_sizes_from_pack(self):
        self.assertEqual(PLAN_SIZES, {"once_off": 1, "three_month": 3})

    def test_more_collected_than_plan_is_flagged(self):
        r = flag_perpetual_debit(plan_instalments=3, collected_instalments=4,
                                 mandate_active=False, plan_end_date=self.PLAN_END,
                                 today=self.AFTER)
        self.assertTrue(r["flagged"])
        self.assertTrue(r["reason"])

    def test_exact_plan_is_not_flagged(self):
        r = flag_perpetual_debit(plan_instalments=3, collected_instalments=3,
                                 mandate_active=False, plan_end_date=self.PLAN_END,
                                 today=self.AFTER)
        self.assertFalse(r["flagged"])

    def test_once_off_second_instalment_is_flagged(self):
        r = flag_perpetual_debit(plan_instalments=1, collected_instalments=2,
                                 mandate_active=False, plan_end_date=self.PLAN_END,
                                 today=self.AFTER)
        self.assertTrue(r["flagged"])

    def test_active_mandate_past_plan_end_is_flagged(self):
        r = flag_perpetual_debit(plan_instalments=3, collected_instalments=3,
                                 mandate_active=True, plan_end_date=self.PLAN_END,
                                 today=self.AFTER)
        self.assertTrue(r["flagged"])

    def test_active_mandate_before_plan_end_is_not_flagged(self):
        r = flag_perpetual_debit(plan_instalments=3, collected_instalments=2,
                                 mandate_active=True, plan_end_date=self.PLAN_END,
                                 today=self.BEFORE)
        self.assertFalse(r["flagged"])

    def test_on_plan_end_day_is_not_flagged(self):
        r = flag_perpetual_debit(plan_instalments=3, collected_instalments=3,
                                 mandate_active=True, plan_end_date=self.PLAN_END,
                                 today=self.PLAN_END)
        self.assertFalse(r["flagged"])

    def test_unknown_plan_end_does_not_crash(self):
        r = flag_perpetual_debit(plan_instalments=3, collected_instalments=3,
                                 mandate_active=True, plan_end_date=None,
                                 today=self.AFTER)
        self.assertFalse(r["flagged"])


class BankCodeTests(SimpleTestCase):
    """Pack Task 3 (A3): map common reasons, never invent an unknown one."""

    def test_common_reasons_map(self):
        for raw, needle in [("Insufficient Funds", "insufficient funds"),
                            ("no funds", "insufficient funds"),
                            ("Account Closed", "account closed"),
                            ("account frozen", "account frozen"),
                            ("Payment Stopped", "payment stopped"),
                            ("No Account", "no such account"),
                            ("Invalid Account Number", "invalid account"),
                            ("Mandate cancelled", "mandate cancelled")]:
            self.assertIn(needle, plain_language(raw).lower(), raw)
            self.assertTrue(is_mapped(raw), raw)

    def test_multi_reason_reads_most_specific_first(self):
        # Fable's breaking input: must not collapse to "no account".
        self.assertEqual(plain_language("Account closed - no account activity"),
                         "Account closed")

    def test_real_realpay_reasons_all_map(self):
        # The actual distinct instalmentResponse values on the live replica
        # (2026-09-07). Every one must map except the meta "no description" code.
        for raw, needle in [
            ("NO AVAILABLE FUNDS", "insufficient funds"),
            ("Acc does not exist", "no such account"),
            ("Account closed", "account closed"),
            ("ACCOUNT FROZEN", "account frozen"),
            ("Acc failed validation", "failed validation"),
            ("INCORRECT ACCOUNT DETAIL", "invalid account"),
            ("Additional amnt not allowed", "additional amount"),
            ("PAYMENT STOPPED", "payment stopped"),
            ("homing/sub acc no combination invalid", "invalid account"),
            ("Invalid transaction ref no", "invalid transaction"),
        ]:
            self.assertTrue(is_mapped(raw), raw)
            self.assertIn(needle, plain_language(raw).lower(), raw)

    def test_meta_code_with_no_description_stays_verbatim(self):
        raw = "No Code Description Found"
        self.assertEqual(plain_language(raw), raw)
        self.assertFalse(is_mapped(raw))

    def test_unknown_returned_verbatim_and_unmapped(self):
        weird = "ERR-9987 contact originator"
        self.assertEqual(plain_language(weird), weird)
        self.assertFalse(is_mapped(weird))

    def test_blank_is_safe(self):
        self.assertTrue(plain_language("").lower().startswith("no reason"))
        self.assertTrue(plain_language(None).lower().startswith("no reason"))
        self.assertFalse(is_mapped(""))


class IncomePostingGuardTests(SimpleTestCase):
    """Pack Task 5 (C2): a fixed dropdown drives posting, enforced server-side."""

    def test_categories_are_fixed(self):
        self.assertEqual(CATEGORIES, frozenset({"premium", "operational",
                                                "refund", "other"}))

    def test_free_text_is_refused(self):
        with self.assertRaises(CategoryError):
            validate_category("some made up value")
        with self.assertRaises(CategoryError):
            validate_category("")

    def test_premium_posts_to_both(self):
        self.assertEqual(posting_targets("premium", policy_issued=True),
                         {"graphite": True, "ledger": True, "draft": False})

    def test_non_premium_posts_to_ledger_only(self):
        self.assertEqual(posting_targets("operational", policy_issued=True),
                         {"graphite": False, "ledger": True, "draft": False})
        self.assertEqual(posting_targets("refund", policy_issued=True),
                         {"graphite": False, "ledger": True, "draft": False})

    def test_operational_on_unissued_policy_still_posts_to_ledger(self):
        # Fable bug 3: operational income has no policy, so it must NOT be held.
        self.assertEqual(posting_targets("operational", policy_issued=False),
                         {"graphite": False, "ledger": True, "draft": False})

    def test_premium_and_refund_on_unissued_policy_hold_as_draft(self):
        self.assertEqual(posting_targets("refund", policy_issued=False),
                         {"graphite": False, "ledger": False, "draft": True})
        self.assertTrue(posting_targets("premium", policy_issued=False)["draft"])

    def test_non_bool_policy_issued_is_refused(self):
        # Fable bug 2: a DRF view passes strings; "false" must not be truthy.
        for bad in ("false", "0", "", 1, None):
            with self.assertRaises(CategoryError):
                posting_targets("premium", policy_issued=bad)


class FailedDebitTriageTests(SimpleTestCase):
    """Pack Task 3 (A3): plain reason + split into finance / call-centre lists."""

    def test_fresh_failure_goes_to_finance(self):
        t = triage_failed_debit(status="F", retry_count=0, response="Insufficient Funds")
        self.assertEqual(t["list_bucket"], FINANCE)
        self.assertIn("insufficient funds", t["reason_plain"].lower())
        self.assertTrue(t["reason_mapped"])

    def test_retried_failure_goes_to_call_centre(self):
        t = triage_failed_debit(status="F", retry_count=2, response="Account closed")
        self.assertEqual(t["list_bucket"], CALL_CENTRE)

    def test_unknown_reason_verbatim_and_unmapped(self):
        t = triage_failed_debit(status="E", retry_count=1, response="ZZ-42 weird")
        self.assertEqual(t["reason_plain"], "ZZ-42 weird")
        self.assertFalse(t["reason_mapped"])

    def test_blank_reason_is_safe(self):
        t = triage_failed_debit(status="F", retry_count=0, response=None)
        self.assertTrue(t["reason_plain"].lower().startswith("no reason"))
        self.assertFalse(t["reason_mapped"])

    def test_bad_retry_count_does_not_crash(self):
        t = triage_failed_debit(status="F", retry_count=None, response="no funds")
        self.assertEqual(t["list_bucket"], FINANCE)


class ScheduledSendTests(SimpleTestCase):
    """Pack Task 2: the email carries summary numbers + a link, never a customer list."""

    def test_summary_html_renders_numbers_not_strings(self):
        from reporting.management.commands.send_finance_monitoring import _summary_rows_html
        html = _summary_rows_html({"failed_debits": 741, "total_amount": 272700.0,
                                   "leaky": "MP-999 customer name"})
        self.assertIn("741", html)
        self.assertIn("Failed debits", html)
        # A non-numeric summary value is skipped — never rendered into the email.
        self.assertNotIn("MP-999", html)

    def test_dead_replica_email_is_error_not_all_clear(self):
        from reporting.management.commands.send_finance_monitoring import _email_html
        body = _email_html(title="X", cadence_word="this week", summary_html="",
                           link="https://omni.example/x", unavailable=True)
        self.assertIn("could not run", body.lower())
        self.assertNotIn("all clear", body.lower())

    def test_dry_run_never_prints_a_customer_row(self):
        # The real guard: even if a builder returns rows with policy numbers,
        # the scheduled email must render only the summary.
        from io import StringIO
        from unittest import mock
        from django.core.management import call_command
        import reporting.management.commands.send_finance_monitoring as cmd

        def stub_builder():
            return {"rows": [{"policy_number": "SENTINEL-POL-1"}],
                    "summary": {"count": 3}, "meta": {}}

        stub = {"x": {"title": "Stub", "owner": "o@example.com", "builder": stub_builder}}
        out = StringIO()
        with mock.patch.dict(cmd.REPORTS, stub, clear=True):
            call_command("send_finance_monitoring", "--slug", "x", "--dry-run", stdout=out)
        text = out.getvalue()
        self.assertIn("count", text.lower())
        self.assertNotIn("SENTINEL-POL-1", text)

    def test_dead_replica_dry_run_says_could_not_run_and_exits_nonzero(self):
        from io import StringIO
        from unittest import mock
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from reporting.finance_monitoring import ReplicaUnavailable
        import reporting.management.commands.send_finance_monitoring as cmd

        def dead_builder():
            raise ReplicaUnavailable("replica down")

        stub = {"x": {"title": "Stub", "owner": "o@example.com", "builder": dead_builder}}
        out = StringIO()
        with mock.patch.dict(cmd.REPORTS, stub, clear=True):
            with self.assertRaises(CommandError):
                call_command("send_finance_monitoring", "--slug", "x", "--dry-run", stdout=out)
        self.assertIn("could not run", out.getvalue().lower())

    def test_scheduled_build_uses_a_cadence_sized_window(self):
        # Fable's catch: a weekly email must NOT be built on the 1-day default.
        import datetime as _dt
        from reporting.management.commands.send_finance_monitoring import _call_builder

        seen = {}

        def windowed(*, date_from=None, date_to=None, limit=1000):
            seen["date_from"] = date_from
            return {"rows": [], "summary": {}, "meta": {}}

        _call_builder(windowed, "weekly")
        self.assertEqual(seen["date_from"], _dt.date.today() - _dt.timedelta(days=7))
