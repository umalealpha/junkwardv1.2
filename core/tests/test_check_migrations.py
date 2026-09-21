"""The pre-deploy migration-conflict guard."""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class CheckMigrationsCommandTest(SimpleTestCase):
    def test_clean_graph_passes(self):
        """The real graph on this branch is single-leaf → no error, says OK."""
        out = StringIO()
        call_command('check_migrations', stdout=out)
        self.assertIn('OK', out.getvalue())

    @mock.patch('core.management.commands.check_migrations.MigrationLoader')
    def test_forked_graph_aborts(self, MockLoader):
        """A forked graph (the crash-loop cause) raises → deploy aborts."""
        MockLoader.return_value.detect_conflicts.return_value = {
            'hris': ['0065_alter_corating', '0066_disciplinary_unheard_issue']}
        with self.assertRaises(CommandError) as ctx:
            call_command('check_migrations', stderr=StringIO())
        self.assertIn('multiple leaf nodes', str(ctx.exception))
        self.assertIn('makemigrations --merge', str(ctx.exception))
