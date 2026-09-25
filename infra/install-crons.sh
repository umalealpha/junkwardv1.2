#!/usr/bin/env bash
# Install / refresh the host cron jobs from the repo so they survive an
# instance rebuild. Runs on the omni EC2 HOST (not inside a container) — cron
# fires on the host and shells into the backend container.
#
# Source of truth: infra/cron/<name>.cron  ->  /etc/cron.d/<name>
#   (dotless target, 0644, root:root).  cron.d / run-parts ignores any file
#   with a dot in the name, which is why the target drops the ".cron" suffix.
#
# Three lists drive it, and every infra/cron/*.cron file must appear in exactly
# one of them (the installer WARNs about any that appears in none):
#   ENABLED   — installed and kept active.
#   DISABLED  — the schedule is version-controlled but held OFF: the installer
#               removes any active /etc/cron.d/<name> and writes a
#               <name>.DISABLED marker. This captures the operator's on/off
#               decision in the repo so a rebuild reproduces it (previously the
#               .DISABLED marker lived only on the mutable host -> a rebuild
#               silently re-enabled the job).
#   EXCLUDED  — deliberately not managed here (kept only as documented below).
#
# Safety:
#   * Must run as root (writes /etc/cron.d) — guarded below.
#   * Idempotent — safe to run on every deploy; a clean run is a no-op.
#   * Atomic per-file writes (temp + mv) so cron never reads a half-written file.
#   * The ONLY delete it performs is removing an active file for a DISABLED job.
#   * Ensures the shared log dir exists so redirected jobs actually run.
#
# Invoked by golive.sh on each deploy; also safe to run by hand:
#   sudo bash /opt/alpha-finance/infra/install-crons.sh
# Dry run (report drift, change nothing, non-zero exit on drift):
#   sudo bash /opt/alpha-finance/infra/install-crons.sh --check
set -uo pipefail

CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

if [ "$(id -u)" -ne 0 ]; then
    echo "install-crons.sh: must run as root (writes /etc/cron.d)" >&2
    exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/cron" && pwd)"
DST_DIR="/etc/cron.d"
LOG_DIR="/var/log/alpha-finance"

# Canonical production cron set (keep in sync with /etc/cron.d on the omni EC2).
ENABLED=(
    # Intraday hours reminders. Held OFF here since build, "until the CFO
    # approves go-live" — approved 2026-09-09 once the Time-Doctor hours floor
    # shipped, so it belongs in ENABLED now.
    #
    # THIS LINE IS WHY THE FILE KEPT SWITCHING ITSELF OFF. It sat in DISABLED
    # below, and step 2 of this script deletes the active file and re-lays the
    # .DISABLED marker on EVERY run — and deploy-zero-downtime.sh runs this
    # script on every deploy. Someone restored the file by hand on 30 August and
    # the next deploy removed it again the following night. The watchdog emailed
    # the CFO nine times and the cause was read as "nobody knows what does it";
    # it was this list all along, doing exactly what it was told.
    hours-reminders
    realpay-reconcile
    reinsurer-approval-expiry
    salary-advance-mismatch
    monthly-feedback
    hr-contracts-and-group-payroll
    underwriting-adoption
    alpha-finance-backup
    backup-watch
    aware-reports
    release-drift
    devlog-digest
    bug-triage
    compliance-brain
    presummarise-claims
    consolidated-ops-digest
    discretionary-leave-report
    dpo-checklist
    duplicate-account-watch
    close-leaver-access
    access-report
    exceptions-report
    failed-debits-report
    weekly-claims-update
    exec-provider-dashboard
    finance-monitoring
    fnb-email-autoclose
    fnb-sync
    instant-admin-fees
    graphite-claims-sync
    helpdesk-reminder
    leave-digest
    late-reminder
    cfo-brief
    m365-license-sync
    premium-refund-import
    manager-accountability
    manager-objectives
    morning-brief
    omni-leave-excuse
    omni-push
    omni-recurring-incentives
    omni-watchdog
    pin-access
    purge-old-cvs
    task-incentive
    task-confirm-digest
    row-watchdog
    snapshot-provider-counts
    fnb-health-watch
    fnb-three-way-check
    bank-integrity-watch
    bank-balances
    # WS1 outbound state bus retry worker (2026-09-26): pushes out any Graphite
    # write-back that failed its live send and is now past its backoff.
    outbound-bus-drain
    payment-ageing-escalation
    staff-loan-rate
    # staff-loan-repayments moved to DISABLED below — the orchestrator owns the
    # loan step now. See the note there.
    payroll-monthly-orchestration
    task-reminders
    stuck-work-sweep
    subrogation-escalation
    td-chase
    td-enforce
    td-reconcile
    timedoctor-token-check
    timedoctor-pull
    timedoctor-healthcheck
    timedoctor-watch
    welcome-back-digest
    workforce-daily-brief
    workforce-offboarding
    # Sunday CEO Workforce Pulse (L-PULSE, CFO 18-Sep-2026). 18:00 CAT. The
    # command itself HOLDS the CEO send while Time Doctor coverage is short.
    ceo-pulse
    # ADH claims EFT settlement loader (B4). Saturday 00:00 UTC = 02:00 CAT,
    # after AFA's four files have landed. Raises payment requests only.
    adh-settlement-loader
    # Seven jobs that were LIVE on the box but in none of these lists, so the
    # installer only WARNed about them and a rebuild would not have brought
    # them back. Checked against the box 13-Sep-2026 before adding: five were
    # byte-identical to the repo, so listing them changes nothing except that
    # they now survive a rebuild.
    #
    # The other two - authority-signature-reminders and manus-activity-digest -
    # differed in one way that matters: the live copies call the management
    # command DIRECTLY, while the repo wraps it in `run_job <name> --`. run_job
    # is the switch gate. So those two jobs were ignoring their on/off switch
    # in Omni entirely: turning them off changed nothing and nobody would have
    # known. Listing them here puts both back under their switch. run_job fails
    # OPEN, so a switch lookup that errors still runs the job.
    authority-signature-reminders
    frozen-screen-shadow
    manus-activity-digest
    payment-daily-digest
    quote-expiry-reminder
    screen-integrity
    screen-integrity-digest
    tax-compliance
    transformation-pulse
)

