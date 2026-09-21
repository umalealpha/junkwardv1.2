import tempfile
from datetime import date
from pathlib import Path

from django.test import SimpleTestCase
from django.utils import timezone

from watchdog.machine_talk import hot_modules


class MachineTalkTests(SimpleTestCase):
    def _write(self, text) -> Path:
        f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        f.write(text)
        f.close()
        return Path(f.name)

    def test_todays_lines_map_to_modules(self):
        today = timezone.localdate()
        stamp = today.strftime("%Y-%m-%d")
        p = self._write(
            f"- **{stamp} 10:00** Fixed a CLAIM settlement bug and ran payroll.\n"
            f"- **2020-01-01 09:00** Old note about leases (ignored).\n")
        hot = hot_modules(today, path=p)
        self.assertIn("claims", hot)
        self.assertIn("payroll", hot)

    def test_old_lines_are_ignored(self):
        today = timezone.localdate()
        p = self._write("- **2020-01-01 09:00** lease change and a claim.\n")
        self.assertEqual(hot_modules(today, path=p), set())

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(hot_modules(date(2026, 1, 1), path=Path("/no/such/file.md")), set())
