"""
underwriting/render.py

Server-side A4 PDF render of an underwriting document via headless Chromium
(Playwright). Used for the emailed + stored (audit) copies, which must match
the in-browser preview pixel-for-pixel. `window.print()` in the user's browser
handles the free "Save PDF" path; this is the server-owned path.

Security (Fable audit 2026-07-08): field/doctype/fmt values are attacker-
influenceable (typed, or AI-extracted from a hostile upload). They are JSON-
encoded AND `</`-escaped before injection so a value can't break out of the
fill <script> and run arbitrary JS inside the (unsandboxed) server browser
(SSRF / PDF tampering). doctype/fmt are also validated against an allowlist.
Concurrent renders are capped so a burst can't OOM the box.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from django.conf import settings

_TEMPLATE = (Path(__file__).resolve().parent
             / 'templates' / 'underwriting' / 'tool.html')

VALID_DOCTYPES = {'cn', 'cnfi', 'wca'}
VALID_FORMATS = {'orig', 'cool', 'royal', 'formal'}

# Cap concurrent Chromium launches (each ~150-300 MB) so a burst of /render or
# /issue calls can't exhaust the box. Tunable via settings.
_RENDER_SEM = threading.BoundedSemaphore(
    int(getattr(settings, 'UNDERWRITING_MAX_CONCURRENT_RENDERS', 2)))

_FILL = """
<script>
window.addEventListener('load', function(){
  var F = %(fields)s;
  var DT = %(doctype)s, FMT = %(fmt)s;
  for (var k in F){ var e=document.getElementById(k); if(e) e.value = F[k]; }
  var dt=document.querySelector('input[name=dt][value="'+DT+'"]'); if(dt){ dt.checked=true; }
  var fm=document.querySelector('input[name=fmt][value="'+FMT+'"]'); if(fm){ fm.checked=true; }
  if (typeof applyVisibility==='function'){ applyVisibility(); }
  if (typeof render==='function'){ render(); }
  document.dispatchEvent(new Event('input'));
  window.__ADRENDERED__ = true;
});
</script>
"""


class RenderUnavailable(RuntimeError):
    """Browser engine missing / renderer busy — caller falls back to Save-PDF."""


def _esc(s: str) -> str:
    # `</` can terminate the <script> block even inside a JS string literal.
    return s.replace('</', '<\\/')


def _filled_html(doctype: str, fmt: str, fields: dict) -> str:
    html = _TEMPLATE.read_text(encoding='utf-8')
    fill = _FILL % {
        'fields':  _esc(json.dumps(fields or {})),
        'doctype': _esc(json.dumps(doctype or 'wca')),
        'fmt':     _esc(json.dumps(fmt or 'orig')),
    }
    return html + fill


def render_pdf(doctype: str, fmt: str, fields: dict) -> bytes:
    """A4 PDF bytes. Raises ValueError on bad input, RenderUnavailable if the
    browser is missing or the renderer is saturated."""
    if doctype not in VALID_DOCTYPES:
        raise ValueError(f'Unknown document type: {doctype!r}.')
    if fmt and fmt not in VALID_FORMATS:
        raise ValueError(f'Unknown certificate format: {fmt!r}.')
    if not isinstance(fields, dict):
        raise ValueError('fields must be an object.')

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        raise RenderUnavailable(f'PDF renderer not installed: {e}') from e

    html = _filled_html(doctype, fmt, fields)
    timeout_ms = int(getattr(settings, 'UNDERWRITING_RENDER_TIMEOUT_MS', 30000))

    if not _RENDER_SEM.acquire(timeout=45):
        raise RenderUnavailable('The document renderer is busy — try again in a moment.')
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                args=['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'])
            try:
                page = browser.new_page()
                page.set_content(html, wait_until='load', timeout=timeout_ms)
                page.emulate_media(media='print')
                page.wait_for_function('window.__ADRENDERED__ === true', timeout=timeout_ms)
                pdf = page.pdf(format='A4', print_background=True,
                               margin={'top': '0', 'right': '0', 'bottom': '0', 'left': '0'},
                               prefer_css_page_size=True)
            finally:
                browser.close()
    except RenderUnavailable:
        raise
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "Executable doesn" in msg or 'looks like Playwright' in msg or 'install' in msg.lower():
            raise RenderUnavailable(msg) from e
        raise ValueError(f'PDF render failed: {msg}') from e
    finally:
        _RENDER_SEM.release()

    if not pdf:
        raise ValueError('PDF render produced no bytes.')
    return pdf
