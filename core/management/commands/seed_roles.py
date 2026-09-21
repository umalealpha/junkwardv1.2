"""
seed_roles.py — idempotent seeder for the RBAC layer.

Creates / refreshes:
  - The Permission catalogue (~60 codes)
  - The Role catalogue (~30 named roles across 7 levels)
  - Role → Permission mappings
  - Bootstrap pganesharajah@alphadirect.co.bw as SUPER_ADMIN

Re-running is safe; existing rows are updated in place. To remove a role/perm
that was previously seeded, remove it from this file AND deactivate it in the
admin (we deliberately don't auto-delete to preserve audit trail).

Run:
    python manage.py seed_roles
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import (
    Department,
    Permission,
    Role,
    UserProfile,
    UserRoleAssignment,
)
from core.rbac_service import assign_role

User = get_user_model()


# ---------------------------------------------------------------------------
# Permission catalogue
# ---------------------------------------------------------------------------

PERMISSIONS = [
    # (code, category, description)

    # ---- System ----
    ('system.admin',                'system',     'Full system administration (super admin)'),
    ('system.audit_log.view',       'system',     'View audit log'),
    ('system.settings.edit',        'system',     'Edit system settings'),

    # ---- Users & Roles ----
    ('users.view',                  'users',      'View user list'),
    ('users.invite',                'users',      'Invite new user'),
    ('users.deactivate',            'users',      'Deactivate user'),
    ('users.reset_password',        'users',      'Reset user password'),
    ('roles.view',                  'users',      'View roles and permissions'),
    ('roles.create',                'users',      'Create custom roles'),
    ('roles.edit',                  'users',      'Edit role permissions'),
    ('roles.assign',                'users',      'Assign roles to users'),

    # ---- Ledger / Journal Entries ----
    ('je.view',                     'ledger',     'View journal entries'),
    ('je.create',                   'ledger',     'Create journal entry (draft)'),
    ('je.submit',                   'ledger',     'Submit JE for approval'),
    ('je.approve',                  'ledger',     'Approve and post JE'),
    ('je.reject',                   'ledger',     'Reject pending JE'),
    ('je.reverse',                  'ledger',     'Reverse posted JE'),
    ('je.post_direct',              'ledger',     'Post JE bypassing approval (system/API only)'),
    ('period.view',                 'ledger',     'View fiscal periods'),
    ('period.close',                'ledger',     'Close fiscal period'),
    ('period.reopen',               'ledger',     'Reopen closed fiscal period'),

    # ---- Billing / Invoices ----
    ('invoice.view',                'billing',    'View invoices and bills'),
    ('invoice.create',              'billing',    'Create invoice or bill draft'),
    ('invoice.post',                'billing',    'Post invoice or bill'),
    ('invoice.approve_bill_tier1',  'billing',    'Approve vendor bill — tier 1 variance'),
    ('invoice.approve_bill_tier2',  'billing',    'Approve vendor bill — tier 2 variance'),

    # ---- Payments ----
    ('payment.view',                'payments',   'View payments'),
    ('payment.create',              'payments',   'Create payment draft'),
    ('payment.confirm',             'payments',   'Confirm payment'),
    ('payment.approve_outbound',    'payments',   'Dual-auth approve outbound payment'),
    ('payment.allocate',            'payments',   'Allocate payment to invoice'),

    # ---- Procurement ----
    ('po.view',                     'procurement', 'View purchase orders'),
    ('po.create',                   'procurement', 'Create purchase order'),
    ('po.fm_approve',               'procurement', 'Finance Manager approve PO'),
    ('po.cfo_approve',              'procurement', 'CFO approve PO'),
    ('po.cancel',                   'procurement', 'Cancel PO'),
    ('po.receive',                  'procurement', 'Post goods receipt note'),
    ('vendor_bank.view',            'procurement', 'View vendor bank accounts'),
    ('vendor_bank.create',          'procurement', 'Create vendor bank account'),
    ('vendor_bank.approve',         'procurement', 'Approve vendor bank account'),

    # ---- Claims ----
    ('claim.view',                  'claims',     'View claims'),
    ('claim.create',                'claims',     'Create claim'),
    ('claim.adjust',                'claims',     'Adjust claim amount'),
    ('claim.approve',               'claims',     'Approve claim payment'),
    ('claim.pay',                   'claims',     'Process claim payment'),
    ('recovery.view',               'claims',     'View recoveries'),
    ('recovery.process',            'claims',     'Process recovery (subrogation / salvage)'),

    # ---- Underwriting ----
    ('uw.view',                     'underwriting', 'View underwriting data'),
    ('uw.quote',                    'underwriting', 'Issue quotes'),
    ('uw.bind',                     'underwriting', 'Bind policies'),
    ('uw.pricing.edit',             'underwriting', 'Edit pricing parameters'),
    ('uw.counterparty.submit',      'underwriting', 'Submit a reinsurer for onboarding'),
    ('uw.counterparty.approve',     'underwriting', 'Approve the underwriting stage of reinsurer onboarding'),

    # ---- Reinsurance ----
    ('re.view',                     'reinsurance', 'View reinsurance treaties'),
    ('re.treaty.edit',              'reinsurance', 'Edit reinsurance treaties'),
    ('re.bordereau.process',        'reinsurance', 'Process bordereau import'),
    ('re.cession.post',             'reinsurance', 'Post cession journal entry'),
    # Reinsurer security controls (Arun P. Iyer brief, 15-Sep-2026). The last two
    # are additionally gated on the NAMED account in reinsurance/onboarding.py —
    # holding the permission is necessary, never sufficient.
    ('re.counterparty.approve_principal', 'reinsurance', 'Principal & operations review of a reinsurer (Paul Beka)'),
    ('re.counterparty.approve_ceo',       'reinsurance', 'Final CEO approval of a reinsurer'),
    ('re.fac.edit',                 'reinsurance', 'Maintain the facultative risk register'),

    # ---- Reports ----
    ('report.financial.view',       'reports',    'View financial reports (TB, P&L, BS)'),
    ('report.management.view',      'reports',    'View management pack and EXCO dashboards'),
    ('report.audit.view',           'reports',    'View audit pack and GL detail'),
    ('report.regulatory.view',      'reports',    'View regulatory capital and NBFIRA reports'),
    ('report.financial.export',     'reports',    'Export financial reports (PDF / CSV)'),

    # ---- Fixed Assets ----
    ('asset.view',                  'assets',     'View asset register'),
    ('asset.create',                'assets',     'Add fixed asset'),
    ('asset.depreciate',            'assets',     'Run monthly depreciation'),
    ('asset.dispose',               'assets',     'Dispose asset'),
    ('asset.sign_off',              'assets',     'Sign off asset register'),
    ('asset.import',                'assets',     'Import assets from external system'),

    # ---- HR / Payroll ----
    ('hr.employee.view',            'hr',         'View employees'),
    ('hr.employee.edit',            'hr',         'Edit employee records'),
    ('hr.payroll.run',              'hr',         'Run payroll calculation'),
    ('hr.payroll.approve',          'hr',         'Approve payroll'),
    ('hr.payroll.pay',              'hr',         'Disburse payroll'),

    # ---- Banking ----
    ('bank.view',                   'banking',    'View bank accounts and statements'),
    ('bank.reconcile',              'banking',    'Reconcile bank statement'),
    ('bank.approve',                'banking',    'Approve a completed bank reconciliation (second person, not the reconciler)'),
    ('bank.feed.run',               'banking',    'Run bank feed sync'),

    # ---- Investments ----
    ('investment.view',             'investments', 'View investment portfolio'),
    ('investment.create',           'investments', 'Create investment'),
    ('investment.approve',          'investments', 'Approve investment transaction'),

    # ---- FX ----
    ('fx.view',                     'fx',         'View FX rates and revaluations'),
    ('fx.revalue',                  'fx',         'Run FX revaluation'),

    # ---- Exceptions ----
    ('exception.view',              'exceptions', 'View exception queue'),
    ('exception.acknowledge',       'exceptions', 'Acknowledge exception'),
    ('exception.resolve',           'exceptions', 'Resolve exception'),
    ('exception.dismiss',           'exceptions', 'Dismiss exception'),

    # ---- Compliance / Risk ----
    ('compliance.view',             'compliance', 'View compliance dashboard'),
    ('compliance.related_party.flag', 'compliance', 'Flag related-party transactions'),
    ('compliance.counterparty.approve', 'compliance', 'Approve the KYC/AML stage of reinsurer onboarding'),
    ('risk.capital.view',           'compliance', 'View regulatory capital position'),
]


# ---------------------------------------------------------------------------
# Role catalogue (with permission bundles)
# ---------------------------------------------------------------------------
#
# 'permissions' is a list of permission codes, OR the literal '__all__' which
# is expanded at seed time to every active permission (Super Admin only).
# Use category-wildcards like 'ledger.*' to grant all permissions in a category.

ALL = '__all__'

ROLES = [
    # ===== LEVEL 0 — SUPER ADMIN =====
    dict(code='SUPER_ADMIN',         name='Super Administrator',          level=0, department=None,
         description='System root. Manages all roles and all permissions.',
         permissions=ALL),

    # ===== LEVEL 1 — EXECUTIVE (C-suite) =====
    dict(code='CEO',                 name='Chief Executive Officer',      level=1, department=Department.EXECUTIVE,
         description='Read-everything plus management dashboards. Cannot post journals.',
         permissions=['report.*', 'je.view', 'po.view', 'claim.view', 'invoice.view', 'payment.view',
                      'users.view', 'roles.view', 'system.audit_log.view',
                      'compliance.view', 'risk.capital.view', 'exception.view',
                      're.view', 're.counterparty.approve_ceo']),

    dict(code='CFO',                 name='Chief Financial Officer',      level=1, department=Department.EXECUTIVE,
         description='Finance authority. Approves journals, POs, payments, period close.',
         permissions=['report.*', 'je.*', 'period.*', 'invoice.*', 'payment.*', 'po.*', 'vendor_bank.*',
                      'asset.*', 'fx.*', 'bank.*', 'investment.*', 'exception.*', 'compliance.*',
                      'risk.capital.view', 're.view',
                      'users.view', 'users.invite', 'roles.view', 'roles.assign',
                      'system.audit_log.view']),

    dict(code='COO',                 name='Chief Operating Officer',      level=1, department=Department.EXECUTIVE,
         description='Operations + claims executive oversight.',
         permissions=['report.financial.view', 'report.management.view', 'report.audit.view',
                      'claim.view', 'claim.approve', 'po.view', 'po.cancel', 're.view',
                      'users.view', 'roles.view', 'roles.assign',
                      'exception.view', 'exception.acknowledge']),

    dict(code='CRO',                 name='Chief Risk Officer',           level=1, department=Department.EXECUTIVE,
         description='Risk, compliance, and regulatory authority.',
         permissions=['report.*', 'compliance.*', 'risk.capital.view',
                      'je.view', 'po.view', 'claim.view', 'recovery.view', 're.view',
                      'exception.*', 'system.audit_log.view',
                      'users.view', 'roles.view']),

    dict(code='CTO',                 name='Chief Technology Officer',     level=1, department=Department.EXECUTIVE,
         description='System administration without financial posting rights.',
         permissions=['system.audit_log.view', 'system.settings.edit',
                      'users.view', 'users.invite', 'users.deactivate', 'users.reset_password',
                      'roles.view', 'roles.assign',
                      'bank.feed.run', 'exception.view', 're.view']),

    # ===== LEVEL 2 — DEPARTMENT HEADS =====
    dict(code='HEAD_FINANCE',        name='Head of Finance',              level=2, department=Department.FINANCE,
         description='Heads the finance department under the CFO.',
         permissions=['report.*', 'je.*', 'period.view', 'invoice.*', 'payment.*', 'po.*',
                      'vendor_bank.view', 'vendor_bank.create', 'asset.view', 'asset.sign_off',
                      'fx.view', 'fx.revalue', 'bank.view', 'bank.reconcile',
                      'exception.view', 'exception.acknowledge', 'exception.resolve',
                      'users.view', 'roles.view', 'roles.assign']),

    dict(code='HEAD_CLAIMS',         name='Head of Claims',               level=2, department=Department.CLAIMS,
         description='Heads the claims department.',
         permissions=['claim.*', 'recovery.*', 'po.view', 'po.fm_approve',
                      'report.management.view', 'exception.view', 'exception.acknowledge',
                      'users.view', 'roles.view', 'roles.assign']),

    dict(code='HEAD_UNDERWRITING',   name='Head of Underwriting',         level=2, department=Department.UNDERWRITING,
         description='Heads the underwriting department.',
         permissions=['uw.*', 're.view', 'report.management.view',
                      'users.view', 'roles.view', 'roles.assign']),

    dict(code='HEAD_REINSURANCE',    name='Head of Reinsurance',          level=2, department=Department.REINSURANCE,
         description='Heads the reinsurance function.',
         permissions=['re.*', 'report.management.view', 'report.financial.view',
                      'users.view', 'roles.view', 'roles.assign']),

    dict(code='HEAD_COMPLIANCE',     name='Head of Compliance & Risk',    level=2, department=Department.COMPLIANCE,
         description='Heads compliance and regulatory.',
         permissions=['compliance.*', 'risk.capital.view', 're.view',
                      'report.regulatory.view', 'report.audit.view',
                      'exception.*', 'system.audit_log.view',
                      'users.view', 'roles.view', 'roles.assign']),

    dict(code='HEAD_HR',             name='Head of Human Resources',      level=2, department=Department.HR,
         description='Heads the HR and payroll function.',
         permissions=['hr.*', 'po.view', 'po.fm_approve',
                      'users.view', 'roles.view', 'roles.assign']),

    dict(code='HEAD_IT',             name='Head of IT',                   level=2, department=Department.IT,
         description='Heads the IT department.',
         permissions=['system.settings.edit', 'system.audit_log.view',
                      'users.view', 'users.invite', 'users.deactivate', 'users.reset_password',
                      'roles.view', 'roles.assign',
                      'bank.feed.run']),

    dict(code='HEAD_OPERATIONS',     name='Head of Operations',           level=2, department=Department.OPERATIONS,
         description='Heads operations.',
         permissions=['po.view', 'po.fm_approve', 'po.create',
                      'report.management.view',
                      'users.view', 'roles.view', 'roles.assign']),

    # ===== LEVEL 3 — MANAGERS =====
    dict(code='FINANCE_MANAGER',     name='Finance Manager',              level=3, department=Department.FINANCE,
         description='Approves JEs, POs at FM tier, manages finance team.',
         permissions=['je.view', 'je.create', 'je.submit', 'je.approve', 'je.reject',
                      'period.view',
                      'invoice.*', 'payment.*', 'po.view', 'po.fm_approve', 'po.create',
                      'vendor_bank.view', 'vendor_bank.create', 'vendor_bank.approve',
                      'asset.view', 'asset.sign_off',
                      'fx.view', 'fx.revalue', 'bank.view', 'bank.reconcile',
                      'report.financial.view', 'report.financial.export',
                      'exception.view', 'exception.acknowledge',
                      'users.view']),

    dict(code='FINANCIAL_CONTROLLER', name='Financial Controller',        level=3, department=Department.FINANCE,
         description='Controls and posts journals, runs FX, closes periods.',
         permissions=['je.view', 'je.create', 'je.submit', 'je.approve', 'je.reject',
                      'period.view', 'period.close',
                      'invoice.view', 'invoice.post',
                      'asset.view', 'asset.depreciate', 'asset.sign_off',
                      'fx.view', 'fx.revalue', 'bank.view', 'bank.reconcile',
                      'report.*', 'exception.view', 'exception.acknowledge',
                      'compliance.related_party.flag']),

    dict(code='CLAIMS_MANAGER',      name='Claims Manager',               level=3, department=Department.CLAIMS,
         description='Approves claims, manages adjusters, tier-1 PO variances.',
         permissions=['claim.*', 'recovery.*', 'po.view', 'po.fm_approve', 'po.create',
                      'invoice.view', 'invoice.approve_bill_tier1',
                      'report.management.view',
                      'exception.view', 'exception.acknowledge', 'users.view']),

    dict(code='UNDERWRITING_MANAGER', name='Underwriting Manager',        level=3, department=Department.UNDERWRITING,
         description='Manages underwriters and pricing.',
         permissions=['uw.*', 're.view', 'report.management.view',
                      'exception.view', 'exception.acknowledge', 'users.view']),

    dict(code='REINSURANCE_MANAGER', name='Reinsurance Manager',          level=3, department=Department.REINSURANCE,
         description='Manages treaties, bordereaux, cessions.',
         permissions=['re.*', 'report.financial.view', 'report.management.view',
                      'exception.view', 'exception.acknowledge', 'users.view']),

    dict(code='OPERATIONS_MANAGER',  name='Operations Manager',           level=3, department=Department.OPERATIONS,
         description='Operations dept lead; approves POs at tier-1.',
         permissions=['po.view', 'po.create', 'po.fm_approve', 'po.receive',
                      'invoice.view', 'invoice.approve_bill_tier1',
                      'report.management.view',
                      'exception.view', 'exception.acknowledge', 'users.view']),

    dict(code='HR_MANAGER',          name='HR Manager',                   level=3, department=Department.HR,
         description='HR lead; runs payroll, manages employees.',
         # CFO 19-Sep-2026: no PO / bill approval — HR heads can grant this role
         # themselves on HR Settings, so it must not carry money approvals.
         permissions=['hr.*', 'po.view', 'po.create',
                      'invoice.view',
                      'users.view']),

    dict(code='HR_VIEWER',           name='HR Viewer (read-only, no pay)', level=5, department=Department.HR,
         description='Read-only view of all staff HR records; own payslip only; '
                     'no pay/compensation of others, no edits or approvals.',
         permissions=['users.view']),

    dict(code='COMPLIANCE_MANAGER',  name='Compliance Manager',           level=3, department=Department.COMPLIANCE,
         description='Day-to-day compliance and exception handling.',
         permissions=['compliance.*', 'risk.capital.view', 're.view',
                      'report.regulatory.view', 'report.audit.view',
                      'exception.view', 'exception.acknowledge', 'exception.resolve',
                      'system.audit_log.view', 'users.view']),

    dict(code='IT_MANAGER',          name='IT Manager',                   level=3, department=Department.IT,
         description='IT operations and user administration.',
         permissions=['system.audit_log.view', 'system.settings.edit',
                      'users.view', 'users.invite', 'users.reset_password',
                      'bank.feed.run', 'exception.view']),

    # ===== LEVEL 4 — SENIOR SPECIALISTS =====
    dict(code='SENIOR_ACCOUNTANT',   name='Senior Accountant',            level=4, department=Department.FINANCE,
         description='Senior finance specialist; preps JEs, runs reports.',
         permissions=['je.view', 'je.create', 'je.submit',
                      'invoice.view', 'invoice.create', 'invoice.post',
                      'payment.view', 'payment.create', 'payment.allocate',
                      'po.view', 'asset.view', 'fx.view', 'bank.view',
                      'report.financial.view', 'report.financial.export',
                      'period.view']),

    dict(code='SENIOR_CLAIMS_ADJUSTER', name='Senior Claims Adjuster',    level=4, department=Department.CLAIMS,
         description='Senior claims processing.',
         permissions=['claim.view', 'claim.create', 'claim.adjust',
                      'recovery.view', 'recovery.process',
                      'po.view', 'invoice.view']),

    # Parts-ordering specialist: raises the claims purchase orders (repairer +
    # parts) from vehicle assessments. Deliberately NO claim settlement, NO
    # payments, NO PO approval (segregation of duties — he raises drafts only),
    # NO payroll/HR. Entity scope (e.g. ADIC-only) is set on the user's company
    # access, not in the role. (CFO 2026-07-07 — for Lemogang Machola.)
    dict(code='SR_CLAIMS_HANDLER_PARTS', name='Senior Claims Handler – Parts Ordering', level=4, department=Department.CLAIMS,
         description='Raises claims purchase orders (repairer + parts) from assessments. No claim settlement, payments, PO approval, or payroll.',
         permissions=['claim.view',
                      'po.view', 'po.create',
                      'vendor_bank.view',
                      'invoice.view']),

    dict(code='SENIOR_UNDERWRITER',  name='Senior Underwriter',           level=4, department=Department.UNDERWRITING,
         description='Senior underwriting.',
         permissions=['uw.view', 'uw.quote', 'uw.bind', 're.view']),

    dict(code='SENIOR_RI_OFFICER',   name='Senior Reinsurance Officer',   level=4, department=Department.REINSURANCE,
         description='Senior reinsurance processing.',
         permissions=['re.view', 're.bordereau.process', 'report.financial.view']),

    dict(code='INTERNAL_AUDITOR',    name='Internal Auditor',             level=4, department=Department.COMPLIANCE,
         description='Internal audit — read access across all financial data.',
         permissions=['report.*', 'je.view', 'invoice.view', 'payment.view', 'po.view',
                      'claim.view', 'recovery.view', 'asset.view', 'bank.view',
                      'period.view', 'system.audit_log.view',
                      'compliance.view', 'risk.capital.view', 're.view']),

    dict(code='RISK_OFFICER',        name='Risk Officer',                 level=4, department=Department.COMPLIANCE,
         description='Risk analysis and capital monitoring.',
         permissions=['compliance.view', 'risk.capital.view', 're.view',
                      'report.regulatory.view', 'report.audit.view',
                      'exception.view', 'exception.acknowledge']),

    # ===== LEVEL 5 — OFFICERS =====
    dict(code='ACCOUNTANT',          name='Accountant',                   level=5, department=Department.FINANCE,
         description='Day-to-day finance staff.',
         permissions=['je.view', 'je.create', 'je.submit',
                      'invoice.view', 'invoice.create',
                      'payment.view', 'payment.create',
                      'asset.view', 'bank.view',
                      'report.financial.view', 'period.view']),

    dict(code='AP_CLERK',            name='Accounts Payable Clerk',       level=5, department=Department.FINANCE,
         description='Vendor bills, payments out.',
         permissions=['invoice.view', 'invoice.create',
                      'payment.view', 'payment.create',
                      'po.view', 'vendor_bank.view',
                      'report.financial.view']),

    dict(code='AR_CLERK',            name='Accounts Receivable Clerk',    level=5, department=Department.FINANCE,
         description='Customer invoices, receipts.',
         permissions=['invoice.view', 'invoice.create',
                      'payment.view', 'payment.allocate',
                      'report.financial.view']),

    dict(code='CLAIMS_ADJUSTER',     name='Claims Adjuster',              level=5, department=Department.CLAIMS,
         description='Processes individual claims.',
         permissions=['claim.view', 'claim.create',
                      'invoice.view', 'po.view']),

    dict(code='RECOVERY_OFFICER',    name='Claims Recovery Officer',      level=5, department=Department.CLAIMS,
         description='Subrogation and salvage recoveries.',
         permissions=['claim.view', 'recovery.view', 'recovery.process']),

    dict(code='UNDERWRITER',         name='Underwriter',                  level=5, department=Department.UNDERWRITING,
         description='Issues quotes, binds policies.',
         permissions=['uw.view', 'uw.quote', 'uw.bind', 're.view']),

    dict(code='PRICING_ANALYST',     name='Pricing Analyst',              level=5, department=Department.UNDERWRITING,
         description='Pricing model maintenance.',
         permissions=['uw.view', 'uw.pricing.edit']),

    dict(code='RI_OFFICER',          name='Reinsurance Officer',          level=5, department=Department.REINSURANCE,
         description='Reinsurance day-to-day.',
         permissions=['re.view', 're.bordereau.process']),

    dict(code='OPERATIONS_OFFICER',  name='Operations Officer',           level=5, department=Department.OPERATIONS,
         description='Operations support.',
         permissions=['po.view', 'po.create', 'po.receive', 'invoice.view']),

    dict(code='HR_OFFICER',          name='HR Officer',                   level=5, department=Department.HR,
         description='HR day-to-day.',
         permissions=['hr.employee.view', 'hr.employee.edit']),

    dict(code='PAYROLL_OFFICER',     name='Payroll Officer',              level=5, department=Department.HR,
         description='Payroll preparation.',
         permissions=['hr.employee.view', 'hr.payroll.run']),

    dict(code='IT_HELPDESK',         name='IT Helpdesk',                  level=5, department=Department.IT,
         description='User support; password resets only.',
         permissions=['users.view', 'users.reset_password']),

    # ===== LEVEL 6 — READ-ONLY =====
    dict(code='BOARD_MEMBER',        name='Board Member',                 level=6, department=Department.EXTERNAL,
         description='Board-level read access to management reporting.',
         permissions=['report.management.view', 'report.financial.view', 'report.regulatory.view',
                      'risk.capital.view']),

    dict(code='EXTERNAL_AUDITOR',    name='External Auditor',             level=6, department=Department.EXTERNAL,
         description='External audit — full read of financial data.',
         permissions=['report.*', 'je.view', 'invoice.view', 'payment.view', 'po.view',
                      'claim.view', 'asset.view', 'bank.view', 'period.view',
                      'system.audit_log.view']),

    dict(code='NBFIRA_INSPECTOR',    name='NBFIRA Inspector',             level=6, department=Department.EXTERNAL,
         description='Regulator read access (regulatory + capital).',
         permissions=['report.regulatory.view', 'report.financial.view',
                      'risk.capital.view', 'system.audit_log.view',
                      'compliance.view']),

    # ===== LEVEL 9 — SYSTEM =====
    dict(code='SERVICE_ACCOUNT',     name='Service Account',              level=9, department=Department.SYSTEM,
         description='Non-human integration account (Graphite, Mailgun, etc.).',
         permissions=['je.post_direct', 'invoice.create', 'invoice.post',
                      'claim.create', 'payment.create', 'bank.feed.run']),
]


# ---------------------------------------------------------------------------
# Seeder
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = 'Seed the RBAC catalogue and bootstrap pganesharajah as SUPER_ADMIN. Idempotent.'

    def add_arguments(self, parser):
        parser.add_argument('--skip-bootstrap', action='store_true',
                            help='Seed roles and permissions only; skip pganesharajah bootstrap.')

    @transaction.atomic
    def handle(self, *args, **opts):
        self._seed_permissions()
        self._seed_roles()
        if not opts['skip_bootstrap']:
            self._bootstrap_super_admin()
        self.stdout.write(self.style.SUCCESS('seed_roles complete.'))

    # -- permissions -------------------------------------------------------

    def _seed_permissions(self):
        created = updated = 0
        for code, category, desc in PERMISSIONS:
            obj, was_created = Permission.objects.update_or_create(
                code=code,
                defaults={'category': category, 'description': desc, 'is_active': True},
            )
            created += int(was_created)
            updated += int(not was_created)
        self.stdout.write(f'  Permissions: {created} created, {updated} updated, {len(PERMISSIONS)} total')

    # -- roles -------------------------------------------------------------

    def _resolve_permissions(self, perm_spec):
        """Expand a role's permission spec (list of codes, wildcards, or ALL) into a queryset."""
        if perm_spec == ALL:
            return Permission.objects.filter(is_active=True)

        codes = set()
        for entry in perm_spec:
            if entry.endswith('.*'):
                category = entry[:-2]
                codes.update(
                    Permission.objects
                              .filter(is_active=True, code__startswith=f'{category}.')
                              .values_list('code', flat=True)
                )
            else:
                codes.add(entry)
        # Validate every code exists; fail loudly to catch typos in the catalogue
        missing = codes - set(Permission.objects.filter(code__in=codes).values_list('code', flat=True))
        if missing:
            raise ValueError(f'Permission codes referenced but not in catalogue: {sorted(missing)}')
        return Permission.objects.filter(code__in=codes)

    def _seed_roles(self):
        created = updated = 0
        for spec in ROLES:
            perms = self._resolve_permissions(spec['permissions'])
            role, was_created = Role.objects.update_or_create(
                code=spec['code'],
                defaults={
                    'name':        spec['name'],
                    'description': spec['description'],
                    'level':       spec['level'],
                    'department':  spec['department'],
                    'is_system':   True,
                    'is_active':   True,
                },
            )
            role.permissions.set(perms)
            created += int(was_created)
            updated += int(not was_created)
        self.stdout.write(f'  Roles:       {created} created, {updated} updated, {len(ROLES)} total')

    # -- bootstrap ---------------------------------------------------------

    def _bootstrap_super_admin(self):
        email = 'pganesharajah@alphadirect.co.bw'
        username = 'pganesharajah'

        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                'email':      email,
                'first_name': 'Prathap',
                'last_name':  'Ganesharajah',
                'is_active':  True,
                'is_staff':   True,    # admin panel access
            },
        )
        # Ensure email is current (in case username existed under a different email)
        if user.email != email:
            user.email = email
            user.save(update_fields=['email'])

        if created:
            user.set_unusable_password()  # SSO will handle auth once wired
            user.save()
            self.stdout.write(f'  User       : created {username} (no password — set via SSO or reset)')
        else:
            self.stdout.write(f'  User       : {username} already exists')

        # Ensure UserProfile exists with CFO title + admin (so legacy code grants approval rights)
        UserProfile.objects.update_or_create(
            user=user,
            defaults={
                'role':             UserProfile.Role.FINANCE_ADMIN,
                'title':            UserProfile.Title.CFO,
                'is_administrator': True,
                'is_active':        True,
            },
        )

        # Assign SUPER_ADMIN role if not already (via service → audit-logged)
        super_admin = Role.objects.get(code='SUPER_ADMIN')
        existing = UserRoleAssignment.objects.filter(
            user=user, role=super_admin, revoked_at__isnull=True,
        ).first()
        if existing and existing.is_currently_active:
            self.stdout.write('  Assignment : SUPER_ADMIN already active')
        else:
            assign_role(
                target_user=user,
                role=super_admin,
                granted_by=None,
                justification=(
                    'Bootstrap of initial Super Administrator during RBAC rollout 2026-05-12. '
                    'Holder is the CFO per CLAUDE.md; subsequent Super Admin grants must follow '
                    'the standard governance workflow (peer grant + justification).'
                ),
                notes='Bootstrap — initial Super Admin',
                bypass_hierarchy=True,  # no granter exists yet; flagged in audit log
            )
            self.stdout.write('  Assignment : SUPER_ADMIN granted to pganesharajah (audit-logged)')
