"""The fan-out gate — proof it fires, and proof it stays quiet.

CFO directive 13-Sep-2026. Eight independent items were built one at a time in a
session where /code, lane-b, PratThis, prat-skill §13.2 and the
dispatching-parallel-agents skill ALL already said to fan out. A rule written in
a document is read only when something happens to load that document; this one
fires on the prompt itself, every time.

Both halves matter. A gate that never fires is the bug we started with. A gate
that fires on everything becomes wallpaper, stops being read, and ends up in the
same place. So the silent cases below are not padding — they are the other half
of the fix.

Delete mode_fanout from gate.py and the three "fires" checks go red.

Run:  python test_gate_fanout.py
"""
import json
import os
import subprocess
import sys

G = os.path.expanduser("~/.claude/gate/gate.py")   # not a hardcoded C:/ path —
                                                   # the suite must run on the Mac too.
fails = []


def run(prompt):
    """(exit code, what the hook injected into the model's context)."""
    p = subprocess.run(
        [sys.executable, G, "fanout"],
        input=json.dumps({"prompt": prompt, "session_id": "TEST-FANOUT"}),
        capture_output=True, text=True)
    return p.returncode, (p.stdout or "")


def check(label, prompt, should_fire):
    rc, out = run(prompt)
    # Match the gate LETTER, not the prose. This assertion broke the moment the
    # gates were given names (14-Sep-2026) because it keyed on the old heading —
    # it failed on a rename rather than on a change in behaviour.
    fired = "GATE G" in out
    ok = rc == 0 and fired == should_fire
    if not ok:
        fails.append(label)
    print(f"{'OK ' if ok else 'BAD'} | {label:<46} -> "
          f"{'fired' if fired else 'silent':<6} rc={rc}")


# ── it fires on work that splits into independent parts ──────────────────────
check("two named things to build",
      "build the forgiveness dashboard and the cron dashboard, these two things", True)
check("a numbered list of jobs",
      "1. fix the tile\n2. add the filter\n3. write the report", True)
check("his own words, the day it was raised",
      "use multiple agents when you are creating this", True)
check("a batch of modules",
      "create the eight modules in this batch", True)
check("several screens named as a group",
      "fix these three screens please", True)

# ── it stays quiet on everything else ────────────────────────────────────────
check("a plain question",
      "what is our FY25 GWP for ADIC standalone?", False)
check("one small fix",
      "fix the address on the letter template", False)
check("an instruction that builds nothing",
      "go to the bug board and close all of them", False)
check("a status question",
      "can you check whether the development board is updated", False)

# ── it can never swallow an instruction ──────────────────────────────────────
# Every failure path in this hook exits 0 on purpose: bookkeeping must not be
# able to wedge his work. Exit 2 here would block the prompt itself.
for odd in ("", "hi", "   ", "x" * 5000):
    rc, _ = run(odd)
    if rc != 0:
        fails.append(f"odd input blocked (rc={rc})")
print(f"{'OK ' if not any('odd input' in f for f in fails) else 'BAD'} | "
      f"{'odd input never blocks':<46} -> rc=0")

print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
