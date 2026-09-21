"""Add terminal PAID / CANCELLED states to PaymentRequest and backfill the
requests that were already stuck (bug ktshutlhedi 2026-07-25).

Before this, the only statuses were the two pending ones + rejected, so a
request the CFO had paid stayed 'pending_cfo' for ever and never left the
queue. This:
  * widens the status choices with 'paid' and 'cancelled';
  * flips any PENDING_CFO whose linked task is already DONE  -> PAID
    (e.g. PAY/ADIC/2026/07/18/0001, which was paid but stayed in the queue);
  * flips any PENDING_* whose linked task was CANCELLED       -> CANCELLED.
"""
from django.db import migrations, models


def backfill(apps, schema_editor):
    PaymentRequest = apps.get_model('taskboard', 'PaymentRequest')

    # Paid: signed off, awaiting the CFO, and the CFO's task is already done.
    (PaymentRequest.objects
        .filter(status='pending_cfo', task__status='done')
        .update(status='paid'))

    # Cancelled: the payment task was cancelled but the request lingered.
    (PaymentRequest.objects
        .filter(status__in=['pending_cfo', 'pending_finance'], task__status='cancelled')
        .update(status='cancelled'))


def unbackfill(apps, schema_editor):
    # Reverse is best-effort: put terminal rows back to their prior pending
    # state so the choices can be narrowed again.
    PaymentRequest = apps.get_model('taskboard', 'PaymentRequest')
    PaymentRequest.objects.filter(status='paid').update(status='pending_cfo')
    PaymentRequest.objects.filter(status='cancelled').update(status='pending_cfo')


class Migration(migrations.Migration):

    dependencies = [
        ('taskboard', '0008_taskreminderemaillog'),
    ]

    operations = [
        migrations.AlterField(
            model_name='paymentrequest',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending_finance', 'Pending finance sign-off'),
                    ('pending_cfo', 'Pending CFO authorisation'),
                    ('rejected', 'Rejected at finance sign-off'),
                    ('paid', 'Paid / authorised'),
                    ('cancelled', 'Cleared / cancelled'),
                ],
                db_index=True, default='pending_finance', max_length=16),
        ),
        migrations.RunPython(backfill, unbackfill),
    ]
