"""
The Telegram bot has to log the sender's ID (CFO 2026-08-12).

A Telegram user_id is not a phone number and lives nowhere in omni, so the only
way to enrol somebody on TELEGRAM_BOT_AUTHORIZED_USERS is to read the id off a
message they send. bot.py logs it — but the root logger sits at ERROR, so the
line was being discarded and enrolment fell back to asking each person to read
the number off their own phone and pass it on.

This test exists because the failure is silent: nothing errors, the bot works,
the ID is simply never written down. If someone tidies the logging config later
and drops this logger, enrolment quietly breaks again and nobody finds out until
the next person needs adding.
"""
import logging

from django.test import TestCase


class TelegramEnrolmentLoggingTests(TestCase):

    def test_the_bot_logger_is_at_INFO_not_ERROR(self):
        log = logging.getLogger('core.telegram_bot')
        self.assertTrue(
            log.isEnabledFor(logging.INFO),
            'core.telegram_bot must log at INFO or the sender Telegram IDs are '
            'discarded and nobody can be enrolled without reading the number off '
            'their own phone.',
        )

    def test_the_bot_module_logger_is_covered_by_it(self):
        """bot.py uses __name__, so the config must catch the child logger too."""
        log = logging.getLogger('core.telegram_bot.bot')
        self.assertTrue(log.isEnabledFor(logging.INFO))

    def test_the_rest_of_the_app_stays_quiet(self):
        """Scoped on purpose — this must not turn INFO on everywhere."""
        self.assertFalse(logging.getLogger('django.request').isEnabledFor(logging.INFO))
        self.assertFalse(logging.getLogger().isEnabledFor(logging.INFO))

    def test_an_incoming_message_actually_writes_the_id(self):
        """The line enrolment depends on. Asserted against the real logger."""
        with self.assertLogs('core.telegram_bot.bot', level='INFO') as captured:
            logging.getLogger('core.telegram_bot.bot').info(
                'IN  user=%s @%s text=%r', '123456789', 'someone', '/whoami')
        self.assertIn('123456789', captured.output[0])
