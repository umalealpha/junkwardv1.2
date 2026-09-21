"""
core/manual_views.py

GET /api/v1/manual/whats-new/ — the live feature log behind the /help page's
"What's New" section. Any signed-in omni user can read it (same access as the
manual itself). Populated by the nightly `update_user_manual` command.
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from core.manual_models import ManualFeatureEntry, ManualUpdateRun

# Cap the payload — the manual shows the recent feature history, not all time.
MAX_ENTRIES = 300


class WhatsNewView(APIView):
    """Recent, published 'What's New' entries, newest first."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = (ManualFeatureEntry.objects
              .filter(published=True)
              .order_by('-commit_date', '-created_at')[:MAX_ENTRIES])
        entries = [{
            'date':    e.commit_date.isoformat(),
            'title':   e.title,
            'summary': e.summary,
            'area':    e.area or 'General',
        } for e in qs]

        last_run = ManualUpdateRun.objects.order_by('-run_date').first()
        return Response({
            'last_updated': last_run.run_date.isoformat() if last_run else None,
            'count': len(entries),
            'entries': entries,
        })
