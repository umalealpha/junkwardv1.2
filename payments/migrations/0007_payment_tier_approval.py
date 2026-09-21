# PAY-003 tier maker-checker on payments (CFO/Oprah directive 2026-05-27)

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0006_seed_discount_received_account'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='approval_status',
            field=models.CharField(
                choices=[
                    ('not_required', 'Not required'),
                    ('pending', 'Pending approval'),
                    ('approved', 'Approved'),
                    ('rejected', 'Rejected'),
                ],
                default='not_required', max_length=15),
        ),
        migrations.AddField(
            model_name='payment',
            name='approval_tier',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='approval_comment',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='payment',
            name='submitted_for_approval_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='payments_submitted',
                to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='payment',
            name='submitted_for_approval_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='approval_decided_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='payments_approval_decided',
                to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='payment',
            name='approval_decided_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
