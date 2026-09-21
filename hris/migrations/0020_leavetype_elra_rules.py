# ELRA 2025 statutory encoding for LeaveType (HRIS blueprint, ref ADI/HC/HRIS/2026).
# Additive columns only — externalises the leave-engine rules so a statutory
# change is a data edit (admin), not a code deploy. All have safe defaults, so
# existing rows are unaffected until seeded (see manage.py seed_elra_leave).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hris", "0019_hrisprofile_personal_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="leavetype",
            name="country_code",
            field=models.CharField(default="BW", max_length=2,
                                   help_text="ISO country — statutory rules differ by jurisdiction (BW/RSA/ZA)."),
        ),
        migrations.AddField(
            model_name="leavetype",
            name="accrual_method",
            field=models.CharField(
                choices=[("monthly", "Accrues monthly"),
                         ("frontload", "Full entitlement available up front")],
                default="frontload", max_length=10,
                help_text="'monthly' accrues 1/12 per month (annual s.219); "
                          "'frontload' = full entitlement immediately (sick s.220)."),
        ),
        migrations.AddField(
            model_name="leavetype",
            name="statutory_min_days",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="ELRA statutory floor. The company entitlement "
                          "(default_annual_days) may exceed it."),
        ),
        migrations.AddField(
            model_name="leavetype",
            name="min_mandatory_take_days",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="ELRA s.219: 8 annual days must be taken within 6 months of cycle end."),
        ),
        migrations.AddField(
            model_name="leavetype",
            name="leave_window_weeks",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="Window within which the leave must be taken — paternity s.227 = 14."),
        ),
        migrations.AddField(
            model_name="leavetype",
            name="blocks_termination",
            field=models.BooleanField(
                default=False,
                help_text="ELRA s.224: no termination notice while on this leave (maternity)."),
        ),
        migrations.AddField(
            model_name="leavetype",
            name="proof_type",
            field=models.CharField(
                blank=True, default="", max_length=40,
                help_text="Document required — e.g. medical_certificate, birth_certificate."),
        ),
        migrations.AddField(
            model_name="leavetype",
            name="statutory_ref",
            field=models.CharField(
                blank=True, default="", max_length=40,
                help_text='Governing-law reference shown to staff — e.g. "ELRA s.222".'),
        ),
    ]
