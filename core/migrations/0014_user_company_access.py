"""
0014_user_company_access — per-user entity allowlist (CFO directive
2026-05-22). Adds UserCompanyAccess and seeds initial grants:

  * Every superuser gets every company (rw).
  * Every UserProfile with is_administrator=True gets every company (rw).
  * Every UserProfile with title='cfo' gets every company (rw).
  * Everyone else gets a single ADIC view-only grant (so existing flows
    don't 403 the day the gate goes live). Operators widen as needed.
"""
import uuid
from django.conf import settings
from django.db import migrations, models


def _seed_access(apps, schema_editor):
    Company           = apps.get_model('core', 'Company')
    UserProfile       = apps.get_model('core', 'UserProfile')
    UserCompanyAccess = apps.get_model('core', 'UserCompanyAccess')
    User              = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))

    adic = Company.objects.filter(code__iexact='ADIC').first()
    all_companies = list(Company.objects.all())

    for u in User.objects.all():
        # Unrestricted bucket: superusers, administrators, CFO
        prof = UserProfile.objects.filter(user=u).first()
        unrestricted = (
            u.is_superuser
            or (prof and prof.is_administrator)
            or (prof and (prof.title or '').lower() == 'cfo')
        )
        if unrestricted:
            for c in all_companies:
                UserCompanyAccess.objects.update_or_create(
                    user=u, company=c,
                    defaults={'can_view': True, 'can_write': True},
                )
        elif adic:
            UserCompanyAccess.objects.update_or_create(
                user=u, company=adic,
                defaults={'can_view': True, 'can_write': False},
            )


def _unseed(apps, schema_editor):
    UserCompanyAccess = apps.get_model('core', 'UserCompanyAccess')
    UserCompanyAccess.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0013_api_key'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UserCompanyAccess',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False,
                                                primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('can_view',   models.BooleanField(default=True)),
                ('can_write',  models.BooleanField(default=False)),
                ('notes',      models.CharField(blank=True, default='', max_length=255)),
                ('company',    models.ForeignKey(
                                  on_delete=models.deletion.CASCADE,
                                  related_name='user_access',
                                  to='core.company')),
                ('granted_by', models.ForeignKey(
                                  blank=True, null=True,
                                  on_delete=models.deletion.SET_NULL,
                                  related_name='company_access_granted',
                                  to=settings.AUTH_USER_MODEL)),
                ('user',       models.ForeignKey(
                                  on_delete=models.deletion.CASCADE,
                                  related_name='company_access',
                                  to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name':        'User company access',
                'verbose_name_plural': 'User company access',
                'ordering':            ['-created_at'],
                'unique_together':     {('user', 'company')},
            },
        ),
        migrations.AddIndex(
            model_name='usercompanyaccess',
            index=models.Index(fields=['user', 'company'],
                               name='core_userco_user_id_e1f8b8_idx'),
        ),
        migrations.RunPython(_seed_access, _unseed),
    ]
