#!/usr/bin/env python3
"""CFO pipeline gate — the three steps that keep getting skipped, made unskippable.

Modes (chosen by argv[1]):
  pre   PreToolUse       — Bash + Edit/Write/MultiEdit/NotebookEdit.
                           Refuses a write in the SHARED checkout (Gates B/C),
                           and refuses to push/PR/merge/deploy unless
                           (a) the Build Log has a row for this work, and
                           (b) /fabe's run log shows a fresh SHIP verdict.
  stop  Stop             — refuse to end the session if code was pushed
                           but never deployed.

Evidence is never a promise in chat. It is:
  Build Log : this session actually ran ~/.claude/scripts/devlog.py
  Fable     : a verdict this gate WATCHED ledger.py write for this chat,
              else a verdict in runs.jsonl carrying this chat's id or commit
  Deploy    : this session actually ran a deploy command

Exit 2 blocks the action; stderr is shown to Claude.
Every failure path inside this hook exits 0 — a broken gate must never
wedge Prathap's work.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
GATE = os.path.join(HOME, ".claude", "gate")

# Where the deliberate SWITCHES live: OVERRIDE, OVERRIDE-<chat>, WIPE-OK,
# SHARED-OK. Overridable for the same reason GATE_RUNS / GATE_STATE / GATE_SAVES
# below are, and it was the one hole left in that rule: the suite pointed the
# ledger, the state and the scoreboard at throwaway files but created and deleted
# the LIVE switch files, which every other session on the machine reads.
#
# Two consequences, both real. A test run dropped a live OVERRIDE into the gate
# folder -- a 30-minute skeleton key past Gates D and E for every open chat --
# and a live WIPE-OK past Gate A. The comment on wipe_allowed() below records a
# WIPE-OK "sitting warm at 22:23 on 11-Sep-2026, set by another session", in very
# likely the window a session reported destroying another session's work; this
# suite is a prime suspect for having set it. And with 15+ chats open on this PC,
# two overlapping runs made checks fail at RANDOM: one run's live OVERRIDE waved
# through the push the other run was asserting must BLOCK. Five different checks
# went red across 8 runs on 17-Sep-2026, never the same one twice, and every one
# of them failed OPEN -- the direction that hides a real break, and that teaches
# a session to re-run until green. A test must not write to the thing it tests.
SWITCHES = os.environ.get("GATE_SWITCHES") or GATE
OVERRIDE = os.path.join(SWITCHES, "OVERRIDE")

# ── THE GATES HAVE NAMES (CFO, 14-Sep-2026) ──────────────────────────────────
# "cant we name the gates Gate A, Gate B, Gate C, etc so we can understand which
# gate we are working on". They were anonymous: every refusal just said BLOCKED,
# so there was no way to say "C fired again" or "turn D off" without describing
# the whole mechanism each time. A letter is the cheapest possible fix and makes
# the refusals, the tests and his instructions all speak the same language.
# Letters are PERMANENT. Retiring a gate leaves its letter unused rather than
# shuffling the rest up, because a letter that changes meaning is worse than no
# letter at all.
#   python gate.py --list   prints this table.
GATES = [
    ("A", "Wipe guard",        "stops one chat destroying another chat's uncommitted work",
     "WIPE-OK"),
    ("B", "Shared folder is read-only (shell)",
     "stops a chat BUILDING in the folder every chat shares", "SHARED-OK"),
    ("C", "Shared folder is read-only (Edit/Write)",
     "same, for file edits that never touch a shell", "SHARED-OK"),
    ("D", "Build Log row",     "no push or deploy until his request is recorded in his words",
     "OVERRIDE-<chat>"),
    ("E", "Fable review",      "no push or deploy until Fable 5.1 says SHIP for THIS chat/commit",
     "OVERRIDE-<chat>"),
    ("F", "Pushed but not deployed",
     "the session cannot end leaving merged work that never went live", "OVERRIDE-<chat>"),
    ("G", "Fan-out order",     "a multi-part job must use parallel agents (advises, never blocks)",
     "n/a"),
]
# Overridable so the test suite can point at throwaway files. It used to edit the
# LIVE ledger and the LIVE session state: it appended fake SHIP verdicts to the
# run log the gate actually reads (16 had accumulated, the newest was the last
# line, so for three hours after any test run the gate saw an approval no review
# ever gave), and it restored a start-of-run snapshot of the state, erasing
# whatever a parallel session had stamped meanwhile. A test must not write to the
# thing it is testing.
RUNS = os.environ.get("GATE_RUNS") or os.path.join(
    HOME, ".claude", "skills", "fabe", "runs.jsonl")
STATE = os.environ.get("GATE_STATE") or os.path.join(GATE, "state.json")

MAX_AGE_H = 3           # a Fable verdict older than this is stale
OVERRIDE_AGE_MIN = 30   # a CFO override lasts 30 minutes
STATE_AGE_H = 12        # forget sessions older than this

DEPLOY_SCRIPTS = {"deploy-zero-downtime.sh", "deploy_ssm.py"}

# A deploy does not have to be one of OUR scripts. PrintOps (Prathap-Alpha/
# printops-erp) is a different repo on a different box (i-05297188826961e72,
# code at /opt/printops) and ships by plain `docker compose build` / `up -d`
# sent over SSM, so no deploy-script name ever appears on the line. On
# 16-Sep-2026 this gate told the CFO "pushed but never deployed" TWICE
# immediately after a real, verified PrintOps deploy, and the session had to be
# overridden by hand (OVERRIDE-fe56e579 records it). Same failure as the
# UniCoin one above: a guard that is wrong right after the work stops being
# read. Matched only INSIDE an SSM payload (see deploys_over_ssm), never on a
# bare line — building a container locally is not a deploy.
DOCKER_DEPLOY = re.compile(
    r"docker[\s-]+compose\b[^\n;|&]*?(?:\bbuild\b|\bup\b[^\n;|&]*?\s-d\b)")
DEVLOG = re.compile(r"devlog(\.py|\.sh)", re.I)

# Nor does a deploy have to touch the backend image at all. Anything under
# infra/ceo-monitor/ -- the CEO and CFO morning briefs -- does NOT ship inside
# it: run_cfo.sh and run.sh pipe the HOST copies at /opt/ceo-monitor/ into
# `manage.py shell`. The documented deploy is `install-crons.sh`, which syncs
# those host copies from the repo; running deploy-zero-downtime.sh instead would
# recreate containers on a live insurance system for no benefit, because nothing
# in the image changed.
#
# On 16-Sep-2026 (PR #1144) exactly that deploy was done -- md5 host-vs-repo
# matched on all three files, a dry run of the brief on prod showed all three
# diary columns populated, and record-release.sh stamped the Build Log Live --
# and GATE F still told the session "pushed but never deployed". Third false
# alarm in five days after UniCoin and PrintOps, and the cause is the same every
# time: this gate knew a closed list of deploy NAMES instead of the behaviour.
#
# `record-release.sh` is here because it is the strongest signal available. It
# is the only writer of `live_at` in the Build Log, so a session that caused it
# to run has demonstrably deployed -- by whatever route, including one nobody
# has taught this file about yet.
#
# `--check` is excluded deliberately: it reports drift and changes nothing, so a
# session that only probed for drift has not deployed. Both spellings rode the
# same real payload, seconds apart. And it must be an INVOCATION -- `cat`,
# `grep` or `git add` of the same path stays a read, the rule the rest of this
# file lives by. Matched only INSIDE an SSM payload, like DOCKER_DEPLOY above:
# install-crons.sh run on a dev machine installs crons on that dev machine.
# `_AT` is not decoration. Without it `sh\s+` matches the TAIL of any word
# ending in those two letters, so a payload reading "...to finish
# install-crons.sh must run" would have scored as a deploy. DeepSeek caught
# it on review, 16-Sep-2026.
#
# The `"` in the lookahead's stop-set is the whole fix, and Fable 5.1 found
# it: the payload is a JSON ARRAY, so `","` ends one command and starts the
# next. Stopping only at the SHELL's separators let the `--check` in the
# following element cancel the bare run in this one -- and running the sync
# then checking for drift is the exact shape infra/ceo-monitor/README.md
# tells people to type. The real 16-Sep payload scored right only because a
# `2>&1 |` happened to sit between the two.
_AT = r"""(?:^|[\s;|&'"(\[])"""
_RUN = r"(?:sudo\s+)?(?:bash\s+|sh\s+|\./)"
_PATH = r"(?:\S*/)?"
HOST_DEPLOY = re.compile(
    _AT + _RUN + _PATH + r"""install-crons\.sh(?![^\n;|&"]*?--check)"""
    r"|" + _AT + _RUN + _PATH + r"record-release\.sh")

# The one checkout every session lands in by default, and therefore the only one
# where a wipe hits somebody else. A session working in its own worktree is never
# touched by this guard. The env var exists so the test can point it at a
# throwaway repo it dirties on purpose — a guard whose test depends on the live
# repo happening to be dirty is a guard that silently stops being tested.
SHARED_REPO = os.environ.get("GATE_SHARED_REPO") or os.path.join(HOME, "work", "alpha-finance")

# ── the shared checkout is READ-ONLY (Fable 5.1 review, 14-Sep-2026) ──────────
# The wipe guard below stops the WHOLESALE destroyers. It does not stop a session
# quietly BUILDING in the shared folder, which is the upstream cause: every
# session lands there by default, so other people's work accumulates there, and
# only then is there something for a reset to destroy. Measured the day this was
# written: 72 worktrees (36 three days earlier), 12 stashes, three of them named
# "stale working tree changes from other sessions".
#
# So the folder gets exactly one job — READ origin/main — and every session is
# pushed into its own worktree BY CONSTRUCTION rather than by a rule nobody
# reads. "never reset the shared checkout" was written in three separate places
# and the wipe still happened on 11-Sep-2026.
#
# Deliberately allowed, because refusing them would break the escape route
# itself: worktree (how you leave), fetch/show/log/status/diff/rev-parse (the
# folder's one job), and `stash push` (exactly what the wipe guard's own refusal
# tells a session to run). `stash drop|clear` stays destructive, handled above.
MUTATING_GIT = {
    "commit", "merge", "rebase", "pull", "cherry-pick", "revert", "am",
    "apply", "mv", "rm", "add", "reset", "checkout", "switch", "restore",
    "clean", "stash",
}
GIT_SAFE_PAIRS = {("stash", "push"), ("stash", "list"), ("stash", "show"),
                  ("stash", "save")}
# Tools that write a file directly, without going through a shell.
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _prog(word):
    """The comparable NAME of a command word: basename, lowercased, no `.exe`.

    Every place that asks "what program is this?" goes through here, so the
    answer cannot differ between them. `.exe` is stripped because this gate runs
    on Windows, where `python.exe` and `bash.exe` are the ordinary spellings: on
    17-Sep-2026 Fable found that `python.exe fabe/scripts/deploy_ssm.py` still
    hid a real deploy, since `python.exe` is not in WRAPPERS and so the peel
    stopped there and never reached the script. Same for `bash.exe <<EOF`. A
    deploy this gate cannot see is a deploy the Build Log reports as never
    happening, which is what the CFO reads at the end of the day.

    Only the trailing `.exe` goes, and nothing in DEPLOY_SCRIPTS, WRAPPERS,
    _INTERPRETERS or the git tables ends in it, so no name this file compares
    against can change meaning.

    Backslashes are folded to `/` before the split, and that is not Windows
    fussiness: `os.path` is `ntpath` here and `posixpath` on the Mac, so
    `basename(r"C:\\Python313\\python.exe")` returns just the program on one
    machine and the WHOLE string on the other. A gate that reads a command
    differently on the two machines is the same class as the hardcoded path at
    the top of test_gate.py, which made every check fail on the Mac before it
    started. DeepSeek flagged it on review, 17-Sep-2026.

    The four callers are _invocations (the `bash -n` test, the wrapper peel, and
    the emitted program) and _feeds_an_interpreter. Nothing else asks the
    question; the one remaining os.path.basename in this file builds a human
    message, not a name to compare.
    """
    name = os.path.basename(word.replace("\\", "/")).lower()
    return name[:-4] if name.endswith(".exe") else name

# Things that stand in FRONT of the real command rather than being it.
WRAPPERS = {"sudo", "env", "nohup", "time", "exec", "bash", "sh", "zsh",
            "python", "python3", "py", "winpty",
            # Added 17-Sep-2026 with the heredoc fix: each of these stands in
            # front of a real command and hid a real deploy behind it. They
            # only make the gate see MORE invocations, so a mistake here
            # over-blocks; `xargs grep x` still reads as `grep`.
            #
            # KNOWN GAPS, written down rather than implied closed (Fable 5.1,
            # 17-Sep-2026): an option with a SEPARATE non-numeric value is not
            # seen through -- `timeout -s KILL <deploy>`, `stdbuf -o L <deploy>`
            # and `sudo -u ubuntu <deploy>` still report the VALUE as the
            # program. A per-tool flag table was rejected as speculative (no
            # such spelling exists anywhere in this repo, the skills or the gate
            # ledger) and because a mis-tabled flag would eat the deploy itself.
            # `docker exec ... <cmd>` is the same remote-execution class as
            # `ssh` and is equally not covered. The numeric skip below handles a
            # bare or suffixed duration (`30`, `10m`, `1.5h`), which is the
            # spelling sessions on this box actually write.
            "timeout", "nice", "ionice", "stdbuf", "xargs", "wsl"}


def commands_run(cmd):
    """Every command a shell line would actually RUN, as (program, args) pairs.

    See _invocations() for the reasoning and the burns; this is the same list
    without the raw word, which is what every caller but deploys_over_ssm wants.

    Heredoc BODIES are stripped first, because text a line WRITES is not a
    command that line RUNS. deploys_over_ssm() learned this one level up; this
    function was still reading the raw line, so on 16-Sep-2026 a real
    `git commit -F - <<'MSGEOF' ... MSGEOF` was refused as a deploy: the commit
    message wrapped so that a body line began with `deploy-zero-downtime.sh`,
    that word became the program, it is in DEPLOY_SCRIPTS, and GATE E blocked
    the commit. The message had to be reworded to get the work in. The same
    mention-is-not-invocation rule the rest of this file lives by, and it now
    covers all four callers -- deploy classification, both shared-checkout
    guards, and the Fable verdict reader -- since a commit message or a
    fixture can name any of those things too.

    _outside_heredocs() keeps every line OUTSIDE the body, so a script written
    by heredoc and then run on a later line is still seen. Pinned by the test
    "heredoc writes a script, a LATER line deploys".
    """
    return [(prog, args) for prog, args, _word in _invocations(_outside_heredocs(cmd))]


def _invocations(cmd):
    """Every command a shell line would actually RUN, as (program, args, word).

    `word` is the command exactly as written, path and all — commands_run()
    reduces it to a basename, which is enough to ask "is this a deploy script?"
    but not enough to OPEN the thing (see deploys_over_ssm).

    The gate used to ask "does this text contain a gated phrase", which is wrong:
    `grep deploy_ssm.py`, `cat deploy-zero-downtime.sh` and `git checkout -- that
    file` are all reading, not deploying, and every one of them blocked real work
    on 11-Sep-2026. This asks the right question instead — is the gated thing
    being INVOKED, or merely mentioned?
    """
    # Drop the quote MARKS, keep the text. Deleting quoted content (the first
    # version) threw away the command itself whenever its path was quoted —
    # `python "$HOME/.claude/.../deploy_ssm.py" --confirm` became `python
    # --confirm`, so two real deploys went unrecorded and the stop hook told the
    # CFO "never deployed" right after watching them. Keeping the text is safe
    # because only the FIRST word of a segment is treated as the command, so
    # `grep "deploy_ssm.py"` and `echo "git push"` still classify as reading.
    cmd = cmd.replace('"', ' ').replace("'", ' ')
    out = []
    for seg in re.split(r"&&|\|\||[;|\n]", cmd):
        words = seg.split()
        if not words:
            continue
        # `bash -n script.sh` parses a script; it does not run it.
        if _prog(words[0]) in {"bash", "sh", "zsh"} and "-n" in words[1:2]:
            continue
        i = 0
        while i < len(words):
            w = words[i]
            if w.startswith("-") or ("=" in w and not w.startswith("/")):
                i += 1                      # a flag, or a VAR=value prefix
            elif _prog(w) in WRAPPERS:
                i += 1                      # sudo / bash / python ... the real command follows
            elif re.fullmatch(r"\d+(\.\d+)?[smhd]?", w):
                # `timeout 30 bash deploy-zero-downtime.sh` peeled to `30` and
                # reported the program as "30", so the deploy was invisible --
                # the SAME prefix class that kept defeating the heredoc check
                # above, one level down, and Fable asked for both to close in
                # one change. A duration is never the command. Bounded on
                # purpose: only a bare number, so `git log -1` is untouched
                # (a flag is already peeled on the branch above).
                i += 1
            else:
                break
        if i < len(words):
            out.append((_prog(words[i]), words[i + 1:], words[i]))
    return out


def _read_script(path, limit=200000):
    """The text of a file this line points at, or "" when it is not readable.

    Reading a file is deliberate here: on this machine the thing that actually
    runs on the box lives in a FILE, not on the line (see deploys_over_ssm), so
    a substring match on the command can never see it. Anything unreadable
    returns "" — a missing file can only make this gate quieter, never crash it.
    """
    try:
        path = _winpath(path)
        if os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read(limit)
    except (OSError, ValueError):
        pass
    return ""


_ASSIGN = re.compile(r"""(?m)^[ \t]*([A-Za-z_]\w*)=(?:"([^"]*)"|'([^']*)'|(\S+))""")
_VAR = re.compile(r"\$\{(\w+)\}|\$(\w+)")


def _resolve(word, context):
    """`$SP/ssm.sh` -> a real path, using the assignments made earlier in the
    SAME block, then the environment.

    Every session on this PC writes its helper into the scratchpad and calls it
    through a variable, because that path is 120 characters long. A resolver
    that only understood literal paths would never open the one file that
    matters. An unresolved variable is left as written, so the read simply
    fails — the quiet direction.
    """
    vals = {m.group(1): (m.group(2) or m.group(3) or m.group(4) or "")
            for m in _ASSIGN.finditer(context)}

    def one(m):
        name = m.group(1) or m.group(2)
        return vals.get(name) or os.environ.get(name) or m.group(0)

    return _VAR.sub(one, word)


def _param_files(token, context):
    """`--parameters file:///tmp/ssm-params.json` — the payload is in that JSON,
    not on the line. Git Bash's /tmp is the Windows temp folder, and Windows
    Python reads `/tmp/x` as a folder that does not exist, so try both."""
    path = _resolve(token[len("file://"):], context)
    out = [path]
    if os.name == "nt" and path.startswith("/tmp/"):
        out.append(os.path.join(os.environ.get("TEMP", ""), path[5:]))
    return out


# `(?<!<)<<(?!<)` because `cat <<< "$x"` is a herestring, not a heredoc, and
# `1<<2` is a shift: either one would otherwise swallow every line after it,
# hiding a real send-command further down the same call. Fable 5.1 found it.
#
# The SHIFT half of that comment was FALSE until 17-Sep-2026: the lookarounds
# only ever excluded `<<<`, so `echo $((1<<2))` still matched `<<2`, took "2" as
# the terminator, and stripped every later line -- that line followed by a real
# deploy scored as no deploy at all. A heredoc terminator is a shell WORD and
# cannot begin with a digit, so requiring a letter or underscore is what this
# comment always claimed. Fable 5.1 found the gap reviewing #1174, re-reading
# its own earlier note: a comment standing in for a guard, which is exactly why
# every claim in this file has to be pinned by a test.

# `((...))` is arithmetic, and _outside_heredocs strips it from the line before
# searching. That is the real rule; the lookbehind below is only a cheap second
# line. See the note at the search itself for why the lookbehind alone was not
# enough -- it closed the unspaced shift and left the spaced one open.
_ARITH = re.compile(r"\(\(.*?\)\)")

# The lookbehind excludes `\w` as well as `<`, which rules out the commonest
# shift spellings on its own: requiring a letter/underscore terminator closed
# `1<<2`, and DeepSeek then pointed out that `$((x<<y))` still matched, because
# `y` is a perfectly good identifier. The character IN FRONT is what separates
# the two -- a heredoc operator follows a space or a redirect, never a word
# character, while a shift always follows its left operand.
#
# This is a SECOND line of defence, not the rule. `_ARITH` above is the rule,
# and a spaced `$(( x << y ))` needs it. Both fail in the SAFE direction: an
# unspaced `cat<<EOF` simply stops being recognised, so its body is read as
# commands and the gate over-blocks rather than swallowing a deploy.
_HEREDOC = re.compile(r"""(?<![\w<])<<(?!<)-?\s*['\"]?([A-Za-z_]\w*)['\"]?""")


def _outside_heredocs(cmd):
    """The part of the line the shell RUNS, with heredoc BODIES removed.

    Writing a script is not running it, and this gate proved the point on
    itself: the command that added the PrintOps test below was refused as a
    deploy, because the fixture text inside `cat > x.py <<'EOF' ... EOF`
    contained both `aws ssm send-command` and `docker compose up -d`. Same
    mention-is-not-invocation rule as everywhere else here, one level up.

    The body is still read as PAYLOAD in deploys_over_ssm — the real deploy
    line writes its script by heredoc and then runs it in the same breath. It
    is only the question "did this line SEND anything" that must ignore it.
    """
    out, ends, keep = [], None, False
    for line in cmd.splitlines():
        if ends is not None:
            if line.strip() == ends:
                ends, keep = None, False
            elif keep:
                out.append(line)
            continue
        out.append(line)
        # Arithmetic is removed BEFORE the search, because a heredoc operator
        # cannot live inside `((...))` while a shift always does. The lookbehind
        # alone was not the whole rule, whatever the note on _HEREDOC claimed:
        # it closed `$((x<<y))` and left the spaced `$(( x << y ))` wide open,
        # still taking `y` as a terminator and swallowing the deploy below it.
        # Fable 5.1 found the spaced sibling of the very case DeepSeek had just
        # found -- one spelling pinned, the whole class claimed.
        m = _HEREDOC.search(_ARITH.sub("", line))
        if m:
            ends = m.group(1)
            keep = _feeds_an_interpreter(line)
    return "\n".join(out)


def _feeds_an_interpreter(line):
    """Is this heredoc marker line piping its body INTO a shell, or writing it?

    `cat > x.sh <<'SH'` writes; `bash <<'SH'` RUNS every line of the body. The
    strip above could not tell them apart, so once commands_run() started using
    it (17-Sep-2026, the commit-message-is-not-a-command fix) `bash <<'EOF' ...
    deploy-zero-downtime.sh ... EOF` became invisible to classify(), and
    `bash <<'EOF' ... git reset --hard ... EOF` invisible to Gate A. Fable 5.1
    found it on review; none of the three external judges did.

    The test is the one _invocations() already implies: peel the wrappers off
    and see what is left holding the line. `bash`, `sh`, `python` are WRAPPERS,
    so for `bash <<'EOF'` the peel runs off the end of the real words and lands
    on the redirection itself — nothing is being given a file to work on, so the
    body IS the program. For `cat > x.sh <<'SH'`, `tee`, or the case this whole
    class came from, `git commit -F - <<'MSGEOF'`, the peel stops on a real
    command that takes the body as DATA, and the body stays stripped.

    `eval` and `ssh` are named explicitly because neither is in WRAPPERS: `ssh
    host <<EOF` runs the body on the far side. (`classify` never caught
    `ssh host bash deploy.sh` anyway -- prog is `ssh` -- so this closes the
    heredoc spelling, not the whole ssh hole.)
    """
    # STOP TRYING TO PEEL. Three spellings got past this in one day -- DeepSeek
    # found `bash -s -- arg <<EOF` (the peel stopped on the positional), OpenAI
    # predicted and Fable confirmed `env FOO=1 BAR=2 bash ... <<EOF` (it stopped
    # on the assignment), and Fable then listed six more: `timeout 30 bash`,
    # `xargs bash`, `nice bash`, `sudo -u ubuntu bash`, `docker exec -i c bash`,
    # `wsl bash`. Each patch closed one spelling and left the next one open,
    # because a peel has to know every prefix that will ever stand in front of a
    # shell -- the same "knows only our own tool name" mistake this gate keeps
    # making. So ask the BEHAVIOUR instead: is there an interpreter anywhere
    # before the heredoc, and was it handed a script file to run?
    #
    # An interpreter given a SCRIPT FILE is being fed that file, and the heredoc
    # is only stdin DATA for it (`bash run.sh <<EOF`, `python app.py <<EOF`). An
    # interpreter given no script file is running the body itself. Where this is
    # wrong it over-blocks, never under-blocks: the worst case is refusing a line
    # that merely names a shell before a heredoc, which a session can reword --
    # the opposite direction hides a deploy.
    words = line.replace('"', " ").replace("'", " ").split()
    before = []
    for w in words:
        if w.startswith("<<"):
            break
        before.append(w)
    names = [_prog(w) for w in before]
    if not any(n in _INTERPRETERS for n in names):
        return False
    # ...and the operands AFTER the interpreter decide it. Anything before it is
    # a prefix and cannot be the script (`docker exec -i c bash` -- `c` is the
    # container, not a script for bash).
    first = min(i for i, n in enumerate(names) if n in _INTERPRETERS)
    for w in before[first + 1:]:
        if w.startswith("-") or w == "--" or "=" in w:
            continue                    # a flag, the end-of-flags marker, or VAR=v
        if w.endswith((".sh", ".py", ".zsh", ".bash", ".pl", ".rb", ".js")) or "/" in w:
            return False                # it was handed a script; the body is data
    return True


# The interpreters this gate knows can READ a heredoc body as a PROGRAM, matched
# by name ANYWHERE before the redirection rather than by peeling a prefix list --
# see the note in _feeds_an_interpreter for the spellings a peel kept missing.
#
# It IS a closed list, and DeepSeek was right to call that out on review
# (17-Sep-2026): an interpreter nobody listed defaults to "the body is data", the
# unsafe direction. The alternative -- treat every unlisted command as an
# interpreter and strip only for a known list of data consumers -- inverts the
# failure to over-blocking, and that is the exact pain this whole thread began
# with: #1164 was a real commit REFUSED because its message looked like a deploy.
# There are far more data consumers (psql, jq, mail, curl -d @-, sqlite3, any
# --stdin tool) than shells, so inverting would refuse honest work every week to
# stop an evasion that needs deliberate intent -- and Fable already ruled that
# intent is not this gate's threat model; it guards a session's own mistakes.
# So: keep the positive list, and keep it HONEST. Anything that runs a script
# from stdin belongs here. Add to it when you meet one.
_INTERPRETERS = {"bash", "sh", "zsh", "dash", "ksh", "ash", "fish",
                 "python", "python3", "py", "perl", "ruby", "node",
                 "php", "lua", "deno", "bun", "tclsh", "osascript", "pwsh",
                 "eval", "ssh"}


def _is_send_command(prog, args):
    """Is this invocation `aws ssm send-command`?

    Two spellings, because the deploy helper on this machine uses the second:
    `CID=$(aws ssm send-command ...)` parses with `CID=$(aws` read as a
    VAR=value prefix, so the program comes out as `ssm`. A check for `aws`
    alone missed every deploy that captured the command id — which is all of
    them, since you have to wait for the invocation to finish.
    """
    return ((prog == "aws" and args[:2] == ["ssm", "send-command"])
            or (prog == "ssm" and args[:1] == ["send-command"]))


def deploys_over_ssm(cmd):
    """True when this line sends a deploy to a box through AWS SSM.

    On Windows there is no SSH: the deploy is `aws ssm send-command` with the
    real script carried INSIDE a quoted JSON payload. commands_run() strips
    quoted text on purpose — a grep for a script name is not a deploy — so it
    could never see the only deploy path this machine has. Every session on
    this PC therefore recorded deployed=null, and the stop hook told the CFO
    "never deployed" straight after a deploy it had watched go past.

    Kept narrow, and the narrowness is the point: the line must INVOKE the
    channel, and what it ships must actually deploy. `grep
    deploy-zero-downtime.sh` still classifies as reading, not deploying.

    Three shapes, all of them real on this machine:
      1. inline   — `aws ssm send-command --parameters "commands=[...]"`.
      2. by file  — `--parameters file://...json`, so the script is not on the
                    line at all; the JSON is read.
      3. by helper— `bash "$SP/ssm.sh" "$SP/deploy.sh"`, where ssm.sh is the
                    thing that calls send-command. This is the shape the
                    16-Sep-2026 PrintOps deploy used, and a check for
                    `aws ssm send-command` ON THE LINE cannot see it at all.
                    The helper is opened and must itself INVOKE send-command —
                    a file merely mentioning it proves nothing, same rule as
                    everywhere else in this gate.

    What counts as a deploy in the payload: one of our DEPLOY_SCRIPTS, a plain
    `docker compose build` / `up -d` (DOCKER_DEPLOY) for repos like PrintOps
    that have no deploy script of ours to name, or a host-script deploy
    (HOST_DEPLOY) for the morning briefs, which never ride the image at all.
    """
    sends = False
    payload = [cmd]
    for prog, args, word in _invocations(_outside_heredocs(cmd)):
        if _is_send_command(prog, args):
            sends = True
            for a in args:
                if a.startswith("file://"):
                    payload += [_read_script(f) for f in _param_files(a, cmd)]
        elif prog.endswith((".sh", ".bash")):
            text = _read_script(_resolve(word, cmd))
            if not any(_is_send_command(p, a) for p, a, _w in _invocations(text)):
                continue                  # not an SSM helper; nothing was sent
            sends = True
            payload.append(text)
            payload += [_read_script(_resolve(a, cmd)) for a in args]
    if not sends:
        return False
    shipped = "\n".join(payload)
    return (any(s in shipped for s in DEPLOY_SCRIPTS)
            or bool(DOCKER_DEPLOY.search(shipped))
            or bool(HOST_DEPLOY.search(shipped)))


_AUTO_DEPLOY_CACHE = {}


def repo_auto_deploys(dirpath):
    """True when merging to main in this repo IS the deploy.

    Added 12-Sep-2026 after this gate cried wolf five times in one night.

    It knew exactly two ways a deploy can happen: one of Omni's deploy scripts
    runs, or someone dispatches a deploy workflow by hand. UniCoin does neither
    -- both of its repositories ship on push to main -- so five deploys that
    this gate watched go past, and that were verified live, were recorded as
    "never deployed", and it told the CFO so after each one. A guard that is
    wrong five times in a row stops being read, which is worse than not having
    it.

    Asks the repository instead of carrying a list of known deploy paths -- the
    same reason the Build Log's own guard was changed to DISCOVER deploy paths
    by behaviour rather than name them: a repo added tomorrow works on the day
    it lands.

    Deliberately narrow. The workflow must trigger on a push to main AND
    actually ship something; a workflow merely NAMED deploy proves nothing, and
    a comment mentioning one proves less. Mentioned is not invoked.
    """
    if not dirpath:
        return False
    key = os.path.normcase(os.path.abspath(dirpath))
    if key in _AUTO_DEPLOY_CACHE:
        return _AUTO_DEPLOY_CACHE[key]

    found = False
    d = key
    for _ in range(6):                       # walk up to the repo root
        wf = os.path.join(d, ".github", "workflows")
        if os.path.isdir(wf):
            for name in os.listdir(wf):
                if not name.endswith((".yml", ".yaml")):
                    continue
                try:
                    with open(os.path.join(wf, name), encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                except OSError:
                    continue
                body = re.sub(r"(?m)#.*$", "", text)     # comments are not code
                on_main = re.search(r"branches:\s*\[?[^\n\]]*\bmain\b", body)
                ships = (
                    "ecs update-service" in body
                    or "update-service" in body and "aws ecs" in body
                    or "docker push" in body
                )
                if on_main and ships:
                    found = True
                    break
            break
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent

    _AUTO_DEPLOY_CACHE[key] = found
    return found


def classify(cmd, cwd=None):
    """(pushes code, deploys) for a shell line."""
    ship = deploy = False
    if deploys_over_ssm(cmd):
        deploy = True
    for prog, args in commands_run(cmd):
        if prog in DEPLOY_SCRIPTS:
            deploy = True
        elif prog == "git" and args[:1] == ["push"]:
            ship = True
        elif prog == "gh" and args[:2] in (["pr", "create"], ["pr", "merge"]):
            ship = True
            # In a repo that ships on push to main, the merge IS the deploy.
            if args[:2] == ["pr", "merge"] and repo_auto_deploys(target_dir(cmd, cwd)):
                deploy = True
        elif prog == "gh" and args[:2] == ["workflow", "run"] and any(
                a.startswith("deploy") for a in args[2:3]):
            deploy = True
    return ship, deploy


_MSYS = re.compile(r"^/([a-zA-Z])/")


def _winpath(p):
    """`/c/Users/x` is how Git Bash writes `C:/Users/x`. Windows Python reads it
    as the literal folder `C:\\c\\Users\\x`, so a path written the way every
    session on this machine writes it would compare as a DIFFERENT folder and
    walk straight past the guard.

    Windows only. On the Mac `/c/...` is an ordinary absolute path and rewriting
    it would point the guard at a folder that does not exist — which fails open,
    silently, exactly where this file is meant to be loud.
    """
    if os.name != "nt":
        return p or ""
    return _MSYS.sub(lambda m: m.group(1).upper() + ":/", p or "")


def _within(child, parent):
    """Is `child` the shared checkout, or anywhere INSIDE it?

    Equality alone was a hole: `cd backend && git reset --hard` wipes the whole
    repo just the same, and running from a subdirectory is the commonest shape
    there is. Proven allowed by the first version.
    """
    try:
        c = os.path.normcase(os.path.realpath(_winpath(child)))
        p = os.path.normcase(os.path.realpath(_winpath(parent)))
        return c == p or c.startswith(p + os.sep)
    except Exception:
        return False


def _is_whole_tree(p):
    """Does this pathspec mean "everything"? `.` is only the first spelling — a
    session refused on `git checkout .` reaches straight for `./` next."""
    q = (p or "").strip().replace("\\", "/").rstrip("/")
    return q in ("", ".", "*", "./*", ":", ":/.", ":/*")


def target_dir(cmd, cwd):
    """Where this line would actually run — `cd X` moves it.

    Every `cd` in the line counts, not just the first: `cd /c/ && cd <shared> &&
    git reset --hard` walked past the first version. Relative hops resolve against
    the payload's cwd, never the hook process's, which is somewhere else entirely.
    """
    cur = cwd
    for seg in re.split(r"&&|\|\||[;\n]", cmd.replace('"', ' ').replace("'", ' ')):
        words = seg.split()
        if words and words[0] == "cd" and len(words) > 1 and not words[1].startswith("-"):
            d = os.path.expanduser(_winpath(words[1]))
            cur = d if os.path.isabs(d) else os.path.join(_winpath(cur or ""), d)
    return cur


def _peel_git_globals(a):
    """Strip git's own options that sit BEFORE the subcommand; return (args, -C dir).

    `git -C <dir> reset --hard` was invisible to the first version of this guard:
    the args began ["-C", dir, "reset", ...] so nothing matched `reset`, and the
    refusal message itself demonstrated the `git -C <dir> ...` form — so a session
    that read the refusal once had been handed the way around it.
    """
    gdir = None
    i = 0
    while i < len(a):
        if a[i] == "-C" and i + 1 < len(a):
            gdir = a[i + 1]
            i += 2
        elif a[i] == "-c" and i + 1 < len(a):
            i += 2
        elif a[i].startswith("-"):          # --git-dir=, --work-tree=, --no-pager, ...
            i += 1
        else:
            break                           # the subcommand
    return a[i:], gdir


def wipes_shared_work(cmd, cwd):
    """Reason this line would destroy work sitting in the SHARED checkout, else None.

    WHY THIS EXISTS
    ---------------
    The rest of this gate asks "is this work good enough to ship". It runs at
    push/PR/deploy time. But the damage on 11-Sep-2026 — one session reporting
    "I just destroyed another session's staged work with that reset" — came from
    an ordinary `git reset`, which ships nothing, so the gate never looked at it.

    It was not a one-off. That repo carried 11 stashes, three of them named for
    what they were: "stale working tree changes from other sessions",
    "skills-sync WIP (another session)". Sessions had been quietly mopping this
    up for days, and the shared checkout was holding 20 unfinished files of
    someone's large-payments work at the moment this was written.

    DELIBERATELY NARROW. Only the WHOLESALE destroyers are listed: things that
    throw away everything in one go and cannot be undone from the working tree.
    Targeted work is left alone — `git checkout -- one/file.py` is how a session
    undoes its OWN edit, and an earlier version of this gate blocked exactly that
    and stopped real work. Branch switching is not here either: git already
    refuses when it would clobber, and otherwise carries the changes across.

    Reads nothing until the command is already known to be destructive, so the
    cost lands on the rare command, never on every Bash call.
    """
    dest = None
    gdir = None
    for prog, args in commands_run(cmd):
        if prog != "git":
            continue
        a, gdir = _peel_git_globals([x for x in args if x])
        sub = a[0] if a else ""
        rest = a[1:]
        flags = [x for x in rest if x.startswith("-")]
        # Anything that is not a flag and not the `--` separator: a ref or a path.
        paths = [x for x in rest if not x.startswith("-") and x != "--"]
        # "." or ":/" means the WHOLE tree, whether or not a ref is named first.
        # `git checkout HEAD -- .` and `git checkout .` both land here; a named
        # file does not, because undoing your own single file is normal work.
        whole = any(_is_whole_tree(p) for p in paths)

        if sub == "reset" and any(f in flags for f in ("--hard", "--merge", "--keep")):
            dest = "git reset --hard throws away every uncommitted change"
        elif sub == "clean" and any("f" in f.lstrip("-") for f in flags if not f.startswith("--")) \
                or (sub == "clean" and "--force" in flags):
            dest = "git clean -f deletes files git is not tracking"
        elif sub in ("checkout", "switch") and any(
                f in ("-f", "--force", "--discard-changes") for f in flags):
            dest = "git %s --force discards every uncommitted change" % sub
        elif sub == "checkout" and whole:
            dest = "git checkout . throws away every uncommitted change"
        elif sub == "restore" and whole and not (
                "--staged" in flags and "--worktree" not in flags):
            # `git restore .` is the modern spelling of `git checkout .`, and the
            # one most often reached for. `--staged` alone only unstages: harmless.
            dest = "git restore . throws away every uncommitted change"
        elif a[:2] in (["stash", "drop"], ["stash", "clear"]):
            dest = "git stash %s destroys parked work permanently" % a[1]
        if dest:
            break
    if not dest:
        return None

    # `git -C <dir>` names the folder without ever cd-ing into it.
    if gdir:
        g = os.path.expanduser(_winpath(gdir))
        where = g if os.path.isabs(g) else os.path.join(_winpath(cwd or ""), g)
    else:
        where = target_dir(cmd, cwd)
    if not _within(where, SHARED_REPO):
        return None                      # own worktree — their own work, their call

    try:
        import subprocess
        out = subprocess.run(["git", "status", "--porcelain"], cwd=SHARED_REPO,
                             capture_output=True, text=True, timeout=15)
        dirty = [ln for ln in out.stdout.splitlines() if ln.strip()]
    except Exception:
        return None                      # a broken guard must never wedge his work
    if not dirty:
        return None                      # nothing to lose

    files = [ln[3:] for ln in dirty]
    show = "\n".join("  " + f for f in files[:10])
    more = "" if len(files) <= 10 else "\n  ...and %d more" % (len(files) - 10)
    return (
        "GATE A (wipe guard) — BLOCKED: that would destroy work you did not write.\n"
        "%s, and the SHARED checkout has %d uncommitted file(s) in it right now:\n"
        "%s%s\n"
        "\n"
        "Every session lands in %s by default, so this work probably belongs to\n"
        "another session that is still using it. On 11-Sep-2026 a reset here wiped\n"
        "another session's staged work; the repo already carries stashes named\n"
        "\"stale working tree changes from other sessions\".\n"
        "\n"
        "Do ONE of these instead:\n"
        "  1. Work in your own copy, which is what this folder is not:\n"
        "       git -C %s worktree add /c/wt-<yourjob> -b <your-branch>\n"
        "  2. If that work is genuinely YOURS, park it with a name you will recognise:\n"
        "       git -C %s stash push -u -m \"<what it is> (<your job>)\"\n"
        "     then re-run your command.\n"
        "  3. ONLY if the CFO has told you to discard THIS work: touch %s\n"
        "     (its own switch, not the general OVERRIDE — see below)"
        % (dest, len(files), show, more, SHARED_REPO, SHARED_REPO, SHARED_REPO,
           os.path.join(SWITCHES, "WIPE-OK"))
    )


def _leave_advice(what, letter="B"):
    """The one message both read-only refusals share. It must name the way OUT,
    not just the refusal — the wipe guard's message was read and worked around
    because it demonstrated `git -C <dir>` while forbidding it."""
    return (
        "GATE %s (shared folder is read-only) — BLOCKED: %s in the SHARED checkout.\n"
        "\n"
        "That folder is READ-ONLY. Every chat on this machine starts there, so work\n"
        "left in it belongs to whoever is still using it: on 11-Sep-2026 one session\n"
        "destroyed another's 19 staged files there, and two were never recovered.\n"
        "Its only job is reading origin/main.\n"
        "\n"
        "Work in your own copy instead — 10 seconds, and nobody can touch it:\n"
        "  git -C %s worktree add /c/wt-<yourjob> -b <your-branch>\n"
        "  cd /c/wt-<yourjob>\n"
        "Then re-run what you just tried. Everything is allowed in there.\n"
        "\n"
        "Still allowed in the shared folder: fetch, show, log, status, diff,\n"
        "worktree, and `git stash push -u -m \"...\"` to park something safely.\n"
        "\n"
        "If the CFO has told you to write in the shared folder ANYWAY:\n"
        "  touch %s      (its own 30-minute switch)"
        % (letter, what, SHARED_REPO, os.path.join(SWITCHES, "SHARED-OK"))
    )


def writes_shared_repo(cmd, cwd):
    """Reason this shell line would WRITE in the shared checkout, else None.

    Narrower than it looks: it only fires on git subcommands that change the
    working tree or the index, and only when the line would run inside the
    shared folder. A worktree is never touched.
    """
    for prog, args in commands_run(cmd):
        if prog != "git":
            continue
        a, gdir = _peel_git_globals([x for x in args if x])
        sub = a[0] if a else ""
        if sub not in MUTATING_GIT:
            continue
        nxt = a[1] if len(a) > 1 else ""
        # Bare `git stash` and `git stash -u -m x` ARE push — parking work, which
        # the wipe guard's own message tells a session to do. Only an explicit
        # drop/clear is destructive, and that is Gate A's business.
        if sub == "stash" and (not nxt or nxt.startswith("-")):
            continue
        if (sub, nxt) in GIT_SAFE_PAIRS:
            continue
        # A dry run writes nothing. Blocking one is the exact false-positive class
        # that stopped real work on 11-Sep-2026, when reading a script counted as
        # deploying it. `-n` is only a dry run for `clean` — on `commit` it means
        # --no-verify, which very much does write.
        rest = a[1:]
        if "--dry-run" in rest or (sub == "clean" and "-n" in rest):
            continue
        if gdir:
            g = os.path.expanduser(_winpath(gdir))
            where = g if os.path.isabs(g) else os.path.join(_winpath(cwd or ""), g)
        else:
            where = target_dir(cmd, cwd)
        if _within(where, SHARED_REPO):
            return "`git %s` writes" % sub
    return None


def edits_shared_repo(data):
    """Reason an Edit/Write tool call would write a file in the shared checkout.

    The gate used to watch Bash only, so a session that never typed a git command
    could still build its whole change in the shared folder — which is how the
    work that later got wiped came to be sitting there in the first place.
    """
    if data.get("tool_name") not in EDIT_TOOLS:
        return None
    ti = data.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    if not path:
        return None
    path = os.path.expanduser(str(path))      # "~/notes.md" is not a repo path
    if not os.path.isabs(_winpath(path)):
        path = os.path.join(_winpath(data.get("cwd") or ""), path)
    if _within(path, SHARED_REPO):
        return "editing %s" % os.path.basename(str(path))
    return None


def shared_write_allowed():
    """Its OWN 30-minute token, for the same reason WIPE-OK is not OVERRIDE: a
    blanket set for an unrelated reason must not quietly unlock this one."""
    return _token_fresh(os.path.join(SWITCHES, "SHARED-OK"))


def _token_fresh(tok):
    if not os.path.exists(tok):
        return False
    try:
        if (time.time() - os.path.getmtime(tok)) < OVERRIDE_AGE_MIN * 60:
            return True
        os.remove(tok)
    except Exception:
        pass
    return False


def wipe_allowed():
    """Its OWN 30-minute token, deliberately NOT the general OVERRIDE.

    OVERRIDE is a blanket a session touches to get past the Fable check. Honouring
    it here would switch the wipe guard off for every session on the machine for
    half an hour, for a reason that has nothing to do with destroying anyone's
    work — and one was sitting warm at 22:23 on 11-Sep-2026, set by another
    session, which is very likely the same window the reported wipe happened in.
    Discarding somebody's unsaved work is its own decision and needs its own yes.
    """
    tok = os.path.join(SWITCHES, "WIPE-OK")
    if not os.path.exists(tok):
        return False
    try:
        if (time.time() - os.path.getmtime(tok)) < OVERRIDE_AGE_MIN * 60:
            return True
        os.remove(tok)
    except Exception:
        pass
    return False


SAVES = os.environ.get("GATE_SAVES") or os.path.join(GATE, "saves.jsonl")


def record_save(letter, sid, detail=""):
    """One line every time a gate actually catches something.

    CFO, 14-Sep-2026: the gates work silently, so the only time he ever hears
    about one is when it gets in the way. Every save goes unseen, every false
    positive gets noticed — which is exactly how a gate ends up being switched
    off. This is the counterweight: at close-out he can see "Gate A saved you
    once, Gate E twice" and judge them on the whole record.

    Bookkeeping only. It never blocks, never raises, and a failure here must
    never turn a refusal into an allow.
    """
    try:
        with open(SAVES, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "gate": letter, "session_id": sid, "detail": detail[:200],
            }) + "\n")
    except Exception:
        pass


def block(msg, sid="", letter=""):
    if not letter:
        m = re.match(r"GATE ([A-G])\b", msg)
        letter = m.group(1) if m else "?"
    record_save(letter, sid, msg.splitlines()[0] if msg else "")
    sys.stderr.write(msg + "\n")
    sys.exit(2)


def scoreboard(days=7):
    """`python gate.py --score` — what the gates have actually caught."""
    names = {g[0]: g[1] for g in GATES}
    rows = []
    try:
        with open(SAVES, encoding="utf-8") as f:
            for ln in f:
                if ln.strip():
                    try:
                        rows.append(json.loads(ln))
                    except Exception:
                        pass
    except FileNotFoundError:
        pass
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    recent = []
    for r in rows:
        try:
            t = datetime.fromisoformat(str(r.get("ts", "")).replace("Z", "+00:00"))
            if t.timestamp() >= cutoff:
                recent.append(r)
        except Exception:
            pass
    print("\nGATE SCOREBOARD - last %d days" % days)
    if not recent:
        print("\n  Nothing caught. Either a very clean week, or the gates are not")
        print("  wired - check with:  python gate.py --list\n")
        return
    counts = {}
    for r in recent:
        counts[r.get("gate", "?")] = counts.get(r.get("gate", "?"), 0) + 1
    print()
    for letter in sorted(counts, key=lambda k: -counts[k]):
        n = counts[letter]
        print("  [%s] %-38s %s  (%d)" % (letter, names.get(letter, "unknown"),
                                         "#" * min(n, 30), n))
    print("\n  %d save%s in total. Each one is a problem that never reached him.\n"
          % (len(recent), "" if len(recent) == 1 else "s"))


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        return {}
    cutoff = time.time() - STATE_AGE_H * 3600
    return {k: v for k, v in st.items() if v.get("t", 0) > cutoff}


def save_state(st):
    """Write atomically. A half-written state file reads as "no sessions", which
    fails OPEN — every gate silently satisfied — so the write must never be seen
    partially done."""
    tmp = STATE + ".tmp%d" % os.getpid()
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2)
        # On Windows os.replace raises PermissionError while ANOTHER process has
        # the target open for reading — and a swallowed failure here loses the
        # mark, so Gate D cries wolf over a step that was actually done.
        for _try in range(10):
            try:
                os.replace(tmp, STATE)
                return
            except PermissionError:
                time.sleep(0.01)
        os.replace(tmp, STATE)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass


