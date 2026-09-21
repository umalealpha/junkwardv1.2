# Hand-written on 15 Sep 2026, because there is no working local Django on the
# machine it was written on. `makemigrations bonu --check` runs against it in a
# throwaway container built from the new image, before the traffic flip — so a
# migration that does not match the models stops the deploy rather than landing.
#
# The BONU legal bill register recorded what a firm BILLED and nothing about
# what we PAID. The CFO's question is the second one, so the register could not
# answer it and the answer lived in an emailed spreadsheet instead.
#
# Four columns, all nullable or defaulted, so the existing rows are untouched:
# the one bill already captured by hand keeps its amount and simply shows
# nothing paid, which is exactly what is known about it.
from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bonu', '0011_alter_legalsettings_cap_blocks_capture'),
    ]

    operations = [
        migrations.AddField(
            model_name='legalbill',
            name='discount',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('0'),
                help_text='Agreed off the bill before payment (BWP).', max_digits=14),
        ),
        migrations.AddField(
            model_name='legalbill',
            name='amount_paid',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('0'),
                help_text='Cash paid against this bill (BWP). Not derived from the stage — '
                          'the stage is derived from THIS.',
                max_digits=14),
        ),
        migrations.AddField(
            model_name='legalbill',
            name='paid_on',
            field=models.DateField(
                blank=True, null=True,
                help_text='When the payment went out. Empty where the source register '
                          'recorded a payment with no readable date — the money is real, '
                          'the month it belongs to is not known.'),
        ),
        migrations.AddField(
            model_name='legalbill',
            name='source_row',
            field=models.PositiveIntegerField(
                blank=True, db_index=True, null=True,
                help_text='Row number in the imported fee-note register, so any figure here '
                          'can be taken back to the line it came from. Also the import key: '
                          're-running a load updates the row rather than adding a second copy.'),
        ),
        migrations.AddConstraint(
            model_name='legalbill',
            constraint=models.UniqueConstraint(
                condition=models.Q(('source_row__isnull', False)),
                fields=('source_row',), name='bonu_bill_one_per_source_row'),
        ),
    ]
