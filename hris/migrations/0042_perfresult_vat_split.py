from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0041_performancetarget_performancetargetresult'),
    ]

    operations = [
        migrations.AddField(
            model_name='performancetargetresult',
            name='actual_excl',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name='performancetargetresult',
            name='actual_vat',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name='performancetargetresult',
            name='actual_incl',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
    ]
