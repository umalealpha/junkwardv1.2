"""healthcare/vendor_esign_views.py — Staff-assisted intake + remote e-sign.

Two-phase vendor onboarding (CFO 2026-06-05):
  Phase 1 (staff, authenticated): prepare a VendorOnboarding draft, no signature.
  Phase 2 (doctor, NO login): open a single-use tokenised link, review the
           prepared agreement, give DPA consent, sign. Finalise = PDF + AFA email.

Security: token = secrets.token_urlsafe(32); only its SHA-256 hash is stored;
single-use (used_at) + expiry + revoke; public endpoints are AnonRateThrottle'd;
the raw token lives only in the emailed link (never in the DB, never logged).
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from core.notifications import send_html_with_cfo_cc
from .agreement_pdf import build_agreement_pdf
from .models import VendorOnboarding, VendorSignatureRequest
from .permissions import IsVendorOnboarder
from .vendor_views import (
    AGREEMENT_RECIPIENTS, _email_html, _flatten, _make_reference,
)

log = logging.getLogger(__name__)

PUBLIC_SIGN_BASE = "https://omni.alphadirect.co.bw/vendor-sign"
TOKEN_TTL_DAYS_DEFAULT = 14

# Fields we expose read-only on the public sign page (NO banking — masked only).
_PUBLIC_FIELDS = (
    "company_name", "registration_number", "trading_name", "practitioner_name",
    "council_type", "council_registration_number", "discipline", "service_category",
    "services_offered", "principal_place_of_business", "effective_date",
    "contact_email", "contact_tel",
)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _client_ip(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR", ""))[:45]


# ----------------------------------------------------------------------------
# Phase 1 — staff (authenticated)
# ----------------------------------------------------------------------------
class VendorDraftSaveView(APIView):
    """POST: create/update a VendorOnboarding DRAFT on the doctor's behalf.
    No signature required. Pass reference_number to update an existing draft.
    """
    permission_classes = [IsAuthenticated, IsVendorOnboarder]

    EDITABLE = (
        "company_name", "registration_number", "registration_date", "registered_address",
        "tin", "vat_number", "practitioner_name", "council_type",
        "council_registration_number", "discipline", "practice_address", "bank_name",
        "branch_name", "branch_code", "account_holder", "account_number",
        "service_category", "services_offered", "trading_name", "representative_name",
        "representative_capacity", "principal_place_of_business", "effective_date",
        "contact_tel", "contact_email",
    )

    def post(self, request):
        flat = _flatten(request.data if isinstance(request.data, dict) else {})
        ref = flat.get("reference_number")
        now = timezone.now()
        rec = VendorOnboarding.objects.filter(reference_number=ref).first() if ref else None
        if rec and rec.signing_status in ("signed", "completed"):
            return Response({"detail": "This record is already signed and cannot be edited."},
                            status=status.HTTP_409_CONFLICT)
        if not rec:
            rec = VendorOnboarding(reference_number=_make_reference())
        for f in self.EDITABLE:
            if flat.get(f) is not None:
                setattr(rec, f, flat.get(f))
        if isinstance(flat.get("directors"), list):
            rec.directors = flat["directors"]
        if not rec.trading_name:
            rec.trading_name = rec.company_name
        ready = bool(flat.get("ready"))
        rec.signing_status = "ready" if ready else "draft"
        rec.prepared_by = request.user if request.user.is_authenticated else None
        rec.prepared_at = now
        rec.payload = request.data if isinstance(request.data, dict) else {}
        rec.save()
        return Response({"success": True, "reference_number": rec.reference_number,
                         "signing_status": rec.signing_status}, status=status.HTTP_200_OK)


class VendorSendSignatureView(APIView):
    """POST {reference_number, sent_to_email?, expires_days?}: generate a single-use
    tokenised sign link and email it to the doctor. Marks the record `sent`.
    """
    permission_classes = [IsAuthenticated, IsVendorOnboarder]

    def post(self, request):
        ref = (request.data or {}).get("reference_number")
        rec = VendorOnboarding.objects.filter(reference_number=ref).first()
        if not rec:
            return Response({"detail": "Unknown reference_number."}, status=404)
        if rec.signing_status in ("signed", "completed"):
            return Response({"detail": "Already signed."}, status=status.HTTP_409_CONFLICT)
        to_email = ((request.data or {}).get("sent_to_email") or rec.contact_email or "").strip()
        if not to_email:
            return Response({"detail": "No email on file for the doctor — provide sent_to_email."},
                            status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        try:
            days = int((request.data or {}).get("expires_days") or TOKEN_TTL_DAYS_DEFAULT)
        except (TypeError, ValueError):
            days = TOKEN_TTL_DAYS_DEFAULT
        days = max(1, min(days, 30))

        raw = secrets.token_urlsafe(32)
        sr = VendorSignatureRequest.objects.create(
            onboarding=rec, token_hash=_hash(raw), sent_to_email=to_email,
            created_by=request.user if request.user.is_authenticated else None,
            expires_at=timezone.now() + timedelta(days=days),
        )
        link = f"{PUBLIC_SIGN_BASE}/{raw}"
        sent = 0
        try:
            html = f"""<div style="font-family:'Book Antiqua',Palatino,Georgia,serif;color:#0D1B2A;max-width:640px">
  <div style="background:#0D1B2A;color:#fff;padding:14px 18px;font-size:18px;font-weight:bold">Alpha Direct Healthcare — Sign your vendor agreement</div>
  <p>Dear Doctor,</p>
  <p>Your AFA Service Provider Network Agreement has been prepared for you by the
  Alpha Direct Healthcare team — you do <b>not</b> need to fill in any forms.
  Please open the secure link below, review your details and the agreement, then
  sign. The link is personal to you and expires in {days} days.</p>
  <p style="text-align:center;margin:22px 0">
    <a href="{link}" style="background:#F4A623;color:#0D1B2A;text-decoration:none;padding:12px 22px;border-radius:8px;font-weight:bold">Review &amp; sign your agreement</a>
  </p>
  <p style="color:#555;font-size:12px">If the button does not work, paste this into your browser:<br/>{link}</p>
  <p style="color:#555;font-size:12px">If you did not expect this, ignore the email — the link cannot be used without your action.</p>
