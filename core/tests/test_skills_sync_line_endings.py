"""`same_text` in infra/skills-sync.sh must ignore line endings and nothing else.

WHY THIS TEST EXISTS
--------------------
The Windows PC writes CRLF; the Mac writes LF. The sync script originally
compared skill files byte-exact, so every file whose only difference was
invisible characters read as "changed on BOTH machines". Measured 11-Sep-2026
against the real shared tree: 237 of 469 files are stored CRLF, and the Mac's
first pull stopped on 173 conflicts and copied nothing.

The noise was not the real damage. Byte-exactness would have started a
line-ending war with no end: the PC pushes CRLF, the Mac pulls and stores CRLF,
the Mac's next edit saves LF and pushes it back, the PC pulls LF, forever — two
machines fighting over characters nobody can see, every file permanently
"changed", neither ever reaching a settled state.

So this is a guard, not a unit test for its own sake. Revert `same_text` to a
plain `cmp -s` and `test_crlf_and_lf_are_the_same_skill` goes red immediately.
Loosen it the other way — compare only the first line, strip all whitespace —
and the three "real change" cases go red. Both halves are load-bearing.

It reads the function out of the real script rather than copying it, so the test
cannot drift away from the code it is protecting.
"""

import os
import shlex
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "infra", "skills-sync.sh")
# Resolve bash once: a bare 'bash' can hit a wrapper on PATH that drops arguments.
BASH = shutil.which("bash")


def _extract_same_text() -> str:
    """Pull the same_text function out of the live script."""
    with open(SCRIPT, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("same_text()"))
    end = next(i for i, ln in enumerate(lines[start:], start) if ln == "}")
    return "\n".join(lines[start : end + 1])


