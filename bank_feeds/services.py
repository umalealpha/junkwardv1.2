"""
bank_feeds/services.py

Run a configured bank feed: pull statement files, deduplicate by SHA-256
file hash, parse via per-bank parsers, and create BankStatement +
BankStatementLine records in the existing banking app.

Public API:
    run_feed(config, *, triggered_by=None) -> BankFeedRun
    pull_files(config) -> list[(filename, sha256, raw_bytes)]
    parse_statement_file(raw, *, protocol, hint_bank=None) -> ParsedStatement | None
    persist_parsed(parsed, config, *, file_name, user) -> BankStatement

Protocol clients implemented:
    sftp_csv      paramiko-based SFTP pull (lazy import)
    manual        no-op (always returns [])

Stubbed for follow-up:
    sftp_ofx, sftp_bai2 → return [] for now; wire ofxtools / bai2 parsers
                          when banks publish those formats
    api_fnb            → reuse fnb.client.FNBClient when its statements
                          endpoint is wired by Boitumelo at FNB BW

Credentials are read from env vars at run time, keyed by the config's
``env_prefix``:
    <PREFIX>_HOST, <PREFIX>_PORT, <PREFIX>_USERNAME,
    one of <PREFIX>_PASSWORD or <PREFIX>_PRIVATE_KEY_PATH
    optionally <PREFIX>_BANK_HINT (e.g. 'fnb_botswana', 'first_capital')
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from .models import BankFeedConfig, BankFeedRun


log = logging.getLogger(__name__)


@dataclass
class ParsedLine:
    transaction_date: str          # YYYY-MM-DD
    description: str
    amount: str                    # signed; positive = credit, negative = debit
    balance: str = ''
    reference: str = ''
    raw: dict = field(default_factory=dict)


@dataclass
class ParsedStatement:
    statement_date: str
    opening_balance: str
    closing_balance: str
    lines: list[ParsedLine] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Env-var loader
# ---------------------------------------------------------------------------

def _env(prefix: str, key: str) -> str | None:
    return os.environ.get(f'{prefix}_{key}', None) or None


# ---------------------------------------------------------------------------
# Protocol clients
# ---------------------------------------------------------------------------

def _pull_sftp_csv(config: BankFeedConfig) -> list[tuple[str, str, bytes]]:
    """Pull every file matching config.file_pattern from the configured
    SFTP host. Returns [(filename, sha256, raw_bytes)].

    Lazy paramiko import so the module loads even when paramiko isn't
    installed (it's only required for SFTP feeds)."""
    try:
        import paramiko  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            'paramiko is required for SFTP bank feeds. '
            'Install with: pip install paramiko'
        ) from exc

    host = _env(config.env_prefix, 'HOST')
    user = _env(config.env_prefix, 'USERNAME')
    if not (host and user):
        raise RuntimeError(
            f'Missing {config.env_prefix}_HOST or {config.env_prefix}_USERNAME '
            f'in environment.'
        )
    port = int(_env(config.env_prefix, 'PORT') or '22')
    password = _env(config.env_prefix, 'PASSWORD')
    key_path = _env(config.env_prefix, 'PRIVATE_KEY_PATH')

    if not (password or key_path):
        raise RuntimeError(
            f'Provide either {config.env_prefix}_PASSWORD or '
            f'{config.env_prefix}_PRIVATE_KEY_PATH for SFTP auth.'
        )

    transport = paramiko.Transport((host, port))
    try:
        if key_path:
            pkey = paramiko.RSAKey.from_private_key_file(key_path)
            transport.connect(username=user, pkey=pkey)
        else:
            transport.connect(username=user, password=password)

        sftp = paramiko.SFTPClient.from_transport(transport)
        if sftp is None:
            raise RuntimeError('Failed to open SFTP channel.')

        try:
            remote_dir = config.remote_path or '.'
            try:
                sftp.chdir(remote_dir)
            except IOError as exc:
                raise RuntimeError(
                    f'Remote dir not accessible: {remote_dir} ({exc})'
                ) from exc

            pattern = config.file_pattern or '*.csv'
            entries = sftp.listdir()
            matches = sorted(fnmatch.filter(entries, pattern))

            results: list[tuple[str, str, bytes]] = []
            for fname in matches:
                with sftp.open(fname, 'rb') as fh:
                    raw = fh.read()
                if not raw:
                    continue
                sha = hashlib.sha256(raw).hexdigest()
                results.append((fname, sha, raw))
            return results
        finally:
            sftp.close()
    finally:
        transport.close()


