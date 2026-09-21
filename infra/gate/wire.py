#!/usr/bin/env python3
"""Wire the pipeline gate into this machine's Claude settings — and PROVE it fires.

WHY THE PROOF STEP EXISTS
-------------------------
The first version hard-coded `python` and a Windows path:

    G = 'python "C:/Users/PrathapAsus/.claude/gate/gate.py" '

On stock macOS there is no `python` — only `python3`. So on the Mac the hook
would have been written into settings.json, looked perfectly installed, and
never run once. Claude Code treats a hook that fails to start as "no objection",
so every check in this gate would have been silently off while the file sat
there claiming otherwise. That is the same failure that started this whole
exercise: something reported as working that had never worked at all.

So this script does not finish on "I wrote the file". It runs the wired command
exactly as the harness will run it, feeds it a payload that MUST be refused, and
only reports success when it actually sees the refusal.
"""
import json
import os
import subprocess
import sys

HOME = os.path.expanduser("~")
SETTINGS = os.path.join(HOME, ".claude", "settings.json")
GATE_PY = os.path.join(HOME, ".claude", "gate", "gate.py")
OVERRIDE = os.path.join(HOME, ".claude", "gate", "OVERRIDE")

# sys.executable is the interpreter that is really here, whatever it is called.
PY = sys.executable or ("python" if os.name == "nt" else "python3")
G = '"%s" "%s" ' % (PY, GATE_PY.replace("\\", "/"))

# ---- prove it FIRST, wire it after ----
# Proving needs nothing from settings.json, and writing first would leave a dead
# hook line behind on a machine where the gate cannot actually run.
#
# The probe runs the SAME STRING the harness will run, through a shell — not a
# tidy argument list. Those are different things: a list proves the interpreter
# exists, the string proves the quoting and the path survive the shell too, which
# is where a Windows path or a missing `python` actually bites.
if os.path.exists(OVERRIDE):
    sys.exit("wire: cannot prove anything while %s exists — it makes the gate\n"
             "wire: stand aside on purpose. Delete it (or wait for it to expire)\n"
             "wire: and run this again." % OVERRIDE)

probe = {"tool_name": "Bash", "session_id": "WIRE-PROOF",
         "tool_input": {"command": "g" + "it pu" + "sh origin HEAD:main"}}
try:
    p = subprocess.run(G + "pre", shell=True, input=json.dumps(probe),
                       capture_output=True, text=True, timeout=60)
except Exception as exc:                                  # noqa: BLE001
    sys.exit("wire: FAILED — could not run the gate at all (%s). NOT wired." % exc)

if p.returncode != 2:
    sys.exit(
        "wire: FAILED — the gate did not refuse a push with no Build Log row "
        "(exit %s).\nwire: NOTHING has been wired; it would have looked installed "
        "and protected nothing.\nwire: command tried: %spre\nwire: stderr: %s"
        % (p.returncode, G, (p.stderr or "").strip()[:300])
    )

with open(SETTINGS, encoding="utf-8") as f:
    s = json.load(f)
s.setdefault("hooks", {})

pre = [h for h in s["hooks"].get("PreToolUse", []) if "gate.py" not in json.dumps(h)]
# Gates B and C need the file-edit tools too. This said "Bash", and wire.py
# REPLACES any existing gate entry — so running it on the Mac would have
# installed Gate C dead, and re-running it here would silently revert the
# widened matcher. (Fable 5.1, F3, 14-Sep-2026.)
pre.append({"matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit", "hooks": [{"type": "command", "command": G + "pre"}]})
s["hooks"]["PreToolUse"] = pre

stop = [h for h in s["hooks"].get("Stop", []) if "gate.py" not in json.dumps(h)]
stop.append({"hooks": [{"type": "command", "command": G + "stop"}]})
s["hooks"]["Stop"] = stop

with open(SETTINGS, "w", encoding="utf-8") as f:
    json.dump(s, f, indent=2)

print("wire: VERIFIED — the gate runs on this machine and refused a gated push,")
print("wire: then was wired into settings.json.")
print("wire:   interpreter %s" % PY)
print("wire:   command     %spre" % G)
print("wire: now prove the wipe guard too:  %s %s"
      % (PY, os.path.join(HOME, ".claude", "gate", "test_gate.py")))
