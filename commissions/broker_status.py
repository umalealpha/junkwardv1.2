"""commissions/broker_status.py — C4 status by broker, C5 month close.

C4: for one broker and one debit month, the collection status of every policy
on the broker's register, read from Graphite's realpay_contract_installments
(CFO 19-Sep-2026: RealPay data comes THROUGH Graphite, never the RealPay API).

    S -> SUCCESSFUL   F -> FAILED   E -> ERROR   R / W -> PROCESSING
    A -> NO RESULT (raised, no result came back)   I -> CANCELLED
    no instalment in the month -> NOT FOUND
    FAILED / ERROR with ANY paid Graphite payment in the month -> EFT SUCCESS

🔴 THE GUARD. RealPay's instalment advice for DOM/COM stopped on 3 June 2026 and
that feed is the one that carries FAILURES. Read today, every DOM/COM debit is
'A' and a status list would show no failures at all — a false all-clear on money
owed. So, exactly like the weekly failed-debits job
(realpay.failed_debits.book_is_reporting, reused here, not copied): if the month
holds scheduled DOM/COM debits and not one outcome came back, REFUSE.

C5: Finance presses "Close month" (Full Access role only — CFO 19-Sep-2026;
never a calendar job, never a report load). The close records every policy's
status for the month in the history table (that is Current -> Previous: next
month's Previous Status is read from it), snapshots each broker's payable, and
resolves earlier months' PROCESSING rows that have since come back.
Closing the same month twice does nothing the second time.

Read-only on Graphite. Nothing here pays anything.
"""
from __future__ import annotations

import calendar
import datetime as _dt
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from commissions import broker_summary
from commissions.commission_calc import money
from commissions.models import (Broker, BrokerMonthClose, BrokerPayableChange,
                                BrokerStatusHistory, BrokerCommissionRate)
from realpay import failed_debits, graphite_feed

log = logging.getLogger(__name__)

SUCCESSFUL = 'SUCCESSFUL'
FAILED = 'FAILED'
ERROR = 'ERROR'
PROCESSING = 'PROCESSING'
CANCELLED = 'CANCELLED'
NO_RESULT = 'NO RESULT'
NOT_FOUND = 'NOT FOUND'
EFT_SUCCESS = 'EFT SUCCESS'
UNKNOWN = 'UNKNOWN'

_CODE_TO_STATUS = {
    'S': SUCCESSFUL, 'F': FAILED, 'E': ERROR, 'R': PROCESSING, 'W': PROCESSING,
    'A': NO_RESULT, 'I': CANCELLED,
}


class StatusRefused(Exception):
    """We will not state a status (or close a month). `reason` says why in
    words a person can act on; `unreachable` separates "Graphite is down" from
    "Graphite answered and the answer cannot be trusted"."""

    def __init__(self, reason: str, feed=None, unreachable: bool = False):
        super().__init__(reason)
        self.reason, self.feed, self.unreachable = reason, feed, unreachable


def month_label(period: str) -> str:
    """'2026-06' -> 'JUNE' (for the late-success status, e.g. JUNE SUCCESS)."""
    return calendar.month_name[int(period[5:7])].upper()


def _last_day(period: str) -> _dt.date:
    return broker_summary.month_bounds(period)[1] - _dt.timedelta(days=1)


def feed_check(period: str, today=None) -> dict:
    """The dead-feed guard for one debit month. Raises StatusRefused."""
    today = today or timezone.localdate()
    start, _ = broker_summary.month_bounds(period)
    if start > today:
        raise StatusRefused(f'{period} has not started yet, so there is no status to read.')
    last = min(_last_day(period), today)
    try:
        feed = failed_debits.book_is_reporting(days=(last - start).days + 1, today=last)
    except failed_debits.GraphiteUnavailable as exc:
        raise StatusRefused(f'Graphite could not be read ({exc}), so no status was worked out.',
                            unreachable=True) from exc
    except Exception as exc:                                  # noqa: BLE001
        log.warning('broker status: feed check failed', exc_info=True)
        raise StatusRefused('Graphite could not be read, so no status was worked out.',
                            unreachable=True) from exc
    if not feed.get('reporting'):
        raise StatusRefused(
            f'Refused. {feed.get("no_outcome", 0)} Domestic/Commercial debits were due '
            f'between {start:%d %b %Y} and {last:%d %b %Y} and RealPay returned a result '
            f'for none of them. The DOM/COM failure feed has been silent since 3 June '
            f'2026, so failed debits cannot be seen — a status list now would read as '
            f'an all-clear that nobody has checked. The feed has to be fixed at '
            f"RealPay's end first.",
            feed=feed)
    return feed