def mark(sid, **kw):
    """Record a step for THIS session, without losing a parallel session's mark.

    Read-modify-write with no lock: with several chats open, two hooks firing
    together both read the old file and the second write erases the first one's
    mark. The session whose devlog mark vanished is then blocked at push time for
    a step it actually did — a gate crying wolf teaches people to override it,
    which is how gates die.
    """
    lock = STATE + ".lock"
    got = False
    for _ in range(50):                  # ~1s worst case, then proceed anyway
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            got = True
            break
        except FileExistsError:
            try:                         # never let a crashed hook wedge the gate
                if time.time() - os.path.getmtime(lock) > 30:
                    os.remove(lock)
                    continue
            except Exception:
                pass
            time.sleep(0.02)
        except Exception:
            break
    try:
        st = load_state()
        row = st.setdefault(sid, {})
        row.update(kw)
        row["t"] = time.time()
        save_state(st)
    finally:
        if got:
            try:
                os.remove(lock)
            except Exception:
                pass


def _sid8(sid):
    return (sid or "nosession")[:8]


def override_path(sid):
    return os.path.join(SWITCHES, "OVERRIDE-%s" % _sid8(sid))


def overridden(sid=""):
    """An explicit CFO skip, good for 30 minutes, then it deletes itself.

    PER SESSION since 14-Sep-2026. It used to be one machine-wide file, so a
    blanket one chat set to get past its own gate switched the gate off for every
    other chat on the machine — which already happened once with the wipe guard
    (a 30-minute OVERRIDE set at 22:23 on 11-Sep for an unrelated reason). The
    plain `OVERRIDE` file is still honoured so a token set the old way, or by the
    CFO by hand, is not silently ignored.
    """
    return _token_fresh(override_path(sid)) or _token_fresh(OVERRIDE)


