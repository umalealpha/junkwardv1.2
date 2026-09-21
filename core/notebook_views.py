"""The shared notebook — CFO 2026-07-25. Write-from-Claude-Code added 2026-08-15.

Four endpoints:

  GET  /api/v1/notebook/raw/          plain text, no JSON wrapper. This is the
                                      one Claude reads at the start of every
                                      session, so it must be FAST and boring.
                                      Machine access via a scoped API key
                                      (`notebook` scope, read-only).
  PUT  /api/v1/notebook/raw/          plain text in, plain text confirmation
                                      out. This is how Claude Code updates the
                                      page headlessly — no browser sign-in.
                                      Needs the CFO/EXCO session OR a key
                                      carrying the separate `notebook-write`
                                      scope (deliberately not the same key as
                                      the read one, so a leaked read-only key
                                      still cannot change the page).
  GET  /api/v1/notebook/              JSON, for the browser page.
  PUT  /api/v1/notebook/              save. Browser page (session auth) —
                                      also now accepts `notebook-write`.

Why plain text on the raw endpoint: Claude reads/writes it with a single
fetch/PUT and no parsing. Measured against the alternative that was almost
built instead — a file in OneDrive — Omni wins because both machines and his
phone see the same text with no sync delay.

Who may see it: the CFO, EXCO, superusers, and anything holding an API key with
the `notebook` scope. It carries staff facts, not secrets — passwords and keys
belong in the Secrets Vault (VaultSecret), and the model docstring says so.

Who may CHANGE it: the CFO, EXCO, superusers, and anything holding an API key
with the `notebook-write` scope. Same size cap as the "keep it one page" rule
this page has always asked of its human editor (NOTEBOOK_MAX_CHARS below) —
enforced here too so a runaway script can't turn one page into ten.
"""
from __future__ import annotations

import re
from zoneinfo import ZoneInfo

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import (api_view, authentication_classes,
                                       permission_classes)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import NotebookPage

DEFAULT_SLUG = 'main'
# Botswana time on every stamp — CFO standing rule.
GABS = ZoneInfo('Africa/Gaborone')

# Rule 2 in the page's own text is "keep it to one page." The cap exists so a
# script bug can't quietly grow the page past the point a human — or Claude —
# will actually read it.
#
# Raised from 24,000 to 60,000 with the CFO's approval, 19-Aug-2026. The old
# figure had stopped being a guard and become a lock: the real page reached
# 51,759 characters, so EVERY headless save was refused while the browser page
# (which never had a cap) went on saving happily. A limit that only blocks the
# automated path pushes edits to the un-capped one — the opposite of the point.
# 60,000 sits above today's page with room to add, and still refuses a runaway.
NOTEBOOK_MAX_CHARS = 60_000

STARTER = """# NOTEBOOK — the shared world between Prathap and Claude

Claude reads this before its first reply, every session, on every machine.

**Rule 1 — this page beats Omni.** If Omni's database disagrees with a line
here, this page is right. Never re-ask Prathap to confirm anything written here.

**Rule 2 — keep it to one page.** The moment it sprawls it stops being read.

**Rule 3 — what belongs here.** Company numbers are wanted, not avoided: GWP,
PAT, PO totals, the month-end position, what we are building. They live here so
a question is answered in one read instead of a long query.

**Never here:** passwords, keys, bank account numbers, Omang/ID numbers, and
individual staff salaries. A shared login exposed staff pay on 25 July 2026 —
that is the one thing this page must not repeat. Company figures yes, per-person
pay no.
"""


# --- Auto-archive ------------------------------------------------------------
# CFO 2026-09-11: "cant we increase the size of the note book? if it exceeds
# charator size we create note book 2?"
#
# Yes to the second half, no to the first, and the reason matters. The cap was
# never the real constraint: Claude reads this ENTIRE page before its first
# reply, every session, on both machines. A bigger page therefore costs on every
# conversation, and a long page gets skimmed instead of read — which is Rule 2
# in the page's own text. Raising the number would buy room by spending the very
# thing the page exists for.
#
# So "notebook 2" is an ARCHIVE, not a second page to read. When a save would
# push `main` past NOTEBOOK_SOFT_CHARS, the OLDEST DATED sections move to the
# `archive` page until it fits. The archive is never read at session start; it
# is searched when something old is needed.
#
# The one rule that makes this safe: a section whose heading carries NO date is
# NEVER moved. Those are the structural core — the people, the mailboxes, the
# frozen figures, the standing traps, how the CFO wants to be spoken to. They
# carry no date because they are not events, and they are exactly what must
# survive. Only dated sections (a write-up of something that happened) age out.
#
# If moving every dated section still is not enough, the save goes through
# anyway rather than eating the core. Refusing a save is the failure this
# replaces: the page sat at 72,586 chars against a 60,000 cap, so every headless
# save was silently rejected for weeks while the browser path (no cap) kept
# saving happily. A limit that only blocks the automated path pushes edits to
# the unchecked one.

