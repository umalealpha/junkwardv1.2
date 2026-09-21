"""Leave administration (Unami Butale, HR, 2026-07-27).

Additive fields on LeaveRequest:
  * reason_category — fixed reason pick-list (replaces the forced free-text why).
  * hr_review_state / hr_reviewer / hr_reviewed_at / hr_review_notes — HR dual-
    approval (verify/flag) stage for manager-approved sick leave.

All nullable / defaulted → no backfill, legacy rows behave exactly as before.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('hris', '0054_rosterflag'),
    ]

    operations = [
        migrations.AddField(
            model_name='leaverequest',
            name='reason_category',
            field=models.CharField(
                blank=True, default='', max_length=16,
                choices=[
                    ('personal', 'Personal / rest'),
                    ('family', 'Family responsibility'),
                    ('medical', 'Medical / health'),
                    ('bereavement', 'Bereavement'),
                    ('travel', 'Travel'),
                    ('religious', 'Religious / cultural'),
                    ('study', 'Study / exams'),
                    ('other', 'Other'),
                    ('undisclosed', 'Prefer not to say'),
                ],
                help_text='Fixed reason category (Unami 2026-07-27). Free-text reason is optional.',
            ),
        ),
        migrations.AddField(
            model_name='leaverequest',
            name='hr_review_state',
            field=models.CharField(
                db_index=True, default='not_required', max_length=12,
                choices=[
                    ('not_required', 'No HR review needed'),
                    ('pending', 'Awaiting HR verification'),
                    ('verified', 'HR verified'),
                    ('flagged', 'HR flagged'),
                ],
            ),
        ),
        migrations.AddField(
            model_name='leaverequest',
            name='hr_reviewer',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='leave_hr_reviews', to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='leaverequest',
            name='hr_reviewed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='leaverequest',
            name='hr_review_notes',
            field=models.TextField(blank=True, default=''),
        ),
    ]
