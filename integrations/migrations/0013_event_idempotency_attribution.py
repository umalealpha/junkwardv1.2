from django.db import migrations, models


class Migration(migrations.Migration):
    """Idempotency key + service attribution on IntegrationEvent.

    Manus nine-area retest P0 (2026-08-25): processing an inbound event CREATES a
    customer invoice, vendor bill or credit note. A redelivered or retried push
    would have booked the same revenue twice with nothing to stop it, and there
    was no record of WHICH integration submitted an event (the records it raises
    are stamped with the first superuser by `_system_user()`).

    The unique constraint is conditional so the existing blank/legacy rows do not
    collide with one another — only a real supplied key is held unique.
    """

    dependencies = [
        ('integrations', '0012_graphitesnapshot'),
    ]

    operations = [
        migrations.AddField(
            model_name='integrationevent',
            name='idempotency_key',
            field=models.CharField(
                blank=True, db_index=True, default='', max_length=128,
                help_text="Sender's own unique id for this event. A repeat push "
                          "with the same key returns the original event instead "
                          "of creating a second accounting record. Blank = not "
                          "supplied (legacy)."),
        ),
        migrations.AddField(
            model_name='integrationevent',
            name='received_via',
            field=models.CharField(
                blank=True, default='', max_length=120,
                help_text='Label of the API key that submitted this event.'),
        ),
        migrations.AddConstraint(
            model_name='integrationevent',
            constraint=models.UniqueConstraint(
                condition=models.Q(('idempotency_key', ''), _negated=True),
                fields=('idempotency_key',),
                name='intgevent_idempotency_key_uniq',
            ),
        ),
    ]
