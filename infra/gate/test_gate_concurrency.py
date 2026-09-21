#!/usr/bin/env python3
"""Proof for the MULTI-CHAT / MULTI-MACHINE guards added 14-Sep-2026.

Fable 5.1 reviewed /PratThis as a pipeline for many chats across two computers
and found four things that still assumed one chat per machine. Three are guarded
here (the fourth lives on the prod box, not in this file):

  1. The shared checkout is READ-ONLY, so two chats can never build in the same
     folder. Covers shell git AND the Edit/Write tools, which the gate could not
     see at all before — a chat that never typed a git command could build its
     whole change in the shared folder, which is how the work that got wiped on
     11-Sep-2026 came to be sitting there.
  2. A Fable SHIP verdict counts only for the chat that earned it, or for the
     commit it reviewed. Measured that morning: four SHIPs in three hours, all
     anonymous, so ONE chat's approval unlocked EVERY other chat's push.
  3. The CFO override is per chat. One machine-wide file meant a blanket set by
     one chat switched the gate off for all of them — which already happened on
     11-Sep at 22:23 with the wipe guard.

RED PROOF — every check here must fail without its fix. Run:
    python test_gate_concurrency.py --prove
It reverts each mechanism in a COPY of gate.py, re-runs, and reports how many
checks go red. A check that stays green with its fix removed is not a test.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

GATE = os.path.expanduser("~/.claude/gate/gate.py")
TMP = tempfile.mkdtemp(prefix="gateconc-")
RUNS = os.path.join(TMP, "runs.jsonl")
STATE = os.path.join(TMP, "state.json")
SWITCH = os.path.join(TMP, "switches")       # our OWN override / SHARED-OK folder
os.makedirs(SWITCH, exist_ok=True)
open(RUNS, "w").close()
json.dump({}, open(STATE, "w"))

SID = "CHAT-AAAA1111"
OTHER = "CHAT-BBBB2222"
fails = []


def _mkrepo(name, dirty):
    """A real git repo, because the guard runs `git status` for real."""
    d = os.path.join(TMP, name)
    os.makedirs(d)
    subprocess.run(["git", "init", "-q"], cwd=d, capture_output=True)
    open(os.path.join(d, "seed.txt"), "w").write("x")
    subprocess.run(["git", "add", "-A"], cwd=d, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "seed"], cwd=d, capture_output=True)
    if dirty:
        open(os.path.join(d, "someone_elses_work.py"), "w").write("# not yours\n")
    return d


SHARED = _mkrepo("shared", dirty=True)
MINE = _mkrepo("mine", dirty=False)


def run(gate, mode, payload, shared=SHARED):
    # GATE_SAVES: never let the suite write into the live scoreboard.
    # GATE_SWITCHES: nor into the live OVERRIDE / OVERRIDE-<chat> / SHARED-OK,
    # which this suite used to create in ~/.claude/gate/ where every other chat
    # on the machine reads them. The comment below section 4 admits it ("a crash
    # here used to leave a real override sitting warm") and guarded it with a
    # try/finally, which narrows the window but cannot close it -- and does
    # nothing about two runs overlapping. Same rule as the three vars above.
    env = dict(os.environ, GATE_RUNS=RUNS, GATE_STATE=STATE,
               GATE_SAVES=os.path.join(TMP, "saves.jsonl"),
               GATE_SWITCHES=SWITCH,
               GATE_SHARED_REPO=shared)
    p = subprocess.run([sys.executable, gate, mode], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    return p.returncode, (p.stderr or "").strip()


def bash(gate, cmd, cwd=SHARED, sid=SID):
    return run(gate, "pre", {"tool_name": "Bash", "session_id": sid, "cwd": cwd,
                             "tool_input": {"command": cmd}})


def edit(gate, path, tool="Edit", sid=SID):
    return run(gate, "pre", {"tool_name": tool, "session_id": sid, "cwd": SHARED,
                             "tool_input": {"file_path": path, "old_string": "a",
                                            "new_string": "b"}})


def set_ledger(rows):
    with open(RUNS, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def ship(sid="", sha="", age_h=0.1):
    import time
    ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                       time.gmtime(time.time() - age_h * 3600))
    r = {"ts": ts, "verdict": "SHIP", "note": "conc test"}
    if sid:
        r["session_id"] = sid
    if sha:
        r["head_sha"] = sha
    return r


def check(label, got, want, detail="", out=None):
    ok = got == want
    (out if out is not None else fails).append(label) if not ok else None
    print("%s | %-56s -> %s%s" % ("OK " if ok else "BAD", label,
          "BLOCK" if got == 2 else "pass",
          ("  [" + detail.splitlines()[0][:58] + "]") if detail and got == 2 else ""))


def suite(gate, out):
    PUSH = "g" + "it pu" + "sh origin feat/x"
    DEVLOG = 'python %s ask --key cfo-2026-09-14-x --text "t"' % os.path.expanduser(
        "~/.claude/scripts/devlog.py")

    print("\n--- 1. the shared checkout is READ-ONLY ---")
    r, e = bash(gate, "git commit -am wip")
    check("git commit in the SHARED checkout", r, 2, e, out)
    check("...the refusal hands over the worktree command",
          2 if "worktree add" in e else 0, 2, e, out)
    r, e = bash(gate, "git add -A")
    check("git add in the SHARED checkout", r, 2, e, out)
    r, e = bash(gate, "git pull")
    check("git pull in the SHARED checkout", r, 2, e, out)
    r, _ = bash(gate, "git commit -am wip", cwd=MINE)
    check("git commit in YOUR OWN worktree", r, 0, "", out)
    r, _ = bash(gate, "git fetch origin -q")
    check("git fetch (the folder's one job) still allowed", r, 0, "", out)
    r, _ = bash(gate, "git show origin/main:MACHINE-TALK.md")
    check("reading origin/main still allowed", r, 0, "", out)
    r, _ = bash(gate, "git worktree add /c/wt-x -b feat/x")
    check("git worktree add (the way OUT) still allowed", r, 0, "", out)
    r, _ = bash(gate, 'git stash push -u -m "parking this"')
    check("git stash push (parking) still allowed", r, 0, "", out)
    r, _ = bash(gate, "git clean -n")
    check("a dry run writes nothing, so it is allowed", r, 0, "", out)

    print("\n--- 2. the Edit/Write tools, which the gate could not see before ---")
    r, e = edit(gate, os.path.join(SHARED, "backend", "views.py"))
    check("Edit tool writing into the SHARED checkout", r, 2, e, out)
    r, e = edit(gate, os.path.join(SHARED, "x.py"), tool="Write")
    check("Write tool writing into the SHARED checkout", r, 2, e, out)
    r, _ = edit(gate, os.path.join(MINE, "backend", "views.py"))
    check("Edit tool writing into YOUR OWN worktree", r, 0, "", out)
    r, _ = edit(gate, os.path.expanduser("~/.claude/notes.md"))
    check("Edit tool writing somewhere else entirely", r, 0, "", out)

    print("\n--- 3. a Fable verdict belongs to ONE chat and ONE commit ---")
    # Satisfy the Build Log step first so only the Fable step can block.
    bash(gate, DEVLOG, cwd=MINE)
    set_ledger([ship(sid="", sha="")])
    r, e = bash(gate, PUSH, cwd=MINE)
    check("an ANONYMOUS SHIP no longer unlocks a push", r, 2, e, out)
    set_ledger([ship(sid=OTHER, sha="deadbeef" * 5)])
    r, e = bash(gate, PUSH, cwd=MINE)
    check("ANOTHER chat's SHIP does not cover my push", r, 2, e, out)
    set_ledger([ship(sid=SID)])
    r, _ = bash(gate, PUSH, cwd=MINE)
    check("MY OWN chat's SHIP does unlock my push", r, 0, "", out)
    # A verdict given on the exact commit counts even from another chat: that is
    # the same code, reviewed. Anything stricter would block honest handovers.
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=MINE,
                          capture_output=True, text=True).stdout.strip()
    set_ledger([ship(sid=OTHER, sha=head)])
    r, _ = bash(gate, PUSH, cwd=MINE)
    check("another chat's SHIP on MY EXACT commit does count", r, 0, "", out)
    # A parallel chat appending its own verdict must not invalidate mine: that
    # would swap a silent leak for a random block, which is worse.
    set_ledger([ship(sid=SID), ship(sid=OTHER, sha="f" * 40)])
    r, _ = bash(gate, PUSH, cwd=MINE)
    check("a later verdict from another chat does not erase mine", r, 0, "", out)

    print("\n--- 4. the CFO override is per chat, not per machine ---")
    set_ledger([ship(sid=OTHER, sha="f" * 40)])       # nothing covers me
    # These used to be LIVE files in ~/.claude/gate/, read by every other chat on
    # the machine: the suite's own docstring forbids writing to the thing under
    # test, and a crash here left a real override sitting warm. Since 17-Sep-2026
    # they are ours alone via GATE_SWITCHES. The finally below stays -- cleaning up
    # after yourself is still right, it just no longer has to be load-bearing.
    mine_tok = os.path.join(SWITCH, "OVERRIDE-%s" % SID[:8])
    other_tok = os.path.join(SWITCH, "OVERRIDE-%s" % OTHER[:8])
    try:
        open(other_tok, "w").close()
        r, e = bash(gate, PUSH, cwd=MINE)
        check("ANOTHER chat's override does not unlock mine", r, 2, e, out)
        open(mine_tok, "w").close()
        r, _ = bash(gate, PUSH, cwd=MINE)
        check("MY OWN override unlocks my push", r, 0, "", out)
    finally:
        for t in (mine_tok, other_tok):
            if os.path.exists(t):
                os.remove(t)

    print("\n--- 4b. the two leaks Fable 5.1 found in the first cut ---")
    # F2: a SHIP that a LATER verdict from the same chat has superseded. The
    # first cut scanned for "any recent SHIP of mine" and let this through.
    set_ledger([ship(sid=SID), {"ts": ship(sid=SID)["ts"], "verdict": "FIX",
                                "session_id": SID}])
    r, e = bash(gate, PUSH, cwd=MINE)
    check("my SHIP, then my later FIX, does NOT unlock a push", r, 2, e, out)

    # F1a: a commit read INSIDE the shared checkout identifies nothing — Gates
    # B/C freeze that folder, so every chat sitting in it reports the same
    # commit for ever. Matching on it would have kept the whole leak open, and
    # the shared folder is where every chat starts.
    shared_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=SHARED,
                                 capture_output=True, text=True).stdout.strip()
    set_ledger([ship(sid=OTHER, sha=shared_head)])
    r, e = bash(gate, PUSH, cwd=SHARED)
    check("a SHIP stamped with the FROZEN shared HEAD unlocks nothing", r, 2, e, out)

    # F1b: the gate records the verdict when it WATCHES ledger.py write one.
    # This is the path that actually works, because the shell has no session id.
    set_ledger([])
    ledger = os.path.expanduser("~/.claude/skills/fabe/scripts/ledger.py")
    r, _ = bash(gate, 'python "%s" log --verdict SHIP --system Omni' % ledger, cwd=MINE)
    check("running ledger.py with SHIP is allowed through", r, 0, "", out)
    r, _ = bash(gate, PUSH, cwd=MINE)
    check("...and the gate now counts that SHIP for THIS chat", r, 0, "", out)
    r, _ = bash(gate, 'python "%s" log --verdict FIX --system Omni' % ledger, cwd=MINE)
    check("recording a later FIX is allowed through", r, 0, "", out)
    r, e = bash(gate, PUSH, cwd=MINE)
    check("...and that FIX now blocks the push", r, 2, e, out)
    # Another chat writing its own SHIP must not unlock mine.
    r, _ = bash(gate, 'python "%s" log --verdict SHIP --system Omni' % ledger,
                cwd=MINE, sid=OTHER)
    r, e = bash(gate, PUSH, cwd=MINE)
    check("another chat recording SHIP does not unlock mine", r, 2, e, out)

    print("\n--- 4c. the papercuts ---")
    r, _ = bash(gate, "git stash")
    check("bare `git stash` is parking, not destroying", r, 0, "", out)
    r, _ = bash(gate, 'git stash -u -m "parking"')
    check("`git stash -u -m` is parking too", r, 0, "", out)
    r, _ = edit(gate, "~/notes.md")
    check("a ~/ path is not inside the shared checkout", r, 0, "", out)

    print("\n--- 5. SHARED-OK is its own switch ---")
    tok = os.path.join(SWITCH, "SHARED-OK")
    try:
        open(tok, "w").close()
        r, _ = bash(gate, "git commit -am wip")
        check("SHARED-OK deliberately allows a write there", r, 0, "", out)
    finally:
        if os.path.exists(tok):
            os.remove(tok)


def prove():
    """Remove each mechanism in a COPY and confirm the checks go red."""
    src = open(GATE, encoding="utf-8").read()
    sabotage = {
        "read-only shared checkout": (
            "        if _within(where, SHARED_REPO):\n"
            "            return \"`git %s` writes\" % sub\n",
            "        if False:\n"
            "            return \"`git %s` writes\" % sub\n"),
        "Edit/Write tools watched": (
            "    if _within(path, SHARED_REPO):\n"
            "        return \"editing %s\" % os.path.basename(str(path))\n",
            "    if False:\n"
            "        return \"editing %s\" % os.path.basename(str(path))\n"),
        "verdict bound to chat/commit": (
            "        rec_sid = str(rec.get(\"session_id\") or \"\")",
            "        return None\n        rec_sid = str(rec.get(\"session_id\") or \"\")"),
        # Restore the OLD single-file behaviour: any chat's override counts.
        # The first sabotage only broke the positive case, so the check that
        # actually proves the leak stayed green either way — a proof that proves
        # nothing is the same failure as a test that cannot fail.
        "override is per chat": (
            "    return _token_fresh(override_path(sid)) or _token_fresh(OVERRIDE)",
            "    import glob as _g\n"
            "    return any(_token_fresh(p) for p in _g.glob(os.path.join(GATE, 'OVERRIDE*')))"),
        "verdict recorded by the hook": (
            '    v = fable_verdict_written(cmd)',
            '    v = None and fable_verdict_written(cmd)'),
    }
    print("\n" + "=" * 70)
    print("RED PROOF — each mechanism removed in a copy, suite re-run")
    print("=" * 70)
    allred = True
    for name, (old, new) in sabotage.items():
        if src.count(old) != 1:
            print("\n!! could not sabotage %r (matched %d times) — "
                  "the proof is NOT valid" % (name, src.count(old)))
            allred = False
            continue
        broken = os.path.join(TMP, "broken.py")
        open(broken, "w", encoding="utf-8", newline="").write(src.replace(old, new))
        # Prove the sabotage actually landed. A stash/patch that silently does
        # nothing, then reports "3 passed", is a FALSE GREEN — it happened here
        # on 12-Sep-2026 and was nearly reported as proof.
        assert new in open(broken, encoding="utf-8").read(), "sabotage did not apply"
        red = []
        print("\n--- without: %s ---" % name)
        try:
            suite(broken, red)
        except Exception as exc:
            red.append("suite crashed: %s" % exc)
        print("  => %d check(s) went RED" % len(red))
        if not red:
            print("  !! NOTHING went red. This mechanism is NOT under test.")
            allred = False
    return allred


if __name__ == "__main__":
    try:
        print("=" * 70)
        print("MULTI-CHAT GUARDS — live gate")
        print("=" * 70)
        suite(GATE, fails)
        ok = True
        if "--prove" in sys.argv:
            ok = prove()
        print("\nRESULT:", "ALL PASS" if not fails else "%d FAILED: %s" % (len(fails), fails))
        if "--prove" in sys.argv:
            print("RED PROOF:", "every mechanism proven" if ok else "INCOMPLETE — see above")
        sys.exit(0 if (not fails and ok) else 1)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
