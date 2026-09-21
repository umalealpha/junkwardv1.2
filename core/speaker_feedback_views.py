"""core/speaker_feedback_views.py — Confidential pre-session audience feedback.

CFO directive 2026-08-03. Prathap opens the YPO Pan Africa 2026-2027 learning
year on 25-Aug-2026 with "The AI Advantage: Building Your AI Roadmap". He wants
the members' real pain points BEFORE he writes the talk, so:

  * the organiser circulates ONE public link — /speak/<slug> — no login,
  * each member rates ten AI pain statements 1-5 and answers three open
    questions, optionally naming themselves,
  * every response is readable by the CFO ALONE inside omni.

Endpoints
  GET  /api/v1/speaker-feedback/form/<slug>/         AllowAny  — the questions
  POST /api/v1/speaker-feedback/form/<slug>/submit/  AllowAny  — one response
  GET  /api/v1/speaker-feedback/access/              auth      — sidebar probe
  GET  /api/v1/speaker-feedback/responses/<slug>/    CFO ONLY  — read them

CONFIDENTIALITY (the load-bearing part)
  `user_can_read_feedback` is a POSITIVE match on the reader's own email
  local-part against SPEAKER_FEEDBACK_READERS. There is deliberately NO
  superuser and NO `is_administrator` bypass — unlike core.hris_access, where
  admins are meant to see the data. Here "confidential, for my eyes" means the
  people who can grant access still cannot read the answers. Unknown or
  missing identity is REFUSED, never allowed through on a default (the trap
  that burned the entity-code check on 24-Jul and the claims-are-ADIC check on
  29-Jul: an unrecognised value resolved to a real one and so SATISFIED the
  very control meant to stop it).

  Env override `OMNI_SPEAKER_FEEDBACK_READERS` (comma-separated local-parts)
  lets the CFO add a reader without a deploy. An EMPTY value is treated as
  unset and falls back to the default, so a blank env var can never open the
  module to everyone.
"""
from __future__ import annotations

import logging
import os

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.parsers import FormParser, JSONParser
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from .models import SpeakerFeedback

log = logging.getLogger(__name__)

# Only these people may read the answers. Local-parts of @alphadirect.co.bw.
DEFAULT_SPEAKER_FEEDBACK_READERS = ('pganesharajah',)   # the CFO, alone

MAX_TEXT = 4000          # generous for a free-text answer, bounded for abuse
PAIN_KEYS = tuple(f'p{i}' for i in range(1, 11))


# ---------------------------------------------------------------------------
# The surveys. Content lives here (not in the DB) so the wording is reviewable
# in a diff and cannot be edited by anyone who reaches the database.
# ---------------------------------------------------------------------------
YPO_PAINS = (
    ('p1',  'We have no clear starting point or roadmap for using AI in our business.'),
    ('p2',  'We cannot tell which AI benefits are real and which are vendor hype.'),
    ('p3',  'We do not know what AI will cost us, or what return we would get back.'),
    ('p4',  'Our data is too messy, scattered and incomplete for AI to work with.'),
    ('p5',  'We are not confident that client information stays private, or who is '
            'accountable when AI gets something wrong.'),
    ('p6',  'We have not prepared our people for AI — no rules, no training, and '
            'some are uneasy.'),
    ('p7',  'We have no AI skills in-house and cannot find or afford people who do.'),
    ('p8',  'We run pilots but they rarely become part of our everyday operations.'),
    ('p9',  'We do not know which jobs and processes in our own industry change first.'),
    ('p10', 'We fear a competitor or new entrant is already ahead and we will not '
            'see it coming.'),
)

SURVEYS = {
    'ypo-ai-roadmap-2026': {
        'title': 'The AI Advantage: Building Your AI Roadmap',
        'event': 'YPO Pan Africa — 2026/2027 Learning Year opening session',
        'session_date': '25 August 2026',
        'speaker': 'Prathap Ganesharajah — Chief Financial Officer, Alpha Direct Insurance',
        'intro': ('Tell me the ONE business problem you want AI to solve. '
                  'I will build the talk around your answers. Takes 30 seconds.'),
        'confidentiality': ('Your answer goes to the speaker only. Not shared with YPO, '
                            'with other members, or with anyone else. Every field is '
                            'optional — you can answer completely anonymously.'),
        'scale_low': '',
        'scale_high': '',
        'pains': (),        # simplified 2026-08-15: no rating grid, one problem box
        'open_questions': (
            {'key': 'biggest_question', 'required': True,
             'label': "What's the problem you want AI to solve in your business?"},
        ),
        'require_company': False,      # anonymous by default — company is optional
        'closes_on': '2026-08-22',
    },
}

HEADCOUNT_BANDS = ('1-20', '21-100', '101-500', '501-2000', '2000+')


