"""Public download of the Omni Desktop installer (Windows .exe).

Plain Django view (no DRF auth) so staff can fetch the installer before their
first sign-in. A normal browser visit shows a friendly download PAGE with
install instructions (incl. how to get past the Windows "isn't commonly
downloaded" / "Windows protected your PC" warning that appears because the app
is not yet code-signed); `?download=1` streams the actual .exe. The binary
lives in MEDIA_ROOT/downloads/ (persisted volume), pushed there out-of-band —
it is not in the repo.
"""
import os

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse

INSTALLER = os.path.join(settings.MEDIA_ROOT, 'downloads', 'OmniDesktop.exe')

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Download Omni Desktop</title></head>
<body style="margin:0;font-family:Montserrat,'Segoe UI',Arial,sans-serif;color:#1D3270;background:#f4f6fb">
<div style="max-width:640px;margin:0 auto;padding:32px 22px">
  <div style="border-left:6px solid #F47C20;padding:4px 0 4px 16px;margin-bottom:22px">
    <div style="font-size:24px;font-weight:700">Omni Desktop</div>
    <div style="font-size:13px;color:#5a6478">The Alpha Direct ERP, as a Windows app</div>
  </div>
  <p style="font-size:15px;line-height:1.6">Click the button to download the app, then open the downloaded file to install it.</p>
  <p style="text-align:center;margin:26px 0">
    <a href="?download=1" style="display:inline-block;background:#1D3270;color:#fff;text-decoration:none;font-size:16px;font-weight:600;padding:14px 34px;border-radius:10px">Download Omni Desktop</a>
  </p>
  <div style="background:#FFF6E5;border-left:4px solid #F47C20;border-radius:8px;padding:14px 18px;font-size:14px;line-height:1.6">
    <b>Windows will show a warning</b> &mdash; something like <i>"OmniDesktop.exe isn't commonly downloaded"</i> or <i>"Windows protected your PC"</i>. This is normal: it happens because this is our own in-house app. <b>It is safe.</b> To continue:
    <ol style="margin:10px 0 0;padding-left:20px">
      <li>In the download bar, click the <b>&hellip;</b> (or the warning) and choose <b>Keep</b>.</li>
      <li>Open the file. If you see <b>"Windows protected your PC"</b>, click <b>More info</b>, then <b>Run anyway</b>.</li>
    </ol>
  </div>
  <p style="font-size:12px;color:#8a93a6;margin-top:22px">For Alpha Direct staff. If the download doesn't start, or you're unsure, contact IT (Sechele).</p>
</div></body></html>"""


def desktop_app(request):
    if not os.path.exists(INSTALLER):
        raise Http404('Omni Desktop installer is not available yet.')
    if request.GET.get('download'):
        return FileResponse(
            open(INSTALLER, 'rb'),
            as_attachment=True,
            filename='OmniDesktop.exe',
            content_type='application/vnd.microsoft.portable-executable',
        )
    return HttpResponse(_PAGE)
