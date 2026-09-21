#!/usr/bin/env bash
# skills-sync.sh — share ~/.claude/skills between the Windows PC and the Mac Mini,
#                  and PROVE the other machine can see it.
#
# WHY THIS EXISTS
# ---------------
# Skills lived in ~/.claude/skills on BOTH machines with NO channel between them.
# Google Drive and OneDrive were both retired, so a skill written on one machine
# could never reach the other by itself — and nothing said so. On 2026-09-10 a
# /PratThis update was reported as shared twice in one morning; it never left the
# PC, while the Mac's fixed copy never reached the PC. Both sides had unique work
# and a naive copy in either direction would have destroyed the other's.
#
# This is the CFO's Option A (2026-09-11): put the skills under version control in
# this repo, leaving MACHINE-TALK.md exactly where he locked it.
#
# USAGE
#   infra/skills-sync.sh pull     # bring down what the other machine shared
#   infra/skills-sync.sh push     # share this machine's skills, then verify
#   infra/skills-sync.sh status   # what differs, change nothing
#   infra/skills-sync.sh baseline # first run only: declare this machine level
#                                 # with origin, after reconciling by hand
#
# WHAT IT SYNCS
#   Skill TEXT only (.md/.py/.sh/.mjs/.js/.ts/.tsx/.json/.txt/.yml/.yaml/.css/
#   .html/.toml under 512KB). Not nested git checkouts, node_modules,
#   3d-templates, venvs, QC screenshots or images — that is 628MB of downloaded
#   assets that no skill needs in order to load and run.
#
# SAFETY — push NEVER deletes, and NEITHER SIDE overwrites blind
#   * push only adds and updates; a skill on origin that is missing here is left
#     alone. Otherwise the first machine to push would silently wipe every skill
#     the other had just written.
#   * Both directions refuse to overwrite a file that changed on BOTH machines
#     since the last successful sync (a three-way check against the recorded base
#     commit in $SKILLS_DIR/.skills-sync-base). That is exactly the 10-Sep
#     /PratThis divergence, where each side held work the other lacked and a copy
#     either way destroyed real work. A conflict stops the run and names the
#     files; merge them by hand, then re-run.
#   Deleting a shared skill is deliberate: do it by hand, in its own PR.
#
#   A file that differs is only a CONFLICT when BOTH sides moved. Compare each
#   side against the base:
#     local == base  -> only the other machine moved  -> pull takes it, push skips
#     origin == base -> only this machine moved       -> push shares it, pull skips
#     neither        -> both moved                    -> CONFLICT, stop, touch nothing
#   Testing only one of those two is not a stricter version of this rule, it is a
#   broken one: PC edits skill A while Mac edits skill B, and every ordinary day
#   deadlocks with both machines refusing both commands.
#
#   FIRST RUN on a machine (no base recorded): every differing file is a conflict,
#   which is the safe reading but has no way out on its own. Reconcile the files by
#   hand, then `skills-sync.sh baseline` to declare "this machine is now level with
#   origin", then push. baseline is deliberately a separate, human-only command —
#   it is the one place that can lose work, so nothing calls it automatically.
#
# LINE ENDINGS — the trap that would have made this lie
#   This PC has core.autocrlf=true, so `git archive` hands back CRLF for anything
#   git treats as text. A pushed LF file would come back CRLF, `cmp` would call
#   every file different, push would report FAILED for files that ARE on the
#   server, and pull would rewrite every skill on every run. `.gitattributes`
#   carries `infra/skills/** -text` to stop git normalising these paths at all.
#   Do not remove that line.
set -euo pipefail

CMD="${1:-status}"
SKILLS_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
REPO="${SKILLS_SYNC_REPO:-$(git rev-parse --show-toplevel)}"
SUB="infra/skills"
BASEFILE="$SKILLS_DIR/.skills-sync-base"
cd "$REPO"

[ -d "$SKILLS_DIR" ] || { echo "skills-sync: no skills folder at $SKILLS_DIR" >&2; exit 2; }

