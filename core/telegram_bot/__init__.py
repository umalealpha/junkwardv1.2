"""
core.telegram_bot
=================

Read-only Telegram interface to alpha-finance. The CFO and his small
authorised group can ask questions in plain English from their phone:

    "Revenue last 6 months"
    "Cash balance"
    "Pending purchase orders"
    "PO-CLM-2026-000001"
    "Bills to pay"

Run on the Mac with:

    python manage.py run_telegram_bot

This package is a sub-module of `core` so it does not need to be a
separate Django app. It owns no models, no migrations, no admin.
"""
