import uuid
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Commandment',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('number', models.PositiveSmallIntegerField(unique=True)),
                ('title', models.CharField(max_length=120)),
                ('summary', models.CharField(max_length=400)),
                ('iso_clauses', models.CharField(max_length=200,
                    help_text='Comma-separated Annex A clauses, e.g. "A.5.15, A.8.2"')),
                ('why_it_matters', models.TextField(blank=True, default='')),
                ('status', models.CharField(default='pending', max_length=10,
                    choices=[
                        ('good', 'Good — operating'),
                        ('done', 'Done — implemented'),
                        ('partial', 'Partial — evidence gap'),
                        ('pending', 'Pending — not yet built'),
                        ('na', 'N/A — out of scope'),
                    ])),
                ('last_audited_at', models.DateTimeField(blank=True, null=True)),
                ('owner', models.CharField(blank=True, default='', max_length=80)),
            ],
            options={'ordering': ['number']},
        ),
        migrations.CreateModel(
            name='AuditFinding',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('severity', models.CharField(default='info', max_length=10,
                    choices=[
                        ('good', 'Good'),
                        ('info', 'Info'),
                        ('low', 'Low'),
                        ('medium', 'Medium'),
                        ('high', 'High'),
                        ('critical', 'Critical'),
                    ])),
                ('state', models.CharField(default='open', max_length=10,
                    choices=[
                        ('open', 'Open'),
                        ('resolved', 'Resolved'),
                        ('accepted', 'Risk accepted'),
                    ])),
                ('title', models.CharField(max_length=200)),
                ('detail', models.TextField(blank=True, default='')),
                ('fix_hint', models.TextField(blank=True, default='')),
                ('evidence', models.TextField(blank=True, default='')),
                ('detected_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('commandment', models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name='findings',
                    to='iso_compliance.commandment',
                )),
            ],
            options={'ordering': ['-detected_at']},
        ),
        migrations.AddIndex(
            model_name='auditfinding',
            index=models.Index(fields=['commandment', 'state'],
                               name='iso_finding_cmd_state_idx'),
        ),
        migrations.AddIndex(
            model_name='auditfinding',
            index=models.Index(fields=['severity', 'state'],
                               name='iso_finding_sev_state_idx'),
        ),
        migrations.CreateModel(
            name='AuditRun',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('started_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('actor', models.CharField(blank=True, default='system', max_length=80)),
                ('findings_created', models.PositiveIntegerField(default=0)),
                ('score_pct', models.PositiveSmallIntegerField(default=0)),
                ('notes', models.TextField(blank=True, default='')),
            ],
            options={'ordering': ['-started_at']},
        ),
    ]
