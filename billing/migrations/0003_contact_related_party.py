# Hand-authored migration — adds related-party fields to Contact (IAS 24).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0002_invoice_company'),
    ]

    operations = [
        migrations.AddField(
            model_name='contact',
            name='is_related_party',
            field=models.BooleanField(
                default=False,
                help_text='True if this party is related under IAS 24 — subsidiaries, key '
                          'management personnel, their close family, or entities they control '
                          'or significantly influence.',
            ),
        ),
        migrations.AddField(
            model_name='contact',
            name='related_party_relationship',
            field=models.CharField(
                blank=True, default='', max_length=200,
                help_text='Nature of the relationship — e.g. "Director", "Subsidiary", '
                          '"Spouse of CFO", "Common control".',
            ),
        ),
    ]