LEDGER_CMD = re.compile(r"ledger(\.py|\.sh)", re.I)


def fable_verdict_written(cmd):
    """The verdict this command is recording in the Fable ledger, or None.

    Narrow on purpose: the line must actually RUN ledger.py (not grep it) and
    carry `log --verdict X`. Reading the ledger records nothing.
    """
    if not LEDGER_CMD.search(cmd):
        return None
    for prog, args in commands_run(cmd):
        if not LEDGER_CMD.search(prog) and not any(LEDGER_CMD.search(a) for a in args[:1]):
            continue
        if "log" not in args:
            continue
        for i, a in enumerate(args):
            if a == "--verdict" and i + 1 < len(args):
                return args[i + 1].strip().upper()
            if a.startswith("--verdict="):
                return a.split("=", 1)[1].strip().upper()
    return None


def verdict_judge(cmd):
    """Who gave the verdict: 'fable' unless the ledger line says --judge X."""
    for _prog, args in commands_run(cmd):
        for i, a in enumerate(args):
            if a == "--judge" and i + 1 < len(args):
                return args[i + 1].strip().lower()
            if a.startswith("--judge="):
                return a.split("=", 1)[1].strip().lower()
    return "fable"


def nofable_path(sid):
    return os.path.join(SWITCHES, "NOFABLE-%s" % _sid8(sid))


