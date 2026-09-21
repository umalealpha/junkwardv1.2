import json, os, re, shutil, subprocess, sys, tempfile, time

G = os.path.expanduser("~/.claude/gate/gate.py")   # not a hardcoded C:/ path:
                                                   # every check failed on the Mac before it started.

# Throwaway copies of the two files the gate reads. The suite used to write fake
# SHIP verdicts into the LIVE Fable ledger and snapshot/restore the LIVE session
# state — so running the tests disarmed the gate for three hours, and erased
# whatever a parallel session had stamped meanwhile. A test must not write to the
# thing it is testing.
_TMP = tempfile.mkdtemp(prefix="gatetest-env-")
RUNS = os.path.join(_TMP, "runs.jsonl")
STATE = os.path.join(_TMP, "state.json")
open(RUNS, "w").close()
json.dump({}, open(STATE, "w"))
SAVES = os.path.join(_TMP, "saves.jsonl")
# GATE_SAVES too: the scoreboard is evidence, and a suite that writes into the
# thing it is testing corrupts it. Same rule as GATE_RUNS and GATE_STATE above.
#
# GATE_SWITCHES was the hole left in that rule until 17-Sep-2026. The ledger, the
# state and the scoreboard were pointed at throwaway files, but the deliberate
# SWITCHES -- OVERRIDE, WIPE-OK, SHARED-OK -- were created and deleted LIVE, in
# the folder every session on the machine reads. So running these tests dropped a
# real 30-minute skip past Gates D and E for every open chat, and a real WIPE-OK
# past Gate A; and with 15+ chats open here, two overlapping runs made checks fail
# at RANDOM -- one run's live OVERRIDE waving through the push the other run was
# asserting must BLOCK. Five different checks went red across 8 runs, never the
# same one twice, every one of them failing OPEN. Now they are ours alone.
SWITCH = os.path.join(_TMP, "switches")
os.makedirs(SWITCH, exist_ok=True)
ENV = dict(os.environ, GATE_RUNS=RUNS, GATE_STATE=STATE, GATE_SAVES=SAVES,
           GATE_SWITCHES=SWITCH)

# The invariant that keeps the fix above from being quietly undone: the switch
# paths this suite writes must sit under OUR folder, never the live one. It is a
# static assertion on those two constants -- a tripwire against someone pointing
# them back, NOT a proof of the gate. The load-bearing red is "push with CFO
# OVERRIDE set", which goes red under the old gate because the test writes our
# folder and the old gate reads the live one. (Fable 5.1 flagged the first
# wording, "every switch this suite writes", as claiming more than it checks.)
#
# The first version of this check compared whether the live switch files EXISTED
# before and after. DeepSeek rejected it on review (17-Sep-2026) and was right:
# a real session setting or clearing its own override mid-run would have failed
# the check, so the guard against flakiness would itself have flaked — and it
# could not see an overwrite of a file that already existed. This asks the
# question that actually matters, and the answer does not depend on what any
# other session is doing.
LIVE_GATE = os.path.expanduser("~/.claude/gate")

def switches_are_ours(*paths):
    live = os.path.realpath(LIVE_GATE)
    return all(os.path.realpath(p) != live
               and not os.path.realpath(p).startswith(live + os.sep)
               for p in paths)

OV = os.path.join(SWITCH, "OVERRIDE")
SID = "TEST-SESSION"
PUSH = "g" + "it pu" + "sh origin feat/x"
DEPLOY = "python fabe/scripts/deploy_ssm.py"
DEVLOG = 'python C:/Users/PrathapAsus/.claude/scripts/devlog.py ask --key cfo-2026-09-11-x --text "t"'

fails = []

def run(mode, payload):
    p = subprocess.run([sys.executable, G, mode], input=json.dumps(payload),
                       capture_output=True, text=True, env=ENV)
    return p.returncode, (p.stderr or "").strip()

def pre(cmd):
    return run("pre", {"tool_name": "Bash", "session_id": SID, "tool_input": {"command": cmd}})

def stop():
    return run("stop", {"session_id": SID, "stop_hook_active": False})

def check(label, got, want, detail=""):
    ok = got == want
    if not ok:
        fails.append(label)
    print(f"{'OK ' if ok else 'BAD'} | {label:<48} -> {'BLOCK' if got == 2 else 'pass'}"
          + (f"  [{detail.splitlines()[0][:70]}]" if detail and got == 2 else ""))

def reset_state():
    """Clear the throwaway state file. It is ours alone, so there is nothing to
    preserve and no parallel session to damage."""
    json.dump({}, open(STATE, "w"))


def set_fable(verdict, age_h, sid=None):
    """Write a temporary last line in the Fable run log; returns the original bytes.

    The session_id is not decoration. Since 14-Sep-2026 a verdict only satisfies
    the gate for the session that earned it, or for the commit it reviewed — the
    gate used to accept ANY recent SHIP, so with several chats open one chat's
    approval unlocked every other chat's push. Pass sid="" to write the old
    anonymous shape and prove it no longer counts.
    """
    if sid is None:
        sid = SID
    orig = open(RUNS, "rb").read()
    ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - age_h * 3600))
    rec = {"ts": ts, "verdict": verdict, "note": "GATE TEST"}
    if sid:
        rec["session_id"] = sid
    with open(RUNS, "a", encoding="utf-8") as f:
        f.write("\n" + json.dumps(rec) + "\n")
    return orig

print("--- Build Log step (must come first) ---")
reset_state()
rc, err = pre(PUSH);   check("push with NO Build Log row", rc, 2, err)
rc, err = pre(DEPLOY); check("deploy with NO Build Log row", rc, 2, err)

