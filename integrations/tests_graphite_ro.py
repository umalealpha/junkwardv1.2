"""The read-only Graphite door must refuse anything that is not a read.

Graphite runs ~213k live policies. The replica rejects writes itself, but relying on
that alone leaves one mis-set DSN between this code and a live policy table — so the
guards are tested here rather than assumed.
"""
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from integrations import graphite_ro as gro

RO = 'mysql://brain_ro:secret@graphite-v2-prod-ro.example.rds.amazonaws.com:3306/Graphite_live'
MASTER = 'mysql://root:secret@graphite-v2-prod-master.example.rds.amazonaws.com:3306/Graphite_live'


class StatementGuardTests(TestCase):
    def test_reads_are_allowed(self):
        for sql in ('SELECT 1',
                    '  select * from refund_requests',
                    'SHOW TABLES',
                    'DESCRIBE refund_requests',
                    'EXPLAIN SELECT 1',
                    'WITH x AS (SELECT 1) SELECT * FROM x'):
            gro.assert_read_only(sql)          # must not raise

    def test_writes_are_refused(self):
        for sql in ('UPDATE policies SET status = 1',
                    'DELETE FROM refund_requests',
                    'INSERT INTO refund_requests (id) VALUES (1)',
                    'DROP TABLE refund_requests',
                    'TRUNCATE refund_requests',
                    'ALTER TABLE policies ADD COLUMN x INT',
                    'CALL do_something()',
                    'GRANT ALL ON *.* TO x'):
            with self.assertRaises(gro.GraphiteReadOnlyError, msg=sql):
                gro.assert_read_only(sql)

    def test_a_write_hidden_behind_a_comment_is_refused(self):
        """A leading comment must not smuggle the verb past the check."""
        with self.assertRaises(gro.GraphiteReadOnlyError):
            gro.assert_read_only('/* harmless */ DELETE FROM policies')
        with self.assertRaises(gro.GraphiteReadOnlyError):
            gro.assert_read_only('-- just looking\nUPDATE policies SET status = 0')

    def test_a_stacked_write_is_refused(self):
        with self.assertRaises(gro.GraphiteReadOnlyError):
            gro.assert_read_only('SELECT 1; DROP TABLE policies')

    def test_a_semicolon_inside_a_string_is_not_a_stacked_statement(self):
        gro.assert_read_only("SELECT * FROM t WHERE note = 'a; b'")

    def test_an_empty_statement_is_refused(self):
        with self.assertRaises(gro.GraphiteReadOnlyError):
            gro.assert_read_only('   ')


class ConnectionGuardTests(TestCase):
    @override_settings(GRAPHITE_RO_DSN=MASTER)
    def test_the_master_endpoint_is_refused(self):
        """The network path to the master is open. This module must still not use it."""
        with self.assertRaises(gro.GraphiteReadOnlyError):
            with gro.connection():
                pass

    @override_settings(GRAPHITE_RO_DSN='', GRAPHITE_RO_DB_HOST='')
    def test_with_nothing_configured_it_says_what_to_set(self):
        self.assertFalse(gro.is_configured())
        with self.assertRaises(ImproperlyConfigured) as caught:
            with gro.connection():
                pass
        self.assertIn('GRAPHITE_RO_DB_HOST', str(caught.exception))

    @override_settings(GRAPHITE_RO_DSN='',
                       GRAPHITE_RO_DB_HOST='graphite-v2-prod-rpro.example.rds.amazonaws.com',
                       GRAPHITE_RO_DB_USER='graphitebwlive',
                       GRAPHITE_RO_DB_PASSWORD='x',
                       GRAPHITE_RO_DB_NAME='Graphite_live')
    def test_it_uses_the_credentials_omni_already_has(self):
        """No second secret: the pair Graphite Aware runs on is enough."""
        self.assertTrue(gro.is_configured())
        parts = gro._settings_parts()
        self.assertEqual(parts['user'], 'graphitebwlive')
        self.assertEqual(parts['database'], 'Graphite_live')
        gro._assert_replica(parts['host'])      # -rpro counts as a replica

    @override_settings(GRAPHITE_RO_DSN='postgres://u:p@host/db', GRAPHITE_RO_DB_HOST='')
    def test_a_malformed_dsn_never_echoes_the_secret(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            with gro.connection():
                pass
        self.assertNotIn('p@host', str(caught.exception))

    @override_settings(GRAPHITE_RO_DSN=RO)
    def test_the_replica_passes_the_host_check(self):
        parts = gro._parse_dsn(RO)
        self.assertEqual(parts['user'], 'brain_ro')
        self.assertEqual(parts['database'], 'Graphite_live')
        self.assertEqual(parts['port'], 3306)
        gro._assert_replica(parts['host'])      # must not raise


class IdentifierTests(TestCase):
    def test_a_real_name_is_quoted(self):
        self.assertEqual(gro.safe_identifier('refund_requests'), '`refund_requests`')

    def test_an_injected_name_is_refused(self):
        for bad in ('refunds`; DROP TABLE x; --', 'a b', 'a-b', '', 'a);'):
            with self.assertRaises(gro.GraphiteReadOnlyError, msg=bad):
                gro.safe_identifier(bad)
