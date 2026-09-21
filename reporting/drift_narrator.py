"""
drift_narrator.py — explain WHY a frozen figure has drifted.

Feature #4 ("self-explaining dashboard"). `reporting.frozen_drift.build_frozen_drift`
already DETECTS when a live tile (GWP / PAT / Total Assets / Cash & Bank) has
moved away from its CFO-locked `FrozenFigure`. This module adds the narrative
layer: for each drifting line it finds the largest POSTED journal entries booked
to the line's *related* accounts SINCE the figure was locked (ADIC only), and
asks the in-app AI to explain the movement in two plain-English sentences citing
the JE numbers.

Read-only. Posts nothing. The facts handed to the AI are JE numbers, account
codes and BWP amounts only — no names / PII — and every AI engine is itself
wrapped by core.pii_firewall.firewall on egress, so nothing identifiable can
leave the box even if a future caller widens the fact set.

Honesty note: the account→label lens is a *related-accounts* heuristic, not a
full MA re-derivation, so the UI/AI speak of "entries on related accounts", not
proven causation.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import F

from reporting.frozen_drift import build_frozen_drift, PERIODS, _adic_company

# How many journal entries to surface + feed the AI per drifting line.
TOP_N_ENTRIES = 5


def _accounts_for_label(label: str):
    """Accounts *related* to a frozen line (association, not causation)."""
    from django.db.models import Q
    from ledger.models import Account

    AT = Account.AccountType
    key = (label or '').strip().lower()
    if key == 'gwp':
        qs = Account.objects.filter(
            Q(account_type=AT.REVENUE)
            | Q(fs_line_item__icontains='premium')
            | Q(fs_line_item__icontains='written')
        )
    elif key == 'pat':
        qs = Account.objects.filter(account_type__in=[AT.REVENUE, AT.EXPENSE])
    elif key == 'total assets':
        qs = Account.objects.filter(account_type=AT.ASSET)
    elif key in ('cash & bank', 'cash and bank', 'cash'):
        qs = Account.objects.filter(Q(is_bank_account=True) | Q(fs_line_item__icontains='cash'))
    else:
        return Account.objects.none()
    return qs


def _top_entries_for_drift(label, from_date, to_date, since, company_id):
    """Largest POSTED JE lines on the label's accounts, booked in the period and
    on/after `since` (the figure's lock time), scoped to the given company.
    Ranked in the DB by the line's BWP movement; only the top N are materialised."""
    from ledger.models import JournalEntry, JournalEntryLine

    accts = list(_accounts_for_label(label).values_list('id', flat=True))
    if not accts:
        return []

    lines = (
        JournalEntryLine.objects
        .filter(
            account_id__in=accts,
            journal_entry__status=JournalEntry.Status.POSTED,
            journal_entry__entry_date__gte=from_date,
            journal_entry__entry_date__lte=to_date,
        )
        .exclude(debit_bwp=Decimal('0'), credit_bwp=Decimal('0'))
        .select_related('journal_entry', 'account')
    )
    if company_id is not None:
        lines = lines.filter(journal_entry__company_id=company_id)
    if since is not None:
        lines = lines.filter(journal_entry__posted_date__gte=since)

    # A posted line is either a debit or a credit; debit+credit is its magnitude
    # on the account (no debit−credit sign trap). Order + slice in the DB.
    lines = lines.annotate(movement=F('debit_bwp') + F('credit_bwp')).order_by('-movement')[:TOP_N_ENTRIES]

    out = []
    for ln in lines:
        je = ln.journal_entry
        out.append({
            'entry_number': je.entry_number,
            'entry_date': str(je.entry_date),
            'account_code': ln.account.code,
            'amount_bwp': str((ln.movement or Decimal('0')).quantize(Decimal('0.01'))),
        })
    return out


def _fallback_sentence(label, drift, top_entries):
    """Deterministic explanation, no AI. Never raises."""
    if not top_entries:
        return (
            f"{label} drifted {drift.get('diff_pct')}% (frozen {drift.get('frozen')} → "
            f"live {drift.get('actual')}), but no posted journal entries were found on its "
            f"related accounts since it was locked — the movement is likely from a "
            f"report-mapping or opening-balance change rather than a new entry."
        )
    lead = top_entries[0]
    return (
        f"{label} drifted {drift.get('diff_pct')}% (frozen {drift.get('frozen')} → "
        f"live {drift.get('actual')}); the largest entry on its related accounts since it "
        f"was locked was {lead['entry_number']} on {lead['entry_date']} "
        f"(P {lead['amount_bwp']} on account {lead['account_code']})."
    )


def _narrate(label, drift, top_entries, use_ai=True):
    """2-sentence explanation. Facts are JE numbers / codes / amounts only.
    Degrades to a deterministic sentence if the AI is off or unavailable."""
    if not use_ai or not top_entries:
        return _fallback_sentence(label, drift, top_entries)

    # Deliberately DO NOT feed amounts to the AI — models paraphrase/round money
    # and a CFO tool must never show a wrong figure. The exact amounts are shown
    # verbatim in top_entries beside the narrative; the AI only supplies the prose.
    facts_lines = "; ".join(
        f"{e['entry_number']} on {e['entry_date']} on account {e['account_code']}"
        for e in top_entries
    )
    prompt = (
        "You are a CFO's finance assistant. In at most two plain-English sentences, "
        "explain why a locked headline figure has drifted, citing the specific "
        "journal-entry numbers and account codes below and the drift percentage. "
        "Describe them as entries on related accounts, not proven causation. "
        "CRITICAL: do NOT state, estimate, round, or invent any monetary amount — "
        "the exact figures are listed separately; refer to them only as 'the entries "
        "below'. No preamble, no bullet points, no advice.\n\n"
        f"Frozen {label} drifted {drift.get('diff_pct')}% from its locked value. "
        f"Largest posted entries on related accounts since it was locked: {facts_lines}."
    )
    try:
        from core.ai_assist import reasoning_complete
        out = reasoning_complete(prompt, feature='frozen_drift_narrative')
        if isinstance(out, str) and out.strip():
            return out.strip()
    except Exception:
        pass
    return _fallback_sentence(label, drift, top_entries)


def build_drift_narratives(period: str, company_id=None, use_ai: bool = True, user=None) -> dict:
    """Return the frozen-drift report enriched with a 'why' narrative per drift.

    {
      period, from_date, to_date, company,
      narratives: [ {label, frozen, actual, diff, diff_pct, narrative, top_entries:[...]}, ... ],
      all_ok: bool,
    }
    """
    base = build_frozen_drift(period, company_id=company_id)
    drifts = base.get('drifts') or []

    from_date, to_date = PERIODS.get(period, (None, None))
    adic = _adic_company()
    scope_company_id = adic.id if adic else None  # frozen figures are ADIC-only

    # locked_at per (period, label) — bounds "what changed since lock".
    locked_at_by_label = {}
    if from_date is not None:
        from ledger.models import FrozenFigure
        for ff in FrozenFigure.objects.filter(period=period, is_active=True):
            locked_at_by_label[ff.line_label] = ff.locked_at

    narratives = []
    for d in drifts:
        label = d.get('label')
        top = []
        if from_date is not None:
            top = _top_entries_for_drift(
                label, from_date, to_date,
                locked_at_by_label.get(label), scope_company_id,
            )
        narratives.append({**d, 'narrative': _narrate(label, d, top, use_ai=use_ai), 'top_entries': top})

    return {
        'period': base.get('period'),
        'from_date': base.get('from_date'),
        'to_date': base.get('to_date'),
        'company': base.get('company'),
        'narratives': narratives,
        'all_ok': base.get('all_ok', len(narratives) == 0),
        'reason': base.get('reason'),
    }
