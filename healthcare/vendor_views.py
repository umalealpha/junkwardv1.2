"""healthcare/vendor_views.py — Vendor onboarding API (Ankete-only).

Endpoints:
  POST /api/v1/health/vendor-onboarding/extract/  — OCR text → DeepSeek fields
  POST /api/v1/health/vendor-onboarding/submit/   — persist + agreement PDF + email

Access restricted to Ankete via IsVendorOnboarder. Login via omni SSO.
Email on sign-off goes to AFA (ankete@ + mtlagae@) through omni's Microsoft
Graph backend (send_html_with_cfo_cc auto-CCs EXCO). No SMTP.
"""
from __future__ import annotations

import logging
import uuid

from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.notifications import send_html_with_cfo_cc
from .agreement_pdf import build_agreement_pdf
from .extract import extract_text
from .models import VendorOnboarding
from .permissions import IsVendorOnboarder
from .vendor_extract import extract_vendor_fields

log = logging.getLogger(__name__)

_MAX_BYTES = 10 * 1024 * 1024
AGREEMENT_RECIPIENTS = ["ankete@alphadirect.co.bw", "mtlagae@alphadirect.co.bw",
                        "providers@alphadirect.co.bw"]  # Alana 2026-06-05: provider-reg inbox


def _make_reference() -> str:
    return f"ADH-{timezone.localdate():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


