"""Budget Library — annual budget packs (headline figures + source files),
a home for FY27, FY28, ... budgets (CFO 2026-06-27)."""
import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('budgets', '0001_initial'),
        ('core', '0002_company'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='BudgetPack',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('fy_label', models.CharField(help_text='e.g. "FY2026/27"', max_length=20)),
                ('period_start', models.DateField(help_text='Budget year start, e.g. 2026-07-01')),
                ('period_end', models.DateField(help_text='Budget year end, e.g. 2027-06-30')),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('approved', 'Approved (adopted plan)'), ('superseded', 'Superseded')], default='draft', max_length=12)),
                ('scenario', models.CharField(blank=True, default='', help_text='e.g. "Base case", "Cost-cut case".', max_length=60)),
                ('gwp_target', models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ('ebitda', models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ('pat', models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ('ebitda_margin', models.DecimalField(blank=True, decimal_places=2, help_text='EBITDA margin on NEP (%).', max_digits=6, null=True)),
                ('notes', models.TextField(blank=True, default='', help_text='Headline summary / key assumptions.')),
                ('source', models.CharField(blank=True, default='', help_text='Who prepared it, e.g. "Kago Tshutlhedi".', max_length=120)),
                ('company', models.ForeignKey(blank=True, help_text='NULL = Group / consolidated.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='budget_packs', to='core.company')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='budget_packs_created', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Budget pack',
                'verbose_name_plural': 'Budget packs',
                'ordering': ['-period_start', 'company__code'],
                'unique_together': {('fy_label', 'company', 'scenario')},
            },
        ),
        migrations.CreateModel(
            name='BudgetPackFile',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('file', models.FileField(upload_to='budgets/%Y/')),
                ('kind', models.CharField(choices=[('model', 'Financial model (xlsx)'), ('highlights', 'Highlights (deck / doc)'), ('calculator', 'Calculator / tool'), ('other', 'Other')], default='other', max_length=12)),
                ('label', models.CharField(blank=True, default='', max_length=200)),
                ('pack', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='files', to='budgets.budgetpack')),
            ],
            options={'ordering': ['kind', 'label']},
        ),
    ]
