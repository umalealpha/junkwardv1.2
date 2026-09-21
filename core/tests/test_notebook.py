"""Shared CFO/Claude notebook (CFO 2026-07-25). Write-from-Claude-Code added
2026-08-15 (CFO: "create a backend to omni where we can update the notebook
from claude code").

The point of the page is that Claude reads it in one fast call before its first
reply, and that only the CFO/EXCO can see or change it. Both are tested here,
plus the thing most likely to go wrong later: someone "improving" the raw
endpoint into JSON, or leaving it open to any logged-in member of staff.

The write path gets the same scrutiny test_hr_extract_key.py gave the read-only
HR key: a `notebook-write` key must be able to PUT the raw page, the existing
read-only `notebook` key must NEVER be able to (that's the whole reason it's a
separate scope), and the one-page size cap must actually refuse an oversized
body rather than silently truncating or saving it.
"""
from __future__ import annotations

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import ApiKey, NotebookPage
from core.notebook_views import (ARCHIVE_SLUG, NOTEBOOK_MAX_CHARS,
                                 NOTEBOOK_SOFT_CHARS, _heading_date, _is_sticky,
                                 overflow_to_archive)

CFO = 'pganesharajah@alphadirect.co.bw'
PLAINTEXT_WRITE = 'aa11bb22cc33' + '0' * 52   # 64 hex, prefix = first 12
PLAINTEXT_READ  = 'dd44ee55ff66' + '0' * 52


def _make_key(service_user, scopes, plaintext):
    return ApiKey.objects.create(
        label='Notebook key (test)',
        key_prefix=plaintext[:12],
        key_hash=make_password(plaintext),
        service_user=service_user,
        allowed_scopes=scopes,
        is_active=True,
    )


