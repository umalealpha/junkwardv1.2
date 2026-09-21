"""
core/bug_report_views.py — "Report a System Bug" channel.

CFO directive 2026-06-10: give staff a single in-omni place to report bugs so
they stop emailing the CFO directly. The /report-bug page enforces:
  * description >= MIN_WORDS words
  * >= MIN_SHOTS screenshots (images)
On submit: store a BugReport row (audit trail) + email excoboard@ with the
screenshots attached, via the house HTML mailer.

  POST /api/v1/bug-reports/   multipart:
      description : str   (>= 50 words)
      page_url    : str   (optional — where the bug was seen)
      screenshots : file  (repeated; >= 3 images)

Auth: inherits DEFAULT_AUTHENTICATION_CLASSES (AzureJWT SSO + ApiKey + Session
+ Token) — same fix as the healthcare upload. Any logged-in user may report.
"""
from __future__ import annotations

import html as _html

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import BugReport

MIN_WORDS         = 50
MIN_SHOTS         = 2                     # CFO 2026-06-10: lowered 3 -> 2 (matches FE)
MAX_SHOTS         = 10                   # images + documents, combined
# CFO 2026-07-31: feature requests come through the same channel, tagged by the
# frontend. Nothing is broken yet, so there is nothing to photograph — demanding
# two screenshots for an idea just pushed people back to emailing the CFO.
_FEATURE_TAG      = '[FEATURE REQUEST]'
FEATURE_MIN_WORDS = 25
FEATURE_MIN_SHOTS = 0
MAX_SHOT_BYTES    = 10 * 1024 * 1024     # 10 MB per image
_ALLOWED_MIME     = {'image/png', 'image/jpeg', 'image/jpg', 'image/webp', 'image/gif'}
# CFO 2026-09-08, asked for by Oratile Tlhomelang on the feature page: a request
# is often already written up in Excel/Word/PDF (a build brief, a policy extract,
# a framework). Demanding a screenshot of a document made people email it to the
# CFO separately, which is what this channel exists to stop. Documents ride the
# same email as the images; they do NOT satisfy the "two screenshots" rule for a
# bug — only real images count toward min_shots.
_ALLOWED_DOC_MIME = {
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',   # .docx
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',         # .xlsx
    'application/msword',                                                        # .doc
    'application/vnd.ms-excel',                                                  # .xls
    'text/csv',
}

_BUG_INBOX        = 'excoboard@alphadirect.co.bw'
# Who gets the "new bug reported" alert. CFO directive 2026-06-12: also send
# to Prathap's personal mailbox, not just the EXCO board inbox.
_BUG_NOTIFY       = [_BUG_INBOX, 'pganesharajah@alphadirect.co.bw']

_STATUS_LABELS = dict(BugReport.Status.choices)


def _word_count(text: str) -> int:
    return len([w for w in (text or '').split() if w.strip()])


def _is_triager(user) -> bool:
    """Who may see ALL reports and change status: superuser / staff / admin /
    CFO. Everyone else sees only their own reports (read-only)."""
    if not (user and user.is_authenticated):
        return False
    if user.is_superuser or user.is_staff:
        return True
    try:
        from core.models import get_user_profile, UserProfile
        p = get_user_profile(user)
        if p and (p.is_administrator or p.title == UserProfile.Title.CFO):
            return True
    except Exception:    # noqa: BLE001
        pass
    return False


def _is_cfo(user) -> bool:
    """Tighter than _is_triager: only the CFO / administrators / superusers may
    kick the triage runner on demand (the 'Run triage now' button). Ordinary
    staff triagers can change a report's status but cannot fire the whole run."""
    if not (user and user.is_authenticated):
        return False
    if user.is_superuser:
        return True
    try:
        from core.models import get_user_profile, UserProfile
        p = get_user_profile(user)
        if p and (p.is_administrator or p.title == UserProfile.Title.CFO):
            return True
    except Exception:    # noqa: BLE001
        pass
    return False