# Version-controlled but held OFF (nexus tester reminders were switched off by
# the operator; keep them off across rebuilds).
DISABLED=(
    nexus-reminder
    # THE DUPLICATE LOAN RUN (CFO, payroll.docx §4, 16-Sep-2026).
    # staff-loan-repayments fired `0 4 20 * *` — the SAME minute as
    # payroll-monthly-orchestration, which already owns the loan step. Same
    # function, same period, no lock between them: whichever lost the race hit
    # the (loan, period) unique key and reported as a FAILED loan step, so a
    # correct system looked broken every month. The live file was moved to
    # .disabled-20260916 by hand on the box that morning; listing it here is
    # what makes that stick, because the installer REMOVES an active file for a
    # DISABLED job and leaves the .DISABLED marker — a comment or a deletion
    # would have let the next rebuild put the duplicate straight back.
    # The orchestrator is the single owner of the loan run.
    # Red-proven by payroll/tests/test_single_loan_schedule.py.
    staff-loan-repayments
    # (FNB health watch was here from 2026-08-23 to 2026-09-13. The CFO turned it
    # off because both its alerts were noise — Omni recorded FNB's "425 not
    # processed yet" as a FAILURE, so a healthy batch looked broken. He asked for
    # it back on 2026-09-13, once 425s read as WAITING and the job runs daily with
    # a 48-hour stuck threshold instead of hourly at two hours. It is now in
    # ENABLED above.)
)

# In the repo for reference but intentionally NOT auto-managed:
#   manual-update — the nightly "What's New" user-manual updater
#     (infra/cron/manual-update.sh: read-only `git log ... | update_user_manual`,
#     dedupes by sha). It is not currently deployed on the host; enable it
#     deliberately (move to ENABLED) if the manual should refresh nightly.
EXCLUDED=(
    manual-update
)

if [ ! -d "$LOG_DIR" ]; then
    if [ "$CHECK" -eq 1 ]; then
        echo "DRIFT log dir missing: $LOG_DIR"
    else
        mkdir -p "$LOG_DIR" && echo "MKDIR $LOG_DIR"
    fi
fi

installed=0; disabled=0; failed=0; drift=0; warned=0

in_list() { local x="$1"; shift; local e; for e in "$@"; do [ "$e" = "$x" ] && return 0; done; return 1; }

# 0) the deploy-window-safe runner every record-writing job calls (2026-09-04)
install -o root -g root -m 0755 "$SRC_DIR/../host/omni-manage.sh" /usr/local/bin/omni-manage

# 1) install / refresh the ENABLED jobs
for name in "${ENABLED[@]}"; do
    src="$SRC_DIR/$name.cron"
    if [ ! -e "$src" ]; then
        echo "MISS  $name (no $src in repo)"; warned=$((warned + 1)); continue
    fi
    dst="$DST_DIR/$name"
    # Clear a stale marker FIRST. A job that moves from DISABLED back to
    # ENABLED leaves its old <name>.DISABLED behind; cron.d ignores a dotted
    # filename so it schedules nothing, but /etc/cron.d then lists
    # "fnb-health-watch" and "fnb-health-watch.DISABLED" side by side and
    # nothing says which is true. This has to run BEFORE the "already current"
    # skip below, or the marker outlives every later run - which is exactly
    # what happened on the first run after the FNB health watch came back on:
    # the cron file was already correct, the loop skipped, and the marker
    # stayed. (13-Sep-2026, caught by reading /etc/cron.d back off the box.)
    marker_stale="$DST_DIR/$name.DISABLED"
    if [ -e "$marker_stale" ]; then
        if [ "$CHECK" -eq 1 ]; then
            echo "DRIFT $name.DISABLED (stale marker, job is enabled)"; drift=$((drift + 1))
        else
            rm -f "$marker_stale" && echo "CLEAR $name.DISABLED (job is enabled again)"
        fi
    fi
    if [ -e "$dst" ] && cmp -s "$src" "$dst"; then
        continue                                   # already current - no-op
    fi
    if [ "$CHECK" -eq 1 ]; then
        echo "DRIFT $name (would install/update)"; drift=$((drift + 1)); continue
    fi
    tmp="$(mktemp "$DST_DIR/.$name.XXXXXX")"
    if install -o root -g root -m 0644 "$src" "$tmp" && mv -f "$tmp" "$dst"; then
        echo "OK    $name"; installed=$((installed + 1))
    else
        rm -f "$tmp"; echo "FAIL  $name"; failed=$((failed + 1))
    fi
