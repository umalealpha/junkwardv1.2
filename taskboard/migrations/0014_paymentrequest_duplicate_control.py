# Duplicate payment control — PAY-DUP-01 (CFO 2026-08-03).
#
# The CFO's authorisation queue held ten requests totalling BWP 789,626.85, and
# BWP 219,600.10 of that was already paid or double-counted: PAY/ADIC/2026/07/29/0002
# was a strict subset of 0003, five lines of 07/28/0001 had been settled days
# earlier on 07/27/0001 and 07/28/0002, and G2025003601 CHOPPIES 42,380.04 sat on
# two pending requests at once. PAY-SUP-01 checks an invoice is DUE; nothing ever
# asked whether it was already PAID.
#
# Every line is now matched on reference AND amount against every live and paid
# request. These two fields record the escape hatch and the evidence:
#   duplicate_override_reason — written justification, the only way past a clash
#                               (a payment marked paid in Omni can still be
#                               rejected by the bank and need re-raising).
#   duplicate_matches         — the clashes as they stood when the request was
#                               raised, so the trail survives the other request
#                               changing state later.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('taskboard', '0013_paymentrequest_claim_payee_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='paymentrequest',
            name='duplicate_override_reason',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='paymentrequest',
            name='duplicate_matches',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
