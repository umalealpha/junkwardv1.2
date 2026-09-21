"""Manager Accountability — the "Answer now" link must be reachable.

Bug d3caf386 (kbotana, 2026-07-30): the note linked to
/hris/manager-accountability/answer/, but the reverse proxy only forwards
/api/*, /admin/*, /static/*, /media/* and /hris/api/* to Django — every other
path is served by the Next.js frontend. So the link 404'd for every manager,
and all three were then escalated to the CEO/COO/Human Capital for the silence
they had no way to break.

These tests pin the two things that broke:
  1. the URL the email actually emits is on a proxy-forwarded prefix, and
  2. that URL resolves to the answer view.
"""
from __future__ import annotations

from django.test import SimpleTestCase
from django.urls import resolve, reverse

# Mirrors the @django matcher in /etc/caddy/Caddyfile. A public, no-login page
# on any other prefix is served by the frontend and cannot work.
PROXIED_PREFIXES = ('/api/', '/api-token-auth/', '/admin/', '/static/', '/media/', '/hris/api/')


class ManagerAccountabilityLinkTests(SimpleTestCase):
    def test_answer_url_is_on_a_proxied_prefix(self):
        url = reverse('hris:manager_accountability_answer')
        self.assertTrue(
            url.startswith(PROXIED_PREFIXES),
            f'{url} is not forwarded to Django by the proxy — it would 404 on the frontend.',
        )

    def test_answer_url_resolves_to_the_answer_view(self):
        url = reverse('hris:manager_accountability_answer')
        self.assertEqual(resolve(url).func.__name__, 'answer_page')

    def test_legacy_url_still_resolves_for_already_emailed_notes(self):
        url = reverse('hris:manager_accountability_answer_legacy')
        self.assertEqual(resolve(url).func.__name__, 'answer_page')

    def test_emailed_link_uses_the_proxied_path(self):
        """The command builds the URL by hand — pin the string it emits."""
        import inspect

        from hris.management.commands import send_manager_accountability as cmd

        src = inspect.getsource(cmd)
        self.assertIn("answer_path = '/hris/api/manager-accountability/answer/'", src)
        self.assertNotIn("/hris/manager-accountability/answer/", src)
