"""Pure-Python unit tests for the recovery helpers — pytest only.

These test `claims/recoveries/*.py`, which import no Django. They use pytest
features (parametrize), so Django's unittest-based test discovery cannot import
them. The `load_tests` hook below makes `manage.py test claims` skip this
package cleanly; run these with pytest instead:

    python -m pytest claims/recoveries/tests -q
"""
import unittest


def load_tests(loader, standard_tests, pattern):  # noqa: ARG001
    return unittest.TestSuite()
