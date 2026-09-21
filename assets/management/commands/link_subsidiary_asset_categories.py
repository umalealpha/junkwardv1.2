"""
assets/management/commands/link_subsidiary_asset_categories.py

CFO directive 2026-05-19. The FAR audit revealed every non-ADIC Asset row was
linked to AssetCategory `ADIC-210002 Motor Vehicles` because commit_ppe
(core/smart_upload/committers.py) picked the first category by code as the
default. The category's GL FKs point to ADIC accounts, so the FAR-vs-GL
audit shows GL=0 for AIZ/RSA/UNI/VCM even though those subs have postings on
their own prefixed accounts.

This command creates one umbrella AssetCategory per subsidiary, pointing to
that subsidiary's own cost / accum-dep / dep-expense accounts, then re-links
the subsidiary's Asset rows away from the ADIC default to the correct
sub-specific category.

Strictly a configuration / linkage fix:
  - No JE postings
  - No changes to Asset.cost or any book values
  - No modification to ADIC's existing 7 categories

Usage:
    python manage.py link_subsidiary_asset_categories            # dry-run
    python manage.py link_subsidiary_asset_categories --commit   # apply
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


# Umbrella category mapping per subsidiary, chosen from the subsidiary's
# own CoA after inspecting which accounts hold real balances:
#     AIZ_151002 has 78,530 Dr      RSA_151000 = Fixed Asset
#     UNI_151000 = Fixed Asset      VCM_151000 = Fixed Asset
#
# Each tuple is (cost_account_code, accum_depr_account_code,
#                depreciation_expense_account_code).
SUBSIDIARY_MAPPING = {
    'AIZ': ('AIZ_151002', 'AIZ_151002.1',  'AIZ_800000'),
    'RSA': ('RSA_151000', 'RSA_151002.01', 'RSA_800000'),
    'UNI': ('UNI_151000', 'UNI_110401.01', 'UNI_800000'),
    'VCM': ('VCM_151000', 'VCM_152000',    'VCM_600040'),
}


class Command(BaseCommand):
    help = (
        'Create umbrella AssetCategory per subsidiary and re-link mis-linked '
        'Asset rows. CFO directive 2026-05-19. Idempotent.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--commit', action='store_true',
            help='Actually apply changes. Without this flag the command is a dry-run.',
        )

    def handle(self, *args, **opts):
        from assets.models import Asset, AssetCategory
        from core.models import Company
        from ledger.models import Account

        commit = opts['commit']
        adic_default_code = 'ADIC-210002'

        # Pre-flight: make sure no ADIC category will be touched.
        if any(code.startswith('ADIC') for code in SUBSIDIARY_MAPPING):
            raise CommandError('SUBSIDIARY_MAPPING must not contain ADIC.')

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'link_subsidiary_asset_categories commit={commit}'
        ))

        results = []
        with transaction.atomic():
            for sub_code, (cost_code, dep_code, exp_code) in SUBSIDIARY_MAPPING.items():
                company = Company.objects.filter(code=sub_code).first()
                if not company:
                    self.stdout.write(self.style.WARNING(
                        f'[{sub_code}] company missing — skip'
                    ))
                    continue

                # Resolve the three accounts; bail out if any missing.
                cost_acct = Account.objects.filter(code=cost_code).first()
                dep_acct = Account.objects.filter(code=dep_code).first()
                exp_acct = Account.objects.filter(code=exp_code).first()
                missing = [
                    label for label, obj in (
                        (cost_code, cost_acct),
                        (dep_code, dep_acct),
                        (exp_code, exp_acct),
                    ) if obj is None
                ]
                if missing:
                    self.stdout.write(self.style.WARNING(
                        f'[{sub_code}] accounts missing: {missing} — skip'
                    ))
                    continue

                cat_code = f'{sub_code}-FAR'
                cat_name = f'{sub_code} Fixed Assets (umbrella)'
                cat, was_created = AssetCategory.objects.get_or_create(
                    code=cat_code,
                    defaults={
                        'name': cat_name,
                        'cost_account': cost_acct,
                        'accum_depr_account': dep_acct,
                        'depreciation_expense_account': exp_acct,
                    },
                )
                # If exists but FKs point elsewhere, repoint (still no ADIC
                # involved — `ADIC-*` codes never enter this branch).
                changed_fields = []
                if cat.cost_account_id != cost_acct.id:
                    cat.cost_account = cost_acct
                    changed_fields.append('cost_account')
                if cat.accum_depr_account_id != dep_acct.id:
                    cat.accum_depr_account = dep_acct
                    changed_fields.append('accum_depr_account')
                if cat.depreciation_expense_account_id != exp_acct.id:
                    cat.depreciation_expense_account = exp_acct
                    changed_fields.append('depreciation_expense_account')
                if changed_fields and not was_created:
                    cat.save(update_fields=changed_fields)

                # Re-link only sub-owned assets currently pointing at the ADIC
                # default. Don't touch ADIC assets, don't touch sub assets
                # already linked to a non-default category.
                to_relink = Asset.objects.filter(
                    company=company, category__code=adic_default_code,
                )
                n = to_relink.count()
                far_cost = sum((a.cost or 0) for a in to_relink)
                if n and commit:
                    to_relink.update(category=cat)

                results.append({
                    'sub': sub_code,
                    'category_code': cat_code,
                    'category_was_created': was_created,
                    'category_changed_fields': changed_fields,
                    'assets_relinked': n,
                    'far_cost_total': str(far_cost),
                    'cost_account': cost_code,
                    'accum_depr_account': dep_code,
                    'depreciation_expense_account': exp_code,
                })

            if not commit:
                transaction.set_rollback(True)

        # Report
        for r in results:
            mark = 'CREATED' if r['category_was_created'] else 'EXISTS '
            self.stdout.write(
                f"[{r['sub']}] {mark} {r['category_code']:14} "
                f"cost={r['cost_account']:18} dep={r['accum_depr_account']:18} "
                f"exp={r['depreciation_expense_account']:14} "
                f"relinked={r['assets_relinked']:4} far_total={r['far_cost_total']}"
            )
            if r['category_changed_fields']:
                self.stdout.write(
                    f"    re-pointed FKs: {r['category_changed_fields']}"
                )

        if not commit:
            self.stdout.write(self.style.WARNING(
                'DRY RUN — re-run with --commit to apply.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('Applied.'))