def _serialize(r: BugReport) -> dict:
    return {
        'id':               str(r.id),
        'status':           r.status,
        'status_label':     _STATUS_LABELS.get(r.status, r.status),
        'description':      r.description,
        'word_count':       r.word_count,
        'screenshot_count': r.screenshot_count,
        'page_url':         r.page_url,
        'reporter_email':   r.reporter_email,
        'resolution_note':  r.resolution_note,
        'resolved_at':      r.resolved_at.isoformat() if r.resolved_at else None,
        'triaged_by':       (r.triaged_by.get_full_name() or r.triaged_by.username) if r.triaged_by_id else None,
        'triage_requested':    r.triage_requested,
        'triage_requested_at': r.triage_requested_at.isoformat() if r.triage_requested_at else None,
        'triage_pr_url':       r.triage_pr_url,
        # Manus QC channel (CFO 2026-08-29)
        'qc_requested':     r.qc_requested,
        'qc_requested_at':  r.qc_requested_at.isoformat() if r.qc_requested_at else None,
        'qc_requested_by':  (r.qc_requested_by.get_full_name() or r.qc_requested_by.username) if r.qc_requested_by_id else None,
        'qc_picked_up_at':  r.qc_picked_up_at.isoformat() if r.qc_picked_up_at else None,
        'qc_result_note':   r.qc_result_note,
        'qc_result_pr_url': r.qc_result_pr_url,
        'qc_result_at':     r.qc_result_at.isoformat() if r.qc_result_at else None,
        'created_at':       r.created_at.isoformat(),
        'updated_at':       r.updated_at.isoformat(),
    }


