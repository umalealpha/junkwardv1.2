"""Every named route the URLConf currently exposes.

Exists because routes have been silently LOST by merges, not by anyone
deciding to remove them. 2026-08-17: a PR that branched from an older copy of
alpha_finance/api_router.py reverted four already-shipped features' routes on
merge (cash flow, reinsurance history, records file-requests, DPIA register).
Each one surfaced only as an unrelated-looking 404 inside its own feature's
test file, so the real story — "one merge switched off four features" — was
invisible until someone read all 41 failures together.

core/tests/test_route_manifest.py diffs this against a committed baseline, so
a dropped route fails as ONE obvious error naming exactly what vanished.
"""
from django.urls import get_resolver
from django.urls.resolvers import URLPattern, URLResolver


def all_route_names() -> set[str]:
    """Every non-anonymous route name in the URLConf, namespaced as reverse()
    would take it (`admin:index`), so two apps' same-named routes stay distinct."""
    found: set[str] = set()

    def walk(patterns, prefix=""):
        for p in patterns:
            if isinstance(p, URLResolver):
                ns = f"{prefix}{p.namespace}:" if p.namespace else prefix
                walk(p.url_patterns, ns)
            elif isinstance(p, URLPattern) and p.name:
                found.add(f"{prefix}{p.name}")

    walk(get_resolver().url_patterns)
    return found