def registered_policies(broker: Broker) -> dict:
    """{policy number: source} — the broker's Graphite book under its aliases,
    plus the rows a person added or uploaded on the register."""
    names = list(broker.aliases.values_list('graphite_agency_name', flat=True))
    try:
        book = graphite_feed.broker_policies(names)
    except Exception as exc:                                  # noqa: BLE001
        raise StatusRefused("Graphite could not be read, so this broker's policies "
                            'could not be listed.', unreachable=True) from exc
    if book is None:
        raise StatusRefused('Graphite is not configured here.', unreachable=True)
    out: dict[str, str] = {}
    for r in book:
        pn = graphite_feed.policy_number_of(r.get('policy_number'))
        if pn:
            out[pn] = 'graphite'
    for pn, src in broker.policies.values_list('policy_number', 'source'):
        pn = graphite_feed.policy_number_of(pn)
        if pn:
            out.setdefault(pn, src)
    return out


def _previous_statuses(broker: Broker, period: str) -> dict:
    """{policy: status} recorded for the month BEFORE `period` — the latest row
    wins, so a late resolution replaces the status captured at close."""
    prev = broker_summary.previous_period(period)
    out = {}
    for pn, st in (BrokerStatusHistory.objects.filter(broker=broker, debit_month=prev)
                   .order_by('created_at').values_list('policy_number', 'status')):
        out[pn] = st
    return out


def broker_statuses(broker: Broker, period: str) -> dict:
    """C4 without the guard (callers go through status_by_broker / close_month)."""
    start, end_excl = broker_summary.month_bounds(period)
    reg = registered_policies(broker)
    keys = sorted(reg)
    try:
        seen = graphite_feed.policy_statuses(keys, start=start, end=_last_day(period)) if keys else {}
        failed = [pn for pn in keys
                  if _CODE_TO_STATUS.get((seen.get(pn) or {}).get('status')) in (FAILED, ERROR)]
        paid = graphite_feed.payments_in_window(failed, start, end_excl) if failed else {}
    except Exception as exc:                                  # noqa: BLE001
        raise StatusRefused('Graphite could not be read, so no status was worked out.',
                            unreachable=True) from exc
    if paid is None:
        raise StatusRefused('Graphite is not configured here.', unreachable=True)

    previous = _previous_statuses(broker, period)
    rows, tally = [], {}
    for pn in keys:
        hit = seen.get(pn)
        if hit is None:
            status = NOT_FOUND
        else:
            status = _CODE_TO_STATUS.get(hit.get('status'), UNKNOWN)
        eft = paid.get(pn) if status in (FAILED, ERROR) else None
        if eft:
            status = EFT_SUCCESS
        rows.append({
            'policy_number': pn,
            'source': reg[pn],
            'status': status,
            'raw_status': (hit or {}).get('status', ''),
            'last_attempt': (hit or {}).get('action_date', ''),
            'amount': str(money((hit or {}).get('amount') or 0)),
            'eft_amount': str(money(eft['amount'])) if eft else None,
            'previous_status': previous.get(pn, ''),
        })
        tally[status] = tally.get(status, 0) + 1
    return {'broker': {'id': str(broker.id), 'name': broker.name},
            'period': period, 'previous_period': broker_summary.previous_period(period),
            'refused': False, 'rows': rows, 'count': len(rows), 'tally': tally}


def status_by_broker(broker: Broker, period: str, today=None) -> dict:
    """C4 for one broker and debit month — guarded. Raises StatusRefused."""
    feed = feed_check(period, today)
    out = broker_statuses(broker, period)
    out['feed'] = feed
    return out


# ─── C5 — close month ────────────────────────────────────────────────────────

def _pending_late(user):
    """Plan the late resolutions: every (broker, policy, month) whose latest
    recorded status is PROCESSING, re-read now. Returns (history rows to write,
    {(broker_id, month)} whose payable must be re-snapshotted)."""
    proc = list(BrokerStatusHistory.objects.filter(status=PROCESSING)
                .values_list('broker_id', 'policy_number', 'debit_month'))
    if not proc:
        return [], set()
    latest = {}
    for b, pn, m, st in (BrokerStatusHistory.objects
                         .filter(debit_month__in={p[2] for p in proc},
                                 policy_number__in={p[1] for p in proc})
                         .order_by('created_at')
                         .values_list('broker_id', 'policy_number', 'debit_month', 'status')):
        latest[(b, pn, m)] = st
    pending = [k for k, st in latest.items() if st == PROCESSING]

    writes, touched = [], set()
    for month in sorted({k[2] for k in pending}):
        start, _ = broker_summary.month_bounds(month)
        keys = [k for k in pending if k[2] == month]
        seen = graphite_feed.policy_statuses([k[1] for k in keys], start=start,
                                             end=_last_day(month))
        for b, pn, m in keys:
            now = _CODE_TO_STATUS.get((seen.get(pn) or {}).get('status'))
            if now == SUCCESSFUL:
                status = f'{month_label(m)} SUCCESS'
                touched.add((b, m))
            elif now in (FAILED, ERROR):
                status = now            # a late failure stays a failure
            else:
                continue                # still processing — ask again next close
            writes.append(BrokerStatusHistory(
                broker_id=b, policy_number=pn, debit_month=m, status=status,
                source=BrokerStatusHistory.Source.LATE, recorded_by=user))
    return writes, touched