# ---------------------------------------------------------------------------
# Confidentiality gate
# ---------------------------------------------------------------------------
def _readers() -> set[str]:
    """Allowed reader local-parts, lowercased.

    An empty / whitespace-only env value falls back to the default so a blank
    override cannot silently widen access.
    """
    env = (os.environ.get('OMNI_SPEAKER_FEEDBACK_READERS') or '').strip()
    if not env:
        return {p.lower() for p in DEFAULT_SPEAKER_FEEDBACK_READERS}
    parts = {p.strip().lower() for p in env.split(',') if p.strip()}
    return parts or {p.lower() for p in DEFAULT_SPEAKER_FEEDBACK_READERS}


# Django emails are NOT unique, so the local-part alone is not an identity: an
# account created with `pganesharajah@anything.com` would match a bare
# local-part check. Only accept the local-part when the address is on one of our
# own domains; usernames are unique so that leg needs no domain.
READER_EMAIL_DOMAINS = ('alphadirect.co.bw',)


def _local_part(email: str | None) -> str:
    """Local-part of a company email, or '' if the address is not on one of our
    domains (an address with no '@' at all is likewise not a company email)."""
    if not email or '@' not in email:
        return ''
    local, _, domain = email.strip().lower().rpartition('@')
    if domain not in READER_EMAIL_DOMAINS:
        return ''
    return local


