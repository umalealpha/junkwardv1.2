"""Gate E, CFO /recc 19-Sep-2026: on a batch where the CFO banned Fable, a
written Opus + DeepSeek SHIP passes; anywhere else it does not. Uses a private
state + gate dir — never the live files. Run: python3 test_gate_nofable.py"""
import os, sys, tempfile, time
import types
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "gate.py"), encoding="utf-8").read()
assert _src.rstrip().endswith("main()")
gate = types.ModuleType("gate")
gate.__file__ = "gate.py"
exec(compile(_src.rstrip()[:-len("main()")], "gate.py", "exec"), gate.__dict__)   # load without running the hook

tmp = tempfile.mkdtemp()
gate.GATE = tmp
gate.SWITCHES = tmp
gate.RUNS = os.path.join(tmp, "no-runs.jsonl")
SID = "abcd1234-test-session"
state = {}
gate.load_state = lambda: state
fails = 0


def check(name, got, want_pass):
    global fails
    ok = (got is None) == want_pass
    fails += 0 if ok else 1
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {got!r}"))


def verdict(v, judge):
    state[SID] = {"fable": v, "fable_t": time.time(), "fable_judge": judge}


cmd = 'ledger.py log --system Omni --verdict SHIP --judge opus+deepseek'
check("judge read from the ledger line", None if gate.verdict_judge(cmd) == "opus+deepseek" else "x", True)
check("no --judge means Fable", None if gate.verdict_judge("ledger.py log --verdict SHIP") == "fable" else "x", True)

verdict("SHIP", "fable")
check("Fable SHIP passes (unchanged)", gate.fable_problem(SID, ""), True)

verdict("SHIP", "opus+deepseek")
check("Opus+DeepSeek SHIP WITHOUT a no-Fable batch is blocked", gate.fable_problem(SID, ""), False)

open(gate.nofable_path(SID), "w").write("   ")
check("an empty NOFABLE note does not count", gate.fable_problem(SID, ""), False)

open(gate.nofable_path(SID), "w").write("CFO file 3: do not use Fable 5.1 anywhere in this batch")
check("Opus+DeepSeek SHIP on a no-Fable batch passes", gate.fable_problem(SID, ""), True)

verdict("FIX", "opus+deepseek")
check("Opus+DeepSeek FIX still blocks on a no-Fable batch", gate.fable_problem(SID, ""), False)

print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
