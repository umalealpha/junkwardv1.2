from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('underwriting', '0012_quote_agent'),
    ]

    operations = [
        migrations.AddField(
            model_name='quote',
            name='notes',
            field=models.TextField(blank=True, default=''),
        ),
    ]