# The one definition of "a skill file", used by push, pull and status alike.
list_skill_files() {   # $1 = root to scan; prints paths relative to that root
    ( cd "$1" && find . \
        -type d \( -name .git -o -name node_modules -o -name 3d-templates \
                   -o -name .venv -o -name shots -o -name __pycache__ \) -prune -o \
        -type f \( -name '*.md' -o -name '*.py' -o -name '*.sh' -o -name '*.mjs' \
                   -o -name '*.js' -o -name '*.ts' -o -name '*.tsx' -o -name '*.json' \
                   -o -name '*.txt' -o -name '*.yml' -o -name '*.yaml' -o -name '*.css' \
                   -o -name '*.html' -o -name '*.toml' \) \
        -size -524288c ! -name 'qc-report.json' -print | sed 's|^\./||' | sort )
    # NOTE: -size -524288c is in BYTES on purpose. `-size -1M` matches NOTHING —
    # GNU find rounds every file up to whole megabytes, so "< 1M" is never true.
    # That trap cost this script its first run.
    #
    # qc-report.json is REGENERATED by every QC run with whatever route was last
    # checked. Syncing it makes a conflict certain on almost every push — the
    # first real push after this one already hit it — and a tool that cries
    # conflict when nothing is wrong is a tool people stop running. It is output,
    # not skill content. (fabe's runs.jsonl ledger is already out of scope: .jsonl
    # is not in the list above.)
}

# Are these two files the SAME SKILL? Line endings are not content.
#
# This machine writes CRLF (237 of the 469 files first shared were CRLF); the Mac
# writes LF. A byte-exact comparison therefore reports every one of those as
# "changed on both machines" even when the words are identical. Measured
# 11-Sep-2026: the Mac's first pull stopped on 173 conflicts, almost all of them
# invisible characters, which is a wall of hand-merging for nothing.
#
# Worse than the noise: byte-exactness would have started a line-ending WAR. The
# PC pushes CRLF, the Mac pulls and stores CRLF, the Mac's next edit saves LF and
# pushes it back, the PC pulls LF... forever, with each machine's file endlessly
# "changed". Comparing as text ends that — a file whose only difference is line
# endings is never staged and never pulled, so neither machine ever flips it.
#
# Bytes are still stored and copied exactly (.gitattributes `-text`); it is only
# the SAME-OR-NOT question that ignores \r. cmp first, because on the same machine
# almost everything matches byte-for-byte and that path spawns nothing.
same_text() {          # $1, $2 = files; 0 if the same skill
    cmp -s "$1" "$2" && return 0
    [ -f "$1" ] && [ -f "$2" ] || return 1
    cmp -s <(tr -d '\r' < "$1") <(tr -d '\r' < "$2")
}

# Extract a committed copy of $SUB. Never masked: an extraction that fails while
# the tree exists must stop the run, not quietly present an empty remote as
# "in sync" — that is the failure-that-looks-like-success this script exists to end.
extract_sub() {        # $1 = commit-ish, $2 = destination dir; 1 if not present
    git cat-file -e "$1:$SUB" 2>/dev/null || return 1
    git archive "$1" "$SUB" | tar -x -C "$2"
}

# mktemp -t NAME fails on Windows Git Bash ("too few X's in template"); the
# XXXXXX suffix is required there and harmless on macOS.
TMP="$(mktemp -d -t sksync.XXXXXX)"
cleanup_tmp() { rm -rf "$TMP"; }
trap cleanup_tmp EXIT

git fetch origin -q

# The base = the commit this machine last successfully synced with. Files that
# changed on only one side since then fast-forward; files that changed on BOTH
# are conflicts. No base recorded (first ever run) => every differing file is a
# conflict, which is the safe reading.
BASE_SHA=""
[ -f "$BASEFILE" ] && BASE_SHA="$(tr -d '[:space:]' < "$BASEFILE")"
BASEDIR="$TMP/base"; mkdir -p "$BASEDIR"
HAVE_BASE=0
if [ -n "$BASE_SHA" ] && extract_sub "$BASE_SHA" "$BASEDIR" 2>/dev/null; then
    HAVE_BASE=1
fi
base_copy() { [ "$HAVE_BASE" = 1 ] && [ -f "$BASEDIR/$SUB/$1" ]; }

