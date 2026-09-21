"""
ifrs17/exceptions_service.py — seed, answer and review the exception register.

Three jobs, and nothing else:
  seed_register()   — create the rows from the actuary's disclosures (idempotent)
  answer()          — Finance records the explanation + action
  review()          — CFO / FC / FM accepts it, or returns it with a note

Deliberately NOT here: any recomputation of a figure. The register describes the
signed valuation; it never changes it.
"""
from __future__ import annotations

from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from . import materiality as M
from .models import IFRS17Exception


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
@transaction.atomic
def seed_register(financial_year: str = 'FY2026', *, user=None) -> dict:
    """Create/refresh a register row for every disclosed variance.

    Idempotent and SAFE TO RE-RUN: the actuary's own fields (title, detail,
    amount, band) are refreshed from the report, but anything a human wrote —
    explanation, action, owner, status, review — is never touched.
    """
    created = updated = 0
    for v in M.classified_variances():
        band = v['materiality_band']
        defaults = {
            'title': v['title'],
            'detail': v['detail'],
            'source': v.get('source', ''),
            'amount': D(str(v.get('amount') or 0)),
            'severity': v.get('severity', ''),
            'band': band,
            'materiality_basis': v['materiality_basis'],
            'materiality_reason': v['materiality_reason'],
            'threshold_at_seed': M.REGISTER_THRESHOLD,
        }
        obj, was_created = IFRS17Exception.objects.get_or_create(
            ref=v['ref'], financial_year=financial_year,
            defaults={
                **defaults,
                # Anything not material starts life closed — that IS the
                # "small variance, ignore" instruction, made structural.
                'status': (IFRS17Exception.Status.OPEN
                           if band == IFRS17Exception.Band.MATERIAL
                           else IFRS17Exception.Status.NO_RESPONSE_REQUIRED),
            },
        )
        if was_created:
            created += 1
            continue
        # Refresh only the actuary-owned fields.
        dirty = False
        for field, value in defaults.items():
            if getattr(obj, field) != value:
                setattr(obj, field, value)
                dirty = True
        if dirty:
            obj.save(audit_user=user,
                     audit_description=f'Refresh IFRS 17 exception {obj.ref} from the report')
            updated += 1
    return {
        'financial_year': financial_year,
        'created': created,
        'updated': updated,
        'total': IFRS17Exception.objects.filter(financial_year=financial_year).count(),
    }


# ---------------------------------------------------------------------------
# Answer  (Finance)
# ---------------------------------------------------------------------------
@transaction.atomic
def answer(exception: IFRS17Exception, *, explanation: str, action: str = '',
           owner=None, user=None, ip: str | None = None) -> IFRS17Exception:
    """Finance records what the exception is and what is being done about it."""
    if not exception.explanation_required:
        raise ValidationError(
            f'{exception.ref} is {exception.get_band_display().lower()} — it is '
            f'below the materiality threshold and does not need an explanation.'
        )
    if exception.status == IFRS17Exception.Status.ACCEPTED:
        raise ValidationError(
            f'{exception.ref} has already been accepted. Ask the reviewer to '
            f'return it before changing the explanation.'
        )
    exception.explanation = (explanation or '').strip()
    exception.action = (action or '').strip()
    if owner is not None:
        exception.owner = owner
    exception.status = IFRS17Exception.Status.ANSWERED
    exception.answered_by = user
    exception.answered_at = timezone.now()
    # Clear a previous review so an answered item cannot look accepted.
    exception.reviewed_by = None
    exception.reviewed_at = None
    exception.review_note = ''
    exception.full_clean()          # enforces the minimum-length gate
    exception.save(audit_user=user, audit_ip=ip,
                   audit_description=f'Answer IFRS 17 exception {exception.ref}')
    return exception


# ---------------------------------------------------------------------------
# Review  (CFO / FC / FM)
# ---------------------------------------------------------------------------
@transaction.atomic
def review(exception: IFRS17Exception, *, accept: bool, note: str = '',
           user=None, ip: str | None = None) -> IFRS17Exception:
    """Accept the answer, or send it back with what is missing."""
    if exception.status != IFRS17Exception.Status.ANSWERED:
        raise ValidationError(
            f'{exception.ref} is "{exception.get_status_display()}" — only an '
            f'answered exception can be reviewed.'
        )
    note = (note or '').strip()
    if not accept and not note:
        raise ValidationError(
            'Say what is missing when returning an exception to Finance.'
        )
    exception.status = (IFRS17Exception.Status.ACCEPTED if accept
                        else IFRS17Exception.Status.RETURNED)
    exception.reviewed_by = user
    exception.reviewed_at = timezone.now()
    exception.review_note = note
    exception.full_clean()
    verb = 'Accept' if accept else 'Return'
    exception.save(audit_user=user, audit_ip=ip,
                   audit_description=f'{verb} IFRS 17 exception {exception.ref}')
    return exception


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def register_summary(financial_year: str = 'FY2026') -> dict:
    """Counts the CFO actually cares about: how many material ones are open."""
    qs = IFRS17Exception.objects.filter(financial_year=financial_year)
    material = qs.filter(band=IFRS17Exception.Band.MATERIAL)
    return {
        'financial_year': financial_year,
        'total': qs.count(),
        'material': material.count(),
        'watch': qs.filter(band=IFRS17Exception.Band.WATCH).count(),
        'immaterial': qs.filter(band=IFRS17Exception.Band.IMMATERIAL).count(),
        'outstanding': material.filter(status__in=[
            IFRS17Exception.Status.OPEN, IFRS17Exception.Status.RETURNED]).count(),
        'awaiting_review': material.filter(
            status=IFRS17Exception.Status.ANSWERED).count(),
        'accepted': material.filter(
            status=IFRS17Exception.Status.ACCEPTED).count(),
        'material_exposure': sum(
            (e.amount for e in material), D('0')),
        'materiality': M.basis_summary(),
    }