</div>"""
            sent = send_html_with_cfo_cc(
                subject=f"Sign your Alpha Direct Healthcare vendor agreement — {rec.company_name}",
                html=html, to=[to_email], cc=None,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("send-signature email failed for %s: %s", rec.reference_number, e)
            return Response({"detail": "Could not send the signing email; link not delivered."},
                            status=status.HTTP_502_BAD_GATEWAY)

        rec.signing_status = "sent"
        rec.save(update_fields=["signing_status"])
        log.info("[vendor-esign] sign link sent for %s to %s (expires %s)",
                 rec.reference_number, to_email, sr.expires_at)
        return Response({"success": True, "reference_number": rec.reference_number,
                         "sent_to": to_email, "expires_at": sr.expires_at,
                         "email_sent": bool(sent)}, status=status.HTTP_200_OK)


# ----------------------------------------------------------------------------
# Phase 2 — public, token-gated, NO login
# ----------------------------------------------------------------------------
class VendorSignThrottle(AnonRateThrottle):
    """Self-contained anon throttle for the public sign endpoints — carries its
    own rate so it does NOT depend on settings.DEFAULT_THROTTLE_RATES (which is
    unset on this project and would raise ImproperlyConfigured)."""
    scope = "vendor_sign"
    THROTTLE_RATES = {"vendor_sign": "20/min"}


class _PublicSignBase(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [VendorSignThrottle]

    def _lookup(self, token):
        sr = (VendorSignatureRequest.objects
              .select_related("onboarding")
              .filter(token_hash=_hash(token or "")).first())
        return sr


class VendorSignDetailView(_PublicSignBase):
    """GET /vendor-sign/<token>/ — read-only prepared data + agreement for review."""
    def get(self, request, token):
        sr = self._lookup(token)
        if not sr:
            return Response({"detail": "Invalid signing link."}, status=404)
        ok, msg = sr.is_valid(timezone.now())
        if not ok:
            return Response({"detail": msg, "expired": True}, status=status.HTTP_410_GONE)
        rec = sr.onboarding
        if sr.viewed_at is None:
            sr.viewed_at = timezone.now(); sr.save(update_fields=["viewed_at"])
            if rec.signing_status in ("sent", "ready"):
                rec.signing_status = "viewed"; rec.save(update_fields=["signing_status"])
        data = {f: getattr(rec, f, "") for f in _PUBLIC_FIELDS}
        data["reference_number"] = rec.reference_number
        data["masked_account"] = rec.masked_account
        # SECURITY (3-agent review 2026-06-05): never expose director ID numbers
        # on the public no-login page — names/roles only.
        data["directors"] = [
            {"full_name": d.get("full_name", ""), "role": d.get("role", "")}
            for d in (rec.directors or []) if isinstance(d, dict)
        ]
        return Response({"success": True, "vendor": data})


class VendorSignSubmitView(_PublicSignBase):
    """POST /vendor-sign/<token>/ {signatory_full_name, signature_data_url, consent:true}
    — finalise: apply signature, build PDF, email AFA + cc doctor, single-use lock.
    """
    def post(self, request, token):
        body = request.data if isinstance(request.data, dict) else {}
        with transaction.atomic():
            sr = (VendorSignatureRequest.objects.select_for_update()
                  .select_related("onboarding")
                  .filter(token_hash=_hash(token or "")).first())
            if not sr:
                return Response({"detail": "Invalid signing link."}, status=404)
            ok, msg = sr.is_valid(timezone.now())
            if not ok:
                return Response({"detail": msg, "expired": True}, status=status.HTTP_410_GONE)
            if not body.get("consent"):
                return Response({"detail": "DPA consent is required to sign."},
                                status=status.HTTP_422_UNPROCESSABLE_ENTITY)
            _sig = body.get("signature_data_url") or ""
            if not _sig:
                return Response({"detail": "A signature is required."},
                                status=status.HTTP_422_UNPROCESSABLE_ENTITY)
            # SECURITY: only accept an image data-URL (block arbitrary payloads).
            if not str(_sig).startswith("data:image/"):
                return Response({"detail": "Signature must be an image."},
                                status=status.HTTP_422_UNPROCESSABLE_ENTITY)
            now = timezone.now()
            rec = sr.onboarding
            rec.signatory_full_name = (body.get("signatory_full_name") or rec.signatory_full_name or "")
            rec.signature_data_url = body.get("signature_data_url")
            rec.signed_at = now
            rec.agreement_accepted = True
            rec.agreement_accepted_at = now
            rec.consent_at = now
            rec.signing_status = "signed"
            rec.save()
            # single-use lock + audit (inside the transaction)
            sr.used_at = now
            sr.signed_at = now
            sr.consent_accepted_at = now
            sr.signer_ip = _client_ip(request)
            sr.signer_user_agent = (request.META.get("HTTP_USER_AGENT", "") or "")[:1000]
            sr.save(update_fields=["used_at", "signed_at", "consent_accepted_at",
                                   "signer_ip", "signer_user_agent"])

        # outside the lock: build PDF + email AFA (cc doctor)
        sent = 0
        try:
            pdf = build_agreement_pdf(rec)
            sent = send_html_with_cfo_cc(
                subject=f"Signed Service Provider Network Agreement — {rec.company_name} ({rec.reference_number})",
                html=_email_html(rec), to=list(AGREEMENT_RECIPIENTS),
                cc=[rec.contact_email] if rec.contact_email else None,
                attachments=[(f"agreement-{rec.reference_number}.pdf", pdf, "application/pdf")],
            )
            rec.agreement_email_sent = bool(sent)
            rec.agreement_emailed_to = ", ".join(AGREEMENT_RECIPIENTS)
            rec.signing_status = "completed"
            rec.save(update_fields=["agreement_email_sent", "agreement_emailed_to", "signing_status"])
        except Exception as e:  # noqa: BLE001
            log.exception("agreement PDF/email failed for %s: %s", rec.reference_number, e)
        return Response({"success": True, "reference_number": rec.reference_number,
                         "agreement_email_sent": bool(sent)}, status=status.HTTP_201_CREATED)
