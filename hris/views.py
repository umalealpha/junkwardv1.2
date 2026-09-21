"""
hris/views.py

Serves the HRIS single-page application. The HTML lives in
hris/templates/hris.html — it's the CFO-supplied design renamed from
"Talent Hub" with embedded credentials (per CFO directive 2026-05-10).

As of the Omni HRIS port (2026-05-13), /hris/ redirects to the new
Next.js landing at /hris by default. Add ?legacy=1 to force the old
SPA — links labelled "Legacy" in the new UI use that flag.

CFO directive 2026-05-18: HRIS contains payroll data and is restricted
to a five-person whitelist (Prathap, Arun, Kago, Pako, Unami) plus
superusers / administrators. The whitelist is enforced server-side here
so the legacy SPA cannot be reached by anyone who knows the URL.
"""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.clickjacking import xframe_options_exempt

from core.hris_access import user_can_access_hris


@xframe_options_exempt
@login_required
def hris_app(request):
    """Serves the HRIS SPA, or redirects to the new Omni landing.

    Whitelist gate runs BEFORE rendering so unauthorised users never
    receive the payroll-bearing HTML — they bounce to /dashboard.
    """
    if not user_can_access_hris(request.user):
        return HttpResponseRedirect('/dashboard')
    if request.GET.get('legacy') == '1':
        return render(request, 'hris.html')
    return HttpResponseRedirect('/hris')
