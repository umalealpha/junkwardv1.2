"""Does the gate count a merge as a deploy, in a repo that ships on merge?

Driven as a SUBPROCESS with a JSON payload, the way test_gate.py does it --
gate.py calls main() at import time with no __main__ guard, so importing it
blocks on stdin. (Found the hard way: the first version of this test ran
silently, exited 0, and proved nothing, then hung.)

Run:  python test_gate_automerge.py

WHAT CHANGED UNDERNEATH IT (16-Sep-2026). This file sat only at
~/.claude/gate/ on the Windows PC for four days, in no repository, so nothing
ran it on the Mac and nobody reviewed it. In that time it went red in five
places, and both causes were in the test, not in the gate:

  1. Gate E became SESSION-SCOPED (14-Sep, PR #987). A SHIP verdict now counts
     only if it names the chat that earned it or the commit it reviewed. This
     file's fixture wrote `{"ts": ..., "verdict": "SHIP"}` and nothing else, so
     from 14-Sep every case was BLOCKED at Gate E and the gate never reached the
     line that records pushed/deployed. The fixture now stamps `session_id`.

  2. It pointed at two hardcoded Windows paths. One of them, uc-be-fix, is a
     throwaway worktree, not a clone anyone keeps -- and neither path exists on
     the Mac, where `repo_auto_deploys` would answer False for a missing folder
     and case 1 would go red for a reason that has nothing to do with the gate.
     The fixtures below are built here, out of the only thing the gate actually
     reads: a workflow that triggers on main and ships something. That is a
     STRONGER test of the original bug than naming a real path was -- a gate
     that went back to carrying a list of known deploy paths would fail it,
     because no list can contain a folder made three lines ago.

The original bug this file guards (12-Sep): the gate knew a closed list of ways
to deploy, so UniCoin -- which ships on push to main -- had five verified
deploys recorded as "never deployed", and the CFO was told so after each one.

AND THE REASON IT WENT UNNOTICED FOR FOUR DAYS: every "NOT deployed" check
stayed green through all of it. Of course it did -- the gate was refusing at
Gate E and recording nothing at all, and "nothing recorded" reads exactly like
"correctly not a deploy". A check that cannot tell those two apart is not a
check. So the first thing asserted below is that the fixture gets PAST the
gates; if it ever goes stale again, that line goes red first and prints the
gate's own words for why.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

G = os.path.expanduser("~/.claude/gate/gate.py")   # not a hardcoded C:/ path --
                                                   # the suite must run on the Mac too.

# Its own throwaway ledger, state AND scoreboard -- every live store gate.py
# reads from an env var. A test must never write to the thing it is testing.
_TMP = tempfile.mkdtemp(prefix="gate-automerge-")
RUNS = os.path.join(_TMP, "runs.jsonl")
STATE = os.path.join(_TMP, "state.json")
SID = "AUTOMERGE-TEST"

# A SHIP verdict dated ONE HOUR AGO, stamped with THIS session. The date matters:
# an earlier version used 2099, the age check reads that as invalid, so the gate
# blocked before it recorded anything and every assertion failed for a reason
# that had nothing to do with what was being tested. The session id matters for
# the same reason -- see note 1 in the docstring.
#
# And before THAT, this test only "passed" because a live OVERRIDE happened to be
# warm -- a green produced by the environment rather than by the code. Third
# false green of the night, same cause every time: the conditions were not
# controlled. A test that has not been watched fail is not known to work.
_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
with open(RUNS, "w") as f:
    f.write(json.dumps({"ts": _ts, "verdict": "SHIP", "session_id": SID}) + "\n")
json.dump({}, open(STATE, "w"))
SAVES = os.path.join(_TMP, "saves.jsonl")
# GATE_SAVES too. Isolating the ledger and the state but NOT the scoreboard
# left every block() this file provokes appending to the LIVE
# ~/.claude/gate/saves.jsonl -- the record the CFO reads with `gate.py
# --score`. Measured when Fable 5.1 caught it on 16-Sep-2026: 43 of that
# file's 113 rows were stamped AUTOMERGE-TEST, so four days of this file
# refusing its own stale fixture had been reporting itself to him as Gate E
# saving him dozens of times. The sister file test_gate.py had redirected
# GATE_SAVES from the start, with the reason written next to it; this one
# claimed in its own docstring never to write to the live stores and did.
# GATE_SWITCHES completes the set (17-Sep-2026). This suite never CREATED a
# live switch, so it was not part of the incident in #1174 -- but its 0a guard
# still read the live folder, which made it the one suite a real session could
# turn red just by holding its own override. Isolating it is what lets that
# guard be deleted rather than merely tolerated.
SWITCH = os.path.join(_TMP, "switches")
os.makedirs(SWITCH, exist_ok=True)
ENV = dict(os.environ, GATE_RUNS=RUNS, GATE_STATE=STATE, GATE_SAVES=SAVES,
           GATE_SWITCHES=SWITCH)


def _repo(name, ships):
    """A folder shaped like the only thing repo_auto_deploys reads.

    `ships=True` gives a workflow that triggers on main AND pushes an image --
    both halves are required, because a workflow merely NAMED deploy proves
    nothing. `ships=False` gives a folder with no workflows at all, which is
    every repo that has to be deployed on purpose, Omni included.
    """
    d = os.path.join(_TMP, name)
    if ships:
        wf = os.path.join(d, ".github", "workflows")
        os.makedirs(wf, exist_ok=True)
        with open(os.path.join(wf, "deploy.yml"), "w") as fh:
            fh.write("on:\n  push:\n    branches: [main]\n"
                     "jobs:\n  ship:\n    steps:\n      - run: docker push $IMAGE\n")
    else:
        os.makedirs(d, exist_ok=True)
    return d


SHIPS_ON_MERGE = _repo("ships-on-merge", ships=True)    # stands for UniCoin
DEPLOYED_ON_PURPOSE = _repo("deploy-on-purpose", ships=False)   # stands for Omni

fails = []


def pre(cmd, cwd):
    """Run the gate's pre hook, then report what it recorded for the session.

    Returns (pushed, deployed, refusal) -- the third being the gate's own words
    if it BLOCKED. Throwing that away is what let this file stay red for four
    days while reading like a clean set of negative results.
    """
    # Build Log step satisfied. The "t" is NOT optional: load_state() drops any
    # entry whose t is older than the cutoff, and a missing t reads as 0, so an
    # entry without one is silently treated as expired and the session looks
    # brand new. Cost half an hour of blaming the gate for a fault in this file.
    json.dump({SID: {"devlog": True, "t": time.time()}}, open(STATE, "w"))
    p = subprocess.run(
        [sys.executable, G, "pre"],
        input=json.dumps(
            {"tool_name": "Bash", "session_id": SID, "cwd": cwd,
             "tool_input": {"command": cmd}}
        ),
        capture_output=True, text=True, env=ENV,
    )
    st = json.load(open(STATE)).get(SID, {})
    refusal = (p.stderr or "").strip().splitlines()
    return bool(st.get("pushed")), bool(st.get("deployed")), \
        (refusal[0] if refusal else "")


def check(label, got, want, why=""):
    ok = got == want
    if not ok:
        fails.append(label)
    print(f"{'ok  ' if ok else 'FAIL'} | {label:<52} got={got} want={want}")
    if not ok and why:
        print(f"     | {why}")


# The 0a check that used to sit here is GONE, deliberately (17-Sep-2026).
#
# It asked whether a warm override was sitting in the LIVE gate folder, because
# one would short-circuit mode_pre before Gates D and E and turn this whole file
# green however stale its fixture was -- the docstring above records a night it
# "passed" for exactly that reason. That was the right guard while this suite
# shared the live switches with every other chat on the machine.
#
# It stopped being a guard the moment GATE_SWITCHES was set above: the gate under
# test now reads OUR empty folder, so a token in the live one cannot reach it. All
# the check could still do is go red because a REAL session was holding its own
# override -- a guard that can only produce false alarms, which is the failure
# this gate keeps being criticised for. Fable 5.1 called for its removal rather
# than leaving code that contradicts the comment five lines above it.
#
# If the isolation is ever removed, bring this back with it.

print("\n0. The fixture still gets past Gates D and E")
_, _, why = pre("gh pr merge 53 --squash --admin", SHIPS_ON_MERGE)
check("harness: the gate is not refusing the fixture", why, "", why)
if why:
    print("     | Everything below would read as a clean set of negatives.")
    print("     | It is not -- the gate never got as far as recording anything.")

print("\n1. A repo that ships on push to main -> merging IS deploying")
ship, deploy, why = pre("gh pr merge 53 --squash --admin", SHIPS_ON_MERGE)
check("ships-on-merge: merge recorded as pushed", ship, True, why)
check("ships-on-merge: merge recorded as DEPLOYED", deploy, True, why)

print("\n2. Opening a PR is still not a deploy")
ship, deploy, why = pre("gh pr create --title x --body y", SHIPS_ON_MERGE)
check("pr create: pushed", ship, True, why)
check("pr create: NOT deployed", deploy, False, why)

print("\n3. A plain push is still not a deploy")
ship, deploy, why = pre("g" + "it pu" + "sh origin feat/x", SHIPS_ON_MERGE)
check("push: pushed", ship, True, why)
check("push: NOT deployed", deploy, False, why)

print("\n4. A repo deployed on purpose -- its merges must still not count")
ship, deploy, why = pre("gh pr merge 900 --squash --admin", DEPLOYED_ON_PURPOSE)
check("deploy-on-purpose: merge recorded as pushed", ship, True, why)
check("deploy-on-purpose: merge NOT deployed", deploy, False, why)

print("\n5. A named deploy path still registers, wherever it is run")
_, deploy, why = pre("python fabe/scripts/deploy_ssm.py", DEPLOYED_ON_PURPOSE)
check("deploy_ssm.py: deployed", deploy, True, why)
_, deploy, why = pre("grep -n foo deploy-zero-downtime.sh", DEPLOYED_ON_PURPOSE)
check("grepping the script: NOT deployed", deploy, False, why)

print("\n6. The real UniCoin clone, when this machine has one")
UNICOIN = os.path.expanduser("~/work/unicoin-backend")
if pathlib.Path(UNICOIN, ".github", "workflows").is_dir():
    _, deploy, why = pre("gh pr merge 53 --squash --admin", UNICOIN)
    check("unicoin-backend: merge recorded as DEPLOYED", deploy, True, why)
else:
    print("     skip (no unicoin-backend clone here -- cases 1-5 still cover it)")

print()
if fails:
    print(f"{len(fails)} FAILED: {', '.join(fails)}")
    sys.exit(1)
print("RESULT: ALL PASS")
