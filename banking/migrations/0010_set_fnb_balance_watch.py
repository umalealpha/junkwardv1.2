"""Set the two FNB flags deliberately, by account number (CFO 2026-09-20).

TWO flags, because reading an account and showing it are different decisions.

``fnb_statement_pull`` — whose daily statement Omni fetches.
``fnb_balance_watch`` — who appears on Morning Bank Balances.

WHY THIS REPLACES A NAME MATCH. ``pull_fnb_statements --all`` used to select on
``bank_name/account_name icontains 'FNB'``. That match:

  * MISSED Veritas (62477843132), Risk Software (62477854999) and Unicoin
    (62842621725) entirely — their names carry no "FNB" — so those three have
    NEVER been read, not once, and nobody noticed because no screen showed
    them; and
  * REACHED 4901344312871000, the FNBB Credit Card Control A/C, which is a
    CARD, not a bank account, and has failed with HTTP 400 every morning.

A name match breaks again the next time somebody renames an account. A flag
does not.

🔴 THE FOUR ACCOUNTS THAT ARE PULLED BUT NOT SHOWN. Investment Income
(62493292264), Choppies Kiosk (62571146797), Alpha Health (63162367601) and
FNB USD (63167551382) are reached by today's name match and are NOT among the
CFO's six. The first version of this migration switched them off, which would
have silently stopped collecting their statements — data Finance still uses
for reconciliation and month-end. Asked directly, the CFO ruled: **"Keep
pulling them, just don't show them."** So they keep ``fnb_statement_pull`` and
do not get ``fnb_balance_watch``. Do not collapse these two flags back into
one.

Reverse sets both flags False everywhere, so a rollback leaves no account
silently read or silently displayed.
"""
from django.db import migrations


# On the Morning Bank Balances screen — the CFO's six, in his order.
SHOWN = (
    '62403392335',   # Alpha Direct — Current
    '62493282265',   # Alpha Direct — Claims
    '62407809485',   # Alpha Direct — Call
    '62477843132',   # Veritas — Current          (never pulled before today)
    '62477854999',   # Risk Software — Current    (never pulled before today)
    '62842621725',   # Unicoin — Current          (never pulled before today)
)

# Statements pulled daily = the six above PLUS the four the old name match
# already reached. Everything the name match used to do, minus the credit card,
# plus the three that were being missed.
PULLED = SHOWN + (
    '62493292264',   # Investment Income
    '62571146797',   # Choppies Kiosk
    '63162367601',   # Alpha Health
    '63167551382',   # FNB USD
)

# Named so the reason travels with the decision, not just the absence.
NEVER_PULLED = (
    '4901344312871000',   # FNBB Credit Card Control A/C — a card, HTTP 400 daily
)


def set_flags(apps, schema_editor):
    BankAccount = apps.get_model('banking', 'BankAccount')

    BankAccount.objects.update(fnb_statement_pull=False, fnb_balance_watch=False)
    BankAccount.objects.filter(account_number__in=PULLED).update(
        fnb_statement_pull=True)
    BankAccount.objects.filter(account_number__in=SHOWN).update(
        fnb_balance_watch=True)
    # Belt and braces: the card must never be pulled, whatever it is named.
    BankAccount.objects.filter(account_number__in=NEVER_PULLED).update(
        fnb_statement_pull=False, fnb_balance_watch=False)


def clear_flags(apps, schema_editor):
    BankAccount = apps.get_model('banking', 'BankAccount')
    BankAccount.objects.update(fnb_statement_pull=False, fnb_balance_watch=False)


class Migration(migrations.Migration):

    dependencies = [
        ('banking', '0009_morning_bank_balances'),
    ]

    operations = [
        migrations.RunPython(set_flags, clear_flags),
    ]
