"""
core/security_dashboard.py — Security Posture dashboard (CFO directive 2026-08-06).

A private, read-only view for the group C-suite of where Omni stands on security:
what is already strong, what is weak, and the specific fix for each weakness with
an owner and a sprint. Deliberately written as a work plan, not a list of
complaints — every open item carries the action that closes it.

Source: the authorised adversarial assessment of 6 August 2026 (attacker pass +
independent re-verification pass). Findings that did not survive re-verification
were dropped rather than reported, so nothing here is speculative — each item
names the file and line it was read from.

Access reuses the group C-suite lock (hris.document_access) — CEO, COO, CFO and
superusers. No new models: the register is maintained here in code, exactly like
the DPA scorecard, so the status is honest and reviewable in the diff.
"""
from __future__ import annotations

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

ASSESSED_ON = '2026-08-06'


def can_view_security_dashboard(user) -> bool:
    """Group C-suite (CEO / COO / CFO) + superusers. Nobody else."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    from hris.document_access import _is_csuite
    return _is_csuite(user)


# ── Findings register ─────────────────────────────────────────────────────────
# (id, severity, area, title, status, plain_english, fix, owner, effort, sprint,
#  evidence, confidence)
# severity: critical | high | medium | low
# status:   open | in_progress | fixed
# effort:   quick   (<1 day) | sprint | architectural
_FINDINGS = [
    ('SEC-01', 'high', 'Money & reporting',
     'The lock protecting the closed financial years is switched off',
     'fixed',
     'FY25 and FY26-to-March are supposed to be sealed. The switch that seals them '
     'is currently off, so a journal can still be posted into the year that carries '
     'our signed-off 125.15M. It was turned off for the May data migration and never '
     'turned back on.',
     'DONE — verified on production 8 Aug 2026. The bypass is off and the lock now '
     'genuinely refuses a backdated journal: a manual entry dated 30 Jun 2025 (FY25, '
     'the year carrying the signed-off 125.15M) is REFUSED, 31 Mar 2026 (FY26-9M) is '
     'REFUSED, and the current period still writes normally. Checked against the real '
     'guard the posting path calls, not the setting alone. Remaining improvement: make '
     'the bypass time-boxed so it expires by itself instead of relying on someone '
     'remembering to switch it back.',
     'CFO decision, then IT', 'quick', 1,
     'Live check on prod 8 Aug 2026: OMNI_FINANCIAL_LOCK_BYPASS=0 in /etc/alpha-finance/.env, ledger.locks._bypass_active() returns False, and ledger.locks.assert_can_write_journal_entry(entry_date=2025-06-30, company_code=ADIC) raises "LOCKED: Historical financial data ... cannot be modified". Same for 2026-03-31. 2026-07-31 is allowed.',
     'CONFIRMED by exercising the real guard on production, not by reading the setting'),

    ('SEC-02', 'high', 'Access control',
     'A staff account with no entity grant can see every company',
     'fixed',
     'We run 13 entities. The rule that keeps one entity out of another only applies '
     'to people who have been explicitly restricted. A normal new account has no '
     'restriction recorded, and the code treats "no restriction" as "allowed '
     'everywhere" rather than "allowed nothing".',
     'DONE 8 Aug 2026 — the default is flipped and live. "No restriction recorded" '
     'now resolves to the person\'s own entity; where it cannot be determined the '
     'answer is NOTHING rather than everything. The consolidated HRIS view survives '
     'for anyone holding view_all (HR and the exec team), which is what the leniency '
     'existed for. OMNI_ENTITY_SCOPE_STRICT puts the old behaviour back with one env '
     'var if something legitimate turns out to be hidden. Measured on production '
     'first: of 188 active accounts, 52 were newly scoped, and 49 of those have never '
     'logged in. Three live accounts now see nothing until HR gives them an entity — '
     'listed in the evidence. SEC-03 (the 19 modules that still do not call either '
     'helper) is the remaining half and stays open.',
     'OMNI dev', 'sprint', 1,
     'core/mixins.py scoped_company_ids — the no-grant branch no longer shares the '
     'unrestricted path. 9 tests in core/test_entity_scope_default.py, including that '
     'an unresolvable caller gets [] and that the switch restores the old behaviour. '
     'Verified live 8 Aug: CFO and Unami still see all entities, Kago sees his 12, and '
     'an account with no grant and no staff record now resolves to nothing. NEEDS HR: '
     'tmolefe@, kgabanamotse@ and kmasilo@ have signed in before and have no entity — '
     'give them one or they see an empty screen.',
     'CONFIRMED live on production, and measured against all 188 accounts before shipping'),

    ('SEC-03', 'high', 'Access control',
     'Entity separation is applied in 3 apps out of 22 that need it',
     'open',
     'Twenty-two parts of Omni hold data that belongs to a specific company. Only '
     'HRIS, payroll and the ledger actually apply the separation rule. The others '
     'were written before the rule existed.',
     'Adopt CompanyScopedViewSetMixin app by app, highest-value first: claims, '
     'billing, payments, banking, procurement. Add a startup check that refuses to '
     'boot if a company-scoped model is served by a view with no scoping, so this '
     'cannot silently reappear.',
     'OMNI dev', 'architectural', 2,
     '22 apps declare a company foreign key; only hris, core, payroll reference '
     'either scoping helper',
     'CONFIRMED'),

    ('SEC-04', 'high', 'Sign-in',
     'The 5-strikes account lockout can be sidestepped',
     'fixed',
     'Omni locks an account after 5 wrong passwords. The lockout counts strikes '
     'against a value the person signing in can set themselves, so an attacker can '
     'reset their own strike count and keep guessing indefinitely.',
     'DONE 6 Aug 2026 — the lockout now counts strikes against Cloudflare\'s '
     'client address, which the caller cannot set, instead of the header they '
     'could. Five wrong passwords now genuinely stops the attempt. The stale '
     'proxy-chain setting was removed at the same time so it cannot creep back.',
     'OMNI dev', 'quick', 1,
     'alpha_finance/settings.py:373-374 (AXES_IPWARE_*) vs '
     'core/staff_login_views.py:63-68, which documents exactly this hazard',
     'CONFIRMED in code; live exploitability depends on whether the server can be '
     'reached without going through Cloudflare — needs one live test'),

    ('SEC-05', 'medium', 'Sign-in',
     'A sign-in code can be reused to change the password',
     'fixed',
     'The emailed 6-digit code is not tied to what it was requested for. A code sent '
     'for signing in is also accepted by the "forgot password" step. So a code a '
     'colleague is tricked into reading out over the phone does not just let someone '
     'in once — it lets them set a new password and keep the account.',
     'DONE 6 Aug 2026 — every emailed code now records what it was issued for, '
     'and each step accepts only its own kind. A code someone is talked into '
     'reading out can no longer be spent on changing the password.',
     'OMNI dev', 'quick', 1,
     'core/staff_login_views.py:224 and :351 both read EmailLoginCode filtered on '
     'email only, with no purpose discriminator',
     'CONFIRMED'),

    ('SEC-06', 'medium', 'Money paths',
     'Duplicate-payment protection depends on the bank sending a reference',
     'open',
     'We de-duplicate incoming bank messages by their reference number. A message '
     'that arrives without one skips the check entirely. This is the same shape as '
     'the double-debit we had before.',
     'Fall back to a fingerprint of the message body plus its timestamp when no '
     'reference is supplied, and reject anything older than a few minutes. Must be '
     'closed before the FNB payment path is switched on.',
     'OMNI dev', 'sprint', 2,
     'fnb/models.py:233-235 — the unique constraint applies only when external_id '
     'is non-empty',
     'CONFIRMED as written; NOT exploitable today because the handler that moves '
     'money (fnb/webhooks.py:98 dispatch_webhook) is still a stub and sending is off'),

    ('SEC-07', 'medium', 'Hardening',
     'No browser content-security policy',
     'open',
     'If a malicious script ever reached a page, nothing in the browser would stop '
     'it talking to an outside server. Today the risk is low because we found no '
     'unsafe HTML anywhere in the codebase — this is a second seatbelt, not a hole.',
     'Add a content-security-policy header at Caddy. Start in report-only mode for a '
     'week so nothing breaks, then enforce.',
     'IT', 'sprint', 2,
     'no CSP in settings, middleware or the proxy config',
     'CONFIRMED'),

    ('SEC-08', 'medium', 'Hardening',
     'The admin console sits at the standard address',
     'fixed',
     'Omni\'s Django admin sat on the predictable /admin/ path, which every '
     'automated scanner on the internet probes. A live check afterwards showed it '
     'is NOT actually reachable from outside today — something in front of the '
     'server already blocks it, even though the proxy config says it should pass '
     'through. Good news, but nobody could tell me why, and a protection nobody '
     'can explain is one a routine config change removes by accident.',
     'DONE 6 Aug 2026 — the admin address is now set by configuration rather than '
     'fixed in the code, so it can be moved in one line the moment we want to. '
     'Two things left for IT, both small: establish and write down WHY the path '
     'is currently blocked from outside, and make that block deliberate by '
     'restricting the admin to the office network at the proxy.',
     'IT', 'quick', 1,
     'alpha_finance/urls.py:34. Live probe 6 Aug: /admin/ returns 404 from the '
     'internet, but 302 to the login page from inside the container — so Django '
     'serves it and something upstream is stopping it.',
     'CONFIRMED in code. My original wording said it was internet-facing; the live '
     'probe disproved that and this entry was corrected.'),

    ('SEC-09', 'medium', 'Documents',
     'Uploaded spreadsheets are opened by LibreOffice on the server',
     'open',
     'To read figures out of uploaded workbooks we run LibreOffice on the server. '
     'There is no injection flaw in how we call it, but office suites are a '
     'well-known way in when they are fed a hostile file.',
     'Run the conversion in a throwaway container with no network and a memory cap. '
     'The call is already isolated enough that this is a contained change.',
     'IT + OMNI dev', 'sprint', 3,
     'core/doc_parse/xlsx_md.py:80 — subprocess.run with a fixed argument list, no '
     'shell, 120s timeout (so no command injection)',
     'CONFIRMED as a hardening gap, not as an exploitable flaw'),

    ('SEC-11', 'high', 'Assurance',
     'Our automated safety net was reporting almost nothing',
     'fixed',
     'The checks that are supposed to warn us when something breaks had been '
     'failing 160 times in a row for months. Not because the code was broken — '
     'because of how we were running them. A permanently red alarm is the same as '
     'no alarm: nobody could tell a genuine new break from the usual noise.',
     'DONE 6 Aug 2026 — one line. The checks were being run with the live website '
     'settings, which forced every internal request to bounce to a secure address '
     'before it reached the thing being tested. Result: 160 failures became 2, and '
     'the seven petty-cash checks that read like security guarantees but were '
     'quietly proving nothing now genuinely pass. The remaining failures are real '
     'and are listed as their own items.',
     'OMNI dev', 'quick', 1,
     'alpha_finance/settings.py — SECURE_SSL_REDIRECT forced off under the test '
     'runner. Measured on the production image, same commit: core 408 tests went '
     'from 145 failures + 15 errors to 1 + 1; petty_cash from 6 + 2 to 1.',
     'CONFIRMED by measurement before and after'),

    ('SEC-10', 'low', 'Sign-in',
     'Two accounts sharing one email resolve to the older one',
     'fixed',
     'Where the same email exists twice, sign-in silently picks the account created '
     'first. That may not be the account the person thinks they are using, and we '
     'have had duplicate logins before.',
     'DONE 6 Aug 2026 — sign-in now refuses a duplicated email outright and logs '
     'it for IT rather than quietly picking one. Clearing the duplicate records '
     'themselves is the remaining housekeeping.',
     'OMNI dev', 'quick', 3,
     'core/staff_login_views.py:149 — _get_user uses .order_by(\'id\').first()',
     'CONFIRMED in code; how many live duplicates remain needs a data check'),

    # Found 8 Aug 2026 while answering the COO's question about a bastion host.
    # This is the FIRST finding on this board that came from looking at the
    # infrastructure rather than the code — and a code review could never have
    # found it. It is the clearest argument for the external test the COO asked
    # for. See also the SEC-08 note: a code-only review cannot tell you what the
    # edge is doing.
    ('SEC-12', 'critical', 'Infrastructure',
     'The production server accepts sign-in attempts from anywhere on the internet',
     'open',
     'Port 22 — the remote-control door of the Omni production server — is open to '
     'the whole internet, and it answers. Anyone, anywhere, can reach it and start '
     'guessing. Two separate firewall groups do this: one is literally named '
     '"allow_ssh_any", the other is a general website group that also leaves 22 '
     'open alongside the normal web ports. We DO own a jump host (a locked front '
     'door that everything else should sit behind), but Omni is not behind it — it '
     'has its own public address — and that jump host carries the same '
     '"allow_ssh_any" rule, so it is not acting as a front door either.',
     'Restrict port 22 to the addresses that genuinely need it, and nothing else. '
     'Careful sequencing matters: our own deploys reach the box through AWS EC2 '
     'Instance Connect, so that service\'s address range for af-south-1 must be '
     'allowed before the open rule is removed, or the next deploy locks us out. '
     'Day-to-day automation already uses AWS SSM, which needs no open port at all. '
     'Suggested order: (1) add the Instance Connect range, (2) prove a deploy still '
     'works, (3) remove allow_ssh_any from Omni, (4) strip port 22 from the '
     '"Basic website group", (5) do the same for the jump host.',
     'CFO decision, then IT', 'quick', 1,
     'Instance i-02a5d76a61f4f09a5 (15.240.20.178), security groups '
     'sg-09701b5aab1271a0c "allow_ssh_any" and sg-0c03dd1515ec6d3c1 "Basic website '
     'group", both tcp/22 from 0.0.0.0/0. Confirmed reachable from outside AWS on '
     '8 Aug 2026 — a TCP connection to port 22 succeeded from a laptop in Botswana. '
     'Jump host i-027d1376baa6b8e8d also carries allow_ssh_any.',
     'CONFIRMED by live connection from outside, not by reading code'),
]


# ── Controls that are already strong (verified, not assumed) ──────────────────
_STRENGTHS = [
    ('Record identifiers cannot be guessed',
     'Every record uses a random 122-bit identifier, so nobody can walk through '
     'claims or payslips by trying 1, 2, 3. This single decision removes the most '
     'common breach route in systems like ours.',
     'core/models.py:29 — UUID4 primary key on the shared base model'),
    ('No secret has ever been committed',
     'The full repository history was searched. Only the example template was ever '
     'committed; no key, password or connection string.',
     'git history scan of .env* plus key-pattern search across all tracked files'),
    ('The database is not addressed with hand-built queries',
     'One raw query exists in the entire codebase and it contains no user input, so '
     'the classic database-injection attack has almost no surface here.',
     'salvage/external_views.py:333 — a fixed date-format string'),
    ('Forms cannot be used to write fields we did not intend',
     'No serializer anywhere accepts "all fields", so an attacker cannot smuggle an '
     'extra field such as "approved" into a normal save.',
     'zero occurrences of fields = \'__all__\' across the codebase'),
    ('No unsafe HTML rendering',
     'Nothing in the codebase switches off Django\'s automatic escaping, so injected '
     'script has nowhere to land.',
     'zero occurrences of mark_safe or |safe'),
    ('Bulk download is capped',
     'Nobody can ask for a million rows in one request — page size is capped at 100 '
     'system-wide.',
     'core.pagination.CappedPageNumberPagination'),
    ('The point-and-click API explorer is off in production',
     'A stolen token gives an attacker an interface, not a browsable map of the '
     'whole system.',
     'alpha_finance/settings.py:326-328 — browsable renderer only when DEBUG'),
    ('Sign-in does not leak who works here',
     'Wrong email and wrong password give the same answer and take the same time, so '
     'an outsider cannot use the login page to build a staff list.',
     'core/staff_login_views.py:60, 85-90, 179-181'),
    ('Bank messages are cryptographically checked',
     'Incoming FNB messages are verified with a shared secret using a constant-time '
     'comparison, and an unverified message is recorded but never acted on.',
     'fnb/webhooks.py:30-48, 84-92'),
    ('The AI models cannot be reached from the server',
     'The local AI address is deliberately empty in production, so the AI layer '
     'cannot be used as a route into the network.',
     'alpha_finance/settings.py:498-501'),
    ('Personal data is masked before any external AI call',
     'The PII firewall replaces personal details with tokens before anything leaves '
     'for an outside model.',
     'core/pii_firewall — verified live on the DPA dashboard'),
]


# ── What code alone cannot answer ─────────────────────────────────────────────
_NEEDS_LIVE_TEST = [
    'Whether the server can be reached directly, bypassing Cloudflare. This one '
    'answer decides how serious SEC-04 is.',
    'Database hardening: whether the app connects as a limited user, whether the '
    'database port is closed to the outside, and whether connections are encrypted.',
    'Whether backups are encrypted, and when a restore was last actually tested.',
    'Django\'s own deployment check run against the real production settings.',
    'A dependency vulnerability scan of Python and JavaScript packages.',
    'Whether personal data is appearing in application logs.',
    'How quickly we would notice a bulk export today — no alerting on mass reads '
    'was found in code.',
]


def _findings():
    keys = ('id', 'severity', 'area', 'title', 'status', 'plain_english', 'fix',
            'owner', 'effort', 'sprint', 'evidence', 'confidence')
    out = [dict(zip(keys, row)) for row in _FINDINGS]
    for f in out:
        _live_status(f)
    return out


def _live_status(finding: dict) -> None:
    """Let a finding read its own live state instead of a hand-typed status.

    SEC-01 sat on 'open' in this list while the bypass it complains about had
    already been switched back off, so the dashboard reported an exposure that
    no longer existed. A status nobody recomputes is a status that goes stale in
    whichever direction is least helpful — it read 'open' when we were exposed
    and would have kept reading 'open' once we were not.

    Only SEC-01 has a cheap, unambiguous live check today. The rest keep their
    reviewed status; add cases here as checks become available, and never widen
    this into a guess.
    """
    if finding['id'] != 'SEC-01':
        return
    try:
        from ledger.locks import _bypass_active
    except Exception:                       # pragma: no cover - defensive import
        return
    try:
        bypassed = _bypass_active()
    except Exception:                       # pragma: no cover - defensive
        return
    if not bypassed:
        finding['status'] = 'fixed'
        finding['evidence'] = (
            'Verified live: ledger.locks._bypass_active() is False, so the FY25 / '
            'FY26-to-March lock is enforcing. Checked at render time, not typed in.'
        )


def _summary(findings):
    total = len(findings)
    fixed = len([f for f in findings if f['status'] == 'fixed'])
    in_prog = len([f for f in findings if f['status'] == 'in_progress'])
    by_sev = {}
    for f in findings:
        if f['status'] != 'fixed':
            by_sev[f['severity']] = by_sev.get(f['severity'], 0) + 1
    # Completion counts a half-mark for work under way, so the number moves as
    # soon as something is genuinely started rather than only at the finish.
    pct = round(100 * (fixed + 0.5 * in_prog) / total) if total else 0
    return {
        'total_findings': total,
        'fixed': fixed,
        'in_progress': in_prog,
        'open': total - fixed - in_prog,
        'pct_complete': pct,
        'open_by_severity': by_sev,
        'strengths_verified': len(_STRENGTHS),
    }


def _sprints(findings):
    out = []
    titles = {1: 'Sprint 1 — close the doors that are open',
              2: 'Sprint 2 — make the rules structural',
              3: 'Sprint 3 — tidy and contain'}
    for n in (1, 2, 3):
        items = [f for f in findings if f['sprint'] == n]
        done = len([f for f in items if f['status'] == 'fixed'])
        out.append({
            'sprint': n,
            'title': titles[n],
            'items': [{'id': f['id'], 'title': f['title'], 'owner': f['owner'],
                       'effort': f['effort'], 'status': f['status'],
                       'severity': f['severity']} for f in items],
            'total': len(items),
            'done': done,
            'pct': round(100 * done / len(items)) if items else 0,
        })
    return out


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def security_dashboard(request):
    if not can_view_security_dashboard(request.user):
        return Response(
            {'detail': 'The Security Posture dashboard is restricted to the CEO, '
                       'COO and CFO.'}, status=403)
    findings = _findings()
    return Response({
        'summary': _summary(findings),
        'findings': findings,
        'strengths': [{'title': t, 'detail': d, 'evidence': e}
                      for (t, d, e) in _STRENGTHS],
        'sprints': _sprints(findings),
        'needs_live_test': _NEEDS_LIVE_TEST,
        'assessed_on': ASSESSED_ON,
        'method': 'Adversarial review of the codebase — an attacker pass followed '
                  'by an independent re-verification pass. Findings that did not '
                  'survive re-verification were dropped, not reported.',
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def security_access(request):
    """Cheap gate check for the frontend (mirrors /dpa-dashboard/access/)."""
    return Response({'allowed': can_view_security_dashboard(request.user)})
