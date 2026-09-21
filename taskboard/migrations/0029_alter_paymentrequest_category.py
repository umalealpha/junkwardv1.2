from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('taskboard', '0028_paymentrequest_cancelled_at_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='paymentrequest',
            name='category',
            field=models.CharField(
                blank=True,
                choices=[
                    ('claim', 'Claim payments'),
                    ('supplier', 'Supplier payments'),
                    ('vendor', 'Vendor payments'),
                    ('petty_cash', 'Petty cash'),
                    ('premium_refund', 'Premium refunds'),
                    ('unicoin', 'Unicoin payments'),
                    ('quantum', 'Quantum payments'),
                    ('rsa', 'Risk Software Africa'),
                    ('veritas', 'Veritas Capital Mgmt'),
                    ('adh', 'Alpha Direct Health'),
                    ('gce', 'GCE payments'),
                    ('other', 'Other'),
                ],
                default='',
                max_length=20,
            ),
        ),
    ]