def nofable_batch(sid):
    """CFO /recc 19-Sep-2026: a batch where HE banned Fable may pass Gate E on a
    written Opus + DeepSeek approval instead. The batch says so with a
    NOFABLE-<chat> file holding his words (at least a sentence) — so every such
    pass is traceable to the instruction that allowed it. No file, no pass."""
    try:
        with open(nofable_path(sid), encoding="utf-8") as f:
            return len(f.read().strip()) >= 20
    except OSError:
        return False


def _head_sha(cwd):
    """The commit this session is sitting on, or "" if it cannot be read."""
    try:
        import subprocess
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd or None,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def fable_problem(sid="", cwd=""):
    """Return a plain reason the Fable gate is not satisfied, or None.

    🔴 THE LEAK THIS CLOSES (Fable 5.1 review, 14-Sep-2026). This used to read the
    LAST line of a machine-wide log and accept any SHIP under 3 hours old — with
    no idea which session it came from or which code it approved. Measured that
    morning: four SHIP verdicts in three hours, every one with an empty
    deploy_sha and no session at all. With several chats open, ONE chat's
    approval silently unlocked EVERY other chat's push, including code Fable had
    never seen. Same class as [[f-a-test-that-cannot-fail]]: a gate that cannot
    tell what it is approving is not a gate.

    A verdict now counts only if it is THIS session's, or was given on the code
    this session is actually sitting on. Scans recent lines rather than only the
    last, so a parallel session appending its own verdict does not invalidate
    yours — that would have swapped a silent leak for a random block.
    """
    # F1 (Fable 5.1, 14-Sep-2026) — TRUST THE HOOK, NOT THE SHELL.
    # The first version asked the /fabe run to stamp its own session id via
    # $CLAUDE_SESSION_ID. That variable is EMPTY in the Bash tool on this
    # machine (measured), so every verdict was written anonymous and Gate E
    # fell back entirely to head_sha — and because this same change freezes the
    # shared checkout, every chat's HEAD there is the SAME commit for ever. One
    # chat's SHIP would still have unlocked another chat's merge, which is the
    # exact hole this was built to close.
    # So the gate records the verdict ITSELF when it watches `ledger.py
    # --verdict ...` go past, the same way Gate D records the Build Log. Nothing
    # the model types can forge it and no environment variable is needed.
    st = load_state().get(sid, {})
    if st.get("fable") and (time.time() - st.get("fable_t", 0)) <= MAX_AGE_H * 3600:
        judge = str(st.get("fable_judge") or "fable").lower()
        if str(st["fable"]).upper() == "SHIP" and judge != "fable" and not nofable_batch(sid):
            return ("your SHIP came from '%s', not Fable — that only counts on a batch "
                    "where the CFO banned Fable (put his words in %s)"
                    % (judge, nofable_path(sid)))
        if str(st["fable"]).upper() == "SHIP":
            return None
        return ("your most recent Fable verdict in this chat was '%s', not SHIP"
                % st["fable"])

    if not os.path.exists(RUNS):
        return "there is no Fable run log at all"
    rows = []
    try:
        with open(RUNS, encoding="utf-8", errors="replace") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        for ln in lines[-40:]:
            try:
                rows.append(json.loads(ln))
            except Exception:
                continue
    except Exception:
        return "the Fable run log is unreadable"
    if not rows:
        return "the Fable run log is unreadable"

    # A HEAD read inside the shared checkout identifies nothing: that folder is
    # frozen by Gates B/C, so every chat sitting in it reports the same commit
    # for ever. Only a commit read from a real worktree can stand for "this
    # work". (Fable 5.1, F1a.)
    head = "" if _within(cwd, SHARED_REPO) else _head_sha(cwd)
    # "nosession" is the placeholder for a payload with no session id. Treating
    # it as an identity would let any such payload match a row literally stamped
    # "nosession". (F1c.)
    me = sid if sid and sid != "nosession" else ""

    fresh_ship = None          # the newest recent SHIP, whoever it belongs to
    for rec in reversed(rows):
        try:
            when = datetime.fromisoformat(str(rec.get("ts", "")).replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - when).total_seconds() / 3600.0
        except Exception:
            continue
        verdict = str(rec.get("verdict", "")).upper()
        rec_sid = str(rec.get("session_id") or "")
        rec_sha = str(rec.get("head_sha") or "")
        # F3 (17-Sep-2026) — A VERDICT THAT NAMES THE COMMIT NEEDS NO CLOCK.
        # The age test used to run FIRST, so a row was thrown away before anyone
        # asked what it reviewed. That is the wrong order. Staleness is a proxy:
        # it stands in for "the code may have moved since", and it is the only
        # thing you have for a verdict identified by nothing but its timestamp.
        # When the row carries the very commit in your hand, you do not need the
        # proxy — you have the thing itself. The code has not changed by a byte.
        #
        # THE BURN: 17-Sep-2026, PR #1157. SHIP was recorded against
        # 7b0e74f2b37cc994538879718767b13cff3a9f27, the branch HEAD, and that is
        # what `gh pr merge` was merging. Four CI shards at ~9 minutes each, then
        # a queue, put the merge about 3h after the review, and Gate E refused it
        # with "no SHIP verdict in the last 3h covers this code, so nothing here
        # has been reviewed". Every word of that was false: the verdict was
        # there, and it covered that code exactly. The session wrote an OVERRIDE
        # to merge green, reviewed work — the fourth false alarm from this gate
        # in a week, and a guard that is wrong right after real work stops being
        # read. The commit-matching half already existed one line below; the age
        # filter simply never let it run.
        #
        # Scope kept deliberately narrow. The exemption needs BOTH shas to exist
        # AND be equal, so nothing widens for a row that pins nothing: a stale
        # verdict for a DIFFERENT commit still dies here, and so does a stale
        # anonymous one. A session-id match is NOT enough on its own — a chat can
        # keep editing after its review, so only the commit proves the code.
        # The practical outer bound is the 40-row scan window above.
        pinned = bool(rec_sha and head and rec_sha == head)
        if age > MAX_AGE_H and not pinned:
            continue
        mine = pinned or (rec_sid and me and rec_sid == me)
        # fresh_ship only feeds the tail messages below, which talk about a
        # RECENT SHIP from somewhere else. An age-exempt row must not fill it or
        # those messages start quoting hours-old verdicts as "recent".
        if verdict == "SHIP" and fresh_ship is None and age <= MAX_AGE_H:
            fresh_ship = (rec, age)
        if not mine:
            continue
        # F2: iterating NEWEST FIRST, the first row that belongs to me decides.
        # Scanning for "any recent SHIP of mine" regressed on a real sequence —
        # my SHIP, then my later FIX — and let the push through. A superseded
        # approval is not an approval.
        rjudge = str(rec.get("judge") or "fable").lower()
        if verdict == "SHIP" and rjudge != "fable" and not nofable_batch(sid):
            return ("your SHIP came from '%s', not Fable — that only counts on a batch "
                    "where the CFO banned Fable (put his words in %s)"
                    % (rjudge, nofable_path(sid)))
        if verdict == "SHIP":
            return None
        return ("your most recent Fable verdict on this work was '%s', not SHIP"
                % (verdict or "unknown"))

    if fresh_ship is None:
        last = rows[-1]
        verdict = str(last.get("verdict", "")).upper()
        if verdict != "SHIP":
            return f"the last Fable verdict was '{verdict or 'unknown'}', not SHIP"
        return (f"no SHIP verdict in the last {MAX_AGE_H}h covers this code, "
                f"so nothing here has been reviewed")

    rec, age = fresh_ship
    if not rec.get("session_id") and not rec.get("head_sha"):
        return ("the most recent SHIP verdict (%.1fh old) does not record WHICH chat "
                "gave it or WHICH commit it reviewed, so it cannot be shown to cover "
                "this code — re-run /fabe here" % age)
    return ("the recent SHIP verdict belongs to a different chat and a different "
            "commit, so it does not cover what you are pushing — run /fabe on THIS "
            "work")


