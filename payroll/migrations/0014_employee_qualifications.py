from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0013_paymentamendmentbatch_applied_by'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='qualifications',
            field=models.TextField(blank=True, default=''),
        ),
    ]
