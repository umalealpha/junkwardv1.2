# The pipeline gate

Machine-local Claude Code hooks that make three things unskippable — the Build
Log row, the Fable SHIP verdict, and the deploy after a push — plus **Step 0**,
which stops one session destroying another session's unsaved work.

This folder is the **source of truth for both machines**. The copy that actually
runs lives at `~/.claude/gate/`.

## Why Step 0 exists

On 11-Sep-2026 a session reported: *"I just destroyed another session's staged
work with that reset."*

The rest of the gate runs at push / PR / deploy time and asks "is this good
enough to ship". A `git reset` ships nothing, so it fell straight through
unguarded.

It was not a one-off. The shared checkout was holding 20 uncommitted files of
someone's half-built feature, and the repo already carried 11 stashes — three of
them named for what they were: *"stale working tree changes from other
sessions"*, *"skills-sync WIP (another session)"*. 36 worktrees exist, so the
right habit was there; nothing enforced it, because every session starts in the
shared folder.

Step 0 refuses the wholesale destroyers (`reset --hard`, `clean -f`,
`checkout .`, `restore .`, `checkout|switch --force`, `stash drop|clear`) when
they are aimed at `~/work/alpha-finance` — or anywhere inside it — while it holds
uncommitted work, and it names the files at risk. Deliberately narrow: undoing
your own single file (`git checkout -- one/file.py`) is normal work and stays
allowed. An earlier version of this gate blocked targeted checkout and stopped
real work; that lesson is baked into the tests.

## Install on a machine

```bash
mkdir -p ~/.claude/gate
cp infra/gate/*.py ~/.claude/gate/
python3 ~/.claude/gate/wire.py        # proves it, THEN wires it
python3 ~/.claude/gate/test_gate.py              # must print: RESULT: ALL PASS
python3 ~/.claude/gate/test_gate_fanout.py       # must print: RESULT: ALL PASS
python3 ~/.claude/gate/test_gate_concurrency.py --prove   # ALL PASS + every mechanism proven
python3 ~/.claude/gate/test_gate_automerge.py    # must print: RESULT: ALL PASS
```

`wire.py` does not finish on "I wrote the file". It runs the wired command
exactly as the harness will — the same string, through a shell — feeds it a
payload that must be refused, and only writes `settings.json` once it has seen
the refusal. If the gate cannot run on that machine it wires **nothing** and says
so.

That matters because of how this nearly shipped broken: the first version
hardcoded `python` and a Windows path. Stock macOS has no `python`, only
`python3`, and Claude Code treats a hook that fails to start as "no objection".
On the Mac it would have installed cleanly, looked perfectly wired, and protected
nothing at all — the same class of silent failure this whole gate exists to end.

## A test that lived outside the repo (16-Sep-2026)

`test_gate_automerge.py` — the proof for Gate F's "merging IS deploying" rule —
sat only at `~/.claude/gate/` on the Windows PC for four days, in no repository.
So it never ran on the Mac, nobody reviewed it, and when the 14-Sep session
scoping landed underneath it, its fixture went stale and five checks went red
with nobody watching. It is in this folder now, and its every "NOT deployed"
check has a partner that proves the fixture got past Gates D and E first —
without that, a fully blocked gate reads exactly like a clean set of negatives.

It also used to name two hardcoded Windows paths, one of them a throwaway
worktree. It now builds its own fixture repos out of the only thing
`repo_auto_deploys` reads, so it runs anywhere and a gate that regressed to a
list of known deploy paths would fail it.

## Keeping the two copies together

`test_gate.py` fails if the installed `~/.claude/gate/gate.py` differs from this
folder's copy. That catches drift **when someone runs the tests**, which is not
the same as catching it when it happens — a known gap, not a solved problem. If
you change the gate, change it here and re-install on both machines.

## Notes for whoever touches this next

- The tests point `GATE_RUNS` / `GATE_STATE` / `GATE_SHARED_REPO` at throwaway
  files. They used to write fake SHIP verdicts into the live Fable ledger that
  the gate reads — 16 had accumulated, the newest was the last line, so running
  the tests disarmed the gate for three hours. A test must not write to the thing
  it is testing.
- Every failure path inside the hook exits 0. A broken gate must never wedge the
  CFO's work; it fails open, loudly in the logs, never silently blocking.