class VendorOnboardingAccessView(APIView):
    """GET: whether the signed-in user is authorised to submit a vendor record.

    Drives an up-front notice in the wizard so an unauthorised user is told at
    the start instead of being walled at the final submit (Oprah QA 2026-07-23).
    Read-only — this does NOT change who may submit; IsVendorOnboarder still
    gates the extract/submit endpoints.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"authorised": IsVendorOnboarder().has_permission(request, self)})


class VendorFieldExtractView(APIView):
    """POST: file (multipart) OR raw_text + doc_type(CoI|extract) → DeepSeek fields.

    Returns {fields:[{path,value,confidence}], deepseek_used}. Every field is for
    HUMAN CONFIRMATION in the UI — nothing is committed here.
    """
    permission_classes = [IsAuthenticated, IsVendorOnboarder]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        doc_type = (request.data.get("doc_type") or "CoI")
        raw_text = (request.data.get("raw_text") or "").strip()
        f = request.FILES.get("file")
        if not raw_text and f:
            if f.size > _MAX_BYTES:
                return Response({"detail": "file too large"}, status=status.HTTP_400_BAD_REQUEST)
            raw_text = extract_text(f.read(), (f.content_type or "").lower(), f.name) or ""
        if not raw_text:
            return Response({"detail": "file or raw_text required"}, status=status.HTTP_400_BAD_REQUEST)
        result = extract_vendor_fields(raw_text, doc_type)
        return Response({"success": True, **result})


class VendorOnboardingSubmitView(APIView):
    """POST: full vendor payload (JSON) → persist + agreement PDF + email AFA."""
    permission_classes = [IsAuthenticated, IsVendorOnboarder]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    REQUIRED = ["company_name", "registration_number", "signatory_full_name"]

    def post(self, request):
        p = request.data if isinstance(request.data, dict) else {}
        # Support nested payload {entity:{...}, agreement:{...}} or flat keys.
        flat = _flatten(p)
        missing = [k for k in self.REQUIRED if not flat.get(k)]
        if not flat.get("agreement_accepted"):
            missing.append("agreement_accepted")
        if not flat.get("signature_data_url"):
            missing.append("signature_data_url")
        # Never persist consent that was not actually given (DPA — the record and
        # the agreement emailed to AFA assert consent, so the flag must be true).
        if not flat.get("consent_accepted"):
            missing.append("consent_accepted")
        if missing:
            # Name the field(s) in plain words so the user can fix it — the old
            # bare "missing required fields" told them nothing (Alana, 2026-07-23).
            labels = {
                "company_name": "Company name",
                "registration_number": "Registration number",
                "signatory_full_name": "Full name of signatory",
                "agreement_accepted": "Accept the AFA agreement",
                "signature_data_url": "Signature",
                "consent_accepted": "Consent to data processing",
            }
            names = ", ".join(labels.get(k, k) for k in missing)
            return Response({"detail": f"Please complete: {names}.", "fields": missing},
                            status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        now = timezone.now()
        rec = VendorOnboarding.objects.create(
            reference_number=_make_reference(),
            company_name=flat.get("company_name", ""),
            registration_number=flat.get("registration_number", ""),
            registration_date=flat.get("registration_date", ""),
            registered_address=flat.get("registered_address", ""),
            tin=flat.get("tin", ""),
            vat_number=flat.get("vat_number", ""),
            directors=flat.get("directors", []) or [],
            practitioner_name=flat.get("practitioner_name", ""),
            council_type=flat.get("council_type", "BHPC"),
            council_registration_number=flat.get("council_registration_number", ""),
            discipline=flat.get("discipline", ""),
            practice_address=flat.get("practice_address", ""),
            bank_name=flat.get("bank_name", ""),
            branch_name=flat.get("branch_name", ""),
            branch_code=flat.get("branch_code", ""),
            account_holder=flat.get("account_holder", ""),
            account_number=flat.get("account_number", ""),
            service_category=flat.get("service_category", ""),
            services_offered=flat.get("services_offered", ""),
            trading_name=flat.get("trading_name", "") or flat.get("company_name", ""),
            representative_name=flat.get("representative_name", ""),
            representative_capacity=flat.get("representative_capacity", ""),
            principal_place_of_business=flat.get("principal_place_of_business", ""),
            effective_date=flat.get("effective_date", ""),
            contact_tel=flat.get("contact_tel", ""),
            contact_email=flat.get("contact_email", ""),
            agreement_accepted=bool(flat.get("agreement_accepted")),
            agreement_accepted_at=now,
            consent_version=flat.get("consent_version", ""),
            consent_at=now,
            signatory_full_name=flat.get("signatory_full_name", ""),
            signed_at=now,
            signature_data_url=flat.get("signature_data_url", ""),
            deepseek_used=bool(flat.get("deepseek_used")),
            submitted_by=request.user if request.user.is_authenticated else None,
            payload=p,
        )

        # Build the filled agreement PDF and email it to AFA via omni Graph.
        sent = 0
        try:
            pdf = build_agreement_pdf(rec)
            html = _email_html(rec)
            sent = send_html_with_cfo_cc(
                subject=f"Signed Service Provider Network Agreement — {rec.company_name} ({rec.reference_number})",
                html=html,
                to=list(AGREEMENT_RECIPIENTS),
                cc=[rec.contact_email] if rec.contact_email else None,
                attachments=[(f"agreement-{rec.reference_number}.pdf", pdf, "application/pdf")],
            )
            rec.agreement_email_sent = bool(sent)
            rec.agreement_emailed_to = ", ".join(AGREEMENT_RECIPIENTS)
            rec.save(update_fields=["agreement_email_sent", "agreement_emailed_to"])
        except Exception as e:  # noqa: BLE001
            log.exception("agreement email failed for %s: %s", rec.reference_number, e)

        log.info("[vendor-onboarding] %s stored; agreement sent=%s -> %s",
                 rec.reference_number, sent, AGREEMENT_RECIPIENTS)
        return Response({"success": True, "reference_number": rec.reference_number,
                         "agreement_email_sent": bool(sent)}, status=status.HTTP_201_CREATED)


def _flatten(p: dict) -> dict:
    """Accept either flat keys or nested {entity,tax,banking,practitioner,
    services,agreement,declaration,consent,directors} and return flat keys."""
    if not isinstance(p, dict):
        return {}
    out = dict(p)
    sect = lambda k: p.get(k) if isinstance(p.get(k), dict) else {}
    e, t, b = sect("entity"), sect("tax"), sect("banking")
    pr, sv, ag = sect("practitioner"), sect("services"), sect("agreement")
    dec, con = sect("declaration"), sect("consent")
    out.setdefault("company_name", e.get("companyName"))
    out.setdefault("registration_number", e.get("registrationNumber"))
    out.setdefault("registration_date", e.get("registrationDate"))
    out.setdefault("registered_address", e.get("registeredAddress"))
    out.setdefault("tin", t.get("tin"))
    out.setdefault("vat_number", t.get("vatNumber"))
    out.setdefault("practitioner_name", pr.get("practitionerName"))
    out.setdefault("council_type", pr.get("councilType"))
    out.setdefault("council_registration_number", pr.get("councilRegistrationNumber"))
    out.setdefault("discipline", pr.get("discipline"))
    out.setdefault("practice_address", pr.get("practiceAddress"))
    out.setdefault("bank_name", b.get("bankName"))
    out.setdefault("branch_name", b.get("branchName"))
    out.setdefault("branch_code", b.get("branchCode"))
    out.setdefault("account_holder", b.get("accountHolder"))
    out.setdefault("account_number", b.get("accountNumber"))
    out.setdefault("service_category", sv.get("serviceCategory"))
    out.setdefault("services_offered", sv.get("servicesOffered"))
    out.setdefault("trading_name", ag.get("tradingName"))
    out.setdefault("representative_name", ag.get("representativeName"))
    out.setdefault("representative_capacity", ag.get("representativeCapacity"))
    out.setdefault("principal_place_of_business", ag.get("principalPlaceOfBusiness"))
    out.setdefault("effective_date", ag.get("effectiveDate"))
    out.setdefault("contact_tel", ag.get("contactTel"))
    out.setdefault("contact_email", ag.get("contactEmail"))
    if ag.get("agreementAccepted") is not None:
        out.setdefault("agreement_accepted", ag.get("agreementAccepted"))
    out.setdefault("signatory_full_name", dec.get("signatoryFullName"))
    out.setdefault("signature_data_url", dec.get("signatureDataUrl"))
    out.setdefault("consent_version", con.get("consentTextVersion"))
    # BUG (Alana 2026-07-23): the nested consent tick was never mapped to the
    # flat key the submit check reads, so EVERY submission failed the final step
    # with "missing required fields" even when consent was given. Mirror the
    # agreement_accepted mapping above.
    if con.get("consentAccepted") is not None:
        out.setdefault("consent_accepted", con.get("consentAccepted"))
    if isinstance(p.get("directors"), dict):
        out["directors"] = p["directors"].get("directors", [])
    return {k: v for k, v in out.items() if v is not None}


def _email_html(rec) -> str:
    return f"""<div style="font-family:'Book Antiqua',Palatino,Georgia,serif;color:#0D1B2A">
  <div style="background:#7A2E22;color:#fff;padding:12px 16px;font-size:18px;font-weight:bold">
    AFA Service Provider Network Agreement
  </div>
  <p>A healthcare vendor has accepted and electronically signed the AFA Service
     Provider Network Agreement via the Alpha Direct Healthcare onboarding portal.</p>
  <table cellpadding="6" style="border-collapse:collapse">
    <tr><td><b>Reference</b></td><td>{rec.reference_number}</td></tr>
    <tr><td><b>Company</b></td><td>{rec.company_name}</td></tr>
    <tr><td><b>Trading name</b></td><td>{rec.trading_name}</td></tr>
    <tr><td><b>Practitioner</b></td><td>{rec.practitioner_name}</td></tr>
    <tr><td><b>Effective date</b></td><td>{rec.effective_date}</td></tr>
  </table>
  <p style="color:#555">Signed agreement attached for AFA counter-signature
     (Tebogo Motsie). Schedule A funder: Alpha Direct Insurance Company.</p>
</div>"""
