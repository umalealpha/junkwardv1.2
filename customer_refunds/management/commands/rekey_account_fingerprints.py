"""Recompute every stored bank-account fingerprint after a keying change.

WHY THIS EXISTS
---------------
`account_fingerprint()` is a keyed HMAC blind index. Two things change what it
produces: the key (`settings.REFUND_ACCOUNT_INDEX_KEY`, added 2026-09-11) and
the canonicalisation (leading zeros are now stripped, to match Graphite).

Change either and every fingerprint already on file is keyed the OLD way. The
same-account fraud check then stops matching across that line — and it fails
SILENTLY: a lookup for a fingerprint simply returns nothing, which is
indistinguishable from "this account has never been seen before". Graphite ran
the same exercise over its own history (Pramod Bisen, 2026-09-11); this is
Omni's half, and it must run AFTER the key is set and BEFORE refunds are armed.

Safe to re-run: it recomputes from the encrypted account number every time, so
a second run over already-correct rows is a no-op.

    manage.py rekey_account_fingerprints             # dry run, changes nothing
    manage.py rekey_account_fingerprints --commit    # write them
"""
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from customer_refunds.models import CustomerRefund, account_fingerprint


class Command(BaseCommand):
    help = ('Recompute CustomerRefund.account_fingerprint after a change to '
            'REFUND_ACCOUNT_INDEX_KEY or to how account numbers are '
            'canonicalised. Dry run unless --commit.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--commit', action='store_true',
            help='Write the new fingerprints. Without this, nothing is saved.')
        parser.add_argument(
            '--allow-secret-key-fallback', action='store_true',
            help='Re-key even though REFUND_ACCOUNT_INDEX_KEY is blank. Only for '
                 'a canonicalisation-only re-key; the result will NOT be keyed '
                 'the same as Graphite.')

    # -- account-sharing groups, the thing the fraud check actually reads ----
    def _groups(self, pairs):
        """How many accounts are shared by more than one refund, and by how many.

        Reported before and after so a re-key that DESTROYS matching (rather
        than preserving it) is visible instead of passing quietly.
        """
        counts = Counter(fp for _, fp in pairs if fp)
        shared = {fp: n for fp, n in counts.items() if n > 1}
        return len(shared), sum(shared.values())

    def handle(self, *args, **opts):
        commit = opts['commit']
        # Say which key we are about to hash under. Declaring the setting fixed
        # "the environment variable never reached the code"; it does NOT remove
        # the silent fallback underneath it. Re-keying 37 rows under SECRET_KEY
        # while believing they are keyed with Graphite recreates the original
        # defect one step to the right, again with no error anywhere.
        shared_key = getattr(settings, 'REFUND_ACCOUNT_INDEX_KEY', '')
        if shared_key:
            self.stdout.write('key                    : '
                              'REFUND_ACCOUNT_INDEX_KEY (shared with Graphite)')
        else:
            self.stdout.write(self.style.WARNING(
                'key                    : FALLBACK to SECRET_KEY '
                '(NOT shared with Graphite)'))
            if commit and not opts['allow_secret_key_fallback']:
                raise CommandError(
                    'Refusing to re-key under the SECRET_KEY fallback. Set '
                    'REFUND_ACCOUNT_INDEX_KEY to the value agreed with Graphite '
                    'and rebuild the backend image first. If you really mean a '
                    'canonicalisation-only re-key, pass '
                    '--allow-secret-key-fallback.')

        rows = list(CustomerRefund.objects.all().only(
            'id', 'account_number_enc', 'account_fingerprint'))
        self.stdout.write(f'refunds on file        : {len(rows)}')

        before, after = [], []
        changed, undecryptable, no_account = [], 0, 0

        for r in rows:
            before.append((r.id, r.account_fingerprint))
            if not r.account_number_enc:
                no_account += 1
                after.append((r.id, r.account_fingerprint))
                continue
            try:
                raw = r.get_account_number()
            except Exception as exc:          # noqa: BLE001 - belt and braces
                raw, why = '', type(exc).__name__
            else:
                why = 'decrypt returned nothing'

            if not raw:
                # THIS ROW HAS CIPHERTEXT BUT WE COULD NOT READ IT. core.decrypt()
                # RETURNS '' on a bad/retired Fernet key, it does not raise — so
                # an `except` around it never fires and a plain
                # `account_fingerprint(raw) if raw else ''` would WRITE '' here,
                # dropping the refund out of the fraud check completely. That is
                # the precise failure this command exists to prevent, keyed on a
                # value ('') that also legitimately means "no account". Count it,
                # leave the old fingerprint alone, and say so out loud.
                undecryptable += 1
                after.append((r.id, r.account_fingerprint))
                self.stderr.write(self.style.WARNING(
                    f'   refund {r.id}: cannot decrypt account number '
                    f'({why}) — fingerprint LEFT AS IS'))
                continue

            new_fp = account_fingerprint(raw)
            after.append((r.id, new_fp))
            if new_fp != r.account_fingerprint:
                changed.append((r, new_fp))

        sb, mb = self._groups(before)
        sa, ma = self._groups(after)
        self.stdout.write(f'no account number      : {no_account}')
        self.stdout.write(f'could not decrypt      : {undecryptable}')
        self.stdout.write(f'fingerprints to change : {len(changed)}')
        self.stdout.write(
            f'shared-account groups  : {sb} ({mb} refunds) before '
            f'-> {sa} ({ma} refunds) after')
        # Compare the REFUNDS inside shared groups, not the group COUNT. The
        # count goes DOWN on the intended outcome (a 0621... group and a 621...
        # group merging into one) and stays LEVEL on the damage case (one row
        # stranded out of a 4-refund group leaves the count alone while the
        # membership drops 4 -> 3). Membership is what the fraud check reads.
        if ma < mb:
            self.stdout.write(self.style.WARNING(
                f'   WARNING: {mb - ma} refund(s) dropped out of a shared-account '
                'group. Matching got WORSE, not better. Check the key and the '
                'canonicalisation before committing.'))

        if not changed:
            self.stdout.write(self.style.SUCCESS('Nothing to change.'))
            return

        if not commit:
            self.stdout.write(self.style.WARNING(
                'DRY RUN — nothing written. Re-run with --commit.'))
            return

        with transaction.atomic():
            for r, new_fp in changed:
                r.account_fingerprint = new_fp
            CustomerRefund.objects.bulk_update(
                [r for r, _ in changed], ['account_fingerprint'], batch_size=200)
        self.stdout.write(self.style.SUCCESS(
            f'Re-keyed {len(changed)} fingerprint(s).'))
