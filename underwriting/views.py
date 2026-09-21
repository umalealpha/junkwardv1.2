"""
underwriting/views.py

Serves the underwriting tool (form + live A4 preview + the four WCA looks,
brand assets inline). The template embeds Alpha Direct's real signature + stamp,
so it must NOT be openable by anyone with the URL — a stranger could type any
numbers and Save-PDF a forged, signed, stamped cover note (Fable audit
2026-07-08). It is therefore gated behind a SHORT-LIVED signed token that only
an authenticated Omni user can mint (see UnderwritingDocumentViewSet.tool_token);
the authed parent page fetches the token and puts it in the iframe src.
"""
from django.core import signing
from django.http import HttpResponse, HttpResponseForbidden
from django.template.loader import render_to_string
from django.views.decorators.clickjacking import xframe_options_exempt

TOOL_TOKEN_SALT = 'underwriting-tool'
TOOL_TOKEN_MAX_AGE = 900  # 15 minutes


@xframe_options_exempt
def underwriting_tool_html(request):
    token = request.GET.get('t', '')
    try:
        signing.loads(token, salt=TOOL_TOKEN_SALT, max_age=TOOL_TOKEN_MAX_AGE)
    except signing.BadSignature:
        return HttpResponseForbidden(
            'This underwriting tool must be opened from inside Omni.')
    html = render_to_string('underwriting/tool.html')
    resp = HttpResponse(html, content_type='text/html; charset=utf-8')
    # Same-origin embedding only (Omni serves the iframe parent).
    resp['Content-Security-Policy'] = "frame-ancestors 'self'"
    return resp
