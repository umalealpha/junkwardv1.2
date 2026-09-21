from django.db import migrations


class Migration(migrations.Migration):
    """Add the `view_records_register` permission.

    Lets a NAMED person be admitted to the records register without changing their
    job title. Requested 2026-08-25 (Tlotlo Maswabi) so Goitsemang Ngwako can cover
    the Records Officer role in her absence; Goitsemang's title is `operations`,
    and in Omni a job title also drives approval rights on payments and purchase
    orders, so promoting her to read a register would grant authority she was never
    given.
    """

    dependencies = [
        ('records', '0007_recordfilerequest'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='recorditem',
            options={
                'ordering': ['reference'],
                'permissions': [
                    ('view_restricted_records',
                     'Can see records marked restricted (personal data)'),
                    ('view_records_register',
                     'Can open the records register (without holding a register job title)'),
                ],
            },
        ),
    ]
