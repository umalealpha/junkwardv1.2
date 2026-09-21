"""
Management command: test_fnb_connection

Pings the configured FNB API base with the configured creds and reports
back. Useful right after dropping new credentials into .env.

  python manage.py test_fnb_connection
"""

from django.core.management.base import BaseCommand

from fnb.client import FNBClient, FNBConfig, FNBNotConfigured, FNBAPIError
from fnb.endpoints import PING
from fnb.models import FNBSyncLog


class Command(BaseCommand):
    help = 'Ping the configured FNB Botswana API and report status.'

    def handle(self, *args, **options):
        cfg = FNBConfig.from_settings()
        self.stdout.write(self.style.MIGRATE_HEADING('FNB integration check'))
        self.stdout.write(f'  api_base:   {cfg.api_base or "(not set)"}')
        self.stdout.write(f'  auth_mode:  {cfg.auth_mode}')
        self.stdout.write(f'  configured: {cfg.is_configured}')

        if not cfg.is_configured:
            self.stdout.write(self.style.WARNING(
                '\nFNB integration is NOT configured.\n'
                'Set the env vars in your .env (see CLAUDE.md → FNB Integration).'
            ))
            return

        client = FNBClient()
        self.stdout.write(self.style.MIGRATE_HEADING('\nTrying ping...'))
        try:
            resp = client.get(PING, service=FNBSyncLog.Service.TEST,
                              request_summary='CLI connection test')
        except FNBNotConfigured as e:
            self.stdout.write(self.style.ERROR(f'  Not configured: {e}'))
            return
        except FNBAPIError as e:
            self.stdout.write(self.style.ERROR(
                f'  HTTP {e.status_code}: {e.body[:300]}'
            ))
            return
        except Exception as e:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f'  Failed: {e}'))
            return

        self.stdout.write(self.style.SUCCESS(
            f'  OK — HTTP {resp.status_code} in {resp.elapsed_ms} ms'
        ))
