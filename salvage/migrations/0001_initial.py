"""Initial migration for salvage app — Phase 1 tables."""
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PartCategory',
            fields=[
                ('id',          models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at',  models.DateTimeField(auto_now_add=True)),
                ('updated_at',  models.DateTimeField(auto_now=True)),
                ('name',        models.CharField(max_length=100, unique=True)),
                ('description', models.TextField(blank=True, default='')),
                ('is_active',   models.BooleanField(default=True)),
            ],
            options={'verbose_name_plural': 'Part categories', 'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='VehicleBrand',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name',       models.CharField(max_length=80, unique=True)),
                ('is_active',  models.BooleanField(default=True)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='VehicleModel',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name',       models.CharField(max_length=120)),
                ('is_active',  models.BooleanField(default=True)),
                ('brand',      models.ForeignKey(on_delete=models.deletion.PROTECT, related_name='models', to='salvage.vehiclebrand')),
            ],
            options={'ordering': ['brand__name', 'name']},
        ),
        migrations.AddConstraint(
            model_name='vehiclemodel',
            constraint=models.UniqueConstraint(fields=('brand', 'name'), name='uniq_vehicle_model_per_brand'),
        ),
        migrations.CreateModel(
            name='SalvageItem',
            fields=[
                ('id',               models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at',       models.DateTimeField(auto_now_add=True)),
                ('updated_at',       models.DateTimeField(auto_now=True)),
                ('item_code',        models.CharField(max_length=40, unique=True)),
                ('claim_number',     models.CharField(blank=True, default='', max_length=60)),
                ('policy_number',    models.CharField(blank=True, default='', max_length=60)),
                ('part_name',        models.CharField(max_length=200)),
                ('part_description', models.TextField(blank=True, default='')),
                ('quantity',         models.PositiveIntegerField(default=1)),
                ('vehicle_year',     models.PositiveIntegerField(blank=True, null=True)),
                ('vehicle_colour',   models.CharField(blank=True, default='', max_length=40)),
                ('vin_number',       models.CharField(blank=True, default='', max_length=50)),
                ('condition',        models.CharField(choices=[('excellent','Excellent'),('good','Good'),('fair','Fair'),('poor','Poor'),('scrap','Scrap')], default='fair', max_length=12)),
                ('asking_price',     models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('reserve_price',    models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('status',           models.CharField(choices=[('available','Available'),('reserved','Reserved'),('sold','Sold'),('scrapped','Scrapped'),('on_hold','On hold')], default='available', max_length=12)),
                ('location',         models.CharField(blank=True, default='', max_length=80)),
                ('posted_at',        models.DateTimeField(blank=True, null=True)),
                ('category',         models.ForeignKey(blank=True, null=True, on_delete=models.deletion.PROTECT, related_name='items', to='salvage.partcategory')),
                ('company',          models.ForeignKey(blank=True, null=True, on_delete=models.deletion.PROTECT, related_name='salvage_items', to='core.company')),
                ('created_by',       models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='salvage_items_created', to=settings.AUTH_USER_MODEL)),
                ('vehicle_brand',    models.ForeignKey(blank=True, null=True, on_delete=models.deletion.PROTECT, related_name='items', to='salvage.vehiclebrand')),
                ('vehicle_model',    models.ForeignKey(blank=True, null=True, on_delete=models.deletion.PROTECT, related_name='items', to='salvage.vehiclemodel')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='salvageitem',
            index=models.Index(fields=['status'], name='salvage_sal_status_idx'),
        ),
        migrations.AddIndex(
            model_name='salvageitem',
            index=models.Index(fields=['condition'], name='salvage_sal_cond_idx'),
        ),
        migrations.AddIndex(
            model_name='salvageitem',
            index=models.Index(fields=['company'], name='salvage_sal_co_idx'),
        ),
        migrations.CreateModel(
            name='SalvageImage',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('image',      models.ImageField(upload_to='salvage/%Y/%m/')),
                ('caption',    models.CharField(blank=True, default='', max_length=200)),
                ('ordering',   models.PositiveIntegerField(default=0)),
                ('item',       models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='images', to='salvage.salvageitem')),
            ],
            options={'ordering': ['ordering', 'created_at']},
        ),
    ]
