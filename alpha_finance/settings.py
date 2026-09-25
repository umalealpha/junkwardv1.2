"""
Alpha Direct Financial Management System
Django Settings
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from decouple import config, Csv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
SECRET_KEY = config('SECRET_KEY')
DEBUG = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=Csv())

# Public base URL — used to build absolute, scannable links (e.g. the QR code
# on the PO print points at the public verification page). PDF generation has
# no request object, so this can't be derived from the request; it must be a
# fixed setting. Prod is omni.alphadirect.co.bw.
PUBLIC_BASE_URL = config('PUBLIC_BASE_URL', default='https://omni.alphadirect.co.bw').rstrip('/')
# Claims automation (CFO plan 19-Sep-2026): client letters carry a DRAFT mark and
# are never emailed until the Claims Manager signs the wording off; then set true.
CLAIMS_LETTER_WORDING_APPROVED = config('CLAIMS_LETTER_WORDING_APPROVED', default=False, cast=bool)
# Salvage handed to Veritas (CFO 19-Sep-2026): we invoice them 20% of what we
# actually paid the client. The receivable already exists in the chart of accounts;
# the income account is Finance's call (Kago) — until it is named here, the charge
# is recorded and held, and NOTHING is posted.
CLAIMS_VERITAS_SALVAGE_RECEIVABLE_ACCOUNT = config('CLAIMS_VERITAS_SALVAGE_RECEIVABLE_ACCOUNT', default='260001')
CLAIMS_VERITAS_SALVAGE_INCOME_ACCOUNT = config('CLAIMS_VERITAS_SALVAGE_INCOME_ACCOUNT', default='')

# Who receives the 09:30 payment daily digest (taskboard/payment_daily_digest).
# Comma-separated. CFO 2026-08-12: widened from the CFO alone to Finance, so the
# people raising the payments see the same single email he does —
#   Pako Kago, Kago Tshutlhedi, Legakwa Ntabeni, Keetile Mokhendo.
# Deliberately here and not appended to the cron line: this survives a box
# rebuild, is reviewable in git, and is testable. Override per environment with
# PAYMENT_DIGEST_TO; the --to flag on the command still wins over both.
PAYMENT_DIGEST_TO = config(
    'PAYMENT_DIGEST_TO',
    default=(
        'pganesharajah@alphadirect.co.bw,'
        'pkago@alphadirect.co.bw,'
        'ktshutlhedi@alphadirect.co.bw,'
        'lntabeni@alphadirect.co.bw,'
        'kmokhendo@alphadirect.co.bw'
    ),
)

# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

THIRD_PARTY_APPS = [
    'rest_framework',
    'rest_framework.authtoken',
    'corsheaders',
    'axes',
]

LOCAL_APPS = [
    'core',
    'ledger',
    'billing',
    'bonu',
    'payments',
    'banking',
    'reporting',
    'equity',
    'finance_report',
    'integrations',
    'documents',
    'budgets',
    'assets',
    'records',
    'ifrs17',
    'claims',
    # Claims automation decision rules (Lindani Mababa's spec, 2026-09-08):
    # write-offs, premium confirmations, AOL, recoveries, Real Pay monitoring.
    # Pure logic — no models, no migrations.
    'claims_rules',
    'claims_automation',
    'payroll',
    'salvage',
    'procurement',
    'underwriting',
    'fx',
    'fx_planning',
    'exceptions',
    'petty_cash',
    'fnb',
    'regulatory',
    'reinsurance',
    'investments',
    'bank_feeds',
    'hris',
    'nbfira',
    'realpay',
    'healthcare',
    'licensing',
    'iso_compliance',
    'rewards',
    'nexus',
    'aware',
    'genric',
    'staff_rewards',
    'reconciliation_hub',
    'supplier_recon',
    'taskboard',
    'agent_portal',
    'recruitment',
    'leases',
    'commissions',
    'staff_loans',
    'internal_audit',
    'customer_refunds',
    'company_cards',
    # Large Payment Authorisation (CFO 2026-09-11) — packages claim payments that
    # have ALREADY been through the gated payment path into one CEO authorisation
    # request. Reads payments, writes none.
    'large_payments',
    'boardroom',
    'devlog',
    'ci_monitor',
    'jobs',
    'watchdog',
    'training',
    # Claims Life Cycle Tracker (CFO 21-Sep-2026, board items B1-B14) — the
    # weighted steps, the traffic light, the three clocks and the performance
    # dashboard. Every piece sits behind its own switch, all of them off.
    'claims_lifecycle',
    # Transformation Board — the CFO's four-month automation programme
    # (2026-09-20). Reads what Omni already knows; writes only progress.
    'transformation',
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ---------------------------------------------------------------------------
# Microsoft 365 / Entra ID — service principal used by the weekly
# `sync_m365_active_users` cron (Settings > M365 Active Users).
# Credentials live in /etc/alpha-finance/.env; never in code.
# ---------------------------------------------------------------------------
import json
import sys
import os as _os

# Omni Watchdog (CFO 2026-08-21). The nightly audit is report-only; safe
# auto-fixes stay OFF until deliberately armed. Dangerous findings (finance /
# permissions / data) are ALWAYS report-only regardless of this switch.
WATCHDOG_AUTOFIX_ENABLED = _os.environ.get('WATCHDOG_AUTOFIX_ENABLED', '').lower() in ('1', 'true', 'yes')

# Claims Life Cycle Tracker (CFO board item B14, 21-Sep-2026). The six claim
# events Graphite fires are ALWAYS accepted and stored at /api/v1/events/ — the
# door never rejects a real event. This switch decides whether Omni then ACTS on
# one: tasks, internal emails and draft letters. It ships OFF, and is armed in
# the premium wave on 1 October, never between 07:45 and 12:00.
CLAIMS_LIFECYCLE_FORWARD_EVENTS = _os.environ.get(
    'CLAIMS_LIFECYCLE_FORWARD_EVENTS', '').lower() in ('1', 'true', 'yes')

M365_TENANT_ID = _os.environ.get("M365_TENANT_ID", "")
M365_CLIENT_ID = _os.environ.get("M365_CLIENT_ID", "")
M365_CLIENT_SECRET = _os.environ.get("M365_CLIENT_SECRET", "")
M365_ACTIVE_CUTOFF_MONTHS = int(_os.environ.get("M365_ACTIVE_CUTOFF_MONTHS", "6"))
# CFO 19-Sep-2026: Omni switches off a leaver's Microsoft 365 account the day after the
# last working day (hris/m365_offboarding.py). 'off' = nothing; 'report' = HR/IT told
# what WOULD be switched off; 'enforce' = switched off. Needs the Graph app permission
# User.EnableDisableAccount.All (admin consent) before 'enforce' can work.
M365_LEAVER_DISABLE = _os.environ.get("M365_LEAVER_DISABLE", "off").strip().lower()
M365_NOTIFY_EMAILS = [e.strip() for e in _os.environ.get(
    "M365_NOTIFY_EMAILS",
    "hr@alphadirect.co.bw,ubutale@alphadirect.co.bw",
).split(",") if e.strip()]

# ---------------------------------------------------------------------------
# Vehicle Register (pool-car checkout / check-in — CFO/EXCO 2026-07-16).
# Damage-on-return alerts go to the fleet admin(s); the CFO/EXCO inbox is
# auto-CC'd by send_html_with_cfo_cc. The reception sign-off can be locked to
# named accounts once confirmed; an empty receptionist list means any
# authenticated staff member may sign (their identity is still recorded).
#
# VEHICLE_FLEET_ADMIN_EMAILS is now also the AUTHORISATION list, not just the
# alert recipients: only these people (plus superusers / omni administrators)
# may add or edit a vehicle, or release a car from maintenance — CFO directive
# 2026-07-26, "unami and Dorothy admin", after the odometer became the basis of
# the no-cash-for-fuel-without-a-booking rule. Everyone else still books cars
# out and checks them in normally. See nexus.vehicle_register._is_fleet_admin.
# ---------------------------------------------------------------------------
VEHICLE_FLEET_ADMIN_EMAILS = [e.strip() for e in _os.environ.get(
    "VEHICLE_FLEET_ADMIN_EMAILS",
    "ubutale@alphadirect.co.bw,dikgopoleng@alphadirect.co.bw",
).split(",") if e.strip()]
VEHICLE_RECEPTIONIST_EMAILS = [e.strip() for e in _os.environ.get(
    "VEHICLE_RECEPTIONIST_EMAILS", "",
).split(",") if e.strip()]
# Who may CORRECT a mistyped odometer reading on a trip (a controlled action —
# it can move the vehicle's current reading DOWN). Fleet admins can already;
# this list adds reception so the person who spots the typo can fix it. CFO
# 2026-07-27: "give her access to correct in future" (Wame reported the B 350
# BWX check-in typo). See nexus.vehicle_register._may_correct_odometer.
VEHICLE_ODOMETER_CORRECTOR_EMAILS = [e.strip() for e in _os.environ.get(
    "VEHICLE_ODOMETER_CORRECTOR_EMAILS",
    "wmatlhare@alphadirect.co.bw",
).split(",") if e.strip()]

# Vendor Onboarding (Health Care) — @alphadirect.co.bw local-parts allowed into
# the wizard (healthcare/permissions.IsVendorOnboarder). Env-overridable so
# testers can be added/removed without a deploy. CFO 2026-07-23: added Oprah
# (omogomotsi) alongside Ankete.
VENDOR_ONBOARDING_ALLOWED_LOCALPARTS = [p.strip().lower() for p in _os.environ.get(
    "VENDOR_ONBOARDING_ALLOWED_LOCALPARTS",
    "ankete,omogomotsi",
).split(",") if p.strip()]

# ---------------------------------------------------------------------------
# App-store reviewer demo account (CFO 2026-09-06). See core/review_demo.py.
# OMNI_REVIEW_MODE is the kill switch — OFF unless deliberately armed, and the
# whole feature stays inert unless the email AND the code are also set. Never
# put the real values in code; they live in /etc/alpha-finance/.env only.
# ---------------------------------------------------------------------------
OMNI_REVIEW_MODE = _os.environ.get('OMNI_REVIEW_MODE', '').strip().lower() in ('1', 'true', 'yes')
OMNI_REVIEW_EMAIL = _os.environ.get('OMNI_REVIEW_EMAIL', '').strip().lower()
OMNI_REVIEW_CODE = _os.environ.get('OMNI_REVIEW_CODE', '').strip()

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',     # serve static files in prod
    # CFO structural-audit directive 2026-05-19: admin paths must not be
    # reachable from arbitrary public IPs. Empty allowlist = no restriction
    # (back-compat for dev / pre-prod). On prod, set ADMIN_IP_ALLOWLIST in
    # /etc/alpha-finance/.env to a comma-separated list of office CIDRs.
    'core.admin_ip_allowlist.AdminIPAllowlistMiddleware',
    'corsheaders.middleware.CorsMiddleware',          # must be before CommonMiddleware
    # App-store reviewer demo data (CFO 2026-09-06). Inert unless
    # OMNI_REVIEW_MODE is armed AND the caller carries the review identity's
    # device token; then it serves invented fixtures and swallows every write
    # before it can reach a view. Sits after CORS so the canned responses still
    # get CORS headers, and before CSRF/Auth so a swallowed write never touches
    # the database.
    'core.review_demo.ReviewDemoMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    # LOCAL DOCKER: re-add the trailing slash the Next.js proxy strips, before
    # CommonMiddleware's APPEND_SLASH would raise on a slash-less POST.
    'core.api_slash_middleware.ApiTrailingSlashMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Internal-tasking presence — heartbeat last_seen for the requesting
    # user once a minute. Must run after AuthenticationMiddleware so
    # request.user is populated. Cheap: one throttled UPDATE per minute.
    'core.presence_middleware.PresenceHeartbeatMiddleware',
    # django-axes — must be LAST middleware in the chain so it can
    # observe the final authenticated-vs-failed state.
    'axes.middleware.AxesMiddleware',
]

ROOT_URLCONF = 'alpha_finance.urls'

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'alpha_finance.wsgi.application'

# ---------------------------------------------------------------------------
# Database
#   Default: PostgreSQL (used by docker-compose and AWS RDS in production)
#   Fallback: SQLite when DB_ENGINE=sqlite — for host-side local dev only
# ---------------------------------------------------------------------------
DB_ENGINE = config('DB_ENGINE', default='postgresql').lower()

if DB_ENGINE == 'sqlite':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': config('DB_NAME', default='alpha_finance'),
            'USER': config('DB_USER', default='alpha_admin'),
            'PASSWORD': config('DB_PASSWORD'),
            'HOST': config('DB_HOST', default='localhost'),
            'PORT': config('DB_PORT', default='5432'),
            # Persistent connections — reuse the same Postgres connection for
            # 60s instead of opening a new TCP+auth handshake on every request.
            # On a multi-call page (dashboard fires ~8) this saves ~hundreds of
            # ms of pure connection overhead. CONN_HEALTH_CHECKS guards against
            # serving a request on a dropped connection (Django 4.1+).
            'CONN_MAX_AGE': config('DB_CONN_MAX_AGE', default=60, cast=int),
            'CONN_HEALTH_CHECKS': True,
            'OPTIONS': {
                'connect_timeout': 10,
                # DPA L-4 (2026-07-19): TLS for the DB link. 'prefer' = encrypt if
                # the server supports it, else plaintext — safe for the same-host
                # in-container Postgres now; set DB_SSLMODE=require on any RDS move.
                'sslmode': config('DB_SSLMODE', default='prefer'),
            },
        }
    }

# ---------------------------------------------------------------------------
# Primary key
# All models inherit from core.models.BaseModel which sets uuid pk explicitly.
# ---------------------------------------------------------------------------
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL = '/admin/login/'
LOGIN_REDIRECT_URL = '/admin/'

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Gaborone'
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static & Media files
# ---------------------------------------------------------------------------
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
_static_dir = BASE_DIR / 'static'
STATICFILES_DIRS = [_static_dir] if _static_dir.exists() else []

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Upload caps — bumped for the AriaRatGLB pipeline. Meshy photoreal GLBs
# routinely exceed Django's 2.5 MB default. 250 MB covers heaviest cases
# the upload endpoint validates separately (200 MB hard cap in api_views).
DATA_UPLOAD_MAX_MEMORY_SIZE = 250 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5  * 1024 * 1024   # anything >5MB streams to disk

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

# A {% static %} reference missing from staticfiles.json (e.g. a request landing
# during the container-start `collectstatic --clear` window) must fall back to the
# plain path, never raise a 500. Turns the strict ManifestStaticFilesStorage
# ValueError into graceful degradation. The DRF login template at /api/auth/login/
# renders bootstrap.min.css unconditionally, so this unauthenticated surface must
# not hard-500. (nightly self-test 2026-09-01)
WHITENOISE_MANIFEST_STRICT = False

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        # Nexus staff bridge (CFO 2026-07-14): staff signed into the Alpha
        # Nexus app reuse the normal omni APIs with their customer token.
        # MUST precede AzureJWT — Azure raises on non-JWT Bearer tokens, which
        # would kill the chain; the bridge only claims tokens that exist in the
        # CustomerSession table and passes everything else through.
        'rewards.staff_bridge_auth.NexusStaffAuthentication',
        # Omni staff phone app: 30-day per-device session (CFO 2026-09-03).
        # Same ordering rule — must precede AzureJWT; claims only stored hashes.
        'core.device_auth.StaffDeviceAuthentication',
        # Azure AD Bearer JWT — dormant until AZURE_SSO_ENABLED=True.
        'core.azure_auth.AzureJWTAuthentication',
        # CFO directive 2026-05-19: permanent service-account API keys
        # for headless agents (Manus). Header form: Authorization: ApiKey <key>.
        'core.api_key_auth.ApiKeyAuthentication',
        'rest_framework.authentication.SessionAuthentication',
        # CFO directive 2026-07-18: DRF tokens now carry a 15h absolute expiry
        # (OMNI_TOKEN_TTL_HOURS) so sessions can't live forever.
        'core.token_auth.ExpiringTokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
        # ApiKeyScopePermission is permissive for non-ApiKey requests;
        # only enforces scope restriction when request.auth is an ApiKey.
        'core.api_key_auth.ApiKeyScopePermission',
    ],
    # CFO structural-audit directive 2026-05-19: a single capped pagination
    # class so no client can request `?page_size=999999` and OOM the API.
    # core.pagination.CappedPageNumberPagination enforces max_page_size=100.
    'DEFAULT_PAGINATION_CLASS': 'core.pagination.CappedPageNumberPagination',
    'PAGE_SIZE': 25,
    # SECURITY (2026-07-17 audit): the HTML BrowsableAPIRenderer is a handy
    # dev explorer but in prod it gives any token-holder a point-and-click UI
    # over the whole API. Enable it only when DEBUG is on.
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ] + (['rest_framework.renderers.BrowsableAPIRenderer'] if DEBUG else []),
    'DEFAULT_FILTER_BACKENDS': [
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    # Throttle rates (per IP for anon, per user for auth). Note: no
    # DEFAULT_THROTTLE_CLASSES set — each view opts in. Currently the only
    # opt-in is the login endpoint via core.auth_views.RateLimitedLoginView
    # to slow password brute-force without impacting other API surfaces
    # (webhooks, Telegram bot, internal services).
    'DEFAULT_THROTTLE_RATES': {
        'login': '10/minute',
        # A staff member's own data-deletion request emails IT — cap it so a
        # stuck finger cannot mail-bomb the team.
        'app-data-request': '5/hour',
        # Underwriting reader/render/issue — bound paid-LLM + headless-render
        # spend per user (cost discipline, CFO 2026-07-08).
        'underwriting': '40/minute',
        'underwriting-email': '6/minute',   # customer-facing quote send (security review 2026-09-04)
        'health-quote-email': '6/minute',   # customer-facing health quote send (Omni Mobile)
        # Alpha Nexus customer OTP (per-IP) — the request/verify endpoints are
        # public + AllowAny; cap email-bombing, member-row spam and OTP brute
        # force from a single source (2026-07-10 security sweep).
        'customer_otp': '20/minute',
        # Guessing a 6-char team join code (~1.07e9 combinations) — a hit would
        # put a stranger inside a family's Nexus numbers. Own bucket so it
        # cannot drain the sign-in/pairing allowance.
        'team_join': '20/minute',
        # Public "check my claim status" line (per-IP) — request/verify are
        # AllowAny; cap email-bombing customers + OTP brute force from one source.
        'claim_status': '20/minute',
        # Graphite -> Omni analytics push: anonymous, internet-facing and it
        # WRITES, so it gets a ceiling. Blunts online token guessing and log
        # spam. (The intel feed has none, but the intel feed only reads.)
        'graphite_ingest': '30/minute',
        # "Snap anything" photo classify (per user) — bounds paid-vision spend;
        # a person cannot snap 20+ photos a minute by hand anyway (CFO 2026-09-03).
        'snap': '20/minute',
    },
    'COERCE_DECIMAL_TO_STRING': True,
}

# ---------------------------------------------------------------------------
# django-axes — brute-force lockout for Django auth (admin + DRF)
# ---------------------------------------------------------------------------
# Protects /admin/login/, /api/auth/login/, and any other view that goes
# through django.contrib.auth.authenticate(). DRF token endpoint already
# has IP-rate-limit via core.auth_views.RateLimitedLoginView; axes adds a
# stricter username-or-IP based LOCKOUT after repeated failures.
AUTHENTICATION_BACKENDS = [
    # AxesStandaloneBackend MUST be first — short-circuits attempts
    # while a lock is active, before the real auth runs.
    'axes.backends.AxesStandaloneBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# 5 failures from the same (username, IP) pair within 30 minutes → lock
# for 30 minutes. Conservative numbers — easy to relax later if needed.
AXES_FAILURE_LIMIT       = 5
AXES_COOLOFF_TIME        = 0.5         # hours (= 30 minutes)
AXES_LOCKOUT_PARAMETERS  = [['username', 'ip_address']]  # lock per (user, IP)
AXES_RESET_ON_SUCCESS    = True        # successful login clears the counter
AXES_LOCK_OUT_AT_FAILURE = True
# Behind Caddy reverse proxy — trust X-Forwarded-For for real client IP.
# SEC-04 (security assessment 2026-08-06): axes used to key the lockout on
# X-Forwarded-For, which the CALLER sets. Rotating that header handed an attacker
# a fresh five attempts every time, so the 5-strike lockout was decorative.
# core/staff_login_views.py:63-68 had already learned this for the login throttle
# and switched to Cloudflare's CF-Connecting-IP — which cannot be spoofed past
# Cloudflare — but the lesson was never carried across to the lockout. It is now.
# No PROXY_COUNT: that setting only applies to the X-Forwarded-For chain we no
# longer read, and leaving it set would silently reintroduce the chain parsing.
AXES_IPWARE_META_PRECEDENCE_ORDER = ['HTTP_CF_CONNECTING_IP', 'REMOTE_ADDR']
AXES_VERBOSE             = True        # log each failed attempt
AXES_LOCKOUT_TEMPLATE    = None        # default plain 403 (no info leak)

# ---------------------------------------------------------------------------
# CORS (django-cors-headers)
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = config(
    'CORS_ALLOWED_ORIGINS',
    default='http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000,http://127.0.0.1:3001',
    cast=Csv(),
)
CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = config(
    'CSRF_TRUSTED_ORIGINS',
    default='http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000,http://127.0.0.1:3001',
    cast=Csv(),
)

# ---------------------------------------------------------------------------
# Production security hardening
# All flags default to safe-for-dev values; .env in production flips them on.
# ---------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = config('SECURE_SSL_REDIRECT', default=False, cast=bool)
# ...but never while running tests. The suite is run with the PRODUCTION env file,
# so this was True, and SecurityMiddleware then 301-redirected every test-client
# request to https before it reached any view. That is not a cosmetic problem: it
# turned 158 of the 160 'failures' in the core suite into one configuration
# artefact, and a permanently-red suite cannot tell anyone that something real
# just broke. Client(secure=True) does NOT rescue it either — SECURE_PROXY_SSL_HEADER
# is set, so Django reads X-Forwarded-Proto and ignores the client's url_scheme.
# Verified 6 Aug 2026: core went from 160 failures to 2 with this single line.
if 'test' in sys.argv or 'pytest' in sys.argv[0]:
    SECURE_SSL_REDIRECT = False
# Secure-by-default: cookies HTTPS-only. Local dev can override via .env.
SESSION_COOKIE_SECURE = config('SESSION_COOKIE_SECURE', default=True, cast=bool)
CSRF_COOKIE_SECURE = config('CSRF_COOKIE_SECURE', default=True, cast=bool)
SECURE_HSTS_SECONDS = config('SECURE_HSTS_SECONDS', default=0, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = config('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=False, cast=bool)
SECURE_HSTS_PRELOAD = config('SECURE_HSTS_PRELOAD', default=False, cast=bool)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False  # frontend reads csrftoken via JS

# ---------------------------------------------------------------------------
# Multi-currency configuration
# ---------------------------------------------------------------------------
DEFAULT_CURRENCY = config('DEFAULT_CURRENCY', default='BWP')
SUPPORTED_CURRENCIES = config('SUPPORTED_CURRENCIES', default='BWP,USD,ZAR,INR,ZMW', cast=Csv())

CURRENCY_DISPLAY = {
    'BWP': {'name': 'Botswana Pula',      'symbol': 'P',   'decimal_places': 2},
    'USD': {'name': 'US Dollar',           'symbol': '$',   'decimal_places': 2},
    'ZAR': {'name': 'South African Rand',  'symbol': 'R',   'decimal_places': 2},
    'INR': {'name': 'Indian Rupee',        'symbol': '₹',   'decimal_places': 2},
    'ZMW': {'name': 'Zambian Kwacha',      'symbol': 'ZK',  'decimal_places': 2},
}

# ---------------------------------------------------------------------------
# Alpha Direct — application-level constants
# ---------------------------------------------------------------------------
COMPANY_NAME = 'Alpha Direct Insurance'
COMPANY_COUNTRY = 'BW'
FINANCIAL_YEAR_START_MONTH = 7   # July (Botswana fiscal year: July–June)
MONETARY_DECIMAL_PLACES = 2
MONETARY_MAX_DIGITS = 18

# ---------------------------------------------------------------------------
# VAT — Reverse Charge on Imported Remote Services
# (Botswana VAT Amendment Act No.16 of 2025, effective 1 June 2026)
# ---------------------------------------------------------------------------
# Self-assessed VAT rate on foreign digital services (AWS, Anthropic, etc.)
# captured via billing.ReverseChargeEntry — see reporting.reports.build_vat_return
# for how the self-assessed output/input legs surface on the VAT return.
# Same 14% as the standard rate today, but kept as its own env-configurable
# constant — NEVER hardcode 0.14 in the model/view/serializer layer — so a
# future BURS rate change is a one-line env-var edit, not a code change.
RC_VAT_RATE = Decimal(config('RC_VAT_RATE', default='0.14'))

# VAT control accounts in the general ledger, read by the VAT reconciliation
# (regulatory/vat_recon.py) to tie the return back to the ledger. Comma-
# separated GL codes, env-configurable so a chart-of-accounts change is a
# settings edit rather than a code change.
#
# 🔴 These say which accounts the reconciliation READS. They are NOT a GL
# mapping: nothing posts to them because of this setting, and changing one
# cannot move a transaction. Which account a document POSTS to is decided
# elsewhere and is frozen pending CFO sign-off (build spec B2 hard stop).
#
# The defaults are NOT chosen here. They come from ledger/vat_accounts.py, the
# single definition of the VAT control accounts that billing/models.py also
# posts through (CFO 2026-09-14: the reconciliation must tie to the same
# accounts the return uses, and the two must never be able to disagree). A code
# is never written twice, so the two cannot drift; regulatory/tests/
# test_vat_recon.py asserts that what posts is what is read.
#
# 🔴 OPEN FOR THE CFO: the 6-digit chart (ops/seeds/seed_coa_v2.py:203) also
# carries 209001 'VAT', which trial-balance imports may post to. If it does hold
# VAT, add it here — `VAT_OUTPUT_CONTROL_ACCOUNTS=2180,209001`. It is left out of
# the default because nothing in the code posts VAT to it, and this must not be
# settled by guessing at live data. An earlier draft of this setting defaulted to
# 209001/132000; 132000 was wrong outright — it comes from
# ledger/invoice_parser.py, the AI invoice-SUGGESTION parser, which posts nothing.
# A configured code that is not in the chart is now reported as VAT-TIE-04
# rather than silently skipped.
from ledger.vat_accounts import (                              # noqa: E402
    VAT_INPUT_CONTROL_ACCOUNTS as _VAT_INPUT_DEFAULT,
    VAT_OUTPUT_CONTROL_ACCOUNTS as _VAT_OUTPUT_DEFAULT,
)

VAT_OUTPUT_CONTROL_ACCOUNTS = [
    c.strip() for c in
    config('VAT_OUTPUT_CONTROL_ACCOUNTS',
           default=','.join(_VAT_OUTPUT_DEFAULT)).split(',')
    if c.strip()
]
VAT_INPUT_CONTROL_ACCOUNTS = [
    c.strip() for c in
    config('VAT_INPUT_CONTROL_ACCOUNTS',
           default=','.join(_VAT_INPUT_DEFAULT)).split(',')
    if c.strip()
]

# ── GENRIC monthly reporting pack (B3) ─────────────────────────────
# The pay window before cancellation is NO LONGER A DJANGO SETTING. The CFO
# answered it on 14 Sep 2026 (30 days, matching the 30-day statement terms) and
# it now lives in the database as the 'genric.unpaid_days_before_cancellation'
# row of genric.models.GenricSetting, edited on screen in Omni Admin — which
# needs is_staff, and today that is the CFO alone (see genric/config.py).
#
# DO NOT reinstate GENRIC_UNPAID_DAYS_BEFORE_CANCELLATION here. Two sources for
# one control is how the screen and the report end up disagreeing about which
# number produced the figures; an env var also cannot be changed without a
# release, which is the whole reason it moved. See genric/config.py.

# 🔴 GENRIC's own bank details — where Alpha Direct PAYS the net reinsurance
# premium. CFO ruling 13 Sep 2026: this is a quota-share cession, so the cedant
# pays the reinsurer. The first build had the invoice inverted, printing Alpha
# Direct's own FNB account as "PAYABLE TO".
#
# DELIBERATELY NO DEFAULT, for the same reason as the pay window above: a
# guessed reinsurer account number pays a stranger. Unset, the invoice prints
# "NOT ON FILE, DO NOT PAY AGAINST THIS DOCUMENT" and names this setting; it
# never falls back to an Alpha Direct account. One line: bank, account number,
# branch and SWIFT, exactly as GENRIC gave them.
GENRIC_REINSURER_BANK_DETAILS = config(
    'GENRIC_REINSURER_BANK_DETAILS', default=None)

# M-3 (Fable-5 review): reverse charge only applies to invoices dated on or
# after this date — the Act takes effect 1 June 2026, not retroactively.
# Env-configurable (never hardcode) so a future statutory change is a
# one-line env-var edit, matching the RC_VAT_RATE convention above.
RC_VAT_EFFECTIVE_DATE = config(
    'RC_VAT_EFFECTIVE_DATE', default='2026-06-01', cast=date.fromisoformat,
)

# ---------------------------------------------------------------------------
# AI Document Processing (Anthropic Claude API)
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = config('ANTHROPIC_API_KEY', default='')
ANTHROPIC_MODEL   = config('ANTHROPIC_MODEL', default='claude-sonnet-4-6')
AI_CONFIDENCE_THRESHOLD = 0.75  # Below this, escalate to AI layer

# ---------------------------------------------------------------------------
# DeepSeek — non-sensitive reasoning tasks
# ---------------------------------------------------------------------------
# Approved by CFO 2026-05-09 as an explicit exception to AD-POL-AI-GOV-001.
# DeepSeek is used ONLY for reasoning tasks where no sensitive data leaves
# the system: chart-of-accounts suggestions on free-text descriptions,
# journal-entry classification hints, anomaly heuristics over aggregates.
# core.ai_assist.is_safe_for_ai() must filter every prompt before send.
DEEPSEEK_API_KEY  = config('DEEPSEEK_API_KEY',  default='')
DEEPSEEK_API_BASE = config('DEEPSEEK_API_BASE', default='https://api.deepseek.com')
DEEPSEEK_MODEL    = config('DEEPSEEK_MODEL',    default='deepseek-chat')
# CFO directive 2026-07-19: kill-switch for DeepSeek (China). When false, every
# DeepSeek call falls back to Gemini (also PII-firewalled) so the work continues
# — flip once the local server (or full Gemini cut-over) is ready. See
# core.ai_assist.deepseek_complete.
DEEPSEEK_ENABLED  = config('DEEPSEEK_ENABLED',  default=True, cast=bool)

# PII firewall (core.pii_firewall) — CFO directive 2026-07-18 (Option B, post DPA
# audit). Every external-LLM call passes through a reversible tokeniser so no
# identifiable personal data leaves in the clear. Modes: off | monitor | tokenize
# | block (see core/pii_firewall.py). Rollback = set to 'off'.
#
# Default is 'tokenize', NOT 'monitor'. Monitor detects and logs but still sends
# the ORIGINAL text, so an environment that loses this variable would quietly go
# back to putting customer data in the clear on an external model. Production was
# verified on 'tokenize' (8 Aug 2026); the default now matches it, so a dropped
# env var fails safe instead of fails open.
PII_FIREWALL_MODE = config('PII_FIREWALL_MODE', default='tokenize')

# Biometric face-match (AWS Rekognition) — OFF. DPA audit H-9: biometrics are
# special-category data and must not run without a completed DPIA + consent.
# BOTH flags must be set to activate (see core.face_verify.rekognition.is_enabled).
REKOGNITION_ENABLED      = config('REKOGNITION_ENABLED', default=False, cast=bool)
FACE_MATCH_DPIA_APPROVED = config('FACE_MATCH_DPIA_APPROVED', default=False, cast=bool)

# ---------------------------------------------------------------------------
# Recruitment external AI CV screening (CFO directive 2026-08-25)
# ---------------------------------------------------------------------------
# OFF by default per AD-POL-AI-GOV-001. When True, recruitment/ai_analysis.py
# runs an AI assessment (fit score, summary, strengths, gaps, interview
# questions) over each CV — AFTER redacting the known identity and passing the
# text through core.ai_assist.is_safe_for_ai() + the PII firewall. Scoring uses
# the cheap-first reasoning chain, which reaches DeepSeek before Gemini. The
# deterministic local match score remains the primary ranking engine.
RECRUITMENT_EXTERNAL_AI = config('RECRUITMENT_EXTERNAL_AI', default=False, cast=bool)

# ---------------------------------------------------------------------------
# Local Ollama — FREE tier-0 for reasoning_complete (CFO directive 2026-07-18)
# ---------------------------------------------------------------------------
# "Review locally first, escalate to cloud if it can't." A local Ollama model
# (e.g. deepseek-r1) answers FIRST when reachable; on a blank/weak answer or if
# the box has no local model, the cascade falls through to cloud DeepSeek → premium.
# OLLAMA_API_BASE is EMPTY by default, so on the cloud server (which cannot reach a
# laptop's Ollama) this tier auto-skips and behaviour is pure cloud cheap-first.
# Set OLLAMA_API_BASE=http://localhost:11434/v1 on a machine that runs Ollama.
OLLAMA_API_BASE = config('OLLAMA_API_BASE', default='')

# Graphite -> Omni analytics feed (CFO 2026-08-06). Shared bearer token for
# integrations.graphite_ingest. EMPTY BY DEFAULT and the endpoint fails closed on
# an empty value, so the feed stays shut until the key is deliberately set.
GRAPHITE_INGEST_TOKEN = config('GRAPHITE_INGEST_TOKEN', default='')
OLLAMA_MODEL    = config('OLLAMA_MODEL',    default='deepseek-r1:14b')

# ---------------------------------------------------------------------------
# Gemini — backup reasoning engine (CFO directive 2026-06-12)
# ---------------------------------------------------------------------------
# Same non-sensitive-reasoning carve-out as DeepSeek: used ONLY as a fallback
# when DeepSeek is unavailable, and ONLY on prompts already passed through
# core.ai_assist.is_safe_for_ai(). Uses Google's OpenAI-compatible endpoint so
# the client mirrors deepseek_complete.
GEMINI_API_KEY  = config('GEMINI_API_KEY',  default='')
GEMINI_API_BASE = config('GEMINI_API_BASE', default='https://generativelanguage.googleapis.com/v1beta/openai')
GEMINI_MODEL    = config('GEMINI_MODEL',    default='gemini-2.5-flash')

# ---------------------------------------------------------------------------
# Grok / xAI — 4th reasoning engine (CFO directive 2026-06-18)
# ---------------------------------------------------------------------------
# Same non-sensitive-reasoning carve-out (is_safe_for_ai filter). xAI's
# OpenAI-compatible endpoint, so grok_complete mirrors the others. Keys are
# managed in the CFO Secrets Vault (Settings → Secrets); env is the fallback.
GROK_API_KEY  = config('GROK_API_KEY',  default='')
GROK_API_BASE = config('GROK_API_BASE', default='https://api.x.ai/v1')
GROK_MODEL    = config('GROK_MODEL',    default='grok-2-latest')

# ---------------------------------------------------------------------------
# Break-glass admin login (CFO directive 2026-06-16)
# ---------------------------------------------------------------------------
# Comma-separated emails allowed to use the non-SSO /break-glass password login
# (POST /api-token-auth/). Lets the CFO + key admins in even when Microsoft SSO
# is down. Blank -> falls back to staff/superuser only. These accounts must have
# a real Django password set (manage.py changepassword).
BREAK_GLASS_EMAILS = config('BREAK_GLASS_EMAILS', default='')

# Who may RELEASE a staff loan (CFO 16-Sep-2026). Named accounts, not job
# titles: role-based let Internal Audit and a shared test login near loan money.
# Set this to change Finance without a code change — comma-separated emails.
STAFF_LOAN_RELEASE_EMAILS = config('STAFF_LOAN_RELEASE_EMAILS', default='')

# ---------------------------------------------------------------------------
# Azure AD SSO (P4 — dormant until IT provides app registrations)
# ---------------------------------------------------------------------------
# Setup: see infra/azure-sso-setup.md
# AZURE_SSO_ENABLED defaults to False — the AzureJWTAuthentication class
# short-circuits and returns None for every request, so DRF falls through
# to Token / Session auth and existing flows keep working unchanged.
AZURE_SSO_ENABLED     = config('AZURE_SSO_ENABLED',     default=False, cast=bool)
AZURE_TENANT_ID       = config('AZURE_TENANT_ID',       default='')
AZURE_API_CLIENT_ID   = config('AZURE_API_CLIENT_ID',   default='')
AZURE_API_AUDIENCE    = config('AZURE_API_AUDIENCE',    default='')
AZURE_ADMIN_GROUP_ID  = config('AZURE_ADMIN_GROUP_ID',  default='')

# CFO rule 19-Sep-2026 "No payroll, no Omni" (core/login_gate.py). 'report' =
# a first Microsoft sign-in by someone not on payroll is logged + HR emailed but
# allowed; 'enforce' = refused. UniCoin agents (insurance.co.bw) not on payroll
# are refused in BOTH modes. Planned switch to 'enforce': 27-Sep-2026.
SSO_NEW_LOGIN_POLICY  = config('SSO_NEW_LOGIN_POLICY',  default='report')

# ELRA-2025 monthly performance check-in + PIP module (hris/performance_views.py).
# Employee personal data — kept off until the CFO (data controller) switches it
# on. CFO directive 2026-07-02: enabled. Set ELRA_PERF_ENABLED=True in the env.
ELRA_PERF_ENABLED     = config('ELRA_PERF_ENABLED',     default=False, cast=bool)

# Days past a task's due date before it is "long overdue" and an application
# for leave / a loan / an incentive needs a CEO or CFO countersignature.
# CFO 2026-08-07: 2. Exposed as an env var so the threshold can be retuned
# without a code change — and listed in docker-compose.yml, because the compose
# env block is an ALLOW-LIST: a variable set in .env but missing there never
# reaches the container (that is how the intel feed silently died).
HRIS_OVERDUE_BLOCK_DAYS = config('HRIS_OVERDUE_BLOCK_DAYS', default=2, cast=int)

# SEC-02 (CFO 2026-08-08). "No entity restriction recorded" used to mean "may
# see all 13 companies". It now means "your own entity". HR and the exec team
# keep the consolidated view via the 'view_all' capability. Set to False to
# restore the old permissive default immediately if something legitimate is
# hidden — no deploy needed, just the env var and a restart.
OMNI_ENTITY_SCOPE_STRICT = config('OMNI_ENTITY_SCOPE_STRICT', default=True, cast=bool)

# Who may upload company credit-card spending (CFO 2026-08-07): Arun Iyer,
# Arjun Parameswaran, Paul Beka, Prathap Ganesharajah. Comma-separated so a
# card can be handed over without a code change. Being listed grants nothing on
# its own — every post is still checked against a CompanyCard row.
COMPANY_CARD_HOLDERS = [
    e.strip() for e in config(
        'COMPANY_CARD_HOLDERS',
        default='aiyer@alphadirect.co.bw,arjuniyer@alphadirect.co.bw,'
                'pbeka@alphadirect.co.bw,pganesharajah@alphadirect.co.bw'
    ).split(',') if e.strip()
]

# ---------------------------------------------------------------------------
# Linker — third-party real-time alerting webhook for the Exceptions engine
# ---------------------------------------------------------------------------
# When BOTH LINKER_API_KEY and LINKER_API_BASE are set, every new exception
# raised by exceptions.services.create_exception() is POSTed to LINKER_API_BASE
# with a Bearer token. Configure to whatever channel you want — Slack incoming
# webhook, Telegram bot proxy, WhatsApp Business API, generic webhook receiver.
# Both unset = no-op (the in-system /exceptions queue still works).
LINKER_API_KEY  = config('LINKER_API_KEY',  default='')
LINKER_API_BASE = config('LINKER_API_BASE', default='')

# ---------------------------------------------------------------------------
# FNB Botswana banking integration
# ---------------------------------------------------------------------------
# All TBD until Boitumelo (FNB Botswana RM) sends the API spec. Once we have
# it, drop the values into .env and the integration goes live without code
# changes — the path mappers in fnb/endpoints.py and the payload mappers in
# fnb/{statements,payments,beneficiaries}.py are the only swap-ins.
#
# Auth modes supported by fnb.client.FNBClient:
#   oauth2_client_credentials   — needs FNB_AUTH_URL + FNB_CLIENT_ID + FNB_CLIENT_SECRET
#   api_key                     — needs FNB_API_KEY
#   mtls                        — needs FNB_TLS_CERT_PATH + FNB_TLS_KEY_PATH
FNB_API_BASE         = config('FNB_API_BASE',         default='')
FNB_AUTH_MODE        = config('FNB_AUTH_MODE',        default='oauth2_client_credentials')
FNB_AUTH_URL         = config('FNB_AUTH_URL',         default='')
FNB_OAUTH_SCOPE      = config('FNB_OAUTH_SCOPE',      default='i_can')
FNB_CLIENT_ID        = config('FNB_CLIENT_ID',        default='')
FNB_CLIENT_SECRET    = config('FNB_CLIENT_SECRET',    default='')
FNB_API_KEY          = config('FNB_API_KEY',          default='')
FNB_TLS_CERT_PATH    = config('FNB_TLS_CERT_PATH',    default='')
FNB_TLS_KEY_PATH     = config('FNB_TLS_KEY_PATH',     default='')
# BUG 55e44cbc — the one-shot "quick transfer" bypasses maker-checker and takes a
# free-text beneficiary. OFF by default; only enable after the CFO signs off a
# proper control model (Vendor Bank register + second-approver + CFO threshold).
FNB_QUICK_TRANSFER_ENABLED = config('FNB_QUICK_TRANSFER_ENABLED', default=False, cast=bool)
FNB_WEBHOOK_SECRET   = config('FNB_WEBHOOK_SECRET',   default='')
# Per-batch ceiling on what one EFT submission may move (BWP). Enforced in
# fnb.payments.submit_eft_batch, which every path to the bank goes through.
# BWP 2,000,000, chosen by the CFO 2026-08-20. A typo-catcher rather than a
# brake: an extra zero on a real run is stopped, ordinary runs pass.
FNB_BATCH_MAX_BWP    = config('FNB_BATCH_MAX_BWP',    default='2000000')
# Up to this amount a Graphite engine refund still loads to the bank on a
# single actor (CFO 2026-08-20). BWP 5,000 mirrors Graphite's own
# one-approver/two-approver line, so there is one definition of a material
# refund. Above it the refund stages and waits for a second person.
REFUND_AUTOLOAD_MAX_BWP = config('REFUND_AUTOLOAD_MAX_BWP', default='5000')

# ── Payment loading window, PAY-WIN-02 (CFO 2026-09-01) ─────────────────────
# The old morning loading window (Africa/Gaborone). ABOLISHED 2026-09-02: a raise
# outside these times is NO LONGER blocked — payments can be raised at any time.
# These times now only tell the create leg whether a raise was off-window, so a
# non-CFO off-window raise can be flagged as a planning concern in the raiser's
# monthly performance feedback. Times are HH:MM, 24-hour; blank/invalid falls back
# to the defaults below. Sign-off was never windowed.
PAYMENT_LOAD_WINDOW_OPEN = config('PAYMENT_LOAD_WINDOW_OPEN', default='08:00')
PAYMENT_LOAD_WINDOW_CLOSE = config('PAYMENT_LOAD_WINDOW_CLOSE', default='09:15')

# ── Payment request -> FNB (CFO 2026-08-20) ─────────────────────────────────
# Finance signs a request off and Omni loads it into FNB, so the team never
# types the same payment twice. The money does not move on this: it lands in the
# CFO's FNB queue and he authorises it on his phone with two-factor.
PAYMENT_REQUEST_AUTO_FNB = config('PAYMENT_REQUEST_AUTO_FNB', default=True, cast=bool)
# The account number payments go OUT of. No default on purpose - guessing which
# bank account to debit is not a guess worth making, and several accounts on
# record have no number set at all. Unset means no automatic load, said plainly
# on the request.
# Which of our accounts pays which KIND of request. CFO 2026-08-21: "claims
# payments will go through alpha Direct claims accounts." Claims leave the
# claims account, everything else the operating account — a single global
# account would have paid claims out of the operating one.
# JSON: {"claim": "<claims account>", "default": "<cheque account>"} — the real
# numbers are set on the server, never committed. A category with no entry and
# no default refuses the load rather than guessing which account to debit.
try:
    PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS = json.loads(
        config('PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS', default='{}') or '{}')
    if not isinstance(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS, dict):
        raise ValueError('must be a JSON object')
except Exception as _src_err:              # noqa: BLE001
    import logging as _logging2
    _logging2.getLogger(__name__).error(
        'PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS is not valid JSON (%s) — treating '
        'it as empty, so no payment request will auto-load until it is fixed.',
        _src_err)
    PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS = {}
# A payment request's `entity` is free text and does not always match a Company
# name ("Alpha Direct Insurance Company South Africa" vs "Alpha Direct South
# Africa"). Exact matches only, so map the rest here rather than letting a fuzzy
# match pay from the wrong entity's account. JSON: {"entity name": "COMPANYCODE"}
# Parsed defensively: a malformed value here would raise at import time and
# crash-loop EVERY container at boot. An empty map fails safe — loads then
# refuse with a readable reason — so a settings typo must never take Omni down.
try:
    PAYMENT_REQUEST_ENTITY_ALIASES = json.loads(
        config('PAYMENT_REQUEST_ENTITY_ALIASES', default='{}') or '{}')
    if not isinstance(PAYMENT_REQUEST_ENTITY_ALIASES, dict):
        raise ValueError('must be a JSON object')
except Exception as _alias_err:            # noqa: BLE001
    import logging as _logging
    _logging.getLogger(__name__).error(
        'PAYMENT_REQUEST_ENTITY_ALIASES is not valid JSON (%s) — treating it as '
        'empty. Payment requests whose entity needs an alias will refuse to '
        'auto-load until it is fixed.', _alias_err)
    PAYMENT_REQUEST_ENTITY_ALIASES = {}
FNB_WEBHOOK_HEADER   = config('FNB_WEBHOOK_HEADER',   default='X-FNB-Signature')
FNB_TIMEOUT_SECONDS  = config('FNB_TIMEOUT_SECONDS',  default='15')

# ISO 20022 / pain.001 fields stamped on every outbound CustomerCreditTransferInitiation.
# Populate from /etc/alpha-finance/.env on prod with the values FNB Botswana confirms.
FNB_INITIATING_PARTY_NAME = config('FNB_INITIATING_PARTY_NAME', default='Alpha Direct Insurance')
FNB_DEBTOR_BIC            = config('FNB_DEBTOR_BIC',            default='FIRNBWGX')
FNB_DEBTOR_BRANCH_ID      = config('FNB_DEBTOR_BRANCH_ID',      default='')
FNB_DEBTOR_ACCOUNT_TYPE   = config('FNB_DEBTOR_ACCOUNT_TYPE',   default='CACC')
FNB_DEBTOR_ACCOUNT_NUMBER = config('FNB_DEBTOR_ACCOUNT_NUMBER', default='63001966639')

# ── MIS customer refunds (Graphite → Omni → FNB), CFO directive 2026-07-24 ──
# Graphite owns the intake/AI/Motlatsi approval; Omni owns the money leg.
# REFUND_INBOUND_TOKEN authenticates Graphite's server-to-server handoff.
# The refund FNB send has its OWN switch. It used to share
# FNB_QUICK_TRANSFER_ENABLED, which also opens the type-in-any-account quick
# transfer screen — so arming refunds opened that side door too (Pramod Bisen,
# 2026-09-02). Split 2026-09-11: this arms refunds alone, and stays OFF until
# every already-hand-paid refund is marked in Graphite, or they load twice.
REFUND_FNB_SEND_ENABLED     = config('REFUND_FNB_SEND_ENABLED', default=False, cast=bool)
# Alpha Direct Current Account — the account refunds are actually paid from
# (Keetile Mokhendo 2026-09-11, CFO confirmed). Deliberately NOT
# FNB_DEBTOR_ACCOUNT_NUMBER, which serves supplier and payroll runs.
REFUND_FNB_DEBIT_ACCOUNT_NUMBER = config('REFUND_FNB_DEBIT_ACCOUNT_NUMBER',
                                         default='62403392335')
REFUND_INBOUND_TOKEN        = config('REFUND_INBOUND_TOKEN', default='')
# Shared HMAC key for the bank-account blind index (account_fingerprint).
# Graphite computes the SAME fingerprint with the SAME key so that one bank
# account paid out under two different customer names is caught across both
# systems. It MUST be declared here: account_fingerprint() reads it off
# settings, so an environment variable alone never reaches it and the
# function silently falls back to SECRET_KEY — keying us differently from
# Graphite with no error anywhere (Pramod Bisen, 2026-09-11). Blank keeps
# the old SECRET_KEY behaviour, so an unconfigured deployment is unchanged.
REFUND_ACCOUNT_INDEX_KEY    = config('REFUND_ACCOUNT_INDEX_KEY', default='')
CUSTOMER_REFUND_COMPANY_CODE = config('CUSTOMER_REFUND_COMPANY_CODE', default='ADIC')
CUSTOMER_REFUND_BANK_GL_CODE = config('CUSTOMER_REFUND_BANK_GL_CODE', default='')
# Outbound callback so Graphite can post the refund to the policy + flip the
# portal flag once paid. Dormant until both are set (never crashes).
GRAPHITE_REFUND_CALLBACK_URL   = config('GRAPHITE_REFUND_CALLBACK_URL',   default='')
GRAPHITE_REFUND_CALLBACK_TOKEN = config('GRAPHITE_REFUND_CALLBACK_TOKEN', default='')

# WS1 outbound state bus (integrations.outbound). Kill switch for ALL write-backs
# to Graphite: when False, OutboundEvents still enqueue (nothing is lost) but
# nothing is sent — the bus can be stopped without a redeploy. Default on.
OUTBOUND_BUS_ENABLED = config('OUTBOUND_BUS_ENABLED', default=True, cast=bool)

# ── Omni → Alpha Brain aggregate feed (CFO directive 2026-07-25) ─────────────
# GET /api/v1/intel/summary/ serves counts + GL totals ONLY (no customer rows)
# to Alpha Brain, authenticated by this shared bearer token. Empty = endpoint is
# shut: token_ok() fails closed, so an unconfigured deployment serves nobody.
INTEL_SUMMARY_TOKEN = config('INTEL_SUMMARY_TOKEN', default='')
# Optional second wall — comma-separated EXACT caller addresses. Empty = no
# address restriction. Pin this to Alpha Brain's own private address once its
# internal load balancer target is known, so a leaked token is not enough on its
# own. Do NOT use a broad prefix like '172.31.': behind Cloudflare/Caddy that
# matches every request and admits everyone while appearing locked.
INTEL_SUMMARY_ALLOWED_IPS = config('INTEL_SUMMARY_ALLOWED_IPS', default='')
# Which entity the token feed may see. CFO 2026-07-26: "Brain sees ADIC." The
# caller cannot widen this — asking for another company, or omitting the
# parameter to get the rollup, is overridden. Set to 'ALL' to allow the rollup.
INTEL_SUMMARY_COMPANY = config('INTEL_SUMMARY_COMPANY', default='ADIC')

# 16th-of-month commission/incentive lock (core/payroll_deadline.py). OFF since
# CFO 19-Sep-2026 so staff can load their own; set true to switch it back on.
SUBMISSION_DEADLINE_ENFORCED = config('SUBMISSION_DEADLINE_ENFORCED', default=False, cast=bool)

# ── Payroll → FNB salary auto-load (CFO directive 2026-07-24) ───────────────
# MASTER SWITCH. Off by default: the /load-to-fnb action only PREVIEWS until a
# real end-to-end test is signed off. When True, a dry_run=false load POSTs the
# salary bulk to FNB (which the CFO still approves manually inside FNB).
PAYROLL_FNB_LOAD_ENABLED       = config('PAYROLL_FNB_LOAD_ENABLED', default=False, cast=bool)
# ADIC scoping — the only entity we hold an FNB payment link for.
PAYROLL_FNB_COMPANY_CODE       = config('PAYROLL_FNB_COMPANY_CODE', default='ADIC')
PAYROLL_FNB_SOURCE_ACCOUNT_NUMBER = config('PAYROLL_FNB_SOURCE_ACCOUNT_NUMBER', default='62403392335')
PAYROLL_FNB_SERVICE_LEVEL      = config('PAYROLL_FNB_SERVICE_LEVEL', default='SDVA')

# ── Payment-request submission window — ABOLISHED 2026-08-21 ─────────────────
# PAY-WIN-01 (07:00–09:30 Africa/Gaborone, CFO 2026-07-29) blocked anyone but
# the CFO from raising a payment request after 09:30, and from 2026-08-04 also
# blocked finance from signing one through to the CFO. Removed in full at the
# CFO's instruction: "we have a blocker, people can't request payments after
# 9.30am. I would like that rule to be abolished, people can request payment
# anytime they want." PAYMENT_WINDOW_OPEN / PAYMENT_WINDOW_CLOSE are gone with
# it — do not reintroduce a time gate here without a new directive.

# ─────────────────────────────────────────────────────────────────────────────
# Salvage Portal (Veritas / Motor Liquidators)
# ─────────────────────────────────────────────────────────────────────────────
# Approval threshold (BWP) — sales above this OR below reserve_price trigger
# an auto-created SalvageApproval(PENDING) for EXCO sign-off.
SALVAGE_APPROVAL_THRESHOLD_BWP = config('SALVAGE_APPROVAL_THRESHOLD_BWP', default='10000')
# GL accounts used when a Sale is posted (see salvage.services.post_sale_to_gl).
SALVAGE_CASH_ACCOUNT_CODE   = config('SALVAGE_CASH_ACCOUNT_CODE',   default='110100')
SALVAGE_INCOME_ACCOUNT_CODE = config('SALVAGE_INCOME_ACCOUNT_CODE', default='400010')

# ─────────────────────────────────────────────────────────────────────────────
# Outbound email (omni@alphadirect.co.bw via Microsoft 365 SMTP)
# ─────────────────────────────────────────────────────────────────────────────
# Tenant mailbox provisioned by IT (Sechele 2026-05-20). Until EMAIL_HOST_PASSWORD
# lands in /etc/alpha-finance/.env the backend falls back to console (no-op in
# prod, logged for dev). Once the SMTP password is set, no code change needed —
# just restart backend.
EMAIL_BACKEND       = config('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
EMAIL_HOST          = config('EMAIL_HOST',          default='smtp.office365.com')
EMAIL_PORT          = config('EMAIL_PORT',          default=587, cast=int)
EMAIL_USE_TLS       = config('EMAIL_USE_TLS',       default=True, cast=bool)
EMAIL_USE_SSL       = config('EMAIL_USE_SSL',       default=False, cast=bool)
EMAIL_HOST_USER     = config('EMAIL_HOST_USER',     default='omni@alphadirect.co.bw')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
EMAIL_TIMEOUT       = config('EMAIL_TIMEOUT',       default=20, cast=int)
DEFAULT_FROM_EMAIL  = config('DEFAULT_FROM_EMAIL',  default='Omni ERP <omni@alphadirect.co.bw>')
SERVER_EMAIL        = config('SERVER_EMAIL',        default='omni@alphadirect.co.bw')

# Default owner for UniCoin reconciliation exceptions (CFO direction 2026-09-01).
# The email of the UniCoin accountant who owns findings raised without an explicit
# owner. Left blank until the CFO confirms the owner with Unami/Keetile; while
# blank, core.unicoin_recon routes to the CFO so a finding is never lost.
UNICOIN_RECON_DEFAULT_OWNER = config('UNICOIN_RECON_DEFAULT_OWNER', default='')

# Auto-switch to SMTP backend when password is present.
if EMAIL_BACKEND == 'django.core.mail.backends.console.EmailBackend' and EMAIL_HOST_PASSWORD:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'

# Microsoft Graph email backend — Sechele 2026-05-22 delivered OAuth2
# client_credentials for omni@alphadirect.co.bw (Mail.Send app perm).
# Preferred over Basic-Auth SMTP (M365 is sunsetting SMTP AUTH).
# RealPay debit-order collections (UAT sandbox 2026-05-25)
REALPAY_BASE_URL          = config('REALPAY_BASE_URL',          default='')
REALPAY_CLIENT_ID         = config('REALPAY_CLIENT_ID',         default='')
REALPAY_CLIENT_SECRET     = config('REALPAY_CLIENT_SECRET',     default='')
REALPAY_BENEFICIARY_USER  = config('REALPAY_BENEFICIARY_USER',  default='')
REALPAY_PRODUCT_CODES     = config('REALPAY_PRODUCT_CODES',     default='')
REALPAY_ENV               = config('REALPAY_ENV',               default='uat')
# Name of the core.VaultSecret row holding the LIVE RealPay credential when it
# is not in .env (CFO directive 2026-07-26 — secret lives only in the encrypted
# vault). username = client_id, secret = client_secret.
REALPAY_VAULT_NAME        = config('REALPAY_VAULT_NAME',        default='RealPay API')
# The three live Botswana beneficiary books (ADI / ADII / Third Parties). Comma
# list of id:label pairs; the nightly live pull iterates all of them.
REALPAY_LIVE_BOOKS        = config('REALPAY_LIVE_BOOKS',        default='')

MICROSOFT_TENANT_ID     = config('MICROSOFT_TENANT_ID',     default='')
MICROSOFT_CLIENT_ID     = config('MICROSOFT_CLIENT_ID',     default='')
MICROSOFT_CLIENT_SECRET = config('MICROSOFT_CLIENT_SECRET', default='')
MICROSOFT_SENDER_UPN    = config('MICROSOFT_SENDER_UPN',    default='omni@alphadirect.co.bw')

# --- Omni Calendar (calendar invites) -------------------------------------
# A DEDICATED app registration, separate from the mail sender above, so
# Calendars.ReadWrite never rides in on the mail app's consent. Fenced at the
# Exchange end by an Application Access Policy on the mail-enabled security
# group omni-calendar-allowed@alphadirect.co.bw — mailboxes are added by
# changing that GROUP, never the policy (IT / M365, 7-Sep-2026).
# Leave these empty and calendar invites are simply off; nothing else breaks.
OMNI_CALENDAR_TENANT_ID     = config('OMNI_CALENDAR_TENANT_ID',     default='')
OMNI_CALENDAR_CLIENT_ID     = config('OMNI_CALENDAR_CLIENT_ID',     default='')
OMNI_CALENDAR_CLIENT_SECRET = config('OMNI_CALENDAR_CLIENT_SECRET', default='')
# Optional second fence in front of the Access Policy, useful while the group
# is still seeded with one mailbox. Empty = the Access Policy is the only gate.
OMNI_CALENDAR_ALLOWED_MAILBOXES = config('OMNI_CALENDAR_ALLOWED_MAILBOXES', default='')

# --- RealPay vs the payment ledger ----------------------------------------
# The daily check added after ~22,500 successful RealPay collections from Jul-Aug
# 2026 never reached payment_transactions and nothing noticed for two months.
# A month is a break only when BOTH thresholds are exceeded, so a few stragglers
# mid-month are not an alarm. No magic numbers in the code.
# An env var that is declared in docker-compose but left unset arrives as an EMPTY
# STRING, not as absent, so config()'s default never fires. `or` catches that —
# without it an empty REALPAY_RECONCILE_EMAIL would send the alarm to nobody and
# an empty threshold would crash the cast.
REALPAY_RECONCILE_MIN_ROWS = int(config('REALPAY_RECONCILE_MIN_ROWS', default='') or 500)
REALPAY_RECONCILE_MIN_PCT  = float(config('REALPAY_RECONCILE_MIN_PCT', default='') or 5.0)
REALPAY_RECONCILE_EMAIL    = (config('REALPAY_RECONCILE_EMAIL', default='')
                              or 'pganesharajah@alphadirect.co.bw')

# Auto-promote to Graph backend the moment all three credentials are
# present. Wins over the SMTP auto-promote above when both are set.
if (MICROSOFT_TENANT_ID and MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET
        and EMAIL_BACKEND in ('django.core.mail.backends.console.EmailBackend',
                              'django.core.mail.backends.smtp.EmailBackend')):
    EMAIL_BACKEND = 'core.email_backends.MicrosoftGraphEmailBackend'

# ── Shared-mailbox guard + oversight log (CFO instruction 2026-07-25, opt A) ──
# Mailboxes that must NEVER receive omni mail, however the message was built.
# admin@ is worked by junior clerks; until 2026-07-25 the shared `admin` account
# carried that address, so Omni sign-in and password-reset CODES were delivered
# straight into it and any clerk reading it could complete the login.
#
# This is NOT the same as NEVER_CC_EMAILS. That list (Arun, Arjun) means "never
# AUTO-CC as a decision-maker" — they are valid recipients when a caller names
# them, and integrations.check_timedoctor_token mails Arjun directly on purpose.
# Enforcing that list here would silently kill the token-renewal reminder.
NEVER_DELIVER_EMAILS = config(
    'NEVER_DELIVER_EMAILS', default='admin@alphadirect.co.bw', cast=Csv())

# Wrap whatever backend was resolved above. Every Django email goes through
# send_messages(), so the wrapper is the only place a rule cannot be bypassed by
# a sender that builds EmailMessage directly (~18 of them do).
# locmem is left alone so the test runner's own backend is untouched.
if EMAIL_BACKEND not in ('django.core.mail.backends.locmem.EmailBackend',
                         'core.email_backends.GuardedEmailBackend'):
    EMAIL_INNER_BACKEND = EMAIL_BACKEND
    EMAIL_BACKEND = 'core.email_backends.GuardedEmailBackend'

# Where the daily "what omni emailed" digest goes.
# Who may read/edit the shared CFO/Claude notebook (/notebook).
NOTEBOOK_EDITORS = config(
    'NOTEBOOK_EDITORS',
    default='pganesharajah@alphadirect.co.bw,excoboard@alphadirect.co.bw',
    cast=Csv())

OUTBOUND_DIGEST_TO = config(
    'OUTBOUND_DIGEST_TO', default='pganesharajah@alphadirect.co.bw', cast=Csv())

# Evening ADH service-provider dashboard (CFO 2026-09-01). Emails EXCO (CC) +
# the CEO & COO by name; a WhatsApp summary goes to the exec WhatsApp contacts.
PROVIDER_DASHBOARD_ENABLED = config('PROVIDER_DASHBOARD_ENABLED', default=True, cast=bool)
PROVIDER_DASHBOARD_TO = config(
    'PROVIDER_DASHBOARD_TO',
    default='aiyer@alphadirect.co.bw,arjuniyer@alphadirect.co.bw', cast=Csv())

# ─────────────────────────────────────────────────────────────────────────────
# Staff mail auto-reply (CFO directive 2026-07-31)
#   Staff email excoboard@ / omni@ instead of using /report-bug. The
#   `auto_reply_omni_mail` command watches AUTO_REPLY_READ_MAILBOX (the only
#   mailbox our Graph apps are allowed to read — mail addressed to the two
#   watched addresses is delivered there too) and replies once per sender.
#   Reader app needs Mail.Read (Application) + mailbox access policy.
# ─────────────────────────────────────────────────────────────────────────────
# Domains treated as "inside Alpha Direct". Used by the do-not-reply banner on
# outbound email (CFO 2026-08-07 — internal emails only; a customer or broker
# cannot log into Omni so they must never see "log it in Omni").
INTERNAL_EMAIL_DOMAINS = config(
    'INTERNAL_EMAIL_DOMAINS',
    default=('alphadirect.co.bw,alphadirect.co.za,alphadirect.co.zm,'
             'insurance.co.bw,motorliquidators.co.bw,cuberoute.co.bw'), cast=Csv())

AUTO_REPLY_ENABLED   = config('AUTO_REPLY_ENABLED', default=False, cast=bool)
AUTO_REPLY_READ_MAILBOX = config(
    'AUTO_REPLY_READ_MAILBOX', default='pganesharajah@alphadirect.co.bw')
AUTO_REPLY_WATCH_ADDRESSES = config(
    'AUTO_REPLY_WATCH_ADDRESSES',
    default='excoboard@alphadirect.co.bw,omni@alphadirect.co.bw', cast=Csv())
# Internal only, per the CFO: Alpha Direct + insurance.co.bw group domains.
AUTO_REPLY_INTERNAL_DOMAINS = config(
    'AUTO_REPLY_INTERNAL_DOMAINS',
    default=('alphadirect.co.bw,alphadirect.co.za,alphadirect.co.zm,'
             'insurance.co.bw,motorliquidators.co.bw,cuberoute.co.bw'), cast=Csv())
AUTO_REPLY_EXCLUDE_EMAILS = config(
    'AUTO_REPLY_EXCLUDE_EMAILS',
    default='pganesharajah@alphadirect.co.bw', cast=Csv())
AUTO_REPLY_COOLOFF_DAYS     = config('AUTO_REPLY_COOLOFF_DAYS', default=14, cast=int)
AUTO_REPLY_LOOKBACK_MINUTES = config('AUTO_REPLY_LOOKBACK_MINUTES', default=120, cast=int)

GRAPH_READER_TENANT_ID     = config('GRAPH_READER_TENANT_ID',     default='')
GRAPH_READER_CLIENT_ID     = config('GRAPH_READER_CLIENT_ID',     default='')
GRAPH_READER_CLIENT_SECRET = config('GRAPH_READER_CLIENT_SECRET', default='')

# ── FNB email auto-reconcile (CFO 2026-08-23) ────────────────────────────────
# Close paid payment requests straight from FNB's "Fully Processed" confirmation
# emails (fnb_email_autoclose). Ships OFF; the CFO turns it on after a --dry-run.
# Reuses the same Mail.Read reader app + mailbox as the auto-reply command.
FNB_EMAIL_AUTOCLOSE_ENABLED       = config('FNB_EMAIL_AUTOCLOSE_ENABLED', default=False, cast=bool)

# Proof-of-payment capture starts HERE and never reaches behind it (CFO
# 2026-08-26: "old payments ignore, we do this properly from today"). A hard
# date, not a rolling window, so no amount of re-running or a widened --hours
# can drag historic mail in. Move it only with the CFO's say-so.
FNB_POP_CAPTURE_FROM = config('FNB_POP_CAPTURE_FROM', default='2026-08-26')
FNB_EMAIL_AUTOCLOSE_LOOKBACK_HOURS = config('FNB_EMAIL_AUTOCLOSE_LOOKBACK_HOURS', default=36, cast=int)
FNB_EMAIL_READ_MAILBOX            = config('FNB_EMAIL_READ_MAILBOX', default=AUTO_REPLY_READ_MAILBOX)

# ─────────────────────────────────────────────────────────────────────────────
# Motor Liquidators V3 integration (CFO directive 2026-05-20)
# ─────────────────────────────────────────────────────────────────────────────
# External Express/SQLite portal that runs salvage operations. Omni hosts
# three integration endpoints under /api/v1/salvage/external/ behind the
# `motor-liquidators` ApiKey scope. Vehicle lookup falls back to Graphite
# / Bizsure when local claims data is missing — those source systems are
# optional; the lookup endpoint returns 404 cleanly if their env vars
# are unset.
MOTOR_LIQUIDATORS_BASE_URL = config('MOTOR_LIQUIDATORS_BASE_URL', default='')
GRAPHITE_API_BASE  = config('GRAPHITE_API_BASE',  default='')
GRAPHITE_API_TOKEN = config('GRAPHITE_API_TOKEN', default='')
BIZSURE_API_BASE   = config('BIZSURE_API_BASE',   default='')
BIZSURE_API_KEY    = config('BIZSURE_API_KEY',    default='')

# ─────────────────────────────────────────────────────────────────────────────
# Graphite V2 Finance API — payment-transaction feed (integrations app)
# ─────────────────────────────────────────────────────────────────────────────
# Machine-to-machine pull of payment_transactions from Graphite V2 into omni's
# local mirror (integrations.GraphitePaymentTransaction). Read-only; no GL
# posting. SEPARATE service account/token from GRAPHITE_API_* above (that one
# is the salvage vehicle-lookup). Sanctum token scoped to `finance:read`.
# Token + integration brief handed off by ADRisk IT (Pramod) — see
# docs/graphite-omni-payment-integration.md. Feed is dormant until both set.
GRAPHITE_FINANCE_API_BASE       = config('GRAPHITE_FINANCE_API_BASE',  default='')
GRAPHITE_FINANCE_API_TOKEN      = config('GRAPHITE_FINANCE_API_TOKEN', default='')
GRAPHITE_FINANCE_TIMEOUT_SECONDS = config('GRAPHITE_FINANCE_TIMEOUT_SECONDS', default=30, cast=int)

# Read-only door into the Graphite database (CFO 2026-08-11). `graphite-v2-prod-ro`
# is a dedicated read replica of Graphite_live; Omni's server is in the same VPC and
# subnet and port 3306 is open to it, and Parameter Store already holds the DSN for a
# purpose-made read-only user at /graphite/GRAPHITE_RO_DSN (Alpha Brain's
# language-to-SQL uses the same one).
#
# It is read here as a plain setting, exactly like DB_PASSWORD, rather than fetched
# from the vault at runtime: pulling a decrypted secret out of SSM inside an ad-hoc
# shell command is indistinguishable from exfiltrating it, and is blocked. See
# integrations/graphite_ro.py, which refuses any host that is not a replica and any
# statement that is not a read.
GRAPHITE_RO_DSN = config('GRAPHITE_RO_DSN', default='')
GRAPHITE_RO_TIMEOUT_SECONDS = config('GRAPHITE_RO_TIMEOUT_SECONDS', default=20, cast=int)

# ─────────────────────────────────────────────────────────────────────────────
# Graphite V2 read-only replica — age analysis (reporting/graphite_age_analysis)
# ─────────────────────────────────────────────────────────────────────────────
# omni-only integration (CFO 2026-06-16): SELECT-only read of the Graphite BW
# read replica for the premium-debtors aging table. No Graphite changes. Point
# at the -rpro replica; creds are the existing read user (in /etc/alpha-finance/.env).
GRAPHITE_RO_DB_HOST     = config('GRAPHITE_RO_DB_HOST', default='')
GRAPHITE_RO_DB_PORT     = config('GRAPHITE_RO_DB_PORT', default=3306, cast=int)
GRAPHITE_RO_DB_NAME     = config('GRAPHITE_RO_DB_NAME', default='Graphite_live')
GRAPHITE_RO_DB_USER     = config('GRAPHITE_RO_DB_USER', default='')
GRAPHITE_RO_DB_PASSWORD = config('GRAPHITE_RO_DB_PASSWORD', default='')
GRAPHITE_RO_DB_TIMEOUT  = config('GRAPHITE_RO_DB_TIMEOUT', default=20, cast=int)

# The single-URL form of the same credential. SSM already holds
# /graphite/GRAPHITE_RO_DSN (mysql://user:pass@graphite-v2-prod-ro/Graphite_live)
# and integrations/graphite_ro.py reads GRAPHITE_RO_DSN first — but the setting
# was never declared, so `is_configured()` could not see it and every consumer
# silently reported the replica as unreachable even with the parameter present.
# Declared here so either form works: DSN if supplied, the split vars otherwise.
# (H24 — a var absent from the settings/compose chain arrives EMPTY and fails
# quietly forever.)
GRAPHITE_RO_DSN         = config('GRAPHITE_RO_DSN', default='')

# --- ADH to AFA member load file -------------------------------------------
# Which Graphite policy statuses count as "on cover". UNRESOLVED business
# question: on 7-Aug-2026 all 293 live ADH group policies sat at status 0
# (Deactivated) with a populated activation date and none at 1 (Activated),
# while the book overall runs 108,670 at 0 / 30,704 at 1. A setting, not a
# guess buried in code.
AFA_ON_COVER_STATUSES = [
    int(x) for x in str(config('AFA_ON_COVER_STATUSES', default='0,1')).split(',') if x.strip()
]
# AFA's "Region Name" column. The sample file repeats the group name here.
# What goes in the file. 'full' = every member on cover, every run - the safe
# assumption until AFA confirm in writing that they expect only the day's
# changes. If AFA replace their membership from each file, a delta empties the
# scheme. Only set 'delta' on a written answer.
AFA_LOADFILE_MODE = config('AFA_LOADFILE_MODE', default='full')
AFA_REGION_NAME = config('AFA_REGION_NAME', default='')
# One of AFA's ten reason codes. EMPTY means a departure is HELD and named
# rather than labelled with a guess (Phase 0 Q5).
AFA_DEFAULT_RESIGNATION_REASON = config('AFA_DEFAULT_RESIGNATION_REASON', default='')
# Who may preview and release the file. Server-side gate, not a hidden button.
AFA_LOADFILE_ALLOWED_LOCALPARTS = config(
    'AFA_LOADFILE_ALLOWED_LOCALPARTS',
    default='rtonkope,mtlagae,lkeotlhoboge,pganesharajah,kmokhendo')
# THE SEND SWITCH. Off until AFA answer the open questions and a fortnight of
# runs match what was previously submitted by hand.
AFA_LOADFILE_AUTOSEND = config('AFA_LOADFILE_AUTOSEND', default=False, cast=bool)
AFA_SFTP_HOST            = config('AFA_SFTP_HOST', default='')
AFA_SFTP_PORT            = config('AFA_SFTP_PORT', default='22')
AFA_SFTP_USERNAME        = config('AFA_SFTP_USERNAME', default='')
AFA_SFTP_PASSWORD        = config('AFA_SFTP_PASSWORD', default='')
AFA_SFTP_PRIVATE_KEY_PATH = config('AFA_SFTP_PRIVATE_KEY_PATH', default='')
AFA_SFTP_REMOTE_DIR      = config('AFA_SFTP_REMOTE_DIR', default='.')
# ── ADH health claims EFT settlement loader (B4) ────────────────────────────
# AFA email the weekly settlement file; Omni reads it out of a mailbox. The
# mailbox is a NAMED SETTING, never a hardcoded address, so it can be moved
# without a code change. health@ is on AFA's recipient list already.
#
# 🔴 Reading needs Microsoft Graph "Mail.Read" (APPLICATION permission, admin
# consent) on Omni's mail app registration, scoped to this one mailbox with an
# Entra application access policy. Omni's registration is Mail.Send only today,
# so until IT grant it the loader fails loudly and emails the owner.
ADH_SETTLEMENT_MAILBOX = config('ADH_SETTLEMENT_MAILBOX',
                                default='health@alphadirect.co.bw')
ADH_SETTLEMENT_MAIL_FOLDER = config('ADH_SETTLEMENT_MAIL_FOLDER', default='Inbox')
# How far back a run looks. THE WINDOW MUST BE SHORTER THAN THE FEED'S WEEKLY
# PERIOD, by a safe margin. The job fires 00:00 Saturday UTC and the emails land
# ~23:20 the Friday night before, so a 7-day window already reaches to within 40
# minutes of LAST week's email and an 8-day one swallows it whole. Last week's
# subject passes all four conditions exactly as well as this week's, so the
# selector would see TWO matches and refuse to guess EVERY SINGLE WEEK — loud,
# but permanently zero. Five days clears last Friday by two whole days even if
# AFA run early or late, and still lets a manual catch-up work up to Wednesday.
# Two days did not: from Sunday night the file was invisible and the run would
# have told Keetile AFA sent nothing when they had. Keep in step with
# healthcare.afa_mailbox.DEFAULT_LOOKBACK_DAYS. (Fable, 14-Sep-2026.)
ADH_SETTLEMENT_LOOKBACK_DAYS = config('ADH_SETTLEMENT_LOOKBACK_DAYS',
                                      default=5, cast=int)

GRAPHITE_AGE_TABLE      = config('GRAPHITE_AGE_TABLE', default='summary_age_analyst_report_dom_com_2025')

# WEBFLEET.connect telematics feed for Project Nexus (Charmaine 2026-06-25).
# Read-only. Set in /etc/alpha-finance/.env; dormant until all four are present.
WEBFLEET_ACCOUNT  = config('WEBFLEET_ACCOUNT',  default='')
WEBFLEET_USERNAME = config('WEBFLEET_USERNAME', default='')
WEBFLEET_PASSWORD = config('WEBFLEET_PASSWORD', default='')
WEBFLEET_APIKEY   = config('WEBFLEET_APIKEY',   default='')

# Cartrack Fleet API — Alpha Direct's OWN company vehicles (CFO 2026-07-10).
# Read-only. Set in /etc/alpha-finance/.env; dormant until username+password are
# present. Region = ISO alpha-2 (bw Botswana). Base fleetapi-<region>.cartrack.com.
CARTRACK_USERNAME = config('CARTRACK_USERNAME', default='')
CARTRACK_PASSWORD = config('CARTRACK_PASSWORD', default='')
CARTRACK_REGION   = config('CARTRACK_REGION',   default='bw')

# Shared token for the phone telematics feed (GPSLogger → /api/v1/nexus/phone-ping/).
# The phone app can't do SSO, so it authenticates with this token. Set in .env.
NEXUS_PHONE_TOKEN = config('NEXUS_PHONE_TOKEN', default='')

# Alpha Nexus: may members SELF-REPORT steps / workouts for points? OFF since
# 7-Sep-2026 (CFO) — typed numbers are fakeable and monthly cash prizes ride on
# the leaderboard. Points come only from device-synced steps (Health Connect).
NEXUS_MANUAL_STEP_POINTS_ENABLED = config('NEXUS_MANUAL_STEP_POINTS_ENABLED', default=False, cast=bool)

# Time Doctor v2 API (workforce time-tracking + productivity). Read-only daily
# pull → omni snapshot + daily email. TIMEDOCTOR_TOKEN is a JWT minted ONCE via
# POST /api/1.0/login (NOT the password); store the token, never the password.
# All default '' / unset → the pull_timedoctor cron exits SKIPPED (non-fatal).
TIMEDOCTOR_API_BASE        = config('TIMEDOCTOR_API_BASE',  default='https://api2.timedoctor.com')
TIMEDOCTOR_TOKEN           = config('TIMEDOCTOR_TOKEN',     default='')
TIMEDOCTOR_COMPANY_ID      = config('TIMEDOCTOR_COMPANY_ID', default='')
TIMEDOCTOR_TIMEOUT_SECONDS = config('TIMEDOCTOR_TIMEOUT_SECONDS', default=45, cast=int)
# How many PEOPLE per activity request (worklog / timeuse). The whole company in
# one call is what produced the 11,887,954-byte truncated timeuse body that HELD
# the 20-Sep-2026 Morning Brief; ten at a time keeps each response inside what
# their API returns intact. Lower it if a truncation ever reappears.
TIMEDOCTOR_USER_BATCH      = config('TIMEDOCTOR_USER_BATCH', default=10, cast=int)
TIMEDOCTOR_EMAIL_TO        = config('TIMEDOCTOR_EMAIL_TO', default='', cast=Csv())
# Shared secret for the n8n → omni ingest endpoint (POST /api/v1/timedoctor/ingest/).
# n8n holds the Time Doctor login in its own encrypted vault, logs in + pulls,
# and POSTs the raw payload here with this key. Set it in the env + in n8n.
TIMEDOCTOR_INGEST_KEY      = config('TIMEDOCTOR_INGEST_KEY', default='')

# Token-renewal reminder (CFO 2026-07-14). Time Doctor API tokens expire ~6
# months after minting and there is no non-expiring company key (vendor
# confirmed). The check_timedoctor_token cron reads the token's own expiry and
# emails these people EVERY day for the last WINDOW_DAYS before it lapses (and
# urgently once expired) so it is renewed before the daily pull silently 401s.
# Arjun is named explicitly here, so the command sends direct — NOT via
# core.notifications, whose _NEVER_CC blocklist would otherwise strip him.
# Workforce Daily Brief (CFO 2026-07-14). Normally switched on/off in-app from
# the Time Doctor page (WorkforceBriefSetting); this env var is an extra
# force-on for CLI/testing. Off by default.
WORKFORCE_BRIEF_ENABLED = config('WORKFORCE_BRIEF_ENABLED', default=False, cast=bool)
# Only these people may flip the in-app switch (server-enforced). CFO / Arun / Arjun.
WORKFORCE_BRIEF_ADMINS = config(
    'WORKFORCE_BRIEF_ADMINS',
    default='pganesharajah@alphadirect.co.bw,aiyer@alphadirect.co.bw,arjuniyer@alphadirect.co.bw',
    cast=Csv())
# Intraday Time Doctor hours reminders (CFO-approved 2026-08-17, GO-LIVE 2026-08-27).
# The send_hours_reminder / send_weekly_hours_review commands gate on this flag;
# it was omitted here originally so the flag could never read true from the env.
# Reminders only (never dock pay) — the /etc/cron.d/hours-reminders cron is the
# other gate. Off by default.
HOURS_REMINDERS_ENABLED = config('HOURS_REMINDERS_ENABLED', default=False, cast=bool)
# Consolidated daily emails (CFO 2026-08-05). Master OFF-switch for the new
# 4-email layout that bundles the many separate morning emails so staff (and the
# CFO) get fewer of them:
#   06:30  Email 1  per-person "morning to-dos"  = task reminders + (if a manager) team-tracking gaps
#   06:30  Email 2  CEO Omni Brief               = unchanged; stays its own email (separate /opt/ceo-monitor cron)
#   06:30  Email 3  CFO/EXCO "ops & system"      = outbound-mail digest + Time Doctor health (all-green silent)
#   07:05  Email 4  per-person "Morning Brief"   = personal brief + (if a manager) the team scoreboard
# When ON, the individual senders (sweep_task_reminders, send_manager_accountability,
# send_exceptions_report, timedoctor_healthcheck, email_outbound_digest) self-skip
# and the consolidated senders take over. OFF by default so a deploy changes nothing
# until the CFO flips it on (dormant rollout).
CONSOLIDATED_EMAILS_ENABLED = config('CONSOLIDATED_EMAILS_ENABLED', default=False, cast=bool)
# Manager group for the daily Exceptions Report (CFO 2026-07-14).
WORKFORCE_EXCEPTIONS_TO = config(
    'WORKFORCE_EXCEPTIONS_TO',
    # CFO instruction 2026-08-07: Arjun (arjuniyer@) re-added — removed 2026-08-01,
    # now back on the manager report — plus Pako Kago (pkago@), Legakwa Ntabeni
    # (lntabeni@) and Bonno Ben (bben@).
    # CFO 2026-09-10: add Pramod (pbisen@theriskco.com), remove Lakshmi (lanand@).
    default=('pganesharajah@alphadirect.co.bw,aiyer@alphadirect.co.bw,arjuniyer@alphadirect.co.bw,'
             'pbeka@alphadirect.co.bw,ubutale@alphadirect.co.bw,dikgopoleng@alphadirect.co.bw,'
             'ktshutlhedi@alphadirect.co.bw,pkago@alphadirect.co.bw,lntabeni@alphadirect.co.bw,'
             'isechele@alphadirect.co.bw,omogomotsi@alphadirect.co.bw,wmoses@alphadirect.co.bw,'
             'gmachobane@alphadirect.co.bw,mtlagae@alphadirect.co.bw,bben@alphadirect.co.bw,'
             'pbisen@theriskco.com'),
    cast=Csv())
# omni Help Desk (IT tickets) — surfaced in the brief when tracking looks broken.
HELPDESK_URL = config('HELPDESK_URL', default='https://omni.alphadirect.co.bw/helpdesk/')
# Workforce recon (CFO 2026-07-14): exclude non-tracking staff from briefs /
# exceptions. WHOLE-WORD keyword match on department/job-title — plurals listed
# explicitly so 'commission' can't re-catch "Commissions Administrator" and
# 'intern' can't catch "Internal Audit" (Fable review 2026-07-14) — plus an
# explicit email opt-out list HR can extend without a deploy.
WORKFORCE_EXCLUDE_KEYWORDS = config(
    'WORKFORCE_EXCLUDE_KEYWORDS',
    default='agent,agents,broker,brokers,independent,commission,consultant,consultants,intern,interns',
    cast=Csv())
WORKFORCE_EXCLUDE_EMAILS = config('WORKFORCE_EXCLUDE_EMAILS', default='', cast=Csv())
# Who gets the circuit-breaker "report HELD" alert (defaults to the CFO), and
# the did-not-track % of the matched roster above which nothing is sent.
WORKFORCE_ALERT_TO = config('WORKFORCE_ALERT_TO',
                            default='pganesharajah@alphadirect.co.bw', cast=Csv())
# OUTAGE-level, not normal-absence: only suppress the send when MOST of the
# matched roster is silent at once (a TD outage / token death / match collapse).
# Normal daily non-tracking is what the report is FOR (Fable review 2026-07-14).
WORKFORCE_BREAKER_DNT_PCT = config('WORKFORCE_BREAKER_DNT_PCT', default=60.0, cast=float)
# Auto-offboarding INVESTIGATION-TICKET engine (CFO 2026-07-14). Review-only:
# raises an HR ticket to Unami after 5 working days of no tracking + no leave,
# never disables/pays/fires. Kept OFF by default — enable only after a clean
# 2-4 week run of the brief so it never fires on a matching/outage glitch.
WORKFORCE_OFFBOARDING_ENABLED = config('WORKFORCE_OFFBOARDING_ENABLED', default=False, cast=bool)

# Naming HR when a ghost-payroll task passes its 3-day deadline (CFO 2026-07-30).
# This used to sit INSIDE the ghost-list block, so switching the ghost list off on
# 5 Aug 2026 silently switched HR chasing off too — a side effect, not a decision.
# They are different things: the ghost list names a STAFF member who may just be
# mismatched in the tracker; this names the HR owner of an unanswered task.
# Default ON — a deadline nobody sees pass is not a deadline (CFO 2026-08-09).
WORKFORCE_HR_OVERDUE_IN_EMAIL = config('WORKFORCE_HR_OVERDUE_IN_EMAIL', default=True, cast=bool)

TIMEDOCTOR_RENEWAL_WINDOW_DAYS = config('TIMEDOCTOR_RENEWAL_WINDOW_DAYS', default=7, cast=int)
TIMEDOCTOR_RENEWAL_TO          = config(
    'TIMEDOCTOR_RENEWAL_TO',
    default='pganesharajah@alphadirect.co.bw,arjuniyer@alphadirect.co.bw,ubutale@alphadirect.co.bw',
    cast=Csv())

# ─────────────────────────────────────────────────────────────────────────────
# Vendor KYC payment guard (CFO directive — procurement/kyc_service.py)
# ─────────────────────────────────────────────────────────────────────────────
# Payments above this BWP amount are blocked at confirm() time when the
# counterparty's KYC record is expired. Other KYC states (missing, flagged)
# are surfaced elsewhere and do not hard-block at this threshold.
KYC_BLOCK_THRESHOLD_BWP = 50_000

# ─────────────────────────────────────────────────────────────────────────────
# Logging (CFO directive 2026-07-18 — after the commissions upload 500s)
# ─────────────────────────────────────────────────────────────────────────────
# With DEBUG=False and no LOGGING config, Django routes unhandled-exception
# tracebacks to the AdminEmailHandler only (no ADMINS configured → dropped),
# and its console handler carries require_debug_true — so prod 500s left
# NOTHING in gunicorn stderr / docker logs. This console handler has no
# debug filter: every django.request ERROR (and anything else at ERROR)
# lands in `docker logs alpha-finance-backend`.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'ERROR',
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        # The Telegram bot at INFO, on purpose (CFO 2026-08-12).
        #
        # A Telegram user_id is NOT a phone number and is stored nowhere in omni —
        # the only way to learn someone's is for them to message the bot. bot.py
        # already logs `IN  user=<id> @<name>` for every message and
        # `WHOAMI user_id=...`, but with root at ERROR those lines were thrown
        # away, so enrolling somebody meant asking them to read the number off
        # their own screen and send it on. At INFO the IDs land in
        # `docker logs alpha-finance-telegrambot` and whoever is enrolling people
        # can simply read them — the difference between four executives copying
        # numbers around and four executives sending one message.
        #
        # Scoped to this one logger so the rest of the app stays quiet at ERROR.
        # What it writes is the sender's Telegram id, their username and the text
        # they typed — no ERP data. The bot itself remains read-only and
        # password-gated.
        'core.telegram_bot': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}


# Shared with GitHub so the gate board can trust what it is told. Empty by
# default and the webhook then refuses every delivery — see ci_monitor/webhook.py,
# where failing closed is deliberate: a board anyone can write to is a board
# that lies.
CI_WEBHOOK_SECRET = config('CI_WEBHOOK_SECRET', default='')


# What counts as a "large" claim payment on the CEO authorisation request
# (CFO 2026-09-11 — the cut-off used on the 1-Sep run). A SETTING rather than a
# constant so the number changes without a rebuild; the value in force is copied
# onto each request when it is raised, so changing it here never rewrites what
# "large" meant on a request already approved.
LARGE_PAYMENT_THRESHOLD_BWP = config('LARGE_PAYMENT_THRESHOLD_BWP', default='20000.00')
