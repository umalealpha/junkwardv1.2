"""Who may open the Transformation Board.

CFO, 2026-09-20: "accessible to ceo cfo coo and unami".

This board names departments and people and shows the consolidated staff cost.
It is therefore a CLOSED list, not a title-based gate: adding a viewer is a
deliberate act, not a side effect of someone's job title changing.

The list lives in the environment so a viewer can be added or removed without
a deploy. Superusers pass, as everywhere else in Omni.
"""
from __future__ import annotations

import os

from rest_framework.permissions import BasePermission

# The four the CFO named. Overridable with TRANSFORMATION_VIEWERS in the env.
DEFAULT_VIEWERS = (
    'aiyer@alphadirect.co.bw',           # Arun Iyer — CEO
    'pganesharajah@alphadirect.co.bw',   # Prathap Ganesharajah — CFO
    'arjuniyer@alphadirect.co.bw',       # Arjun Parameswaran — COO
    'ubutale@alphadirect.co.bw',         # Unami Butale — Chief Human Capital Officer
    'excoboard@alphadirect.co.bw',       # the EXCO mailbox (the CFO's own account)
)


def viewers() -> set[str]:
    raw = os.environ.get('TRANSFORMATION_VIEWERS', '')
    names = [e.strip().lower() for e in raw.split(',') if e.strip()]
    return set(names or [e.lower() for e in DEFAULT_VIEWERS])


def may_view(user) -> bool:
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    email = (getattr(user, 'email', '') or '').strip().lower()
    username = (getattr(user, 'username', '') or '').strip().lower()
    allowed = viewers()
    # Positive match only — never a prefix or a domain match. An unknown
    # address must be REFUSED, not fall through to a default (checklist L6).
    if email and email in allowed:
        return True
    return bool(username and username in allowed)


class CanViewTransformationBoard(BasePermission):
    message = ('The Transformation Board is for the CEO, CFO, COO and the Chief '
               'Human Capital Officer.')

    def has_permission(self, request, view) -> bool:
        return may_view(getattr(request, 'user', None))