done

# 2) enforce DISABLED: remove any active file, ensure the .DISABLED marker
for name in "${DISABLED[@]}"; do
    active="$DST_DIR/$name"
    marker="$DST_DIR/$name.DISABLED"
    if [ "$CHECK" -eq 1 ]; then
        { [ -e "$active" ] || [ ! -e "$marker" ]; } && { echo "DRIFT $name (should be disabled)"; drift=$((drift + 1)); }
        continue
    fi
    [ -e "$active" ] && rm -f "$active" && echo "OFF   $name (removed active file)"
    if [ ! -e "$marker" ]; then
        src="$SRC_DIR/$name.cron"
        [ -e "$src" ] && install -o root -g root -m 0644 "$src" "$marker" || : > "$marker"
        echo "MARK  $name.DISABLED"
    fi
    disabled=$((disabled + 1))
done

# 2b) refresh the ceo-monitor host scripts from the repo
#
# WHY THIS EXISTS. The CEO/CFO brief drivers run on the HOST, not inside the
# backend image - run_cfo.sh pipes /opt/ceo-monitor/cfo_driver.py into
# `manage.py shell`. So a deploy rebuilds the image and changes NOTHING about
# them: the repo copy moves, the host copy does not. Measured 13-Sep-2026,
# right after a deploy that reported success: the CFO had asked on 12-Sep for
# his brief to show people decisions instead of payments, the change was
# merged, tested and deployed - and the file the 04:30 job actually reads was
# still the old one. It would have gone on sending the old brief for ever, with
# every gate green. ceo_sunday_driver.py was stale too, missing the
# Africa/Gaborone date fix.
#
# Repo is the source of truth. Only files that exist in the repo are touched,
# so host-only files (graph.env, the .bak history) are left alone, and the
# previous version is kept once per day before it is replaced.
CEO_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ceo-monitor"
CEO_DST="/opt/ceo-monitor"
if [ -d "$CEO_SRC" ] && [ -d "$CEO_DST" ]; then
    for src in "$CEO_SRC"/*.py "$CEO_SRC"/*.sh; do
        [ -e "$src" ] || continue
        n="$(basename "$src")"
        dst="$CEO_DST/$n"
        cmp -s "$src" "$dst" 2>/dev/null && continue
        if [ "$CHECK" -eq 1 ]; then
            echo "DRIFT ceo-monitor/$n (host differs from repo)"; drift=$((drift + 1))
            continue
        fi
        [ -e "$dst" ] && cp -p "$dst" "$dst.bak.$(date -u +%Y%m%d)"
        # run_cfo.sh and friends are executed, not read - a 0644 copy would
        # break the very job this step exists to keep working.
        case "$n" in *.sh) mode=0755 ;; *) mode=0644 ;; esac
        if install -o root -g root -m "$mode" "$src" "$dst"; then
            echo "SYNC  ceo-monitor/$n"
        else
            echo "FAIL  ceo-monitor/$n"; failed=$((failed + 1))
        fi
    done
fi

# 3) warn about any repo cron file not accounted for in a list
for src in "$SRC_DIR"/*.cron; do
    [ -e "$src" ] || continue
    n="$(basename "$src" .cron)"
    if ! in_list "$n" "${ENABLED[@]}" && ! in_list "$n" "${DISABLED[@]}" && ! in_list "$n" "${EXCLUDED[@]}"; then
        echo "WARN  $n is in infra/cron/ but not in ENABLED/DISABLED/EXCLUDED"; warned=$((warned + 1))
    fi
done

if [ "$CHECK" -eq 1 ]; then
    echo "--- cron check: $drift drift, $warned warning(s) ---"
    [ "$drift" -eq 0 ] || exit 2
    exit 0
fi

echo "--- cron sync: $installed installed, $disabled disabled, $failed failed, $warned warning(s) ---"
[ "$failed" -eq 0 ] || { echo "CRON-SYNC FAILED ($failed job(s))" >&2; exit 1; }
