"""HR request 2026-07-01 (Unami): capture fuller employee personal data on the
HRIS profile — marital status, passport/permit, disabilities, allergies and an
emergency contact. All additive, optional (blank/default), no backfill."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0018_hrdocument_resignation_category'),
    ]

    operations = [
        migrations.AddField(
            model_name='hrisprofile',
            name='marital_status',
            field=models.CharField(blank=True, default='', max_length=10, choices=[
                ('single', 'Single'), ('married', 'Married'), ('divorced', 'Divorced'),
                ('widowed', 'Widowed'), ('other', 'Other / prefer not to say')]),
        ),
        migrations.AddField(
            model_name='hrisprofile',
            name='passport_number',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
        migrations.AddField(
            model_name='hrisprofile',
            name='permit_number',
            field=models.CharField(blank=True, default='', max_length=40,
                                   help_text='Work / residence permit number (non-citizens).'),
        ),
        migrations.AddField(
            model_name='hrisprofile',
            name='disabilities',
            field=models.TextField(blank=True, default='', help_text='Optional, HR-only. Never required.'),
        ),
        migrations.AddField(
            model_name='hrisprofile',
            name='allergies',
            field=models.TextField(blank=True, default='', help_text='Optional, HR-only. Never required.'),
        ),
        migrations.AddField(
            model_name='hrisprofile',
            name='emergency_contact_name',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='hrisprofile',
            name='emergency_contact_phone',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
        migrations.AddField(
            model_name='hrisprofile',
            name='emergency_contact_relationship',
            field=models.CharField(blank=True, default='', max_length=60),
        ),
        # New document-vault categories (CV, certificate, pre-boarding) so HR can
        # file those per Unami's request. Choices-only change (no DB column change).
        migrations.AlterField(
            model_name='hrdocument',
            name='category',
            field=models.CharField(db_index=True, default='onboarding', max_length=20, choices=[
                ('onboarding', 'Onboarding'), ('preboarding', 'Pre-boarding pack'),
                ('cv', 'CV / résumé'), ('certificate', 'Certificate / qualification'),
                ('policy', 'Policy'), ('contract', 'Contract / agreement'),
                ('resignation', 'Resignation'), ('exit_interview', 'Exit interview'),
                ('disciplinary', 'Disciplinary'), ('other', 'Other')]),
        ),
    ]