# Record the commit this machine is now level with. Takes an explicit commit when
# one is known (a push records the exact commit IT produced, never whatever
# origin/main happens to be by then — the other machine can merge in between, and
# recording that would read as THIS machine's newer work on the next pull and let
# the next push overwrite it).
record_base() { if [ $# -gt 0 ]; then printf '%s\n' "$1" > "$BASEFILE"; else git rev-parse origin/main > "$BASEFILE"; fi; }

# The conflict recovery recipe. "Merge by hand then re-run" does not terminate: a
# hand-merged file equals neither base nor origin, so the very next run conflicts
# again, forever. This is the sequence that actually ends.
print_recovery() {     # $1 = file list
    echo "skills-sync: to resolve, run these FROM INSIDE $REPO." >&2
    echo "skills-sync: for EACH file listed below (shown as <file>):" >&2
    echo "  1. Keep your own version:    mkdir -p /tmp/mine/\$(dirname <file>) && cp \"$SKILLS_DIR/<file>\" \"/tmp/mine/<file>\"" >&2
    echo "  2. Take the shared version:  git show origin/main:$SUB/<file> > \"$SKILLS_DIR/<file>\"" >&2
    echo "  3. Then run:                 $0 pull     # no conflict now; also brings any missing skills" >&2
    echo "  4. Re-apply your own edits from /tmp/mine/<file>, keeping BOTH sides' work (the 10-Sep /PratThis lesson)." >&2
    echo "  5. Then run:                 $0 push" >&2
    [ -s "$1" ] && { echo "skills-sync: the <file> values:" >&2; sed 's|^|  |' "$1" >&2; }
}

case "$CMD" in

status)
    REMOTE="$TMP/remote"; mkdir -p "$REMOTE"
    HAVE_REMOTE=0
    if extract_sub origin/main "$REMOTE"; then HAVE_REMOTE=1; fi
    RDIR="$REMOTE/$SUB"; [ -d "$RDIR" ] || mkdir -p "$RDIR"
    echo "skills-sync: comparing $SKILLS_DIR with origin/main:$SUB"
    [ "$HAVE_REMOTE" = 1 ] || echo "skills-sync: (nothing shared on origin/main yet)"
    only_local=0; only_remote=0; differ=0; seen=0
    while IFS= read -r f; do
        seen=$((seen+1))
        if [ ! -f "$RDIR/$f" ]; then echo "  + only here:   $f"; only_local=$((only_local+1))
        elif ! same_text "$SKILLS_DIR/$f" "$RDIR/$f"; then echo "  ~ differs:     $f"; differ=$((differ+1)); fi
    done < <(list_skill_files "$SKILLS_DIR")
    [ "$seen" -gt 0 ] || { echo "skills-sync: FAILED — scanned $SKILLS_DIR and found no skill files at all." >&2; exit 1; }
    while IFS= read -r f; do
        [ -f "$SKILLS_DIR/$f" ] || { echo "  - only shared: $f"; only_remote=$((only_remote+1)); }
    done < <(list_skill_files "$RDIR")
    echo "skills-sync: $only_local only here, $only_remote only shared, $differ differ."
    [ $((only_local+only_remote+differ)) -eq 0 ] && echo "skills-sync: in sync."
    exit 0
    ;;

pull)
    REMOTE="$TMP/remote"; mkdir -p "$REMOTE"
    extract_sub origin/main "$REMOTE" || {
        echo "skills-sync: nothing shared on origin/main yet — run 'push' first."; exit 0; }
    RDIR="$REMOTE/$SUB"
    # Decide everything BEFORE copying anything, so a conflict leaves the machine untouched.
    TO_COPY="$TMP/tocopy"; CONFLICTS="$TMP/conflicts"; : > "$TO_COPY"; : > "$CONFLICTS"
    seen=0; skipped_mine=0
    while IFS= read -r f; do
        seen=$((seen+1))
        if [ ! -f "$SKILLS_DIR/$f" ]; then printf '%s\n' "$f" >> "$TO_COPY"; continue; fi
        same_text "$RDIR/$f" "$SKILLS_DIR/$f" && continue
        # Differs — ask which side actually moved.
        if base_copy "$f" && same_text "$SKILLS_DIR/$f" "$BASEDIR/$SUB/$f"; then
            printf '%s\n' "$f" >> "$TO_COPY"            # only origin moved: take it
        elif base_copy "$f" && same_text "$RDIR/$f" "$BASEDIR/$SUB/$f"; then
            skipped_mine=$((skipped_mine+1))            # only this machine moved: leave it, push will share it
        else
            printf '%s\n' "$f" >> "$CONFLICTS"          # both moved (or no base): stop
        fi
    done < <(list_skill_files "$RDIR")
    [ "$seen" -gt 0 ] || { echo "skills-sync: FAILED — origin/main:$SUB extracted but contained no skill files." >&2; exit 1; }
    if [ -s "$CONFLICTS" ]; then
        echo "skills-sync: STOPPED — changed on BOTH machines since the last sync:" >&2
        sed 's/^/  ! /' "$CONFLICTS" >&2
        echo "skills-sync: nothing was changed on this machine." >&2
        [ "$HAVE_BASE" = 1 ] || echo "skills-sync: this is the FIRST run on this machine, so every file that differs from the shared copy is listed above." >&2
        print_recovery "$CONFLICTS"
        exit 1
    fi
    n=0
    while IFS= read -r f; do
        mkdir -p "$SKILLS_DIR/$(dirname "$f")"
        cp "$RDIR/$f" "$SKILLS_DIR/$f"
        echo "  updated: $f"; n=$((n+1))
    done < "$TO_COPY"
    # Safe with skips: a file left alone here has origin == base, so recording the
    # new base keeps it correctly readable as "only this machine moved".
    record_base
    echo "skills-sync: pulled $n file(s) into $SKILLS_DIR."
    [ "$skipped_mine" -eq 0 ] || echo "skills-sync: $skipped_mine file(s) left alone — this machine's own newer work. Run 'push' to share them."
    exit 0
    ;;

