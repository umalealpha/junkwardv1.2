"""taskboard/test_helpers.py — shared test decorators for the payment module.

⚠️ `window_always_open` IS NOW A NO-OP. The 07:00–09:30 submission window
(PAY-WIN-01, CFO 2026-07-29) was ABOLISHED on 2026-08-21 — "people can request
payment anytime they want" — so there is no window to open and the settings it
used to override are gone. It is kept, doing nothing, only so the eight suites
already decorated with it do not have to churn; three of them were being edited
in another session the day this landed.

Do NOT read it as evidence that a window still exists, and do not add it to new
tests. Removing the decorator and its usages is a tidy-up on its own.
"""
from django.test import override_settings

#: No-op. See the module docstring: PAY-WIN-01 was abolished 2026-08-21, so
#: there is nothing left to open. Overriding the old setting names here would be
#: worse than nothing — it would imply the control is still read somewhere.
window_always_open = override_settings()


def seed_adic():
    """Create the real ADIC Company row.

    Claims payments are ADIC's alone (CFO 2026-07-29) and the check requires a
    POSITIVE resolution — it must never be satisfied by _entity_code()'s
    catch-all `return 'ADIC'`, or an unknown entity would pass the very rule
    meant to stop it. So any test that raises a CLAIMS pack needs the row to
    genuinely exist.
    """
    from core.models import Company, Currency
    Currency.objects.get_or_create(code='BWP', defaults={'name': 'Pula'})
    Company.objects.get_or_create(
        code='ADIC',
        defaults={'name': 'Alpha Direct Insurance Company',
                  'base_currency_id': 'BWP'})
