from django.db import migrations, models


class Migration(migrations.Migration):
    """A second reminder in the feedback cycle (CFO 2026-09-09).

    Choices only — no data change. The cycle moved from 5th/6th/7th to
    2nd/4th/7th/10th and gained a second reminder, so `kind` needs the extra
    value. Existing rows keep their stored values.
    """

    dependencies = [
        ('hris', '0084_manager_objectives'),
    ]

    operations = [
        migrations.AlterField(
            model_name='monthlyfeedbacknotice',
            name='kind',
            field=models.CharField(
                choices=[('notice', 'Facts sent to the manager (2nd)'),
                         ('reminder', 'First reminder (4th)'),
                         ('reminder2', 'Second reminder (7th)'),
                         ('autopost', 'Auto-posted (10th)')],
                max_length=10),
        ),
    ]
