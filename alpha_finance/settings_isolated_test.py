"""Test settings that give this run its OWN test database.

Several people (and agents) run `manage.py test` against the same container at
once. Django names the test database `test_<NAME>` for everybody, so two runs
collide and the second is prompted to delete the first's database mid-run.
Set OMNI_TEST_DB_SUFFIX to anything unique and the collision goes away.

    DJANGO_SETTINGS_MODULE=alpha_finance.settings_isolated_test \
    OMNI_TEST_DB_SUFFIX=iw python manage.py test banking.test_integrity_watch --noinput
"""
import os

from .settings import *  # noqa: F401,F403

_suffix = os.environ.get('OMNI_TEST_DB_SUFFIX', '')
if _suffix:
    DATABASES['default'].setdefault('TEST', {})          # noqa: F405
    DATABASES['default']['TEST']['NAME'] = (             # noqa: F405
        f"test_{DATABASES['default'].get('NAME', 'omni')}_{_suffix}"  # noqa: F405
    )
