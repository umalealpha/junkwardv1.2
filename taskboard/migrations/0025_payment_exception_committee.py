# Generated for the payment EXCEPTION committee (CFO 2026-09-02).
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('taskboard', '0024_paymentrequest_draft_read_amount'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='paymentrequest',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Draft — not yet submitted'),
                    ('exception', 'Exception — with the committee'),
                    ('pending_finance', 'Pending finance sign-off'),
                    ('pending_cfo', 'Pending CFO authorisation'),
                    ('rejected', 'Rejected at finance sign-off'),
                    ('paid', 'Paid / authorised'),
                    ('cancelled', 'Cleared / cancelled'),
                ],
                db_index=True, default='pending_finance', max_length=16),
        ),
        migrations.AddField(
            model_name='paymentrequest',
            name='exception_control',
            field=models.CharField(blank=True, default='', help_text='e.g. PAY-BANK-01', max_length=20),
        ),
        migrations.AddField(
            model_name='paymentrequest',
            name='exception_reason',
            field=models.TextField(blank=True, default='', help_text='the plain message shown to the raiser'),
        ),
        migrations.AddField(
            model_name='paymentrequest',
            name='exception_raised_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='paymentrequest',
            name='exception_decision',
            field=models.CharField(
                blank=True,
                choices=[('approve', 'Approved by committee'), ('reject', 'Rejected by committee')],
                default='', max_length=8),
        ),
        migrations.AddField(
            model_name='paymentrequest',
            name='exception_decided_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='paymentrequest',
            name='exception_cleared_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='paymentrequest',
            name='exception_cleared_by',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='payment_exceptions_cleared', to=settings.AUTH_USER_MODEL),
        ),
        migrations.CreateModel(
            name='PaymentReleaseSignoff',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('signer_email', models.CharField(blank=True, default='', max_length=254)),
                ('decision', models.CharField(choices=[('approve', 'Approve'), ('reject', 'Reject')], max_length=8)),
                ('is_independent', models.BooleanField(default=False, help_text='signer is on the independent side (Legakwa / Oprah)')),
                ('called_who', models.CharField(blank=True, default='', max_length=120)),
                ('called_number', models.CharField(blank=True, default='', max_length=40)),
                ('note', models.TextField(blank=True, default='')),
                ('request', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='release_signoffs', to='taskboard.paymentrequest')),
                ('signer', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payment_release_signoffs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
        migrations.AddConstraint(
            model_name='paymentreleasesignoff',
            constraint=models.UniqueConstraint(fields=('request', 'signer'), name='one_release_signoff_per_member_per_request'),
        ),
    ]
