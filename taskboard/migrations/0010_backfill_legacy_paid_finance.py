"""Backfill legacy pre-two-stage paid requests (bug ktshutlhedi 2026-07-25, part 2).

Migration 0009 cleared requests stuck at PENDING_CFO whose task was already
done. But PAY/ADIC/2026/07/18/0001 was raised on 2026-07-18 — BEFORE the
two-stage flow (2026-07-23) — so it was paid under the old single-stage flow
(its CFO task is DONE) yet the status column defaulted to 'pending_finance'
when the two-stage field was added. It therefore sat in the finance sign-off
queue for ever.

Rule: a request at 'pending_finance' whose linked task is DONE has in fact been
actioned to completion (paid) — in the two-stage flow a genuine finance sign-off
moves the request to 'pending_cfo' and never leaves it at 'pending_finance' with
a done task. Flip those to the terminal PAID state so they leave the queue.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    PaymentRequest = apps.get_model('taskboard', 'PaymentRequest')
    (PaymentRequest.objects
        .filter(status='pending_finance', task__status='done')
        .update(status='paid'))


def unbackfill(apps, schema_editor):
    # Non-reversible in a precise sense (we can't tell which 'paid' rows this
    # produced); leave paid rows as-is on reverse.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('taskboard', '0009_paymentrequest_terminal_states'),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
