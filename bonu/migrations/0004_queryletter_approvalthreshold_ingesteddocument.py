# Hand-written to match bonu/models.py: the container engine on the build machine was
# unresponsive, so `makemigrations` could not be run here. It MUST be checked with
# `makemigrations bonu --check --dry-run` (expect "No changes detected") before deploy.

import core.models
import django.db.models.deletion
import uuid
from decimal import Decimal
from django.db import migrations, models


MATTER_TYPE_CHOICES = [
    ('divorce', 'Divorce / family'),
    ('conveyancing', 'Conveyancing / property transfer'),
    ('criminal', 'Criminal defence'),
    ('labour', 'Labour / employment'),
    ('debt', 'Debt collection'),
    ('estate', 'Deceased estate / will'),
    ('civil', 'Civil litigation'),
    ('contract', 'Contract / commercial'),
    ('road', 'Traffic / road accident'),
    ('tenancy', 'Landlord / tenancy'),
    ('advice', 'Consultation / advice only'),
    ('other', 'Other / unclassified'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('bonu', '0003_retaineragreement_legalcase_caseevent_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='bonuinvoiceline',
            name='matter_type_source',
            field=models.CharField(
                choices=[('firm', 'Stated by the firm on the invoice'),
                         ('manual', 'Set by our accountant'),
                         ('ai', 'Suggested by AI — not yet confirmed'),
                         ('default', 'Never classified')],
                default='default',
                help_text='An AI guess is reported as a guess. Spend reports say how much of '
                          'the total rests on unconfirmed classification.',
                max_length=7),
        ),
        migrations.CreateModel(
            name='ApprovalThreshold',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('matter_type', models.CharField(choices=MATTER_TYPE_CHOICES, max_length=14, unique=True)),
                ('typical_cost', models.DecimalField(
                    blank=True, decimal_places=2, max_digits=14, null=True,
                    help_text='What this kind of case normally costs us. Blank = work it out '
                              'from our own history instead.')),
                ('approval_above', models.DecimalField(
                    decimal_places=2, max_digits=14,
                    help_text='Above this total, the firm must get written approval BEFORE '
                              'doing the work.')),
                ('warn_multiple', models.DecimalField(
                    decimal_places=2, default=Decimal('2.00'), max_digits=5,
                    help_text='Warn when a case reaches this multiple of typical — catch it at '
                              '2x, not 5x.')),
                ('note', models.TextField(blank=True, default='')),
            ],
            options={
                'verbose_name': 'BONU approval limit',
                'ordering': ['matter_type'],
                'abstract': False,
            },
            bases=(core.models.AuditableMixin, models.Model),
        ),
        migrations.CreateModel(
            name='QueryLetter',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reference', models.CharField(help_text='Our own query reference.', max_length=40)),
                ('subject', models.CharField(max_length=200)),
                ('body', models.TextField(
                    help_text='The letter itself, in plain language, question by question.')),
                ('amount_queried', models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=16)),
                ('amount_conceded', models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=16)),
                ('status', models.CharField(
                    choices=[('draft', 'Drafted — not sent'), ('sent', 'Sent to the firm'),
                             ('replied', 'Firm has replied'), ('conceded', 'Firm accepted the query'),
                             ('rejected', 'Firm stands by the charge'),
                             ('withdrawn', 'We withdrew the query')],
                    default='draft', max_length=10)),
                ('sent_on', models.DateField(blank=True, null=True)),
                ('reply_due_on', models.DateField(
                    blank=True, null=True,
                    help_text='A query with no date on it is a query nobody answers.')),
                ('replied_on', models.DateField(blank=True, null=True)),
                ('chased_count', models.PositiveSmallIntegerField(default=0)),
                ('last_chased_on', models.DateField(blank=True, null=True)),
                ('reply_note', models.TextField(blank=True, default='')),
                ('findings', models.ManyToManyField(blank=True, related_name='queries', to='bonu.bonufinding')),
                ('firm', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                           related_name='queries', to='bonu.lawfirm')),
            ],
            options={
                'verbose_name': 'BONU query letter',
                'ordering': ['-sent_on', '-created_at'],
                'abstract': False,
            },
            bases=(core.models.AuditableMixin, models.Model),
        ),
        migrations.AddConstraint(
            model_name='queryletter',
            constraint=models.UniqueConstraint(fields=('firm', 'reference'),
                                               name='bonu_uniq_firm_query_ref'),
        ),
        migrations.CreateModel(
            name='IngestedDocument',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('filename', models.CharField(max_length=255)),
                ('arrived_by', models.CharField(
                    choices=[('upload', 'Uploaded by the accountant'), ('email', 'Emailed in by the firm')],
                    default='upload', max_length=6)),
                ('from_address', models.CharField(
                    blank=True, default='', max_length=200,
                    help_text='Who emailed it, when it came in by mail.')),
                ('extraction_method', models.CharField(blank=True, default='', max_length=60)),
                ('extraction_error', models.TextField(blank=True, default='')),
                ('draft', models.JSONField(
                    blank=True, default=dict,
                    help_text='Exactly what the machine proposed, kept for audit even after the '
                              'accountant corrects it.')),
                ('text_preview', models.TextField(blank=True, default='')),
                ('status', models.CharField(
                    choices=[('parsed', 'Read — waiting for the accountant'),
                             ('needs_ocr', 'A scan — needs the vision model'),
                             ('failed', 'Could not be read'),
                             ('confirmed', 'Confirmed into an invoice'),
                             ('discarded', 'Discarded')],
                    default='parsed', max_length=10)),
                ('warnings', models.JSONField(
                    blank=True, default=list,
                    help_text='Duplicate alerts and anything else worth seeing BEFORE the '
                              'invoice is created.')),
                ('firm', models.ForeignKey(blank=True, null=True,
                                           on_delete=django.db.models.deletion.SET_NULL,
                                           related_name='documents', to='bonu.lawfirm')),
                ('invoice', models.ForeignKey(blank=True, null=True,
                                              on_delete=django.db.models.deletion.SET_NULL,
                                              related_name='documents', to='bonu.bonuinvoice')),
            ],
            options={
                'verbose_name': 'BONU ingested document',
                'ordering': ['-created_at'],
                'abstract': False,
                'indexes': [models.Index(fields=['status'], name='bonu_doc_status_idx')],
            },
        ),
    ]
