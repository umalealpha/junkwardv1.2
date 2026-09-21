"""
Alpha Direct Financial Management System — Root URL Configuration
"""
from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse
from django.conf import settings
from decouple import config
from django.conf.urls.static import static
from core.auth_views import RateLimitedLoginView
from core.notebook_views import notebook_raw, notebook_detail
from core.unicoin_mail_views import unicoin_login_code
from core.unicoin_recon_views import recon_exceptions, clear_recon_exception
from core.staff_login_views import (staff_login_start, staff_login_verify,
                                    staff_login_change, staff_login_forgot_start,
                                    staff_login_forgot_reset)
from core.sso_exchange_views import sso_exchange
from core.device_session_views import my_devices, revoke_device
from core.app_data_views import request_data_deletion, sign_out_everywhere
from core.views import helpdesk_comments, helpdesk_notify
from core.brief_note_views import (brief_note_access, brief_note_router,
                                   brief_note_withdraw)
from core.ceo_monitor_views import ceo_monitor_decide, ceo_monitor_escalate
from core.magic_action import magic_action
from procurement.verify_view import po_verify
from procurement.claim_po_views import po_by_claim
from underwriting.verify_view import quote_verify
from hris.leave_explain import explain_page
from core.processor_dpa import dpa_sign_page, dpa_sign_submit
from recruitment.authority_actions import (authority_action_page,
                                           authority_action_submit)
from recruitment.candidate_status import status_page as applicant_status_page


def health_check(request):
    """Liveness, plus WHICH BUILD is answering.

    The commit is the point. On 2026-09-10 a fix was merged, CI went green, and
    production kept serving the previous build for hours with a broken page in
    the menu — and nothing anywhere said so. A deploy that silently does not
    happen looks exactly like a deploy that did.

    OMNI_BUILD_SHA is baked into the image at build time (see Dockerfile ARG
    GIT_SHA). It is absent when running from a source checkout, which is
    honest: 'unknown' means nobody can tell you what this is, not that it is
    current. infra/host/check-release-drift.sh compares this to origin/main.
    """
    return JsonResponse({
        'status': 'ok',
        'system': 'Alpha Direct Financial Management System',
        'version': '1.0.0',
        'commit': config('OMNI_BUILD_SHA', default='unknown'),
        'built_at': config('OMNI_BUILD_AT', default='unknown'),
    })


# SEC-08 (security assessment 2026-08-06): the Django admin sat on the guessable
# /admin/ path, internet-facing, which every automated scanner on the internet
# probes continuously. The path is now set by OMNI_ADMIN_PATH so it can be moved
# without a code change. It defaults to 'admin/' so nothing breaks on deploy —
# moving it is a one-line environment change, and the old path then 404s.
ADMIN_PATH = (config('OMNI_ADMIN_PATH', default='admin/') or 'admin/').strip().strip('/') + '/'

