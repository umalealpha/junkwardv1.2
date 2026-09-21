from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customer_refunds', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='customerrefund',
            name='ai_fraud_review',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