class BugReportView(APIView):
    """POST a bug report (stores + emails excoboard@). GET lists reports —
    role-aware: triagers see all (optional ?status=), everyone else sees only
    their own. This is the user/admin status board's data source."""
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser]

    def get(self, request):
        # CFO directive 2026-06-15: the bug list is visible to EVERYONE —
        # every authenticated staff member sees ALL reports, not just their own.
        # Triager status now gates only STATUS CHANGES (BugReportDetailView.patch)
        # and the off-box triage runner's ?triage_requested poll — NOT visibility.
        # (Previously non-triagers saw only their own reports, which made the
        # board look empty for most staff.)
        qs = BugReport.objects.all().select_related('reporter', 'triaged_by')
        am_triager = _is_triager(request.user)
        st = (request.query_params.get('status') or '').strip()
        if st:
            qs = qs.filter(status=st)
        # triage_requested is an operational poll for the off-box runner — keep
        # it triager-only so a normal status view isn't accidentally narrowed.
        tr = (request.query_params.get('triage_requested') or '').strip().lower()
        if am_triager and tr in ('true', '1', 'yes'):
            qs = qs.filter(triage_requested=True)
        # Manus's QC poll (CFO 2026-08-29): the flagged pickup queue. Just a
        # narrowing filter on the already-everyone-visible board, so it is not
        # gated to triagers — Manus reaches it with its scoped 'qc-manus' key.
        qc = (request.query_params.get('qc_requested') or '').strip().lower()
        if qc in ('true', '1', 'yes'):
            qs = qs.filter(qc_requested=True)
        qs = qs.order_by('-created_at')[:200]
        return Response({
            'is_triager': am_triager,
            'is_cfo':     _is_cfo(request.user),
            'statuses':   [{'value': v, 'label': l} for v, l in BugReport.Status.choices],
            'results':    [_serialize(r) for r in qs],
        })

    def post(self, request):
        # Manus's QC key may only poll the queue and post QC results — never
        # raise bug reports (CFO 2026-08-29). Defence in depth: the prefix-based
        # scope map cannot express "GET-only" on this path, so block create here.
        _auth = getattr(request, 'auth', None)
        if _auth is not None and 'qc-manus' in (getattr(_auth, 'allowed_scopes', None) or []):
            return Response(
                {'detail': 'This key may only read the QC queue and post QC results.'},
                status=status.HTTP_403_FORBIDDEN)
        description = (request.data.get('description') or '').strip()
        page_url    = (request.data.get('page_url') or '').strip()[:500]
        shots       = request.FILES.getlist('screenshots')

        # --- validation ------------------------------------------------------
        is_feature = description.upper().startswith(_FEATURE_TAG)
        min_words  = FEATURE_MIN_WORDS if is_feature else MIN_WORDS
        min_shots  = FEATURE_MIN_SHOTS if is_feature else MIN_SHOTS
        # The Omni QC agent (scoped 'qc-bot' key, CFO 2026-09-07) files one report
        # per broken page with its single page capture — a human's "2 screenshots"
        # rule would only make it attach the same image twice.
        if _auth is not None and 'qc-bot' in (getattr(_auth, 'allowed_scopes', None) or []):
            min_shots = min(min_shots, 1)

        images = [f for f in shots
                  if (getattr(f, 'content_type', '') or '').lower() in _ALLOWED_MIME]
        docs   = [f for f in shots
                  if (getattr(f, 'content_type', '') or '').lower() in _ALLOWED_DOC_MIME]

        errors = {}
        wc = _word_count(description)
        if wc < min_words:
            errors['description'] = (
                f'Please describe the issue in at least {min_words} words '
                f'(you wrote {wc}).'
            )
        if len(images) < min_shots:
            errors['screenshots'] = (
                f'Please attach at least {min_shots} screenshots '
                f'(you attached {len(images)}).'
            )
        elif len(shots) > MAX_SHOTS:
            errors['screenshots'] = f'Please attach at most {MAX_SHOTS} files.'
        for f in shots:
            mime = (getattr(f, 'content_type', '') or '').lower()
            if mime not in _ALLOWED_MIME and mime not in _ALLOWED_DOC_MIME:
                errors['screenshots'] = (
                    f'"{f.name}" is not an image or a document '
                    f'(png/jpg/webp/gif, pdf, Word, Excel or CSV only).'
                )
                break
            if f.size and f.size > MAX_SHOT_BYTES:
                errors['screenshots'] = (
                    f'"{f.name}" is larger than 10 MB. Please compress it.'
                )
                break
        if errors:
            return Response({'errors': errors}, status=status.HTTP_400_BAD_REQUEST)

        # --- read attachments (bytes ride the email; not persisted to DB) ----
        attachments = []
        for f in shots:
            attachments.append((f.name, f.read(), (f.content_type or 'image/png')))

        reporter       = request.user if request.user.is_authenticated else None
        reporter_email = (getattr(reporter, 'email', '') or '').strip()
        reporter_name  = (
            (reporter.get_full_name() if reporter else '') or
            (getattr(reporter, 'username', '') if reporter else '') or
            reporter_email or 'Unknown user'
        )

        report = BugReport.objects.create(
            reporter         = reporter,
            reporter_email   = reporter_email,
            description      = description,
            word_count       = wc,
            screenshot_count = len(images),
            page_url         = page_url,
            status           = BugReport.Status.NEW,
        )

        # --- email excoboard@ -------------------------------------------------
        emailed_ok = False
        try:
            from .notifications import send_html_with_cfo_cc
            html_body = self._build_html(
                report_id=str(report.id),
                reporter_name=reporter_name,
                reporter_email=reporter_email,
                page_url=page_url,
                word_count=wc,
                shot_count=len(images),
                doc_names=[f.name for f in docs],
                description=description,
            )
            sent = send_html_with_cfo_cc(
                subject=(
                    f'[Omni Feature Request] {reporter_name} — {wc} words'
                    if is_feature else
                    f'[Omni Bug Report] {reporter_name} — {wc} words, {len(images)} screenshots'
                ),
                html=html_body,
                to=_BUG_NOTIFY,
                reply_to=[reporter_email] if reporter_email else None,
                attachments=attachments,
            )
            emailed_ok = bool(sent)
        except Exception:    # noqa: BLE001 — never lose the stored report on a mail hiccup
            import logging
            logging.getLogger('bug-report').exception(
                'Bug report %s stored but email to %s failed', report.id, _BUG_INBOX)

        if emailed_ok and not report.emailed_ok:
            report.emailed_ok = True
            report.save(update_fields=['emailed_ok'])

        return Response(
            {
                'id': str(report.id),
                'emailed': emailed_ok,
                'message': (
                    ('Thanks — your feature request was sent to the Exco board inbox.'
                     if is_feature else
                     'Thanks — your bug report was sent to the Exco board inbox.')
                    if emailed_ok else
                    'Your report was saved. Email delivery is delayed; the team '
                    'will still see it.'
                ),
            },
            status=status.HTTP_201_CREATED,
        )

    @staticmethod
    def _build_html(*, report_id, reporter_name, reporter_email, page_url,
                    word_count, shot_count, description, doc_names=()) -> str:
        esc = _html.escape
        desc_html = esc(description).replace('\n', '<br>')
        page_row = (
            f'<tr><td style="padding:5px 12px;color:#6B7280;">Page</td>'
            f'<td style="padding:5px 12px;"><a href="{esc(page_url)}" '
            f'style="color:#F4A623;">{esc(page_url)}</a></td></tr>'
            if page_url else ''
        )
        # Documents (Excel / Word / PDF) ride the same email as the images — name
        # them so the reader knows to look past the screenshots.
        doc_row = (
            f'<tr><td style="padding:5px 12px;color:#6B7280;">Documents</td>'
            f'<td style="padding:5px 12px;">'
            f'{esc(", ".join(doc_names))}</td></tr>'
            if doc_names else ''
        )
        return f"""\
<div style="font-family:'Book Antiqua',Georgia,serif;color:#111827;max-width:680px;">
  <div style="background:#0D1B2A;padding:16px 20px;border-radius:8px 8px 0 0;">
    <span style="color:#F4A623;font-size:18px;font-weight:700;">Omni — System Bug Report</span>
  </div>
  <div style="border:1px solid #e5e7eb;border-top:0;padding:18px 20px;border-radius:0 0 8px 8px;">
    <p style="margin-top:0;">A bug was reported through the in-app
      <b>Report a System Bug</b> page. {shot_count} screenshot(s) attached.</p>
    <table style="border-collapse:collapse;font-size:13px;margin:10px 0;">
      <tr><td style="padding:5px 12px;color:#6B7280;">Reported by</td>
          <td style="padding:5px 12px;">{esc(reporter_name)} &lt;{esc(reporter_email) or 'no email'}&gt;</td></tr>
      <tr><td style="padding:5px 12px;color:#6B7280;">Report ID</td>
          <td style="padding:5px 12px;font-family:monospace;">{esc(report_id)}</td></tr>
      <tr><td style="padding:5px 12px;color:#6B7280;">Length</td>
          <td style="padding:5px 12px;">{word_count} words &middot; {shot_count} screenshots</td></tr>
      {page_row}
      {doc_row}
    </table>
    <p style="margin:14px 0 4px;"><b>Description</b></p>
    <div style="background:#FAF7F2;border:1px solid #e5e7eb;border-radius:6px;
                padding:12px;font-size:14px;line-height:1.5;">{desc_html}</div>
    <p style="margin:16px 0 0;color:#6B7280;font-size:12px;">
      Reply to this email to reach the reporter directly. Screenshots are
      attached to this message only (not stored in omni).</p>
  </div>
</div>"""


