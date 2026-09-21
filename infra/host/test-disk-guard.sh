#!/usr/bin/env bash
# Proves the "0a. DISK GUARD + BACKUP RETENTION" block in deploy-zero-downtime.sh,
# with df / docker / aws stubbed. Asserts the four things the 9-Sep-2026 half-deploy
# taught us:
#   A. plenty of room  -> the block is silent and does not exit
#   B. short of room, reclaim gets us there -> carries on
#   C. short of room, reclaim does NOT      -> aborts BEFORE anything is built
#   D. retention deletes only dumps that are the same size in S3, and only old ones
# Run: bash infra/host/test-disk-guard.sh
set -uo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)/deploy-zero-downtime.sh"
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
BIN="$WORK/bin"; mkdir -p "$BIN"

START=$(grep -n '^# ---- 0a\. DISK GUARD' "$SRC" | cut -d: -f1)
END=$(grep -n '^# ---- 0\. BACKUP FIRST' "$SRC" | cut -d: -f1)
[ -n "$START" ] && [ -n "$END" ] || { echo "FAIL: could not find the DISK GUARD block"; exit 1; }
sed -n "${START},$((END-1))p" "$SRC" > "$WORK/block.sh"

# ---- stubs -------------------------------------------------------------------------
# df reports FREE_1 on the first call and FREE_2 on every call after it, so a test can
# say "reclaiming helped" or "reclaiming did not help".
cat > "$BIN/df" <<'EOF'
#!/usr/bin/env bash
C="$STUB_DF_COUNT_FILE"; n=$(cat "$C" 2>/dev/null); n=${n:-0}; echo $((n+1)) > "$C"
if [ "$n" -eq 0 ]; then g="$STUB_FREE_1"; else g="${STUB_FREE_2:-$STUB_FREE_1}"; fi
echo "Filesystem 1G-blocks Used Available Capacity Mounted"
echo "/dev/root 96 90 $g 95% /"
EOF
cat > "$BIN/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $*" >> "$STUB_LOG"
EOF
# aws head-object: the file's real local size, unless the name is in STUB_S3_MISSING.
cat > "$BIN/aws" <<'EOF'
#!/usr/bin/env bash
key=""; for a in "$@"; do [ "$prev" = "--key" ] && key="$a"; prev="$a"; done
base=$(basename "$key")
case " ${STUB_S3_MISSING:-} " in *" $base "*) echo "MISSING"; exit 1 ;; esac
sz=$(stat -c %s "$STUB_BK_DIR/$base" 2>/dev/null || echo 0)
case " ${STUB_S3_WRONG_SIZE:-} " in *" $base "*) echo $((sz-1)); exit 0 ;; esac
echo "$sz"
EOF
if ! stat -c %s "$SRC" >/dev/null 2>&1; then
  cat > "$BIN/stat" <<'EOF'
