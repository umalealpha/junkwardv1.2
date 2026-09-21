import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('payments', '0008_posted_payment_invoice_immutable'),
    ]

    operations = [
        migrations.CreateModel(
            name='PaymentApproval',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('role', models.CharField(choices=[('fm', 'Finance Manager / Financial Controller'), ('exec', 'CFO / CEO')], max_length=4)),
                ('comment', models.TextField(blank=True, default='')),
                ('approved_at', models.DateTimeField(auto_now_add=True)),
                ('approver', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payment_approvals', to=settings.AUTH_USER_MODEL)),
                ('payment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='approvals', to='payments.payment')),
            ],
            options={
                'verbose_name': 'Payment Approval',
                'verbose_name_plural': 'Payment Approvals',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='paymentapproval',
            constraint=models.UniqueConstraint(fields=('payment', 'approver'), name='uniq_payment_approver'),
        ),
    ]