print("\n--- Fable step (only reachable once the Build Log row exists) ---")
rc, _ = pre(DEVLOG);   check("devlog command itself always allowed", rc, 0)
orig = open(RUNS, "rb").read()
try:
    set_fable("SHIP", 40)
    rc, err = pre(PUSH); check("Build Log done, Fable SHIP but 40h STALE", rc, 2, err)
    open(RUNS, "wb").write(orig)

    set_fable("FIX", 0.1)
    rc, err = pre(PUSH); check("Build Log done, fresh Fable verdict = FIX", rc, 2, err)
    open(RUNS, "wb").write(orig)

    set_fable("SHIP", 0.1)
    rc, _ = pre(PUSH);   check("Build Log done + fresh Fable SHIP", rc, 0)

    print("\n--- Deploy step (session must not end on an undeployed push) ---")
    rc, err = stop();    check("end session after push, never deployed", rc, 2, err)
    rc, _ = pre(DEPLOY); check("deploy command allowed once gates pass", rc, 0)
    rc, _ = stop();      check("end session after push AND deploy", rc, 0)

    print("\n--- CFO override + no false positives ---")
    reset_state()
    open(OV, "w").close()
    rc, _ = pre(PUSH);   check("push with CFO OVERRIDE set", rc, 0)
    # ...and it came from OUR throwaway folder, not the one every other chat reads.
    check("this suite's OVERRIDE path is ours, not the live one",
          2 if switches_are_ours(OV, SWITCH) else 0, 2)
    os.remove(OV)
    reset_state()
    rc, _ = pre("ls -la");                 check("ordinary command untouched", rc, 0)
    # Reading a file that MENTIONS a gated phrase is not doing the gated thing.
    rc, _ = pre("grep -n devlog_deploy infra/host/deploy-zero-downtime.sh")
    check("grep of the deploy script (read, not run)", rc, 0)
    rc, _ = pre("cd repo && git grep -n 'g" + "it pu" + "sh'")
    check("git grep for a gated phrase", rc, 0)
    rc, _ = pre("cat infra/host/deploy-zero-downtime.sh")
    check("cat of the deploy script", rc, 0)
    rc, _ = pre('grep -n "deploy-zero-downtime\\|record_release" deploy_ssm.py | head')
    check("gated words inside a quoted search pattern", rc, 0)
    rc, _ = pre('echo "remember to g' + "it pu" + 'sh later"')
    check("gated words inside a quoted echo", rc, 0)
    rc, _ = pre("cd repo\necho hi\ngrep -n x deploy_ssm.py")
    check("multi-line read-only block", rc, 0)
    rc, _ = pre("cd repo\npython fabe/scripts/deploy_ssm.py")
    check("multi-line block that DOES deploy", rc, 2)
    rc, _ = pre("bash -n infra/host/deploy-zero-downtime.sh")
    check("bash -n syntax check (parse, not run)", rc, 0)
    rc, _ = pre("bash infra/host/deploy-zero-downtime.sh")
    check("bash without -n actually runs it", rc, 2)
    # The whole class that kept blocking real work: MENTIONED, not invoked.
    for label, c in [
        ("git checkout of the deploy script", "git checkout -- infra/host/deploy-zero-downtime.sh"),
        ("sed -i on the deploy script",        "sed -i s/a/b/ infra/host/deploy-zero-downtime.sh"),
        ("mv of the deploy script",            "mv infra/host/deploy-zero-downtime.sh /tmp/x"),
        ("git add the deploy script",          "git add infra/host/deploy-zero-downtime.sh"),
        ("git status",                         "git status --porcelain"),
    ]:
        rc, _ = pre(c)
        check(label, rc, 0)
    # ...and the invocations that must still block, however they are dressed up.
    for label, c in [
        ("sudo bash deploy script",   "sudo bash /opt/alpha-finance/infra/host/deploy-zero-downtime.sh"),
        ("env VAR=1 python deploy",   "env FOO=1 python fabe/scripts/deploy_ssm.py --confirm"),
        ("gh workflow run deploy",    "gh workflow run deploy.yml -f ref=main"),
        ("gh pr merge",               "gh pr merge 900 --auto --squash"),
    ]:
        rc, _ = pre(c)
        check(label, rc, 2)
    # ...but actually running it still blocks.
    rc, _ = pre("cd repo && bash infra/host/deploy-zero-downtime.sh")
    check("actually RUNNING the deploy script after cd", rc, 2)
    # A commit MESSAGE is not a command. On 16-Sep-2026 this exact line was
    # refused with "GATE E (Fable review) BLOCKED": the message wrapped so that
    # a body line began with `deploy-zero-downtime.sh`, classify() read that
    # word as the program, it is in DEPLOY_SCRIPTS, and the commit was gated as
    # a deploy. The message had to be reworded to get the work in. Same
    # mention-is-not-invocation class deploys_over_ssm() already handled one
    # level up with _outside_heredocs() -- classify() was reading the RAW line.
    rc, err = pre("git commit -q -F - <<'MSGEOF'\n"
                  "fix(gate): install-crons.sh IS a deploy\n"
                  "\n"
                  "The documented deploy is install-crons.sh, which syncs the\n"
                  "host copies from the repo. Running\n"
                  "deploy-zero-downtime.sh instead would recreate containers on\n"
                  "a live insurance system for no benefit.\n"
                  "MSGEOF")
    check("commit message whose wrapped line IS a deploy script", rc, 0, err)
    # ...and the behaviour that must SURVIVE the fix: a deploy script written by
    # heredoc and then RUN on a later line is still a deploy, because
    # _outside_heredocs() keeps every line outside the body.
    rc, _ = pre("cat > d.sh <<'SH'\necho hi\nSH\nbash infra/host/deploy-zero-downtime.sh")
    check("heredoc writes a script, a LATER line deploys", rc, 2)
    # ...but a heredoc body piped INTO a shell is EXECUTED, not written, so the
    # strip above must keep it. Fable 5.1 found this on review of the
    # commit-message fix (17-Sep-2026); none of the three external judges did.
    rc, _ = pre("bash <<'EOF'\nbash infra/host/deploy-zero-downtime.sh\nEOF")
    check("heredoc piped INTO bash is executed, not written", rc, 2)
    rc, _ = pre("sudo bash <<'EOF'\npython fabe/scripts/deploy_ssm.py --confirm\nEOF")
    check("sudo bash <<EOF carrying a deploy", rc, 2)
    # `-- arg` makes the next word a POSITIONAL for the body, not a program,
    # so the wrapper-peel stopped on it and the body went unread. DeepSeek
    # found this hole in the first cut of the fix above, 17-Sep-2026.
    rc, _ = pre("bash -s -- x <<'EOF'" + chr(10)
                + "bash infra/host/deploy-zero-downtime.sh" + chr(10) + "EOF")
    check("bash -s -- arg <<EOF is still the body running", rc, 2)
    # The prefix claim in _feeds_an_interpreter needed its own pin: OpenAI
    # predicted this exact bypass was untested, and it was -- assignments in
    # front of the interpreter got past BOTH passes. 17-Sep-2026.
    rc, _ = pre("env FOO=1 BAR=2 bash -s -- a <<'EOF'" + chr(10)
                + "bash infra/host/deploy-zero-downtime.sh" + chr(10) + "EOF")
    check("env VAR=1 in front of the interpreter", rc, 2)
    # Fable listed six more spellings a PEEL kept missing (17-Sep-2026), so the
    # check stopped peeling and started asking whether an interpreter appears
    # anywhere before the redirection. One pin each, because "handled" without a
    # pin is how the last three got through.
    for label, pre_words in [
        ("timeout N bash <<EOF",      "timeout 30 bash"),
        ("xargs bash <<EOF",          "xargs bash"),
        ("nice bash <<EOF",           "nice bash"),
        ("sudo -u user bash <<EOF",   "sudo -u ubuntu bash"),
        ("docker exec -i c bash <<EOF", "docker exec -i c bash"),
        ("wsl bash <<EOF",            "wsl bash"),
    ]:
        rc, _ = pre(pre_words + " <<'EOF'" + chr(10)
                    + "bash infra/host/deploy-zero-downtime.sh" + chr(10) + "EOF")
        check(label + " runs the body", rc, 2)
    # ...and the same prefix class one level DOWN, in the plain spelling. This is
    # the half Fable asked to close in the same change: the peel reported the
    # program as "30", so a real deploy was invisible with no heredoc involved.
    rc, _ = pre("timeout 30 bash infra/host/deploy-zero-downtime.sh")
    check("timeout N in front of a PLAIN deploy", rc, 2)
    rc, _ = pre("nice bash infra/host/deploy-zero-downtime.sh")
    check("nice in front of a PLAIN deploy", rc, 2)
    # ...without turning an ordinary read into a deploy.
    rc, _ = pre("timeout 30 grep -n x infra/host/deploy-zero-downtime.sh")
    check("timeout N in front of a READ is still a read", rc, 0)
    rc, _ = pre("xargs grep -n x")
    check("xargs in front of grep is still a read", rc, 0)
    # A SHIFT is not a heredoc. The _HEREDOC comment claimed 1<<2 was excluded
    # and it was not: it took "2" as the terminator and swallowed every later
    # line, so the deploy below scored as nothing. Fable 5.1, 17-Sep-2026.
    rc, _ = pre("echo $((1<<2))" + chr(10) + "bash infra/host/deploy-zero-downtime.sh")
    check("a shift does not swallow the deploy after it", rc, 2)
    rc, _ = pre("echo $((1<<2))" + chr(10) + "echo done")
    check("...and a shift on its own is still just arithmetic", rc, 0)
    # SPACED arithmetic too. The lookbehind fix closed $((x<<y)) and left
    # $(( x << y )) open, so the real rule is to strip ((...)) before looking
    # for a heredoc at all -- a heredoc operator cannot live inside it. Fable
    # 5.1 found the spaced sibling of the case DeepSeek had just found: one
    # spelling pinned, the whole class claimed.
    rc, _ = pre("echo $(( x << y ))" + chr(10) + "bash infra/host/deploy-zero-downtime.sh")
    check("a SPACED variable shift does not swallow it either", rc, 2)
    rc, _ = pre("(( x << y ))" + chr(10) + "bash infra/host/deploy-zero-downtime.sh")
    check("bare (( x << y )) does not swallow it either", rc, 2)
    # A duration can carry a unit. `30` was pinned and `10m` was not, which is
    # the same one-spelling-pinned mistake one level down.
    for d in ("10m", "5s", "1.5h"):
        rc, _ = pre("timeout " + d + " bash infra/host/deploy-zero-downtime.sh")
        check("timeout " + d + " in front of a PLAIN deploy", rc, 2)
    # A shift with VARIABLE operands too. The first fix only required the
    # terminator to start with a letter, which closed 1<<2 and left $((x<<y))
    # wide open, because y IS a letter. DeepSeek caught the half-fix the same
    # day; the real rule is the character in FRONT of the operator.
    rc, _ = pre("echo $((x<<y))" + chr(10) + "bash infra/host/deploy-zero-downtime.sh")
    check("a VARIABLE shift does not swallow the deploy either", rc, 2)
    # The interpreter list is a closed list, which DeepSeek rightly flagged as
    # the gate's recurring mistake. It stays positive on purpose (see the note
    # on _INTERPRETERS), so the least it can do is be pinned for the ones it
    # claims -- an unlisted interpreter is a KNOWN gap, not a surprise.
    for name in ("php", "lua", "deno", "bun", "pwsh", "perl", "ruby", "node"):
        rc, _ = pre(name + " <<'EOF'" + chr(10)
                    + "bash infra/host/deploy-zero-downtime.sh" + chr(10) + "EOF")
        check(name + " <<EOF runs the body too", rc, 2)
    # ...and an interpreter handed a SCRIPT FILE is being fed it, so the
    # heredoc is only that script's stdin -- data, not commands.
    rc, _ = pre("bash run.sh <<'EOF'" + chr(10)
                + "bash infra/host/deploy-zero-downtime.sh" + chr(10) + "EOF")
    check("bash run.sh <<EOF feeds the script, not the shell", rc, 0)
    # ...and the two shapes that must stay UNAFFECTED, because their body is DATA:
    # `cat`/`tee` write a file, and `git commit -F -` reads a message.
    rc, _ = pre("cat > x.sh <<'SH'\nbash infra/host/deploy-zero-downtime.sh\nSH")
    check("cat >file <<SH still writes, not deploys", rc, 0)
    # Windows spells them with .exe, and the gate could not see through it:
    # `python.exe` is not in WRAPPERS, so the peel stopped there and never
    # reached the deploy script behind it. A deploy this gate cannot see is
    # one the Build Log reports as never happening. Fable 5.1, 17-Sep-2026.
    rc, _ = pre("python.exe fabe/scripts/deploy_ssm.py --confirm")
    check("python.exe in front of a deploy script", rc, 2)
    rc, _ = pre("C:/Python313/python.exe fabe/scripts/deploy_ssm.py")
    check("a full .exe path in front of a deploy script", rc, 2)
    # A BACKSLASH path too, and this one has to pass on the Mac as well: there
    # os.path is posixpath, which would return the whole string from basename
    # and see no program at all. DeepSeek flagged the platform split, so _prog
    # folds the separator before splitting. (chr(92) keeps the fixture readable
    # without an escape thicket.)
    rc, _ = pre("C:" + chr(92) + "Python313" + chr(92) + "python.exe"
                + " fabe/scripts/deploy_ssm.py")
    check("a BACKSLASH .exe path in front of a deploy", rc, 2)
    rc, _ = pre("bash.exe <<'EOF'" + chr(10)
                + "bash infra/host/deploy-zero-downtime.sh" + chr(10) + "EOF")
    check("bash.exe <<EOF runs the body too", rc, 2)
    # ...and .exe must not turn a READ into a deploy.
    rc, _ = pre("python.exe -c pass")
    check("python.exe doing something harmless is untouched", rc, 0)
    rc, _ = pre("grep.exe -n x infra/host/deploy-zero-downtime.sh")
    check("grep.exe of the deploy script is still a read", rc, 0)
    rc, _ = pre("python manage.py test");  check("running tests untouched", rc, 0)

    # The Windows deploy path (11-Sep-2026). The stop hook told the CFO "never
    # deployed" minutes after a deploy it had watched go past: on this machine
    # there is no SSH, so the deploy travels inside a quoted JSON payload to
    # `aws ssm send-command`, and quoted text is deliberately ignored. Every
    # session on this PC had recorded deployed=null for that reason.
    rc, _ = pre('CMD="cd /opt/alpha-finance && sudo bash infra/host/'
                'deploy-zero-downtime.sh"; aws ssm send-command --instance-ids i-0 '
                '--document-name AWS-RunShellScript --parameters "$P"')
    check("SSM send-command carrying the deploy script", rc, 2)
    # ...and the narrowness that makes it safe: reading is still reading.
    for label, c in [
        ("grep of the deploy script",   "grep -n build infra/host/deploy-zero-downtime.sh"),
        ("SSM call that is not a deploy",
         'aws ssm send-command --instance-ids i-0 --document-name AWS-RunShellScript '
         '--parameters "$(python -c pass)"'),
    ]:
        rc, _ = pre(c)
        check(label, rc, 0)
    # A QUOTED PATH to the deploy script (11-Sep-2026). The stop hook nagged
    # "never deployed" immediately after two real deploys: the earlier version
    # deleted quoted TEXT, so `python "$HOME/.../deploy_ssm.py" --confirm`
    # reduced to `python --confirm` and the deploy was invisible. A gate that
    # cannot see a real deploy cannot see a real skip either.
    for label, c in [
        ("deploy script at a quoted path",
         'python "$HOME/.claude/skills/fabe/scripts/deploy_ssm.py" --backend --confirm'),
        ("deploy script at a single-quoted path",
         "bash '/opt/alpha-finance/infra/host/deploy-zero-downtime.sh'"),
    ]:
        rc, _ = pre(c)
        check(label, rc, 2)
    # ...and keeping the text must not resurrect the mention/invoke confusion.
    for label, c in [
        ("grep for a quoted deploy script", 'grep -rn "deploy_ssm.py" .'),
        ("echo naming a quoted deploy script", 'echo "run deploy_ssm.py later"'),
        ("git add a quoted path", 'git add "infra/host/deploy-zero-downtime.sh"'),
    ]:
        rc, _ = pre(c)
        check(label, rc, 0)
    # PrintOps (16-Sep-2026). GATE F told the CFO "pushed but never deployed"
    # TWICE straight after a real, verified deploy, and the session had to be
    # overridden by hand (OVERRIDE-fe56e579 records why). PrintOps is a
    # different repo on a different box and ships by plain `docker compose
    # build` / `up -d` over SSM, so no deploy-script name ever appears on the
    # line -- and the send-command itself lives inside a scratchpad helper, so
    # it is not on the line either. Real files, because the fix has to OPEN
    # them: a string-only test cannot tell a helper that sends from one that
    # merely mentions sending.
    PO = tempfile.mkdtemp(prefix="gatetest-printops-")
    open(os.path.join(PO, "ssm.sh"), "w").write(
        'set -u\nINST=i-05297188826961e72\nPF="$SP/ssm-params.json"\n'
        'CID=$(aws ssm send-command --region af-south-1 --instance-ids $INST '
        '--document-name AWS-RunShellScript --parameters "file://$PF" '
        '--query Command.CommandId --output text) || exit 1\n')
    open(os.path.join(PO, "deploy.sh"), "w").write(
        'set -e\ncd /opt/printops\nsudo git fetch origin --quiet\n'
        'sudo docker compose -f docker-compose.yml -f printops-deploy/'
        'docker-compose.caddy.yml build frontend 2>&1 | tail -3\n'
        'sudo docker compose -f docker-compose.yml -f printops-deploy/'
        'docker-compose.caddy.yml up -d 2>&1 | tail -4\n')
    open(os.path.join(PO, "check.sh"), "w").write(
        'cd /opt/printops\nsudo git log --oneline -1\n'
        'pgrep -c -f "docker compose" || echo "no compose process"\n')
    PSP = 'SP="%s"\n' % PO.replace(chr(92), "/")
    rc, _ = pre(PSP + 'bash "$SP/ssm.sh" "$SP/deploy.sh" 2>&1 | tail -45')
    check("PrintOps docker-compose deploy sent through the SSM helper", rc, 2)
    INLINE = ('aws ssm send-command --instance-ids i-05297188826961e72 '
              '--document-name AWS-RunShellScript --parameters '
              '"commands=[cd /opt/printops && docker compose up -d]"')
    rc, _ = pre(INLINE)
    check("SSM send-command carrying docker compose up -d inline", rc, 2)
    # The payload is usually a FILE, so a substring match on the line sees nothing.
    # `build frontend`, not `build backend`: devlog's EveryBackendBuildIsStamped
    # scans all of infra/ for anything that builds the BACKEND image without
    # --build-arg GIT_SHA, and it cannot tell a fixture string from a real build
    # path. It is right to be that broad -- prod once reported its commit as
    # "unknown" for the whole life of the drift alarm -- so the fixture moves,
    # not the guard. Which service is named makes no difference to what is
    # tested here.
    json.dump({"commands": ["cd /opt/printops\ndocker compose build frontend"]},
              open(os.path.join(PO, "params.json"), "w"))
    json.dump({"commands": ["docker ps"]},
              open(os.path.join(PO, "look.json"), "w"))
    SEND = ('aws ssm send-command --instance-ids i-0 --document-name '
            'AWS-RunShellScript --parameters file://%s')
    rc, _ = pre(SEND % os.path.join(PO, "params.json").replace(chr(92), "/"))
    check("SSM file:// payload that deploys", rc, 2)
    # ...and the narrowness that keeps it safe. Every one of these was a real
    # line from the same session, minutes either side of the deploy.
    rc, _ = pre(SEND % os.path.join(PO, "look.json").replace(chr(92), "/"))
    check("SSM file:// payload that only looks", rc, 0)
    rc, _ = pre(PSP + 'bash "$SP/ssm.sh" "$SP/check.sh" 2>&1 | tail -15')
    check("same helper, a look (pgrep docker compose) not a deploy", rc, 0)
    rc, _ = pre(PSP + 'grep -n "docker compose" "$SP/deploy.sh"')
    check("grep of the PrintOps deploy script", rc, 0)
    rc, _ = pre(PSP + 'cat "$SP/ssm.sh"')
    check("cat of the SSM helper itself", rc, 0)
    rc, _ = pre("docker compose -f docker-compose.yml build frontend")
    check("docker compose build on THIS machine (not a deploy)", rc, 0)
    rc, _ = pre("docker compose up -d")
    check("docker compose up -d on THIS machine (not a deploy)", rc, 0)
    # Writing a script that deploys is not deploying: this suite's own fixture
    # above was refused until the gate learned to ignore heredoc BODIES.
    rc, _ = pre("cat > x.sh <<'SH'\n" + INLINE + "\nSH\necho written")
    check("heredoc that CONTAINS a deploy (written, not run)", rc, 0)
    # A herestring is not a heredoc: `cat <<< x` must not swallow the rest of
    # the call and hide the send that follows it (Fable 5.1, 16-Sep-2026).
    rc, _ = pre('cat <<< "checking"\n' + INLINE)
    check("send-command AFTER a herestring is still seen", rc, 2)
    shutil.rmtree(PO, ignore_errors=True)

    # The morning briefs (16-Sep-2026, PR #1144). GATE F told the session
    # "pushed but never deployed" after a deploy that was verified md5
    # host-vs-repo on all three files, proven live by a dry run of the brief on
    # prod showing all three diary columns populated, and stamped Live in the
    # Build Log by record-release.sh. Nothing under infra/ceo-monitor/ ships
    # inside the backend image -- run_cfo.sh pipes the HOST copies at
    # /opt/ceo-monitor/ into manage.py shell -- so its correct deploy is
    # install-crons.sh, and deploy-zero-downtime.sh would have recreated
    # containers on a live insurance system for nothing.
    # Every line below is the real line from that session, verbatim.
    BRIEF_DEPLOY = (
        'aws ssm send-command --profile claude-cli --region af-south-1 '
        '--instance-ids i-02a5d76a61f4f09a5 --document-name AWS-RunShellScript '
        '--parameters \'commands=["echo \\"=== 1. reflog BEFORE (who deployed '
        'last) ===\\"","sudo -u ubuntu git -C /opt/alpha-finance reflog '
        '--date=iso -3","echo \\"=== 2. drift BEFORE ===\\"","cd '
        '/opt/alpha-finance && sudo bash infra/install-crons.sh --check 2>&1 | '
        'grep -E \\"DRIFT|ceo-monitor\\" || echo \\"(no ceo-monitor drift '
        'reported)\\"","echo \\"=== 3. pull ===\\"","sudo -u ubuntu git -C '
        '/opt/alpha-finance fetch origin main -q && sudo -u ubuntu git -C '
        '/opt/alpha-finance reset --hard origin/main","echo \\"=== 5. sync the '
        'host copies ===\\"","cd /opt/alpha-finance && sudo bash '
        'infra/install-crons.sh 2>&1 | grep -E \\"SYNC|FAIL|DRIFT\\" || echo '
        '\\"(nothing to sync)\\"","echo \\"=== 6. drift AFTER (must be clean) '
        '===\\"","cd /opt/alpha-finance && sudo bash infra/install-crons.sh '
        '--check 2>&1 | grep -E \\"DRIFT\\" && echo \\"DRIFT REMAINS - STOP\\" '
        '|| echo \\"NO DRIFT\\""]\' --query "Command.CommandId" --output text')
    rc, _ = pre(BRIEF_DEPLOY)
    check("install-crons.sh host-script deploy over SSM", rc, 2)
    # record-release.sh is the strongest signal there is: it is the only writer
    # of live_at in the Build Log, so a session that caused it to run has
    # demonstrably deployed -- by any route, including one this file has never
    # been taught.
    RECORD_RELEASE = (
        'aws ssm send-command --profile claude-cli --region af-south-1 '
        '--instance-ids i-02a5d76a61f4f09a5 --document-name AWS-RunShellScript '
        '--parameters \'commands=["cd /opt/alpha-finance && sudo bash '
        'infra/host/record-release.sh 2>&1 | tail -8"]\' --query '
        '"Command.CommandId" --output text 2>&1 | tail -1')
    rc, _ = pre(RECORD_RELEASE)
    check("record-release.sh stamping the Build Log Live", rc, 2)
    # ...and the narrowness. --check reports drift and changes nothing, so a
    # session that only probed has not deployed -- and BOTH spellings rode the
    # payload above, seconds apart, so excluding it cannot be done by name.
    DRIFT_ONLY = (
        'aws ssm send-command --profile claude-cli --region af-south-1 '
        '--instance-ids i-02a5d76a61f4f09a5 --document-name AWS-RunShellScript '
        '--parameters \'commands=["cd /opt/alpha-finance && sudo bash '
        'infra/install-crons.sh --check 2>&1 | grep -E \\"DRIFT\\" && echo '
        '\\"DRIFT REMAINS - STOP\\" || echo \\"NO DRIFT\\""]\'')
    rc, _ = pre(DRIFT_ONLY)
    check("install-crons.sh --check only (a drift probe, not a deploy)", rc, 0)
    # Fable 5.1's catch, and it is the shape infra/ceo-monitor/README.md tells
    # people to type: sync, then check for drift. As two JSON array elements
    # there is no SHELL separator between them, only `","` -- so a lookahead
    # that stops at ; | & alone reads the second element's --check and cancels
    # the first element's real deploy. The 16-Sep payload escaped this only
    # because a `2>&1 |` happened to fall in between.
    README_SHAPE = (
        'aws ssm send-command --instance-ids i-02a5d76a61f4f09a5 '
        '--document-name AWS-RunShellScript --parameters '
        '\'commands=["sudo bash infra/install-crons.sh","sudo bash '
        'infra/install-crons.sh --check"]\'')
    rc, _ = pre(README_SHAPE)
    check("README shape: bare install-crons.sh, then --check as the NEXT command",
          rc, 2)
    for label, c in [
        ("SSM grep of install-crons.sh on the box",
         'aws ssm send-command --instance-ids i-0 --document-name '
         'AWS-RunShellScript --parameters \'commands=["grep -n ceo-monitor '
         '/opt/alpha-finance/infra/install-crons.sh"]\''),
        ("SSM cat of record-release.sh",
         'aws ssm send-command --instance-ids i-0 --document-name '
         'AWS-RunShellScript --parameters \'commands=["cat '
         '/opt/alpha-finance/infra/host/record-release.sh"]\''),
        # It ran on the box or it did not happen: installing crons on THIS
        # machine deploys nothing, exactly like docker compose build above.
        ("install-crons.sh run on this machine", "sudo bash infra/install-crons.sh"),
        ("grep of install-crons.sh", 'grep -n "ceo-monitor" infra/install-crons.sh'),
        ("git add of install-crons.sh", "git add infra/install-crons.sh"),
        # DeepSeek's catch on review: an unanchored `sh\s+` matches the TAIL of
        # any word ending in those two letters, so a note about the deploy
        # scored as the deploy.
        ("a sentence ENDING in sh before the script name",
         'aws ssm send-command --instance-ids i-0 --document-name '
         'AWS-RunShellScript --parameters \'commands=["echo \\"to finish '
         'install-crons.sh must run\\""]\''),
    ]:
        rc, _ = pre(c)
        check(label, rc, 0)

    rc, _ = run("pre", {"tool_name": "Read", "session_id": SID,
                        "tool_input": {"file_path": "x"}})
    check("non-Bash tool untouched", rc, 0)
