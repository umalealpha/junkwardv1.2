"""
training/certificate.py

Renders the one-page PDF certificate for a passing AML/compliance
TrainingAttempt (Kakale Botana's module, bug 5ef4cc79).

CFO directive 20-Sep-2026: **"do the same certificate format for the AML module"**.
This used to draw its own vector-only certificate with the wordmark typed out in
Helvetica and the drifted brand pair (#0D1B2A / #F4A623). It now calls the house
renderer in `hris/training_certificate.py`, so a compliance certificate and an
induction certificate are the same document — the real full-colour logo used as
it is, the navy/orange frame, the serial and the QR.

One renderer, one certificate. If the house design changes, both change together.
"""
from __future__ import annotations

from hris.training_certificate import render_certificate


def render_certificate_pdf(attempt) -> bytes:
    """Return a landscape A4 PDF certificate as bytes for a passing attempt."""
    user = attempt.user
    name = (f'{user.first_name} {user.last_name}'.strip() or user.username)

    # The AML module stores a percentage, not a mark out of N, so the
    # "(x of y questions)" clause is simply omitted rather than invented.
    return render_certificate(
        holder_name=name,
        course_title=attempt.module.title,
        percent=attempt.score_pct,
        serial=attempt.certificate_number,
        issued_at=attempt.completed_at,
        statement=('and has confirmed that they have read and understood the '
                   'content of this module in full'),
    )
