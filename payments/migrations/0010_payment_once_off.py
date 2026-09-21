from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0009_payment_approval'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='is_once_off',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='payment',
            name='payee_name',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='payment',
            name='payee_bank_name',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='payment',
            name='payee_account_number',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
        migrations.AddField(
            model_name='payment',
            name='payee_branch_code',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
    ]