finally:
    open(RUNS, "wb").write(orig)
    reset_state()


# ---------------------------------------------------------------------------
# Step 0 — one session must not wipe another's work.
#
# Added 11-Sep-2026 after a session reported "I just destroyed another session's
# staged work with that reset". The rest of this gate runs at push/deploy time;
# a `git reset` ships nothing, so it fell straight through unguarded. The repo
# was already carrying stashes named "stale working tree changes from other
# sessions" — this had been happening for days.
#
# Uses a THROWAWAY repo it dirties itself, via GATE_SHARED_REPO. A test that
# relied on the live checkout happening to be dirty would quietly stop testing
# anything the day somebody tidied up.
# ---------------------------------------------------------------------------
import shutil
import tempfile


def _repo(dirty):
    d = tempfile.mkdtemp(prefix="gatetest-")
    q = dict(cwd=d, capture_output=True, text=True)
    subprocess.run(["git", "init", "-q"], **q)
    with open(os.path.join(d, "kept.txt"), "w") as f:
        f.write("committed\n")
    subprocess.run(["git", "add", "-A"], **q)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", "base"], **q)
    if dirty:
        with open(os.path.join(d, "someone_elses_work.py"), "w") as f:
            f.write("half finished\n")
    return d


def pre_in(cmd, cwd, shared):
    env = dict(ENV, GATE_SHARED_REPO=shared)
    p = subprocess.run(
        [sys.executable, G, "pre"],
        input=json.dumps({"tool_name": "Bash", "session_id": SID,
                          "cwd": cwd, "tool_input": {"command": cmd}}),
        capture_output=True, text=True, env=env)
    return p.returncode, (p.stderr or "").strip()


