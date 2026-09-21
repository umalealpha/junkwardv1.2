import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Link a compensating amendment back to the one it reverses.

    Manus nine-area retest P2 (2026-08-25): an applied HRIS amendment had no
    supported reversal path. The reversal itself is an ordinary amendment going
    through the same maker-checker approval — this FK only records the pairing.
    """

    dependencies = [
        ('hris', '0073_encashment_cfo_selfleg_skip'),
    ]

    operations = [
        migrations.AddField(
            model_name='hrisamendment',
            name='reversal_of',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='reversals',
                to='hris.hrisamendment',
                help_text='The applied amendment this one reverses.',
            ),
        ),
    ]