baseline)
    # The one command that can lose work, so it is never called automatically and
    # never runs on a machine that already has a base.
    if [ -f "$BASEFILE" ]; then
        echo "skills-sync: this machine already has a base ($(cat "$BASEFILE")). baseline is for a first run only." >&2
        echo "skills-sync: if you truly need to reset it, delete $BASEFILE by hand first." >&2
        exit 2
    fi
    REMOTE="$TMP/remote"; mkdir -p "$REMOTE"
    extract_sub origin/main "$REMOTE" || {
        echo "skills-sync: nothing shared on origin/main yet — there is no baseline to declare. Run 'push'." >&2; exit 1; }
    RDIR="$REMOTE/$SUB"
    # Only files present on BOTH sides can be divergent. A file this machine simply
    # does not have yet is not divergence — pull will fetch it — and counting those
    # made baseline impossible to satisfy on the very machine it exists for.
    differ=0; missing=0
    while IFS= read -r f; do
        if [ ! -f "$SKILLS_DIR/$f" ]; then missing=$((missing+1)); continue; fi
        same_text "$RDIR/$f" "$SKILLS_DIR/$f" || { echo "  ~ still differs: $f"; differ=$((differ+1)); }
    done < <(list_skill_files "$RDIR")
    if [ "$differ" -gt 0 ]; then
        echo "skills-sync: REFUSED — $differ shared file(s) above are not yet reconciled on this machine." >&2
        echo "skills-sync: baseline means 'this machine is level with origin'. Declaring it now would make the next push overwrite the other machine's work." >&2
        exit 1
    fi
    record_base
    echo "skills-sync: baseline recorded at $(cat "$BASEFILE") — every shared file this machine has matches origin/main."
    [ "$missing" -eq 0 ] || echo "skills-sync: $missing shared file(s) are not on this machine yet — run 'pull' to get them."
    echo "skills-sync: this machine's own extra skills are untouched; run 'push' to share them."
    exit 0
    ;;

push) ;;
*) echo "skills-sync: usage: $0 {pull|push|status|baseline}" >&2; exit 2 ;;
esac