# ---------------------------------------------------------------------------
# GATE E — A VERDICT THAT NAMES THE COMMIT DOES NOT AGE OUT (17-Sep-2026)
# ---------------------------------------------------------------------------
# PR #1157 was refused with "no SHIP verdict in the last 3h covers this code, so
# nothing here has been reviewed". The SHIP was sitting in the ledger against
# 7b0e74f2b37cc994538879718767b13cff3a9f27 — the HEAD of that branch, unchanged
# by a byte since the review. What had expired was the wall clock: four CI shards
# at ~9 minutes each, then a queue, put the merge about 3h after the review. The
# session wrote an OVERRIDE to merge green, reviewed work. Fourth false alarm
# from this gate in a week, and a guard that is wrong right after real work stops
# being read.
#
# These checks pin BOTH halves, because the fix is only worth having if the rule
# it relaxes still holds everywhere else:
#   - a stale verdict that NAMES this commit is honoured (the fix), and
#   - a stale verdict for a DIFFERENT commit, a stale ANONYMOUS one, and a stale
#     one from this chat that pins no commit, all still BLOCK (the rule).
# The merge is driven through `gh pr merge` in a repo that ships on push to main,
# which is the shape the incident had, not a stand-in push.

DEPLOY_WORKFLOW = "\n".join([
    "on:",
    "  push:",
    "    branches: [main]",
    "jobs:",
    "  ship:",
    "    steps:",
    "      - run: docker push registry/app:latest",
    "",
])


