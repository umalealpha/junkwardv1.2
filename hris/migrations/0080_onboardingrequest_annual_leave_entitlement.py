from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0079_successionnominee'),
    ]

    operations = [
        migrations.AddField(
            model_name='onboardingrequest',
            name='annual_leave_entitlement',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=5, null=True),
        ),
    ]