# ---------------- push ----------------
command -v gh >/dev/null || { echo "skills-sync: the gh CLI is required." >&2; exit 2; }

BR="chore/skills-sync-$(date +%Y%m%d-%H%M%S)-$$"
WT="$(mktemp -d -t skwt.XXXXXX)"
# rm -rf as well as worktree remove: if `git worktree add` itself fails, $WT is
# just a directory and `worktree remove` will not clean it up.
cleanup() { cd "$REPO" 2>/dev/null || true; git worktree remove --force "$WT" >/dev/null 2>&1 || true; rm -rf "$WT"; cleanup_tmp; }
trap cleanup EXIT

# Always work in a throwaway checkout. Committing in the shared working copy
# lands on a parallel session's branch — that has bitten this repo twice.
git worktree add -q --detach "$WT" origin/main
mkdir -p "$WT/$SUB"

STAGE="$TMP/tostage"; CONFLICTS="$TMP/conflicts"; BEHIND="$TMP/behind"
: > "$STAGE"; : > "$CONFLICTS"; : > "$BEHIND"
seen=0
while IFS= read -r f; do
    seen=$((seen+1))
    if [ ! -f "$WT/$SUB/$f" ]; then printf '%s\n' "$f" >> "$STAGE"; continue; fi
    same_text "$SKILLS_DIR/$f" "$WT/$SUB/$f" && continue
    # Differs — ask which side actually moved.
    if base_copy "$f" && same_text "$WT/$SUB/$f" "$BASEDIR/$SUB/$f"; then
        printf '%s\n' "$f" >> "$STAGE"             # only this machine moved: share it
    elif base_copy "$f" && same_text "$SKILLS_DIR/$f" "$BASEDIR/$SUB/$f"; then
        printf '%s\n' "$f" >> "$BEHIND"            # only origin moved: this machine is behind
    else
        printf '%s\n' "$f" >> "$CONFLICTS"         # both moved (or no base): stop
    fi
done < <(list_skill_files "$SKILLS_DIR")

[ "$seen" -gt 0 ] || { echo "skills-sync: FAILED — scanned $SKILLS_DIR and found no skill files at all. Nothing was shared." >&2; exit 1; }

if [ -s "$CONFLICTS" ]; then
    echo "skills-sync: STOPPED — changed on BOTH machines since the last sync:" >&2
    sed 's/^/  ! /' "$CONFLICTS" >&2
    echo "skills-sync: nothing was pushed." >&2
    [ "$HAVE_BASE" = 1 ] || echo "skills-sync: this is the FIRST run on this machine." >&2
    print_recovery "$CONFLICTS"
    exit 1
fi

# Refuse to push while behind. Pushing anyway would record a base this machine has
# not reached, and the next pull would then read those files as a false conflict.
if [ -s "$BEHIND" ]; then
    echo "skills-sync: STOPPED — the other machine has newer versions of:" >&2
    sed 's/^/  < /' "$BEHIND" >&2
    echo "skills-sync: nothing was pushed. Run 'skills-sync.sh pull' first, then push." >&2
    exit 1
fi

n=0
while IFS= read -r f; do
    mkdir -p "$WT/$SUB/$(dirname "$f")"
    cp "$SKILLS_DIR/$f" "$WT/$SUB/$f"
    n=$((n+1))
done < "$STAGE"

if [ "$n" -eq 0 ]; then
    echo "skills-sync: nothing to share — origin/main already has this machine's skills."
    record_base
    exit 0
fi

git -C "$WT" add "$SUB"
git -C "$WT" -c user.name="${GIT_AUTHOR_NAME:-skills-sync}" \
              -c user.email="${GIT_AUTHOR_EMAIL:-noreply@anthropic.com}" \
              commit -qm "chore(skills): share $n skill file(s) from $(uname -s)"
git -C "$WT" push -q origin "HEAD:refs/heads/$BR"

TITLE="chore(skills): share $n skill file(s) from $(uname -s)"
BODY="Skill text only, add/update, no deletions. Generated by infra/skills-sync.sh."
PR="$(gh pr create --base main --head "$BR" --title "$TITLE" --body "$BODY" \
        --json number --jq .number 2>/dev/null \
     || gh pr create --base main --head "$BR" --title "$TITLE" --body "$BODY" \
        | grep -oE '[0-9]+$')"