urlpatterns = [
    # Health check
    path('', health_check, name='health-check'),

    # Django admin
    path(ADMIN_PATH, admin.site.urls),

    # DRF auth (login/logout for browsable API)
    path('api/auth/', include('rest_framework.urls')),

    # Token auth endpoint for frontend login (throttled to 10/min/IP
    # via core.auth_views.RateLimitedLoginView to cap brute-force).
    path('api-token-auth/', RateLimitedLoginView.as_view(), name='api-token-auth'),

    # Staff email + password + email-OTP login (non-SSO, all AD staff).
    # UniCoin asks Omni to send its sign-in codes — Omni's mail works, Amazon's
    # in Cape Town has no verified sender. Narrow on purpose: a code to a staff
    # mailbox, nothing else. See core/unicoin_mail_views.py.
    path('api/v1/mail/unicoin-login-code/', unicoin_login_code,
         name='unicoin-login-code'),

    # UniCoin reconciliation exceptions (CFO direction 2026-09-01). A detection
    # flow raises a finding; Omni turns it into an owned, SLA'd task. Never moves
    # money or touches a policy. See core/unicoin_recon_views.py.
    path('api/v1/unicoin/recon-exceptions/', recon_exceptions,
         name='unicoin-recon-exceptions'),
    path('api/v1/unicoin/recon-exceptions/<uuid:pk>/clear/', clear_recon_exception,
         name='unicoin-recon-exception-clear'),

    # Shared CFO/Claude notebook. `raw` is plain text and must stay fast.
    path('api/v1/notebook/raw/', notebook_raw,    name='notebook-raw'),
    path('api/v1/notebook/',     notebook_detail, name='notebook-detail'),
    path('api/v1/auth/sso/exchange/', sso_exchange),
    path('api/v1/auth/staff/start/',  staff_login_start),
    path('api/v1/auth/staff/verify/', staff_login_verify),
    path('api/v1/auth/staff/change/', staff_login_change),
    # Forgot-password reset (no old password needed; emailed code is the gate).
    path('api/v1/auth/staff/forgot-start/',  staff_login_forgot_start),
    path('api/v1/auth/staff/forgot-reset/',  staff_login_forgot_reset),
    path('api/v1/auth/devices/', my_devices),
    path('api/v1/auth/devices/<int:pk>/', revoke_device),
    path('api/v1/auth/devices/sign-out-everywhere/', sign_out_everywhere),
    path('api/v1/app/data-request/', request_data_deletion),

    # PO-in-Graphite doorway (CFO 2026-08-24). Registered BEFORE the router
    # include on purpose: the router maps /api/v1/purchase-orders/<id>/, so
    # without this line "by-claim" would be read as a PO id. First match wins.
    path('api/v1/purchase-orders/by-claim/', po_by_claim, name='po-by-claim'),
    # Claims automation end to end (CFO plan 19-Sep-2026) — before the router.
    path('api/v1/claims-automation/', include('claims_automation.urls')),

    # REST API v1 — all modules via DRF router
    path('api/v1/', include('alpha_finance.api_router')),
    # The CFO's own screens — build log, and later the job switches.
    path('api/v1/', include('devlog.urls')),
    path('api/v1/training/', include('training.urls')),
    # Morning-brief note spaces (CFO 2026-09-10)
    path('api/v1/brief-notes/access/', brief_note_access, name='brief-note-access'),
    path('api/v1/brief-notes/<uuid:note_id>/', brief_note_withdraw,
         name='brief-note-withdraw'),
    path('api/v1/brief-notes/', brief_note_router, name='brief-notes'),
    path('api/v1/', include('ci_monitor.urls')),

    # Public, no-login "explain your day" page — token-identified, CSRF-exempt
    # (the signed token is the gate). Under /api/ so Caddy routes it to the
    # backend (like /api/verify/po/), not the Next.js frontend. CFO 2026-07-22.
    path('api/leave-explain/<str:token>/', explain_page, name='leave-explain'),

    # Public, no-login processor-DPA signing page — token-identified, CSRF-exempt
    # (the signed token is the gate). Under /api/ so Caddy routes it to the
    # backend, not the Next.js frontend. CFO 2026-07-29 (DPA-audit H-6).
    path('api/dpa/sign/<str:token>/',        dpa_sign_page,   name='dpa-sign'),
    path('api/dpa/sign/<str:token>/submit/', dpa_sign_submit, name='dpa-sign-submit'),
    path('api/ceo-monitor/escalate/', ceo_monitor_escalate, name='ceo-monitor-escalate'),
    path('api/ceo-monitor/decide/', ceo_monitor_decide, name='ceo-monitor-decide'),
    path('api/magic/<str:token>/', magic_action, name='magic-action'),

    # Login-free Authority-to-Recruit signing (CFO 2026-08-18) — signed-token
    # page so a signatory can approve/decline from the email or morning brief
    # without an omni sign-in. Same pattern as leave-action / incentive-action.
    path('api/authority-action/<str:token>/',
         authority_action_page,   name='authority-action-page'),
    path('api/authority-action/<str:token>/submit/',
         authority_action_submit, name='authority-action-submit'),

    # Login-free applicant status page (CFO 2026-08-26, H2) — a candidate opens
    # the link in their acknowledgement email and sees their own coarse status.
    path('api/applicant-status/<str:token>/',
         applicant_status_page, name='applicant-status'),

    # Legacy / standalone routes (kept for backwards compatibility)
    path('api/ledger/',  include('ledger.urls')),
    path('api/reports/', include('reporting.urls')),

    # IT Help Desk — read-only Django-side comments (posted via SSM management command)
    path('api/helpdesk/comments/', helpdesk_comments, name='helpdesk-comments'),
    path('api/helpdesk/notify/',   helpdesk_notify,   name='helpdesk-notify'),

    # Public PO verification page (opened by the QR code on the PO print).
    # Under /api/ so Caddy proxies it to Django; view-only, no auth.
    path('api/verify/po/<str:code>/', po_verify, name='po-verify'),

    # Public quotation verification page (opened by the QR code on the quote).
    # Same pattern as the PO code: the quotation's UUID is the capability token,
    # so only someone holding the printed document can reach it. View-only.
    path('api/verify/quote/<str:code>/', quote_verify, name='quote-verify'),

    # HRIS — Human Resource Information System (CFO-mandated 2026-05-10)
    path('hris/', include('hris.urls', namespace='hris')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


# Customise admin site header
admin.site.site_header = 'Alpha Direct Financial Management'
admin.site.site_title = 'Alpha Direct Admin'
admin.site.index_title = 'Financial Management System'