# --- Status colours for the feedback email ---------------------------------
_STATUS_COLOR = {
    'new':         '#6B7280',
    'triaged':     '#1D4ED8',
    'in_progress': '#CC6C00',
    'resolved':    '#059669',
    'wont_fix':    '#DC2626',
}


class BugReportDetailView(APIView):
    """GET one report; PATCH its status + resolution_note (triagers only).
    On a status change the reporter gets a feedback email."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            r = BugReport.objects.select_related('reporter', 'triaged_by').get(pk=pk)
        except BugReport.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        # Viewing is open to all authenticated staff (CFO 2026-06-15) — same
        # policy as the list. Editing the status stays triager-only (see patch).
        return Response(_serialize(r))

    def patch(self, request, pk):
        if not _is_triager(request.user):
            return Response({'detail': 'Only admins can change a bug report status.'},
                            status=status.HTTP_403_FORBIDDEN)
        try:
            r = BugReport.objects.select_related('reporter').get(pk=pk)
        except BugReport.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        new_status = (request.data.get('status') or '').strip()
        note       = request.data.get('resolution_note')
        valid      = {v for v, _ in BugReport.Status.choices}
        if new_status and new_status not in valid:
            return Response({'errors': {'status': f'Invalid status (choose one of {sorted(valid)}).'}},
                            status=status.HTTP_400_BAD_REQUEST)

        from django.utils import timezone
        old_status = r.status
        changed = False
        fields = set()
        if new_status and new_status != old_status:
            r.status = new_status
            r.triaged_by = request.user
            fields.update({'status', 'triaged_by'})
            if new_status in (BugReport.Status.RESOLVED, BugReport.Status.WONT_FIX):
                r.resolved_at = timezone.now()
                fields.add('resolved_at')
            changed = True
        if note is not None and note != r.resolution_note:
            r.resolution_note = note
            fields.add('resolution_note')
            changed = True
        # AI-triage queue flag — "Request AI fix" button sets True; the runner
        # clears it (False) after opening the draft PR.
        if 'triage_requested' in request.data:
            want = str(request.data.get('triage_requested')).lower() in ('true', '1', 'yes')
            if want != r.triage_requested:
                r.triage_requested = want
                fields.add('triage_requested')
                if want:
                    r.triage_requested_at = timezone.now()
                    r.triage_requested_by = request.user
                    fields.update({'triage_requested_at', 'triage_requested_by'})
                changed = True
        pr_url = request.data.get('triage_pr_url')
        if pr_url is not None and pr_url != r.triage_pr_url:
            r.triage_pr_url = str(pr_url)[:500]
            fields.add('triage_pr_url')
            changed = True
        # "Send to Manus for QC" button (CFO 2026-08-29) — a separate pickup
        # queue from the off-box triage runner. Manus clears it via qc-result
        # once it has a finding.
        if 'qc_requested' in request.data:
            want = str(request.data.get('qc_requested')).lower() in ('true', '1', 'yes')
            if want != r.qc_requested:
                r.qc_requested = want
                fields.add('qc_requested')
                if want:
                    r.qc_requested_at = timezone.now()
                    r.qc_requested_by = request.user
                    fields.update({'qc_requested_at', 'qc_requested_by'})
                changed = True
        if changed:
            fields.add('updated_at')
            r.save(update_fields=list(fields))

        # Feedback email to the reporter on a status change.
        emailed = False
        if changed and new_status and new_status != old_status and r.reporter_email:
            try:
                from core.notifications import send_html_with_cfo_cc
                send_html_with_cfo_cc(
                    subject=f'[Omni] Your bug report is now: {_STATUS_LABELS.get(new_status, new_status)}',
                    html=self._feedback_html(r, new_status),
                    to=[r.reporter_email],
                )
                emailed = True
            except Exception:    # noqa: BLE001
                import logging
                logging.getLogger('bug-report').exception(
                    'status updated for %s but feedback email failed', r.id)

        out = _serialize(r)
        out['feedback_emailed'] = emailed
        return Response(out)

    @staticmethod
    def _feedback_html(r: BugReport, new_status: str) -> str:
        color   = _STATUS_COLOR.get(new_status, '#6B7280')
        label   = _STATUS_LABELS.get(new_status, new_status)
        note    = _html.escape(r.resolution_note or '').replace('\n', '<br>')
        desc    = _html.escape((r.description or '')[:240])
        note_block = (
            f'<p style="margin:12px 0 4px;"><b>Note from the team</b></p>'
            f'<div style="background:#FAF7F2;border:1px solid #e5e7eb;border-radius:6px;'
            f'padding:10px;font-size:13px;">{note}</div>'
            if note else ''
        )
        return f"""\
