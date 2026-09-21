#!/usr/bin/env bash
# Does this change touch anything the Django test suite protects?
#
# Writes `code=true` or `code=false` to $GITHUB_OUTPUT. The CI workflow skips the
# four test shards only when this says false.
#
# WHY THIS EXISTS. `paths-ignore` in ci.yml has skipped MACHINE-TALK-only PUSHES
# since Jul-2026. It stopped working on 2026-09-09, silently: main became a
# protected branch, so every chat line now arrives as a PULL REQUEST, and
# `paths-ignore` does not apply to the `pull_request` event. Measured 2026-09-11:
# five runs in flight at once, ~25 jobs competing for runners, a real change
# waiting 20+ minutes for an 8-minute suite — and half the queue was one-line log
# entries running 2,700 insurance tests four times each.
#
# IT FAILS SAFE, ALWAYS. No base SHA, a git error, an empty diff, an unreadable
# list — every one of those reports `code=true` and the whole suite runs. The
# gate is never skipped because something could not be worked out. The only way
# to get `code=false` is a diff that was read successfully and in which EVERY
# changed file matched the list below.
#
# Run the self-test with:  bash .github/ci-touches-code.sh --self-test
set -uo pipefail

# The ONLY paths outside the test suite. Keep this list tiny and provable:
#   MACHINE-TALK.md / MACHINE-SYNC.md — the Mac<->Windows chat logs.
#   .claude/**                        — the tracked specs/steering docs (see .gitignore).
# Anything else, including every other markdown file, runs the full gate.
is_no_code_path() {
    case "$1" in
        MACHINE-TALK.md|MACHINE-SYNC.md) return 0 ;;
        # Only the specs/steering docs are tracked here (.gitignore). NOTE:
        # salvage/management/commands/import_motor_liquidators.py reads a data
        # file under .claude/specs/ — no test covers it, so skipping loses
        # nothing today. If a test is ever written that READS .claude/, move
        # the file out of here instead of widening this list.
        .claude/*)                       return 0 ;;
        *)                               return 1 ;;
    esac
}

# Given a newline-separated file list on stdin, echo "true" or "false".
# Empty list => "true" (run everything): an empty diff means we learnt nothing.
classify() {
    local any=0 f
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        any=1
        if ! is_no_code_path "$f"; then
            echo "true"; return
        fi
    done
    if [ "$any" = "0" ]; then echo "true"; else echo "false"; fi
}

# ── The required-check verdict ─────────────────────────────────────────────
# Given the shard job's result and the detector's answer, should the `test`
# check pass? This is the DANGEROUS half: a required check that goes green
# because a job vanished is worse than no check at all. Kept here, beside its
# tests, rather than as untestable YAML.
#   verdict <shard-result> <code>   ->  exit 0 pass, exit 1 fail
verdict() {
    local shard="$1" code="${2:-}"
    if [ "$shard" = "skipped" ]; then
        # A pass ONLY when the detector deliberately skipped the suite. Any
        # other skip (a failed detector, a cancelled run) is NOT a pass.
        if [ "$code" = "false" ]; then
            echo "no code changed (log / skills only) — suite deliberately skipped"
            return 0
        fi
        echo "shards were skipped but this change DOES touch code — refusing"
        return 1
    fi
    if [ "$shard" != "success" ]; then
        echo "at least one shard did not pass — see the shard (N) jobs above"
        return 1
    fi
    echo "all four shards green"
    return 0
}

self_test() {
    local fails=0
    check() {  # check <expected> <label> <files...>
        local want="$1" label="$2"; shift 2
        local got
        got="$(printf '%s\n' "$@" | classify)"
        if [ "$got" = "$want" ]; then
            echo "  ok   — $label ($want)"
        else
            echo "  FAIL — $label: wanted $want, got $got"; fails=1
        fi
    }
    echo "ci-touches-code self-test"
    check false "the chat log alone"            "MACHINE-TALK.md"
    check false "both chat logs"                "MACHINE-TALK.md" "MACHINE-SYNC.md"
    check false "a skills file"                 ".claude/skills/fabe/SKILL.md"
    check false "chat log plus skills"          "MACHINE-TALK.md" ".claude/settings.json"
    check true  "a chat log AND real code"      "MACHINE-TALK.md" "customer_refunds/services.py"
    check true  "python only"                   "taskboard/models.py"
    check true  "a migration"                   "taskboard/migrations/0030_x.py"
    check true  "the frontend"                  "frontend/src/app/page.tsx"
    check true  "another markdown file"         "README.md"
    check true  "this very workflow"            ".github/workflows/ci.yml"
    check true  "this very script"              ".github/ci-touches-code.sh"
    check true  "an empty diff (fails safe)"    ""
    # A file that merely starts with the same letters must NOT slip through.
    check true  "MACHINE-TALK.md.bak"           "MACHINE-TALK.md.bak"
    check true  "a nested claude-ish dir"       "backend/.claude/x.py"
    check true  "a path CONTAINING .claude/"    "vendor/.claude/thing.py"

    echo "required-check verdict"
    v() {  # v <expect pass|fail> <label> <shard-result> <code>
        local want="$1" label="$2" shard="$3" code="${4:-}"
        if verdict "$shard" "$code" >/dev/null; then local got=pass; else local got=fail; fi
        if [ "$got" = "$want" ]; then
            echo "  ok   — $label ($want)"
        else
            echo "  FAIL — $label: wanted $want, got $got"; fails=1
        fi
    }
    v pass "shards green on a code change"        success  true
    v pass "shards green on a log change"         success  false
    v pass "deliberately skipped for a log PR"    skipped  false
    v fail "skipped but the change TOUCHES code"  skipped  true
    v fail "skipped and the detector said nothing" skipped ""
    v fail "a shard went red"                     failure  true
    v fail "the run was cancelled"                cancelled true
    v fail "cancelled on a log change too"        cancelled false
    echo "real git output (the only kind that catches rename detection)"
    local tmp; tmp="$(mktemp -d)"
    (
        cd "$tmp" || exit 1
        git init -q . && git config user.email t@t && git config user.name t
        mkdir -p core/tests .claude/specs
        echo "def test_clock(): assert True" > core/tests/test_botswana_clock.py
        echo "log" > MACHINE-TALK.md
        git add -A && git commit -qm base
        BASE_REAL="$(git rev-parse HEAD)"
        # Move a REAL test file INTO the ignored folder. With git's default
        # rename detection this lists ONLY the new path, and the gate would be
        # skipped for a change that deletes a test.
        git mv core/tests/test_botswana_clock.py .claude/specs/test_botswana_clock.py
        git commit -qm rename
        HEAD_REAL="$(git rev-parse HEAD)"
        echo "$BASE_REAL $HEAD_REAL"
    ) > "$tmp/.shas" 2>/dev/null
    local shas; shas="$(tail -1 "$tmp/.shas")"
    if [ -n "$shas" ]; then
        local got
        got="$(cd "$tmp" && git diff --name-only --no-renames $shas | classify)"
        if [ "$got" = "true" ]; then
            echo "  ok   — a real test file RENAMED into .claude/ (true)"
        else
            echo "  FAIL — a real test file renamed into .claude/ was treated as no-code"; fails=1
        fi
    else
        echo "  FAIL — could not build the temp git repo for the rename case"; fails=1
    fi
    rm -rf "$tmp"

    echo
    if [ "$fails" = "0" ]; then echo "ci-touches-code: PASS"; else echo "ci-touches-code: FAIL"; fi
    return "$fails"
}

if [ "${1:-}" = "--self-test" ]; then
    self_test
    exit $?
fi

# Called by the `test` job, which is the required status check.
if [ "${1:-}" = "--verdict" ]; then
    echo "shards: ${2:-} | touches code: ${3:-}"
    verdict "${2:-}" "${3:-}"
    exit $?
fi

emit() {  # emit <true|false> <reason>
    echo "touches code: $1 — $2"
    if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "code=$1" >> "$GITHUB_OUTPUT"; fi
}

BASE="${BASE_SHA:-}"
HEAD="${HEAD_SHA:-}"

# Not a pull request (a push to main, a manual run): never try to be clever.
if [ -z "$BASE" ] || [ -z "$HEAD" ]; then
    emit true "no pull-request base/head SHA — running the whole suite"
    exit 0
fi

FILES="$(git diff --name-only --no-renames "$BASE" "$HEAD" 2>/dev/null)"
if [ $? -ne 0 ]; then
    emit true "could not read the diff — running the whole suite"
    exit 0
fi

echo "changed files:"
printf '%s\n' "$FILES" | sed 's/^/  /'

RESULT="$(printf '%s\n' "$FILES" | classify)"
if [ "$RESULT" = "true" ]; then
    emit true "at least one changed file is covered by the suite"
else
    emit false "only the cross-machine logs / skills changed"
fi
