"""
core/aria/news.py — ARIA news feed.

CFO directive 2026-05-22: ARIA pops up very-very-important news once
every 3 hours unless something major lands sooner.

Pulls Google News RSS for a fixed set of Alpha Direct-relevant queries.
Tags each item with a deterministic severity (1-5) based on keyword
matches; severity ≥ 4 is `major` and bypasses the cooldown on the
frontend side.

Hard rules:
  * No paid API. Google News RSS is free + keyless.
  * Server-side cache for 15 minutes so a busy dashboard doesn't hammer
    Google.
  * Pure-Python parser — uses xml.etree, no extra deps.
  * Timeout 8s per query; failures silently drop that query.
"""

from __future__ import annotations

import re
import time
from urllib.parse import quote_plus

# Security audit 2026-06-09 — ruff S314. Google News RSS is untrusted —
# stdlib xml.etree is vulnerable to billion-laughs / XXE. Use defusedxml,
# fall back to stdlib only if the package is not installed (it IS in
# requirements.txt; the fallback exists so module import never breaks
# a dev env that hasn't pip-installed yet).
try:
    import defusedxml.ElementTree as ET    # type: ignore
except ImportError:                          # pragma: no cover
    # Fallback is only reached if defusedxml isn't installed — primary
    # import succeeds in prod (requirements.txt pins defusedxml>=0.7.1).
    import xml.etree.ElementTree as ET      # noqa: S405,S314

import requests
from django.core.cache import cache


# Queries we run against Google News. Tuned for Alpha Direct's reading.
# Add / remove without code changes elsewhere.
QUERIES = [
    'Botswana insurance regulation',
    'NBFIRA Botswana',
    'BURS tax Botswana',
    'Botswana Pula exchange rate',
    'FNB Botswana',
    'Botswana banking sector',
    'Alpha Direct Insurance',
    'Bank of Botswana monetary policy',
    'IFRS 17 insurance contracts',
    'reinsurance market Africa',
]

# Severity 1 (informational) → 5 (red alert). Keyword class buckets.
HIGH_KEYWORDS = (
    'scandal', 'fraud', 'breach', 'hack', 'leak', 'lawsuit', 'sanction',
    'liquidation', 'bankruptcy', 'insolvent', 'collapse', 'arrest', 'raid',
    'suspended', 'revoke', 'revocation', 'cease and desist',
    'critical', 'emergency', 'crisis', 'shutdown', 'outage',
    'devaluation', 'crash', 'plunge', 'default', 'downgrade',
)
MEDIUM_KEYWORDS = (
    'investigation', 'fine', 'penalty', 'warning', 'recall', 'audit',
    'inquiry', 'probe', 'review', 'reform', 'amendment', 'regulation',
    'increase', 'cut', 'hike', 'rate', 'announce',
    'approve', 'reject', 'merger', 'acquisition',
)


def _score(title: str, summary: str) -> int:
    text = f'{title}. {summary}'.lower()
    hits_h = sum(1 for kw in HIGH_KEYWORDS   if kw in text)
    hits_m = sum(1 for kw in MEDIUM_KEYWORDS if kw in text)
    if hits_h >= 2:
        return 5
    if hits_h == 1:
        return 4
    if hits_m >= 2:
        return 3
    if hits_m == 1:
        return 2
    return 1


def _strip_html(s: str) -> str:
    return re.sub(r'<[^>]+>', '', s or '').strip()


def _parse_pubdate(s: str) -> str:
    return (s or '').strip()


def _fetch_query(query: str, timeout: float = 8.0) -> list[dict]:
    url = (
        'https://news.google.com/rss/search?'
        f'q={quote_plus(query)}&hl=en-GB&gl=BW&ceid=BW:en'
    )
    try:
        r = requests.get(url, timeout=timeout, headers={
            'User-Agent': 'Mozilla/5.0 (ARIA omni news fetcher)',
        })
        if r.status_code != 200 or not r.content:
            return []
        # noqa: S314 — `ET` resolves to defusedxml.ElementTree at runtime
        # (see top-of-module import). Ruff sees the fallback alias name.
        root = ET.fromstring(r.content)  # noqa: S314
    except (requests.RequestException, ET.ParseError):
        return []

    out: list[dict] = []
    # RSS 2.0 — items under channel/item
    for item in root.iter('item'):
        title   = _strip_html((item.findtext('title')       or '').strip())
        summary = _strip_html((item.findtext('description') or '').strip())
        link    = (item.findtext('link') or '').strip()
        pub     = _parse_pubdate(item.findtext('pubDate') or '')
        source_el = item.find('source')
        source = (source_el.text or '').strip() if source_el is not None else ''
        if not title:
            continue
        sev = _score(title, summary)
        out.append({
            'title':   title[:240],
            'summary': summary[:600],
            'link':    link,
            'source':  source[:80],
            'published': pub,
            'severity':  sev,
            'major':     sev >= 4,
            'query':     query,
        })
    return out


def fetch_news(force: bool = False) -> list[dict]:
    """Return a deduped, severity-sorted list of news items.

    Cached server-side for 15 minutes (cache key 'aria_news_feed_v1').
    `force=True` bypasses the cache — used by an admin debug route.
    """
    KEY = 'aria_news_feed_v1'
    if not force:
        cached = cache.get(KEY)
        if cached is not None:
            return cached

    all_items: list[dict] = []
    for q in QUERIES:
        all_items.extend(_fetch_query(q))

    # Dedupe by title (case-insensitive); keep the highest severity instance.
    by_title: dict[str, dict] = {}
    for it in all_items:
        key = it['title'].lower()
        prev = by_title.get(key)
        if prev is None or it['severity'] > prev['severity']:
            by_title[key] = it

    items = list(by_title.values())
    items.sort(key=lambda x: (-x['severity'], x['published']), reverse=False)
    # Cap to top 30 so the JSON stays small.
    items = items[:30]

    cache.set(KEY, items, 15 * 60)
    return items


def top_item(force: bool = False) -> dict | None:
    items = fetch_news(force=force)
    return items[0] if items else None