# An empty PR number means both create attempts failed (auth, network). Without
# this guard the poll below waits 25 minutes on `gh pr view ""` before saying so.
[ -n "$PR" ] || { echo "skills-sync: FAILED — could not open a PR for $BR. Nothing was shared." >&2; exit 1; }
echo "skills-sync: PR #$PR on $BR — waiting until GitHub says it can merge…"

# Ask GitHub the one question that matters. Do NOT read `gh pr checks`: it prints
# nothing for a moment after a PR opens, and "no output" reads as "we are done".
state=""
for _ in $(seq 1 100); do          # up to ~25 min
    state="$(gh pr view "$PR" --json mergeStateStatus --jq .mergeStateStatus 2>/dev/null || echo '')"
    case "$state" in
        CLEAN|UNSTABLE) break ;;
        DIRTY) echo "skills-sync: FAILED — PR #$PR has a conflict. Nothing was shared." >&2; exit 1 ;;
    esac
    sleep 15
done
if [ "$state" != "CLEAN" ] && [ "$state" != "UNSTABLE" ]; then
    echo "skills-sync: FAILED — PR #$PR is still '$state' after 25 minutes. Nothing was shared." >&2
    gh pr checks "$PR" 2>/dev/null | head -6 >&2
    exit 1
fi

if ! MERGE_ERR="$(gh pr merge "$PR" --squash --delete-branch 2>&1)"; then
    echo "skills-sync: FAILED — could not merge PR #$PR:" >&2
    printf '%s\n' "$MERGE_ERR" | head -4 >&2
    exit 1
fi

# ---- the point of the whole script: read it back off the server ----
# Verify against the EXACT commit this push produced, not against origin/main.
# The other machine can merge between the fetch above and this moment; verifying
# against a moving origin/main would both misreport this push and record a base
# this machine never reached, which the next push would then overwrite.
git fetch origin -q
MERGE_SHA=""
for _ in 1 2 3 4 5; do
    MERGE_SHA="$(gh pr view "$PR" --json mergeCommit --jq '.mergeCommit.oid // empty' 2>/dev/null || echo '')"
    [ -n "$MERGE_SHA" ] && break
    sleep 3
done
[ -n "$MERGE_SHA" ] || { echo "skills-sync: FAILED — merged PR #$PR but GitHub did not report its commit, so nothing is proven." >&2; exit 1; }
git fetch origin -q "$MERGE_SHA" 2>/dev/null || true
VER="$TMP/verify"; mkdir -p "$VER"
extract_sub "$MERGE_SHA" "$VER" || {
    echo "skills-sync: FAILED — could not read $SUB from commit $MERGE_SHA (PR #$PR). Either the commit did not reach this machine or it does not contain the skills tree; nothing is proven." >&2; exit 1; }
# Verify the files THIS push staged, not every local file. The other machine can
# merge between the fetch above and this check; a file it changed that we never
# intended to share would otherwise read as a false FAILED on work that IS shared.
bad=0; checked=0
while IFS= read -r f; do
    checked=$((checked+1))
    same_text "$SKILLS_DIR/$f" "$VER/$SUB/$f" 2>/dev/null || { echo "  NOT on origin/main: $f" >&2; bad=$((bad+1)); }
done < "$STAGE"

if [ "$checked" -eq 0 ]; then
    echo "skills-sync: FAILED — verification scanned nothing, so nothing is proven. Do not report this as shared." >&2
    exit 1
fi
if [ "$bad" -eq 0 ]; then
    record_base "$MERGE_SHA"
    echo "skills-sync: VERIFIED on origin/main (PR #$PR merged as $MERGE_SHA) — $n file(s) shared, $checked checked."
    echo "skills-sync: the other machine gets them with  infra/skills-sync.sh pull"
    exit 0
fi
echo "skills-sync: FAILED — $bad of $checked file(s) did not reach origin/main." >&2
echo "skills-sync: the other machine CANNOT see them. Do not report this as shared." >&2
exit 1
