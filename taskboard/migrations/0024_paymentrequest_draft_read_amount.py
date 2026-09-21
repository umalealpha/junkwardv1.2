from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('taskboard', '0023_paymentrequest_draft'),
    ]

    operations = [
        migrations.AddField(
            model_name='paymentrequest',
            name='draft_read_amount',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=16, null=True),
        ),
    ]