def _autodeploy_repo():
    """A throwaway repo where merging to main IS the deploy, so `gh pr merge`
    reaches Gate E at all. Without it the "allowed" checks below would be passing
    on an ungated command and proving nothing — see [[f-a-test-that-cannot-fail]].
    The control check immediately below is what holds them honest."""
    d = _repo(dirty=False)
    wf = os.path.join(d, ".github", "workflows")
    os.makedirs(wf, exist_ok=True)
    with open(os.path.join(wf, "deploy.yml"), "w") as f:
        f.write(DEPLOY_WORKFLOW)
    q = dict(cwd=d, capture_output=True, text=True)
    subprocess.run(["git", "add", "-A"], **q)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", "ships on merge"], **q)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d,
                         capture_output=True, text=True).stdout.strip()
    return d, sha


def set_fable_sha(verdict, age_h, sha, sid=""):
    """A ledger row in the shape /fabe really writes: it records the commit it
    reviewed and, as measured on 14-Sep-2026, usually no session id at all."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                       time.gmtime(time.time() - age_h * 3600))
    rec = {"ts": ts, "verdict": verdict, "note": "GATE TEST",
           "diff_hash": sha, "head_sha": sha}
    if sid:
        rec["session_id"] = sid
    with open(RUNS, "a", encoding="utf-8") as f:
        f.write("\n" + json.dumps(rec) + "\n")


print("\n--- Gate E: a verdict that NAMES the commit does not age out ---")
MERGE = "gh pr merge 1157 --squash --delete-branch"
OTHER_SHA = "9" * 40
_e_orig = open(RUNS, "rb").read()
_E_REPO, _E_SHA = _autodeploy_repo()
# The shared checkout is somewhere else entirely, so HEAD is readable here — a
# HEAD read INSIDE the shared checkout identifies nothing and the gate blanks it.
_E_ELSEWHERE = tempfile.mkdtemp(prefix="gatetest-shared-")
try:
    # Start from an empty ledger: rows written by the blocks above must not be
    # what decides these checks.
    open(RUNS, "w").close()
    reset_state()
    pre(DEVLOG)                  # Gate D comes first, or Gate E is never reached

    # Control. A FRESH SHIP from another chat on another commit is exactly what
    # the 14-Sep session-scoping closed, and it also proves this command really
    # is gated — every "allowed" check below is worthless without it.
    set_fable_sha("SHIP", 0.1, OTHER_SHA)
    rc, err = pre_in(MERGE, _E_REPO, _E_ELSEWHERE)
    check("control: fresh SHIP for ANOTHER commit -> merge blocked", rc, 2, err)
    open(RUNS, "wb").write(b"")

    # THE FIX. The same row, 3.4h old, naming the commit being merged.
    set_fable_sha("SHIP", 3.4, _E_SHA)
    rc, err = pre_in(MERGE, _E_REPO, _E_ELSEWHERE)
    check("stale SHIP that NAMES this commit -> merge allowed", rc, 0, err)
    rc, err = pre_in(PUSH, _E_REPO, _E_ELSEWHERE)
    check("...and the same verdict covers a push of it", rc, 0, err)
    open(RUNS, "wb").write(b"")

    # Far past the window. A hash match is exact, so hours do not enter into it.
    set_fable_sha("SHIP", 40, _E_SHA)
    rc, err = pre_in(MERGE, _E_REPO, _E_ELSEWHERE)
    check("40h-old SHIP naming this commit -> still allowed", rc, 0, err)
    open(RUNS, "wb").write(b"")

    # ---- the staleness rule itself, which must NOT be widened away ----
    set_fable_sha("SHIP", 3.4, OTHER_SHA)
    rc, err = pre_in(MERGE, _E_REPO, _E_ELSEWHERE)
    check("stale SHIP for a DIFFERENT commit still blocks", rc, 2, err)
    open(RUNS, "wb").write(b"")

    set_fable("SHIP", 3.4, sid="")
    rc, err = pre_in(MERGE, _E_REPO, _E_ELSEWHERE)
    check("stale ANONYMOUS SHIP still blocks", rc, 2, err)
    open(RUNS, "wb").write(b"")

    # A chat is not a commit: it can keep editing after its own review, so a
    # session match alone does not pin the code and does not earn the exemption.
    set_fable("SHIP", 40, sid=SID)
    rc, err = pre_in(MERGE, _E_REPO, _E_ELSEWHERE)
    check("stale SHIP from THIS chat pinning no commit still blocks", rc, 2, err)
    open(RUNS, "wb").write(b"")

    # F2 must survive the exemption: newest-first, the first row that belongs to
    # me decides. A superseded approval is not an approval, at any age.
    set_fable_sha("SHIP", 5.0, _E_SHA)
    set_fable_sha("FIX", 4.0, _E_SHA)
    rc, err = pre_in(MERGE, _E_REPO, _E_ELSEWHERE)
    check("older SHIP superseded by a later FIX on the SAME commit blocks", rc, 2, err)
    open(RUNS, "wb").write(b"")

    # Pinning cuts both ways: a stale FIX that names this commit is mine, and it
    # is not SHIP, so it must block rather than fall through the age filter.
    set_fable_sha("FIX", 40, _E_SHA)
    rc, err = pre_in(MERGE, _E_REPO, _E_ELSEWHERE)
    check("stale FIX naming this commit blocks (pinned cuts both ways)", rc, 2, err)
finally:
    open(RUNS, "wb").write(_e_orig)
    reset_state()
    shutil.rmtree(_E_REPO, ignore_errors=True)
    shutil.rmtree(_E_ELSEWHERE, ignore_errors=True)


# Satisfy steps 1 and 2 first, so only step 0 can possibly block below.
reset_state()
set_fable("SHIP", 0.1)
pre(DEVLOG)

DIRTY = _repo(dirty=True)
CLEAN = _repo(dirty=False)
MINE = _repo(dirty=True)          # a session's OWN worktree — never guarded
try:
    for _cmd, _label in [
        ("git reset --hard origin/main", "reset --hard over someone's work"),
        ("git clean -fd", "clean -fd over someone's work"),
        ("git checkout .", "checkout . over someone's work"),
        ("git checkout -f main", "checkout --force over someone's work"),
        ("git switch --discard-changes main", "switch --discard-changes"),
        ("git stash clear", "stash clear over someone's work"),
    ]:
        rc, err = pre_in(_cmd, DIRTY, DIRTY)
        check(_label, rc, 2, err)

    rc, err = pre_in("cd %s && git reset --hard" % DIRTY, MINE, DIRTY)
    check("cd INTO the shared checkout, then reset", rc, 2, err)

    # A refusal that does not say what is at risk is not actionable.
    check("the refusal names the file at risk",
          2 if "someone_elses_work.py" in err else 0, 2)

    rc, _ = pre_in("git reset --hard origin/main", MINE, DIRTY)
    check("same reset in your OWN worktree", rc, 0)

    # ---- SUPERSEDED 14-Sep-2026: the shared checkout is now READ-ONLY ----
    # These five used to pass, and passing was right while the folder was a
    # place you could work in. It no longer is: the wipe guard only caught the
    # WHOLESALE destroyers, which left every session free to BUILD there - and
    # that is how the work a reset later destroys comes to be sitting in a
    # shared folder at all. Writing is now refused whether it is targeted or
    # not; the refusal hands over the worktree command instead.
    # A targeted undo is not lost, it just happens in your own copy.
    rc, err = pre_in("git reset --hard origin/main", CLEAN, CLEAN)
    check("reset in the shared checkout, even when clean", rc, 2, err)
    check("...and the refusal offers the worktree way out",
          2 if "worktree add" in err else 0, 2)

    rc, err = pre_in("git checkout -- one/file.py", DIRTY, DIRTY)
    check("targeted undo in the SHARED checkout now refused", rc, 2, err)

    rc, _ = pre_in("git checkout -- one/file.py", MINE, DIRTY)
    check("targeted undo in your OWN worktree still allowed", rc, 0)

    rc, _ = pre_in("git stash push -u -m parking", DIRTY, DIRTY)
    check("parking work safely still allowed", rc, 0)

    rc, _ = pre_in("grep -rn reset docs/", DIRTY, DIRTY)
    check("merely MENTIONING the command", rc, 0)

    rc, _ = pre_in("git status", DIRTY, DIRTY)
    check("plain status", rc, 0)

    # --- the four holes Fable proved by running the live file, 11-Sep-2026 ---
    # Each of these was ALLOWED before the second round and would have let the
    # reported wipe happen anyway.
    rc, err = pre_in("git -C %s reset --hard" % DIRTY, MINE, DIRTY)
    check("git -C <shared> named from another folder", rc, 2, err)

    _posix = re.sub(r"^([A-Za-z]):", lambda m: "/" + m.group(1).lower(),
                    DIRTY.replace("\\", "/"))
    rc, err = pre_in("cd %s && git reset --hard" % _posix, MINE, DIRTY)
    check("cd with a Git-Bash /c/ style path", rc, 2, err)

    rc, err = pre_in("git restore .", DIRTY, DIRTY)
    check("git restore . (modern spelling of checkout .)", rc, 2, err)

    rc, err = pre_in("git checkout HEAD -- .", DIRTY, DIRTY)
    check("git checkout HEAD -- .", rc, 2, err)

    rc, err = pre_in("git restore --staged .", DIRTY, DIRTY)
    check("git restore --staged . writes the index -> refused", rc, 2, err)
    rc, err = pre_in("git restore one/file.py", DIRTY, DIRTY)
    check("git restore ONE file in the shared checkout", rc, 2, err)
    rc, err = pre_in("git checkout HEAD -- one/file.py", DIRTY, DIRTY)
    check("git checkout HEAD -- ONE file in the shared checkout", rc, 2, err)
    rc, _ = pre_in("git clean -n", DIRTY, DIRTY)
    check("git clean --dry-run", rc, 0)
    rc, _ = pre_in("git -C %s reset --hard" % MINE, DIRTY, DIRTY)
    check("git -C MY OWN worktree", rc, 0)

    # The wipe guard must NOT ride on the general OVERRIDE. That token is a
    # 30-minute blanket a session touches to get past /fabe; honouring it here
    # switched this guard off machine-wide for a reason that has nothing to do
    # with destroying work — and one was warm at 22:23 the day this was written.
    open(OV, "w").close()
    rc, err = pre_in("git reset --hard origin/main", DIRTY, DIRTY)
    check("Fable OVERRIDE does NOT license a wipe", rc, 2, err)
    os.remove(OV)

    # --- second round of holes Fable proved, 12-Sep-2026 ---
    # Running from a SUBDIRECTORY is the commonest shape there is, and it wipes
    # the whole repo just the same. Equality on the path let every one of these by.
    os.makedirs(os.path.join(DIRTY, "backend"), exist_ok=True)
    rc, err = pre_in("git reset --hard", os.path.join(DIRTY, "backend"), DIRTY)
    check("reset run from a SUBDIRECTORY of the shared repo", rc, 2, err)
    rc, err = pre_in("git -C backend reset --hard", DIRTY, DIRTY)
    check("git -C a subdirectory of the shared repo", rc, 2, err)

    # "." is only the first spelling. A session refused on `.` reaches for `./`.
    for _cmd in ("git checkout -- ./", "git restore ./",
                 "git checkout -- :/.", "git restore -- *"):
        rc, err = pre_in(_cmd, DIRTY, DIRTY)
        check("whole-tree respelling: %s" % _cmd, rc, 2, err)

    # Only the FIRST cd used to count.
    rc, err = pre_in("cd %s && cd %s && git reset --hard" % (MINE, DIRTY), MINE, DIRTY)
    check("two cd hops ending in the shared repo", rc, 2, err)

    # The repo copy is the source of truth for BOTH machines; the installed copy
    # at ~/.claude/gate is what actually runs. Two copies that can drift apart is
    # the exact problem that cost two days over the skills folder, so say so out
    # loud the moment they differ, on whichever machine notices first.
    # 🔴 THIS CHECK HAD NEVER RUN ONCE (found 14-Sep-2026). It read the WORKING
    # COPY at ~/work/alpha-finance/infra/gate/gate.py, and that folder holds only
    # README.md on this machine — the other three files are in origin/main but
    # were never checked out here. `os.path.exists(...)` was therefore False every
    # single time, so the check quietly skipped and reported nothing, while the
    # installed gate drifted 120 lines ahead of the repo copy. Exactly the
    # "a test that cannot fail" class: a guard whose precondition is never met is
    # not a lenient guard, it is an absent one.
    # So: read the repo copy from origin/main, and FAIL LOUDLY when it cannot be
    # read instead of skipping.
    _live_gate = os.path.expanduser("~/.claude/gate/gate.py")
    _repo = os.path.expanduser("~/work/alpha-finance")

    def _norm(b):
        # Compare as TEXT. git normalises .py to LF in the repo while this PC
        # keeps CRLF on disk, so a byte comparison would scream "drift" on two
        # identical files, for ever — the exact trap that cost a day on the
        # skills sync earlier the same night.
        return b.replace(b"\r\n", b"\n")

    _repo_bytes = None
    try:
        _p = subprocess.run(["git", "-C", _repo, "show", "origin/main:infra/gate/gate.py"],
                            capture_output=True, timeout=20)
        if _p.returncode == 0 and _p.stdout.strip():
            _repo_bytes = _p.stdout
    except Exception:
        pass
    if _repo_bytes is None:
        check("repo copy of the gate is READABLE (never skip silently)", 0, 2)
    else:
        _same = _norm(_repo_bytes) == _norm(open(_live_gate, "rb").read())
        check("installed gate matches the repo copy (no drift)", 2 if _same else 0, 2)

    WOK = os.path.join(SWITCH, "WIPE-OK")
    open(WOK, "w").close()
    rc, _ = pre_in("git reset --hard origin/main", DIRTY, DIRTY)
    check("WIPE-OK, its own deliberate switch, allows it", rc, 0)
    check("...and WIPE-OK was ours too, not the machine-wide one", 2 if
          switches_are_ours(WOK) else 0, 2)
    os.remove(WOK)
finally:
    for _d in (DIRTY, CLEAN, MINE):
        shutil.rmtree(_d, ignore_errors=True)
    # Leave nothing behind. This block stamps the test session as "Build Log done"
    # and can drop an OVERRIDE; both would make the NEXT run's first tests pass for
    # the wrong reason. Observed once already — five earlier checks went red purely
    # because a previous run had left that stamp sitting there.
    if os.path.exists(OV):
        os.remove(OV)
    reset_state()

print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)

shutil.rmtree(_TMP, ignore_errors=True)