def _pull_manual(config: BankFeedConfig) -> list[tuple[str, str, bytes]]:
    """Manual feeds never auto-pull — files come in via the existing
    banking CSV upload UI."""
    return []


_PULL_CLIENTS = {
    BankFeedConfig.Protocol.SFTP_CSV: _pull_sftp_csv,
    BankFeedConfig.Protocol.MANUAL:   _pull_manual,
    # SFTP_OFX / SFTP_BAI2 / API_FNB intentionally absent — see module
    # docstring. They fall through to the warning path in pull_files().
}


def pull_files(config: BankFeedConfig) -> list[tuple[str, str, bytes]]:
    """Dispatch to the right protocol client. Always returns a list (may
    be empty); clients raise on hard error so run_feed records it."""
    fn = _PULL_CLIENTS.get(config.protocol)
    if fn is None:
        log.warning(
            'bank_feeds.pull_files: protocol %s not yet implemented for config %s',
            config.protocol, config.name,
        )
        return []
    return fn(config)


# ---------------------------------------------------------------------------
# Parser dispatch
# ---------------------------------------------------------------------------

def parse_statement_file(
    raw: bytes,
    *,
    protocol: str,
    hint_bank: str | None = None,
) -> ParsedStatement | None:
    """Pick the right parser per protocol. CSV protocols route through
    parsers.parse_csv which auto-detects bank by header inspection."""
    if protocol == BankFeedConfig.Protocol.SFTP_CSV:
        # Local import to avoid a circular pull (parsers.py imports the
        # ParsedStatement dataclass from this module).
        from .parsers import parse_csv
        return parse_csv(raw, hint_bank=hint_bank)
    log.warning(
        'bank_feeds.parse_statement_file: parser for %s not yet implemented',
        protocol,
    )
    return None


# ---------------------------------------------------------------------------
# Persistence into the existing banking models
# ---------------------------------------------------------------------------

def _resolve_bank_account(config: BankFeedConfig):
    """Find the banking.BankAccount whose gl_account is the config's GL.

    Returns None if no banking.BankAccount has been linked to that GL —
    the caller decides whether to log + skip or hard-fail."""
    from banking.models import BankAccount
    return (
        BankAccount.objects
        .filter(gl_account=config.bank_account)
        .first()
    )


