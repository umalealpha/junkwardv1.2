from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('taskboard', '0022_paymentrequest_loaded_off_window'),
    ]

    operations = [
        migrations.AlterField(
            model_name='paymentrequest',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Draft — not yet submitted'),
                    ('pending_finance', 'Pending finance sign-off'),
                    ('pending_cfo', 'Pending CFO authorisation'),
                    ('rejected', 'Rejected at finance sign-off'),
                    ('paid', 'Paid / authorised'),
                    ('cancelled', 'Cleared / cancelled'),
                ],
                db_index=True, default='pending_finance', max_length=16),
        ),
        migrations.AddField(
            model_name='paymentrequest',
            name='draft_source_file',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='paymentrequest',
            name='draft_needs_check',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
