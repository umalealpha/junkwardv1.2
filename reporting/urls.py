"""
Legacy report mount: /api/reports/…

THIS FILE NO LONGER AUTHORS ROUTES. It mirrors them.

Until 2026-09-11 there were two hand-maintained lists of report addresses —
this one under /api/reports/, and the report block of alpha_finance/api_router.py
under /api/v1/reports/. The frontend only ever calls /api/v1 (api.ts sets
API_BASE = ${BASE_URL}/api/v1), so a report added here and forgotten there was
invisible to the entire product.

That shipped twice. The cash-flow report in August 2026 and the market benchmark
on 10 September 2026 both reached production with a working menu entry and a
missing data address, and both showed users a raw error. CI was green both
times, because two lists disagreeing is not something a unit test notices unless
you write one specifically to compare them — and by then you have already
accepted that the design needs a guard.

So: api_router.py is now the ONE authored list. Everything under 'reports/'
there is re-exposed here automatically. A new report cannot exist in one and not
the other, because there is only one.

The mirrored routes are registered WITHOUT names. The v1 entries keep theirs
(name='v1-cash-flow' and friends) and code reverses those — duplicating the
names here would make reverse() resolve to whichever Django loaded last, which
is exactly the kind of quiet ambiguity this file exists to remove.

WHY KEEP THE LEGACY PATH AT ALL
Two days of production access logs show ZERO hits on /api/reports/ against
steady traffic on /api/v1/reports/, so it is almost certainly dead. But log
rotation means two days is all we can see, and n8n flows, the Manus scripts and
the Windows machine are all plausible callers we cannot grep. Mirroring costs
nothing and breaks nobody; deleting on two days of evidence could break a job
nobody remembers. Remove the mount from alpha_finance/urls.py once there is a
fortnight of confirmed silence.
"""
from django.urls import path

from alpha_finance.api_router import urlpatterns as _v1_patterns

_PREFIX = 'reports/'

# Re-expose every /api/v1/reports/<x>/ route at /api/reports/<x>/, unnamed.
urlpatterns = [
    path(str(p.pattern)[len(_PREFIX):], p.callback)
    for p in _v1_patterns
    if str(getattr(p, 'pattern', '')).startswith(_PREFIX) and getattr(p, 'callback', None)
]
