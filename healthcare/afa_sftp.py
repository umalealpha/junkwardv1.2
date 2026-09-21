"""
healthcare/afa_sftp.py — Phase 4 of the ADH → AFA load file.

Delivers the day's file to AFA's SFTP server, reusing the paramiko approach
already proven in `bank_feeds/services.py`.

TWO SWITCHES, NOT ONE.
  * `AFA_LOADFILE_AUTOSEND` gates the UNATTENDED cron only. Off by default,
    the same pattern as the FNB payment leg and the refund money leg.
  * A person clicking Release is NOT gated by it — otherwise the day you let
    Ritah send her first file you would also arm the cron to send every file
    unattended, which is exactly the fortnight of human oversight the rollout
    depends on.
A human Release still cannot send without configured credentials, and there
are none until AFA supply them.

WRITE, THEN RENAME. The file is uploaded under a temporary name and only
renamed into place once every byte is there, so AFA can never pick up a
half-written file and load a truncated membership.

NO BLIND RETRY. A transfer that failed outright is retried. A transfer whose
outcome is UNKNOWN — the connection died after the rename — is NOT retried
automatically, because nobody has yet confirmed what AFA do with the same file
arriving twice (Phase 0 Q2). It raises for a human instead.

CONFIG comes from the environment only, never from code:
    AFA_SFTP_HOST / _PORT / _USERNAME / _PASSWORD / _PRIVATE_KEY_PATH / _REMOTE_DIR
Every one of these must ALSO appear in docker-compose.yml's environment block.
A variable that is in .env but missing from compose arrives EMPTY inside the
container and fails silently forever (checklist H24 — this is how the intel
feed died).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from django.conf import settings

log = logging.getLogger('afa-loadfile')

TEMP_SUFFIX = '.part'
RETRY_DELAYS = (5, 20, 60)


class AfaSendDisabled(Exception):
    """Sending is switched off. The file was built but not delivered."""


class AfaSendNotConfigured(Exception):
    """The AFA SFTP settings are missing or empty."""


class AfaSendFailed(Exception):
    """The transfer failed. Nothing was delivered."""


class AfaSendUnknown(Exception):
    """The transfer may or may not have landed. NEVER retried automatically."""


def autosend_enabled() -> bool:
    return bool(getattr(settings, 'AFA_LOADFILE_AUTOSEND', False))


def _conf(name: str, default: str = '') -> str:
    return (getattr(settings, f'AFA_SFTP_{name}', '') or default).strip()


def is_configured() -> bool:
    return bool(_conf('HOST') and _conf('USERNAME')
                and (_conf('PASSWORD') or _conf('PRIVATE_KEY_PATH')))


def _require_config() -> dict:
    host, user = _conf('HOST'), _conf('USERNAME')
    missing = [n for n, v in (('HOST', host), ('USERNAME', user)) if not v]
    if not (_conf('PASSWORD') or _conf('PRIVATE_KEY_PATH')):
        missing.append('PASSWORD or PRIVATE_KEY_PATH')
    if missing:
        raise AfaSendNotConfigured(
            'AFA SFTP is not configured: missing '
            + ', '.join(f'AFA_SFTP_{m}' for m in missing)
            + '. Set them in /etc/alpha-finance/.env AND list them in '
              'docker-compose.yml — a variable absent from compose arrives empty.'
        )
    return {
        'host': host,
        'port': int(_conf('PORT', '22') or 22),
        'user': user,
        'password': _conf('PASSWORD'),
        'key_path': _conf('PRIVATE_KEY_PATH'),
        'remote_dir': _conf('REMOTE_DIR', '.') or '.',
    }


def _open_sftp(cfg: dict):
    try:
        import paramiko  # type: ignore
    except ImportError as exc:                     # pragma: no cover - env issue
        raise AfaSendNotConfigured(
            'paramiko is required to deliver the AFA load file.'
        ) from exc

    transport = paramiko.Transport((cfg['host'], cfg['port']))
    if cfg['key_path']:
        pkey = paramiko.RSAKey.from_private_key_file(cfg['key_path'])
        transport.connect(username=cfg['user'], pkey=pkey)
    else:
        transport.connect(username=cfg['user'], password=cfg['password'])
    sftp = paramiko.SFTPClient.from_transport(transport)
    if sftp is None:
        transport.close()
        raise AfaSendFailed('Could not open an SFTP channel to AFA.')
    return sftp, transport


def _put_once(cfg: dict, filename: str, body: bytes) -> None:
    """One attempt: upload to a temp name, then rename into place."""
    import io

    sftp, transport = _open_sftp(cfg)
    temp_name = filename + TEMP_SUFFIX
    # Set the moment the rename is ATTEMPTED, not after it returns. If the
    # connection dies mid-rename the file may already be in place under its
    # real name, so the outcome is UNKNOWN and must never be retried. Setting
    # this after the call left it False on exactly that failure and retried
    # three times — a duplicate membership load at AFA.
    rename_attempted = False
    try:
        try:
            sftp.chdir(cfg['remote_dir'])
        except IOError as exc:
            raise AfaSendFailed(
                f'AFA drop folder not reachable: {cfg["remote_dir"]} ({exc})'
            ) from exc

        sftp.putfo(io.BytesIO(body), temp_name, confirm=True)

        written = sftp.stat(temp_name).st_size
        if written != len(body):
            try:
                sftp.remove(temp_name)
            except IOError:
                pass
            raise AfaSendFailed(
                f'Upload was short: AFA received {written} of {len(body)} bytes. '
                'The partial file was removed and nothing was delivered.'
            )

        try:
            sftp.remove(filename)                  # a same-day rebuild replaces
        except IOError:
            pass
        rename_attempted = True
        sftp.rename(temp_name, filename)
    except (AfaSendFailed, AfaSendNotConfigured):
        raise
    except Exception as exc:                       # noqa: BLE001
        if rename_attempted:
            # The bytes are already in place under the real name but the
            # connection died before we could confirm. Retrying could deliver
            # the same membership twice, and nobody has confirmed what AFA do
            # with a duplicate (Phase 0 Q2). A person decides.
            raise AfaSendUnknown(
                f'The file was renamed into place but the connection dropped before '
                f'it could be confirmed ({exc.__class__.__name__}). It has NOT been '
                'retried — check with AFA whether it arrived before resending.'
            ) from exc
        raise AfaSendFailed(f'Transfer to AFA failed ({exc.__class__.__name__}).') from exc
    finally:
        try:
            sftp.close()
        finally:
            transport.close()


def send(filename: str, body: str, *, attempts: int = 3,
         unattended: bool = True) -> str:
    """Deliver one load file to AFA. Returns the delivered filename.

    `unattended=True` (the cron) additionally requires AFA_LOADFILE_AUTOSEND.
    `unattended=False` (a person clicked Release) does not — see the two-switch
    note at the top of this module.
    """
    if unattended and not autosend_enabled():
        raise AfaSendDisabled(
            'Automatic sending to AFA is switched off. The file was built and is '
            'waiting for someone to release it.'
        )
    cfg = _require_config()
    raw = body.encode('utf-8')

    last: Optional[Exception] = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            _put_once(cfg, filename, raw)
            log.info('afa: delivered %s (%s bytes) on attempt %s',
                     filename, len(raw), attempt)
            return filename
        except AfaSendUnknown:
            raise                                  # never retried
        except (AfaSendFailed, OSError) as exc:
            last = exc
            log.warning('afa: send attempt %s failed: %s', attempt, exc)
            if attempt < attempts:
                time.sleep(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])

    raise AfaSendFailed(
        f'Could not deliver {filename} to AFA after {attempts} attempts: {last}'
    )


# ---------------------------------------------------------------------------
# INBOUND — reading what AFA drop for us
# ---------------------------------------------------------------------------
# The ADH claims settlement file (B4) arrives FROM AFA rather than going to
# them. It was specified as an email attachment, but Omni's Microsoft mail
# registration is Mail.Send ONLY — it cannot open a mailbox (see the docstring
# in bonu/mailbox.py). The CFO settled it on 2026-09-13: AFA drop the file on
# the SFTP link we already run, and Omni reads it from there.
#
# Same credentials, same connection code, same config rules as the outbound
# leg above — only the direction changes. Reading is NOT gated by
# AFA_LOADFILE_AUTOSEND: that switch exists to stop us SENDING a membership
# file unattended, and reading a file AFA put there for us moves nothing.


def inbound_dir() -> str:
    """Where AFA drop files for us. Separate from the outbound folder so a file
    we wrote can never be read back as a file they sent."""
    return _conf('INBOUND_DIR', 'inbound') or 'inbound'


def listdir(remote_dir: Optional[str] = None) -> list[str]:
    """Names of the files AFA have dropped. Directories are not returned."""
    cfg = _require_config()
    sftp, transport = _open_sftp(cfg)
    try:
        target = remote_dir or inbound_dir()
        try:
            entries = sftp.listdir_attr(target)
        except IOError as exc:
            raise AfaSendFailed(
                f'AFA drop folder not readable: {target} ({exc})') from exc
        import stat as _stat
        return sorted(e.filename for e in entries
                      if not _stat.S_ISDIR(e.st_mode or 0))
    finally:
        try:
            sftp.close()
        finally:
            transport.close()


def read(filename: str, remote_dir: Optional[str] = None) -> bytes:
    """One dropped file, as bytes. Never deletes or renames what it read —
    AFA own their folder, and a file we removed is a file nobody can re-check."""
    cfg = _require_config()
    sftp, transport = _open_sftp(cfg)
    try:
        target = remote_dir or inbound_dir()
        try:
            sftp.chdir(target)
            with sftp.open(filename, 'rb') as fh:
                return fh.read()
        except IOError as exc:
            raise AfaSendFailed(
                f'Could not read {filename} from the AFA drop folder ({exc})'
            ) from exc
    finally:
        try:
            sftp.close()
        finally:
            transport.close()
