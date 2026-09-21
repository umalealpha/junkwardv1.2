"""Tests for the AFA transfer leg (healthcare/afa_sftp.py).

No network. paramiko is stubbed, so these prove the RULES — sending is off by
default, a short upload is never left in place, a rename-then-drop is never
retried — rather than that a socket works.
"""
from __future__ import annotations

import sys
import types
from unittest import mock

from django.test import SimpleTestCase, override_settings

from healthcare import afa_sftp

BODY = 'ADH25002|ALPHA DIRECT INSURANCE\n'
CONF = dict(AFA_LOADFILE_AUTOSEND=True, AFA_SFTP_HOST='sftp.afa.test',
            AFA_SFTP_USERNAME='alphadirect', AFA_SFTP_PASSWORD='x',
            AFA_SFTP_PRIVATE_KEY_PATH='', AFA_SFTP_REMOTE_DIR='/in',
            AFA_SFTP_PORT='22')


class _FakeSFTP:
    def __init__(self, *, short_by=0, fail_on=None, drop_after_rename=False):
        self.short_by = short_by
        self.fail_on = fail_on or set()
        self.drop_after_rename = drop_after_rename
        self.files, self.removed, self.renames = {}, [], []

    def chdir(self, path):
        if 'chdir' in self.fail_on:
            raise IOError('no such folder')

    def putfo(self, fh, name, confirm=True):
        if 'put' in self.fail_on:
            raise OSError('connection reset')
        self.files[name] = fh.read()

    def stat(self, name):
        return types.SimpleNamespace(st_size=len(self.files[name]) - self.short_by)

    def remove(self, name):
        self.removed.append(name)
        self.files.pop(name, None)

    def rename(self, src, dst):
        self.renames.append((src, dst))
        self.files[dst] = self.files.pop(src)
        if self.drop_after_rename:
            raise OSError('connection dropped')

    def close(self):
        pass


class _AfaSftpTestCase(SimpleTestCase):
    """Installs a stub paramiko so afa_sftp's lazy import finds it."""

    def install_paramiko(self, fake):
        mod = types.ModuleType('paramiko')

        class _Transport:
            def __init__(self, addr): self.addr = addr
            def connect(self, **kw): self.auth = kw
            def close(self): pass

        mod.Transport = _Transport
        mod.RSAKey = types.SimpleNamespace(from_private_key_file=lambda p: f'key:{p}')
        mod.SFTPClient = types.SimpleNamespace(from_transport=lambda t: fake)
        patcher = mock.patch.dict(sys.modules, {'paramiko': mod})
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake


class SendSwitchTests(_AfaSftpTestCase):

    @override_settings(AFA_LOADFILE_AUTOSEND=False)
    def test_the_unattended_cron_cannot_send_unless_switched_on(self):
        self.assertFalse(afa_sftp.autosend_enabled())
        with self.assertRaises(afa_sftp.AfaSendDisabled):
            afa_sftp.send('ADH_20260807.csv', BODY)

    @override_settings(**dict(CONF, AFA_LOADFILE_AUTOSEND=False))
    def test_a_PERSON_can_release_while_the_cron_switch_is_off(self):
        """Two switches, not one. Enabling Ritah's first release must not also
        arm the cron to send every file unattended."""
        fake = self.install_paramiko(_FakeSFTP())
        self.assertEqual(
            afa_sftp.send('ADH_20260807.csv', BODY, unattended=False),
            'ADH_20260807.csv')
        self.assertEqual(len(fake.renames), 1)

    @override_settings(AFA_LOADFILE_AUTOSEND=False, AFA_SFTP_HOST='',
                       AFA_SFTP_USERNAME='', AFA_SFTP_PASSWORD='',
                       AFA_SFTP_PRIVATE_KEY_PATH='')
    def test_a_person_still_cannot_send_without_credentials(self):
        with self.assertRaises(afa_sftp.AfaSendNotConfigured):
            afa_sftp.send('ADH_20260807.csv', BODY, unattended=False)

    @override_settings(AFA_LOADFILE_AUTOSEND=True, AFA_SFTP_HOST='',
                       AFA_SFTP_USERNAME='', AFA_SFTP_PASSWORD='',
                       AFA_SFTP_PRIVATE_KEY_PATH='')
    def test_missing_config_names_what_is_missing_and_mentions_compose(self):
        with self.assertRaises(afa_sftp.AfaSendNotConfigured) as ctx:
            afa_sftp.send('f.csv', BODY)
        msg = str(ctx.exception)
        self.assertIn('AFA_SFTP_HOST', msg)
        self.assertIn('docker-compose', msg)            # checklist H24

    @override_settings(AFA_SFTP_HOST='h', AFA_SFTP_USERNAME='u',
                       AFA_SFTP_PASSWORD='', AFA_SFTP_PRIVATE_KEY_PATH='')
    def test_is_configured_needs_a_credential_not_just_a_host(self):
        self.assertFalse(afa_sftp.is_configured())

    @override_settings(AFA_SFTP_HOST='h', AFA_SFTP_USERNAME='u',
                       AFA_SFTP_PASSWORD='p', AFA_SFTP_PRIVATE_KEY_PATH='')
    def test_is_configured_with_a_password(self):
        self.assertTrue(afa_sftp.is_configured())


