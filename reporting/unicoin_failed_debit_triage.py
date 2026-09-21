"""A3 — triage a failed debit into a plain reason and the right worklist.

Pack Task 3. The failed-debit report is split into two lists so the right team
acts: Finance chases the batch, the call centre phones the customer. And the
bank's terse code is turned into a plain reason a call-centre agent can read.

Pure logic (no DB, no framework) so it can be unit-proven off the box; the
report builder calls it per row.
"""
from __future__ import annotations

from reporting.unicoin_bank_codes import plain_language, is_mapped

#: The two worklists.
FINANCE = "finance"
CALL_CENTRE = "call_centre"

#: A debit that has already been retried this many times and is still failing is
#: not a batch problem any more — a person phones the customer. Matches the
#: existing 'critical' severity in build_failed_debits (retry_count >= 2).
_CALL_CENTRE_RETRIES = 2


def triage_failed_debit(*, status, retry_count, response) -> dict:
    """Classify one failed debit for the A3 lists.

    Returns {"reason_plain", "reason_mapped", "list_bucket"}. A debit already
    retried twice or more routes to the call centre (phone the customer); a
    fresh failure stays on the Finance action list.
    """
    try:
        retries = int(retry_count or 0)
    except (TypeError, ValueError):
        retries = 0
    bucket = CALL_CENTRE if retries >= _CALL_CENTRE_RETRIES else FINANCE
    return {
        "reason_plain": plain_language(response),
        "reason_mapped": is_mapped(response),
        "list_bucket": bucket,
    }
