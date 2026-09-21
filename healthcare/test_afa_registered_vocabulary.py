"""Bug 186a514c (raised by the ADH team, 14-Sep-2026): the ADH provider registry
reported 11 AFA-registered providers when the team's sheet held 222.

`afa_registered` derived from `contract_status` with an exact `== "signed"`
test. Live prod data on 14-Sep held contract_status 'YES' x210, 'Signed' x11,
'AFA Signing' x1, 'NO' x69 — so 210 providers whose sheet said YES were read as
NOT registered. The CFO ruled on 14-Sep that YES in that column means registered.

Revert the fix in healthcare/models.py and `test_yes_counts_as_registered`
goes red.
"""
from django.test import SimpleTestCase

from .models import ServiceProvider


def _p(status):
    return ServiceProvider(practice_number="1", name="X", contract_status=status)


class AfaRegisteredVocabularyTests(SimpleTestCase):
    def test_yes_counts_as_registered(self):
        """The live sheet's own word. This is the bug."""
        for raw in ["YES", "yes", "Yes", " YES "]:
            self.assertEqual(_p(raw).afa_registered, "Yes", raw)

    def test_signed_still_counts_as_registered(self):
        """The 11 rows that already worked must keep working."""
        for raw in ["Signed", "signed", " SIGNED "]:
            self.assertEqual(_p(raw).afa_registered, "Yes", raw)

    def test_afa_signing_is_pending_not_registered(self):
        """'AFA Signing' contains neither YES nor a bare 'signed', but it must
        not fall through to registered either — it is still in flight."""
        for raw in ["AFA Signing", "afa signing", "Signing", "Pending"]:
            self.assertEqual(_p(raw).afa_registered, "Pending", raw)

    def test_no_and_blank_are_not_registered(self):
        for raw in ["NO", "no", "", "   ", "Declined"]:
            self.assertEqual(_p(raw).afa_registered, "No", repr(raw))

    def test_live_prod_mix_gives_222_registered_or_pending(self):
        """The exact 14-Sep-2026 prod distribution: 210 YES + 11 Signed +
        1 AFA Signing + 69 NO. Registered must be 221 and pending 1 — together
        the 222 the ADH team counted, instead of the 11 the tile showed."""
        rows = ["YES"] * 210 + ["Signed"] * 11 + ["AFA Signing"] * 1 + ["NO"] * 69
        verdicts = [_p(r).afa_registered for r in rows]
        self.assertEqual(verdicts.count("Yes"), 221)
        self.assertEqual(verdicts.count("Pending"), 1)
        self.assertEqual(verdicts.count("Yes") + verdicts.count("Pending"), 222)
        self.assertEqual(verdicts.count("No"), 69)
