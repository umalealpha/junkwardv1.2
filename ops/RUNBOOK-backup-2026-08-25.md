# Runbook — fix the off-site database backup (prepared 25 Aug 2026, NOT APPLIED)

**Status: HELD.** The CFO's instruction is that nothing goes to the live server
until he says go. Every step below is ready to run and has not been run.

## What is actually wrong

Three overlapping backup jobs, one of which works.

| # | Job | When | State |
|---|---|---|---|
| 1 | `/usr/local/bin/alpha-finance-backup.sh` → S3 (root, `/etc/cron.d/alpha-finance-backup`) | 00:00 UTC | **works.** 1,516 objects in `s3://alphadirect-db-backups-capetown/alpha-finance/daily/`, ~140MB a night. Bucket is SSE-AES256, public access fully blocked, 30d→IA / 90d→Glacier / 365d expiry. Two weaknesses: the dump goes up as **plain gzipped SQL**, and nothing alerts on a missed night. |
| 2 | `ops/backup_daily.sh` → GitHub, as `ubuntu` | 04:00 | **dead since 7 July 2026.** Dumps and encrypts fine, then the push is refused: `File dumps/omni-2026-08-25.sql.gz.gpg is 139.05 MB; this exceeds GitHub's file size limit of 100.00 MB`. Remote HEAD is still `9c9a4f5 backup 2026-07-07 size=74MB`. |
| 3 | the **same** `ops/backup_daily.sh`, as `root` | 04:00 | **fails nightly at encrypt** — root has no GPG key. Worse, both 04:00 jobs `tee` to the same `backup-YYYYMMDD.log`, so the log a person reads shows root's `No public key` and hides job 2 entirely. |

Why this went unnoticed for seven weeks: the artifact and the dry-run both keep
passing. Only the last step fails. Nothing was watching the last step.

## The decision (CFO, 25 Aug 2026)

Retire the GitHub leg. Harden the Amazon leg and watch it.

## Steps

Run as yourself via EC2 Instance Connect / SSM on `i-02a5d76a61f4f09a5`.

**1. Stop the two failing 04:00 jobs.**

```bash
sudo crontab -l | grep -v 'ops/backup_daily.sh' | sudo crontab -
crontab -l | grep -v 'ops/backup_daily.sh' | crontab -
sudo crontab -l | grep -c backup_daily   # expect 0
crontab -l | grep -c backup_daily        # expect 0
```

**2. Put the hardened S3 script in place.** It now lives in the repo
(`ops/backup_s3_daily.sh`) instead of only on this one server, which is what the
cron file's own comment asked for.

```bash
cd /opt/alpha-finance && sudo -u ubuntu git pull
sudo cp /usr/local/bin/alpha-finance-backup.sh /usr/local/bin/alpha-finance-backup.sh.pre-2026-08-25
sudo install -m 0755 /opt/alpha-finance/ops/backup_s3_daily.sh /usr/local/bin/alpha-finance-backup.sh
```

**3. Give root the encryption key.** The new script *refuses to run* without it
rather than silently uploading plaintext — so this step is not optional. The
public key is already in `ubuntu`'s keyring (`4BB3686BD867019B21378968B43E3D7AEDF07326`);
the private key stays offline with the CFO.

```bash
sudo -u ubuntu gpg --export -a 'Alpha Direct ERP Backup' | sudo gpg --import
sudo gpg --list-keys 'Alpha Direct ERP Backup'   # must show the key
```

**4. Prove it before trusting it.** Run it by hand once and look.

```bash
sudo /usr/local/bin/alpha-finance-backup.sh
AWS_DEFAULT_REGION=af-south-1 aws s3 ls \
  s3://alphadirect-db-backups-capetown/alpha-finance/daily/ | tail -3
# expect today's object, name ending .sql.gz.gpg
AWS_DEFAULT_REGION=af-south-1 aws s3 cp \
  s3://alphadirect-db-backups-capetown/alpha-finance/last-success.txt -
```

**5. Install the watcher.**

```bash
sudo install -m 0644 /opt/alpha-finance/infra/cron/backup-watch.cron \
  /etc/cron.d/backup-watch
sudo mkdir -p /var/log/alpha-finance
/opt/alpha-finance/ops/backup_watch.sh --self-test   # expect: self-test OK
sudo /opt/alpha-finance/ops/backup_watch.sh          # expect: OK off-site copy is current
```

**6. Prove the alarm actually rings.** A watcher nobody has seen fire is a
watcher nobody should trust — this is the whole lesson of the seven weeks.

```bash
# Point it at a nonexistent marker so it must complain, then check the mail.
S3_PREFIX=alpha-finance-nope sudo -E /opt/alpha-finance/ops/backup_watch.sh
# expect: BACKUP NOT VERIFIED: ... and an email to the CFO
```

## 7. Restore drill — 🔴 needs the CFO

This is the step that decides whether any of the above is worth anything, and it
is the one I cannot finish: decrypting needs the **private** key, which is
offline with the CFO by design.

```bash
# 1. Pull last night's copy (I can do this)
AWS_DEFAULT_REGION=af-south-1 aws s3 cp \
  s3://alphadirect-db-backups-capetown/alpha-finance/daily/<name>.sql.gz.gpg /tmp/

# 2. Decrypt — CFO's offline private key required (only the CFO can do this)
gpg --decrypt /tmp/<name>.sql.gz.gpg > /tmp/restore.sql.gz

# 3. Restore into a throwaway database and count rows against prod (I can do this)
sudo -u postgres createdb restore_drill_20260825
gunzip -c /tmp/restore.sql.gz | sudo -u postgres psql -d restore_drill_20260825
sudo -u postgres psql -d restore_drill_20260825 -c \
  "select count(*) from payments_payment; select count(*) from ledger_journalentry;"

# 4. Destroy the copy — it is a full customer database
sudo -u postgres dropdb restore_drill_20260825
rm -f /tmp/<name>.sql.gz.gpg /tmp/restore.sql.gz
```

Record the row counts and the date. An untested backup is a belief, not a control.

## Rollback

Steps 1–5 are reversible in under a minute:

```bash
sudo rm -f /etc/cron.d/backup-watch
sudo install -m 0755 /usr/local/bin/alpha-finance-backup.sh.pre-2026-08-25 \
  /usr/local/bin/alpha-finance-backup.sh
```

The retired 04:00 jobs are deliberately *not* restored by rollback: one has
produced nothing since 7 July and the other has never worked at all.
