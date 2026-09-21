"""
core/smart_upload/committers.py

Per-section idempotent DB writers. Each committer takes:
  rows:    list[dict] — already mapped to canonical field names by the API view
  company: core.Company — owning legal entity
  user:    auth User — for created_by tracking

Returns a CommitReport(created, updated, skipped, errors).

Rules of the road:
  * Every row is wrapped in a try/except so one bad row doesn't kill the batch.
  * external_ref = "smart_upload:<section>:<company.code>:<natural-key>"
    keeps re-uploads idempotent.
  * Per-company stamping is non-negotiable. ADIC rows do NOT leak into ADSA.
  * GL writes go through JournalEntry.post() so the chain-integrity rules hold.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

log = logging.getLogger(__name__)

ZERO = Decimal('0.00')


@dataclass
class CommitReport:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def as_dict(self):
        return {
            'created':  self.created,
            'updated':  self.updated,
            'skipped':  self.skipped,
            'errors':   self.errors[:50],   # cap to keep payload bounded
            'extra':    self.extra,
        }


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------

def _str(v) -> str:
    if v is None:
        return ''
    return str(v).strip()


def _dec(v) -> Decimal:
    if v is None or v == '':
        return ZERO
    s = str(v).replace(',', '').replace(' ', '').replace('(', '-').replace(')', '')
    try:
        return Decimal(s).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return ZERO


def _int(v):
    if v is None or v == '':
        return None
    s = str(v).replace(',', '').strip()
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _date(v):
    if v is None or v == '':
        return None
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y',
                '%Y/%m/%d', '%d %b %Y', '%d-%b-%Y'):
        try:
            return datetime.strptime(s[:19].split(' ')[0], fmt).date()
        except ValueError:
            continue
    # Last-ditch ISO-like
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def _bool(v, default=True):
    if v is None or v == '':
        return default
    s = str(v).strip().lower()
    return s in ('y', 'yes', 'true', 't', '1', 'active')


def _prefix_code(code: str, company_code: str) -> str:
    """ADIC stays bare, everyone else gets a prefix."""
    code = _str(code)
    if not code:
        return code
    if company_code == 'ADIC':
        return code
    pre = f'{company_code}_'
    return code if code.startswith(pre) else pre + code


# ---------------------------------------------------------------------------
# Period-lock override (CFO directive 2026-05-18)
#
# The CFO locked FY25 + FY26 for ADIC to stop accidental backdated postings.
# He has now authorised smart_upload to override those locks because the
# uploaded data IS the authoritative restatement. Helper:
#   - finds any FiscalPeriod covering the supplied entry dates whose status
#     is LOCKED or CLOSING,
#   - flips it back to OPEN,
#   - records who did it + when + why on lock_reason for audit.
#
# CLOSED periods are NOT touched — that's a separate year-end finalisation
# gate which only the CFO can clear directly via /admin.
# ---------------------------------------------------------------------------

def _override_period_locks(dates: list, user, reason: str, company=None) -> list[str]:
    """
    Open any LOCKED/CLOSING FiscalPeriods that cover the supplied dates.
    Returns the list of period_names that were opened (for the commit report).

    CFO directive 2026-05-20: optional `company` arg scopes the override
    to a single legal entity so a force_unlock on UNI's TB doesn't
    silently re-open ADIC's audit-locked FY25 period at the same date.
    Legacy callers without `company` keep the old global behaviour.
    """
    from ledger.models import FiscalPeriod
    from core.models import AuditLog   # late import to avoid circulars

    if not dates:
        return []

    opened = []
    for d in dates:
        if not d:
            continue
        # locate ANY period (not only OPEN) that covers this date
        qs = FiscalPeriod.objects.filter(
            start_date__lte=d, end_date__gte=d,
        )
        if company is not None:
            qs = qs.filter(company=company)
        period = qs.first()
        if not period:
            continue
        if period.status not in (FiscalPeriod.Status.LOCKED,
                                 FiscalPeriod.Status.CLOSING):
            continue
        # Open it. Track the override on the audit log + lock_reason.
        prior = period.status
        period.status = FiscalPeriod.Status.OPEN
        # Don't clear CFO/FM signatures — the audit trail of who originally
        # locked it stays intact; we just record the override on top.
        override_note = (
            f'[smart_upload override {datetime.now().isoformat(timespec="seconds")} '
            f'by {user.username}] {reason}'
        )
        period.lock_reason = (
            (period.lock_reason or '').rstrip() + '\n' + override_note
        )[:1000]
        period.save(update_fields=['status', 'lock_reason', 'updated_at'])
        try:
            AuditLog.objects.create(
                table_name='FiscalPeriod',
                record_id=str(period.pk),
                action='UPDATE',
                old_values={'status': prior},
                new_values={'status': period.status, 'note': override_note},
                user=user,
                description=f'smart_upload override: opened {period.period_name}',
            )
        except Exception:    # noqa: BLE001  audit-log is best-effort
            log.warning('audit log failed for period override %s', period.period_name)
        opened.append(period.period_name)
    return opened


# ---------------------------------------------------------------------------
# 1. Chart of Accounts
# ---------------------------------------------------------------------------

def commit_coa(
    rows: list[dict], company, user: User, *, mode: str = 'create', **_kw,
) -> CommitReport:
    # CoA replace would cascade into every posting in the system. Block it.
    if mode == 'replace':
        rep = CommitReport()
        rep.errors.append(
            'mode=replace is not supported for Chart of Accounts — replacing '
            'accounts would orphan every existing journal entry line. Use the '
            'CoA upload\'s `archive_unmatched` flag (admin CSV endpoint) '
            'instead.'
        )
        return rep
    return _commit_coa_create(rows, company, user)


def _commit_coa_create(rows: list[dict], company, user: User) -> CommitReport:
    from ledger.models import Account

    rep = CommitReport()
    TYPE_MAP = {
        'asset': 'asset', 'liability': 'liability', 'equity': 'equity',
        'revenue': 'revenue', 'income': 'revenue', 'expense': 'expense',
    }

    for i, r in enumerate(rows):
        try:
            raw_code = _str(r.get('code'))
            name     = _str(r.get('name'))
            if not raw_code or not name:
                rep.skipped += 1
                continue
            code  = _prefix_code(raw_code, company.code)
            atype = TYPE_MAP.get(_str(r.get('account_type')).lower(), 'asset')
            stype = _str(r.get('sub_type')).lower() or 'other_asset'
            ref   = f'smart_upload:coa:{company.code}:{code}'

            obj, was = Account.objects.get_or_create(
                code=code,
                defaults={
                    'name':            name,
                    'account_type':    atype,
                    'sub_type':        stype,
                    'is_bank_account': (stype == 'bank'),
                    'owner_company':   company,
                    'external_ref':    ref,
                },
            )
            if was:
                rep.created += 1
            else:
                changed = False
                if obj.name != name and name:
                    obj.name = name; changed = True
                if obj.owner_company_id != company.id:
                    obj.owner_company = company; changed = True
                if obj.account_type != atype:
                    obj.account_type = atype; changed = True
                if stype and obj.sub_type != stype:
                    obj.sub_type = stype; changed = True
                if changed:
                    obj.save()
                    rep.updated += 1
                else:
                    rep.skipped += 1
        except Exception as e:    # noqa: BLE001
            rep.errors.append(f'row {i+1}: {e}')

    return rep


# ---------------------------------------------------------------------------
# 2. Trial Balance — posts ONE JE summarising the period
# ---------------------------------------------------------------------------

def commit_tb(
    rows: list[dict], company, user: User, *, mode: str = 'create', **_kw,
) -> CommitReport:
    """
    Commit a Trial Balance batch.

    `mode='create'` (default) — historical behaviour: creates one new JE.
    `mode='replace'` — first deletes any prior smart_upload_tb JEs for the
    same (company, entry_date) so re-uploads don't pile up duplicates.
    CFO directive 2026-05-19.
    """
    from ledger.models import (
        Account, FiscalPeriod, FiscalYear,
        JournalEntry, JournalEntryLine,
    )

    rep = CommitReport()
    if not rows:
        return rep

    # CFO directive 2026-05-20: accept period_start + period_end + period_label
    # from the request payload (the upload form's date fields) and let them
    # override whatever the file rows say. Falls back to file values if
    # the request didn't supply them.
    override_start = _date(_kw.get('period_start'))
    override_end   = _date(_kw.get('period_end'))
    override_label = _str(_kw.get('period_label'))

    last_end = override_end
    if not last_end:
        for r in rows:
            d = _date(r.get('period_end'))
            if d:
                last_end = d
    if not last_end:
        rep.errors.append(
            'No usable period_end — supply via the upload form '
            '(Period end field) or include period_end in the file rows.'
        )
        return rep

    # Resolve period_start. CFO directive 2026-05-20 (Manus TB-audit, PR3/5):
    # honour the form's period_start. If absent, fall back to the FiscalYear
    # that contains period_end on this company. If even that's missing,
    # default to a 12-month window ending at period_end so we never silently
    # collapse to "1-month window" (the legacy bug).
    period_start = override_start
    if not period_start:
        for r in rows:
            d = _date(r.get('period_start'))
            if d:
                period_start = d
                break
    fiscal_year = (FiscalYear.objects
                   .filter(company=company,
                           start_date__lte=last_end,
                           end_date__gte=last_end)
                   .first())
    if not period_start and fiscal_year is not None:
        period_start = fiscal_year.start_date
    if not period_start:
        from datetime import timedelta as _td
        # 12-month window heuristic when no schema hint is available.
        period_start = date(last_end.year - 1, last_end.month, 1)

    if period_start > last_end:
        rep.errors.append(
            f'period_start ({period_start}) must be on or before '
            f'period_end ({last_end}).'
        )
        return rep

    period_label = (
        override_label or _str(rows[0].get('period_label'))
        or (fiscal_year.label if fiscal_year else last_end.strftime('%Y-%m'))
    )

    # CFO/Oprah directive 2026-06-03 (ADRG TB root cause). Resolve accounts
    # FIRST, BEFORE the balance check, so rows whose Account row doesn't yet
    # exist in the CoA do NOT silently drop out between the pre-gate balance
    # check and JE-line creation (the old behaviour: pre-gate passed → JE
    # built with rows minus the missing-CoA ones → JournalEntry.post()
    # rejected with "Entry is not balanced in INR" downstream). Any
    # missing-account row is reported up front with the exact codes + their
    # contributions so the user can seed the CoA in one go.
    from ledger.models import Account as _Acct
    missing_accounts: list[dict] = []
    for r in rows:
        raw = _str(r.get('account_code'))
        if not raw:
            continue
        code = _prefix_code(raw, company.code)
        dr_v = _dec(r.get('debit')); cr_v = _dec(r.get('credit'))
        if dr_v == ZERO and cr_v == ZERO:
            continue
        if not _Acct.objects.filter(code=code).exists():
            missing_accounts.append({
                'code':   code,
                'name':   _str(r.get('account_name'))[:80],
                'debit':  str(dr_v),
                'credit': str(cr_v),
            })
    if missing_accounts:
        miss_dr = sum(_dec(m['debit']) for m in missing_accounts)
        miss_cr = sum(_dec(m['credit']) for m in missing_accounts)
        codes_summary = ', '.join(m['code'] for m in missing_accounts[:6])
        more = f' (+{len(missing_accounts) - 6} more)' if len(missing_accounts) > 6 else ''
        rep.errors.append(
            f'TB rejected: {len(missing_accounts)} account code(s) not in '
            f'{company.code} CoA — Dr {miss_dr} / Cr {miss_cr} unposted. '
            f'Missing: {codes_summary}{more}. '
            f'Seed these accounts on the CoA first, then re-upload.'
        )
        rep.extra.update({
            'rejected':         True,
            'reason':           'missing_accounts',
            'missing_accounts': missing_accounts[:200],
            'missing_dr':       str(miss_dr),
            'missing_cr':       str(miss_cr),
        })
        return rep

    total_dr = sum(_dec(r.get('debit')) for r in rows)
    total_cr = sum(_dec(r.get('credit')) for r in rows)
    if abs(total_dr - total_cr) > Decimal('0.05'):
        # CFO/Oprah directive 2026-06-03 (ADRG TB miscommunication): when the
        # upload is rejected for imbalance, leave a forensic trail so the
        # CFO can replay the exact rows omni saw. Without this, debating
        # "your file vs my file" is unwinnable.
        diff = total_dr - total_cr
        dr_rows = [r for r in rows if _dec(r.get('debit')) > ZERO]
        cr_rows = [r for r in rows if _dec(r.get('credit')) > ZERO]
        rep.errors.append(
            f'TB unbalanced: Dr {total_dr} vs Cr {total_cr} '
            f'(diff {diff}, {len(rows)} rows: {len(dr_rows)} debit, {len(cr_rows)} credit). '
            f'See AuditLog "SmartUploadTB:rejected" for full row dump.'
        )
        rep.extra.update({
            'rejected':       True,
            'total_dr':       str(total_dr),
            'total_cr':       str(total_cr),
            'diff':           str(diff),
            'row_count':      len(rows),
            'debit_rows':     len(dr_rows),
            'credit_rows':    len(cr_rows),
        })
        # Persist the actual rows omni parsed (capped at 500) so a future
        # forensic check can answer "what did Charmaine's upload contain?"
        try:
            from core.models import AuditLog as _AL
            import json as _json
            sample = []
            for r in rows[:500]:
                sample.append({
                    'account_code': str(r.get('account_code') or ''),
                    'account_name': str(r.get('account_name') or '')[:60],
                    'debit':        str(_dec(r.get('debit'))),
                    'credit':       str(_dec(r.get('credit'))),
                    'period_end':   str(r.get('period_end') or ''),
                })
            _AL.objects.create(
                table_name='SmartUploadTB',
                record_id=f'{getattr(company, "code", "?")}_{last_end.isoformat() if last_end else "noperiod"}',
                action=_AL.Action.UPDATE,
                new_values={
                    'event':       'tb_rejected_unbalanced',
                    'company':     getattr(company, 'code', None),
                    'period_end':  last_end.isoformat() if last_end else None,
                    'total_dr':    str(total_dr),
                    'total_cr':    str(total_cr),
                    'diff':        str(diff),
                    'row_count':   len(rows),
                    'sample_rows': sample,
                },
                user=user,
                description=(
                    f'TB upload rejected for {getattr(company, "code", "?")} — '
                    f'Dr {total_dr} vs Cr {total_cr} diff {diff} ({len(rows)} rows)'
                ),
            )
        except Exception:    # noqa: BLE001 — diagnostic write must not raise
            pass
        return rep

    # Ensure every month from period_start → period_end exists as a
    # per-company FiscalPeriod, linked to the parent FiscalYear. Falls
    # back to a single closing-month row if FiscalYear back-link is absent
    # (legacy companies without seed_fiscal_years applied).
    cursor = date(period_start.year, period_start.month, 1)
    while cursor <= last_end:
        # Last day of cursor's month.
        if cursor.month == 12:
            month_end = date(cursor.year, 12, 31)
        else:
            month_end = date(cursor.year, cursor.month + 1, 1) - __import__('datetime').timedelta(days=1)
        # Don't extend past the period_end.
        month_end = min(month_end, last_end)
        FiscalPeriod.objects.get_or_create(
            company=company, period_name=cursor.strftime('%Y-%m'),
            defaults={
                'start_date':  cursor,
                'end_date':    month_end,
                'status':      FiscalPeriod.Status.OPEN,
                'fiscal_year': fiscal_year,
            },
        )
        # Advance to the first of next month.
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)

    # CFO directive 2026-05-20: amendments to a TB whose period is
    # locked must require explicit unlock + confirmation — no more
    # silent override on every upload. Two-stage:
    #   1. If the period is LOCKED/CLOSING and the caller didn't pass
    #      `force_unlock=true`, refuse with a `period_locked` flag so the
    #      UI can prompt for confirmation.
    #   2. If `force_unlock=true` was supplied, run the override helper
    #      and proceed.
    force_unlock = str(_kw.get('force_unlock', '')).lower() in ('1', 'true', 'yes')
    # CFO directive 2026-05-20: lock check MUST be company-scoped. Previously
    # the query swept every FiscalPeriod overlapping `last_end`, so an
    # ADIC-specific FY25 lock (or a legacy company=NULL system-wide lock)
    # blocked Unicoin / RSA / VCM / ADSA TB uploads for the same calendar
    # date. Now only locks owned by THIS company gate the commit; NULL-
    # company legacy rows are ignored. ADIC's frozen FY25/FY26 figures
    # remain protected by ledger/locks.py (LOCK_COMPANY_CODE = 'ADIC')
    # which fires inside JournalEntry.save() with the override-password gate.
    locked_periods = FiscalPeriod.objects.filter(
        company=company,
        start_date__lte=last_end, end_date__gte=last_end,
        status__in=(FiscalPeriod.Status.LOCKED, FiscalPeriod.Status.CLOSING),
    ).values_list('period_name', flat=True)
    if locked_periods and not force_unlock:
        rep.errors.append(
            f'Period {list(locked_periods)} is locked. '
            f'Go to Period Management (/periods) to clear your lock signature, '
            f'then re-commit this TB.'
        )
        rep.extra['period_locked'] = True
        rep.extra['locked_periods'] = list(locked_periods)
        rep.extra['period_management_url'] = '/periods'
        return rep

    if force_unlock:
        overridden = _override_period_locks(
            [last_end], user,
            reason=f'TB amendment for {company.code} {period_label}',
            company=company,
        )
        if overridden:
            rep.extra['period_overrides'] = overridden

    accounts: dict[str, 'Account'] = {}
    for r in rows:
        raw = _str(r.get('account_code'))
        if not raw:
            continue
        code = _prefix_code(raw, company.code)
        if code not in accounts:
            acct = Account.objects.filter(code=code).first()
            if not acct:
                rep.errors.append(f'Account {code} not in CoA; row skipped.')
                continue
            accounts[code] = acct

    with transaction.atomic():
        if mode == 'replace':
            prior = JournalEntry.objects.filter(
                source_type='smart_upload_tb',
                company=company,
                entry_date=last_end,
            )
            replaced_ids = list(prior.values_list('entry_number', flat=True))
            if replaced_ids:
                # Cascades to JournalEntryLine via FK on_delete=CASCADE
                prior.delete()
                rep.extra['replaced_je_numbers'] = replaced_ids
                rep.extra['replaced_count'] = len(replaced_ids)

        je = JournalEntry.objects.create(
            entry_date=last_end,
            # PR3/5 (CFO directive 2026-05-20): record the full period this
            # TB JE represents so the reporting layer can place opening /
            # period / closing buckets correctly without inferring from a
            # calendar-month FiscalPeriod.
            period_start=period_start,
            description=f'TB import — {period_label}',
            source_type='smart_upload_tb',
            journal_type=JournalEntry.JournalType.GENERAL,
            status=JournalEntry.Status.DRAFT,
            company=company,
            currency_code_id=(company.base_currency_id or 'BWP'),
            created_by=user,
            is_related_party=False,
        )
        for i, r in enumerate(rows):
            raw = _str(r.get('account_code'))
            code = _prefix_code(raw, company.code)
            acct = accounts.get(code)
            if not acct:
                continue
            dr = _dec(r.get('debit'))
            cr = _dec(r.get('credit'))
            if dr == ZERO and cr == ZERO:
                continue
            if getattr(acct, 'is_summary_only', False):
                continue
            JournalEntryLine.objects.create(
                journal_entry=je,
                account=acct,
                description=f'TB {period_label}: {_str(r.get("account_name")) or acct.name}',
                debit_amount=dr, credit_amount=cr,
                debit_bwp=dr,    credit_bwp=cr,
            )
        try:
            je.post(user=user, _allow_direct=True)
            rep.created = 1
            rep.extra['journal_entry'] = je.entry_number
            rep.extra['lines'] = je.lines.count()
            rep.extra['period_start'] = period_start.isoformat()
            rep.extra['period_end'] = last_end.isoformat()
            rep.extra['period_label'] = period_label
            if fiscal_year is not None:
                rep.extra['fiscal_year'] = fiscal_year.label
        except Exception as e:    # noqa: BLE001
            rep.errors.append(f'JE.post failed: {e}')
            raise

    # Deferred auto-lock — 5-day grace (CFO directive 2026-06-09).
    # Previously: after CFO 2026-05-20 we hard-locked the period immediately
    # on TB import, but with no dual-sign trail (status=LOCKED, cfo_sig=NULL,
    # fm_sig=NULL). That was BUG-3 — users couldn't post JEs to e.g. ADIC
    # 2026-06 with no audit trail of who locked it.
    # Now: leave status untouched and schedule auto_lock_at = now + 5 days.
    # During the grace window the period stays effectively OPEN. After the
    # 5 days elapse, `FiscalPeriod.is_locked` returns True (via the property)
    # and JE posting is gated as if the period were status=LOCKED. To unlock
    # again, an admin can null `auto_lock_at` (or extend it).
    try:
        from datetime import timedelta
        from django.utils import timezone as _tz
        fp = FiscalPeriod.objects.filter(
            company=company,
            start_date__lte=last_end, end_date__gte=last_end,
        ).first()
        if fp and not fp.has_full_lock_signoff:
            new_lock_at = _tz.now() + timedelta(days=5)
            # If a later auto_lock_at is already pending, keep the earlier one
            # (e.g. two TB imports same week — first to schedule wins).
            if fp.auto_lock_at is None or new_lock_at < fp.auto_lock_at:
                fp.auto_lock_at = new_lock_at
            fp.lock_reason = (
                (fp.lock_reason or '').rstrip()
                + f'\n[TB import {period_label} by {user.username} — '
                  f'auto-locks {fp.auto_lock_at:%Y-%m-%d %H:%M}]'
            )[:1000]
            fp.save(update_fields=['auto_lock_at', 'lock_reason', 'updated_at'])
            rep.extra['period_auto_lock_scheduled'] = (
                f'{fp.period_name} -> {fp.auto_lock_at.isoformat()}'
            )
    except Exception:    # noqa: BLE001  best-effort; commit already succeeded
        pass

    return rep


# ---------------------------------------------------------------------------
# 3. General Ledger — one JE per entry_ref
# ---------------------------------------------------------------------------

def commit_gl(
    rows: list[dict], company, user: User, *, mode: str = 'create', **_kw,
) -> CommitReport:
    """
    Commit a General Ledger batch.

    `mode='replace'` (CFO directive 2026-05-19) — first deletes every
    `source_type='smart_upload_gl'` JE for this company whose entry_date
    falls within the date range of the supplied rows. Scoped to the
    rows' date span so a partial re-upload doesn't nuke unrelated months.
    """
    from ledger.models import Account, FiscalPeriod, JournalEntry, JournalEntryLine

    rep = CommitReport()
    if not rows:
        return rep

    if mode == 'replace':
        row_dates = [d for d in (_date(r.get('entry_date')) for r in rows) if d]
        if row_dates:
            d_min, d_max = min(row_dates), max(row_dates)
            prior = JournalEntry.objects.filter(
                source_type='smart_upload_gl',
                company=company,
                entry_date__gte=d_min,
                entry_date__lte=d_max,
            )
            replaced = list(prior.values_list('entry_number', flat=True))
            if replaced:
                prior.delete()
                rep.extra['replaced_je_numbers'] = replaced
                rep.extra['replaced_count'] = len(replaced)
                rep.extra['replaced_date_range'] = [d_min.isoformat(), d_max.isoformat()]

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        ref = _str(r.get('entry_ref'))
        if not ref:
            continue
        groups[ref].append(r)

    if not groups:
        rep.errors.append('No entry_ref column populated; cannot group GL lines.')
        return rep

    # Pre-validate balance per group
    for ref, ls in groups.items():
        td = sum(_dec(l.get('debit')) for l in ls)
        tc = sum(_dec(l.get('credit')) for l in ls)
        if abs(td - tc) > Decimal('0.05'):
            rep.errors.append(f'entry_ref {ref} unbalanced ({td} vs {tc}); will skip.')

    # CFO directive 2026-05-18: override any FY25/26 ADIC period lock that
    # would otherwise block this batch. We pre-open all the periods covered
    # by the supplied GL date range so JE.post()'s _validate_for_posting()
    # finds an OPEN period for every line.
    all_dates = sorted({_date(r.get('entry_date')) for r in rows
                        if _date(r.get('entry_date'))})
    overridden = _override_period_locks(
        all_dates, user, reason=f'GL import for {company.code}',
    )
    if overridden:
        rep.extra['period_overrides'] = overridden

    # Resolve all accounts up front
    needed = {_prefix_code(_str(r.get('account_code')), company.code)
              for r in rows if _str(r.get('account_code'))}
    accounts = {a.code: a for a in
                __import__('ledger.models', fromlist=['Account']).Account.objects.filter(code__in=needed)}
    missing = needed - set(accounts)
    for m in sorted(missing):
        rep.errors.append(f'Account {m} not in CoA — rows referencing it will fail.')

    for ref, ls in groups.items():
        try:
            td = sum(_dec(l.get('debit')) for l in ls)
            tc = sum(_dec(l.get('credit')) for l in ls)
            if abs(td - tc) > Decimal('0.05'):
                rep.skipped += 1
                continue

            head = ls[0]
            entry_date = _date(head.get('entry_date'))
            if not entry_date:
                rep.errors.append(f'entry_ref {ref}: missing entry_date')
                rep.skipped += 1
                continue

            # Company-scoped (bug fix 2026-06-08, "get() returned 14"):
            # period_name like '2025-07' is NOT unique on its own — it exists
            # once per company plus a legacy company=NULL row. An unscoped
            # get_or_create runs .get(period_name=...) and matches every
            # company's copy (~14), raising MultipleObjectsReturned on every
            # GL entry. The (company, period_name) unique constraint makes the
            # scoped lookup resolve to exactly one row. Matches the TB
            # committer above and JournalEntry.post()'s company-scoped period
            # resolution.
            _m_end = (date(entry_date.year, entry_date.month + 1, 1)
                      - timedelta(days=1)) if entry_date.month < 12 \
                else date(entry_date.year, 12, 31)
            FiscalPeriod.objects.get_or_create(
                company=company,
                period_name=entry_date.strftime('%Y-%m'),
                defaults={
                    'start_date': date(entry_date.year, entry_date.month, 1),
                    'end_date':   _m_end,
                    'status':     FiscalPeriod.Status.OPEN,
                },
            )

            with transaction.atomic():
                je = JournalEntry.objects.create(
                    entry_date=entry_date,
                    description=(_str(head.get('description'))
                                 or f'GL {ref}')[:200],
                    source_type='smart_upload_gl',
                    journal_type=JournalEntry.JournalType.GENERAL,
                    status=JournalEntry.Status.DRAFT,
                    company=company,
                    currency_code_id=(company.base_currency_id or 'BWP'),
                    created_by=user,
                    is_related_party=False,
                )
                lines_sorted = sorted(
                    ls, key=lambda x: _int(x.get('line_no')) or 0,
                )
                for l in lines_sorted:
                    code = _prefix_code(_str(l.get('account_code')), company.code)
                    acct = accounts.get(code)
                    if not acct:
                        continue
                    if getattr(acct, 'is_summary_only', False):
                        continue
                    dr = _dec(l.get('debit'))
                    cr = _dec(l.get('credit'))
                    if dr == ZERO and cr == ZERO:
                        continue
                    JournalEntryLine.objects.create(
                        journal_entry=je,
                        account=acct,
                        description=(_str(l.get('memo'))
                                     or _str(l.get('description'))
                                     or acct.name)[:200],
                        debit_amount=dr, credit_amount=cr,
                        debit_bwp=dr,    credit_bwp=cr,
                    )
                je.post(user=user, _allow_direct=True)
                rep.created += 1
        except Exception as e:   # noqa: BLE001
            rep.errors.append(f'entry_ref {ref}: {e}')
            rep.skipped += 1

    return rep


# ---------------------------------------------------------------------------
# 4. Vendors  5. Customers
# ---------------------------------------------------------------------------

def _commit_contacts(rows, company, user, kind: str, *, mode: str = 'create') -> CommitReport:
    from billing.models import Contact

    rep = CommitReport()
    if mode == 'replace':
        ref_prefix = f'smart_upload:{kind}:{company.code}:'
        prior = Contact.objects.filter(
            external_ref__startswith=ref_prefix, company=company,
        )
        replaced = list(prior.values_list('external_ref', flat=True))
        if replaced:
            prior.delete()
            rep.extra['replaced_refs'] = replaced
            rep.extra['replaced_count'] = len(replaced)
    for i, r in enumerate(rows):
        try:
            name = _str(r.get('name'))
            if not name:
                rep.skipped += 1
                continue
            natural_key = _str(r.get('code')) or name.lower()
            ref = f'smart_upload:{kind}:{company.code}:{natural_key}'
            obj, was = Contact.objects.get_or_create(
                external_ref=ref,
                defaults={
                    'contact_type':       kind,
                    'name':               name,
                    'tax_id':             _str(r.get('tax_id')) or None,
                    'email':              _str(r.get('email')) or None,
                    'phone':              _str(r.get('phone')) or None,
                    'currency_code_id':   (company.base_currency_id or 'BWP'),
                    'is_active':          _bool(r.get('is_active'), True),
                    'payment_terms_days': _int(r.get('payment_terms')) or 30,
                    'company':            company,
                },
            )
            if was:
                rep.created += 1
            else:
                changed = False
                if obj.name != name:
                    obj.name = name; changed = True
                if obj.company_id != company.id:
                    obj.company = company; changed = True
                if obj.contact_type != kind:
                    obj.contact_type = kind; changed = True
                if changed:
                    obj.save()
                    rep.updated += 1
                else:
                    rep.skipped += 1
        except Exception as e:    # noqa: BLE001
            rep.errors.append(f'row {i+1}: {e}')
    return rep


def commit_vendors(rows, company, user, *, mode: str = 'create', **_kw) -> CommitReport:
    return _commit_contacts(rows, company, user, kind='vendor', mode=mode)


def commit_customers(rows, company, user, *, mode: str = 'create', **_kw) -> CommitReport:
    return _commit_contacts(rows, company, user, kind='customer', mode=mode)


# ---------------------------------------------------------------------------
# 6. PP&E
# ---------------------------------------------------------------------------

def commit_ppe(rows, company, user, *, mode: str = 'create', **_kw) -> CommitReport:
    from assets.models import Asset, AssetCategory
    from ledger.models import Account

    rep = CommitReport()

    if mode == 'replace':
        ref_prefix = f'smart_upload:ppe:{company.code}:'
        prior = Asset.objects.filter(
            external_ref__startswith=ref_prefix, company=company,
        )
        replaced = list(prior.values_list('tag_number', flat=True))
        if replaced:
            prior.delete()
            rep.extra['replaced_tags'] = replaced
            rep.extra['replaced_count'] = len(replaced)

    # Pick a default category if none supplied; assets.models requires one.
    default_cat = AssetCategory.objects.filter(is_active=True).order_by('code').first()
    if not default_cat:
        rep.errors.append(
            'No AssetCategory rows exist. Seed asset categories before importing PP&E.'
        )
        return rep

    for i, r in enumerate(rows):
        try:
            tag  = _str(r.get('tag_number'))
            name = _str(r.get('name'))
            if not tag or not name:
                rep.skipped += 1
                continue
            ref = f'smart_upload:ppe:{company.code}:{tag}'

            # Skip accumulated-dep rows (gl ends in .1)
            gl_code = _str(r.get('gl_account'))
            if gl_code.endswith('.1'):
                rep.skipped += 1
                continue

            obj, was = Asset.objects.get_or_create(
                external_ref=ref,
                defaults={
                    'tag_number':        tag,
                    'name':              name,
                    'company':           company,
                    'category':          default_cat,
                    'cost':              _dec(r.get('cost')),
                    'salvage_value':     _dec(r.get('salvage_value')),
                    'opening_accumulated_depreciation':
                                         _dec(r.get('opening_accumulated_depreciation')),
                    'purchase_date':     _date(r.get('purchase_date')) or date(2024, 7, 1),
                    'in_service_date':   _date(r.get('in_service_date')) or
                                         _date(r.get('purchase_date')) or date(2024, 7, 1),
                    'useful_life_months': _int(r.get('useful_life_months')) or 60,
                    'method':            _str(r.get('method')).lower() or 'straight_line',
                    'location':          _str(r.get('location')),
                    'custodian':         _str(r.get('custodian')),
                    'status':            'in_service',
                    'created_by':        user,
                },
            )
            if was:
                rep.created += 1
            else:
                rep.skipped += 1
        except Exception as e:   # noqa: BLE001
            rep.errors.append(f'row {i+1}: {e}')

    return rep


# ---------------------------------------------------------------------------
# 7. Bank Accounts
# ---------------------------------------------------------------------------

def commit_bank_accounts(rows, company, user, *, mode: str = 'create', **_kw) -> CommitReport:
    from banking.models import BankAccount
    from ledger.models import Account

    rep = CommitReport()

    if mode == 'replace':
        # BankAccount has no direct company FK — scope via the linked GL account.
        prior = BankAccount.objects.filter(gl_account__owner_company=company)
        replaced = list(prior.values_list('account_number', flat=True))
        if replaced:
            prior.delete()
            rep.extra['replaced_account_numbers'] = replaced
            rep.extra['replaced_count'] = len(replaced)

    for i, r in enumerate(rows):
        try:
            name = _str(r.get('account_name'))
            gl_code = _prefix_code(_str(r.get('gl_account')), company.code)
            if not name or not gl_code:
                rep.skipped += 1
                continue
            gl = Account.objects.filter(code=gl_code).first()
            if not gl:
                rep.errors.append(f'row {i+1}: GL {gl_code} not found.')
                continue
            if not gl.is_bank_account:
                gl.is_bank_account = True
                gl.save(update_fields=['is_bank_account'])

            obj, was = BankAccount.objects.get_or_create(
                gl_account=gl,
                defaults={
                    'bank_name':       _str(r.get('bank_name')),
                    'account_name':    name,
                    'account_number':  _str(r.get('account_number')) or '0',
                    'branch_code':     _str(r.get('branch_code')) or None,
                    'currency_code_id':_str(r.get('currency_code'))
                                       or (company.base_currency_id or 'BWP'),
                    'is_active':       _bool(r.get('is_active'), True),
                },
            )
            if was:
                rep.created += 1
            else:
                changed = False
                if obj.account_name != name and name:
                    obj.account_name = name; changed = True
                if changed:
                    obj.save()
                    rep.updated += 1
                else:
                    rep.skipped += 1
        except Exception as e:    # noqa: BLE001
            rep.errors.append(f'row {i+1}: {e}')

    return rep


# ---------------------------------------------------------------------------
# 8. Employees
# ---------------------------------------------------------------------------

def commit_employees(rows, company, user, *, mode: str = 'create', **_kw) -> CommitReport:
    from payroll.models import Employee

    rep = CommitReport()

    if mode == 'replace':
        ref_prefix = f'smart_upload:employees:{company.code}:'
        prior = Employee.objects.filter(
            external_ref__startswith=ref_prefix, company=company,
        )
        replaced = list(prior.values_list('employee_number', flat=True))
        if replaced:
            prior.delete()
            rep.extra['replaced_employee_numbers'] = replaced
            rep.extra['replaced_count'] = len(replaced)

    for i, r in enumerate(rows):
        try:
            code   = _str(r.get('employee_code'))
            first  = _str(r.get('first_name'))
            last   = _str(r.get('last_name'))
            if not code or not (first or last):
                rep.skipped += 1
                continue
            full = (first + ' ' + last).strip()
            ref  = f'smart_upload:employees:{company.code}:{code}'
            obj, was = Employee.objects.get_or_create(
                external_ref=ref,
                defaults={
                    'employee_number': code,
                    'full_name':       full,
                    'job_title':       _str(r.get('job_title')),
                    'department':      _str(r.get('department')),
                    'email':           _str(r.get('email')),
                    'phone':           _str(r.get('phone')),
                    'national_id':     _str(r.get('national_id')),
                    'hire_date':       _date(r.get('hire_date')),
                    'company':         company,
                    'status':          ('active' if _bool(r.get('is_active'), True)
                                        else 'terminated'),
                },
            )
            if was:
                # A staff record with no HRIS row is invisible to self-service:
                # the person cannot apply for leave, open a payslip or see their
                # own profile, and is told their record is "not linked" when it
                # is (2026-08-08). Best-effort — an upload must not fail on it.
                try:
                    from hris.models import HRISProfile
                    HRISProfile.objects.get_or_create(employee=obj)
                except Exception as exc:                    # noqa: BLE001
                    # Still best-effort, but say so. Swallowing this silently
                    # recreates the very fault it was written to prevent.
                    log.warning('HRIS row not created for employee %s: %s',
                                getattr(obj, 'pk', '?'), exc)
                rep.created += 1
            else:
                changed = False
                if obj.full_name != full and full:
                    obj.full_name = full; changed = True
                if obj.company_id != company.id:
                    obj.company = company; changed = True
                if changed:
                    obj.save()
                    rep.updated += 1
                else:
                    rep.skipped += 1
        except Exception as e:    # noqa: BLE001
            rep.errors.append(f'row {i+1}: {e}')

    return rep


# ---------------------------------------------------------------------------
# 9. Payroll
# ---------------------------------------------------------------------------

# Canonical payroll field (sections.py) -> PayslipComponent code
# (payroll/management/commands/setup_payroll_components.py). Totals — gross,
# total_deductions, net, ctc — are deliberately ABSENT: they are recomputed
# from the lines below and only cross-checked against the file.
PAYROLL_FIELD_TO_COMPONENT = {
    'basic':                  'BASIC',
    'commission':             'COMMISSION',
    'incentive':              'INCENTIVE',
    'bonus':                  'BONUS',
    'po_allowance':           'PO_ALLOWANCE',
    'allowance':              'ALLOWANCE',
    'housing_allowance':      'HOUSING_ALLOWANCE',
    'leave_pay':              'LEAVE_PAY',
    'medical_aid_allowance':  'MEDICAL_AID_ALLOWANCE',
    'vehicle_allowance':      'VEHICLE_ALLOWANCE',
    'health_ins_allowance':   'HEALTH_INS_ALLOWANCE',
    'fuel_allowance':         'FUEL_ALLOWANCE',
    'mobile_allowance':       'MOBILE_ALLOWANCE',
    'internet_allowance':     'INTERNET_ALLOWANCE',
    'sales_allowance':        'SALES_ALLOWANCE',
    'severance':              'SEVERANCE',
    'non_cash_benefit':       'NON_CASH_BENEFIT',
    'paye':                   'PAYE',
    'loans_deduction':        'LOANS_DEDUCTION',
    'housing_tax':            'HOUSING_TAX',
    'medical_aid_ee':         'MEDICAL_AID_EE',
    'pension_ee':             'PENSION_EE',
    'provident_ee':           'PROVIDENT_EE',
    'medical_aid_er':         'MEDICAL_AID_ER',
    'pension_er':             'PENSION_ER',
    'provident_er':           'PROVIDENT_ER',
}


def _payroll_name_key(s) -> str:
    """casefold + collapse whitespace, so 'John Doe' / 'JOHN  DOE' are one
    employee. Same rule as payroll.importer._name_key."""
    return re.sub(r'\s+', ' ', _str(s)).casefold()


# A spreadsheet total row is not a person. Reused shape from
# payroll.importer._TOTAL_ROW_RE — ingesting "TOTAL (72 payslips)" as an
# employee DOUBLES the register (CFO directive 2026-06-25).
_PAYROLL_TOTAL_ROW_RE = re.compile(
    r'^(grand\s+total|sub\s*-?\s*totals?|totals?|sum)'
    r'\s*($|[\(\):,;#\d-].*|(for|of)\b.*|payslips?\b.*)',
    re.IGNORECASE,
)


def _is_placeholder_code(v) -> bool:
    """The July 2026 ADIC register carries literal "nill" staff numbers on five
    rows pending an HR backfill. Treat those as absent, not as a real code."""
    return _str(v).casefold() in ('', 'nil', 'nill', 'none', 'n/a', 'na', '-', '0', 'tbc', 'pending')


def commit_payroll(rows, company, user, *, mode: str = 'create',
                   period_start: str = '', period_end: str = '',
                   period_label: str = '', **_kw) -> CommitReport:
    """
    Imports a payroll register per employee per period.

    Creates PayrollPeriod (if missing) + Payslip, AND one PayslipLine per
    mapped component (Basic, Commission, Incentive, every allowance, each
    deduction, PAYE, each company contribution).

    Bug report Pako Kago 2026-07-29 (ADIC July 2026), both halves:
      * 24 of 30 columns were unmapped — fixed in sections.py + mapper.py.
      * Commission (87,774.22) and Incentive (30,300.00) never showed on the
        Payroll dashboard. Root cause was HERE: this committer wrote only the
        gross/paye/net headline and explicitly created NO PayslipLine rows,
        but the dashboard reads the component lines. So the answer to "(a)
        mapping, (b) aggregation, or (c) both" is (c) — and it was never an
        aggregation bug: there was simply nothing stored to aggregate.

    Totals are recomputed from the lines by Payslip.recompute_totals() (the
    sign-robust routine — a stored deduction must never inflate gross). The
    file's own GROSS / TOTAL DED / NET PAY / CTC columns are kept as a
    CROSS-CHECK only; any employee whose recomputed gross or net differs from
    the register by more than one thebe is listed in report.extra['variances']
    so Finance can tie out before the period is posted.

    PAYE: the register's PAYE column is stored as-given. We do NOT re-derive it
    from the BURS brackets on import, because the register is the already-run
    payroll — re-deriving would silently disagree with what staff were paid.

    Employee matching: staff number first, then normalised full name. A blank
    or "nill" staff number does NOT reject the row (HR backfills later).

    `mode='replace'` (CFO directive 2026-05-19) — first deletes every Payslip
    for this company in the periods present in the upload, so a re-upload
    doesn't pile up duplicate slips per (employee, period).
    """
    from payroll.models import Employee, PayrollPeriod, Payslip, PayslipComponent, PayslipLine

    rep = CommitReport()

    # ── Period: the upload form wins over the file (CFO directive 2026-05-20).
    # Previously these kwargs were swallowed by **_kw and silently ignored, so
    # a register whose "Period" column read "FY27" created ONE PayrollPeriod
    # named FY27 — collapsing all twelve months of the year into a single
    # period, where Payslip's unique (employee, period) makes every later
    # month look like a duplicate. Derive a real monthly period instead.
    form_end   = _date(period_end)
    form_start = _date(period_start)
    form_label = _str(period_label)

    components = {c.code: c for c in PayslipComponent.objects.filter(is_active=True)}
    missing_components = sorted(
        {code for code in PAYROLL_FIELD_TO_COMPONENT.values() if code not in components}
    )
    if missing_components:
        rep.errors.append(
            'payslip components not seeded: ' + ', '.join(missing_components) +
            ' — run `manage.py setup_payroll_components` first.'
        )
        return rep

    def _resolve_period(row):
        """Returns (PayrollPeriod, created). Monthly, named YYYY-MM."""
        pend = form_end or _date(row.get('period_end')) or timezone.localdate()
        # A label like "FY27" names a YEAR, not a payroll month — never let it
        # become the period name. Only accept a label that looks like a period.
        label = form_label or _str(row.get('period_label'))
        pname = label[:10] if re.match(r'^\d{4}[-/]\d{1,2}$', label) else pend.strftime('%Y-%m')
        # A payroll period is a MONTH. Honour the form's start date only if it
        # is a real month start that precedes the end date; otherwise derive it.
        # The reported default sent start AND end both = 31/07/2026, which
        # would have stored a period one day long.
        pstart = date(pend.year, pend.month, 1)
        if form_start and form_start < pend and form_start.day == 1:
            pstart = form_start
        return PayrollPeriod.objects.get_or_create(
            period_name=pname,
            defaults={'start_date': pstart, 'end_date': pend},
        )

    if mode == 'replace':
        labels = set()
        for r in rows:
            period, _created = _resolve_period(r)
            labels.add(period.period_name)
        if labels:
            prior = Payslip.objects.filter(
                company=company, period__period_name__in=sorted(labels),
            )
            replaced = prior.count()
            if replaced:
                prior.delete()
                rep.extra['replaced_periods'] = sorted(labels)
                rep.extra['replaced_count'] = replaced

    variances = []
    unmatched = []

    for i, r in enumerate(rows):
        try:
            emp_code = _str(r.get('employee_code'))
            emp_name = _str(r.get('employee_name'))

            if _PAYROLL_TOTAL_ROW_RE.match(emp_name):
                rep.skipped += 1                 # spreadsheet total row
                continue
            if _is_placeholder_code(emp_code):
                emp_code = ''
            if not emp_code and not emp_name:
                rep.skipped += 1                 # blank filler row
                continue

            emp = None
            if emp_code:
                emp = Employee.objects.filter(
                    employee_number=emp_code, company=company,
                ).first()
            if emp is None and emp_name:
                key = _payroll_name_key(emp_name)
                for cand in Employee.objects.filter(company=company).only(
                        'id', 'full_name'):
                    if _payroll_name_key(cand.full_name) == key:
                        emp = cand
                        break
            if emp is None:
                who = emp_name or emp_code
                unmatched.append(who)
                rep.errors.append(
                    f'row {i+1}: {who!r} is not an employee of {company.code} '
                    f'— add them in HRIS (or fix the spelling) and re-upload.'
                )
                continue

            period, _created = _resolve_period(r)

            ps, was = Payslip.objects.get_or_create(
                employee=emp, period=period,
                defaults={'company': company},
            )
            if ps.company_id is None:
                ps.company = company

            # Replace this slip's lines for the components present in the
            # upload. Components absent from the file are left untouched, so a
            # correction file carrying only Commission can't wipe Basic.
            for field, code in PAYROLL_FIELD_TO_COMPONENT.items():
                if field not in r:
                    continue
                amount = _dec(r.get(field))
                comp = components[code]
                if amount == ZERO:
                    # Zero means "no such earning this month" — drop any stale
                    # line rather than parking a 0.00 row on the payslip.
                    PayslipLine.objects.filter(payslip=ps, component=comp).delete()
                    continue
                PayslipLine.objects.update_or_create(
                    payslip=ps, component=comp,
                    defaults={'amount': amount, 'notes': 'Smart Upload register'},
                )

            # The register is the already-run payroll — keep its PAYE.
            ps.recompute_totals(recompute_paye=False)
            ps.save()

            # Cross-check against the file's own totals.
            file_gross = _dec(r.get('gross'))
            file_net   = _dec(r.get('net'))
            dg = (ps.gross_amount - file_gross) if 'gross' in r else ZERO
            dn = (ps.net_amount   - file_net)   if 'net'   in r else ZERO
            if abs(dg) > Decimal('0.01') or abs(dn) > Decimal('0.01'):
                variances.append({
                    'employee':       emp.full_name,
                    'file_gross':     str(file_gross),
                    'computed_gross': str(ps.gross_amount),
                    'file_net':       str(file_net),
                    'computed_net':   str(ps.net_amount),
                })

            if was:
                rep.created += 1
            else:
                rep.updated += 1
        except Exception as e:    # noqa: BLE001
            rep.errors.append(f'row {i+1}: {e}')

    if variances:
        rep.extra['variances'] = variances
        rep.extra['variance_count'] = len(variances)
    if unmatched:
        rep.extra['unmatched_employees'] = sorted(set(unmatched))

    return rep


# ---------------------------------------------------------------------------
# Registry — keyed by section.key
# ---------------------------------------------------------------------------

COMMITTERS = {
    'coa':            commit_coa,
    'tb':             commit_tb,
    'gl':             commit_gl,
    'vendors':        commit_vendors,
    'customers':      commit_customers,
    'ppe':            commit_ppe,
    'bank_accounts':  commit_bank_accounts,
    'employees':      commit_employees,
    'payroll':        commit_payroll,
}
