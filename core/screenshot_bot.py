"""core/screenshot_bot.py — a locked, READ-ONLY service identity used only to
render omni pages for screenshots.

CFO 2026-07-22: a headless "screenshot robot" so the AI can capture feature
screenshots on its own, without depending on the CFO's live Microsoft SSO
session (which lapses). The robot runs server-side (a Playwright headless
browser on the EC2 host — see ops/omni_screenshot.py), authenticating with a
FRESH DRF token minted per run by `manage.py mint_screenshot_token`. The SPA's
getToken() guard passes and apiFetch sends `Authorization: Token <key>`.

Read-only is enforced at the AUTHENTICATION layer (see
core.token_auth.ExpiringTokenAuthentication): the bot's token authenticates ONLY
on safe methods (GET/HEAD/OPTIONS). Any write/approve/delete by the bot fails
auth and is rejected across EVERY view — even views with custom permissions —
so the robot can never change anything or move money. It is a superuser purely
so it can VIEW every feature page for the screenshot; the read-only auth gate is
what keeps it harmless.
"""
from __future__ import annotations

SCREENSHOT_BOT_USERNAME = 'omni-screenshot-bot'
SCREENSHOT_BOT_EMAIL = 'omni-screenshot-bot@alphadirect.co.bw'
SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')

# CFO 2026-07-29: the human quality-control view (core.qa_view_login) is a
# SECOND locked read-only identity, separate from the robot so that a screenshot
# run and an open QA browser tab do not knock each other out — DRF issues one
# token per user, so minting for one identity revokes that identity's other
# token. Same lock: superuser to VIEW every page, read-only enforced in
# core.token_auth, unusable password so it can never sign in normally.
QA_VIEW_USERNAME = 'omni-qa-view'
QA_VIEW_EMAIL = 'omni-qa-view@alphadirect.co.bw'

# CFO 2026-09-06: the APP-STORE REVIEWER identity (core.review_demo) is the
# third locked read-only name. Its demo data and swallowed writes are handled by
# ReviewDemoMiddleware; listing it here is the belt-and-braces backstop, so a
# write on a path the middleware does not cover still fails at the auth layer
# instead of touching real data. Unlike the two above it is NOT a superuser and
# it is created by `manage.py ensure_review_account`, not by _get_or_create_locked.
from core.review_demo import REVIEW_USERNAME  # noqa: E402  (constant only)

# Every identity here is refused by core.token_auth on any non-safe method.
READ_ONLY_USERNAMES = frozenset({SCREENSHOT_BOT_USERNAME, QA_VIEW_USERNAME,
                                 REVIEW_USERNAME})


def _get_or_create_locked(username: str, email: str):
    """Create/normalise a locked read-only identity. Returns (user, created).
    Superuser (so it can view any feature) + unusable password (can never log in
    the normal way) + active. Read-only is enforced in token_auth, not here."""
    from django.contrib.auth import get_user_model
    U = get_user_model()
    bot, created = U.objects.get_or_create(
        username=username,
        defaults={'email': email, 'is_staff': True,
                  'is_superuser': True, 'is_active': True},
    )
    changed = False
    if not bot.is_active:
        bot.is_active = True; changed = True
    if not (bot.is_staff and bot.is_superuser):
        bot.is_staff = True; bot.is_superuser = True; changed = True
    if bot.has_usable_password():
        bot.set_unusable_password(); changed = True
    if changed:
        bot.save()
    _ensure_auditor_profile(bot)
    return bot, created


def _ensure_auditor_profile(user) -> None:
    """Give the identity the AUDITOR title — 'Auditor (read-only)'.

    CFO 2026-07-29: "no finance manager, or internal auditor is best." Auditor is
    exactly what a quality-control identity is, and the system already understands
    it that way: UserProfile.AUDITOR sits in FINANCIALS_VIEW_TITLES (so nothing is
    hidden from a check) and in NONE of CREATION_TITLES, APPROVAL_TITLES or
    PAYROLL_APPROVAL_TITLES. So the title itself grants no authority, on top of
    core.token_auth already refusing these identities on any write.

    It also gives them a profile, so personal panels stop erroring — a profile-less
    user makes /user-profiles/me/ answer "no profile", which is honest but leaves
    every personal tile blank.

    Only ever applied to the locked identities in this module, never to a human:
    a real person's title is the CFO's decision, never a side effect.
    """
    from core.models import UserProfile
    if getattr(user, 'username', '') not in READ_ONLY_USERNAMES:
        return
    profile, _created = UserProfile.objects.get_or_create(
        user=user,
        defaults={'role': UserProfile.Role.FINANCE_REVIEWER,
                  'title': UserProfile.Title.AUDITOR,
                  'job_title': 'Quality Control (read-only)',
                  'is_administrator': False},
    )
    # Normalise if something drifted — never leave one of these holding a title
    # that can approve or create.
    if profile.title != UserProfile.Title.AUDITOR or profile.is_administrator:
        profile.title = UserProfile.Title.AUDITOR
        profile.is_administrator = False
        profile.save(update_fields=['title', 'is_administrator'])


def get_or_create_bot():
    """The headless screenshot robot."""
    return _get_or_create_locked(SCREENSHOT_BOT_USERNAME, SCREENSHOT_BOT_EMAIL)


def get_or_create_qa_viewer():
    """The human read-only quality-control viewer."""
    return _get_or_create_locked(QA_VIEW_USERNAME, QA_VIEW_EMAIL)
