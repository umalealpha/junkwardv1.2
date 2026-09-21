"""
python manage.py iso_audit            — seed if needed, run scan, write findings
python manage.py iso_audit --seed     — seed only, skip scan
python manage.py iso_audit --rescore  — recompute score from existing findings
                                        without rerunning the scan
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from iso_compliance.models import Commandment, AuditFinding, AuditRun
from iso_compliance.seed import seed_commandments
from iso_compliance.soa_seed import seed_soa


# ─── severity → score weight ──────────────────────────────────────────
SEVERITY_WEIGHT = {
    AuditFinding.SEVERITY_GOOD: 0,
    AuditFinding.SEVERITY_INFO: 0,
    AuditFinding.SEVERITY_LOW: 5,
    AuditFinding.SEVERITY_MED: 15,
    AuditFinding.SEVERITY_HIGH: 35,
    AuditFinding.SEVERITY_CRIT: 60,
}


class Command(BaseCommand):
    help = 'Run the 10-commandment ISO compliance audit and write findings.'

    def add_arguments(self, parser):
        parser.add_argument('--seed', action='store_true',
                            help='Only seed the 10 commandments, skip the scan.')
        parser.add_argument('--rescore', action='store_true',
                            help='Recompute the score from existing findings.')
        parser.add_argument('--actor', default='system',
                            help='Who triggered this run (logged on the AuditRun row).')

    # ─── entry ────────────────────────────────────────────────────────
    def handle(self, *args, **opts):
        created = seed_commandments()
        self.stdout.write(f'Seeded commandments (new={created})')

        try:
            soa_created = seed_soa()
            self.stdout.write(f'Seeded SoA controls (new={soa_created})')
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'SoA seed skipped: {e!r}'))

        if opts['seed']:
            return

        if opts['rescore']:
            self._rescore()
            return

        run = AuditRun.objects.create(actor=opts['actor'])
        try:
            with transaction.atomic():
                # Clear all OPEN findings from previous runs. Resolved/accepted survive.
                AuditFinding.objects.filter(state=AuditFinding.STATE_OPEN).delete()

                findings_added = 0
                for fn in self._all_checks():
                    findings_added += fn()

                self._score_each_commandment()
                run.findings_created = findings_added
                run.score_pct = self._aggregate_score()
                run.finished_at = timezone.now()
                run.save()
        except Exception as e:
            run.notes = f'failed: {e!r}'
            run.finished_at = timezone.now()
            run.save()
            raise

        self.stdout.write(self.style.SUCCESS(
            f'Audit complete. findings={run.findings_created} score={run.score_pct}%'
        ))

    # ─── all checks (one method per commandment) ──────────────────────
    def _all_checks(self):
        return [
            self.check_01_identity,
            self.check_02_logging,
            self.check_03_encryption,
            self.check_04_backup,
            self.check_05_change_management,
            self.check_06_incident_response,
            self.check_07_suppliers,
            self.check_08_assets,
            self.check_09_continuity,
            self.check_10_compliance,
        ]

    # ─── helpers ──────────────────────────────────────────────────────
    def _cmd(self, number: int) -> Commandment:
        return Commandment.objects.get(number=number)

    def _add(self, c: Commandment, severity: str, title: str,
             detail: str = '', fix: str = '', evidence: str = '') -> int:
        AuditFinding.objects.create(
            commandment=c, severity=severity, title=title,
            detail=detail, fix_hint=fix, evidence=evidence,
        )
        return 1

    # ── per-commandment scan ─────────────────────────────────────────
    def check_01_identity(self) -> int:
        from django.contrib.auth import get_user_model

        c = self._cmd(1)
        n = 0
        User = get_user_model()

        # SSO check — at least one Azure account present
        try:
            from core.azure_auth import EXPECTED_TENANT  # noqa: F401
            n += self._add(c, 'good', 'Azure SSO module present',
                           'core/azure_auth.py wired with Entra tenant constant.')
        except Exception:
            n += self._add(c, 'high', 'No SSO module found',
                           'Direct Django logins remain the only auth path.',
                           fix='Wire Azure Entra SSO; disable local password login.')

        # axes (lockout) enabled
        if 'axes' in settings.INSTALLED_APPS:
            n += self._add(c, 'good', 'Brute-force lockout (django-axes) installed.')
        else:
            n += self._add(c, 'med', 'No brute-force protection',
                           fix='Add django-axes; configure 5-attempt lockout.')

        # superuser headcount
        su_count = User.objects.filter(is_superuser=True).count()
        if su_count == 0:
            n += self._add(c, 'critical', 'No superuser exists',
                           'System cannot be administered.',
                           evidence=f'is_superuser=True → {su_count}')
        elif su_count > 4:
            n += self._add(c, 'medium', f'{su_count} superusers — too many',
                           fix='Demote unused supers; keep ≤3 (CFO, primary admin, break-glass).',
                           evidence=f'is_superuser=True → {su_count}')
        else:
            n += self._add(c, 'good', f'{su_count} superusers (within target ≤4).')

        # stale active users (no login >180d)
        cutoff = timezone.now() - timedelta(days=180)
        stale = User.objects.filter(is_active=True, last_login__lt=cutoff).count()
        if stale > 0:
            n += self._add(c, 'low', f'{stale} active users have not logged in >180 days',
                           fix='Quarterly access review — disable dormant accounts.',
                           evidence=f'active & last_login<{cutoff:%Y-%m-%d} → {stale}')

        # password length policy
        validators = getattr(settings, 'AUTH_PASSWORD_VALIDATORS', [])
        min_len = next((v.get('OPTIONS', {}).get('min_length')
                        for v in validators
                        if 'MinimumLength' in v.get('NAME', '')), None)
        if not min_len or min_len < 10:
            n += self._add(c, 'low', f'Password min-length is {min_len or "unset"}',
                           fix='Raise MinimumLengthValidator min_length to 12.')
        else:
            n += self._add(c, 'good', f'Password min-length = {min_len}.')

        return n

    def check_02_logging(self) -> int:
        from django.apps import apps

        c = self._cmd(2)
        n = 0

        # AuditLog model exists?
        try:
            apps.get_model('core', 'AuditLog')
            n += self._add(c, 'good', 'core.AuditLog model present',
                           'Immutable append-only audit trail.')
        except LookupError:
            n += self._add(c, 'high', 'No AuditLog model',
                           fix='Add an AuditLog model that records every approval, post, and export.')

        # AuditableMixin usage
        repo_root = Path(settings.BASE_DIR)
        mixin_users = 0
        try:
            for py in repo_root.rglob('*/models.py'):
                if 'site-packages' in str(py):
                    continue
                txt = py.read_text(encoding='utf-8', errors='ignore')
                if 'AuditableMixin' in txt:
                    mixin_users += 1
            if mixin_users >= 5:
                n += self._add(c, 'good',
                               f'AuditableMixin used in {mixin_users} app models.',
                               evidence=f'grep AuditableMixin → {mixin_users} models.py files')
            else:
                n += self._add(c, 'medium',
                               f'AuditableMixin only used in {mixin_users} models',
                               fix='Apply AuditableMixin across all approval-bearing models.')
        except Exception as e:
            n += self._add(c, 'info', f'Could not scan models for AuditableMixin: {e!r}')

        # JE immutability: ledger.JournalEntry posted-state guard?
        try:
            JE = apps.get_model('ledger', 'JournalEntry')
            field_names = {f.name for f in JE._meta.get_fields()}
            if 'posted_at' in field_names or 'is_posted' in field_names:
                n += self._add(c, 'good',
                               'JournalEntry has posted-state field — supports immutability rule.')
            else:
                n += self._add(c, 'medium',
                               'JournalEntry has no posted-state field',
                               fix='Add posted_at + guard save() to block edits after posting.')
        except LookupError:
            pass

        return n

    def check_03_encryption(self) -> int:
        c = self._cmd(3)
        n = 0

        # SECURE_SSL_REDIRECT / HSTS
        if settings.DEBUG:
            n += self._add(c, 'info', 'DEBUG=True in this environment',
                           'OK for dev; verify production runs with DEBUG=False.')
        if not getattr(settings, 'SECURE_SSL_REDIRECT', False):
            n += self._add(c, 'medium', 'SECURE_SSL_REDIRECT not enabled',
                           fix='Set SECURE_SSL_REDIRECT=True behind the prod reverse proxy.')
        else:
            n += self._add(c, 'good', 'SECURE_SSL_REDIRECT enabled.')

        if not getattr(settings, 'SECURE_HSTS_SECONDS', 0):
            n += self._add(c, 'medium', 'HSTS not enabled',
                           fix='SECURE_HSTS_SECONDS=31536000; include subdomains.')
        else:
            n += self._add(c, 'good',
                           f'HSTS configured ({settings.SECURE_HSTS_SECONDS}s).')

        # SECRET_KEY in env, not hardcoded
        env_secret = os.environ.get('DJANGO_SECRET_KEY') or os.environ.get('SECRET_KEY')
        if not env_secret:
            n += self._add(c, 'high', 'SECRET_KEY not read from environment',
                           fix='Move SECRET_KEY to /etc/alpha-finance/.env.')
        else:
            n += self._add(c, 'good', 'SECRET_KEY loaded from environment.')

        # Look for obvious plaintext secrets accidentally committed
        repo_root = Path(settings.BASE_DIR)
        leaks = 0
        suspect_patterns = ('ghp_', 'AKIA', 'sk_live_', 'AIza')
        try:
            for py in repo_root.rglob('*.py'):
                if 'site-packages' in str(py) or '/migrations/' in str(py):
                    continue
                txt = py.read_text(encoding='utf-8', errors='ignore')
                if any(p in txt for p in suspect_patterns):
                    leaks += 1
            if leaks:
                n += self._add(c, 'critical',
                               f'Possible secret patterns in {leaks} source files',
                               fix='Audit + rotate any matched key immediately.',
                               evidence=f'patterns: {suspect_patterns}')
            else:
                n += self._add(c, 'good',
                               'No obvious GitHub/AWS/Stripe/Google key patterns in source.')
        except Exception as e:
            n += self._add(c, 'info', f'Secret scan failed: {e!r}')

        return n

    def check_04_backup(self) -> int:
        c = self._cmd(4)
        n = 0

        backup_dir = Path('/var/backups/alpha-finance')
        if backup_dir.exists():
            try:
                backups = sorted(backup_dir.glob('*.sql*'),
                                 key=lambda p: p.stat().st_mtime, reverse=True)
                if backups:
                    latest = backups[0]
                    age_hr = (timezone.now().timestamp() - latest.stat().st_mtime) / 3600
                    if age_hr > 36:
                        n += self._add(c, 'high',
                                       f'Latest backup is {age_hr:.0f}h old',
                                       fix='Restart pg_dump cron; RPO target ≤24h.')
                    else:
                        n += self._add(c, 'good',
                                       f'Latest backup {age_hr:.0f}h old ({latest.name}).')
                else:
                    n += self._add(c, 'high',
                                   f'{backup_dir} exists but no backups found',
                                   fix='Run pg_dump cron now; verify retention policy.')
            except Exception as e:
                n += self._add(c, 'info', f'Backup dir scan failed: {e!r}')
        else:
            n += self._add(c, 'medium',
                           f'Standard backup dir {backup_dir} not found on this host',
                           fix='Configure pg_dump + S3 sync; document RPO/RTO.')

        # Restore drill — placeholder, can only be evidenced manually
        n += self._add(c, 'medium', 'No automated restore drill on record',
                       fix='Quarterly: restore latest backup into a scratch DB, run TB recon, log evidence.')

        return n

    def check_05_change_management(self) -> int:
        c = self._cmd(5)
        n = 0

        repo_root = Path(settings.BASE_DIR)
        # .git presence
        if (repo_root / '.git').exists():
            n += self._add(c, 'good', 'Source code under git version control.')

            # Default branch protection (we can only check the remote URL is sane)
            try:
                remote = subprocess.check_output(
                    ['git', '-C', str(repo_root), 'remote', 'get-url', 'origin'],
                    text=True, timeout=5,
                ).strip()
                if remote.startswith('https://') and '@github.com' in remote:
                    n += self._add(c, 'high',
                                   'Git remote uses HTTPS with embedded token',
                                   fix='Switch remote to SSH; revoke embedded PAT.',
                                   evidence=remote.split('@')[0] + '@github.com…')
                else:
                    n += self._add(c, 'good',
                                   'Git remote uses SSH (no in-URL credentials).',
                                   evidence=remote)
            except Exception:
                pass
        else:
            n += self._add(c, 'critical', 'Not a git repo on this host',
                           fix='Check out the source tree under version control.')

        # Spec-workflow directory present?
        if (repo_root / '.claude' / 'specs').exists():
            n += self._add(c, 'good', '.claude/specs/ directory exists — spec-driven changes.')
        else:
            n += self._add(c, 'low', 'No spec-workflow directory',
                           fix='Adopt .claude/specs/ for non-trivial changes.')

        # Migrations directory health — any model with no recent migration?
        return n

    def check_06_incident_response(self) -> int:
        c = self._cmd(6)
        n = 0

        repo_root = Path(settings.BASE_DIR)
        for f in ['INCIDENT-RUNBOOK.md', 'docs/incident-response.md', 'docs/RUNBOOK.md']:
            if (repo_root / f).exists():
                n += self._add(c, 'good', f'Incident runbook present: {f}')
                break
        else:
            n += self._add(c, 'high',
                           'No incident-response runbook found in repo',
                           fix='Create docs/incident-response.md (severity scale, comms tree, postmortem template).')

        # On-call rota — env hint
        if os.environ.get('ONCALL_PRIMARY'):
            n += self._add(c, 'good',
                           f'ONCALL_PRIMARY env set ({os.environ["ONCALL_PRIMARY"]}).')
        else:
            n += self._add(c, 'medium', 'No on-call rota env var',
                           fix='Set ONCALL_PRIMARY / ONCALL_SECONDARY in /etc/alpha-finance/.env.')

        return n

    def check_07_suppliers(self) -> int:
        from django.apps import apps

        c = self._cmd(7)
        n = 0

        # Supplier model and contract attachment field?
        try:
            Supplier = apps.get_model('procurement', 'Supplier')
            count = Supplier.objects.count()
            n += self._add(c, 'good',
                           f'Supplier register present ({count} suppliers).',
                           evidence=f'procurement.Supplier rows = {count}')
        except LookupError:
            try:
                Supplier = apps.get_model('billing', 'Contact')
                vendor_count = Supplier.objects.filter(
                    is_vendor=True).count() if 'is_vendor' in [f.name for f in Supplier._meta.get_fields()] else Supplier.objects.count()
                n += self._add(c, 'good',
                               f'Vendor register present in billing.Contact ({vendor_count}).')
            except Exception as e:
                n += self._add(c, 'high', f'No supplier/vendor register: {e!r}',
                               fix='Build a vendor master with DPA flag + contract attachment.')

        # DPA-on-file evidence — no current field. Flag as pending.
        n += self._add(c, 'medium',
                       'No DPA-on-file flag on vendor records',
                       fix='Add Supplier.has_dpa boolean + contract upload; track sub-processor list.')

        return n

    def check_08_assets(self) -> int:
        from django.apps import apps

        c = self._cmd(8)
        n = 0

        try:
            FixedAsset = apps.get_model('assets', 'FixedAsset')
            asset_count = FixedAsset.objects.count()
            n += self._add(c, 'good',
                           f'Fixed asset register present ({asset_count} assets).')
        except LookupError:
            n += self._add(c, 'high', 'No FixedAsset model',
                           fix='Build assets.FixedAsset with category + custodian.')

        # Data classification register — does not yet exist
        n += self._add(c, 'medium',
                       'No data-classification register',
                       fix='Tag each ERP module: public / internal / confidential / PII. Document in docs/data-classification.md.')

        return n

    def check_09_continuity(self) -> int:
        c = self._cmd(9)
        n = 0
        repo_root = Path(settings.BASE_DIR)
        for f in ['BCP.md', 'docs/bcp.md', 'docs/business-continuity.md', 'docs/dr-plan.md']:
            if (repo_root / f).exists():
                n += self._add(c, 'good', f'Business-continuity doc present: {f}')
                break
        else:
            n += self._add(c, 'high',
                           'No BCP/DR plan in repo',
                           fix='docs/business-continuity.md — RTO/RPO per service, comms tree, annual test log.')

        # Multi-AZ / multi-region — single-instance EC2 is single-AZ
        n += self._add(c, 'medium',
                       'Single-instance prod (no warm standby)',
                       fix='Document accepted-risk OR provision a warm-standby snapshot+restore script.')
        return n

    def check_10_compliance(self) -> int:
        from django.apps import apps

        c = self._cmd(10)
        n = 0

        # NBFIRA app
        if apps.is_installed('nbfira'):
            n += self._add(c, 'good',
                           'nbfira app installed — NBFIRA returns scaffolded.')
        else:
            n += self._add(c, 'high',
                           'No nbfira app',
                           fix='Add nbfira returns module per Insurance Industry Act.')

        # IFRS 17 module hint
        if apps.is_installed('reinsurance'):
            n += self._add(c, 'info',
                           'reinsurance app present (IFRS 17 inputs).')

        # Evidence register — directory of board minutes etc.
        evidence_dirs = [Path('/var/alpha-finance/evidence'),
                         Path(settings.BASE_DIR) / 'docs' / 'evidence']
        if any(p.exists() for p in evidence_dirs):
            n += self._add(c, 'good', 'Evidence register directory exists.')
        else:
            n += self._add(c, 'medium',
                           'No central evidence register',
                           fix='Create docs/evidence/ with subfolders per commandment.')

        return n

    # ─── scoring ──────────────────────────────────────────────────────
    def _score_each_commandment(self) -> None:
        now = timezone.now()
        for cmd in Commandment.objects.all():
            findings = list(cmd.findings.filter(state=AuditFinding.STATE_OPEN))
            if not findings:
                cmd.status = Commandment.STATUS_GOOD
            else:
                worst = max((SEVERITY_WEIGHT[f.severity] for f in findings), default=0)
                if worst >= SEVERITY_WEIGHT['high']:
                    cmd.status = Commandment.STATUS_PENDING
                elif worst >= SEVERITY_WEIGHT['medium']:
                    cmd.status = Commandment.STATUS_PARTIAL
                elif worst > 0:
                    cmd.status = Commandment.STATUS_DONE
                else:
                    cmd.status = Commandment.STATUS_GOOD
            cmd.last_audited_at = now
            cmd.save(update_fields=['status', 'last_audited_at'])

    def _aggregate_score(self) -> int:
        # Each commandment contributes 10 points; deduct per worst finding
        total_lost = 0
        for cmd in Commandment.objects.all():
            findings = cmd.findings.filter(state=AuditFinding.STATE_OPEN)
            worst = 0
            for f in findings:
                worst = max(worst, SEVERITY_WEIGHT[f.severity])
            # Cap deduction at 10 (the slot weight)
            total_lost += min(10, worst / 6)   # crit(60)/6=10, high(35)/6≈5.8, med(15)/6=2.5, low(5)/6≈0.8
        score = max(0, round(100 - total_lost))
        return min(100, score)

    def _rescore(self) -> None:
        self._score_each_commandment()
        score = self._aggregate_score()
        AuditRun.objects.create(
            actor='rescore', findings_created=0, score_pct=score,
            finished_at=timezone.now(),
            notes='Recomputed from existing findings without rerunning the scan.',
        )
        self.stdout.write(self.style.SUCCESS(f'Rescored. score={score}%'))