@override_settings(**CONF)
class TransferTests(_AfaSftpTestCase):

    def test_a_good_send_writes_to_a_temp_name_then_renames(self):
        fake = self.install_paramiko(_FakeSFTP())
        self.assertEqual(afa_sftp.send('ADH_20260807.csv', BODY), 'ADH_20260807.csv')
        self.assertEqual(fake.renames, [('ADH_20260807.csv.part', 'ADH_20260807.csv')])
        self.assertEqual(fake.files['ADH_20260807.csv'], BODY.encode('utf-8'))

    def test_afa_never_sees_the_temp_name_as_a_finished_file(self):
        fake = self.install_paramiko(_FakeSFTP())
        afa_sftp.send('ADH_20260807.csv', BODY)
        self.assertNotIn('ADH_20260807.csv.part', fake.files)

    def test_a_short_upload_is_removed_and_nothing_is_delivered(self):
        fake = self.install_paramiko(_FakeSFTP(short_by=5))
        with self.assertRaises(afa_sftp.AfaSendFailed) as ctx:
            afa_sftp.send('ADH_20260807.csv', BODY, attempts=1)
        self.assertIn('short', str(ctx.exception))
        self.assertIn('ADH_20260807.csv.part', fake.removed)
        self.assertEqual(fake.renames, [])

    def test_an_unreachable_drop_folder_fails_without_writing(self):
        fake = self.install_paramiko(_FakeSFTP(fail_on={'chdir'}))
        with self.assertRaises(afa_sftp.AfaSendFailed) as ctx:
            afa_sftp.send('f.csv', BODY, attempts=1)
        self.assertIn('drop folder', str(ctx.exception))
        self.assertEqual(fake.files, {})

    def test_a_drop_after_the_rename_is_never_retried(self):
        """The bytes may already be with AFA. Retrying could load the
        membership twice, and nobody has confirmed what AFA do with a
        duplicate (Phase 0 Q2)."""
        fake = self.install_paramiko(_FakeSFTP(drop_after_rename=True))
        with self.assertRaises(afa_sftp.AfaSendUnknown) as ctx:
            afa_sftp.send('ADH_20260807.csv', BODY, attempts=3)
        self.assertIn('NOT been retried', str(ctx.exception))
        self.assertEqual(len(fake.renames), 1)          # one attempt, not three

    def test_a_clean_failure_is_retried_then_reported(self):
        self.install_paramiko(_FakeSFTP(fail_on={'put'}))
        with mock.patch.object(afa_sftp.time, 'sleep', lambda *_: None):
            with self.assertRaises(afa_sftp.AfaSendFailed) as ctx:
                afa_sftp.send('f.csv', BODY, attempts=3)
        self.assertIn('3 attempts', str(ctx.exception))
