"""
hris/oneclick_page.py — the branded, sign-in-free page shell shared by the
one-click email actions (CFO 2026-08-07).

hris.leave_actions proved the pattern in July: a signed, time-limited token IS
the credential, the GET page has no side effect (so Outlook Safe-Links cannot
"click" it), and the POST re-checks the same guardrails as the in-app screen.
Two more actions now ride on it — the CEO/CFO countersignature and the
manager's monthly feedback — so the markup lives here instead of a third copy.

leave_actions keeps its own copy untouched; this is for the new pages only.
"""
from __future__ import annotations

from django.http import HttpResponse
from django.utils.html import escape

NAVY = '#0D1B2A'
ORANGE = '#F4A623'


def page(header: str, title: str, inner: str, *, status: int = 200) -> HttpResponse:
    """`header` is the navy bar caption; `title` the browser tab title."""
    html = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} — Alpha Direct</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family:'Segoe UI',Arial,sans-serif; background:#F3F4F6; color:#1F2937; }}
  .wrap {{ max-width:560px; margin:0 auto; padding:24px 16px; }}
  .card {{ background:#fff; border-radius:14px; overflow:hidden; box-shadow:0 8px 30px rgba(13,27,42,.10); }}
  .head {{ background:{NAVY}; padding:22px 26px; }}
  .head h1 {{ margin:0; color:{ORANGE}; font-size:19px; font-weight:700; }}
  .body {{ padding:24px 26px; }}
  .body h2 {{ margin:0 0 4px; font-size:20px; color:{NAVY}; }}
  .muted {{ color:#6B7280; font-size:13px; }}
  table.kv {{ width:100%; border-collapse:collapse; margin:16px 0; font-size:14px; }}
  table.kv td {{ padding:8px 0; border-bottom:1px solid #EEF0F3; vertical-align:top; }}
  table.kv td:first-child {{ color:#6B7280; width:42%; }}
  table.kv td:last-child {{ font-weight:600; text-align:right; }}
  .warn {{ background:#FFFBEB; border:1px solid #FDE68A; border-radius:10px;
           padding:12px 14px; margin:14px 0; font-size:13px; color:#92400E; }}
  .warn ul {{ margin:8px 0 0; padding-left:18px; }}
  .btn {{ display:block; width:100%; text-align:center; padding:15px; border:none;
          border-radius:10px; font-size:16px; font-weight:700; cursor:pointer; margin-top:12px; }}
  .btn-ok {{ background:#059669; color:#fff; }}
  .btn-no {{ background:#fff; color:#DC2626; border:1.5px solid #DC2626; }}
  label.fld {{ display:block; margin-top:14px; font-size:13px; color:#374151; font-weight:600; }}
  textarea, select {{ width:100%; padding:10px; border:1px solid #D1D5DB; border-radius:8px;
             font-family:inherit; font-size:14px; margin-top:6px; background:#fff; }}
  .pill {{ display:inline-block; padding:3px 12px; border-radius:999px; font-size:13px; font-weight:700; }}
  .pill-ok {{ background:#ECFDF5; color:#059669; }}
  .pill-no {{ background:#FEF2F2; color:#DC2626; }}
  .pill-wait {{ background:#FFFBEB; color:#92400E; }}
  .foot {{ text-align:center; color:#9CA3AF; font-size:11px; padding:16px; }}
  /* Tappable team list — the WHOLE row is the link, so a thumb cannot miss it
     (CFO 2026-08-07: "I should see the staff list, I should be able to click
     here and give feedback"). 56px+ tall meets the touch-target guidance. */
  .back {{ display:inline-block; color:#6B7280; font-size:13px; text-decoration:none;
           margin-bottom:10px; }}
  .back:hover {{ color:{NAVY}; }}
  .bar {{ height:6px; background:#EEF0F3; border-radius:99px; overflow:hidden; margin:12px 0 4px; }}
  .barfill {{ height:100%; background:#059669; border-radius:99px; }}
  .people {{ margin-top:14px; }}
  .person {{ display:block; padding:14px 16px; margin-bottom:10px; border-radius:12px;
             border:1px solid #E5E7EB; background:#fff; text-decoration:none;
             transition:border-color .12s, box-shadow .12s; }}
  .person:hover, .person:focus {{ border-color:{ORANGE}; box-shadow:0 2px 10px rgba(13,27,42,.07); }}
  .person:active {{ background:#FFFBEB; }}
  .pname {{ display:block; font-size:16px; font-weight:700; color:{NAVY}; }}
  .prole {{ display:block; font-size:12.5px; color:#6B7280; margin-top:2px; }}
  .pgo {{ display:block; font-size:13px; font-weight:700; color:{ORANGE}; margin-top:8px; }}
  .chip {{ display:inline-block; margin-left:8px; padding:2px 8px; border-radius:99px;
           background:#FEF2F2; color:#B42318; font-size:11px; font-weight:700;
           vertical-align:middle; }}
</style></head>
<body><div class="wrap"><div class="card">
  <div class="head"><h1>Alpha Direct · {escape(header)}</h1></div>
  <div class="body">{inner}</div>
</div><div class="foot">Omni ERP — omni.alphadirect.co.bw</div></div></body></html>"""
    return HttpResponse(html, status=status)
