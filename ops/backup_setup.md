# Omni Daily Backup — Setup Runbook

**Target machine:** prod EC2 `i-02a5d76a61f4f09a5` (af-south-1)
**Backup repo:** https://github.com/alphadirectinsurance/ERP-OMNI-backup (private)
**Cadence:** 06:00 Africa/Maputo daily, 30-day rolling retention
**Encryption:** GPG, public key on EC2, CFO holds the private key offline

The script `ops/backup_daily.sh` is committed in this repo. The setup
steps below run **once** to wire it onto prod. Re-run anytime to verify
state — every step is idempotent.

## 1. Generate the GPG keypair (run on prod EC2 as ubuntu)

```bash
sudo -i -u ubuntu
gpg --batch --gen-key <<'GPG'
%no-protection
Key-Type: RSA
Key-Length: 4096
Subkey-Type: RSA
Subkey-Length: 4096
Name-Real: Alpha Direct ERP Backup
Name-Email: backup@alphadirect.co.bw
Expire-Date: 0
%commit
GPG
gpg --list-keys "Alpha Direct ERP Backup"
```

Then export the **private** key, download it to the CFO's Mac, **delete
it from the EC2 instance**. The CFO stores the private key offline (1Password
secure note, an encrypted USB stick — *not* in email or chat):

```bash
# On EC2:
gpg --output /tmp/erp-backup-private.asc --armor --export-secret-keys "Alpha Direct ERP Backup"
chmod 600 /tmp/erp-backup-private.asc
# From CFO laptop:
aws ssm start-session --target i-02a5d76a61f4f09a5
# then `cat /tmp/erp-backup-private.asc` and copy the entire ASCII block
# into a secure note; verify decrypt locally with `gpg --import` on a
# trusted machine before the next step.

# Back on EC2 — DELETE THE PRIVATE KEY:
rm -f /tmp/erp-backup-private.asc
gpg --delete-secret-keys "Alpha Direct ERP Backup"   # leave only the public
```

The public key stays on EC2 so the backup script can encrypt. Only the
CFO's offline copy can decrypt.

## 2. Wire a deploy key for the backup repo

```bash
sudo -i -u ubuntu
ssh-keygen -t ed25519 -N "" -f ~/.ssh/erp_omni_backup -C "omni-backup-bot@$(hostname)"
cat ~/.ssh/erp_omni_backup.pub
```

Copy the pub line. Then via gh CLI on the CFO Mac (or in the GitHub UI):

```bash
gh repo deploy-key add ~/.ssh/erp_omni_backup.pub \
    -R alphadirectinsurance/ERP-OMNI-backup \
    -t "omni-backup-bot" --allow-write
```

Add an SSH alias so the script's `git push` uses the right key:

```bash
cat >> ~/.ssh/config <<'SSH'
Host github-backup
  HostName github.com
  User git
  IdentityFile ~/.ssh/erp_omni_backup
  IdentitiesOnly yes
SSH
chmod 600 ~/.ssh/config
ssh -T git@github-backup    # should print "Hi alphadirectinsurance/ERP-OMNI-backup!"
```

## 3. Install the cron entry

Schedule `06:00 Africa/Maputo` = `04:00 UTC`:

```bash
sudo crontab -u ubuntu -e
# Append:
0 4 * * * /opt/alpha-finance/ops/backup_daily.sh >> /var/log/alpha-finance/cron.log 2>&1
```

## 4. First dry-run + first real run

```bash
sudo mkdir -p /var/log/alpha-finance
sudo chown ubuntu:ubuntu /var/log/alpha-finance

sudo -u ubuntu /opt/alpha-finance/ops/backup_daily.sh --dry-run
# inspect output. If clean, run for real:
sudo -u ubuntu /opt/alpha-finance/ops/backup_daily.sh
```

## 5. Restore drill (every 90 days)

Pick a recent encrypted dump and verify it decrypts + restores into a
throwaway Postgres instance. Stops the "backup works but restore
doesn't" silent failure mode.

```bash
gpg --decrypt omni-2026-05-19.sql.gz.gpg | gunzip > /tmp/omni.sql
docker run --rm -e POSTGRES_PASSWORD=test -p 55432:5432 -d --name omni-restore-test postgres:16-alpine
sleep 6
PGPASSWORD=test psql -h 127.0.0.1 -p 55432 -U postgres -d postgres -c 'create database omni_restore;'
PGPASSWORD=test psql -h 127.0.0.1 -p 55432 -U postgres -d omni_restore < /tmp/omni.sql
PGPASSWORD=test psql -h 127.0.0.1 -p 55432 -U postgres -d omni_restore -c "select count(*) from ledger_journalentry;"
docker rm -f omni-restore-test
rm /tmp/omni.sql
```

## 6. Monitoring

The script writes `/var/log/alpha-finance/backup-YYYYMMDD.log` each
night. To get notified on failure, add a one-liner cron right after the
backup line that pings a webhook if the log doesn't contain
"backup_daily done":

```bash
5 4 * * * tail -10 /var/log/alpha-finance/backup-$(date -u +\%Y\%m\%d).log | grep -q "backup_daily done" || curl -sX POST <slack/webhook> -d '{"text":"omni backup FAILED"}'
```
