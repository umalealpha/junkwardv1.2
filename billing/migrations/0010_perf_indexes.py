from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0009_billapprovalpolicy_tiers'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='invoice',
            index=models.Index(fields=['status', 'due_date'], name='invoice_status_due_idx'),
        ),
        migrations.AddIndex(
            model_name='invoice',
            index=models.Index(fields=['-created_at'], name='invoice_created_at_idx'),
        ),
    ]