@unittest.skipIf(BASH is None, "bash not available")
class SkillsSyncLineEndingsTests(unittest.TestCase):
    """Every case below is one the two machines actually hit."""

    def setUp(self):
        self.assertTrue(os.path.exists(SCRIPT), "infra/skills-sync.sh is missing")
        self.fn = _extract_same_text()
        self.dir = tempfile.mkdtemp(prefix="sksynctest")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _write(self, name: str, data: bytes) -> str:
        path = os.path.join(self.dir, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    @staticmethod
    def _bash_path(path: str) -> str:
        # Git Bash cannot open a Windows path written with backslashes; forward
        # slashes work on both platforms. Without this every case here fails as
        # "unreadable" and the test lies about the code rather than checking it.
        return path.replace("\\", "/")

    def _same(self, a: str, b: str) -> bool:
        """Run the REAL same_text against two files; True when it says 'same'."""
        # The two paths are quoted INTO the script. They cannot be passed as
        # arguments after `bash -c`, nor through the environment: on the Windows
        # PC neither survives the hop, so the function ran against two empty
        # strings and every case here reported "differs" — the test lied about
        # the code instead of checking it. Verified: `echo $#` prints 0.
        script = (
            "set -euo pipefail\n"
            + self.fn
            + "\nif same_text {} {}; then echo SAME; else echo DIFFERS; fi\n".format(
                shlex.quote(self._bash_path(a)), shlex.quote(self._bash_path(b))
            )
        )
        out = subprocess.run(
            [BASH, "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(out.returncode, 0, f"probe failed: {out.stderr}")
        return out.stdout.strip() == "SAME"

    # --- the whole point: line endings are not content ---

    def test_crlf_and_lf_are_the_same_skill(self):
        # Revert same_text to `cmp -s` and THIS is the assertion that fails.
        win = self._write("win.md", b"# Skill\r\nDo the thing.\r\n")
        mac = self._write("mac.md", b"# Skill\nDo the thing.\n")
        self.assertTrue(
            self._same(win, mac),
            "CRLF vs LF with identical words must not read as a conflict",
        )

    def test_identical_bytes_are_the_same_skill(self):
        a = self._write("a.md", b"# Skill\r\nDo the thing.\r\n")
        b = self._write("b.md", b"# Skill\r\nDo the thing.\r\n")
        self.assertTrue(self._same(a, b))

    # --- and it must still catch every real edit ---

    def test_a_real_edit_is_still_a_difference(self):
        old = self._write("old.md", b"# Skill\r\nDo the thing.\r\n")
        new = self._write("new.md", b"# Skill\r\nDo the OTHER thing.\r\n")
        self.assertFalse(
            self._same(old, new),
            "a real content change must never be skipped as unchanged",
        )

    def test_a_real_edit_across_platforms_is_still_a_difference(self):
        win = self._write("win2.md", b"# Skill\r\nDo the thing.\r\n")
        mac = self._write("mac2.md", b"# Skill\nDo the OTHER thing.\n")
        self.assertFalse(self._same(win, mac))

    def test_an_added_line_is_a_difference_even_across_platforms(self):
        win = self._write("win3.md", b"alpha\r\nbeta\r\n")
        mac = self._write("mac3.md", b"alpha\nbeta\ngamma\n")
        self.assertFalse(self._same(win, mac))

    # --- absent and unreadable are NOT "the same" ---

    def test_a_missing_file_is_a_difference(self):
        have = self._write("have.md", b"alpha\r\n")
        self.assertFalse(
            self._same(have, os.path.join(self.dir, "nope.md")),
            "a file the other side does not have must never read as identical",
        )

    def test_a_directory_is_a_difference(self):
        have = self._write("have2.md", b"alpha\r\n")
        sub = os.path.join(self.dir, "adir")
        os.mkdir(sub)
        self.assertFalse(self._same(have, sub))

    def test_two_empty_files_are_the_same(self):
        a = self._write("e1.md", b"")
        b = self._write("e2.md", b"")
        self.assertTrue(self._same(a, b))


class SkillsSyncGuardsStillPresentTests(unittest.TestCase):
    """Three one-line guards, each of which cost a real failure to find."""

    def setUp(self):
        self.assertTrue(os.path.exists(SCRIPT), "infra/skills-sync.sh is missing")
        with open(SCRIPT, encoding="utf-8") as fh:
            self.src = fh.read()

    def test_file_size_filter_is_in_bytes(self):
        # `-size -1M` matches NOTHING: GNU find rounds every file up to whole
        # megabytes, so "< 1M" is never true and the first run synced 2 files.
        self.assertIn("-size -524288c", self.src)
        # Only the live command lines count: the note beside it names `-size -1M`
        # to explain why it is wrong, and a flat search would trip over that.
        code = [ln for ln in self.src.splitlines() if not ln.lstrip().startswith("#")]
        self.assertNotIn("-size -1M", "\n".join(code))

    def test_mktemp_templates_keep_their_XXXXXX(self):
        # `mktemp -d -t NAME` dies on Windows Git Bash ("too few X's in
        # template"). Without the suffix this script cannot run on the PC at all.
        for line in self.src.splitlines():
            if "mktemp -d -t " in line:
                self.assertIn("XXXXXX", line, f"mktemp without XXXXXX: {line.strip()}")

    def test_generated_qc_output_is_not_synced(self):
        # Regenerated by every QC run, so syncing it makes a conflict near-certain
        # on every push. 10 copies exist across the QC output directories.
        self.assertIn("! -name 'qc-report.json'", self.src)


class GitAttributesKeepsSkillsByteExactTests(unittest.TestCase):
    """`infra/skills/** -text` is load-bearing, not tidiness."""

    def test_skills_are_excluded_from_line_ending_normalisation(self):
        # autocrlf is on for the Windows PC, so without this git hands CRLF back
        # for anything it treats as text: a pushed LF file returns as CRLF, the
        # push-verification fails for files that ARE on the server, and pull
        # rewrites every skill on every run.
        path = os.path.join(REPO, ".gitattributes")
        with open(path, encoding="utf-8") as fh:
            self.assertIn("infra/skills/** -text", fh.read())