def mode_pre(data):
    tool = data.get("tool_name")
    sid = data.get("session_id") or "nosession"

    # Step 0a: a direct file write into the shared checkout. Not a shell command,
    # so none of the git logic below would ever see it.
    if tool in EDIT_TOOLS:
        try:
            why = None if shared_write_allowed() else edits_shared_repo(data)
        except Exception:
            why = None                   # a broken guard must never wedge his work
        if why:
            block(_leave_advice(why, "C"), sid)
        sys.exit(0)

    if tool != "Bash":
        sys.exit(0)
    cmd = (data.get("tool_input") or {}).get("command", "") or ""
    cwd = data.get("cwd") or os.getcwd()

    # Step 0: do not let one session wipe another's work. Checked BEFORE anything
    # else, because a `git reset` ships nothing — it would fall straight through
    # the ship/deploy exit below, which is exactly how it went unguarded.
    try:
        why = None if wipe_allowed() else wipes_shared_work(cmd, cwd)
    except Exception:
        why = None                       # a broken guard must never wedge his work
    if why:
        block(why, sid)

    # Step 0b: and do not let it BUILD there either — that is how the work a
    # reset destroys comes to be sitting in a shared folder in the first place.
    # wipe_allowed() counts here too: if the CFO has already said "yes, discard
    # that work", refusing the same command a second time under a different
    # heading is just a gate arguing with itself.
    try:
        why = None if (shared_write_allowed() or wipe_allowed()) else \
            writes_shared_repo(cmd, cwd)
    except Exception:
        why = None
    if why:
        block(_leave_advice(why), sid)

    if DEVLOG.search(cmd):
        mark(sid, devlog=True)
        sys.exit(0)

    # Watch the Fable verdict being written, and record it against THIS chat.
    # Evidence the gate saw for itself beats a field the reviewed party fills in.
    v = fable_verdict_written(cmd)
    if v:
        mark(sid, fable=v, fable_t=time.time(), fable_judge=verdict_judge(cmd))
        sys.exit(0)

    is_ship, is_deploy = classify(cmd, cwd)
    if not (is_ship or is_deploy):
        sys.exit(0)

    if overridden(sid):
        mark(sid, pushed=is_ship or None, deployed=is_deploy or None)
        sys.exit(0)

    st = load_state().get(sid, {})

    # Step 1 of the pipeline: the Build Log row.
    if not st.get("devlog"):
        block(
            "GATE D (Build Log) — BLOCKED: the CFO Build Log has no row for this work.\n"
            "Step 1 of /PratThis is to record the request BEFORE building or shipping.\n"
            "Run it first (his words untouched, key shaped cfo-YYYY-MM-DD-slug):\n"
            '  "%s" "%s" ask --key cfo-<date>-<slug> '
            '--text "<his exact words>" --source code --area <area>\n'
            "Then set it to building/waiting as you go, and retry this command."
            # Backslashes and a bare `python` are Windows-only. On the Mac that
            # rendered an unrunnable path, and stock macOS has no `python` at all —
            # so the one job this message has, telling the next session exactly what
            # to type, failed on half the machines it runs on.
            % (sys.executable, os.path.join(HOME, ".claude", "scripts", "devlog.py")),
            sid,
        )

    # Step 2: the Fable gate.
    why = fable_problem(sid, cwd)
    if why:
        block(
            "GATE E (Fable review) — BLOCKED: %s.\n"
            "Run /fabe on THIS diff until it says SHIP, then retry.\n"
            "If the CFO banned Fable for this batch: put his words in %s and record\n"
            "an Opus + DeepSeek approval with: ledger.py log --verdict SHIP --judge opus+deepseek\n"
            "If the CFO explicitly says to skip it: touch %s\n"
            "(that switch is YOURS alone — it used to be one file that turned the\n"
            "gate off for every other chat on the machine.)"
            % (why, nofable_path(sid), override_path(sid)), sid,
        )

    mark(sid, pushed=True if is_ship else st.get("pushed"),
         deployed=True if is_deploy else st.get("deployed"))
    sys.exit(0)


