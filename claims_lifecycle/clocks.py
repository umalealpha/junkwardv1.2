"""B4 and B5 — the three claim clocks.

  Clock 1  supplier invoice APPROVED on the claim  ->  the BANK CONFIRMS it left
  Clock 2  Agreement of Loss AUTHORISED            ->  the BANK CONFIRMS the
                                                       client was paid (target 5)
  Clock 3  the claim was NOTIFIED                  ->  the decline letter is SENT
                                                       to the client (target 14)

The rule that outranks everything else here: **APPROVED IS NOT PAID.** Neither
money clock may stop on an approval, only on a confirmed bank payment. In Omni
that is an FNB batch whose status is 'settled'; `PaymentRequest.status == 'paid'`
only means authorised, and a 'failed', 'cancelled' or 'unknown' batch never
moved money at all — so a settled DATE alongside one of those statuses does not
stop the clock either. A status argument that changes nothing is decoration, and
a clock that lies is worse than no clock.

Clock 3 counts from NOTIFICATION — not the day the file was opened, not the day
the police report arrived. A claim with no notification date reads 'unknown'
rather than quietly counting from something else.

Pure: no database, no clock, no Django. Working days come from the one shared
helper so the tracker cannot grow two definitions of a working day.
"""
from claims_lifecycle.workdays import count_working_days

#: FNB batch statuses that mean the money did NOT move. A settled date sitting
#: next to one of these is a data problem, never a stopped clock.
DID_NOT_MOVE = frozenset({'failed', 'cancelled', 'unknown', 'pending',
                          'submitted', 'acknowledged'})


def _light(days, target_days):
    """Green up to and including the target, amber past it, red at double.

    Same three colours and the same "at the target is still green" rule the
    completion bar uses — one meaning per colour across the whole screen.
    """
    if target_days is None:
        return 'unknown'
    if days <= target_days:
        return 'green'
    if days <= target_days * 2:
        return 'amber'
    return 'red'


def _absent(target_days):
    return {'applies': False, 'running': False, 'days': 0, 'stopped_on': None,
            'light': 'unknown', 'target_days': target_days}


def _measure(start, stopped_on, today, target_days, holidays):
    end = stopped_on or today
    days = count_working_days(start, end, holidays or set())
    return {
        'applies': True,
        'running': stopped_on is None,
        'days': days,
        'stopped_on': stopped_on,
        'light': _light(days, target_days),
        'target_days': target_days,
    }


def _bank_confirmed(bank_settled_on, fnb_status):
    """A confirmation needs BOTH a date and a status that means the money left."""
    if bank_settled_on is None:
        return None
    if fnb_status is not None and str(fnb_status).strip().lower() in DID_NOT_MOVE:
        return None
    return bank_settled_on


def clock_supplier(invoice_approved_on=None, bank_settled_on=None, today=None,
                   target_days=5, holidays=None, payment_request_status=None,
                   fnb_status=None):
    """Supplier invoice approved on the claim -> the bank confirms it left.

    `payment_request_status` is accepted and deliberately ignored: 'paid' in Omni
    means authorised, and authorising is not paying. It is in the signature so a
    caller passing it cannot believe it stops the clock.
    """
    if invoice_approved_on is None:
        return _absent(target_days)
    return _measure(invoice_approved_on,
                    _bank_confirmed(bank_settled_on, fnb_status),
                    today, target_days, holidays)


def clock_aol(authorised_on=None, bank_settled_on=None, today=None,
              target_days=5, holidays=None, fnb_status=None):
    """Agreement of Loss authorised -> the bank confirms the client was paid."""
    if authorised_on is None:
        return _absent(target_days)
    return _measure(authorised_on,
                    _bank_confirmed(bank_settled_on, fnb_status),
                    today, target_days, holidays)


def clock_repudiation(notified_on=None, letter_sent_on=None, today=None,
                      target_days=14, holidays=None, file_opened_on=None,
                      police_report_on=None):
    """Notified -> the decline letter is sent to the client.

    `file_opened_on` and `police_report_on` are accepted and deliberately
    ignored. They are the two dates this clock has always been measured from by
    mistake; taking them and refusing to use them is what makes that explicit.
    """
    if notified_on is None:
        return _absent(target_days)
    out = _measure(notified_on, letter_sent_on, today, target_days, holidays)
    out['from_date'] = notified_on
    return out
