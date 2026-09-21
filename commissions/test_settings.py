"""Isolated test settings for the commissions app.

The full project test DB can't build on SQLite because core/ledger/payments/
procurement ship Postgres-only RunSQL migrations (triggers/indexes). The
commissions models depend only on the *abstract* core.BaseModel, so the app's
tables need no core table — we can run its suite against SQLite with a minimal
app set (its own portable migrations, including the group seed, still run).

Run:
  DB_ENGINE=sqlite SECRET_KEY=x \
    python manage.py test commissions --settings=commissions.test_settings

(On the real Postgres DB the app runs under the normal settings — this file is
only a fast, dependency-free way to verify the app in a shared checkout.)
"""
from alpha_finance.settings import *  # noqa: F401,F403

DATABASES = {
    'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}
}

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'rest_framework',
    'rest_framework.authtoken',
    'core',          # commissions uses the abstract core.BaseModel; core's own
                     # migrations are SQLite-safe (unlike ledger/payments/procurement)
    'commissions',
]

# No custom auth backends / middleware needed for the model + service suite
# (tests call the service + models directly, not over HTTP).
AUTHENTICATION_BACKENDS = ['django.contrib.auth.backends.ModelBackend']
MIDDLEWARE = []
# The real root urlconf imports every app's views; use an empty one here.
ROOT_URLCONF = 'commissions.test_urls'