def close_month(period: str, user=None, today=None) -> dict:
    """Close one debit month for every active broker. Raises StatusRefused.

    Every Graphite read happens BEFORE anything is written, so a refusal or an
    outage part-way leaves nothing half-closed.
    """
    today = today or timezone.localdate()
    start, end_excl = broker_summary.month_bounds(period)
    if end_excl > today:
        raise StatusRefused(f'{period} has not finished yet. It can be closed from '
                            f'{end_excl:%d %b %Y}.')
    if BrokerMonthClose.objects.filter(period=period).exists():
        return {'period': period, 'already_closed': True, 'brokers_closed': 0,
                'statuses_recorded': 0, 'late_resolved': 0, 'payable_changes': 0}
    if BrokerCommissionRate.in_force_on(start) is None:
        raise StatusRefused(f'No commission rates are configured for {period}, so there '
                            f'is no payable to close.')

    feed_check(period, today)
    try:
        health = graphite_feed.outcome_feed_health(start, end_excl)
        pays = broker_summary.payables(period)
    except Exception as exc:                                  # noqa: BLE001
        raise StatusRefused('Graphite could not be read, so the month was not closed.',
                            unreachable=True) from exc
    if not health.get('reporting'):
        raise StatusRefused('Refused. Collected premium for this month is incomplete: '
                            + (health.get('reason') or 'the RealPay results have not come back.'),
                            feed=health)

    brokers = list(Broker.objects.filter(is_active=True).prefetch_related('aliases'))
    per_broker = [(b, broker_statuses(b, period)) for b in brokers]
    try:
        late_rows, touched = _pending_late(user)
        late_pays = {m: broker_summary.payables(m) for m in {m for _, m in touched}}
    except Exception as exc:                                  # noqa: BLE001
        raise StatusRefused('Graphite could not be read, so the month was not closed.',
                            unreachable=True) from exc

    recorded = changes = 0
    try:
        with transaction.atomic():
            for b, res in per_broker:
                calc = pays[b.id]
                BrokerMonthClose.objects.create(
                    broker=b, period=period, commission_excl_vat=calc.commission_excl_vat,
                    wht=calc.wht, vat=calc.vat, current_payable=calc.current_payable,
                    closed_by=user)
                BrokerStatusHistory.objects.bulk_create([
                    BrokerStatusHistory(broker=b, policy_number=r['policy_number'],
                                        debit_month=period, status=r['status'],
                                        source=BrokerStatusHistory.Source.CLOSE,
                                        recorded_by=user)
                    for r in res['rows']])
                recorded += len(res['rows'])
            BrokerStatusHistory.objects.bulk_create(late_rows)
            for broker_id, month in sorted(touched, key=lambda t: (t[1], str(t[0]))):
                snap = (BrokerMonthClose.objects.select_for_update()
                        .filter(broker_id=broker_id, period=month).first())
                new = (late_pays.get(month) or {}).get(broker_id)
                if snap is None or new is None or new.current_payable == snap.current_payable:
                    continue
                BrokerPayableChange.objects.create(
                    month_close=snap, old_payable=snap.current_payable,
                    new_payable=new.current_payable, changed_by=user,
                    reason=f'Late PROCESSING resolved to {month_label(month)} SUCCESS '
                           f'at the close of {period}.')
                snap.current_payable = new.current_payable
                snap.commission_excl_vat = new.commission_excl_vat
                snap.wht, snap.vat = new.wht, new.vat
                snap.save(update_fields=['current_payable', 'commission_excl_vat',
                                         'wht', 'vat', 'updated_at'])
                changes += 1
    except IntegrityError:
        # Two presses at once: the unique (broker, period) constraint let one
        # through; the other is the same no-op as a second close.
        return {'period': period, 'already_closed': True, 'brokers_closed': 0,
                'statuses_recorded': 0, 'late_resolved': 0, 'payable_changes': 0}
    return {'period': period, 'already_closed': False, 'brokers_closed': len(per_broker),
            'statuses_recorded': recorded, 'late_resolved': len(late_rows),
            'payable_changes': changes}
