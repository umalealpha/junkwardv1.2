#!/usr/bin/env bash
# machine-talk.sh — append one line to the shared cross-machine log, and PROVE it landed.
#
# WHY THIS EXISTS
# ---------------
# MACHINE-TALK.md lives in this repo, and `main` is a PROTECTED branch requiring
# the `test` check. So a plain `git push origin HEAD:main` is ALWAYS REJECTED:
#
#     remote: error: GH006: Protected branch update failed for refs/heads/main.
#     remote: - Required status check "test" is expected.
#
# Repo auto-merge is also disabled, so there is no fire-and-forget option. Every
# append must go: branch -> PR -> wait for `test` -> merge.
#
# That friction is exactly why entries kept going missing. A machine would append
# to its LOCAL working copy, run `git push`, not check the exit code, and report
# "MACHINE-TALK is updated" — while the other machine saw nothing, for days. On
# 2026-09-10 that happened twice in one morning: a /PratThis skill and a CFO
# update both reported as shared and neither ever left the PC.
#
# This script removes the judgement. It does the whole dance and then READS THE
# LINE BACK FROM origin/main. It exits 0 only if the line is actually there.
# Silence is no longer mistakable for success.
#
# USAGE
#   infra/machine-talk.sh "2026-09-10 Windows (win32) — what I did and what is left"
#   echo "…" | infra/machine-talk.sh -
#
# RULES IT ENFORCES FOR YOU
#   * Never commits in the shared checkout — always a throwaway worktree
#     (a commit there lands on a parallel session's branch; that has bitten twice).
#   * Never rebases, never force-pushes.
#   * merge=union on MACHINE-TALK.md means both machines can append freely.
set -euo pipefail

LINE="${1:-}"
[ "$LINE" = "-" ] && LINE="$(cat)"
if [ -z "${LINE//[[:space:]]/}" ]; then
    echo "machine-talk: refusing to log an empty line." >&2; exit 2
fi
case "$LINE" in -*) echo "machine-talk: pass the line as one quoted argument." >&2; exit 2;; esac

REPO="${MACHINE_TALK_REPO:-$(git rev-parse --show-toplevel)}"
cd "$REPO"
command -v gh >/dev/null || { echo "machine-talk: the gh CLI is required." >&2; exit 2; }

git fetch origin -q

# Already there? A re-run must be a no-op, not a duplicate.
if git show origin/main:MACHINE-TALK.md | grep -Fqx -- "$LINE"; then
    echo "machine-talk: already on origin/main — nothing to do."; exit 0
fi

BR="chore/machine-talk-$(date +%Y%m%d-%H%M%S)-$$"
WT="$(mktemp -d -t mtlog.XXXXXX)"
cleanup() { cd "$REPO" 2>/dev/null || true; git worktree remove --force "$WT" >/dev/null 2>&1 || true; }
trap cleanup EXIT

git worktree add -q --detach "$WT" origin/main
printf '%s\n' "$LINE" >> "$WT/MACHINE-TALK.md"
git -C "$WT" add MACHINE-TALK.md
git -C "$WT" -c user.name="${GIT_AUTHOR_NAME:-machine-talk}" \
              -c user.email="${GIT_AUTHOR_EMAIL:-noreply@anthropic.com}" \
              commit -qm "chore(machine-talk): $(printf '%.72s' "$LINE")"
git -C "$WT" push -q origin "HEAD:refs/heads/$BR"

PR="$(gh pr create --base main --head "$BR" \
        --title "chore(machine-talk): cross-machine log entry" \
        --body "One append-only line to the shared machine log. No code change." \
        --json number --jq .number 2>/dev/null \
     || gh pr create --base main --head "$BR" \
        --title "chore(machine-talk): cross-machine log entry" \
        --body "One append-only line to the shared machine log. No code change." \
        | grep -oE '[0-9]+$')"
# An empty PR number means both create attempts failed (auth, network). Without
# this guard the poll below waits 25 minutes on `gh pr view ""` before saying so.
[ -n "$PR" ] || { echo "machine-talk: FAILED — could not open a PR for $BR. The line is NOT logged." >&2; exit 1; }
echo "machine-talk: PR #$PR on $BR — waiting until GitHub says it can merge…"

# Ask GitHub the ONE question that matters: can this merge now?
#
# Do NOT infer it from `gh pr checks`. Two attempts at that both failed, for two
# different reasons (2026-09-10):
#   * right after a PR opens, `gh pr checks` prints NOTHING, and "no output" read
#     as "nothing pending, we are done";
#   * the listing is eventually-consistent, so even after checks appear a later
#     call can come back empty for an instant — the same false "done" via a race.
#   * and the required check here is `test`, which registers AFTER the four
#     shards, so "no shard is pending" is not the same as "the gate has passed".
#
# `mergeStateStatus` is GitHub's own verdict against the actual branch policy:
#   BLOCKED  — requirements not met yet (keep waiting)
#   CLEAN    — ready
#   UNSTABLE — mergeable; only non-required checks are unhappy
#   DIRTY    — conflict; a human is needed
# Polling that removes every guess about check names, counts and wording.
state=""
for _ in $(seq 1 100); do          # up to ~25 min
    state="$(gh pr view "$PR" --json mergeStateStatus --jq .mergeStateStatus 2>/dev/null || echo '')"
    case "$state" in
        CLEAN|UNSTABLE) break ;;
        DIRTY)
            echo "machine-talk: FAILED — PR #$PR has a conflict. The line is NOT logged." >&2
            exit 1 ;;
    esac
    sleep 15
done
if [ "$state" != "CLEAN" ] && [ "$state" != "UNSTABLE" ]; then
    echo "machine-talk: FAILED — PR #$PR is still '$state' after 25 minutes." >&2
    echo "machine-talk: the line is NOT logged. Look at the PR." >&2
    gh pr checks "$PR" 2>/dev/null | head -6 >&2
    exit 1
fi

# Do not swallow the merge error: if it refuses, say why.
if ! MERGE_ERR="$(gh pr merge "$PR" --squash 2>&1)"; then
    echo "machine-talk: FAILED — could not merge PR #$PR:" >&2
    printf '%s\n' "$MERGE_ERR" | head -4 >&2
    exit 1
fi

# ---- the point of the whole script: prove it, do not assume it ----
git fetch origin -q
if git show origin/main:MACHINE-TALK.md | grep -Fqx -- "$LINE"; then
    echo "machine-talk: VERIFIED on origin/main (PR #$PR merged)."
    exit 0
fi
echo "machine-talk: FAILED — PR #$PR did not reach origin/main. The other machine CANNOT see this line. Do not report it as shared." >&2
exit 1
