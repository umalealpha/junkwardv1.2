from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0007_timedoctorusermap'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='integrationevent',
            index=models.Index(fields=['-received_at'], name='intgevent_received_at_idx'),
        ),
    ]