@transaction.atomic
def persist_parsed(
    parsed: ParsedStatement,
    config: BankFeedConfig,
    *,
    file_name: str,
    user: User | None = None,
):
    """Create BankStatement + BankStatementLine rows from a ParsedStatement.

    Returns the created BankStatement. Raises if no BankAccount is linked
    to the config's GL account (run_feed catches and records the error).
    """
    from banking.models import (
        BankAccount, BankStatement, BankStatementLine,
        compute_line_dedupe_key, line_identity,
    )

    bank_acct = _resolve_bank_account(config)
    if bank_acct is None:
        raise RuntimeError(
            f'No banking.BankAccount is linked to GL account '
            f'{config.bank_account.code}. Create one in the banking module '
            f'before running this feed.'
        )

    statement_date = (
        datetime.fromisoformat(parsed.statement_date).date()
        if parsed.statement_date else timezone.localdate()
    )

    statement = BankStatement.objects.create(
        bank_account=bank_acct,
        statement_date=statement_date,
        opening_balance=Decimal(parsed.opening_balance or '0.00'),
        closing_balance=Decimal(parsed.closing_balance or '0.00'),
        file_name=file_name[:255],
        imported_by=user,
        line_count=len(parsed.lines),
    )
    if hasattr(statement, 'save') and user is not None:
        # AuditableMixin save with audit user — re-save to capture audit
        statement.save(audit_user=user)

    # No-double-import guard (2026-09-12): this is a THIRD writer of
    # BankStatementLine (alongside banking.services.BankStatementImporter and
    # fnb.statements._persist_statement) and must skip a colliding fingerprint
    # the same way, never let the DB's unique constraint crash the write.
    # Keyed on an occurrence count, not line position — see
    # banking/models.py compute_line_dedupe_key() for why.
    existing_keys = set(BankStatementLine.objects.filter(
        statement__bank_account=bank_acct,
    ).values_list('dedupe_key', flat=True))
    occurrence_counts: dict = {}
    duplicate_lines = 0

    for i, line in enumerate(parsed.lines, start=1):
        try:
            txn_date = datetime.fromisoformat(line.transaction_date).date()
        except (TypeError, ValueError):
            txn_date = statement_date
        description = (line.description or '')[:500]
        reference = (line.reference or '')[:200] or None
        amount = Decimal(line.amount or '0.00')

        identity = line_identity(bank_acct.id, txn_date, amount, description, reference or '')
        occurrence = occurrence_counts.get(identity, 0)
        occurrence_counts[identity] = occurrence + 1
        key = compute_line_dedupe_key(
            bank_acct.id, txn_date, amount, description, reference or '',
            occurrence=occurrence)
        if key in existing_keys:
            duplicate_lines += 1
            continue
        existing_keys.add(key)

        BankStatementLine.objects.create(
            statement=statement,
            line_number=i,
            transaction_date=txn_date,
            description=description,
            reference=reference,
            amount=amount,
            running_balance=(
                Decimal(line.balance) if line.balance else None
            ),
            raw_data=line.raw or None,
            dedupe_key=key,
        )

    if duplicate_lines:
        log.info(
            'Statement %s: skipped %d duplicate line(s) already present.',
            statement.statement_number, duplicate_lines,
        )
        # line_count must equal rows actually stored, not entries merely
        # seen — a duplicate skipped above is not a line on this statement.
        # run_feed() folds this straight into BankFeedRun.lines_created.
        statement.line_count -= duplicate_lines
        statement.save(update_fields=['line_count'])

    return statement


# ---------------------------------------------------------------------------
# Run wrapper
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@transaction.atomic
def run_feed(config: BankFeedConfig, *, triggered_by: User | None = None) -> BankFeedRun:
    """Execute one pull cycle for *config*. Always creates a BankFeedRun
    audit row, even on failure."""
    run = BankFeedRun.objects.create(
        config=config,
        triggered_by=triggered_by,
        started_at=timezone.now(),
    )
    try:
        files = pull_files(config)
        run.files_seen = len(files)

        # All hashes processed by past successful runs — gives idempotency.
        already_seen: set[str] = set()
        for prior in BankFeedRun.objects.filter(
            config=config, outcome=BankFeedRun.Outcome.SUCCESS,
        ).only('file_hashes'):
            already_seen.update(prior.file_hashes or [])

        hint = _env(config.env_prefix, 'BANK_HINT')

        new_hashes: list[str] = []
        for filename, sha, raw in files:
            if sha in already_seen:
                run.duplicates_skipped += 1
                continue
            parsed = parse_statement_file(raw, protocol=config.protocol, hint_bank=hint)
            if parsed is None or not parsed.lines:
                # No usable content — record + skip but don't error the whole run
                continue
            try:
                statement = persist_parsed(
                    parsed, config, file_name=filename, user=triggered_by,
                )
            except Exception as exc:
                log.exception('Persist failed for %s', filename)
                run.error_message = (run.error_message + f'\n{filename}: {exc}'[:200]).strip()
                continue
            run.statements_created += 1
            run.lines_created += statement.line_count
            new_hashes.append(sha)
            run.files_processed += 1

        run.file_hashes = new_hashes
        if not files:
            run.outcome = BankFeedRun.Outcome.EMPTY
        elif run.error_message:
            run.outcome = BankFeedRun.Outcome.ERROR
        else:
            run.outcome = BankFeedRun.Outcome.SUCCESS
    except Exception as exc:
        log.exception('bank_feed run failed for config=%s', config.name)
        run.outcome = BankFeedRun.Outcome.ERROR
        run.error_message = str(exc)[:2000]

    run.finished_at = timezone.now()
    run.save()

    config.last_run_at = run.finished_at
    config.last_run_status = run.outcome
    config.save(audit_user=triggered_by)
    return run
