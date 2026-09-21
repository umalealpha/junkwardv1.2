"""healthcare/vendor_invite_views.py — Self-service vendor onboarding by invite.

CFO 2026-07-28. The service provider fills in their OWN onboarding form via a
private, single-use, expiring link (NO login). Distinct from the e-sign flow in
vendor_esign_views (there staff prepare the record and the doctor only signs).

  Phase 1 (staff, authenticated): generate an invite link, optionally email it.
  Phase 2 (provider, NO login): open /onboard/<token>, fill the whole form, submit.

Security mirrors the e-sign flow: token = secrets.token_urlsafe(32); only its
SHA-256 hash is stored; single-use (used_at) + expiry + revoke; public endpoints
are AnonRateThrottle'd; the raw token lives only in the shared link.
"""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.notifications import send_html_with_cfo_cc
from .agreement_pdf import build_agreement_pdf
from .models import VendorOnboarding, VendorOnboardingInvite
from .permissions import IsVendorOnboarder
from .vendor_esign_views import VendorSignThrottle, _client_ip, _hash
from .vendor_views import AGREEMENT_RECIPIENTS, _email_html, _flatten, _make_reference

log = logging.getLogger(__name__)

PUBLIC_ONBOARD_BASE = "https://omni.alphadirect.co.bw/onboard"
INVITE_TTL_DAYS_DEFAULT = 14


# ----------------------------------------------------------------------------
# Phase 1 — staff (authenticated)
# ----------------------------------------------------------------------------
class VendorInviteCreateView(APIView):
    """POST {invited_email?, invited_name?, expires_days?, send_email?}: mint a
    single-use self-onboarding link and (optionally) email it to the provider.
    Returns the raw link ONCE for the staff member to copy."""
    permission_classes = [IsAuthenticated, IsVendorOnboarder]

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        email = (body.get("invited_email") or "").strip()
        name = (body.get("invited_name") or "").strip()
        try:
            days = int(body.get("expires_days") or INVITE_TTL_DAYS_DEFAULT)
        except (TypeError, ValueError):
            days = INVITE_TTL_DAYS_DEFAULT
        days = max(1, min(days, 30))

        raw = secrets.token_urlsafe(32)
        inv = VendorOnboardingInvite.objects.create(
            token_hash=_hash(raw), invited_email=email, invited_name=name,
            created_by=request.user if request.user.is_authenticated else None,
            expires_at=timezone.now() + timedelta(days=days),
        )
        link = f"{PUBLIC_ONBOARD_BASE}/{raw}"

        email_sent = False
        if body.get("send_email") and email:
            try:
                html = f"""<div style="font-family:'Book Antiqua',Palatino,Georgia,serif;color:#0D1B2A;max-width:640px">
  <div style="background:#0D1B2A;color:#fff;padding:14px 18px;font-size:18px;font-weight:bold">Alpha Direct Healthcare — Service Provider Onboarding</div>
  <p>Dear {name or 'Service Provider'},</p>
  <p>Please complete your onboarding with Alpha Direct Healthcare using the secure
  link below. You will enter your company, tax, credential and banking details,
  read and accept the AFA Service Provider Network Agreement, and sign — all in
  one form. The link is personal to you and expires in {days} days.</p>
  <p style="text-align:center;margin:22px 0">
    <a href="{link}" style="background:#F4A623;color:#0D1B2A;text-decoration:none;padding:12px 22px;border-radius:8px;font-weight:bold">Start your onboarding</a>
  </p>
  <p style="color:#555;font-size:12px">If the button does not work, paste this into your browser:<br/>{link}</p>
  <p style="color:#555;font-size:12px">If you did not expect this, ignore the email — the link cannot be used without your action.</p>
</div>"""
                sent = send_html_with_cfo_cc(
                    subject="Complete your Alpha Direct Healthcare onboarding",
                    html=html, to=[email], cc=None,
                )
                email_sent = bool(sent)
            except Exception as e:  # noqa: BLE001
                log.exception("vendor-invite email failed for %s: %s", email, e)

        log.info("[vendor-invite] link minted by %s for %s (expires %s, emailed=%s)",
                 getattr(request.user, "username", "?"), email or "(copy only)",
                 inv.expires_at, email_sent)
        return Response({"success": True, "link": link, "expires_at": inv.expires_at,
                         "email_sent": email_sent}, status=status.HTTP_201_CREATED)


# ----------------------------------------------------------------------------
# Phase 2 — public, token-gated, NO login
# ----------------------------------------------------------------------------
class _PublicInviteBase(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [VendorSignThrottle]

    def _lookup(self, token):
        return (VendorOnboardingInvite.objects
                .filter(token_hash=_hash(token or "")).first())


class VendorInviteDetailView(_PublicInviteBase):
    """GET /onboard/<token>/ — validate the invite so the public form can render."""
    def get(self, request, token):
        inv = self._lookup(token)
        if not inv:
            return Response({"detail": "Invalid onboarding link."}, status=404)
        ok, msg = inv.is_valid(timezone.now())
        if not ok:
            return Response({"detail": msg, "expired": True}, status=status.HTTP_410_GONE)
        if inv.viewed_at is None:
            inv.viewed_at = timezone.now()
            inv.save(update_fields=["viewed_at"])
        return Response({"success": True, "invited_name": inv.invited_name,
                         "invited_email": inv.invited_email})


class VendorInviteSubmitView(_PublicInviteBase):
    """POST /onboard/<token>/submit/ — the provider's full onboarding payload.
    Persist + agreement PDF + email AFA, then single-use-lock the invite."""
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    REQUIRED = ["company_name", "registration_number", "signatory_full_name"]

    def post(self, request, token):
        p = request.data if isinstance(request.data, dict) else {}
        flat = _flatten(p)
        missing = [k for k in self.REQUIRED if not flat.get(k)]
        if not flat.get("agreement_accepted"):
            missing.append("agreement_accepted")
        if not flat.get("signature_data_url"):
            missing.append("signature_data_url")
        if not flat.get("consent_accepted"):
            missing.append("consent_accepted")
        if missing:
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
        # SECURITY: only accept an image data-URL for the signature.
        sig = flat.get("signature_data_url") or ""
        if not str(sig).startswith("data:image/"):
            return Response({"detail": "Signature must be an image."},
                            status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        now = timezone.now()
        with transaction.atomic():
            inv = (VendorOnboardingInvite.objects.select_for_update(of=("self",))
                   .filter(token_hash=_hash(token or "")).first())
            if not inv:
                return Response({"detail": "Invalid onboarding link."}, status=404)
            ok, msg = inv.is_valid(now)
            if not ok:
                return Response({"detail": msg, "expired": True}, status=status.HTTP_410_GONE)

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
                contact_email=flat.get("contact_email", "") or inv.invited_email,
                agreement_accepted=bool(flat.get("agreement_accepted")),
                agreement_accepted_at=now,
                consent_version=flat.get("consent_version", ""),
                consent_at=now,
                signatory_full_name=flat.get("signatory_full_name", ""),
                signed_at=now,
                signature_data_url=flat.get("signature_data_url", ""),
                signing_status="signed",
                deepseek_used=bool(flat.get("deepseek_used")),
                submitted_by=None,   # self-service — no staff user
                payload=p,
            )
            inv.used_at = now
            inv.onboarding = rec
            inv.submitter_ip = _client_ip(request)
            inv.submitter_user_agent = (request.META.get("HTTP_USER_AGENT", "") or "")[:1000]
            inv.save(update_fields=["used_at", "onboarding", "submitter_ip", "submitter_user_agent"])

        # outside the lock: build PDF + email AFA (cc the provider)
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
