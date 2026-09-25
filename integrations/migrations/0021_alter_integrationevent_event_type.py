from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the WS1 harness 'bus.ping' choice to IntegrationEvent.event_type.

    Choices are not a database constraint, so this is a state-only change — no
    column is altered — but it keeps `makemigrations` clean.
    """

    dependencies = [
        ('integrations', '0020_outboundevent'),
    ]

    operations = [
        migrations.AlterField(
            model_name='integrationevent',
            name='event_type',
            field=models.CharField(
                max_length=50,
                choices=[
                    ('policy_issued', 'Policy Issued'),
                    ('policy_cancelled', 'Policy Cancelled'),
                    ('claim_approved', 'Claim Approved'),
                    ('commission_calculated', 'Commission Calculated'),
                    ('bank_transaction', 'Bank Transaction'),
                    ('claim_registered', 'Claim Registered'),
                    ('form_submitted', 'Customer Form Submitted'),
                    ('premium_checked', 'Premium Checked'),
                    ('assessment_received', 'Assessment Received'),
                    ('write_off_flagged', 'Write-off Flagged'),
                    ('decision_recorded', 'Decision Recorded'),
                    ('bus.ping', 'Bus Ping (WS1 harness)'),
                ],
            ),
        ),
    ]
