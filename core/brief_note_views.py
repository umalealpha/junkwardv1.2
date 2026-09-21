"""The morning-brief note spaces (CFO 2026-09-10).

Endpoints (all under /api/v1/):
    GET    /brief-notes/access/          -> what this person may do (drives the UI)
    GET    /brief-notes/?audience=ceo    -> the space for the next brief
    POST   /brief-notes/                 -> write or replace my note
    DELETE /brief-notes/<id>/            -> withdraw my note

Deliberately small: no pagination, no filtering, no admin surface. A space holds
at most one note per person per morning, so it is always a short list.
"""
import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.brief_note_access import (AUDIENCES, CEO_AUDIENCE, CFO_AUDIENCE,
                                    audiences_for, can_post, can_read_space)
from core.models import BriefNote

#: The CEO brief is built at 04:30 UTC. A note written after that missed today
#: and belongs to tomorrow — otherwise someone writes at 05:00, sees "queued for
#: today", and is quietly never delivered.
BRIEF_BUILD_HOUR_UTC = 4
BRIEF_BUILD_MINUTE_UTC = 30


def next_brief_date(now=None) -> datetime.date:
    """The morning the next note will actually appear in."""
    now = now or timezone.now()
    cutoff = now.replace(hour=BRIEF_BUILD_HOUR_UTC, minute=BRIEF_BUILD_MINUTE_UTC,
                         second=0, microsecond=0)
    return now.date() if now < cutoff else now.date() + datetime.timedelta(days=1)


def _shape(note, *, me) -> dict:
    return {
        "id": str(note.id),
        "author": (note.author.get_full_name() or note.author.username),
        "author_username": note.author.username,
        "is_mine": note.author_id == me.id,
        "body": note.body,
        "words": BriefNote.word_count(note.body),
        "status": note.status,
        "for_date": note.for_date.isoformat(),
        "locked": note.is_locked,
        "created_at": note.created_at.isoformat(),
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def brief_note_access(request):
    """What the dashboard should show this person. Loading renders nothing, so
    an unauthorised card never flashes into view."""
    allowed = audiences_for(request.user)
    return Response({
        "audiences": allowed,
        "can_post_ceo": CEO_AUDIENCE in allowed,
        "can_post_cfo": CFO_AUDIENCE in allowed,
        "word_limit": BriefNote.WORD_LIMIT,
        "next_brief_date": next_brief_date().isoformat(),
    })


def _read_space(request):
    audience = (request.GET.get("audience") or "").strip().lower()
    if audience not in AUDIENCES:
        return Response({"detail": "Unknown space."}, status=400)

    may_post = can_post(request.user, audience)
    may_read_all = can_read_space(request.user, audience)
    if not (may_post or may_read_all):
        return Response({"detail": "This space is not yours."}, status=403)

    for_date = next_brief_date()
    qs = (BriefNote.objects
          .filter(audience=audience, for_date=for_date)
          .exclude(status=BriefNote.Status.WITHDRAWN)
          .select_related("author"))
    # The CEO space is shared by design; the CFO space is not — a junior's note
    # to the CFO is between the two of them.
    if not may_read_all:
        qs = qs.filter(author=request.user)

    notes = [_shape(n, me=request.user) for n in qs]
    return Response({
        "audience": audience,
        "for_date": for_date.isoformat(),
        # `shared` means "everyone in this space sees each other" — true only of
        # the CEO space. `can_read_all` means "YOU may read every note here",
        # which is how the CFO sees his own space and how the CEO sees both.
        # Keying the UI on `shared` alone left the CFO with a box to write a
        # note to himself and every staff message hidden.
        "shared": may_read_all and audience == CEO_AUDIENCE,
        "can_read_all": may_read_all,
        "can_post": may_post,
        "word_limit": BriefNote.WORD_LIMIT,
        "my_note": next((n for n in notes if n["is_mine"]), None),
        "notes": notes,
    })


def _write_note(request):
    """Write or replace my note for the next brief.

    Idempotent on (audience, author, for_date) so a double-tap on a phone
    replaces the note instead of hitting the unique constraint.
    """
    audience = (request.data.get("audience") or "").strip().lower()
    if audience not in AUDIENCES:
        return Response({"detail": "Unknown space."}, status=400)
    if not can_post(request.user, audience):
        return Response({"detail": "You cannot post to this space."}, status=403)

    body = (request.data.get("body") or "").strip()
    for_date = next_brief_date()
    existing = (BriefNote.objects
                .filter(audience=audience, author=request.user, for_date=for_date)
                .first())
    if existing and existing.is_locked:
        return Response({"detail": (
            "That note has already gone out in this morning's brief, so it can "
            "no longer be changed."
        )}, status=409)

    note = existing or BriefNote(audience=audience, author=request.user,
                                 for_date=for_date)
    note.body = body
    note.status = BriefNote.Status.QUEUED
    try:
        with transaction.atomic():
            note.save()
    except ValidationError as ex:
        detail = (ex.message_dict.get("body") or ["That note is not valid."])[0] \
            if hasattr(ex, "message_dict") else str(ex)
        return Response({"detail": detail}, status=400)
    except IntegrityError:
        # Two first writes at once (phone and web) both saw existing=None, so
        # the second one lost the race to the unique constraint. Model-level
        # validation cannot catch this: full_clean(exclude=["author"]) skips
        # every constraint that includes `author`. Treat it as the replace the
        # user intended rather than handing them a 500.
        again = (BriefNote.objects
                 .filter(audience=audience, author=request.user,
                         for_date=for_date)
                 .first())
        if again is None:
            return Response({"detail": "Could not save that note — try again."},
                            status=409)
        if again.is_locked:
            return Response({"detail": (
                "That note has already gone out in this morning's brief."
            )}, status=409)
        again.body = body
        again.status = BriefNote.Status.QUEUED
        again.save()
        return Response(_shape(again, me=request.user), status=200)

    return Response(_shape(note, me=request.user),
                    status=200 if existing else 201)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def brief_note_withdraw(request, note_id):
    note = BriefNote.objects.filter(pk=note_id).select_related("author").first()
    if note is None:
        return Response({"detail": "That note no longer exists."}, status=404)
    if note.author_id != request.user.id:
        return Response({"detail": "You can only withdraw your own note."},
                        status=403)
    if note.is_locked:
        return Response({"detail": (
            "That note has already gone out in this morning's brief."
        )}, status=409)
    note.status = BriefNote.Status.WITHDRAWN
    note.save(update_fields=["status", "updated_at"])
    return Response({"withdrawn": True, "id": str(note.id)})


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def brief_note_router(request):
    """One URL for the collection: GET reads the space, POST writes my note."""
    return _write_note(request) if request.method == "POST" else _read_space(request)