def user_can_read_feedback(user) -> bool:
    """True only for an explicitly listed reader. No superuser/admin bypass.

    Matches on the username, or on the local-part of a COMPANY email address
    (see READER_EMAIL_DOMAINS), both by EXACT equality — never `startswith`, so
    a lookalike account such as `pganesharajah2` or `pganesharajah-test` is
    refused rather than admitted, and neither is `pganesharajah@gmail.com`.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if not getattr(user, 'is_active', False):
        return False
    allowed = _readers()
    candidates = {
        _local_part(getattr(user, 'email', '') or ''),
        (getattr(user, 'username', '') or '').strip().lower(),
    }
    candidates.discard('')
    return bool(candidates & allowed)


class IsSpeakerFeedbackReader(BasePermission):
    message = 'These responses are confidential to the speaker.'

    def has_permission(self, request, view):
        return user_can_read_feedback(request.user)


class SpeakerFeedbackThrottle(AnonRateThrottle):
    """Rate-limit the public endpoints — they take no login at all. Carries its
    own rate because the `speaker_feedback` scope is absent from
    settings.DEFAULT_THROTTLE_RATES, and reading a missing scope raises
    ImproperlyConfigured — same pattern as
    healthcare.vendor_esign_views.VendorSignThrottle."""
    scope = 'speaker_feedback'
    THROTTLE_RATES = {'speaker_feedback': '20/min'}


def _client_ip(request) -> str | None:
    fwd = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    return fwd or request.META.get('REMOTE_ADDR') or None


def _survey_or_none(slug: str):
    """Positive lookup against SURVEYS — an unknown slug returns None, it never
    falls back to 'the first survey' (see the module docstring)."""
    return SURVEYS.get((slug or '').strip().lower())


# ---------------------------------------------------------------------------
# Public — the form itself (no login)
# ---------------------------------------------------------------------------
class SpeakerFeedbackFormView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []          # public: never try to auth the caller
    throttle_classes = [SpeakerFeedbackThrottle]

    def get(self, request, slug):
        s = _survey_or_none(slug)
        if not s:
            return Response({'detail': 'This feedback link is not valid.'},
                            status=status.HTTP_404_NOT_FOUND)
        closed = False
        closes_on = s.get('closes_on') or ''
        if closes_on:
            closed = timezone.localdate().isoformat() > closes_on
        return Response({
            'success': True,
            'slug': slug,
            'title': s['title'],
            'event': s['event'],
            'session_date': s['session_date'],
            'speaker': s['speaker'],
            'intro': s['intro'],
            'confidentiality': s['confidentiality'],
            'scale_low': s['scale_low'],
            'scale_high': s['scale_high'],
            'pains': [{'key': k, 'statement': t} for k, t in s['pains']],
            'open_questions': list(s['open_questions']),
            'headcount_bands': list(HEADCOUNT_BANDS),
            'require_company': bool(s.get('require_company')),
            'closes_on': s.get('closes_on') or '',
            'closed': closed,
        })


class SpeakerFeedbackSubmitView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [SpeakerFeedbackThrottle]
    parser_classes = [JSONParser, FormParser]

    def post(self, request, slug):
        s = _survey_or_none(slug)
        if not s:
            return Response({'detail': 'This feedback link is not valid.'},
                            status=status.HTTP_404_NOT_FOUND)

        p = request.data if isinstance(request.data, dict) else {}

        # Ratings: keep only the survey's own keys, only 1-5, skip anything else.
        raw = p.get('pain_ratings') or {}
        if not isinstance(raw, dict):
            raw = {}
        ratings: dict[str, int] = {}
        for key, _stmt in s['pains']:
            v = raw.get(key)
            if v in (None, '', 'null'):
                continue
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= 5:
                ratings[key] = n

        def text(field: str) -> str:
            return str(p.get(field) or '').strip()[:MAX_TEXT]

        biggest = text('biggest_question')
        if not biggest:
            open_qs = s.get('open_questions') or ()
            label = (open_qs[0].get('label') if open_qs else
                     'What is the ONE thing about AI you most want answered in this session?')
            return Response(
                {'detail': f'Please answer: {label}',
                 'fields': ['biggest_question']},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        company_val = text('company_name')[:200]
        if s.get('require_company') and not company_val:
            return Response(
                {'detail': 'Please enter your company name.',
                 'fields': ['company_name']},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        # A survey with no pain grid (simplified form) skips the ratings requirement;
        # a survey WITH a grid still needs at least one score, so it can be ranked.
        if s['pains'] and not ratings:
            return Response(
                {'detail': 'Please rate at least one of the ten statements.',
                 'fields': ['pain_ratings']},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        band = text('headcount_band')
        if band and band not in HEADCOUNT_BANDS:
            band = ''

        rec = SpeakerFeedback.objects.create(
            survey_slug=slug.strip().lower(),
            pain_ratings=ratings,
            biggest_question=biggest,
            wish_ai_did=text('wish_ai_did'),
            tried_already=text('tried_already'),
            respondent_name=text('respondent_name')[:200],
            respondent_email=text('respondent_email')[:254],
            respondent_role=text('respondent_role')[:120],
            company_name=company_val,
            industry=text('industry')[:120],
            country=text('country')[:120],
            headcount_band=band,
            may_quote=bool(p.get('may_quote')),
            submitter_ip=_client_ip(request),
        )
        # Log the fact, never the content — the answers are confidential and
        # application logs are read by more people than the module is.
        log.info('[speaker-feedback] response %s stored for survey %s (%d ratings)',
                 rec.id, rec.survey_slug, len(ratings))
        return Response({'success': True}, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# CFO-only — read the responses
# ---------------------------------------------------------------------------
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def speaker_feedback_access(request):
    """Sidebar probe — is this user allowed to see the module at all?"""
    return Response({'allowed': user_can_read_feedback(request.user)})


class SpeakerFeedbackResponsesView(APIView):
    permission_classes = [IsAuthenticated, IsSpeakerFeedbackReader]

    def get(self, request, slug):
        s = _survey_or_none(slug)
        if not s:
            return Response({'detail': 'Unknown survey.'},
                            status=status.HTTP_404_NOT_FOUND)

        qs = SpeakerFeedback.objects.filter(survey_slug=slug.strip().lower())
        rows = list(qs)

        # Average + "how many called this a 4 or 5" per statement, so he can see
        # at a glance which pains to build the talk around. Averages are over
        # the people who ANSWERED that statement — skips are excluded, not
        # counted as a 3.
        summary = []
        for key, statement in s['pains']:
            scores = [r.pain_ratings.get(key) for r in rows]
            scores = [x for x in scores if isinstance(x, int) and 1 <= x <= 5]
            n = len(scores)
            summary.append({
                'key': key,
                'statement': statement,
                'answered': n,
                'average': round(sum(scores) / n, 2) if n else None,
                'top_box': sum(1 for x in scores if x >= 4),
                'top_box_pct': round(100 * sum(1 for x in scores if x >= 4) / n) if n else None,
            })
        # Rank by HOW MANY PEOPLE called it a serious problem first, average
        # second. Sorting on the average alone floats a statement one person
        # scored 5 above one that four people scored 4.5 — which would send the
        # talk after an outlier instead of the room's real pain.
        summary.sort(key=lambda d: (d['top_box'], d['average'] or 0), reverse=True)

        return Response({
            'success': True,
            'slug': slug,
            'title': s['title'],
            'event': s['event'],
            'session_date': s['session_date'],
            'public_link': f'https://omni.alphadirect.co.bw/speak/{slug}',
            'response_count': len(rows),
            'summary': summary,
            'scale_low': s['scale_low'],
            'scale_high': s['scale_high'],
            'responses': [{
                'id': str(r.id),
                'submitted_at': r.created_at,
                'pain_ratings': r.pain_ratings,
                'biggest_question': r.biggest_question,
                'wish_ai_did': r.wish_ai_did,
                'tried_already': r.tried_already,
                'respondent_name': r.respondent_name,
                'respondent_email': r.respondent_email,
                'respondent_role': r.respondent_role,
                'company_name': r.company_name,
                'industry': r.industry,
                'country': r.country,
                'headcount_band': r.headcount_band,
                'may_quote': r.may_quote,
            } for r in rows],
        })
