"""SEC-05 — bind each one-time code to what it was issued for.

Existing rows become 'sign_in', which is the safe reading: every code already in
the table was issued before the reset flow could be distinguished, and a sign-in
code is the weaker of the two capabilities. Any live reset code in flight at
deploy time is invalidated rather than silently upgraded — the person simply
requests another, which costs them ten seconds.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('core', '0057_add_ceo_coo_titles')]

    operations = [
        migrations.AddField(
            model_name='emaillogincode',
            name='purpose',
            field=models.CharField(
                choices=[('sign_in', 'Sign in'), ('reset', 'Password reset')],
                db_index=True, default='sign_in', max_length=16),
        ),
        migrations.AddIndex(
            model_name='emaillogincode',
            index=models.Index(fields=['email', 'purpose', 'consumed'],
                               name='core_emaill_email_e1e8c0_idx'),
        ),
    ]
