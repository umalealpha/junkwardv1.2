"""Add the BILL_NOT_POSTED reason code.

Needed once draft bills joined the board (see constants.LedgerStage). On the
first prod build every vendor bill in omni was still a draft, so payables need a
standard way to say why — and it escalates, because a bill raised weeks ago and
never posted is not a clerical detail, it is unrecorded liability.
"""

from django.db import migrations

CODE = 'BILL_NOT_POSTED'


def seed(apps, schema_editor):
    ReasonCode = apps.get_model('supplier_recon', 'ReasonCode')
    ReasonCode.objects.get_or_create(
        code=CODE,
        defaults={
            'label': 'Bill still in draft - not yet posted to the ledger',
            'group': 'docs_missing',
            'requires_escalation': True,
            'active': True,
        },
    )


def unseed(apps, schema_editor):
    ReasonCode = apps.get_model('supplier_recon', 'ReasonCode')
    ReasonCode.objects.filter(code=CODE, items__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('supplier_recon', '0003_ledger_stage'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
