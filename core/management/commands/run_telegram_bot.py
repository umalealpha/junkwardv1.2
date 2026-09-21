"""
core/management/commands/run_telegram_bot.py

Long-running CFO Telegram bot.

Usage on the Mac:

    python manage.py run_telegram_bot

The process blocks forever and long-polls Telegram. Ctrl-C to stop.
Read-only — the bot cannot mutate the ERP.

Required .env settings (see .env.example):
  TELEGRAM_BOT_TOKEN              — from BotFather
  TELEGRAM_BOT_PASSWORD           — initial gate (REQUIRED, no default)
  TELEGRAM_BOT_AUTHORIZED_USERS   — comma-separated Telegram user IDs (REQUIRED, non-empty)
  TELEGRAM_BOT_SESSION_MINUTES    — auth idle timeout, default 60
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run the Alpha Direct CFO Telegram bot (long-polling, read-only)."

    def handle(self, *args, **options):
        # Import here so Django apps are fully ready before the bot loop loads
        # any models via the service layer.
        from core.telegram_bot.bot import run_loop

        try:
            run_loop()
        except KeyboardInterrupt:
            self.stdout.write(self.style.NOTICE('\n[telegram-bot] stopped.'))
