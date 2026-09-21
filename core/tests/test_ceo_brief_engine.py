"""CEO daily-brief engine — the two faults the CFO reported on 11-Sep-2026.

1. A courtesy "Thanks boss" sitting on top of a quoted payment request was read
   as the request itself and printed as a HIGH customer complaint, recommending
   Wangu phone a customer. Only what the sender wrote today is news.
2. With a single matter, "The One Thing Today" box and the card below it were
   word-for-word the same — the CEO read the same issue twice.

The engine lives in infra/ceo-monitor/ (a host-cron script, not an app), so it
is loaded by path. No LLM and no network here on purpose: the classifier's own
output is the INPUT, because the guard has to hold whatever the model says.

Run: python manage.py test core.tests.test_ceo_brief_engine
"""
import re
import importlib.util
from pathlib import Path

from django.test import SimpleTestCase

_ENGINE = (Path(__file__).resolve().parents[2]
           / "infra" / "ceo-monitor" / "ceo_engine.py")


def _load_engine():
    spec = importlib.util.spec_from_file_location("ceo_engine_under_test", _ENGINE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


E = _load_engine()

#: Exactly the shape Outlook hands us: two words of new text sitting on top of
#: the whole quoted request, amounts and all.
_REPLY = (
    "Payment Authorisation Request for Current Account & Claims Payments"
    " -- Thanks boss\n"
    "\n"
    "From: Prathap Ganesharajah <pganesharajah@alphadirect.co.bw>\n"
    "Sent: 10 September 2026 16:42\n"
    "To: Arun P. Iyer\n"
    "Subject: Payment Authorisation Request\n"
    "\n"
    "Dear Arun, please authorise the attached payment run for current account\n"
    "and claims payments totalling BWP 1,809,400 covering 68 claim settlements."
)


def _item(**kw):
    """The real 11-Sep item: the CFO thanking the CEO for approving a run."""
    defaults = dict(
        id=1,
        title="Payment Authorisation Request for Current Account & Claims "
              "Payments dated 10 September 2026",
        from_addr="pganesharajah@alphadirect.co.bw",
        category="complaint",          # what DeepSeek actually labelled it
        summary=_REPLY,
        confirmed=True,
    )
    defaults.update(kw)
    it = E.QueueItem(**defaults)
    it.severity = "High"
    it.party = "Payment Authorisation Request"
    it.polished_matter = defaults["title"]
    it.polished_why = ("It is a courtesy acknowledgement of an already-approved "
                       "payment run with no new amount, dispute or escalation.")
    return it


class QuotedReplyChainTests(SimpleTestCase):
    def test_new_text_keeps_only_what_the_sender_wrote(self):
        self.assertNotIn("1,809,400", E.new_text(_REPLY))
        self.assertIn("Thanks boss", E.new_text(_REPLY))

    def test_a_pure_forward_is_not_emptied(self):
        fwd = "From: someone@example.test\nThe insured disputes BWP 900,000."
        self.assertIn("900,000", E.new_text(fwd))


class CourtesyAcknowledgementTests(SimpleTestCase):
    def test_thanks_boss_over_a_quoted_request_is_dropped(self):
        """The fault exactly as it reached the CEO on 11-Sep-2026."""
        it = _item()
        E.extract_facts(it)
        E.score_item(it)
        self.assertEqual(it.amount_bwp, 0.0,
                         "an amount inside the quote is not a new amount")
        self.assertEqual(it.score, 0, "a thank-you must score nothing")
        self.assertEqual(it.severity, "Watch")
        # Only matters scoring 40+ are printed, so this never reaches the CEO.
        self.assertLess(it.score, 40)

    def test_thanks_with_a_real_complaint_still_counts(self):
        it = _item(summary=(
            "Thanks, but the client is still not happy and has asked for the "
            "Ombudsman's details.\n\nFrom: someone@example.test\nquoted history"))
        E.extract_facts(it)
        E.score_item(it)
        self.assertGreaterEqual(it.score, 40, "a real complaint must survive")

    def test_thanks_followed_by_an_ask_still_counts(self):
        it = _item(summary="Thanks. Please confirm the NBFIRA return today.")
        E.extract_facts(it)
        E.score_item(it)
        self.assertGreaterEqual(it.score, 40)

    def test_a_long_mail_that_merely_opens_with_thanks_is_not_an_ack(self):
        body = ("Thank you for your email. " + "The insured disputes the "
                "settlement figure and has instructed attorneys. " * 3)
        it = _item(summary=body)
        self.assertFalse(E.is_courtesy_ack(it))


class OneThingDuplicationTests(SimpleTestCase):
    def _digest(self, n):
        d = E.Digest(date="Friday, 11 September 2026", raw_count=n, distinct_count=n)
        for i in range(n):
            it = _item(id=i, summary="The insured disputes BWP 750,000 and has "
                                     "instructed attorneys.")
            it.title = f"Matter {i}"
            it.polished_matter = f"Matter {i}"
            E.extract_facts(it)
            E.score_item(it)
            E.recommend(it)
            d.items.append(it)
        return d

    def test_the_top_story_is_printed_exactly_once(self):
        """Counting beats presence/absence (CFO reported this TWICE).

        The 11-Sep guard asserted the phrase was ABSENT with a single matter.
        That said nothing about two matters - and two matters is exactly the
        case that came back on 16-Sep: the box returned AND the same story
        still printed as its own card underneath. Count it instead. Once is
        right; twice is the bug he reported; never is an over-correction that
        loses the signal.
        """
        for n in (1, 2, 3):
            html = E.render_html(self._digest(n))
            self.assertEqual(
                html.count("The One Thing Today"), 1,
                f"{n} matter(s): the badge must appear exactly once")
            self.assertEqual(
                html.count("Matter 0"), 1,
                f"{n} matter(s): the top story itself must be rendered once")

    def test_rendering_does_not_scribble_on_the_callers_digest(self):
        """render_html used to set is_one_thing on the caller's item, so a
        second render of the same digest carried state from the first."""
        d = self._digest(3)
        before = [getattr(i, "is_one_thing", False) for i in d.items]
        first = E.render_html(d)
        after = [getattr(i, "is_one_thing", False) for i in d.items]
        self.assertEqual(before, after, "render_html mutated the digest")
        self.assertEqual(first, E.render_html(d), "rendering is not repeatable")

    def test_every_matter_still_gets_its_own_card(self):
        html = E.render_html(self._digest(3))
        for i in range(3):
            self.assertEqual(html.count(f"Matter {i}"), 1)


class StrategyPaperRoutingTests(SimpleTestCase):
    """A competitor paper is not a customer complaint (CFO, 16-Sep-2026).

    The CFO's own competitor analysis reached the CEO labelled CUSTOMER
    COMPLAINTS and recommending the CLAIMS MANAGER "call the customer today;
    expedite the claim" - the reputational playbook, word for word, on a
    strategy paper she can do nothing about.
    """

    #: Deliberately carries NO company money figure. An earlier draft copied the
    #: revenue line straight out of the brief and tripped the frozen-number gate -
    #: a fixture has no business restating an audited figure it does not need.
    _PAPER = ("An internal competitor analysis against eight competitors using "
              "signed results shows our combined ratio at 100.0% and most of the "
              "book ceded to reinsurers against a much lower market median, "
              "while a same-size peer earned a far better underwriting profit.")

    def _paper(self):
        it = _item(id=7, title="Competitor analysis - signed 2025 results",
                   summary=self._PAPER, category="complaint")
        it.polished_matter = self._PAPER
        E.extract_facts(it)
        E.score_item(it)
        E.recommend(it)
        return it

    def test_the_claims_manager_is_not_asked_to_ring_a_competitor(self):
        it = self._paper()
        self.assertEqual(it.at_stake, "strategy")
        self.assertNotEqual(it.rec_owner_label, "Wangu")
        self.assertEqual(it.rec_owner_label, "Prathap")

    def test_no_canned_action_is_attached_to_a_strategy_paper(self):
        self.assertEqual(self._paper().rec_text, "")

    def test_it_is_filed_under_performance_not_customer_complaints(self):
        d = E.Digest(date="Wednesday, 16 September 2026", raw_count=1,
                     distinct_count=1)
        d.items.append(self._paper())
        html = E.render_html(d)
        self.assertIn("Business Performance", html)
        self.assertNotIn("Customer Complaints", html)
        self.assertNotIn("Call the customer today", html)

    def _paper_from(self, addr):
        it = _item(id=9, from_addr=addr,
                   title="Competitor analysis - signed results",
                   summary=self._PAPER, category="complaint")
        it.polished_matter = self._PAPER
        E.extract_facts(it)
        E.score_item(it)
        E.recommend(it)
        return it

    def test_a_lookalike_address_is_refused(self):
        """Checklist L6 (CFO 29-Jul-2026): an identity check needs a positive
        match, never a substring, with a test proving unknown input is REFUSED.
        Every address below CONTAINS the CFO's username and none of them is him.
        """
        for spoof in ("pganesharajah@gmail.com",
                      "notpganesharajah@alphadirect.co.bw",
                      "pganesharajah@alphadirect.co.bw.attacker.net",
                      "pganesharajah@insurance.co.bw"):
            with self.subTest(spoof=spoof):
                self.assertFalse(E.is_cfo(spoof), spoof)
                self.assertNotEqual(
                    self._paper_from(spoof).at_stake, "strategy",
                    f"{spoof} was treated as the CFO")

    def test_both_of_the_cfo_logins_are_accepted(self):
        """cfo@ and pganesharajah@ are the same person, two logins."""
        for real in ("pganesharajah@alphadirect.co.bw",
                     "CFO@alphadirect.co.bw",
                     "Prathap Ganesharajah <pganesharajah@alphadirect.co.bw>"):
            with self.subTest(real=real):
                self.assertTrue(E.is_cfo(real), real)
                self.assertEqual(self._paper_from(real).at_stake, "strategy")

    def test_an_empty_or_missing_sender_is_refused(self):
        for blank in ("", None, "   "):
            self.assertFalse(E.is_cfo(blank), repr(blank))

    def test_a_forwarded_regulator_letter_outranks_the_strategy_bucket(self):
        """NBFIRA prudential correspondence routinely says "loss ratio" and
        "cession". Without this guard the CFO forwarding one filed it under
        Business Performance and capped it at High - demoting a regulator
        matter that should read Critical."""
        it = _item(id=11, from_addr="pganesharajah@alphadirect.co.bw",
                   title="FW: NBFIRA prudential return query",
                   summary="NBFIRA have written about our loss ratio and the "
                           "level of business ceded; they want a response.",
                   category="regulatory")
        it.polished_matter = it.title
        E.extract_facts(it)
        E.score_item(it)
        E.recommend(it)
        self.assertEqual(it.at_stake, "regulatory")
        self.assertNotEqual(it.rec_text, "")

    def test_a_forwarded_legal_demand_outranks_the_strategy_bucket(self):
        it = _item(id=12, from_addr="pganesharajah@alphadirect.co.bw",
                   title="FW: letter of demand",
                   summary="Attorneys have sent a letter of demand; it touches "
                           "our combined ratio for the year.",
                   category="legal")
        it.polished_matter = it.title
        E.extract_facts(it)
        E.score_item(it)
        E.recommend(it)
        self.assertEqual(it.at_stake, "legal")

    def test_a_real_complaint_still_reaches_the_claims_manager(self):
        """The carve-out must not swallow the case it sits next to."""
        it = _item(id=8, from_addr="anguished.customer@example.com",
                   title="I am not happy with how my claim was handled",
                   summary="I am not happy. My claim has been open for six "
                           "weeks and nobody has called me back.",
                   category="complaint")
        it.polished_matter = it.title
        E.extract_facts(it)
        E.score_item(it)
        E.recommend(it)
        self.assertNotEqual(it.at_stake, "strategy")
        self.assertEqual(it.rec_owner_label, "Wangu")

class QuietWeekClaimsTests(SimpleTestCase):
    """A quiet week must say so, not pad the brief with a P15 key loss."""

    #: render_money_lens returns "" unless at least one number tile is present,
    #: so a lens with no tiles would silently take the claims block with it and
    #: the assertions below would pass for the wrong reason.
    _TILE = {"label": "Premium collected", "value": "P8,810,988",
             "sub": "14991 receipts", "delta": -58.0, "ok": True}

    def _digest(self):
        return E.Digest(date="Wednesday, 16 September 2026", raw_count=0,
                        distinct_count=0)

    def _lens(self, claims, floor=200000):
        return {"period": "01 SEP TO 16 SEP", "basis": "vs 01 AUG TO 16 AUG",
                "tiles": [self._TILE], "claims_floor": floor,
                "large_claims": claims}

    def test_no_qualifying_claims_says_so_in_words(self):
        html = E.render_html(self._digest(), lens=self._lens([]))
        self.assertIn("P8,810,988", html, "the money lens must have rendered")
        self.assertIn("No claims above P200,000", html)

    def test_the_heading_names_the_floor(self):
        html = E.render_html(self._digest(), lens=self._lens(
            [{"claim": "G2026009999", "policy": "P1", "type": "Motor",
              "status": "Open", "description": "Collision",
              "registered": "2026-09-14", "reserve": "P450,000",
              "paid": "P0"}]))
        self.assertIn("CLAIMS ABOVE P200,000", html)
        self.assertNotIn("3 LARGEST NEW CLAIMS", html)
        self.assertIn("G2026009999", html)

    def test_a_failed_lookup_never_claims_there_were_no_claims(self):
        """large_claims=None means the Graphite read failed. Saying "No claims
        above P200,000" there would state as fact something nothing proved -
        the CFO's standing rule, and the exact way a broken feed reads as good
        news to a CEO."""
        lens = self._lens([])
        lens["large_claims"] = None
        html = E.render_html(self._digest(), lens=lens)
        self.assertNotIn("No claims above", html)
        self.assertIn("no fresh data", html)

    def test_a_missing_floor_is_not_rendered_as_zero(self):
        """An older backend sending no floor must not print "above P0"."""
        lens = self._lens([])
        del lens["claims_floor"]
        html = E.render_html(self._digest(), lens=lens)
        self.assertNotIn("P0", html)
        self.assertIn("no fresh data", html)

    def test_the_wording_follows_the_floor_and_cannot_drift(self):
        """The number in the copy is derived, not a second hardcoded copy."""
        html = E.render_html(self._digest(),
                             lens=self._lens([], floor=500000))
        self.assertIn("No claims above P500,000", html)
        self.assertNotIn("P200,000", html)


class MeetingsColumnsTests(SimpleTestCase):
    """Whose day the brief is allowed to make a statement about.

    The CFO brief renders three diary columns (You / Arun / Arjun) but its
    driver only ever fetched ONE calendar, so Arun and Arjun printed
    "0 today" and "No meetings" every morning. Nobody had asked Microsoft
    about their day; the brief simply said they were free.

    "Asked and the day is empty" and "never asked" must not look the same.
    """

    @staticmethod
    def _meetings(today, pending=None, counts=None, today_counts=None):
        return {"today": today, "pending": pending or {},
                "today_counts": today_counts or {},
                "tomorrow_counts": counts or {}, "tomorrow_headline": "",
                "quip": ""}

    _COLUMNS = [("You", "CFO"), ("Arun", "CEO"), ("Arjun", "COO")]

    @staticmethod
    def _column(html, name):
        """Just the one diary column, so an assertion can never pass or fail
        on what a neighbouring column happens to say."""
        return html.split(name + " <span", 1)[1].split("</table>", 1)[0]

    def test_a_calendar_that_was_never_read_is_not_an_empty_day(self):
        html = E.render_meetings(
            self._meetings({"You": [{"time": "09:00", "title": "Board",
                                     "ext": False}]}),
            columns=self._COLUMNS)
        for absent in ("Arun", "Arjun"):
            col = self._column(html, absent)
            # the literal, not just the constant: stash the fix and the
            # constant disappears, so a constant-only assertion goes red on
            # AttributeError and proves nothing about the behaviour.
            self.assertIn("Calendar not shared", col)
            self.assertIn(E.UNAVAILABLE_NOT_READ, col)
            self.assertNotIn("No meetings", col)
            # ...and no headline count either, or the column reads
            # "Arun (CEO) 0 today" directly above the sentence saying we do
            # not know what his day holds.
            self.assertNotIn("today", col)

    def test_a_day_that_really_is_empty_still_says_no_meetings(self):
        """The fix must not swallow a genuine free day."""
        html = E.render_meetings(
            self._meetings({"You": [], "Arun": [], "Arjun": []}),
            columns=self._COLUMNS)
        self.assertIn("No meetings", html)
        self.assertNotIn(E.UNAVAILABLE_NOT_READ, html)

    def test_a_refusal_outside_column_one_still_says_still_enabling(self):
        """The pending test used to be pinned to the middle column.

        A 403 on the third column printed "still enabling" next to a real
        count of zero — the note and the number contradicted each other.
        """
        html = E.render_meetings(
            self._meetings({"You": [], "Arun": [], "Arjun": []},
                           pending={"Arjun": True}),
            columns=self._COLUMNS)
        col = self._column(html, "Arjun")
        self.assertIn(E.UNAVAILABLE_PENDING, col)
        self.assertNotIn("today", col)
        # the columns that WERE read keep their honest zero
        self.assertIn("0 today", self._column(html, "Arun"))

    def test_a_nine_meeting_day_says_nine_and_still_shows_six(self):
        """The header count is a claim about the person's day, not about the
        column. It used to be len(rows), and the drivers cap rows at six, so
        every busy day read "6 today" — which is the cap, not the diary. Seen
        on prod 16-Sep-2026: the CFO brief showed exactly "6 today" for both
        the CFO and the CEO.
        """
        rows = [{"time": "%02d:00" % h, "title": "Meeting %d" % h,
                 "ext": False} for h in range(8, 17)]
        html = E.render_meetings(
            self._meetings({"You": rows[:6]}, today_counts={"You": len(rows)}),
            columns=self._COLUMNS)
        col = self._column(html, "You")
        self.assertIn("9 today", col)
        self.assertNotIn("6 today", col)
        # the cap on what is DISPLAYED stays — the panel is a column, not a list
        self.assertEqual(col.count("Meeting"), 6)

    def test_an_older_driver_without_the_count_still_renders_its_rows(self):
        """install-crons.sh syncs /opt/ceo-monitor file by file, so the new
        engine can land beside a driver that has not. No key means count the
        rows, exactly as before — no worse, and never a crash."""
        html = E.render_meetings(
            {"today": {"You": [{"time": "09:00", "title": "Board",
                                "ext": False}]},
             "pending": {}, "tomorrow_counts": {}, "tomorrow_headline": "",
             "quip": ""},
            columns=self._COLUMNS)
        self.assertIn("1 today", self._column(html, "You"))

    def test_the_ceo_brief_default_columns_are_unchanged(self):
        """The CEO brief fetches all three, so nothing about it may move."""
        html = E.render_meetings(
            self._meetings({"You": [], "Arjun": [], "Prathap": []}))
        self.assertIn("No meetings", html)
        self.assertNotIn(E.UNAVAILABLE_NOT_READ, html)
        self.assertNotIn(E.UNAVAILABLE_PENDING, html)


class CfoDriverSourceTests(SimpleTestCase):
    """Guards on the driver script itself.

    cfo_driver.py runs inside `manage.py shell` and cannot be imported here,
    so these read it as text. Crude, but they are the checks that would have
    caught both live faults.
    """

    _DRIVER = (Path(__file__).resolve().parents[2]
               / "infra" / "ceo-monitor" / "cfo_driver.py")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = cls._DRIVER.read_text(encoding="utf-8")

    @staticmethod
    def _labels(block):
        return re.findall(r'\(\s*"([^"]+)"\s*,\s*"[^"]*"\s*\)', block)

    def _block(self, name):
        m = re.search(name + r"\s*=\s*\[(.*?)\]", self.src, re.S)
        self.assertIsNotNone(m, f"{name} not found in cfo_driver.py")
        return m.group(1)

    def test_every_rendered_column_has_a_calendar_to_read(self):
        columns = self._labels(self._block("meeting_columns"))
        people = self._labels(self._block(r"\bpeople"))
        self.assertEqual(len(columns), 3)
        missing = [c for c in columns if c not in people]
        self.assertEqual(
            missing, [],
            "cfo_driver renders a diary column it never fetches: %s. That "
            "column prints 'No meetings', which tells the CFO someone has a "
            "free day when nobody asked." % missing)

    def test_the_brief_runs_on_botswana_time(self):
        """Africa/Gaborone is UTC+2; the server clock is UTC."""
        self.assertNotIn("date.today()", self.src)
        self.assertIn("timezone.localdate()", self.src)


class CalendarReadFailureTests(SimpleTestCase):
    """A failed read is not a free day.

    Both drivers branched on `err == 403` alone. Any other failure — a
    timeout, a 5xx, throttling — fell through to an empty event list, and
    the column then printed "No meetings" against a real person's name.
    """

    def test_a_clean_read_has_no_reason(self):
        self.assertEqual(E.unavailable_reason(None), "")
        self.assertEqual(E.unavailable_reason(""), "")

    def test_a_refusal_is_the_access_policy(self):
        self.assertEqual(E.unavailable_reason(403), E.UNAVAILABLE_PENDING)

    def test_any_other_failure_is_not_the_access_policy(self):
        """Wording matters: "still enabling" sends the reader to IT over a
        blip IT has already dealt with."""
        for err in (500, 503, 429, "timed out"):
            self.assertEqual(E.unavailable_reason(err), E.UNAVAILABLE_ERROR,
                             "err=%r" % (err,))

    def test_the_column_prints_the_reason_it_was_given(self):
        html = E.render_meetings(
            {"today": {"You": [], "Arun": [], "Arjun": []},
             "pending": {"Arun": E.UNAVAILABLE_ERROR},
             "tomorrow_counts": {}, "tomorrow_headline": "", "quip": ""},
            columns=[("You", "CFO"), ("Arun", "CEO"), ("Arjun", "COO")])
        col = html.split("Arun <span", 1)[1].split("</table>", 1)[0]
        self.assertIn(E.UNAVAILABLE_ERROR, col)
        self.assertNotIn("No meetings", col)

    def test_a_bare_true_still_means_still_enabling(self):
        """Survives a half-synced host.

        install-crons.sh syncs /opt/ceo-monitor file by file, so the new
        engine can land beside a driver that has not. The old driver hands
        back a bare True, which has only ever meant "still enabling".
        (ceo_sunday_driver does not render this panel at all.)
        """
        html = E.render_meetings(
            {"today": {"You": [], "Arjun": [], "Prathap": []},
             "pending": {"Arjun": True}, "tomorrow_counts": {},
             "tomorrow_headline": "", "quip": ""})
        col = html.split("Arjun <span", 1)[1].split("</table>", 1)[0]
        self.assertIn(E.UNAVAILABLE_PENDING, col)
        self.assertNotIn("True", col)


class DriverFailureBranchTests(SimpleTestCase):
    """Both morning briefs must route every failed read through the helper.

    One copy of the rule, used by both callers. A driver that keeps its own
    `err == 403` test is the fault coming back in the file nobody re-read.
    """

    _DIR = Path(__file__).resolve().parents[2] / "infra" / "ceo-monitor"

    def test_no_driver_branches_on_403_alone(self):
        for name in ("cfo_driver.py", "ceo_driver.py"):
            src = (self._DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("if err==403", src, name)
            self.assertIn("unavailable_reason(err)", src, name)
            # An empty default hands back "" for a URLError or a timeout, and
            # "" reads as a clean read all the way to "No meetings".
            self.assertNotIn('getattr(ex,"code","")', src, name)


class DriverTodayCountTests(SimpleTestCase):
    """Both briefs must hand the renderer an UNCAPPED count of today.

    One copy of the rule, both callers — the same shape as the 403 guard
    above. The renderer counted the rows it was given, and the drivers cap
    those at six, so a nine-meeting day printed "6 today". Fixing one driver
    and not the other leaves the wrong number on two of the three columns.
    """

    _DIR = Path(__file__).resolve().parents[2] / "infra" / "ceo-monitor"

    def test_every_driver_publishes_a_count_of_today(self):
        for name in ("cfo_driver.py", "ceo_driver.py"):
            src = (self._DIR / name).read_text(encoding="utf-8")
            self.assertIn(
                '"today_counts"', src,
                "%s does not publish today_counts, so the renderer falls back "
                "to counting the six rows it was given and the header lies on "
                "any day with more than six meetings." % name)
            # The defect signature: today's rows sliced to six in the SAME
            # expression that produces them, leaving nothing uncapped to count.
            self.assertNotIn(
                'today_iso][:6]', src,
                "%s still caps today's meetings in the expression that builds "
                "them. Build the full list, count it, THEN slice." % name)
