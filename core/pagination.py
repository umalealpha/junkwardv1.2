"""
core/pagination.py

Project-wide DRF pagination class with a hard cap so a misbehaving client
or a malicious actor can't request `?page_size=999999` and force the API
to materialise every row in memory.

Plugged into REST_FRAMEWORK.DEFAULT_PAGINATION_CLASS in alpha_finance/settings.py
(CFO structural-audit directive 2026-05-19).
"""

from __future__ import annotations

from rest_framework.pagination import PageNumberPagination


class CappedPageNumberPagination(PageNumberPagination):
    """
    DRF page-number pagination with a strict client cap.

    Defaults:
      * page size 25 (clients fetch this when no override sent)
      * `?page_size=` query param honoured up to ``max_page_size``
      * absolute ceiling of 100 — refuses bigger pages

    Why bother:
      Default DRF PageNumberPagination doesn't expose a page_size_query_param,
      so clients can't request bigger pages even when they need to. We want
      to allow bigger pages for legitimate use (e.g. exporting a TB), but
      not unlimited.
    """

    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100