@override_settings(NOTEBOOK_EDITORS=[CFO])
class NotebookTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.cfo = User.objects.create_user('pg', email=CFO, password='x')
        cls.staff = User.objects.create_user('worker',
                                             email='worker@alphadirect.co.bw',
                                             password='x')
        cls.write_svc = User.objects.create_user(
            'claude-notebook-writer', email='claude-notebook-writer@alphadirect.co.bw')
        cls.write_svc.set_unusable_password()
        cls.write_svc.save()
        cls.read_svc = User.objects.create_user(
            'claude-notebook-reader', email='claude-notebook-reader@alphadirect.co.bw')
        cls.read_svc.set_unusable_password()
        cls.read_svc.save()

    # ---- reading -------------------------------------------------------
    def test_raw_is_plain_text_not_json(self):
        """Claude reads this with no parsing. It must stay text/plain."""
        self.client.force_login(self.cfo)
        r = self.client.get(reverse('notebook-raw'))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r['Content-Type'].startswith('text/plain'))
        self.assertNotIn(b'{', r.content[:1])

    def test_first_read_creates_the_page_with_the_rules(self):
        self.client.force_login(self.cfo)
        r = self.client.get(reverse('notebook-raw'))
        self.assertIn(b'beats Omni', r.content)
        self.assertEqual(NotebookPage.objects.count(), 1)

    def test_raw_needs_a_login(self):
        self.assertIn(self.client.get(reverse('notebook-raw')).status_code,
                      (401, 403))

    def test_ordinary_staff_CANNOT_read_raw(self):
        """The hole DeepSeek found on 2026-07-26 — regression guard.

        The raw endpoint shipped with only IsAuthenticated, so every logged-in
        account could pull the whole page. Proved on prod with an external
        TheRiskCo partner account reading all 5,517 characters including the GWP
        and PAT figures. The original test only checked ANONYMOUS access, which
        is why nothing caught it. Read is now the same gate as edit.
        """
        self.client.force_login(self.staff)
        r = self.client.get(reverse('notebook-raw'))
        self.assertEqual(r.status_code, 403)
        self.assertNotIn(b'beats Omni', r.content)

    def test_read_and_edit_gates_cannot_drift_apart(self):
        """Whoever can read the JSON can read the text, and vice versa."""
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse('notebook-raw')).status_code,
                         self.client.get(reverse('notebook-detail')).status_code)

    def test_updated_header_lets_a_caller_skip_a_reread(self):
        self.client.force_login(self.cfo)
        r = self.client.get(reverse('notebook-raw'))
        self.assertTrue(r['X-Notebook-Updated'])

    # ---- who may see and change it -------------------------------------
    def test_ordinary_staff_cannot_read_the_notebook_page(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse('notebook-detail')).status_code, 403)

    def test_ordinary_staff_cannot_edit(self):
        self.client.force_login(self.staff)
        r = self.client.put(reverse('notebook-detail'),
                            data={'body': 'hacked'},
                            content_type='application/json')
        self.assertEqual(r.status_code, 403)
        # Asserted as "no page anywhere holds it" rather than reading `main`:
        # a refused save no longer creates the page before the gate, so the row
        # need not exist. That is the point — nothing is written before the gate.
        self.assertFalse(NotebookPage.objects.filter(body__contains='hacked').exists())

    def test_cfo_can_read_and_save(self):
        self.client.force_login(self.cfo)
        r = self.client.put(reverse('notebook-detail'),
                            data={'body': 'Bharath = Operations Manager in Training'},
                            content_type='application/json')
        self.assertEqual(r.status_code, 200)
        page = NotebookPage.objects.get(slug='main')
        self.assertIn('Operations Manager in Training', page.body)
        self.assertEqual(page.updated_by, self.cfo)

    def test_saved_text_comes_straight_back_out_of_raw(self):
        """What he types is exactly what Claude reads — no transformation."""
        self.client.force_login(self.cfo)
        text = '# Notes\n\nPako Kago = Financial Controller\nKago Tshutlhedi = Finance Manager\n'
        self.client.put(reverse('notebook-detail'), data={'body': text},
                        content_type='application/json')
        r = self.client.get(reverse('notebook-raw'))
        self.assertEqual(r.content.decode(), text)

    def test_superuser_may_edit(self):
        su = User.objects.create_superuser('root', email='root@x.bw', password='x')
        self.client.force_login(su)
        r = self.client.put(reverse('notebook-detail'), data={'body': 'ok'},
                            content_type='application/json')
        self.assertEqual(r.status_code, 200)

    def test_body_is_required_on_save(self):
        self.client.force_login(self.cfo)
        r = self.client.put(reverse('notebook-detail'), data={'title': 'x'},
                            content_type='application/json')
        self.assertEqual(r.status_code, 400)

    # ---- write-from-Claude-Code (CFO 2026-08-15) ------------------------
    def test_notebook_write_key_can_put_raw(self):
        """The whole point of the new scope: a headless PUT actually saves."""
        key = _make_key(self.write_svc, ['notebook-write'], PLAINTEXT_WRITE)
        r = self.client.put(reverse('notebook-raw'), data=b'New standing trap.\n',
                            content_type='text/plain',
                            HTTP_AUTHORIZATION=f'ApiKey {PLAINTEXT_WRITE}')
        self.assertEqual(r.status_code, 200)
        page = NotebookPage.objects.get(slug='main')
        self.assertEqual(page.body, 'New standing trap.\n')
        self.assertEqual(page.updated_by, self.write_svc)
        # and it comes straight back out of a plain GET, same as a human edit
        r2 = self.client.get(reverse('notebook-raw'),
                             HTTP_AUTHORIZATION=f'ApiKey {PLAINTEXT_WRITE}')
        self.assertEqual(r2.content.decode(), 'New standing trap.\n')
        key.delete()

    def test_read_only_notebook_key_CANNOT_put_raw(self):
        """The regression this scope split exists to prevent: a leaked read
        key must never be able to change the page. Enforced two layers deep —
        READ_ONLY_SCOPES at authentication, and _key_has_notebook_write_scope
        in the view — so this guards both."""
        _make_key(self.read_svc, ['notebook'], PLAINTEXT_READ)
        original = NotebookPage.objects.get_or_create(slug='main')[0].body
        r = self.client.put(reverse('notebook-raw'), data=b'hacked via read key',
                            content_type='text/plain',
                            HTTP_AUTHORIZATION=f'ApiKey {PLAINTEXT_READ}')
        self.assertIn(r.status_code, (401, 403))
        self.assertEqual(NotebookPage.objects.get(slug='main').body, original)

    def test_write_raw_refuses_an_oversized_body(self):
        """Rule 2 on the page itself — 'keep it to one page' — enforced here
        too, so a runaway script can't quietly turn one page into ten."""
        key = _make_key(self.write_svc, ['notebook-write'], PLAINTEXT_WRITE)
        too_big = 'x' * (NOTEBOOK_MAX_CHARS + 1)
        r = self.client.put(reverse('notebook-raw'), data=too_big.encode(),
                            content_type='text/plain',
                            HTTP_AUTHORIZATION=f'ApiKey {PLAINTEXT_WRITE}')
        self.assertEqual(r.status_code, 400)
        self.assertNotEqual(NotebookPage.objects.get(slug='main').body, too_big)
        key.delete()

    def test_ordinary_staff_cannot_put_raw(self):
        self.client.force_login(self.staff)
        r = self.client.put(reverse('notebook-raw'), data=b'hacked',
                            content_type='text/plain')
        self.assertEqual(r.status_code, 403)
        self.assertNotIn(b'hacked', NotebookPage.objects.get_or_create(slug='main')[0]
                         .body.encode())

    def test_anonymous_cannot_put_raw(self):
        r = self.client.put(reverse('notebook-raw'), data=b'hacked',
                            content_type='text/plain')
        self.assertIn(r.status_code, (401, 403))

    def test_cfo_session_can_also_put_raw(self):
        """Not just keys — the CFO's own logged-in session works on this
        endpoint too, so he isn't forced through the JSON form to fix a typo."""
        self.client.force_login(self.cfo)
        r = self.client.put(reverse('notebook-raw'), data=b'CFO typo fix.\n',
                            content_type='text/plain')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(NotebookPage.objects.get(slug='main').body, 'CFO typo fix.\n')

    # ---- a read must never create a page -------------------------------
    def test_raw_404s_on_an_unknown_slug_and_creates_nothing(self):
        """The prod trap of 2026-08-15 — regression guard.

        A plain GET with an unknown ?slug= used to run get_or_create, so reading
        seeded a brand-new STARTER page. It happened: `claude-code-live-proof`
        (833 chars) appeared beside the real `main` (41,507 chars) and, being the
        NEWER row, anything reaching for the notebook by recency or `.first()`
        picked up an almost-empty page and believed it was the notebook. This
        page is the documented source of truth that beats Omni's database, so a
        phantom copy is a correctness bug, not clutter.
        """
        self.client.force_login(self.cfo)
        self.client.get(reverse('notebook-raw'))          # bootstrap `main`
        before = set(NotebookPage.objects.values_list('slug', flat=True))

        r = self.client.get(reverse('notebook-raw'), {'slug': 'claude-code-live-proof'})

        self.assertEqual(r.status_code, 404)
        self.assertEqual(set(NotebookPage.objects.values_list('slug', flat=True)), before)
        self.assertEqual(NotebookPage.objects.count(), 1)

    def test_raw_404_stays_plain_text_like_the_rest_of_the_endpoint(self):
        """This endpoint promises no JSON envelope — not even on an error."""
        self.client.force_login(self.cfo)
        r = self.client.get(reverse('notebook-raw'), {'slug': 'nope'})
        self.assertEqual(r.status_code, 404)
        self.assertTrue(r['Content-Type'].startswith('text/plain'))

    def test_detail_404s_on_an_unknown_slug_and_creates_nothing(self):
        self.client.force_login(self.cfo)
        r = self.client.get(reverse('notebook-detail'), {'slug': 'no-such-page'})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(NotebookPage.objects.filter(slug='no-such-page').exists())

    def test_a_read_never_leaves_a_starter_page_behind(self):
        """No row may appear from reads alone, whichever endpoint is read.

        The orphan was recognisable by its body: untouched STARTER. Asserting
        the shape rather than one slug means any future read path that starts
        creating pages fails here too.
        """
        self.client.force_login(self.cfo)
        for slug in ('live-proof', 'test', 'scratch'):
            self.client.get(reverse('notebook-raw'), {'slug': slug})
            self.client.get(reverse('notebook-detail'), {'slug': slug})
        self.assertEqual(NotebookPage.objects.count(), 0)

    def test_a_refused_raw_put_creates_nothing_either(self):
        """Same phantom row, other door: an unauthorised PUT used to create the
        page before the permission check, so ordinary staff could litter the
        table with STARTER pages while being refused every time."""
        self.client.force_login(self.staff)
        r = self.client.put(reverse('notebook-raw') + '?slug=staff-scratch',
                            data=b'hacked', content_type='text/plain')
        self.assertEqual(r.status_code, 403)
        self.assertFalse(NotebookPage.objects.filter(slug='staff-scratch').exists())

    def test_default_slug_still_bootstraps_on_a_fresh_install(self):
        """Day one has no rows at all — the main page must still read."""
        NotebookPage.objects.all().delete()
        self.client.force_login(self.cfo)
        r = self.client.get(reverse('notebook-raw'))
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'beats Omni', r.content)

    def test_saving_a_new_slug_still_creates_it(self):
        """Writing may create — only reading may not."""
        self.client.force_login(self.cfo)
        r = self.client.put(reverse('notebook-detail') + '?slug=fy27-plan',
                            data={'body': 'FY27 notes'},
                            content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(NotebookPage.objects.get(slug='fy27-plan').body, 'FY27 notes')

    def test_raw_put_can_still_create_a_new_slug(self):
        """The headless save path keeps its create — it is a write."""
        self.client.force_login(self.cfo)
        r = self.client.put(reverse('notebook-raw') + '?slug=fy27-raw',
                            data=b'FY27 via raw\n', content_type='text/plain')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(NotebookPage.objects.get(slug='fy27-raw').body, 'FY27 via raw\n')

    def test_unknown_slug_hides_behind_the_read_gate_for_staff(self):
        """403 before 404 — an outsider learns nothing about what pages exist."""
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.get(reverse('notebook-raw'), {'slug': 'secret-page'}).status_code,
            403)

    def test_the_cap_is_above_the_real_page_so_headless_saves_are_not_locked_out(self):
        """The cap must never sit BELOW the page it is guarding.

        At 24,000 it did: the live page was 51,759 characters, so every save through
        the raw endpoint was refused while the browser page — which has no cap — kept
        working. A limit that only blocks the automated path drives edits to the
        un-capped one, which is the opposite of what the guard is for. Raised to
        60,000 with the CFO's approval on 19-Aug-2026.
        """
        self.assertGreaterEqual(NOTEBOOK_MAX_CHARS, 60_000)

    def test_a_page_the_size_of_the_real_one_saves_through_raw(self):
        """51,759 chars is what the live page actually held on 19-Aug-2026."""
        self.client.force_login(self.cfo)
        body = 'x' * 51_759
        r = self.client.put(reverse('notebook-raw'), data=body.encode(),
                            content_type='text/plain')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(NotebookPage.objects.get(slug='main').body), 51_759)

    def test_the_browser_json_save_obeys_the_same_cap_as_the_plain_text_one(self):
        """A guard on ONE write path to a field is not a guard — it moves the traffic.

        The cap lived only on the raw endpoint, so it bound the headless caller while
        the browser page (the one a human types into) could grow the notebook without
        limit. Fable 5 named this on 19-Aug-2026 while reviewing the cap raise. Both
        doors now use the same number.
        """
        self.client.force_login(self.cfo)
        too_big = 'x' * (NOTEBOOK_MAX_CHARS + 1)
        r = self.client.put(reverse('notebook-detail'), data={'body': too_big},
                            content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('one-page limit', r.data['detail'])
        self.assertNotEqual(NotebookPage.objects.get(slug='main').body, too_big)

    def test_both_endpoints_refuse_at_exactly_the_same_size(self):
        """Not "both have a cap" — the SAME cap. Two numbers would drift apart."""
        self.client.force_login(self.cfo)
        over = 'x' * (NOTEBOOK_MAX_CHARS + 1)
        raw = self.client.put(reverse('notebook-raw'), data=over.encode(),
                              content_type='text/plain')
        js = self.client.put(reverse('notebook-detail'), data={'body': over},
                             content_type='application/json')
        self.assertEqual((raw.status_code, js.status_code), (400, 400))

    def test_a_page_at_the_live_size_still_saves_through_the_browser_path(self):
        """Capping the browser path must not lock out the real page — 51,759 chars."""
        self.client.force_login(self.cfo)
        body = 'y' * 51_759
        r = self.client.put(reverse('notebook-detail'), data={'body': body},
                            content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(NotebookPage.objects.get(slug='main').body), 51_759)


class NotebookAutoArchiveTests(TestCase):
    """Auto-archive — CFO 2026-09-11: "if it exceeds charator size we create note book 2?"

    The rule these lock down: DATED sections age out oldest-first; UNDATED
    sections (the people, the frozen figures, the standing traps) NEVER move.
    """

    def _page(self, *sections):
        return '# NOTEBOOK\n\nRule 1 — this page beats Omni.\n\n' + '\n'.join(sections)

    def _section(self, heading, chars):
        return f'## {heading}\n' + ('x' * chars) + '\n'

    def test_under_the_limit_nothing_moves(self):
        body = self._page(self._section('Built 2026-01-01', 100))
        kept, moved = overflow_to_archive(body, limit=10_000)
        self.assertEqual(kept, body)
        self.assertEqual(moved, '')

    def test_oldest_dated_section_moves_first(self):
        body = self._page(
            self._section('Built 2026-08-01 old thing', 4_000),
            self._section('Built 2026-09-01 new thing', 4_000),
        )
        kept, moved = overflow_to_archive(body, limit=5_000)
        self.assertIn('2026-09-01 new thing', kept)
        self.assertNotIn('2026-08-01 old thing', kept)
        self.assertIn('2026-08-01 old thing', moved)

    def test_undated_section_is_never_moved(self):
        """The whole safety of this feature. Without the date check an aggressive
        trim would carry off PEOPLE / FROZEN NUMBERS — the page's entire point."""
        body = self._page(
            self._section('1. PEOPLE I KEEP GETTING WRONG', 9_000),
            self._section('3. FROZEN FINANCIAL NUMBERS', 9_000),
        )
        kept, moved = overflow_to_archive(body, limit=1_000)
        self.assertEqual(moved, '')
        self.assertIn('PEOPLE I KEEP GETTING WRONG', kept)
        self.assertIn('FROZEN FINANCIAL NUMBERS', kept)

    def test_preamble_and_rules_always_survive(self):
        body = self._page(self._section('Built 2026-07-01', 9_000))
        kept, _moved = overflow_to_archive(body, limit=500)
        self.assertIn('Rule 1 — this page beats Omni.', kept)

    def test_it_stops_as_soon_as_it_fits(self):
        """Moves the minimum, not everything dated."""
        body = self._page(
            self._section('Built 2026-07-01 oldest', 3_000),
            self._section('Built 2026-08-01 middle', 3_000),
            self._section('Built 2026-09-01 newest', 3_000),
        )
        kept, moved = overflow_to_archive(body, limit=7_000)
        self.assertNotIn('oldest', kept)
        self.assertIn('middle', kept)
        self.assertIn('newest', kept)

    def test_every_date_shape_the_real_page_uses(self):
        for heading in ('Built 2026-08-06 thing', 'SETTLED 9-Aug-2026',
                        'LIVE 27 Jul 2026', 'UPDATE 18-AUG-2026'):
            self.assertIsNotNone(_heading_date(heading), heading)
        for heading in ('1. PEOPLE I KEEP GETTING WRONG', 'STANDING TRAPS',
                        'MAILBOXES — what is really behind them'):
            self.assertIsNone(_heading_date(heading), heading)

    def test_saving_a_long_page_archives_instead_of_refusing(self):
        """End to end through the real endpoint: the save SUCCEEDS and the
        overflow lands on the archive page. Before this, a page over the cap was
        refused outright — which is how the live page sat unsaveable for weeks."""
        user = User.objects.create_user(
            username='cfo-archive', email='pganesharajah@alphadirect.co.bw',
            password='x', is_superuser=True)
        self.client.force_login(user)
        body = self._page(
            self._section('1. PEOPLE I KEEP GETTING WRONG', 2_000),
            self._section('Built 2026-07-01 ancient', 50_000),
            self._section('Built 2026-09-10 recent', 2_000),
        )
        resp = self.client.put('/api/v1/notebook/raw/', data=body.encode('utf-8'),
                               content_type='text/plain')
        self.assertEqual(resp.status_code, 200, resp.content)

        main = NotebookPage.objects.get(slug='main').body
        self.assertIn('PEOPLE I KEEP GETTING WRONG', main)
        self.assertIn('2026-09-10 recent', main)
        self.assertNotIn('2026-07-01 ancient', main)
        self.assertLessEqual(len(main), NOTEBOOK_SOFT_CHARS)

        archive = NotebookPage.objects.get(slug=ARCHIVE_SLUG).body
        self.assertIn('2026-07-01 ancient', archive)

    def test_the_archive_page_never_archives_itself(self):
        NotebookPage.objects.create(slug=ARCHIVE_SLUG, title='Notebook Archive',
                                    body='seed')
        user = User.objects.create_user(
            username='cfo-arch2', email='pganesharajah@alphadirect.co.bw',
            password='x', is_superuser=True)
        self.client.force_login(user)
        body = self._page(self._section('Built 2026-07-01 old', 50_000))
        resp = self.client.put('/api/v1/notebook/raw/?slug=' + ARCHIVE_SLUG,
                               data=body.encode('utf-8'), content_type='text/plain')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIn('2026-07-01 old',
                      NotebookPage.objects.get(slug=ARCHIVE_SLUG).body)

    def test_a_settled_fact_never_ages_out_even_though_it_is_dated(self):
        """Found on the LIVE page, 2026-09-11, on the first real overflow: it
        carried off 'Titles & reporting lines — SETTLED 26-Jul-2026 (do not
        re-ask)' and 'Email addresses — the ones I keep needing (26-Jul-2026)'.
        Both record WHEN they were settled, not an event. A dated heading that
        marks itself as standing must stay."""
        body = self._page(
            self._section('Titles & reporting lines — SETTLED 26-Jul-2026 (do not re-ask)', 9_000),
            self._section('Built 2026-08-01 an actual event', 9_000),
        )
        kept, moved = overflow_to_archive(body, limit=10_000)
        self.assertIn('do not re-ask', kept)
        self.assertNotIn('do not re-ask', moved)
        self.assertIn('an actual event', moved)

    def test_the_real_headings_that_must_never_age_out(self):
        for heading in (
                'Titles & reporting lines — SETTLED 26-Jul-2026 (do not re-ask)',
                'Email addresses — the ones I keep needing (26-Jul-2026)',
                'System emails must NOT make the CFO the enforcer (HARD RULE, 28-Jul-2026)',
                'REALPAY - SETTLED, DO NOT ASK THE CFO AGAIN (11-Sep-2026)',
                'SETTLED (2026-08-08): DUPLICATE ACCOUNTS — DO NOT RE-RAISE.'):
            self.assertTrue(_is_sticky(heading), heading)
        for heading in ('Built 2026-09-04, live (prod): Omni phone app ROUND 2',
                        'Veritas · Parts & Savings — LIVE 10-Sep-2026 (PR #839)'):
            self.assertFalse(_is_sticky(heading), heading)

    def test_an_open_item_never_ages_out(self):
        """Second live run, 2026-09-12: it aged out 'People-data gaps still OPEN
        (26-Jul-2026)'. An unfinished item is not a closed event, however old the
        date beside it — archiving it is how something quietly stops being chased."""
        for heading in ('People-data gaps still OPEN (26-Jul-2026)',
                        'Still OPEN and costing money (18-Aug-2026)',
                        'Outstanding with Kutlo 14-Sep-2026',
                        'PENDING CFO input 2026-08-06'):
            self.assertTrue(_is_sticky(heading), heading)
        body = self._page(
            self._section('People-data gaps still OPEN (26-Jul-2026)', 9_000),
            self._section('Built 2026-08-01 a finished thing', 9_000),
        )
        kept, moved = overflow_to_archive(body, limit=10_000)
        self.assertIn('still OPEN', kept)
        self.assertIn('a finished thing', moved)
