"""Add OmniTask.performance_points_awarded — staff-rewards gamification.

CFO directive 2026-07-13: task performance feedback (Done/Partial/Not-done)
feeds the assignee's Staff Rewards score. This field records how many points a
task has credited so far, so re-feedback reconciles by delta (never double-counts;
a task flipped back to Not-done forfeits what it earned).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0036_omnitask_completion_pct'),
    ]

    operations = [
        migrations.AddField(
            model_name='omnitask',
            name='performance_points_awarded',
            field=models.IntegerField(
                default=0,
                help_text='Staff-rewards points this task has credited so far.',
            ),
        ),
    ]
