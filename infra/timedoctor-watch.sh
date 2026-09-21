#!/usr/bin/env bash
# Hourly Time Doctor access watch — CFO 19-Sep-2026.
#
# Time Doctor blocked Omni from 17-Sep 13:50 UTC until Arjun reactivated the
# CFO's account on 19-Sep. The only alarm was check_timedoctor_token at 05:30
# UTC once a day, email only. This watch probes every hour and speaks ONLY when
# the state changes:
#   ok   -> down : urgent email to CFO/Arjun/Unami (the existing command, not a
#                  copy) + Telegram to the CFO.
#   down -> ok   : Telegram to the CFO that it works again.
# An unclear probe (backend unreachable, network blip, unexpected error) changes
# nothing and alerts no one — only a clear 401/403/denied counts as "down".
#
# Runs on the omni EC2 HOST from infra/cron/timedoctor-watch.cron. The Telegram
# key lives only in the telegrambot container, so the message is sent from there.
# Every docker call can be overridden for tests: TDW_PROBE, TDW_ALERT, TDW_TELEGRAM.
set -uo pipefail

if [ -z "${TDW_PROBE:-}" ]; then
    cd /opt/alpha-finance || { echo "$(date -u) cannot cd /opt/alpha-finance"; exit 1; }
fi
DC="/usr/bin/docker compose --env-file /etc/alpha-finance/.env"
STATE="${TDW_STATE:-/var/lib/alpha-finance/timedoctor-access.state}"
PROBE="${TDW_PROBE:-$DC exec -T backend python manage.py check_timedoctor_token --dry-run}"
ALERT="${TDW_ALERT:-$DC exec -T backend python manage.py check_timedoctor_token}"
TELEGRAM="${TDW_TELEGRAM:-$DC exec -T -e TDW_MSG telegrambot python -c}"

stamp() { date -u '+%Y-%m-%d %H:%M UTC'; }

send_telegram() {
    # $1 = message text, handed over in the environment (never pasted into code).
    # Chat = the bot's authorised user (the CFO, sole entry). Succeeds only on 'telegram sent'.
    local py out
    py="import os
from decouple import config, Csv
from core.telegram_bot.bot import _send_message
ids = config('TELEGRAM_BOT_AUTHORIZED_USERS', default='', cast=Csv())
ok = bool(ids) and _send_message(config('TELEGRAM_BOT_TOKEN'), int(ids[0]), os.environ['TDW_MSG'], parse_mode='')
print('telegram sent' if ok else 'telegram NOT sent')"
    out="$(TDW_MSG="$1" $TELEGRAM "$py" 2>&1)"
    echo "$out" | tail -1
    grep -q 'telegram sent' <<<"$out"
}

mkdir -p "$(dirname "$STATE")" || { echo "$(stamp) cannot create state dir"; exit 1; }
prev="$(cat "$STATE" 2>/dev/null || echo ok)"
# A damaged state file must not hide an outage: treat it as unknown, so the
# next clear reading always speaks.
case "$prev" in ok|down) ;; *) echo "$(stamp) state file unreadable ('$prev')"; prev=unknown ;; esac
out="$($PROBE 2>&1)"

if grep -q 'rejected the live probe' <<<"$out"; then
    now=down
elif grep -q 'live probe succeeded' <<<"$out"; then
    now=ok
else
    echo "$(stamp) unclear probe, state left at '$prev': $(tail -1 <<<"$out")"
    exit 0
fi

if [ "$now" = "$prev" ]; then
    echo "$(stamp) $now (no change)"
    exit 0
fi

delivered=1
if [ "$now" = down ]; then
    echo "$(stamp) Time Doctor went DOWN — alerting"
    mail_out="$($ALERT 2>&1)"
    echo "$mail_out" | tail -2
    if grep -q 'alert emailed to .*send()=[1-9]' <<<"$mail_out"; then
        mail_note="Arjun and Unami have been emailed"
    else
        mail_note="the email to Arjun and Unami FAILED — retrying next hour"
        delivered=0
    fi
    send_telegram "🔴 Time Doctor has blocked Omni again (access denied). Staff hours and the workforce reports have stopped updating. $mail_note — the account used by Omni needs reactivating in Time Doctor." || delivered=0
elif [ "$prev" = unknown ]; then
    echo "$(stamp) ok (state file rebuilt)"
else
    echo "$(stamp) Time Doctor is back UP — telling the CFO"
    send_telegram "✅ Time Doctor is working again — Omni can read staff hours. Missed days will fill in on the next pull." || delivered=0
fi

# Only remember the new state once the alert actually went out; otherwise the
# next hour tries again rather than going quiet.
if [ "$delivered" = 1 ]; then
    echo "$now" > "$STATE"
else
    echo "$(stamp) alert NOT fully delivered — will retry next hour"
fi
