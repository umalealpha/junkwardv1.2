"""Idempotent seed for the reconciliation metric-source map (Phase 1).

Metrics (CFO 2026-07-03): GWP, Premium Debtors, Claims, Policy count.
Scope = ADIC incl. Instant (Instant accounts live inside ADIC's chart).

    python manage.py setup_recon_metric_map
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from ledger.models import Account
from reconciliation_hub.constants import MetricKey, FlowType, Unit, SourceSystem
from reconciliation_hub.models import MetricSourceMap

User = get_user_model()

# metric_key -> config. account_codes are linked if present in the CoA.
SEED = [
    {
        'metric_key': MetricKey.GWP,
        'label': 'Gross Written Premium',
        'unit': Unit.BWP,
        'flow_type': FlowType.PERIOD,
        'account_codes': ['4100'],
        'include_all_receivable': False,
        'source_system': SourceSystem.REGISTER,
        'source_ref': 'Reporting portal: written-premium (Phase 2 auto); register for now',
        'sort_order': 10,
    },
    {
        'metric_key': MetricKey.PREMIUM_DEBTORS,
        'label': 'Premium Debtors (ageing)',
        'unit': Unit.BWP,
        'flow_type': FlowType.BALANCE,
        'account_codes': [],
        'include_all_receivable': True,   # covers 1210 + 78 (Instant A/R) + others
        'source_system': SourceSystem.GRAPHITE_RDS,
        'source_ref': 'Graphite replica summary_age_analyst_report_dom_com (Dom-Com only)',
        'sort_order': 20,
    },
    {
        'metric_key': MetricKey.CLAIMS,
        'label': 'Claims Incurred (gross)',
        'unit': Unit.BWP,
        'flow_type': FlowType.PERIOD,
        'account_codes': ['5100', '5110', '103000'],
        'include_all_receivable': False,
        'source_system': SourceSystem.REGISTER,
        'source_ref': 'Reporting portal: claims-as-on-date (Phase 2 auto); register for now',
        'sort_order': 30,
    },
    {
        'metric_key': MetricKey.POLICY_COUNT,
        'label': 'Policy Count',
        'unit': Unit.COUNT,
        'flow_type': FlowType.NONE,       # no GL side — count only
        'account_codes': [],
        'include_all_receivable': False,
        'source_system': SourceSystem.GRAPHITE_RDS,
        'source_ref': 'Graphite replica policy count (Dom-Com); portal cross-check Phase 2',
        'sort_order': 40,
    },
]


class Command(BaseCommand):
    help = 'Seed/refresh the reconciliation metric-source map (idempotent).'

    def handle(self, *args, **options):
        audit_user = User.objects.filter(is_superuser=True).order_by('date_joined').first()
        created, updated, missing = 0, 0, []

        for spec in SEED:
            codes = spec['account_codes']
            fields = {k: v for k, v in spec.items() if k != 'account_codes'}
            obj, was_created = MetricSourceMap.objects.get_or_create(
                metric_key=fields['metric_key'], defaults=fields,
            )
            if not was_created:
                for k, v in fields.items():
                    setattr(obj, k, v)
            obj.save(audit_user=audit_user)

            if codes:
                accts = list(Account.objects.filter(code__in=codes))
                found = {a.code for a in accts}
                missing += [c for c in codes if c not in found]
                obj.accounts.set(accts)
            else:
                obj.accounts.clear()

            created += int(was_created)
            updated += int(not was_created)
            self.stdout.write(f'  {"+" if was_created else "~"} {obj.metric_key}: {obj.label}')

        self.stdout.write(self.style.SUCCESS(
            f'Metric map seeded. created={created} updated={updated}'))
        if missing:
            self.stdout.write(self.style.WARNING(
                f'CoA codes not found (skipped, wire when present): {sorted(set(missing))}'))
