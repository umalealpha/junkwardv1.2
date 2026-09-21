"""The Fable learning loop must work on a Windows console, not only on the Mac.

WHY THIS TEST EXISTS
--------------------
`infra/skills/fabe/scripts/checklist.py` is how a lesson Fable proposes reaches
the CFO and then the permanent checklist. On 17-Sep-2026 it was found to be
broken on the Windows PC in two separate ways, and both were invisible:

  1. It read and wrote its two reference files with the platform default codec.
     Those files are UTF-8 and full of em dashes, so on Windows (cp1252) every
     command that touched the APPROVED file died with
     `UnicodeDecodeError: 0x9d`. `pending` alone survived, because it never
     reads that file -- which is exactly why nobody noticed: the one command
     anyone ran by hand was the one that worked.

  2. Worse, `propose` wrote the entry and THEN died printing its own success
     message, which contained a "->" arrow as U+2192. A crash after the write
     reads as a failure, so the next attempt files the SAME lesson again. L19
     sits in BOTH the pending queue and the approved section, and by the time
     anyone looked, L50, L51, L62, L63 and L64 were all taken too.

The measured cost: 28 lessons queued on that machine and none approvable, while
17 already-approved entries showed the loop working fine from the Mac.

WHAT IS LOAD-BEARING
--------------------
Both halves. Remove `encoding="utf-8"` from `read()` and
`test_reads_an_approved_file_that_is_not_cp1252` goes red. Put a non-ASCII
character back into any printed message and
`test_every_console_message_survives_a_cp1252_console` goes red -- that one runs
the real script in a subprocess with the console forced to cp1252, because the
bug only ever appeared at the moment of printing.

The test drives the REAL script through a temporary copy of its own directory
layout, so it cannot drift away from the file it is protecting, and it never
touches the live queue -- a suite that writes into the thing it is testing was
its own separate incident the same day (PR #1174).
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "infra", "skills", "fabe", "scripts", "checklist.py")

# The em dash and the arrow that broke it. Kept as escapes so this file stays
# readable and so nothing normalises them away.
EM_DASH = "—"
ARROW = "→"


def _sandbox():
    """A throwaway copy of the script's own layout: scripts/ beside reference/.

    checklist.py finds its files with `Path(__file__).parent.parent/"reference"`,
    so the layout IS the fixture.
    """
    root = tempfile.mkdtemp(prefix="checklist-enc-")
    os.makedirs(os.path.join(root, "scripts"))
    os.makedirs(os.path.join(root, "reference"))
    shutil.copy(SCRIPT, os.path.join(root, "scripts", "checklist.py"))
    # Both reference files carry an em dash, exactly as the real ones do.
    with open(os.path.join(root, "reference", "omni-graphite-mistakes.md"),
              "w", encoding="utf-8") as fh:
        fh.write(f"# Common mistakes {EM_DASH} the approved list\n\n"
                 "## LEARNED (CFO-approved, auto-grown)\n\n"
                 f"### L2 {EM_DASH} an already approved lesson\n"
                 "**Severity:** HIGH.\nbody\n")
    with open(os.path.join(root, "reference", "pending-checklist.md"),
              "w", encoding="utf-8") as fh:
        fh.write(f"# Pending checklist entries {EM_DASH} awaiting CFO "
                 "one-line approval\n\n"
                 f"### L7 {EM_DASH} a queued lesson\n"
                 "**Severity:** HIGH.\nbody\n")
    return root


def _run(root, *args, cp1252=False):
    """Run the real script. With cp1252=True the console is the Windows one.

    PYTHONIOENCODING is how the crash is reproduced on any machine, including
    the Mac -- otherwise this test would pass there for the wrong reason and the
    bug would stay live on the only machine that has it.
    """
    env = dict(os.environ)
    env.pop("PYTHONWARNINGS", None)
    if cp1252:
        env["PYTHONIOENCODING"] = "cp1252"
    # check=False is deliberate and explicit: a non-zero exit is the SUBJECT of
    # several of these tests (the id-clash path exits 1 on purpose), so raising
    # here would turn an assertion into a crash.
    return subprocess.run(
        [sys.executable, os.path.join(root, "scripts", "checklist.py")] + list(args),
        capture_output=True, text=True, errors="replace", env=env, check=False)


class ChecklistEncoding(unittest.TestCase):
    def setUp(self):
        self.root = _sandbox()
        self.addCleanup(shutil.rmtree, self.root, True)

    def _pending(self):
        with open(os.path.join(self.root, "reference", "pending-checklist.md"),
                  encoding="utf-8") as fh:
            return fh.read()

    def _approved(self):
        with open(os.path.join(self.root, "reference", "omni-graphite-mistakes.md"),
                  encoding="utf-8") as fh:
            return fh.read()

    def test_reads_an_approved_file_that_is_not_cp1252(self):
        """`propose` checks the APPROVED file for a clashing id, and used to die there."""
        p = _run(self.root, "propose", "--id", "L99", "--severity", "HIGH",
                 "--title", "a new lesson", "--detail", "why it matters")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("### L99", self._pending())

    def test_every_console_message_survives_a_cp1252_console(self):
        """The bug that filed lessons twice: the WRITE worked, the PRINT killed it.

        Forcing cp1252 is the whole test, and the character matters. An em dash
        IS representable in cp1252 (0x97), so it never crashed and is not the
        thing to look for; `pending` legitimately prints the queue file, em
        dashes and all. The killer was U+2192, the arrow in the success line,
        which cp1252 has no byte for. The first version of this test asserted
        "no em dash in stdout" and failed on `pending` printing its own data --
        a test that would have forced the file content to be mangled to stay
        green.

        So the contract is: every command exits 0 with nothing raised, and the
        arrow is gone from the two messages that carried it. ASCII-only messages
        as a rule are held by test_the_real_script_prints_only_ascii below,
        statically, where file data cannot confuse the question.
        """
        for args in (
            ("propose", "--id", "L98", "--severity", "HIGH", "--title", "t",
             "--detail", "d"),
            ("pending",),
            ("approve", "L98"),
            ("reject", "L7"),
        ):
            with self.subTest(cmd=args[0]):
                p = _run(self.root, *args, cp1252=True)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertNotIn("UnicodeEncodeError", p.stderr)
                self.assertNotIn(ARROW, p.stdout,
                                 "a console message still carries U+2192")

    def test_an_id_clash_is_reported_not_crashed(self):
        """The clash message is the one path that writes to stderr."""
        p = _run(self.root, "propose", "--id", "L2", "--severity", "HIGH",
                 "--title", "t", "--detail", "d", cp1252=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("already exists", p.stderr)
        self.assertNotIn("UnicodeEncodeError", p.stderr)

    def test_approve_moves_the_entry_and_keeps_the_em_dashes(self):
        """Approving must not mangle the UTF-8 already in either file."""
        p = _run(self.root, "approve", "L7", cp1252=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        approved = self._approved()
        self.assertIn("### L7", approved)
        self.assertIn(EM_DASH, approved, "an em dash was lost or mangled")
        self.assertNotIn("### L7", self._pending())
        # The previously approved entry is still there -- approve rewrites the
        # whole file, so a bad codec here would have truncated or corrupted it.
        self.assertIn("### L2", approved)

    def test_the_real_script_prints_only_ascii(self):
        """A static guard, so a new message cannot reintroduce the crash.

        Cheaper and broader than exercising every path: it reads the shipped
        file and refuses any non-ASCII inside a print() or sys.exit().
        """
        with open(SCRIPT, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        offenders = [
            ln for ln in lines
            if re.search(r"\b(print|sys\.exit)\(", ln)
            and any(ord(c) > 127 for c in ln)
        ]
        self.assertEqual(offenders, [],
                         "console output must be ASCII; a Windows console is cp1252")