def prune_worktrees():
    """Drop worktree registrations whose folder is gone. Never touches a folder
    that still exists, so no session can lose work to this.

    Pushing every session into its own worktree (the read-only rule above) means
    they accumulate: 36 on 11-Sep, 72 on 14-Sep, many under Temp paths the
    harness had already deleted. Each dead entry still shows in `worktree list`,
    which is the list a session reads to decide where it is safe to work.
    Session end is the right moment — once per session, never on a Bash call.
    """
    try:
        import subprocess
        subprocess.run(["git", "worktree", "prune"], cwd=SHARED_REPO,
                       capture_output=True, text=True, timeout=20)
    except Exception:
        pass                             # bookkeeping must never block a stop


def mode_stop(data):
    # Never fight a stop we already blocked once.
    if data.get("stop_hook_active"):
        sys.exit(0)
    prune_worktrees()
    sid = data.get("session_id") or "nosession"
    st = load_state().get(sid, {})
    if st.get("pushed") and not st.get("deployed"):
        if overridden(sid):
            sys.exit(0)
        block(
            "GATE F (pushed but not deployed) — NOT DONE: code was pushed this session but never deployed.\n"
            "Step 4 of /PratThis: a merged PR that is not deployed is not live, and the "
            "Build Log will never show it finished.\n"
            "Run /deploy now (or say plainly to the CFO why it must wait).\n"
            "If he has already said to hold it back: touch %s" % override_path(sid),
            sid,
        )
    sys.exit(0)


