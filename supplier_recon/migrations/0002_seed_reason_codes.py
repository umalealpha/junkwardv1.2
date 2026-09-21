"""Seed the standard payables reason codes.

Done as a migration rather than a `loaddata` step so a deploy cannot land with
an empty reason-code list — which would leave the payables team unable to
explain a single bill, and the month unable to close.

Idempotent: existing codes keep whatever the CFO has since changed (label,
escalation flag, active). Only missing codes are added.
"""

from django.db import migrations

REASON_CODES = [
    # (code, label, group, requires_escalation)
    ('QUERY_DISPUTE', 'Bill queried / in dispute with supplier', 'dispute', False),
    ('DOCS_MISSING', 'Missing PO, goods receipt or valid tax invoice', 'docs_missing', False),
    ('SALVAGE_NOT_IN_YARD', 'Salvage not yet physically in the yard', 'salvage_not_in_yard', True),
    ('AWAITING_APPROVAL', 'Awaiting internal approval', 'awaiting_approval', False),
    ('NO_PO', 'No purchase order raised', 'no_po', True),
    ('NO_CLAIM_AUTH', 'No claim authorisation on the PO', 'docs_missing', True),
    ('CASHFLOW_HOLD', 'Deferred - cash-flow / funding hold', 'funds', True),
    ('SUSPECTED_DUPLICATE', 'Suspected duplicate bill', 'duplicate', True),
    ('PRICE_VARIANCE', 'Bill exceeds the PO - variance unresolved', 'dispute', True),
    ('WORK_INCOMPLETE', 'Repair / delivery not complete', 'dispute', False),
    ('BANK_DETAILS', 'Supplier bank details unverified', 'docs_missing', False),
    ('FX_CONFIRM', 'Awaiting FX / rate confirmation', 'fx', False),
    ('PAID_AFTER_CUTOFF', 'Paid after the 09:00 cut-off - settles next run',
     'awaiting_approval', False),
    ('OTHER', 'Other (see justification)', 'other', False),
]


def seed(apps, schema_editor):
    ReasonCode = apps.get_model('supplier_recon', 'ReasonCode')
    for code, label, group, escalates in REASON_CODES:
        ReasonCode.objects.get_or_create(
            code=code,
            defaults={'label': label, 'group': group,
                      'requires_escalation': escalates, 'active': True},
        )


def unseed(apps, schema_editor):
    """Remove only codes never used by a payables decision."""
    ReasonCode = apps.get_model('supplier_recon', 'ReasonCode')
    (ReasonCode.objects
     .filter(code__in=[c for c, *_ in REASON_CODES], items__isnull=True)
     .delete())


class Migration(migrations.Migration):

    dependencies = [
        ('supplier_recon', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
