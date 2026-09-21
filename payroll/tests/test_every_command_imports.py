"""Every management command must at least IMPORT.

WHY (CFO, 2026-09-14): `feed_salary_advances` and `feed_severance` shipped to
prod carrying a Python syntax error — an apostrophe inside a single-quoted
help string ("this period's payslips"). Both commands were impossible to run:
they died on import, before argument parsing, every single time.

Nothing caught it. The unit tests for those builds exercise the service layer
directly and never load the command module, `manage.py check` does not import
management commands, and CI was green on all six checks. The only thing that
found it was running the command on the live server.

So this test loads every command module in the project and fails if any one of
them cannot be imported. It is deliberately dumb: it asserts nothing about
behaviour, only that the file is loadable, which is the cheapest possible floor
under "the command exists".
"""
from __future__ import annotations

import pkgutil
from importlib import import_module

from django.apps import apps
from django.test import SimpleTestCase


def _command_modules():
    for config in apps.get_app_configs():
        try:
            pkg = import_module(f"{config.name}.management.commands")
        except ModuleNotFoundError:
            continue
        for info in pkgutil.iter_modules(pkg.__path__):
            if not info.name.startswith("_"):
                yield f"{config.name}.management.commands.{info.name}"


class EveryCommandImportsTests(SimpleTestCase):
    def test_every_management_command_module_imports(self):
        broken = []
        found = 0
        for dotted in _command_modules():
            found += 1
            try:
                import_module(dotted)
            except Exception as exc:                      # noqa: BLE001
                broken.append(f"{dotted}: {type(exc).__name__}: {exc}")

        self.assertGreater(found, 50, "command discovery found almost nothing")
        self.assertEqual(
            broken, [],
            "management commands that cannot be imported:\n  " + "\n  ".join(broken),
        )