# ── fan-out ───────────────────────────────────────────────────────────────────
# CFO directive 13-Sep-2026, after a build session ran eight independent items
# one at a time: "the instruction is very clear: use multiple agents when you are
# creating." The instruction was ALREADY written in five places — /code, lane-b,
# PratThis, prat-skill 13.2 and the dispatching-parallel-agents skill — and was
# ignored anyway, because a rule living in a document is only read when something
# happens to load that document. This fires on the prompt itself, every time.
#
# It does NOT order a fixed number of agents. A blanket "always 8" would spend
# eight times over to close eleven bug rows, and eight agents editing one
# checkout is what wiped a parallel session's staged work. The trigger is the
# shape of the work: independent parts get their own agent, dependent steps stay
# in order, and going sequential on a multi-part job now costs a written reason.
BUILD_VERBS = re.compile(
    r'\b(build|creat|develop|implement|add|cod|writ|design|redesign|'
    r'migrat|refactor|fix|mak|wir|generat)\w{0,4}\b', re.I)
# "Several things" — a numbered/bulleted list, an explicit plural of work, or a
# batch word. Deliberately narrow: a broad trigger becomes wallpaper and stops
# being read, which is the failure this is here to correct.
MULTI = re.compile(
    r'(^\s*[-*•]\s|^\s*\d+[.)]\s|\b\d+\s+(items?|jobs?|tasks?|things?|'
    r'modules?|pages?|screens?|fixes|bugs?)\b|\ball of (them|these|it)\b|'
    r'\b(these|both|each of)\s+\w+\b|\bbatch\b|\bone by one\b|'
    r'\bmultiple agents?\b|\bin parallel\b)', re.I | re.M)

