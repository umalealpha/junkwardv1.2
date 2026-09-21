"""Hand this morning's notes to a brief, then mark them delivered.

Lives in the app (tested) rather than in infra/ceo-monitor/ — that README is
explicit that business rules belong here.
"""
import logging

from django.utils import timezone

log = logging.getLogger(__name__)

#: How many notes a brief will print. Roughly 200 people can write to the CFO
#: space; at 25 words each, an uncapped block would be a wall of text and he
#: would stop reading it — which defeats the purpose. The rest are counted in
#: the footer line, not dropped silently.
MAX_NOTES_IN_BRIEF = 12


def _role_of(user) -> str:
    """A short human role for the byline. Job title is free text from the HR
    master and is the friendlier label; the profile title is the fallback."""
    emp = getattr(user, "employee_record", None)
    jt = (getattr(emp, "job_title", "") or "").strip()
    if jt:
        return jt[:40]
    prof = getattr(user, "profile", None)
    raw = (getattr(prof, "title", "") or "").strip()
    return raw.replace("_", " ").title()[:40] if raw else ""


def _brief_day():
    """The UTC date the views stamped on today's notes.

    MUST match how core.brief_note_views.next_brief_date assigns `for_date`,
    which works in UTC because the cron does. `timezone.localdate()` would be
    the Gaborone date — the same value at 04:30 UTC, but silently a day out if
    the brief ever ran late in the UTC evening. Matching the writer's clock
    removes the trap instead of relying on the schedule never moving.
    """
    return timezone.now().date()


def notes_for_brief(audience, for_date=None):
    """The notes to print, oldest first, capped.

    Returns (rows, overflow). Each row carries its own `id`, and ONLY those ids
    may be marked delivered — see mark_delivered.

    Reads `for_date <= today`, not `== today`, so a note that lost its place to
    the cap yesterday is at the front of the queue this morning instead of being
    stranded on a past date forever.

    Never raises: a brief must go out even if this fails, so the caller gets an
    empty block rather than an exception.
    """
    try:
        from core.models import BriefNote
        for_date = for_date or _brief_day()
        qs = (BriefNote.objects
              .filter(audience=audience, for_date__lte=for_date,
                      status=BriefNote.Status.QUEUED)
              .select_related("author")
              .order_by("for_date", "created_at"))
        rows, ids = [], []
        for n in qs:
            rows.append({
                "author": (n.author.get_full_name() or n.author.username),
                "role": _role_of(n.author),
                "body": n.body,
                "id": str(n.id),
            })
            ids.append(n.id)
        overflow = max(0, len(rows) - MAX_NOTES_IN_BRIEF)
        return rows[:MAX_NOTES_IN_BRIEF], overflow
    except Exception:  # noqa: BLE001 - never break the brief
        log.exception("brief_note_delivery: could not read notes")
        return [], 0


def mark_delivered(audience, note_ids) -> int:
    """Freeze the notes that were actually PRINTED, once the send succeeded.

    🔴 Two rules, both learned the hard way:

    1. Call this ONLY after the email send succeeded. Marking first and failing
       to send would silently lose someone's note — they would be shown
       "delivered" for something the reader never received.
    2. Mark ONLY the ids that were rendered. `notes_for_brief` caps the block,
       so a bulk update keyed on the day's filter would stamp the overflow as
       delivered too: its author is told it has gone out and can no longer edit
       it, the reader never saw it, and it can never resurface. Passing the
       rendered ids is what makes the cap safe.

    Only QUEUED rows are touched, so a re-run cannot re-stamp or resurrect a
    withdrawn note, and a second run is a no-op.
    """
    # Guard the argument shape LOUDLY. This used to take a date, and a caller
    # still passing one fell into the broad except below, logged, and returned
    # 0 — "nothing marked". That is the safe direction for one morning but it
    # means the notes are never frozen and go out again every day after, which
    # is worse and silent. A wrong call is a programming error, not a runtime
    # condition, so it raises.
    if isinstance(note_ids, (str, bytes)) or (
            note_ids is not None and not hasattr(note_ids, "__iter__")):
        raise TypeError(
            "mark_delivered(audience, note_ids) takes the ids that were "
            f"actually printed, not {type(note_ids).__name__}. Pass "
            "[n['id'] for n in rows] from notes_for_brief.")
    try:
        from core.models import BriefNote
        ids = [i for i in (note_ids or []) if i]
        if not ids:
            return 0
        return (BriefNote.objects
                .filter(audience=audience, id__in=ids,
                        status=BriefNote.Status.QUEUED)
                .update(status=BriefNote.Status.SENT, sent_at=timezone.now()))
    except Exception:  # noqa: BLE001
        log.exception("brief_note_delivery: could not mark notes delivered")
        return 0
