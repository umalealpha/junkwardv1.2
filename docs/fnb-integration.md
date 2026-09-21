# FNB Botswana banking integration

## Quick map

| Concern | Where it lives |
|---|---|
| HTTP + OAuth2 client | `fnb/client.py` |
| Endpoint paths | `fnb/endpoints.py` |
| Outbound payments (pain.001) | `fnb/payments.py` |
| Statements (camt.053-ish) | `fnb/statements.py` |
| Notifications (pull every minute) | `fnb/notifications.py` |
| Inbound webhook (if FNB enables it later) | `fnb/webhooks.py` |
| Audit trail | `FNBSyncLog`, `FNBBatchSubmission`, `FNBWebhookEvent` |
| Maker-checker queue | `payments/` app (no FNB knowledge required) |

## API endpoints exposed by omni

| Path | Method | Use |
|---|---|---|
| `/api/v1/fnb/status/`              | GET  | Is the integration configured? Last sync? |
| `/api/v1/fnb/test-connection/`     | POST | Ping FNB with current credentials |
| `/api/v1/fnb/submit-batch/`        | POST | Submit approved Payments as an EFT batch |
| `/api/v1/fnb/batches/<id>/refresh/`| POST | Poll FNB for the latest status on this batch |
| `/api/v1/fnb/batch-submissions/`   | GET  | Audit log of every submitted batch |
| `/api/v1/fnb/webhook/`             | POST | Receiver (if FNB ever sends push notifications) |

## Maker-checker flow

```
 ┌───────────────┐    create draft     ┌──────────────┐    approve   ┌──────────────┐
 │  AP clerk     │───────────────────► │  Payment     │────────────► │  Approved    │
 │ (Maker)       │                     │  status=draft│              │  status=     │
 └───────────────┘                     └──────────────┘   ▲          │  approved    │
                                                          │ (Checker)└──────┬───────┘
                                                          │                 │
                                                  ┌───────┴────────┐        │ POST /fnb/submit-batch/
                                                  │ CFO / finance  │        ▼
                                                  │ approver       │ ┌────────────────┐
                                                  └────────────────┘ │ FNBBatchSubmis │
                                                                     │ status=        │
                                                                     │   submitted    │
                                                                     └────────┬───────┘
                                                                              │ FNB retrieveReport
                                                                              ▼
                                                                     ┌────────────────┐
                                                                     │   completed /  │
                                                                     │   failed       │
                                                                     └────────────────┘
```

A Payment can only be picked up by `submit_eft_batch` when `status == 'approved'` —
this is enforced inside `FNBSubmitBatchView` before the FNB API call fires. There
is no way to bypass maker-checker; the API filters and silently drops any
non-approved IDs.

## Configuration

All FNB settings live in `/etc/alpha-finance/.env` on prod (root-owned, separate
from code). Never paste secrets in chat or commit them to git.

```ini
# Base URL — confirmed by FNB Botswana on technical-session call
FNB_API_BASE=https://api.fnb.co.za/apigateway

# OAuth2 client_credentials
FNB_AUTH_MODE=oauth2_client_credentials
FNB_AUTH_URL=https://api.fnb.co.za/apigateway/oauth2/token/v2
FNB_OAUTH_SCOPE=i_can
FNB_CLIENT_ID=<from-fnb>
FNB_CLIENT_SECRET=<from-fnb>

# ISO 20022 / pain.001 fields stamped on every outbound message
FNB_INITIATING_PARTY_NAME=Alpha Direct Insurance Co. (Pty) Ltd
FNB_DEBTOR_BIC=FIRNBWGX
FNB_DEBTOR_BRANCH_ID=<branch-id-from-fnb>
FNB_DEBTOR_ACCOUNT_TYPE=CACC

# Network
FNB_TIMEOUT_SECONDS=15
```

To rotate credentials:

```
sudo nano /etc/alpha-finance/.env       # edit values
sudo docker compose --env-file /etc/alpha-finance/.env -f /opt/alpha-finance/docker-compose.yml up -d backend
```

## Cron jobs

```cron
*/1 * * * *  ubuntu  cd /opt/alpha-finance && sudo docker exec alpha-finance-backend python manage.py poll_fnb_notifications >> /var/log/alpha-finance/fnb-notifications.log 2>&1
0   6 * * *  ubuntu  cd /opt/alpha-finance && sudo docker exec alpha-finance-backend python manage.py pull_fnb_statements --all --days 2 >> /var/log/alpha-finance/fnb-statements.log 2>&1
*/5 * * * *  ubuntu  cd /opt/alpha-finance && sudo docker exec alpha-finance-backend python manage.py shell -c 'from fnb.models import FNBBatchSubmission as B; from fnb.payments import refresh_batch_status; [refresh_batch_status(b) for b in B.objects.filter(status="submitted")[:50]]'
```

## Sandbox → production cutover checklist

1. Sandbox creds in `/etc/alpha-finance/.env` (`FNB_API_BASE=api.i.fnb.co.za/...`).
2. `python manage.py test_fnb_connection` returns 200.
3. Submit a P 1.00 self-payment batch; confirm it lands in FNB sandbox.
4. Pull the statement back; confirm the P 1.00 line appears in `BankStatementLine`.
5. Wait for FNB to confirm sandbox tests reviewed.
6. Swap `FNB_API_BASE` + creds to production. Repeat steps 2-4 with a P 1.00
   live payment to a known account.
7. Flip the `payments/` UI's "Submit to FNB" button from sandbox-only flag
   to default-on.

## Going live for real vendor payments

Only after step 7 above and the CFO's written sign-off. The first live batch
should be small (≤ 5 payments, total ≤ P 100,000) and reconciled inside the
hour against the FNB statement before scaling up.
