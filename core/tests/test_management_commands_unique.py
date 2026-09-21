"""No two apps may ship a management command with the same name.

Django resolves a duplicate name to whichever app is listed FIRST in
INSTALLED_APPS and says nothing. On 2026-09-04 core gained a push command
called send_morning_brief, hris already had the emailed send_morning_brief,
and core (listed first) silently took over: the HR cron would have sent a
push instead of the brief, and five hris tests died on
"unrecognized arguments: --date". This test makes the next clash loud.
"""
from collections import defaultdict

from django.apps import apps
from django.core.management import find_commands
from django.test import SimpleTestCase


class ManagementCommandNamesAreUniqueTests(SimpleTestCase):
    def test_no_command_name_is_shared_by_two_apps(self):
        owners = defaultdict(list)
        for cfg in apps.get_app_configs():
            if not cfg.name.startswith('django.') and 'site-packages' not in cfg.path:
                for name in find_commands(f'{cfg.path}/management'):
                    owners[name].append(cfg.label)
        clashes = {n: a for n, a in owners.items() if len(a) > 1}
        self.assertEqual(clashes, {}, f'shadowed management commands: {clashes}')
