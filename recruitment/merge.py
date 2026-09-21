"""
recruitment/merge.py — fold one Candidate into another safely.

The Candidate email-uniqueness constraint (Manus QC 2026-08-27) stops NEW
duplicates, but a merge tool is still needed to fold any that predate it (and to
join a walk-in captured without an email to their later online application). This
respects the uniq_req_candidate constraint: an application on `drop` that would
collide with one `keep` already has for the same requisition is dropped, not
moved (which would violate the constraint and abort the whole merge).
"""
from __future__ import annotations

from django.db import transaction

from .models import Application, Candidate


def merge_candidates(keep: Candidate, drop: Candidate) -> dict:
    """Move `drop`'s applications to `keep`, then delete `drop`. Returns a small
    summary {moved, dropped_dup}. Idempotent-safe: merging a candidate into
    itself is a no-op."""
    if keep.pk == drop.pk:
        return {"moved": 0, "dropped_dup": 0, "note": "same candidate"}

    moved = dropped = 0
    with transaction.atomic():
        keep_reqs = set(
            Application.objects.filter(candidate=keep)
            .values_list("requisition_id", flat=True)
        )
        for app in Application.objects.filter(candidate=drop):
            if app.requisition_id in keep_reqs:
                app.delete()               # keep already applied to this role
                dropped += 1
            else:
                app.candidate = keep
                app.save(update_fields=["candidate"])
                keep_reqs.add(app.requisition_id)
                moved += 1
        # Carry a CV / skills onto keep if it has none, so the merge does not
        # lose the only copy of the person's documents.
        if not keep.cv and drop.cv:
            keep.cv = drop.cv
            keep.cv_text = drop.cv_text or keep.cv_text
            keep.skills = keep.skills or drop.skills
            keep.save(update_fields=["cv", "cv_text", "skills"])
        drop.delete()

    return {"moved": moved, "dropped_dup": dropped}