- `WIPE-OK` is Step 0's own 30-minute token. It is **not** the general `OVERRIDE`,
  which sessions touch to get past the Fable check — honouring that here switched
  the wipe guard off machine-wide for an unrelated reason.


---

## The gates have letters (14-Sep-2026)

    python3 ~/.claude/gate/gate.py --list     what each gate stops, and its switch
    python3 ~/.claude/gate/gate.py --score    what they have actually caught

| | Gate | Stops | Switch |
|---|---|---|---|
| A | Wipe guard | one chat destroying another chat's uncommitted work | `WIPE-OK` |
| B | Shared folder read-only (shell) | a chat BUILDING in the folder every chat shares | `SHARED-OK` |
| C | Shared folder read-only (Edit/Write) | the same, for edits that never touch a shell | `SHARED-OK` |
| D | Build Log row | pushing before the request is recorded in his words | `OVERRIDE-<chat>` |
| E | Fable review | pushing before Fable 5.1 says SHIP for THIS chat/commit | `OVERRIDE-<chat>` |
| F | Pushed but not deployed | a session ending on merged work that never went live | `OVERRIDE-<chat>` |
| G | Fan-out order | a multi-part job done one at a time (advises, never blocks) | n/a |

Letters are PERMANENT: retiring a gate leaves its letter unused rather than
shuffling the others up. A letter that changes meaning is worse than no letter.

## What the multi-chat pass added, and why (Fable 5.1 review, 14-Sep-2026)

Reviewed as a pipeline for MANY chats across TWO computers. Four things still
assumed one chat per machine; three are fixed here.

**B and C - the shared checkout is read-only.** Gate A only caught the wholesale
destroyers, which left every chat free to BUILD in the shared folder - and that is
how the work a reset later destroys comes to be sitting there at all. Measured that
day: 72 worktrees (36 three days earlier) and 12 stashes, three named "stale working
tree changes from other sessions". The folder now has one job, reading origin/main,
and a chat that wants to change anything must move to its own worktree.
NOTE, precisely: this is read-only **for git commands and for Claude's file-edit
tools**. A chat can still write files there through the shell (`sed`, `echo >`, a
heredoc, a python one-liner) and this gate will not see it. Work can therefore
still pile up in that folder; what it cannot do is get committed there.
C exists because the gate watched Bash only: a chat that never typed a git command
could build its whole change there unseen. `settings.json` must therefore match
`Bash|Edit|Write|MultiEdit|NotebookEdit`, not `Bash`.

**E - a verdict now names its chat and its commit.** It used to read the last line of
this machine-wide log and accept any SHIP under 3h old, with no idea whose it was.
Measured that morning: four SHIPs in three hours, every one anonymous. With several
chats open, ONE chat's approval silently unlocked EVERY other chat's push, including
code Fable had never seen. The first cut asked /fabe to stamp its own
`--session-id` from `$CLAUDE_SESSION_ID` - and that variable is EMPTY in the Bash
tool (measured), so every verdict was written anonymous and Gate E fell back
entirely to `head_sha`. Worse, because this same change FREEZES the shared
checkout, every chat sitting there reports the same HEAD for ever, so the leak
would have stayed wide open by default. Fable 5.1 caught it in review.
**The gate now records the verdict itself** when it watches `ledger.py
--verdict ...` run, exactly as Gate D records the Build Log - evidence the gate
saw beats a field the reviewed party fills in. A commit read INSIDE the frozen
shared checkout is ignored, and a later FIX supersedes an earlier SHIP.

**Per-chat switches.** `OVERRIDE` was one machine-wide file, so a blanket set by one
chat turned the gate off for all of them - which had already happened on 11-Sep at
22:23 with the wipe guard. Now `OVERRIDE-<first 8 of the session id>`.

**The drift check had never run once.** It read the WORKING COPY at
`infra/gate/gate.py`, which on the Windows box held only README.md, so
`os.path.exists(...)` was False every time and the check silently skipped while the
installed gate drifted 120 lines ahead. It now reads `origin/main:infra/gate/gate.py`
and FAILS LOUDLY when it cannot, because a guard whose precondition is never met is
not a lenient guard, it is an absent one.

**Still open (needs the prod box, not this repo):** the `git reset` that pins prod to
a SHA runs OUTSIDE `deploy-zero-downtime.sh`'s lock, and nothing refuses a SHA older
than the one already live.