#!/usr/bin/env bash
# BSD shim: only the `stat -c %s <file>` form the script uses.
[ "$1" = "-c" ] && [ "$2" = "%s" ] && exec /usr/bin/stat -f %z "$3"
exec /usr/bin/stat "$@"
EOF
fi
chmod +x "$BIN"/*
export PATH="$BIN:$PATH"

run_block() {  # run_block <free1> <free2>
  : > "$WORK/dfcount"; : > "$WORK/log"
  STUB_DF_COUNT_FILE="$WORK/dfcount" STUB_LOG="$WORK/log" \
  STUB_FREE_1="$1" STUB_FREE_2="${2:-$1}" STUB_BK_DIR="$WORK/backups" \
  STUB_S3_MISSING="${S3_MISSING:-}" STUB_S3_WRONG_SIZE="${S3_WRONG:-}" \
  bash -c "BK_DIR='$WORK/backups'; DISK_MIN_GB=12; RETAIN_DAYS=7; \
           say() { echo \"[t] \$*\"; }; source '$WORK/block.sh'; \
           echo __REACHED_END__" 2>&1
}

# touch -d is GNU-only; -t works on both. Compute the stamp with whichever date we have.
stamp_days_ago() {
  date -d "$1 days ago" +%Y%m%d%H%M 2>/dev/null || date -v-"$1"d +%Y%m%d%H%M
}
OLD30=$(stamp_days_ago 30); OLD40=$(stamp_days_ago 40)

seed_backups() {
  rm -rf "$WORK/backups"; mkdir -p "$WORK/backups"
  for n in old_a old_b; do
    head -c 2048 /dev/zero > "$WORK/backups/alpha_finance_predeploy_2026-08-01T00-00-00Z_$n.sql.gz"
    touch -t "$OLD30" "$WORK/backups/alpha_finance_predeploy_2026-08-01T00-00-00Z_$n.sql.gz"
  done
  head -c 2048 /dev/zero > "$WORK/backups/alpha_finance_predeploy_2026-09-09T00-00-00Z_new.sql.gz"
  head -c 2048 /dev/zero > "$WORK/backups/alpha_finance_predeploy_2026-08-01T00-00-00Z_unsafe.sql.gz"
  touch -t "$OLD30" "$WORK/backups/alpha_finance_predeploy_2026-08-01T00-00-00Z_unsafe.sql.gz"
  # things retention must NEVER touch, both old
  head -c 100 /dev/zero > "$WORK/backups/alpha_finance_predeploy_TEST_2026-07-31T08-43-10Z.sql.gz"
  touch -t "$OLD40" "$WORK/backups/alpha_finance_predeploy_TEST_2026-07-31T08-43-10Z.sql.gz"
  echo '{}' > "$WORK/backups/merged_duplicate_vendors_20260725.json"
  touch -t "$OLD40" "$WORK/backups/merged_duplicate_vendors_20260725.json"
}

fails=0
ck() { if eval "$2"; then echo "  ok   — $1"; else echo "  FAIL — $1"; fails=$((fails+1)); fi; }

echo "A. plenty of room"
seed_backups; OUT=$(run_block 40 40)
ck "does not abort"                'grep -q __REACHED_END__ <<<"$OUT"'
ck "says nothing about reclaiming" '! grep -qi "reclaiming" <<<"$OUT"'
ck "deletes nothing"               '[ "$(ls -1 "$WORK/backups" | wc -l)" -eq 6 ]'

echo "B. short, and reclaiming gets us there"
seed_backups; S3_MISSING="" OUT=$(run_block 3 40)
ck "does not abort"          'grep -q __REACHED_END__ <<<"$OUT"'
ck "says it reclaimed"       'grep -q "reclaimed" <<<"$OUT"'
ck "pruned the build cache"  'grep -q "builder prune" "$WORK/log"'

echo "C. short, and reclaiming does NOT get us there"
seed_backups; OUT=$(run_block 3 4)
ck "ABORTS before the build" '! grep -q __REACHED_END__ <<<"$OUT"'
ck "says nothing changed"    'grep -q "Nothing has changed" <<<"$OUT"'

echo "D. retention only removes what is provably in S3"
seed_backups
S3_MISSING="alpha_finance_predeploy_2026-08-01T00-00-00Z_unsafe.sql.gz" OUT=$(run_block 3 40)
ck "removed the two verified old dumps" '[ ! -f "$WORK/backups/alpha_finance_predeploy_2026-08-01T00-00-00Z_old_a.sql.gz" ] && [ ! -f "$WORK/backups/alpha_finance_predeploy_2026-08-01T00-00-00Z_old_b.sql.gz" ]'
ck "KEPT the one missing from S3"       '[ -f "$WORK/backups/alpha_finance_predeploy_2026-08-01T00-00-00Z_unsafe.sql.gz" ]'
ck "KEPT today's dump"                  '[ -f "$WORK/backups/alpha_finance_predeploy_2026-09-09T00-00-00Z_new.sql.gz" ]'
ck "KEPT the TEST dump"                 '[ -f "$WORK/backups/alpha_finance_predeploy_TEST_2026-07-31T08-43-10Z.sql.gz" ]'
ck "KEPT the vendor-merge record"       '[ -f "$WORK/backups/merged_duplicate_vendors_20260725.json" ]'
ck "reported one kept as unverified"    'grep -q "kept 1 that are not verified" <<<"$OUT"'

echo
if [ "$fails" -eq 0 ]; then echo "test-disk-guard: PASS"; else echo "test-disk-guard: $fails FAILURE(S)"; exit 1; fi
