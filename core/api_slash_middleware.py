"""core/api_slash_middleware.py — LOCAL DOCKER: tolerate a missing trailing
slash on /api/ requests.

The Next.js proxy that fronts this backend in the local Docker stack drops the
trailing slash when it rewrites ``/api/:path*`` -> ``<backend>/api/...`` (its
path-to-regexp discards the empty trailing segment). Django's ``APPEND_SLASH``
then RAISES on a POST to the slash-less URL, because it cannot 301-redirect a
request that carries a body. That surfaced as the login "Something went wrong".

This middleware re-adds the slash IN PLACE (no redirect, POST body preserved) so
the request resolves against the normal slash-terminated URLconf. It is scoped to
``/api/`` paths whose final segment is not a file (no dot), so static, media and
file downloads are left untouched. Harmless in any environment: a path that
already ends in ``/`` is not modified.
"""
from __future__ import annotations


class ApiTrailingSlashMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        p = request.path_info
        if (p.startswith('/api/') and not p.endswith('/')
                and '.' not in p.rsplit('/', 1)[-1]):
            request.path_info = p + '/'
            if not request.path.endswith('/'):
                request.path = request.path + '/'
        return self.get_response(request)