ORDER = """GATE G (fan-out, CFO directive 13-Sep-2026) - this request looks like work with
independent parts. Before you write the first line of code:
  1. List the parts, one line each.
  2. Spawn ONE Agent per independent part, ALL IN A SINGLE message so they run together.
     A few parts -> the Agent tool. Many parts, or build-then-verify -> the Workflow tool.
  3. Parallel EDITS each need their own git worktree. Parallel reads/checks need none.
  4. Going sequential is allowed ONLY with one written line saying which part depends
     on which. Silently doing them one at a time is the failure this gate exists to stop.
Nothing here skips a gate: fabe stays mandatory and /code still stops before deploy."""


def looks_like_fanout_work(prompt):
    """True when the prompt is build-shaped AND names more than one piece."""
    if not prompt or len(prompt) < 25:
        return False
    return bool(BUILD_VERBS.search(prompt)) and bool(MULTI.search(prompt))


def mode_fanout(data):
    # stdout on UserPromptSubmit is added to the model's context. Never blocks:
    # a bookkeeping hook must not be able to swallow one of his instructions.
    if looks_like_fanout_work(data.get("prompt") or ""):
        sys.stdout.write(ORDER + "\n")
    sys.exit(0)


def list_gates():
    """Print the gate table. `python gate.py --list` — for the CFO, so he can
    say "Gate C fired again" instead of describing the mechanism each time."""
    print("\nTHE GATES — what each one stops, and the switch that turns it off\n")
    print("  %-3s %-38s %s" % ("", "NAME", "SWITCH"))
    for letter, name, what, tok in GATES:
        print("  %-3s %-38s %s" % ("[" + letter + "]", name, tok))
        print("      %s" % what)
    print("\n  A switch is a file in ~/.claude/gate/ and lasts 30 minutes, then")
    print("  deletes itself. <chat> is the first 8 characters of the session id:")
    print("  each chat's switch is its own, so turning a gate off for yourself")
    print("  never turns it off for another chat on the machine.")
    print("\n  Gates B and C are the same rule ('do not work in the shared")
    print("  folder') reaching the two different ways a file gets written.\n")
    sys.exit(0)


def main():
    if "--list" in sys.argv or "--gates" in sys.argv:
        list_gates()
    if "--score" in sys.argv:
        scoreboard()
        sys.exit(0)
    mode = sys.argv[1] if len(sys.argv) > 1 else "pre"
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if mode == "stop":
        mode_stop(data)
    elif mode == "fanout":
        mode_fanout(data)
    else:
        mode_pre(data)


main()
