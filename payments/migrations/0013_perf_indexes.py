from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0012_payment_bank_submitted'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='payment',
            index=models.Index(fields=['status', '-payment_date'], name='payment_status_date_idx'),
        ),
        migrations.AddIndex(
            model_name='payment',
            index=models.Index(fields=['-created_at'], name='payment_created_at_idx'),
        ),
    ]
