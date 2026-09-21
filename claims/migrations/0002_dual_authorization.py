# Hand-authored migration — adds dual-authorisation columns + status values
# to RecoveryImportBatch.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('claims', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='recoveryimportbatch',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Draft (preview)'),
                    ('partially_approved', 'Awaiting second approval'),
                    ('approved', 'Fully approved — ready to commit'),
                    ('committed', 'Committed'),
                    ('rejected', 'Rejected'),
                    ('failed', 'Failed'),
                ],
                default='draft', max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='recoveryimportbatch',
            name='first_approved_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='recovery_imports_first_approved',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='recoveryimportbatch',
            name='first_approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='recoveryimportbatch',
            name='second_approved_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='recovery_imports_second_approved',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='recoveryimportbatch',
            name='second_approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='recoveryimportbatch',
            name='rejected_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='recovery_imports_rejected',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='recoveryimportbatch',
            name='rejected_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='recoveryimportbatch',
            name='rejection_reason',
            field=models.TextField(blank=True, default=''),
        ),
    ]
