"""
Tests for /api/v1/health/dashboard/ (healthcare/dashboard_views.py).

The month figures below are the REAL production upload totals as at
8-Sep-2026, and every expected answer is the figure the Health team asked the
dashboard to show. That makes this a reconciliation test, not a smoke test: if
the VAT direction, the financial-year boundary, the superseded filter or the
loss-ratio denominator drifts, one of these numbers moves and the test fails.

No member data anywhere — only month totals and employer-group counts.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from healthcare.models import HealthcareUpload, HealthQuote

# (year, month, gross_amount) — gross_amount on a revenue upload is INCL VAT.
REVENUE = [
    (2025, 10, "8516.94"),
    (2025, 11, "8516.94"),
    (2025, 12, "18282.18"),
    (2026, 1, "18282.18"),
    (2026, 2, "18987.84"),
    (2026, 3, "16203.96"),
    (2026, 4, "16203.96"),
    (2026, 5, "17918.52"),
    (2026, 6, "28724.58"),
    (2026, 7, "59555.88"),
    (2026, 8, "57753.54"),
]

# (year, month, paid_amount) — several AFT remits land in one month.
CLAIMS = [
    (2026, 2, "3424.00"),
    (2026, 2, "6354.00"),
    (2026, 3, "1418.20"),
    (2026, 3, "8320.09"),
    (2026, 3, "9874.00"),
    (2026, 3, "3310.00"),
    (2026, 4, "75.90"),
    (2026, 4, "1240.63"),
    (2026, 4, "8002.82"),
    (2026, 5, "1180.18"),
    (2026, 5, "5812.98"),
    (2026, 5, "3784.31"),
    (2026, 5, "6177.19"),
    (2026, 5, "405.12"),
    (2026, 6, "210.61"),
    (2026, 6, "1464.86"),
    (2026, 6, "952.77"),
    (2026, 6, "885.01"),
    (2026, 7, "1010.81"),
    (2026, 7, "423.38"),
    (2026, 7, "270.59"),
    (2026, 8, "1140.86"),
    (2026, 8, "607.70"),
    (2026, 8, "347.89"),
    (2026, 8, "767.60"),
    (2026, 8, "4404.63"),
]

URL = "/api/v1/health/dashboard/"


class HealthDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("medu", "medu@example.test", "x")
        K = HealthcareUpload.Kind
        S = HealthcareUpload.Status
        for y, m, gross in REVENUE:
            HealthcareUpload.objects.create(
                kind=K.REVENUE,
                status=S.PARSED,
                file_name=f"gwp-{y}-{m}.xlsx",
                period_year=y,
                period_month=m,
                gross_amount=Decimal(gross),
                paid_amount=Decimal("0"),
                total_rows=1,
                total_lives_count=1,
            )
        for y, m, paid in CLAIMS:
            HealthcareUpload.objects.create(
                kind=K.CLAIMS,
                status=S.PARSED,
                file_name=f"aft-{y}-{m}-{paid}.xlsx",
                period_year=y,
                period_month=m,
                gross_amount=Decimal(paid),
                paid_amount=Decimal(paid),
                total_rows=1,
                total_lives_count=1,
            )
        # A treaty upload in the same month as a revenue one: it must never be
        # counted as premium.
        HealthcareUpload.objects.create(
            kind=K.TREATY,
            status=S.PARSED,
            file_name="treaty-2025-10.xlsx",
            period_year=2025,
            period_month=10,
            gross_amount=Decimal("9126.32"),
            paid_amount=Decimal("0"),
            total_rows=1,
            total_lives_count=0,
        )
        # A superseded correction and a failed parse: both must be ignored.
        HealthcareUpload.objects.create(
            kind=K.REVENUE,
            status=S.PARSED,
            file_name="gwp-2026-8-old.xlsx",
            period_year=2026,
            period_month=8,
            gross_amount=Decimal("99999.99"),
            paid_amount=Decimal("0"),
            superseded=True,
            total_rows=1,
            total_lives_count=1,
        )
        HealthcareUpload.objects.create(
            kind=K.CLAIMS,
            status=S.FAILED,
            file_name="aft-broken.xlsx",
            period_year=2026,
            period_month=8,
            gross_amount=Decimal("88888.88"),
            paid_amount=Decimal("88888.88"),
            total_rows=0,
            total_lives_count=0,
        )
        for i in range(3):
            HealthQuote.objects.create(
                ref=f"D{i}", client_name="C", status=HealthQuote.Status.DRAFT
            )
        HealthQuote.objects.create(
            ref="S1", client_name="C", status=HealthQuote.Status.SUBMITTED
        )
        HealthQuote.objects.create(
            ref="A1", client_name="C", status=HealthQuote.Status.APPROVED
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def get(self, asof="2026-08-31"):
        r = self.client.get(URL, {"asof": asof})
        self.assertEqual(r.status_code, 200, r.content[:400])
        return r.json()

    # ---- the figures the Health team asked for -------------------------

    def test_fy_window_is_july_to_june_named_by_the_closing_year(self):
        d = self.get()
        self.assertEqual(
            d["fy"], {"label": "FY27", "start": "2026-07-01", "end": "2027-06-30"}
        )

    def test_gwp_year_to_date_is_the_financial_year_not_inception_to_date(self):
        d = self.get()
        # Jul + Aug 2026 only.
        self.assertEqual(d["kpi"]["gwpYtdIncl"], 117309.42)
        self.assertEqual(d["kpi"]["gwpYtdExcl"], 102903.00)
        # Inception-to-date is far larger and is returned only under its own key.
        self.assertEqual(d["itd"]["gwpIncl"], 268946.52)
        self.assertNotEqual(d["itd"]["gwpIncl"], d["kpi"]["gwpYtdIncl"])

    def test_vat_is_stripped_not_added(self):
        d = self.get()
        aug = [m for m in d["monthly"] if m["month"] == "2026-08"][0]
        self.assertEqual(aug["gwpIncl"], 57753.54)
        self.assertEqual(aug["gwpExcl"], 50661.00)  # incl / 1.14
        self.assertEqual(d["kpi"]["gwpMonthIncl"], 57753.54)
        self.assertEqual(d["kpi"]["gwpMonthExcl"], 50661.00)

    def test_month_on_month_and_annualised(self):
        d = self.get()
        self.assertEqual(d["kpi"]["gwpMonMoMPct"], -3.03)
        self.assertEqual(d["kpi"]["annualisedIncl"], 693042.48)
        self.assertEqual(d["kpi"]["objective"], 16000000.00)
        self.assertEqual(d["kpi"]["livesTarget"], 7500)

    def test_headline_loss_ratio_is_year_to_date(self):
        d = self.get()
        # FY27 claims 8,973.46 / FY27 premium excl VAT 102,903.00.
        self.assertEqual(d["kpi"]["lossRatioYtdPct"], 8.72)
        self.assertEqual(d["kpi"]["lossRatioMonthPct"], 14.35)
        self.assertNotEqual(d["kpi"]["lossRatioYtdPct"], d["itd"]["lossRatioPct"])

    def test_every_monthly_loss_ratio_is_claims_over_gwp_excl(self):
        d = self.get()
        for m in d["monthly"]:
            if m["claims"] is None or not m["gwpExcl"]:
                self.assertIsNone(m["lossRatioPct"])
                continue
            expected = round(m["claims"] / m["gwpExcl"] * 100, 2)
            self.assertAlmostEqual(
                m["lossRatioPct"], expected, places=2, msg=m["month"]
            )

    def test_march_is_the_over_breakeven_month(self):
        d = self.get()
        mar = [m for m in d["monthly"] if m["month"] == "2026-03"][0]
        self.assertEqual(mar["claims"], 22922.29)
        self.assertEqual(mar["lossRatioPct"], 161.27)

    def test_months_with_no_claim_run_are_null_never_zero(self):
        d = self.get()
        for key in ("2025-10", "2025-11", "2025-12", "2026-01"):
            row = [m for m in d["monthly"] if m["month"] == key][0]
            self.assertIsNone(row["claims"], key)
            self.assertIsNone(row["lossRatioPct"], key)

    def test_prior_year_totals(self):
        d = self.get()
        fy26 = [f for f in d["fyTotals"] if f["label"] == "FY26"][0]
        self.assertEqual(fy26["gwpIncl"], 151637.10)
        self.assertEqual(fy26["gwpExcl"], 133015.00)
        self.assertEqual(fy26["claims"], 62892.67)
        self.assertFalse(fy26["isCurrent"])
        fy27 = [f for f in d["fyTotals"] if f["label"] == "FY27"][0]
        self.assertTrue(fy27["isCurrent"])

    def test_kpi_band_reconciles_to_the_monthly_rows(self):
        """The acceptance test in the request: YTD incl == sum of FY27 months."""
        d = self.get()
        fy27 = [m for m in d["monthly"] if m["fy"] == 2027]
        self.assertEqual([m["month"] for m in fy27], ["2026-07", "2026-08"])
        self.assertEqual(
            round(sum(m["gwpExcl"] for m in fy27), 2), d["kpi"]["gwpYtdExcl"]
        )
        self.assertEqual(
            round(sum(m["gwpIncl"] for m in fy27), 2), d["kpi"]["gwpYtdIncl"]
        )

    # ---- exclusions ----------------------------------------------------

    def test_treaty_upload_is_not_counted_as_premium(self):
        d = self.get()
        oct25 = [m for m in d["monthly"] if m["month"] == "2025-10"][0]
        self.assertEqual(oct25["gwpIncl"], 8516.94)  # not 17,643.26

    def test_superseded_and_failed_uploads_are_excluded(self):
        d = self.get()
        aug = [m for m in d["monthly"] if m["month"] == "2026-08"][0]
        self.assertEqual(aug["gwpIncl"], 57753.54)  # the 99,999.99 correction is out
        self.assertEqual(aug["claims"], 7268.68)  # the failed 88,888.88 is out

    # ---- as-of ---------------------------------------------------------

    def test_asof_rewinds_the_whole_page(self):
        d = self.get("2026-07-31")
        self.assertEqual(d["asOf"], "2026-07-31")
        self.assertEqual(d["kpi"]["gwpMonthIncl"], 59555.88)
        self.assertEqual(d["kpi"]["gwpYtdIncl"], 59555.88)
        self.assertFalse([m for m in d["monthly"] if m["month"] > "2026-07"])

    def test_asof_before_any_data_returns_empty_not_an_error(self):
        d = self.get("2025-01-31")
        self.assertEqual(d["monthly"], [])
        self.assertEqual(d["kpi"]["gwpYtdIncl"], 0.0)
        self.assertIsNone(d["kpi"]["gwpMonMoMPct"])

    def test_bad_asof_falls_back_to_today_rather_than_500ing(self):
        from django.utils import timezone

        r = self.client.get(URL, {"asof": "not-a-date"})
        self.assertEqual(r.status_code, 200)
        # The fallback is not silent — the date actually used is echoed back.
        self.assertEqual(r.json()["asOf"], timezone.localdate().isoformat())

    # ---- pipeline, gap, honesty ---------------------------------------

    def test_pipeline_counts_are_live_quote_statuses(self):
        d = self.get()
        self.assertEqual(d["pipeline"]["draft"], 3)
        self.assertEqual(d["pipeline"]["inReview"], 1)
        self.assertEqual(d["pipeline"]["approved"], 1)
        self.assertEqual(d["pipeline"]["invoiced"], 0)

    def test_register_unavailable_degrades_instead_of_breaking_the_page(self):
        """No Graphite replica in the test environment: lives read null, the
        rest of the dashboard still answers 200."""
        d = self.get()
        self.assertIsNone(d["kpi"]["activeLives"])
        self.assertFalse(d["cancellations"]["available"])
        self.assertEqual(d["cancellations"]["byGroup"], [])
        self.assertEqual(d["kpi"]["gwpYtdIncl"], 117309.42)

    def test_cancellation_reasons_are_declared_unavailable_not_invented(self):
        d = self.get()
        self.assertFalse(d["cancellations"]["reasonsAvailable"])
        self.assertEqual(d["cancellations"]["reasons"], [])
        self.assertIn("no cancellation reason", d["cancellations"]["note"])

    def test_the_register_versus_on_cover_book_gap_is_stated(self):
        d = self.get()
        self.assertIn("quoteBookLives", d["gap"])
        self.assertIn("registerLives", d["gap"])
        self.assertFalse(d["gap"]["reconciled"])

    def test_no_personal_data_is_returned(self):
        body = self.client.get(URL, {"asof": "2026-08-31"}).content.decode().lower()
        for banned in (
            "omang",
            "id_number",
            "idnumber",
            "account_number",
            "accountnumber",
            "passport",
            "dob",
            "date_of_birth",
            "first_name",
            "surname",
            "cellphone",
        ):
            self.assertNotIn(banned, body, banned)

    # ---- the register fences (added after the fabe panel flagged them) ----

    @staticmethod
    def _fake_replica(rows, lag=0):
        """A stand-in replica connection that really answers with `rows`."""

        class _Cur:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, *a): pass
            def fetchall(self): return rows
            def fetchone(self): return {"Seconds_Behind_Master": lag}

        class _Con:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def cursor(self): return _Cur()

        return _Con()

    def test_an_empty_register_read_is_unavailable_not_zero_lives(self):
        """A replica answering with NO ROWS is a failed read, not an empty book.

        This drives the empty-rows fence itself, not the except path.
        """
        from unittest.mock import patch
        from healthcare import register_counts as rc
        with patch.object(rc, "is_configured", return_value=True), \
             patch.object(rc, "_connect_fast", return_value=self._fake_replica([])):
            r = rc.register_counts()
        self.assertFalse(r["available"])
        self.assertEqual(r["reason"], "register read came back empty")

    def test_a_dead_replica_is_unavailable_too(self):
        """The except path, tested separately from the empty-rows fence."""
        from unittest.mock import patch
        import pymysql
        from healthcare import register_counts as rc
        with patch.object(rc, "is_configured", return_value=True), \
             patch.object(rc, "_connect_fast",
                          side_effect=pymysql.err.OperationalError(2003, "down")):
            r = rc.register_counts()
        self.assertFalse(r["available"])
        self.assertEqual(r["reason"], "replica unreachable")

    def test_a_sql_bug_is_not_reported_as_replica_unreachable(self):
        """H76: a programming error must not hide behind an infrastructure excuse."""
        import pymysql
        from healthcare import register_counts as rc
        self.assertEqual(rc._classify(pymysql.err.OperationalError(2003, "down")),
                         "replica unreachable")
        self.assertEqual(rc._classify(pymysql.err.ProgrammingError(1064, "syntax")),
                         "internal error reading the register")
        self.assertEqual(rc._classify(ValueError("our own bug")),
                         "internal error reading the register")

    def test_a_badly_lagged_register_is_refused(self):
        """H25: yesterday's membership must not be printed as today's."""
        from unittest.mock import patch
        from healthcare import register_counts as rc

        rows = [{"employer_group_id": 1, "group_name": "G", "policy_status": 0, "lives": 5}]

        with patch.object(rc, "is_configured", return_value=True), \
             patch.object(rc, "_connect_fast", return_value=self._fake_replica(rows, lag=3600)):
            r = rc.register_counts()
        self.assertFalse(r["available"])
        self.assertIn("behind", r["reason"])
        self.assertEqual(r["replica_lag_seconds"], 3600)

        with patch.object(rc, "is_configured", return_value=True), \
             patch.object(rc, "_connect_fast", return_value=self._fake_replica(rows, lag=5)):
            ok = rc.register_counts()
        self.assertTrue(ok["available"])
        self.assertEqual(ok["policyholders"], 5)

    def test_sign_in_is_required(self):
        anon = APIClient()
        self.assertIn(anon.get(URL).status_code, (401, 403))


class RegisterTimeoutTests(TestCase):
    """The register read must not hold a screen hostage.

    Found by rendering the page: with the replica unreachable the endpoint took
    20.1s and the dashboard sat on "Loading…", because the load-file engine's
    connection allows 20s to connect. A dashboard gets seconds.
    """

    def test_the_register_connects_on_dashboard_timeouts_not_load_file_ones(self):
        from unittest.mock import patch
        from healthcare import afa_members as am
        from healthcare import register_counts as rc

        self.assertLessEqual(rc.CONNECT_TIMEOUT_SECONDS, 5)
        self.assertLessEqual(rc.READ_TIMEOUT_SECONDS, 10)

        captured = {}

        class _FakePyMySQL:
            class cursors:
                DictCursor = object

            @staticmethod
            def connect(**kwargs):
                captured.update(kwargs)
                return object()

        cfg = {
            "host": "replica.invalid", "port": 3306, "user": "u",
            "password": "p", "database": "d", "timeout": 20,
        }
        with patch.object(rc, "_config", return_value=cfg), \
             patch.dict("sys.modules", {"pymysql": _FakePyMySQL,
                                        "pymysql.cursors": _FakePyMySQL.cursors}):
            rc._connect_fast()

        self.assertEqual(captured["connect_timeout"], rc.CONNECT_TIMEOUT_SECONDS)
        self.assertEqual(captured["read_timeout"], rc.READ_TIMEOUT_SECONDS)
        # The load-file reader keeps its own, longer, ceiling — untouched.
        self.assertGreaterEqual(
            int(getattr(am, "settings").GRAPHITE_RO_DB_TIMEOUT
                if hasattr(getattr(am, "settings"), "GRAPHITE_RO_DB_TIMEOUT") else 20),
            rc.CONNECT_TIMEOUT_SECONDS,
        )
