# CEO Omni Brief — engine

The daily "CEO Omni Brief" (and the Sunday board read) that reach the CEO,
cc the CFO. Live copies run from **`/opt/ceo-monitor/` on the omni host**, NOT
from this directory.

| file | what it does |
|---|---|
| `run.sh` | cron entry point: copies `ceo_engine.py` into the backend container, pipes `ceo_driver.py` into `manage.py shell` |
| `ceo_driver.py` | gathers the data (mail, meetings, tasks) and calls the engine |
| `ceo_engine.py` | scores/renders the HTML |
| `ceo_sunday_driver.py`, `run_sunday.sh` | the Sunday board read |
| `cfo_driver.py`, `run_cfo.sh` | the CFO's own morning brief (same engine, his mailbox) |

Cron (host, not in `install-crons.sh`):

    /etc/cron.d/ceo-brief        30 4 * * *  -> run.sh
    /etc/cron.d/ceo-board-read    0 5 * * 0  -> run_sunday.sh
    /etc/cron.d/cfo-brief        35 4 * * *  -> run_cfo.sh

`install-crons.sh` only ever DELETES an active file for a job it lists as
DISABLED, so these host-only jobs are safe from it. It does NOT write these cron
files — but since 13-Sep-2026 it DOES sync `/opt/ceo-monitor` from the repo and
report drift under `--check`, so running it is how a change here reaches the
host (see "Deploying a change" below).

The CFO brief runs five minutes after the CEO brief on purpose: both read the
same note table, and staggering them keeps two `docker compose exec` runs off
the same second.

MAILBOX CREDENTIALS DIFFER, and this is easy to get wrong:
  * `run.sh`     -> CEO_GRAPH_*    (from /opt/ceo-monitor/graph.env) -> aiyer@
  * `run_cfo.sh` -> GRAPH_READER_* (already in the container env)    -> pganesharajah@
The tenant's app-only access policy for GRAPH_READER_* is scoped to the Exchange
group `App-Scope-MailSend`, plus `pganesharajah@` and `aiyer@` by name. A mailbox
outside that — excoboard@, omni@ — returns "Blocked by tenant configured AppOnly
AccessPolicy settings", so do not point a driver at one expecting it to work.

Mailboxes are added by changing GROUP MEMBERSHIP, never the policy. On
16-Sep-2026 IT added `arjuniyer@` to `App-Scope-MailSend` and confirmed
`Test-ApplicationAccessPolicy` = Granted, which is what lets the CFO brief read
the COO's diary. Membership of `grp-omni-ceo-monitor` has never had any bearing
on this — that was the wrong group and cost two rounds of chasing.

This governs CALENDARS. The INBOX each brief digests is a separate decision made
in the driver, and the CFO brief still digests `pganesharajah@` alone.

## Why these files are in the repo

Until 2026-08-21 they existed **only** on the host, versioned by `.bak.<date>`
copies and hand-deployed. Nothing tested them, `install-crons.sh` did not manage
them, and a rebuild would have lost them. The copies here were taken from prod
and verified byte-identical by md5:

    ceo_driver.py         d126c8b651ace772924ccc27c40f7020  (pre-patch)
    ceo_engine.py         8c44496fc5f53ef685841a6448a78645
    ceo_sunday_driver.py  508fd507ab9b5c9b52be2aa1951042d0
    run.sh                60eb7f2e8d988f07942a0584ba5cde43
    run_sunday.sh         18a963b8b67d80f6eef853847f422783

`graph.env` (the Microsoft Graph client secret) is deliberately NOT here and
must never be committed. It stays at `/opt/ceo-monitor/graph.env` on the host.

## Deploying a change

These do not ride the normal image build. `run_cfo.sh` pipes the HOST copy into
`manage.py shell`, so a green deploy leaves the old brief running for ever — that
happened on 13-Sep-2026 and was caught only by md5summing the host copy. After
deploying anything in this folder:

    sudo bash infra/install-crons.sh            # syncs /opt/ceo-monitor from the repo
    sudo bash infra/install-crons.sh --check    # must report no DRIFT

Then prove it on the host rather than trusting the deploy:

    md5sum /opt/ceo-monitor/cfo_driver.py /opt/alpha-finance/infra/ceo-monitor/cfo_driver.py

Business rules belong in the Django app where they can be tested, not in these
files. `fetch_waiting_on_ceo` calls
`hris.exec_signoff_service.is_routine_signoff_task`, which has tests in
`hris/tests/test_signoff_sibling_tasks.py`; the inline fallback there exists
only so an older backend image cannot kill the 04:30 brief on an import error.
