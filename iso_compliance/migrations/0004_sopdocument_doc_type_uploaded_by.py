"""Add doc_type (SOP/Policy) + uploaded_by to the SOP Bank.

CFO directive 2026-07-27 (Unami's Corporate Governance request): the SOP Bank
becomes a two-kind library per department — SOPs and Policies — with self-service
upload for empowered HR/Finance staff. Existing 227 rows are all SOPs, so
doc_type defaults to 'sop'. Hand-written (not makemigrations) to add ONLY these
two columns + the doc_type index, avoiding the pre-existing help_text/index drift
in core/payroll/hris that a full makemigrations sweep would drag in.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('iso_compliance', '0003_sopacknowledgement_sopdocument_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='sopdocument',
            name='doc_type',
            field=models.CharField(
                choices=[('sop', 'SOP'), ('policy', 'Policy')],
                default='sop', db_index=True, max_length=10),
        ),
        migrations.AddField(
            model_name='sopdocument',
            name='uploaded_by',
            field=models.CharField(
                blank=True, default='', max_length=80,
                help_text='Username of the staff member who uploaded this via the UI '
                          '(blank for the original bulk Data Room ingest).'),
        ),
    ]
