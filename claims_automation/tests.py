"""claims_automation — the Omni half of the end-to-end claims pipeline.

Each test proves one promise from the CFO's plan of 19-Sep-2026:
  * only a key carrying `claims-events` may push, and a redelivery is harmless
  * a police-report breach drafts a repudiation for the Claims MANAGER only,
    and nothing reaches the client until a person approves AND the wording is
    signed off
  * a total loss drafts the Agreement of Loss with the right arithmetic
  * an itemised assessment goes through the SAME claims-PO engine, once
  * the insight Graphite reads back carries no customer identity
"""

from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings

from core.models import ApiKey, Company, OmniTask, UserProfile
from procurement.claims_models import ClaimsAssessment

from .models import (ClaimAutomationEvent, ClaimCase, ClaimLetter, SalvageHandover,
                     VeritasSalvageCharge)

EVENTS = "/api/v1/claims-automation/graphite/events/"
INSIGHT = "/api/v1/claims-automation/graphite/insight/"


def facts(**over):
    base = {
        "claim": {
            "number": "G2026000777",
            "type": "Accident",
            "date_of_loss": "2026-09-10",
            "reported_on": "2026-09-11",
            "description": "Hit a kudu on the A1",
        },
        "insured": {
            "name": "Mpho Testperson",
            "email": "client@example.com",
            "phone": "phone-on-file",
        },
        "policy": {
            "number": "DOMG2026123456",
            "product": "Motor Comprehensive",
            "inception": "2026-01-01",
            "expiry": "2026-12-31",
            "sum_insured": "120000",
            "excess": "5000",
        },
        "vehicle": {"registration": "B777AAA", "make": "TOYOTA", "model": "COROLLA"},
        "premium": {
            "light": "green",
            "status": "Paid up",
            "balance": 0,
            "settled": True,
        },
    }
    for k, v in over.items():
        base[k] = v
    return base


def event(etype, key, **extra):
    return {
        "event_type": etype,
        "idempotency_key": key,
        "claim_ref": "g2026000777",
        "graphite_id": 777,
        "is_motor": True,
        "handler_email": "handler@example.com",
        "facts": facts(),
    } | extra


