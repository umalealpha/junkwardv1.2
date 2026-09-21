# Generated for the claims-vs-operations payment question (CFO 2026-07-29).
#
# The supplier terms gate (PAY-SUP-01, migration 0012) only bit on
# category=supplier, but a repairer or parts supplier billing us on a claim is
# raised as a CLAIM payment and posted to claims payable — so the packs the
# control existed for were sailing straight through with no invoice date and no
# due date. The raiser is now asked whether the payment is claims or operations,
# and on a claims payment, who is being paid: the client direct (no invoice) or
# a supplier / repairer (invoice required, gated).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('taskboard', '0012_paymentrequest_supplier_terms'),
    ]

    operations = [
        migrations.AddField(
            model_name='paymentrequest',
            name='claim_payee_type',
            field=models.CharField(
                blank=True, default='', max_length=10,
                choices=[('client', 'The client / policyholder direct'),
                         ('provider', 'A supplier, repairer or service provider')],
            ),
        ),
    ]