NOTEBOOK_SOFT_CHARS = 45_000
ARCHIVE_SLUG = 'archive'

ARCHIVE_STARTER = """# NOTEBOOK ARCHIVE — aged-out sections

Claude does NOT read this at session start. It reads `main`. This page is where
dated sections go when `main` outgrows the size a person will actually read.

Nothing here was deleted, and nothing here is authoritative any more: if this
page and `main` disagree, **`main` wins**. Search this page, never read it whole.
"""

# 2026-08-06 / 9-Aug-2026 / 27 Jul 2026 / 18-AUG-2026 — every shape the real page
# uses. Anything unparseable means "no date", which means "never move".
_MONTHS = {m: i for i, m in enumerate(
    ['jan', 'feb', 'mar', 'apr', 'may', 'jun',
     'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], start=1)}
_ISO = re.compile(r'(\d{4})-(\d{2})-(\d{2})')
_DMY = re.compile(r'(\d{1,2})[-\s]([A-Za-z]{3,9})[-\s](\d{4})')


# A date in the heading is not enough to make a section an EVENT. Proved on the
# live page 2026-09-11: the first real overflow carried off "Titles & reporting
# lines — SETTLED 26-Jul-2026 (do not re-ask)" and "Email addresses — the ones I
# keep needing (26-Jul-2026)". Both are settled FACTS that merely record when
# they were settled, and both are exactly the kind of thing the CFO should never
# be asked twice. A heading that marks itself as standing never ages out,
# whatever date it carries.
# 'open' / 'still' added 2026-09-12 after the second live run aged out
# "People-data gaps still OPEN (26-Jul-2026)". An unfinished item is not a closed
# event, however old the date beside it: archiving it is how something quietly
# stops being chased.
_STICKY = ('settled', 'do not re-ask', 'do not raise', 'do not re-raise',
           'hard rule', 'keep needing', 'never', 'standing', 'frozen',
           'trap', 'rule', 'open', 'still', 'pending', 'outstanding')


def _is_sticky(heading):
    """True when the heading marks itself as a standing fact, not an event."""
    low = heading.lower()
    return any(word in low for word in _STICKY)


def _heading_date(heading):
    """(y, m, d) from a section heading, or None when it carries no date."""
    m = _ISO.search(heading)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        if 2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
            return (y, mo, d)
    m = _DMY.search(heading)
    if m:
        d, name, y = int(m.group(1)), m.group(2)[:3].lower(), int(m.group(3))
        mo = _MONTHS.get(name)
        if mo and 2000 <= y <= 2100 and 1 <= d <= 31:
            return (y, mo, d)
    return None


def _split_sections(body):
    """(preamble, [(heading, section text)]) split on top-level '## '.

    The preamble is everything before the first '## ' — the title and the page's
    own rules. It is not a section and can never move.
    """
    lines = body.split('\n')
    starts = [i for i, ln in enumerate(lines) if ln.startswith('## ')]
    if not starts:
        return body, []
    preamble = '\n'.join(lines[:starts[0]])
    sections = []
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        sections.append((lines[i], '\n'.join(lines[i:end])))
    return preamble, sections


def overflow_to_archive(body, limit=NOTEBOOK_SOFT_CHARS):
    """Split `body` into (what stays on main, what moves to the archive).

    Moves whole dated sections, oldest first, until the remainder fits. Undated
    sections never move. The moved half is '' when nothing moved.
    """
    if len(body) <= limit:
        return body, ''
    preamble, sections = _split_sections(body)
    if not sections:
        return body, ''

    dated = []
    for n, (heading, _text) in enumerate(sections):
        d = _heading_date(heading)
        if d is not None and not _is_sticky(heading):
            dated.append((d, n))
    dated.sort()  # oldest first; ties keep page order

    moving = set()
    size = len(body)
    for _d, n in dated:
        if size <= limit:
            break
        moving.add(n)
        size -= len(sections[n][1]) + 1

    if not moving:
        return body, ''

    kept = '\n'.join([preamble] + [t for n, (_h, t) in enumerate(sections)
                                   if n not in moving])
    moved = '\n'.join(sections[n][1] for n in sorted(moving))
    return kept, moved


def _archive(moved):
    """Append aged-out sections to the archive page, creating it if needed."""
    page, _created = NotebookPage.objects.get_or_create(
        slug=ARCHIVE_SLUG,
        defaults={'title': 'Notebook Archive', 'body': ARCHIVE_STARTER})
    stamp = timezone.now().astimezone(GABS).strftime('%Y-%m-%d %H:%M')
    page.body = (page.body or ARCHIVE_STARTER).rstrip('\n') + (
        '\n\n<!-- moved off the main notebook ' + stamp
        + ' (Botswana time) -->\n\n' + moved.strip('\n') + '\n')
    page.save(update_fields=['body', 'updated_at'])


def _editors() -> set[str]:
    raw = getattr(settings, 'NOTEBOOK_EDITORS', None) or (
        'pganesharajah@alphadirect.co.bw', 'excoboard@alphadirect.co.bw')
    return {(a or '').strip().lower() for a in raw}


def _key_has_notebook_scope(request) -> bool:
    """True when the caller authenticated with an ApiKey carrying `notebook`."""
    api_key = getattr(request, 'auth', None)
    if api_key is None or not hasattr(api_key, 'allowed_scopes'):
        return False
    scopes = list(api_key.allowed_scopes or [])
    return 'notebook' in scopes or 'admin' in scopes


def _key_has_notebook_write_scope(request) -> bool:
    """True when the caller authenticated with an ApiKey carrying `notebook-write`.

    Deliberately a different check to `_key_has_notebook_scope` above — the
    read-only `notebook` scope must NEVER satisfy this. `notebook-write` is
    also outside READ_ONLY_SCOPES (core.api_key_auth), so it clears the
    authentication-layer gate before this even runs.
    """
    api_key = getattr(request, 'auth', None)
    if api_key is None or not hasattr(api_key, 'allowed_scopes'):
        return False
    scopes = list(api_key.allowed_scopes or [])
    return 'notebook-write' in scopes or 'admin' in scopes


def _may_read(request) -> bool:
    """Who may READ the notebook.

    DeepSeek review 2026-07-26 caught this and it was a real live hole: the raw
    endpoint originally carried only IsAuthenticated, so ANY logged-in account
    could read the page — proved on prod with `pbisen@theriskco.com`, an external
    platform partner, pulling all 5,517 characters including the GWP and PAT
    figures. The JSON endpoint was gated but the plain-text one was not, and the
    test only covered anonymous access, so nothing caught it.

    Reading is now exactly the same gate as editing, plus a scoped API key for
    headless reads. A key that can WRITE the page (`notebook-write`) can also
    read it — a write-only key that can save but never see what it just saved
    would be a strange, unusable shape, and the safe append pattern (GET
    current body, add to it, PUT the result back) needs the read half anyway.
    """
    return (_may_edit(getattr(request, 'user', None))
            or _key_has_notebook_scope(request)
            or _key_has_notebook_write_scope(request))


def _may_edit(user) -> bool:
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    if (getattr(user, 'email', '') or '').strip().lower() in _editors():
        return True
    profile = getattr(user, 'profile', None)
    return (getattr(profile, 'title', '') or '').lower() in ('cfo', 'ceo')


def _may_write_raw(request) -> bool:
    """Who may WRITE via the plain-text endpoint: a logged-in editor, or a key
    carrying `notebook-write`. Never satisfied by the read-only `notebook`
    scope — see _key_has_notebook_write_scope."""
    return _may_edit(getattr(request, 'user', None)) or _key_has_notebook_write_scope(request)


def _page(slug: str = DEFAULT_SLUG, *, create: bool = True) -> NotebookPage | None:
    """The page, or None when it does not exist and `create` says don't make one.

    `create=False` is what a read passes, because reading must never write. It
    used to: an unknown ?slug= went straight into get_or_create, so merely
    GETting a slug seeded a fresh STARTER page. That happened on prod on
    2026-08-15 — `claude-code-live-proof` (833 chars) appeared beside the real
    `main` (41,507 chars) and, being the newer row, anything reaching for the
    notebook by recency or `.first()` picked up the near-empty page and believed
    it was the notebook. This page is the documented source of truth that beats
    Omni's database, so a phantom copy is a correctness bug, not clutter.

    DEFAULT_SLUG still bootstraps on a read, so a fresh install has a notebook
    on day one without anyone having to save one first.
    """
    if not create and slug != DEFAULT_SLUG:
        return NotebookPage.objects.filter(slug=slug).first()
    page, created = NotebookPage.objects.get_or_create(
        slug=slug, defaults={'title': 'Notebook', 'body': STARTER})
    return page


@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
def notebook_raw(request):
    """Plain text. Deliberately no JSON, no envelope — one fetch/PUT, zero parsing."""
    slug = request.GET.get('slug') or DEFAULT_SLUG

    if request.method == 'PUT':
        if not _may_write_raw(request):
            return HttpResponse('This notebook is restricted.\n',
                                content_type='text/plain; charset=utf-8', status=403)
        # Fetched after the gate, and only here: saving may create a page,
        # reading may not, and a refused save must not create one either.
        page = _page(slug)
        body = request.body.decode('utf-8', errors='replace')
        # Age the page down BEFORE the hard cap, so a legitimate save is never
        # refused merely for being long — it trims itself instead. Only `main`
        # ages: the archive must never archive itself.
        moved_chars = 0
        if slug == DEFAULT_SLUG:
            body, moved = overflow_to_archive(body)
            if moved:
                _archive(moved)
                moved_chars = len(moved)
        if len(body) > NOTEBOOK_MAX_CHARS:
            return HttpResponse(
                f'Refused: {len(body):,} chars is over the {NOTEBOOK_MAX_CHARS:,}-char '
                f'one-page limit. Trim it before saving — this page only works if it '
                f'stays short enough to actually read.\n',
                content_type='text/plain; charset=utf-8', status=400)
        page.body = body
        page.updated_by = request.user if getattr(request.user, 'is_authenticated', False) else None
        page.save(update_fields=['body', 'updated_by', 'updated_at'])
        note = (f' Aged {moved_chars:,} chars of dated sections into the "{ARCHIVE_SLUG}" page.'
                if moved_chars else '')
        resp = HttpResponse(f'OK - saved {len(body):,} chars.{note}\n',
                            content_type='text/plain; charset=utf-8')
        resp['X-Notebook-Updated'] = page.updated_at.isoformat() if page.updated_at else ''
        resp['Cache-Control'] = 'no-store'
        return resp

    if not _may_read(request):
        return Response({'detail': 'This notebook is restricted.'},
                        status=status.HTTP_403_FORBIDDEN)
    # Gate first, then existence, so an outsider learns nothing about which
    # pages exist. Plain text on the 404 too — this endpoint promises no JSON.
    page = _page(slug, create=False)
    if page is None:
        return HttpResponse('No such notebook page.\n',
                            content_type='text/plain; charset=utf-8', status=404)
    resp = HttpResponse(page.body or '', content_type='text/plain; charset=utf-8')
    # So a caller can tell whether it changed without re-reading the whole thing.
    resp['X-Notebook-Updated'] = page.updated_at.isoformat() if page.updated_at else ''
    resp['Cache-Control'] = 'no-store'
    return resp


@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
def notebook_detail(request):
    slug = (request.GET.get('slug') or request.data.get('slug')
            if request.method == 'PUT' else request.GET.get('slug')) or DEFAULT_SLUG

    if request.method == 'GET':
        if not _may_read(request):
            return Response({'detail': 'This notebook is restricted.'},
                            status=status.HTTP_403_FORBIDDEN)
        page = _page(slug, create=False)
        if page is None:
            return Response({'detail': 'No such notebook page.'},
                            status=status.HTTP_404_NOT_FOUND)
        return Response({
            'slug': page.slug,
            'title': page.title,
            'body': page.body,
            'updated_at': page.updated_at,
            'updated_by': getattr(page.updated_by, 'get_full_name', lambda: '')()
                          or getattr(page.updated_by, 'username', ''),
        })

    if not _may_edit(request.user):
        return Response({'detail': 'You may not edit the notebook.'},
                        status=status.HTTP_403_FORBIDDEN)

    page = _page(slug)
    body = request.data.get('body')
    if body is None:
        return Response({'detail': 'body is required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # Age the page down BEFORE the hard cap, so a legitimate save is never
    # refused merely for being long — it trims itself instead. Only `main`
    # ages: the archive must never archive itself.
    moved_chars = 0
    if slug == DEFAULT_SLUG:
        body, moved = overflow_to_archive(body)
        if moved:
            _archive(moved)
            moved_chars = len(moved)
    # The SAME cap the plain-text endpoint enforces. It used to live only there,
    # so the limit bound the headless caller and left the browser — the path a
    # human actually types into — free to grow the page without bound. A guard on
    # one write path to a field is not a guard; it just moves the traffic.
    if len(body) > NOTEBOOK_MAX_CHARS:
        return Response(
            {'detail': f'Refused: {len(body):,} chars is over the '
                       f'{NOTEBOOK_MAX_CHARS:,}-char one-page limit. Trim it before saving — '
                       f'this page only works if it stays short enough to actually read.'},
            status=status.HTTP_400_BAD_REQUEST)
    page.body = body
    if request.data.get('title'):
        page.title = request.data['title'][:140]
    page.updated_by = request.user
    page.save(update_fields=['body', 'title', 'updated_by', 'updated_at'])
    return Response({'slug': page.slug, 'updated_at': page.updated_at,
                     'archived_chars': moved_chars,
                     'chars': len(page.body)})
