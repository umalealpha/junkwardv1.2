"""
healthcare/afa_service.py — Phase 3 of the ADH → AFA load file.

Ties the reader (afa_members) and the engine (afa_loadfile) to the stored
snapshot, and decides what actually goes in a given day's file.

HOW THE DAILY FILE IS DECIDED — the rule that must not drift:

    Every run is a FULL PULL of the membership, diffed against the snapshot of
    what we last successfully sent. It is never an accumulated list of events.

That makes a missed day self-healing and a double run harmless.

WHAT GOES IN THE FILE is controlled by `AFA_LOADFILE_MODE`:
  * 'full'  (DEFAULT) — every member on cover, every run. The safe assumption
     while nobody has confirmed whether AFA expect the whole membership or only
     the day's changes. If AFA replace their membership from each file, a delta
     file empties the scheme.
  * 'delta' — only rows whose content changed. ONLY set this once AFA have
     confirmed in writing that they expect a delta.

Triggers annotate WHY a row changed. They do not decide WHAT is in the file —
with one exception:

    PAYMENT IS A GATE, NOT A NOTE. A member we have never sent before is held
    until that employer group's invoice is paid in Omni. Departures are NEVER
    gated on payment — someone leaving must always flow.

    Graphite records NO payments for ADH at all (verified 7-Aug-2026: zero
    transactions, any status), so the gate reads Omni's billing invoices via
    AfaGroupNameMap.billing_contact. If that link is unset the group's new
    members are held and named, never sent.

DATA PROTECTION (AD-POL-AI-GOV-001): rendered rows are policyholder data. They
live in the run record, go to AFA, and nowhere else. Never logged, never in an
error message, never near an external model. Every log line is a count. The
snapshot stores hashes and ids only — no names, no ID numbers.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from healthcare import afa_loadfile as engine
from healthcare import afa_members as reader
from healthcare.models import AfaGroupNameMap, AfaLoadFileRun, AfaMemberSnapshot

log = logging.getLogger('afa-loadfile')

MODE_FULL = 'full'
MODE_DELTA = 'delta'

# A run whose departures exceed this share of everyone we believe is on cover
# aborts. The H25 fence guards the READ; this guards the DIFF — a mass status
# flip upstream (or someone "correcting" AFA_ON_COVER_STATUSES) leaves the row
# count intact, sails through the read fence, and resigns the entire scheme.
MAX_DEPARTURE_RATIO = 0.15
# ...but never on a handful. On a small book any single departure exceeds any
# ratio, so a five-person employer could never lose anybody. The fence is for
# mass events; below this many departures the ratio is not consulted at all.
MIN_DEPARTURES_BEFORE_FENCE = 5


def _mode() -> str:
    return (getattr(settings, 'AFA_LOADFILE_MODE', MODE_FULL) or MODE_FULL).lower()


def _region_default() -> str:
    return getattr(settings, 'AFA_REGION_NAME', '') or ''


def _default_resignation_reason() -> str:
    """AFA need a reason code for every departure; Graphite stores free text.

    Deliberately EMPTY by default, so an unmapped departure is HELD and named
    rather than labelled with a guess. Set AFA_DEFAULT_RESIGNATION_REASON once
    the business confirms which of AFA's ten codes a plain cancellation is.
    """
    return getattr(settings, 'AFA_DEFAULT_RESIGNATION_REASON', '') or ''


def _key(policy_number: str, dependant_no: int) -> str:
    return f'{policy_number}|{dependant_no}'


def _unkey(k: str) -> Tuple[str, int]:
    policy_number, _, dep = k.rpartition('|')
    return policy_number, int(dep)


def _group_maps() -> Dict[str, AfaGroupNameMap]:
    return {
        m.employer_group_id: m
        for m in AfaGroupNameMap.objects.select_related('billing_contact').filter(is_active=True)
    }


def _paid_group_ids(maps: Dict[str, AfaGroupNameMap]) -> set:
    """Employer groups whose Omni invoice has been paid.

    One query over the linked contacts, never one per group. A group with no
    billing_contact can never appear here — its new members stay held, which is
    the correct failure direction.
    """
    from billing.models import Invoice

    contact_to_group = {
        m.billing_contact_id: gid
        for gid, m in maps.items() if m.billing_contact_id
    }
    if not contact_to_group:
        return set()
    paid_contacts = (
        Invoice.objects
        .filter(contact_id__in=contact_to_group.keys(),
                invoice_type=Invoice.InvoiceType.CUSTOMER_INVOICE,
                status__in=[Invoice.Status.PAID, Invoice.Status.PARTIALLY_PAID])
        .values_list('contact_id', flat=True)
        .distinct()
    )
    return {contact_to_group[cid] for cid in paid_contacts if cid in contact_to_group}


def _snapshot_index() -> Dict[Tuple[str, int], AfaMemberSnapshot]:
    return {(s.policy_number, s.dependant_no): s for s in AfaMemberSnapshot.objects.all()}


class BuildOutcome:
    """What a build produced, before anything is persisted."""

    def __init__(self) -> None:
        self.rows: List[str] = []
        self.held: List[engine.HeldRow] = []
        self.new = 0
        self.changed = 0
        self.departures = 0
        self.seen_keys: set = set()
        # key string -> {h: row_hash, g: group, p: policy_id, b: beneficiary_id}
        self.sent: Dict[str, Dict[str, Any]] = {}
        # key strings whose RESIGNATION row is actually in the file
        self.departure_keys: List[str] = []


def _allocate_dependant_no(
    policy_number: str,
    beneficiary_id: Any,
    snapshots: Dict[Tuple[str, int], AfaMemberSnapshot],
    allocated_this_run: Dict[str, set],
) -> int:
    """This dependant's PERSISTED number, or the next free one.

    Graphite has no dependant number — only row order. Deriving one from
    position would renumber the survivors every time a dependant left
    (checklist H20), so the number is looked up by the Graphite beneficiary id.

    `allocated_this_run` is what makes it correct WITHIN a run too: without it
    every dependant of a policy gets the same number on the first run, because
    the database snapshot is empty for all of them at once.
    """
    for (p, d), snap in snapshots.items():
        if p == policy_number and snap.graphite_beneficiary_id == beneficiary_id:
            return d

    used = {d for (p, d) in snapshots.keys() if p == policy_number}
    used |= allocated_this_run.get(policy_number, set())
    nxt = (max(used) + 1) if used else 1
    allocated_this_run.setdefault(policy_number, set()).add(nxt)
    return nxt


def build(run_date: Optional[date] = None) -> Tuple[BuildOutcome, Dict[str, Any]]:
    """Read, fence, render and diff. Persists nothing — see build_and_store."""
    run_date = run_date or timezone.localdate()
    outcome = BuildOutcome()
    mode = _mode()

    lag = reader.replica_lag_seconds()
    data = reader.fetch_membership()
    principals = data['principals']
    dependants_by_policy = data['dependants_by_policy']

    snapshots = _snapshot_index()
    active = [s for s in snapshots.values() if s.state == AfaMemberSnapshot.State.ACTIVE]
    known_active = len(active)
    # Compare LIKE WITH LIKE. `principals` counts policies only, so the read
    # fence must count principal snapshots only (dependant_no 0). Comparing
    # 293 principals against 322 principals-plus-dependants reads as a 9% drop
    # every single run, and false-aborts outright as the dependant count grows.
    known_active_principals = sum(1 for s in active if s.dependant_no == 0)

    # H25 — guards the READ. Runs before anything is rendered.
    engine.check_fence(
        source_rows=len(principals),
        known_members=known_active_principals,
        columns_seen=data['columns_seen'],
        replica_lag=lag,
    )

    maps = _group_maps()
    paid_groups = _paid_group_ids(maps)
    region_default = _region_default()
    allocated: Dict[str, set] = {}

    # Every dependant in the pull, by beneficiary id — so a departure can be
    # rendered from real source data rather than invented.
    deps_by_beneficiary: Dict[Any, Dict[str, Any]] = {
        d['beneficiary_id']: d
        for deps in dependants_by_policy.values() for d in deps
    }

    def _emit(key: Tuple[str, int], rendered: str, prior, meta: Dict[str, Any]) -> None:
        """Record a row and decide whether this run actually sends it."""
        h = engine.row_hash(rendered)
        outcome.seen_keys.add(key)
        outcome.sent[_key(*key)] = {'h': h, **meta}
        is_new = prior is None
        # A member who previously RESIGNED and is back must be re-sent even
        # though their row content is identical to the last time we sent it.
        returning = (prior is not None
                     and prior.state != AfaMemberSnapshot.State.ACTIVE)
        if is_new:
            outcome.new += 1
        elif prior.row_hash != h or returning:
            outcome.changed += 1
        if mode == MODE_FULL or is_new or returning or prior.row_hash != h:
            outcome.rows.append(rendered)

    for member in principals:
        policy_number = member.get('policy_number') or ''
        group_id = member.get('employer_group_id') or ''
        key = (policy_number, 0)

        if not reader.on_cover(member):
            continue                       # departures come from the diff below

        mapping = maps.get(group_id)
        if mapping is None:
            outcome.held.append(engine.HeldRow(
                policy_number, 0,
                'employer group is not mapped to an iMed group name'))
            continue

        if key not in snapshots and group_id not in paid_groups:
            outcome.held.append(engine.HeldRow(
                policy_number, 0,
                'new member — waiting for the employer group invoice to be paid'))
            continue

        values = engine.build_principal_row(
            member,
            imed_group_name=mapping.imed_group_name,
            region_name=mapping.region_name or region_default,
        )
        why = engine.validate_row(values)
        if why:
            outcome.held.append(engine.HeldRow(policy_number, 0, why))
            continue

        _emit(key, engine.render_row(values), snapshots.get(key), {
            'g': group_id, 'p': member.get('policy_id'), 'b': None,
        })

        for dep in dependants_by_policy.get(member.get('policy_id'), []):
            dep_no = _allocate_dependant_no(
                policy_number, dep.get('beneficiary_id'), snapshots, allocated)
            dep_key = (policy_number, dep_no)
            dvalues = engine.build_dependant_row(
                dep, member, dep_no,
                imed_group_name=mapping.imed_group_name,
                region_name=mapping.region_name or region_default,
            )
            dwhy = engine.validate_row(dvalues)
            if dwhy:
                outcome.held.append(engine.HeldRow(policy_number, dep_no, dwhy))
                continue
            _emit(dep_key, engine.render_row(dvalues), snapshots.get(dep_key), {
                'g': group_id, 'p': member.get('policy_id'),
                'b': dep.get('beneficiary_id'),
            })

    # ---- departures -----------------------------------------------------
    principals_by_number = {p.get('policy_number'): p for p in principals}
    reason_default = _default_resignation_reason()
    # AFA force resignation to a month end. Graphite has no status-change date
    # (there is no policy_status_logs table), so the only defensible date is
    # the end of the CURRENT month — never the member's activation month, which
    # would retro-cancel every month since they joined.
    resign_date = engine.end_of_month(run_date)

    for (policy_number, dep_no), snap in snapshots.items():
        if snap.state != AfaMemberSnapshot.State.ACTIVE:
            continue
        if (policy_number, dep_no) in outcome.seen_keys:
            continue

        mapping = maps.get(snap.employer_group_id)
        if mapping is None:
            outcome.held.append(engine.HeldRow(
                policy_number, dep_no,
                'departure — employer group is not mapped to an iMed group name'))
            continue
        if not reason_default:
            outcome.held.append(engine.HeldRow(
                policy_number, dep_no,
                'departure — no AFA reason code has been agreed for a cancellation'))
            continue

        source = principals_by_number.get(policy_number)
        if source is None:
            outcome.held.append(engine.HeldRow(
                policy_number, dep_no,
                'departure — the policy is no longer in Graphite, so no '
                'resignation row can be built'))
            continue

        if dep_no == 0:
            values = engine.build_principal_row(
                source,
                imed_group_name=mapping.imed_group_name,
                region_name=mapping.region_name or region_default,
                resignation=(resign_date, reason_default),
            )
        else:
            # A dependant departure must carry the DEPENDANT's identity, never
            # the principal's. Rendering the principal here would resign the
            # wrong human under the dependant's number.
            dep = deps_by_beneficiary.get(snap.graphite_beneficiary_id)
            if dep is None:
                outcome.held.append(engine.HeldRow(
                    policy_number, dep_no,
                    'departure — the dependant is no longer in Graphite, so no '
                    'resignation row can be built'))
                continue
            values = engine.build_dependant_row(
                dep, source, dep_no,
                imed_group_name=mapping.imed_group_name,
                region_name=mapping.region_name or region_default,
                resignation=(resign_date, reason_default),
            )

        why = engine.validate_row(values)
        if why:
            outcome.held.append(engine.HeldRow(policy_number, dep_no, f'departure — {why}'))
            continue
        outcome.rows.append(engine.render_row(values))
        outcome.departure_keys.append(_key(policy_number, dep_no))
        outcome.departures += 1

    # Guards the DIFF, not the read (see MAX_DEPARTURE_RATIO).
    if (known_active
            and outcome.departures >= MIN_DEPARTURES_BEFORE_FENCE
            and outcome.departures > known_active * MAX_DEPARTURE_RATIO):
        raise engine.LoadFileAborted(
            f'{outcome.departures} of {known_active} members would be resigned in one '
            'run. That is far more than a normal day, so nothing was built — check '
            'whether the source data or the on-cover status rule has changed.'
        )

    meta = {
        'run_date': run_date,
        'source_rows': len(principals),
        'replica_lag': lag,
        'known_active': known_active,
        'mode': mode,
    }
    log.info(
        'afa build %s [%s]: source=%s new=%s changed=%s departures=%s held=%s rows=%s',
        run_date, mode, len(principals), outcome.new, outcome.changed,
        outcome.departures, len(outcome.held), len(outcome.rows),
    )
    return outcome, meta


@transaction.atomic
def build_and_store(run_date: Optional[date] = None) -> AfaLoadFileRun:
    """Build the day's file and record it. One run per date; a run that has not
    been sent is replaced, so re-running before release is safe."""
    run_date = run_date or timezone.localdate()

    existing = AfaLoadFileRun.objects.select_for_update().filter(run_date=run_date).first()
    if existing and existing.status == AfaLoadFileRun.Status.SENT:
        raise ValueError(
            f'The {run_date} file has already been delivered to AFA and will not be rebuilt.'
        )

    try:
        outcome, meta = build(run_date)
    except engine.LoadFileAborted as exc:
        run = existing or AfaLoadFileRun(run_date=run_date)
        run.status = AfaLoadFileRun.Status.ABORTED
        run.abort_reason = str(exc)
        run.row_count = 0
        run.file_body = ''
        run.file_sha256 = ''
        run.sent_keys = {}
        run.departure_keys = []
        run.save()
        log.error('afa build %s ABORTED: %s', run_date, exc)
        return run

    body = engine.render_file(outcome.rows)
    result = engine.BuildResult(
        body=body, rows=len(outcome.rows), new=outcome.new,
        changed=outcome.changed, departures=outcome.departures,
        held=outcome.held, source_rows=meta['source_rows'],
        replica_lag=meta['replica_lag'],
    )

    run = existing or AfaLoadFileRun(run_date=run_date)
    run.status = AfaLoadFileRun.Status.BUILT
    run.abort_reason = ''
    run.row_count = result.rows
    run.new_count = result.new
    run.changed_count = result.changed
    run.departure_count = result.departures
    run.held_count = len(result.held)
    run.held_reasons = result.held_summary()
    run.file_name = f'ADH_{run_date:%Y%m%d}.csv'
    run.file_body = body
    run.file_sha256 = result.sha256
    # Persisted so the snapshot can be committed from the LATER release request
    # (checklist H26 — an instance attribute never survives the round trip).
    run.sent_keys = outcome.sent
    run.departure_keys = outcome.departure_keys
    run.source_row_count = result.source_rows
    run.replica_lag_seconds = result.replica_lag
    run.save()
    return run


class SnapshotNotRecorded(Exception):
    """The run carries no record of what it sent, so the snapshot cannot move."""


@transaction.atomic
def commit_snapshot(run: AfaLoadFileRun) -> None:
    """Record what we just SENT, so tomorrow's diff is against reality.

    Only ever called after AFA have actually taken the file. Reads what to
    commit from the RUN, never from an in-memory attribute — build and release
    are different requests and anything stashed on the instance is long gone
    (checklist H26). If the record is missing it raises rather than quietly
    doing nothing, because silently skipping this makes every subsequent day
    re-send the entire membership.
    """
    if not run.sent_keys and not run.departure_keys:
        raise SnapshotNotRecorded(
            f'The {run.run_date} run has no record of which members were in the file, '
            'so the snapshot was NOT advanced. Rebuild the file before releasing it.'
        )

    stamp = f'{run.run_date:%Y%m%d}'
    now = timezone.now()

    for key, meta in (run.sent_keys or {}).items():
        policy_number, dep_no = _unkey(key)
        snap, _ = AfaMemberSnapshot.objects.get_or_create(
            policy_number=policy_number, dependant_no=dep_no,
            defaults={'first_sent_run': stamp},
        )
        snap.row_hash = meta.get('h') or ''
        snap.state = AfaMemberSnapshot.State.ACTIVE
        snap.employer_group_id = meta.get('g') or snap.employer_group_id
        snap.graphite_policy_id = meta.get('p') or snap.graphite_policy_id
        if meta.get('b') is not None:
            snap.graphite_beneficiary_id = meta['b']
        snap.last_sent_run = stamp
        snap.last_sent_at = now
        snap.save()

    # ONLY the departures whose resignation row was actually in the file. A
    # departure that was HELD never reached AFA — marking it resigned would
    # leave AFA covering someone our snapshot says has left, permanently and
    # invisibly, because the departure loop skips anything already resigned.
    for key in (run.departure_keys or []):
        policy_number, dep_no = _unkey(key)
        AfaMemberSnapshot.objects.filter(
            policy_number=policy_number, dependant_no=dep_no,
        ).update(state=AfaMemberSnapshot.State.RESIGNED,
                 last_sent_run=stamp, last_sent_at=now)

    log.info('afa snapshot committed for %s: %s active, %s resigned',
             run.run_date, len(run.sent_keys or {}), len(run.departure_keys or []))