<div style="font-family:'Book Antiqua',Georgia,serif;color:#111827;max-width:620px;">
  <div style="background:#0D1B2A;padding:16px 20px;border-radius:8px 8px 0 0;">
    <span style="color:#F4A623;font-size:18px;font-weight:700;">Omni — Bug Report Update</span>
  </div>
  <div style="border:1px solid #e5e7eb;border-top:0;padding:18px 20px;border-radius:0 0 8px 8px;">
    <p style="margin-top:0;">Hi &mdash; the bug you reported has a new status:</p>
    <p style="margin:8px 0;">
      <span style="display:inline-block;background:{color};color:#fff;font-weight:700;
                   padding:5px 14px;border-radius:999px;font-size:13px;">{label}</span>
    </p>
    {note_block}
    <p style="margin:14px 0 4px;color:#6B7280;font-size:12px;">Your report (excerpt):</p>
    <div style="font-size:12px;color:#374151;font-style:italic;">&ldquo;{desc}&hellip;&rdquo;</div>
    <p style="margin:14px 0 0;font-size:12px;color:#6B7280;">
      Track all your reports in omni &rarr; <b>Bug Reports</b>. Ref: {r.id}</p>
  </div>
</div>"""


class BugTriageRunView(APIView):
    """POST — CFO-only. Fires the safe `triage_bugs` runner on demand instead of
    waiting for the hourly cron. It classifies NEW reports, sets their status and
    emails the reporter. It NEVER writes or deploys code — code fixes stay with
    the off-box AI runner (per-report 'Request AI fix' → draft PR you merge)."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not _is_cfo(request.user):
            return Response(
                {'detail': 'Only the CFO / an administrator can run triage.'},
                status=status.HTTP_403_FORBIDDEN)

        from io import StringIO
        from django.core.management import call_command
        from django.db.models import Count

        def _counts():
            rows = (BugReport.objects.values('status')
                    .annotate(n=Count('id')).order_by())
            return {row['status']: row['n'] for row in rows}

        # Cap one button press at 10 reports so the synchronous run stays well
        # inside gunicorn's 120s worker timeout (~5s per report measured).
        # Anything beyond the cap is reported back as `remaining` — press again
        # or let the hourly cron sweep it.
        MAX_PER_RUN = 10

        before = _counts()
        new_before = BugReport.objects.filter(status=BugReport.Status.NEW).count()

        buf = StringIO()
        try:
            call_command('triage_bugs', limit=MAX_PER_RUN, stdout=buf, stderr=buf)
        except Exception as exc:                       # noqa: BLE001
            return Response(
                {'ok': False, 'detail': f'Triage run failed: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        output = buf.getvalue().strip()[:4000]
        if 'already in progress' in output:
            # The advisory lock in triage_bugs is held by another run (hourly
            # cron or a second click) — nothing was processed here.
            return Response(
                {'ok': False,
                 'detail': 'A triage run is already in progress — the board '
                           'will update in a moment.'},
                status=status.HTTP_409_CONFLICT)

        after = _counts()
        return Response({
            'ok':        True,
            'processed': min(new_before, MAX_PER_RUN),
            'remaining': after.get(BugReport.Status.NEW, 0),
            'output':    output,
            'counts':    after,
            'moved':     {k: after.get(k, 0) - before.get(k, 0)
                          for k in set(before) | set(after)
                          if after.get(k, 0) != before.get(k, 0)},
        })


class BugReportQCResultView(APIView):
    """POST /bug-reports/<pk>/qc-result/ — Manus posts its QC finding back onto
    the item (CFO 2026-08-29). This is the ONLY write Manus's scoped 'qc-manus'
    key can make, and it touches only the qc_* result fields — never status,
    money, or anything else.

    Body (all optional): {picked_up, note, pr_url, done}
      picked_up → stamp qc_picked_up_at (Manus has started on it)
      note      → the plain finding text (+ stamps qc_result_at)
      pr_url    → the draft-PR link Manus opened (+ stamps qc_result_at)
      done      → clear qc_requested so the item leaves the pickup queue
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        # Only Manus's scoped key, or an admin (for testing), may post a result.
        auth = getattr(request, 'auth', None)
        scopes = set(getattr(auth, 'allowed_scopes', None) or []) if auth is not None else set()
        if 'qc-manus' not in scopes and not _is_triager(request.user):
            return Response(
                {'detail': 'Only the Manus QC key (or an admin) may post a QC result.'},
                status=status.HTTP_403_FORBIDDEN)
        try:
            r = BugReport.objects.get(pk=pk)
        except BugReport.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        from django.utils import timezone
        data = request.data or {}

        def _truthy(v):
            return str(v).lower() in ('true', '1', 'yes', 'on')

        fields = set()
        if _truthy(data.get('picked_up')):
            r.qc_picked_up_at = timezone.now()
            fields.add('qc_picked_up_at')
        note = data.get('note')
        if note is not None:
            r.qc_result_note = str(note)[:20000]
            r.qc_result_at = timezone.now()
            fields.update({'qc_result_note', 'qc_result_at'})
        pr_url = data.get('pr_url')
        if pr_url is not None:
            r.qc_result_pr_url = str(pr_url)[:500]
            r.qc_result_at = timezone.now()
            fields.update({'qc_result_pr_url', 'qc_result_at'})
        if _truthy(data.get('done')):
            r.qc_requested = False
            fields.add('qc_requested')

        if not fields:
            return Response(
                {'detail': 'Nothing to update — send picked_up, note, pr_url or done.'},
                status=status.HTTP_400_BAD_REQUEST)
        fields.add('updated_at')
        r.save(update_fields=list(fields))
        return Response(_serialize(r))
