# Workforce Brief control gap (Fable review 2026-07-14): self-reported
# explanations now park as EXPLAINED pending manager sign-off, with the
# reviewer recorded. ONLY WorkdayJustification is touched here — makemigrations
# also wanted to emit unrelated pre-existing drift (monthlycheckin / PIP /
# payroll help-texts); that drift belongs to its own change, not this one.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0029_workforcebriefsetting'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='workdayjustification',
            name='review_note',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='workdayjustification',
            name='reviewed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='workdayjustification',
            name='reviewed_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='workdayjustification',
            name='status',
            field=models.CharField(choices=[('met', 'Met required hours'), ('not_required', 'No hours required (off day)'), ('justified', 'Shortfall justified'), ('unjustified', 'Shortfall NOT justified'), ('pending', 'Awaiting employee response'), ('explained', 'Explained — pending manager review')], default='pending', max_length=14),
        ),
    ]