@patch(
    "claims_automation.ai_reader.read_claim",
    return_value=("A clean motor claim.", "Book the assessor.", "test"),
)
class ClaimsAutomationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root = User.objects.create_superuser("root", "root@x.co", "x")
        Company.objects.create(code="ADIC", name="Alpha Direct Insurance Company")
        cls.manager = User.objects.create_user("wangu", "wangu@example.com", "x")
        UserProfile.objects.create(
            user=cls.manager, title=UserProfile.Title.CLAIMS_MANAGER, is_active=True
        )
        cls.senior = User.objects.create_user("senior", "senior@example.com", "x")
        UserProfile.objects.create(
            user=cls.senior,
            title=UserProfile.Title.SENIOR_CLAIMS_ASSOCIATE,
            is_active=True,
        )
        cls.handler = User.objects.create_user(
            "handler", "handler@example.com", "x"
        )
        UserProfile.objects.create(
            user=cls.handler,
            title=UserProfile.Title.JUNIOR_CLAIMS_ASSOCIATE,
            is_active=True,
        )
        cls.outsider = User.objects.create_user("outsider", "o@example.com", "x")
        # The salvage yard: Veritas (VCM) staff, e.g. Moses Ncube / Tshephang Motswagae.
        cls.yard = User.objects.create_user("yard", "yard@example.com", "x")
        vcm = Company.objects.create(code="VCM", name="Veritas Capital Management")
        try:
            from payroll.models import Employee
            Employee.objects.create(user=cls.yard, full_name="Yard Person", company=vcm)
        except Exception:  # noqa: BLE001 — salvage permission falls back to claims titles
            pass
        cls.key = cls._key(["claims-events"], "clmev")
        cls.po_key = cls._key(["po-claim-read"], "poread")

    @classmethod
    def _key(cls, scopes, prefix):
        plaintext = prefix + "0" * (64 - len(prefix))
        ApiKey.objects.create(
            label=f"test {prefix}",
            key_prefix=plaintext[:12],
            key_hash=make_password(plaintext),
            service_user=cls.root,
            allowed_scopes=scopes,
            is_active=True,
        )
        return plaintext

    def push(self, body, key=None):
        return self.client.post(
            EVENTS,
            body,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"ApiKey {key or self.key}",
        )

    # ── the door ───────────────────────────────────────────────────────────
    def test_wrong_scope_is_refused(self, _ai):
        r = self.push(event("claim_registered", "k0"), key=self.po_key)
        self.assertIn(r.status_code, (401, 403))
        self.assertFalse(ClaimCase.objects.exists())

    def test_signed_in_human_cannot_push(self, _ai):
        self.client.force_login(self.manager)
        r = self.client.post(
            EVENTS, event("claim_registered", "k0h"), content_type="application/json"
        )
        self.assertIn(r.status_code, (401, 403))

    def test_register_then_redeliver_is_idempotent(self, _ai):
        self.assertEqual(self.push(event("claim_registered", "k1")).status_code, 201)
        r = self.push(event("claim_registered", "k1"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(ClaimAutomationEvent.objects.count(), 1)
        case = ClaimCase.objects.get()
        self.assertEqual(case.claim_ref, "G2026000777")
        self.assertEqual(case.premium_light, "green")

    def test_bad_payload_is_400(self, _ai):
        self.assertEqual(
            self.push(
                {"event_type": "nope", "idempotency_key": "x", "claim_ref": "A"}
            ).status_code,
            400,
        )

    # ── the customer form ──────────────────────────────────────────────────
    def test_clean_form_is_straight_through_and_tells_the_handler(self, _ai):
        self.push(
            event(
                "claim_form_submitted",
                "k2",
                answers={"driver_licence": "L1", "driver_permission": "yes"},
            )
        )
        case = ClaimCase.objects.get()
        self.assertEqual(case.triage, "straight_through")
        self.assertEqual(case.ai_next_step, "Book the assessor.")
        self.assertFalse(ClaimLetter.objects.exists())
        self.assertTrue(OmniTask.objects.filter(assignee=self.handler).exists())

    def test_drunk_driver_drafts_repudiation_for_the_manager_only(self, _ai):
        self.push(
            event(
                "claim_form_submitted",
                "k3",
                answers={"driver_licence": "L1"},
                documents_text="Police report: the driver was under the influence of alcohol.",
            )
        )
        case = ClaimCase.objects.get()
        self.assertEqual(case.triage, "exception")
        letter = ClaimLetter.objects.get()
        self.assertEqual((letter.kind, letter.status), ("repudiation", "awaiting"))
        self.assertTrue(OmniTask.objects.filter(assignee=self.manager).exists())
        # Nothing went to the client.
        self.assertFalse(any("client@example.com" in m.to for m in mail.outbox))
        # A senior associate may NOT approve a repudiation; the manager may.
        self.client.force_login(self.senior)
        self.assertEqual(
            self.client.post(
                f"/api/v1/claims-automation/letters/{letter.id}/approve/"
            ).status_code,
            403,
        )
        self.client.force_login(self.manager)
        r = self.client.post(f"/api/v1/claims-automation/letters/{letter.id}/approve/")
        self.assertEqual(r.status_code, 200)
        letter.refresh_from_db()
        # Wording not signed off -> approved but NOT sent.
        self.assertEqual(letter.status, "approved")
        self.assertIn("Not sent", letter.decision_note)
        self.assertFalse(any("client@example.com" in m.to for m in mail.outbox))

    def test_police_report_upload_is_read_locally_and_flags_drink(self, _ai):
        docs = [
            {
                "name": "police.pdf",
                "mime": "application/pdf",
                "url": "https://alphadirect.s3.ap-south-1.amazonaws.com/MIS/1/Documents/x.pdf?sig=1",
            },
            {"name": "evil.pdf", "url": "https://attacker.example.com/x.pdf"},
        ]

        class _R:
            def __init__(self, b):
                self.b = b

            def read(self, n=-1):
                return self.b

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _P:
            text = "Officer notes: driver failed the breathalyser, under the influence of alcohol."

        with (
            patch("claims_automation.processor._NO_REDIRECT.open", return_value=_R(b"%PDF-fake")) as op,
            patch("core.doc_parse.cascade.parse", return_value=_P()),
        ):
            self.push(
                event(
                    "claim_form_submitted",
                    "k3b",
                    answers={"driver_licence": "L1"},
                    documents=docs,
                )
            )
        self.assertEqual(
            op.call_count, 1
        )  # the non-allow-listed host was never fetched
        self.assertEqual(ClaimLetter.objects.get().kind, "repudiation")

    @override_settings(CLAIMS_LETTER_WORDING_APPROVED=True)
    def test_signed_off_wording_sends_the_letter_with_pdf_and_no_exco_copy(self, _ai):
        self.push(
            event("claim_form_submitted", "k4", answers={"driver_licence": "n/a"})
        )
        letter = ClaimLetter.objects.get()
        self.client.force_login(self.manager)
        self.client.post(f"/api/v1/claims-automation/letters/{letter.id}/approve/")
        letter.refresh_from_db()
        self.assertEqual(letter.status, "sent")
        sent = [m for m in mail.outbox if "client@example.com" in m.to]
        self.assertEqual(len(sent), 1)
        self.assertTrue(
            sent[0].attachments and sent[0].attachments[0][0].endswith(".pdf")
        )
        from django.conf import settings as dj
        from core.notifications import _DEFAULT_CFO_CC
        cfo_cc = str(getattr(dj, "MANDATORY_CFO_CC", _DEFAULT_CFO_CC)).lower()
        self.assertTrue(cfo_cc)
        self.assertNotIn(cfo_cc, [a.lower() for a in sent[0].cc])

    def test_decline_needs_a_reason_and_sends_nothing(self, _ai):
        self.push(
            event("claim_form_submitted", "k5", answers={"driver_permission": "no"})
        )
        letter = ClaimLetter.objects.get()
        self.client.force_login(self.manager)
        url = f"/api/v1/claims-automation/letters/{letter.id}/decline/"
        self.assertEqual(
            self.client.post(url, {}, content_type="application/json").status_code, 400
        )
        self.assertEqual(
            self.client.post(
                url, {"note": "Driver was the spouse"}, content_type="application/json"
            ).status_code,
            200,
        )
        letter.refresh_from_db()
        self.assertEqual(letter.status, "declined")

    # ── the assessment ─────────────────────────────────────────────────────
    def test_total_loss_drafts_aol_with_the_right_figures(self, _ai):
        f = facts(premium={"light": "amber", "balance": "1500.50", "settled": False})
        self.push(
            event(
                "assessment_received",
                "k6",
                facts=f,
                assessment={"assessmentId": "ML-1", "totalLoss": 1, "finalCost": 0},
            )
        )
        letter = ClaimLetter.objects.get()
        self.assertEqual(letter.kind, "aol")
        self.assertEqual(letter.figures["net"], "113499.50")  # 120000 - 5000 - 1500.50
        self.assertEqual(ClaimCase.objects.get().stage, "aol_pending")
        # A second write-off signal does not draft a second AoL.
        self.push(event("write_off_flagged", "k7"))
        self.assertEqual(ClaimLetter.objects.count(), 1)
        # A senior associate may authorise an AoL — but only once Veritas has
        # confirmed they hold the vehicle (CFO 19-Sep-2026).
        self.client.force_login(self.senior)
        url = f"/api/v1/claims-automation/letters/{letter.id}/approve/"
        self.assertEqual(self.client.post(url).status_code, 409)
        self.client.force_login(self.yard)
        self._tick()
        self.client.force_login(self.senior)
        self.assertEqual(self.client.post(url).status_code, 200)

    def test_itemised_assessment_drafts_through_the_claims_po_engine_once(self, _ai):
        a = {
            "assessmentId": "ML-2",
            "totalLoss": 0,
            "repairer": "Rolling Wheels",
            "summary": {"Excess": 5000.0},
            "lines": [
                {
                    "type": "part",
                    "code": "P1",
                    "description": "Bumper",
                    "supplier": "CFAO",
                    "qty": 1,
                    "unitPrice": 1000,
                    "total": 1000,
                },
                {
                    "type": "labour",
                    "code": "L1",
                    "description": "Fit",
                    "qty": 2,
                    "unitPrice": 300,
                    "total": 600,
                },
            ],
        }
        self.push(event("assessment_received", "k8", assessment=a))
        ca = ClaimsAssessment.objects.get()
        self.assertEqual(ca.claim_number, "G2026000777")
        self.assertEqual(
            [g["kind"] for g in ca.report_json["groups"]], ["parts", "labour"]
        )
        self.assertEqual(ClaimCase.objects.get().po_assessment_id, ca.id)
        self.push(event("assessment_received", "k9", assessment=a))
        self.assertEqual(ClaimsAssessment.objects.count(), 1)

    def test_assessment_without_lines_asks_for_the_pdf(self, _ai):
        self.push(
            event(
                "assessment_received",
                "k10",
                assessment={"assessmentId": "ML-3", "finalCost": 9000},
            )
        )
        self.assertFalse(ClaimsAssessment.objects.exists())
        self.assertTrue(
            OmniTask.objects.filter(title__icontains="Assessment received").exists()
        )

    def test_po_drafting_failure_is_reported_as_needs_you_not_success(self, _ai):
        a = {"assessmentId": "ML-X", "totalLoss": 0, "repairer": "R",
             "lines": [{"type": "part", "code": "P", "description": "D", "supplier": "S",
                        "qty": 1, "unitPrice": 10, "total": 10}]}
        with patch("procurement.claims_api.ClaimsAssessmentViewSet.create_pos", side_effect=RuntimeError("boom")):
            self.push(event("assessment_received", "k20", assessment=a))
        self.assertNotEqual(ClaimCase.objects.get().stage, "po_drafted")
        self.assertTrue(OmniTask.objects.filter(title__icontains="NOT drafted").exists())

    def test_unreadable_document_sends_the_claim_to_a_person(self, _ai):
        docs = [{"name": "police.pdf", "url": "https://x.s3.amazonaws.com/a.pdf"}]
        with patch("claims_automation.processor._NO_REDIRECT.open", side_effect=OSError("timeout")):
            self.push(event("claim_form_submitted", "k21", answers={"driver_licence": "L1"}, documents=docs))
        case = ClaimCase.objects.get()
        self.assertEqual(case.triage, "exception")
        self.assertTrue(any("police.pdf" in r for r in case.triage_reasons))

    def test_revised_assessment_drafts_again_and_says_so(self, _ai):
        a = {"assessmentId": "ML-R", "totalLoss": 0, "repairer": "R",
             "lines": [{"type": "part", "code": "P", "description": "D", "supplier": "S",
                        "qty": 1, "unitPrice": 10, "total": 10}]}
        self.push(event("assessment_received", "k30", assessment=a))
        self.push(event("assessment_received", "k31", assessment=a | {"updatedAt": "later"}))
        self.assertEqual(ClaimsAssessment.objects.count(), 1)      # same content, new timestamp
        b = a | {"lines": a["lines"] + [{"type": "part", "code": "Q", "description": "E", "supplier": "S",
                                          "qty": 1, "unitPrice": 5, "total": 5}]}
        self.push(event("assessment_received", "k32", assessment=b))
        self.assertEqual(ClaimsAssessment.objects.count(), 2)      # revised -> new drafts
        self.assertTrue(OmniTask.objects.filter(title__icontains="REVISED").exists())

    def test_second_approve_click_is_refused(self, _ai):
        self.push(event("claim_form_submitted", "k33", answers={"driver_permission": "no"}))
        letter = ClaimLetter.objects.get()
        self.client.force_login(self.manager)
        url = f"/api/v1/claims-automation/letters/{letter.id}/approve/"
        self.assertEqual(self.client.post(url).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 409)

    def test_failed_event_is_retried_on_redelivery(self, _ai):
        with patch("claims_automation.processor._on_registered", side_effect=RuntimeError("db blip")), \
                patch.dict("claims_automation.processor.HANDLERS",
                           {"claim_registered": lambda c, p: (_ for _ in ()).throw(RuntimeError("db blip"))}):
            self.push(event("claim_registered", "k34"))
        self.assertEqual(ClaimAutomationEvent.objects.get().status, "failed")
        self.push(event("claim_registered", "k34"))
        self.assertEqual(ClaimAutomationEvent.objects.get().status, "processed")

    # ── what Graphite reads back ───────────────────────────────────────────
    def test_insight_carries_no_customer_identity(self, _ai):
        self.push(
            event("claim_form_submitted", "k11", answers={"driver_licence": "L1"})
        )
        r = self.client.get(
            INSIGHT,
            {"claim_ref": "G2026000777"},
            HTTP_AUTHORIZATION=f"ApiKey {self.key}",
        )
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        for pii in (
            "Mpho",
            "client@example.com",
            "DOMG2026123456",
            "B777AAA",
            "phone-on-file",
        ):
            self.assertNotIn(pii, body)
        self.assertEqual(r.json()["insight"]["triage"], "straight_through")

    def test_third_party_details_never_reach_the_ai(self, _ai):
        from claims_automation.ai_reader import build_facts_text
        text = build_facts_text({"answers": {
            "third_party": "Kabo Secret 71234567", "injuries": "Neo Hidden hurt",
            "vehicle_location": "Plot 999 Tlokweng", "driver_name": "Tumi Private",
            "damage_description": "Front bumper cracked"}}, [])
        for secret in ("Kabo", "71234567", "Neo Hidden", "Plot 999", "Tumi"):
            self.assertNotIn(secret, text)
        self.assertIn("Front bumper cracked", text)

    # ── salvage: the wreck goes to Veritas (CFO 19-Sep-2026) ───────────────
    def _aol(self):
        """A total loss, so an Agreement of Loss is drafted and awaiting."""
        self.push(event("assessment_received", "s1",
                        assessment={"assessmentId": "ML-S", "totalLoss": 1}))
        return ClaimLetter.objects.get(kind="aol")

    def _tick(self, **answers):
        case = ClaimCase.objects.get()
        base = {"vehicle_in_yard": "yes", "blue_book": "yes", "spare_keys": "yes",
                "number_plates": "yes"}
        base.update(answers)
        return self.client.post(f"/api/v1/claims-automation/handover/{case.claim_ref}/",
                                {"checklist": base, "note": "handed over"},
                                content_type="application/json")

    def test_agreement_of_loss_is_blocked_until_the_yard_confirms(self, _ai):
        letter = self._aol()
        self.client.force_login(self.senior)
        r = self.client.post(f"/api/v1/claims-automation/letters/{letter.id}/approve/")
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.json()["needs_override"])
        letter.refresh_from_db()
        self.assertEqual(letter.status, "awaiting")
        self.assertFalse(VeritasSalvageCharge.objects.exists())

    def test_yard_ticks_the_checklist_then_it_settles_and_veritas_is_charged(self, _ai):
        letter = self._aol()
        self.client.force_login(self.yard)                      # Veritas (VCM)
        self.assertEqual(self._tick().status_code, 200)
        self.client.force_login(self.senior)
        r = self.client.post(f"/api/v1/claims-automation/letters/{letter.id}/approve/")
        self.assertEqual(r.status_code, 200)
        charge = VeritasSalvageCharge.objects.get()
        # 20% of what we PAID the client (120000 - 5000 excess), not of the sum insured
        self.assertEqual(str(charge.settlement), "115000.00")
        self.assertEqual(str(charge.amount), "23000.00")

    def test_a_missing_item_still_blocks_but_not_applicable_with_a_note_does_not(self, _ai):
        letter = self._aol()
        self.client.force_login(self.yard)
        self._tick(spare_keys="no")
        self.client.force_login(self.senior)
        self.assertEqual(self.client.post(
            f"/api/v1/claims-automation/letters/{letter.id}/approve/").status_code, 409)
        self.client.force_login(self.yard)
        self._tick(spare_keys="na")                              # burnt shell, note given
        self.client.force_login(self.senior)
        self.assertEqual(self.client.post(
            f"/api/v1/claims-automation/letters/{letter.id}/approve/").status_code, 200)

    def test_only_the_claims_manager_may_override_and_the_reason_is_kept(self, _ai):
        letter = self._aol()
        self.client.force_login(self.senior)
        r = self.client.post(f"/api/v1/claims-automation/letters/{letter.id}/approve/",
                             {"override_reason": "Client keeps the wreck"},
                             content_type="application/json")
        self.assertEqual(r.status_code, 403)
        self.client.force_login(self.manager)
        r = self.client.post(f"/api/v1/claims-automation/letters/{letter.id}/approve/",
                             {"override_reason": "Client keeps the wreck by agreement"},
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        h = SalvageHandover.objects.get()
        self.assertEqual(h.override_by_id, self.manager.id)
        self.assertIn("keeps the wreck", h.override_reason)

    def test_nothing_is_posted_until_finance_names_the_income_account(self, _ai):
        letter = self._aol()
        self.client.force_login(self.yard)
        self._tick()
        self.client.force_login(self.senior)
        self.client.post(f"/api/v1/claims-automation/letters/{letter.id}/approve/")
        charge = VeritasSalvageCharge.objects.get()
        self.assertEqual(charge.status, "pending_config")
        self.assertIsNone(charge.invoice_id)

    @override_settings(CLAIMS_VERITAS_SALVAGE_INCOME_ACCOUNT="105004")
    def test_with_the_account_named_a_draft_invoice_is_raised_to_the_right_receivable(self, _ai):
        from ledger.models import Account
        from billing.models import Invoice
        from core.models import TaxRate
        from decimal import Decimal as D

        TaxRate.objects.get_or_create(tax_code="VAT_STD", defaults={
            "name": "Standard Rate 14%", "rate": D("14.00"), "is_active": True,
            "effective_from": "2026-01-01"})
        for code, name in (("105004", "Salvages & Recoveries"), ("260001", "Veritas salvage receivable")):
            Account.objects.get_or_create(code=code, defaults={"name": name, "account_type": "asset"})
        letter = self._aol()
        self.client.force_login(self.yard)
        self._tick()
        self.client.force_login(self.senior)
        self.client.post(f"/api/v1/claims-automation/letters/{letter.id}/approve/")
        charge = VeritasSalvageCharge.objects.get()
        self.assertEqual(charge.status, "drafted")
        inv = Invoice.objects.get(pk=charge.invoice_id)
        self.assertEqual(inv.status, "draft")                     # Omni never posts it
        self.assertEqual(inv.receivable_account.code, "260001")   # not premium receivable
        # 20% of the settlement is the VALUE of the wreck; VAT is added on top at
        # the standard rate. Finance confirms the treatment before posting.
        self.assertEqual(str(inv.subtotal), "23000.00")
        self.assertEqual(str(inv.total_amount), "26220.00")
        self.assertIn("veritas capital management", inv.contact.name.lower())
        self.assertEqual(inv.contact.contact_type, "customer")

    def test_a_receipt_clears_the_veritas_account_not_premium_receivable(self, _ai):
        """When Veritas pays, the money must clear the Veritas receivable — not
        premium debtors. A mixed receipt still behaves exactly as before."""
        from datetime import date
        from ledger.models import Account
        from payments.models import receivable_override
        from billing.models import Contact, Invoice
        ar = Account.objects.create(code="260001", name="Veritas salvage receivable",
                                    account_type="asset")
        contact = Contact.objects.create(name="Veritas Capital Management",
                                         contact_type=Contact.ContactType.CUSTOMER)

        def inv(receivable):
            return Invoice.objects.create(
                invoice_type=Invoice.InvoiceType.CUSTOMER_INVOICE, contact=contact,
                receivable_account=receivable, issue_date=date(2026, 9, 20),
                created_by=self.root)

        self.assertEqual(receivable_override([inv(ar)]).code, "260001")
        self.assertIsNone(receivable_override([inv(None)]))              # ordinary invoice
        self.assertIsNone(receivable_override([inv(ar), inv(None)]))     # mixed -> unchanged

    def test_the_yard_worklist_shows_no_customer_details(self, _ai):
        self._aol()
        self.client.force_login(self.yard)
        r = self.client.get("/api/v1/claims-automation/handovers/")
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("G2026000777", body)
        for pii in ("Mpho", "client@example.com", "DOMG2026123456"):
            self.assertNotIn(pii, body)

    def test_an_outsider_cannot_declare_possession(self, _ai):
        self._aol()
        self.client.force_login(self.outsider)
        self.assertEqual(self._tick().status_code, 403)

    # ── the staff screen ───────────────────────────────────────────────────
    def test_staff_list_is_claims_team_only(self, _ai):
        self.push(event("claim_registered", "k12"))
        self.client.force_login(self.outsider)
        self.assertEqual(
            self.client.get("/api/v1/claims-automation/cases/").status_code, 403
        )
        self.client.force_login(self.handler)
        r = self.client.get("/api/v1/claims-automation/cases/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["results"][0]["claim_ref"], "G2026000777")
