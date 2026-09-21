# Bank reconciliation maker-checker (BUG b72695a8) — adds reconciled_by /
# approved_by / approved_at and the PENDING_APPROVAL status choice.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('banking', '0003_bankaccount_hide_in_banking_ui'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='bankstatement',
            name='reconciled_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='reconciled_statements',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='bankstatement',
            name='approved_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='approved_statements',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='bankstatement',
            name='approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='bankstatement',
            name='status',
            field=models.CharField(
                choices=[
                    ('imported', 'Imported'),
                    ('in_progress', 'Reconciliation in progress'),
                    ('pending_approval', 'Reconciled — awaiting approval'),
                    ('reconciled', 'Reconciled & approved'),
                ],
                default='imported', max_length=20,
            ),
        ),
    ]
