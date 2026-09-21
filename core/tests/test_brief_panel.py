"""Two models judge the brief and settle their disagreements (CFO, 16-Sep-2026).

No network and no real model here on purpose: the engines are stubs returning
fixed JSON, so what is under test is the MERGE — who wins, what happens to a
split, and whether a dead engine can take the brief down with it. The single
biggest risk in this design is a disagreement being silently averaged into
"not significant", which is how a real matter would vanish from the CEO's brief.

Run: manage.py test core.tests.test_brief_panel
"""
import json
from unittest import mock

from django.test import SimpleTestCase

from core import brief_panel as bp

SYS = "triage these"
PAYLOAD = json.dumps([{"i": 0, "text": "a customer is very unhappy"},
                      {"i": 1, "text": "a newsletter about tyres"}])


def _engine(payload_by_round, *, fail=False):
    """A stub model. `payload_by_round` is [round1_json, round2_json]."""
    calls = {"n": 0}

    def fn(prompt, system_prompt=None, response_format=None, max_tokens=None):
        if fail:
            raise RuntimeError("engine down")
        i = min(calls["n"], len(payload_by_round) - 1)
        calls["n"] += 1
        return json.dumps(payload_by_round[i])

    fn.calls = calls
    return fn


def _v(i, significant, severity="High", category="complaint",
       matter="a thing happened", why="because of a number"):
    return {"i": i, "significant": significant, "severity": severity,
            "category": category, "matter": matter, "why": why}


class _SafeText:
    safe = True
    redacted_text = PAYLOAD


def _run(engines):
    with mock.patch("core.ai_assist.is_safe_for_ai", return_value=_SafeText()):
        return bp.panel_classify(PAYLOAD, SYS, engines=engines)


class PanelAgreementTests(SimpleTestCase):
    def test_when_both_agree_the_answer_stands(self):
        a = _engine([[_v(0, True), _v(1, False)]])
        b = _engine([[_v(0, True), _v(1, False)]])
        verdicts, note = _run((("A", a), ("B", b)))
        self.assertTrue(verdicts[0]["significant"])
        self.assertFalse(verdicts[1]["significant"])
        self.assertIn("agreed on all", note)
        self.assertEqual(a.calls["n"], 1, "no second round was needed")
        self.assertEqual(b.calls["n"], 1)

    def test_the_more_serious_severity_wins(self):
        a = _engine([[_v(0, True, severity="Watch")]])
        b = _engine([[_v(0, True, severity="Critical")]])
        verdicts, _ = _run((("A", a), ("B", b)))
        self.assertEqual(verdicts[0]["severity"], "Critical")

    def test_a_named_category_beats_a_shrug(self):
        a = _engine([[_v(0, True, category="other")]])
        b = _engine([[_v(0, True, category="regulatory")]])
        verdicts, _ = _run((("A", a), ("B", b)))
        self.assertEqual(verdicts[0]["category"], "regulatory")

    def test_the_fuller_explanation_is_kept(self):
        a = _engine([[_v(0, True, why="it matters")]])
        b = _engine([[_v(0, True, why="NBFIRA want an answer within 14 days "
                                      "on a P3.2m prudential shortfall")]])
        verdicts, _ = _run((("A", a), ("B", b)))
        self.assertIn("NBFIRA", verdicts[0]["why"])


class ConcreteWordingTests(SimpleTestCase):
    """The CFO picked GPT-5's wording out of the two by eye (16-Sep-2026):
    "Commercial property flood claim for Nexovant (Pty) Ltd". It names the
    client, the policy and the event. Length alone would not have chosen it.
    """

    _CONCRETE = "Commercial property flood claim for Nexovant (Pty) Ltd, policy COMG0042311."
    _WAFFLE = ("This appears to be a matter that may require some attention from "
               "the executive in due course, and should probably be reviewed by "
               "the relevant team as part of the usual process going forward.")

    def test_the_concrete_sentence_wins_even_though_it_is_shorter(self):
        self.assertLess(len(self._CONCRETE), len(self._WAFFLE))
        self.assertEqual(bp._richer(self._WAFFLE, self._CONCRETE), self._CONCRETE)
        self.assertEqual(bp._richer(self._CONCRETE, self._WAFFLE), self._CONCRETE)

    def test_it_reaches_the_card_through_the_merge(self):
        a = _engine([[_v(0, True, matter=self._WAFFLE, why=self._WAFFLE)]])
        b = _engine([[_v(0, True, matter=self._CONCRETE, why=self._CONCRETE)]])
        verdicts, _ = _run((("A", a), ("B", b)))
        self.assertEqual(verdicts[0]["matter"], self._CONCRETE)

    def test_length_still_breaks_a_tie(self):
        short, long = "NBFIRA replied.", "NBFIRA replied and wants a response."
        self.assertEqual(bp._richer(short, long), long)

    def test_an_empty_answer_never_wins(self):
        self.assertEqual(bp._richer("", self._CONCRETE), self._CONCRETE)
        self.assertEqual(bp._richer(self._CONCRETE, ""), self._CONCRETE)


class PanelDisagreementTests(SimpleTestCase):
    def test_a_disagreement_goes_to_a_second_round(self):
        a = _engine([[_v(0, True)], [_v(0, False)]])   # A backs down
        b = _engine([[_v(0, False)], [_v(0, False)]])  # B holds
        verdicts, note = _run((("A", a), ("B", b)))
        self.assertFalse(verdicts[0]["significant"], "they settled on 'no'")
        self.assertEqual(a.calls["n"], 2, "A was asked to settle")
        self.assertEqual(b.calls["n"], 2, "B was asked to settle")
        self.assertIn("1 settled", note)

    def test_an_unresolved_split_is_shown_not_dropped(self):
        """THE failure that matters: a real matter quietly averaged away."""
        a = _engine([[_v(0, True, severity="Critical")], [_v(0, True, severity="Critical")]])
        b = _engine([[_v(0, False)], [_v(0, False)]])
        verdicts, note = _run((("A", a), ("B", b)))
        self.assertTrue(verdicts[0]["significant"],
                        "an unresolved split must be SHOWN - hiding a real "
                        "matter is the expensive mistake, not showing a spare one")
        self.assertEqual(verdicts[0]["severity"], "Critical")
        self.assertTrue(verdicts[0]["panel_split"])
        self.assertIn("still split", note)

    def test_only_the_disputed_items_are_re_argued(self):
        a = _engine([[_v(0, True), _v(1, False)], [_v(1, False)]])
        b = _engine([[_v(0, True), _v(1, True)], [_v(1, False)]])
        verdicts, _ = _run((("A", a), ("B", b)))
        self.assertTrue(verdicts[0]["significant"], "item 0 was never in dispute")
        self.assertFalse(verdicts[1]["significant"])


class PanelResilienceTests(SimpleTestCase):
    """The brief must not go dark because a vendor had a bad afternoon."""

    def test_one_dead_engine_still_produces_a_brief(self):
        a = _engine([[_v(0, True)]])
        b = _engine([], fail=True)
        verdicts, note = _run((("A", a), ("B", b)))
        self.assertTrue(verdicts[0]["significant"])
        self.assertIn("A alone", note)
        self.assertIn("B failed", note)

    def test_both_dead_hands_back_none_so_the_caller_can_fall_back(self):
        a = _engine([], fail=True)
        b = _engine([], fail=True)
        verdicts, note = _run((("A", a), ("B", b)))
        self.assertIsNone(verdicts, "the caller must be told to use the old path")
        self.assertIn("failed", note)

    def test_unreadable_json_counts_as_a_failure_not_an_empty_inbox(self):
        def junk(prompt, system_prompt=None, response_format=None, max_tokens=None):
            return "I think the first email is quite important, actually."
        a = _engine([[_v(0, True)]])
        verdicts, note = _run((("A", a), ("B", junk)))
        self.assertTrue(verdicts[0]["significant"])
        self.assertIn("unreadable json", note)

    def test_pii_is_never_sent_to_either_model(self):
        called = []

        def spy(prompt, system_prompt=None, response_format=None, max_tokens=None):
            called.append(prompt)
            return json.dumps([_v(0, True)])

        class _Blocked:
            safe = False
            redacted_text = ""

        with mock.patch("core.ai_assist.is_safe_for_ai", return_value=_Blocked()):
            verdicts, note = bp.panel_classify(PAYLOAD, SYS,
                                               engines=(("A", spy), ("B", spy)))
        self.assertIsNone(verdicts)
        self.assertEqual(note, "pii-block")
        self.assertEqual(called, [], "nothing may leave when the firewall says no")

    def test_the_redacted_text_is_what_gets_sent(self):
        seen = []

        def spy(prompt, system_prompt=None, response_format=None, max_tokens=None):
            seen.append(prompt)
            return json.dumps([_v(0, True)])

        class _Redacted:
            safe = True
            redacted_text = "REDACTED PAYLOAD"

        with mock.patch("core.ai_assist.is_safe_for_ai", return_value=_Redacted()):
            bp.panel_classify("raw text with a name in it", SYS,
                              engines=(("A", spy), ("B", spy)))
        self.assertTrue(seen)
        for prompt in seen:
            self.assertEqual(prompt, "REDACTED PAYLOAD")
            self.assertNotIn("raw text", prompt)


class DegradedPanelTests(SimpleTestCase):
    """Every way the panel can quietly collapse back to ONE cheap model.

    Each of these fails silently: the note says "Gemini alone" in a cron log
    nobody reads, and the CEO is triaged by exactly what this change exists to
    stop. That is worse than an obvious crash.
    """

    def test_a_model_may_wrap_its_array_under_any_key(self):
        """OpenAI JSON mode REQUIRES a top-level object, so a model asked for an
        array invents a wrapper key. Guessing the name is a losing game."""
        for key in ("results", "emails", "analysis", "triage", "output"):
            with self.subTest(key=key):
                parsed = bp._parse(json.dumps({key: [_v(0, True)]}))
                self.assertIsNotNone(parsed, f"{key} was discarded")
                self.assertTrue(parsed[0]["significant"])

    def test_a_bare_verdict_object_is_accepted(self):
        """Round 2 re-argues a handful of items and gpt-5 replies with ONE
        object, not an array of one. Measured on prod 16-Sep-2026 - the answer
        was good and was thrown away as unreadable."""
        parsed = bp._parse(json.dumps(_v(3, True, matter="CFO wants Global Admin")))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[3]["matter"], "CFO wants Global Admin")

    def test_a_bare_object_that_is_not_a_verdict_is_still_refused(self):
        self.assertIsNone(bp._parse(json.dumps({"status": "ok", "count": 3})))

    def test_an_ambiguous_object_with_two_lists_is_still_refused(self):
        raw = json.dumps({"a": [_v(0, True)], "b": [_v(1, False)]})
        self.assertIsNone(bp._parse(raw), "guessing between two lists is wrong")

    def test_both_engines_are_given_a_timeout_that_fits_the_model(self):
        """gpt-5 reasons before answering and takes 60-120s on a real inbox; the
        library default is 30s, so EVERY call would time out."""
        import inspect
        from functools import partial
        from unittest import mock
        seen = {}

        def spy(prompt, system_prompt=None, response_format=None, max_tokens=None,
                model=None, timeout=None):
            seen[model or "gemini"] = timeout
            return json.dumps([_v(0, True)])

        with mock.patch("core.ai_assist.openai_complete", spy),                 mock.patch("core.ai_assist.gemini_complete", spy),                 mock.patch("core.ai_assist.is_safe_for_ai", return_value=_SafeText()):
            bp.panel_classify(PAYLOAD, SYS)
        self.assertTrue(seen, "no engine was called")
        for who, t in seen.items():
            self.assertIsNotNone(t, f"{who} inherited the library default")
            self.assertGreaterEqual(t, 60, f"{who} timeout {t}s is too short")


class HollowAnswerTests(SimpleTestCase):
    """A model that answers without doing the work.

    Measured on prod 16-Sep-2026: gemini-2.5-flash returned 30 structurally
    perfect verdicts on the real inbox with EVERY matter and why empty and
    nothing significant. Valid JSON, right shape, right count, completely
    hollow - and "nothing is significant" is indistinguishable from a clean
    result, so it would have sat in the panel looking alive.
    """

    def _blank(self, i):
        return {"i": i, "significant": False, "category": "other",
                "severity": "Watch", "matter": "", "why": ""}

    def test_all_blank_verdicts_count_as_a_failure(self):
        self.assertTrue(bp._is_vacuous({i: self._blank(i) for i in range(30)}))

    def test_one_real_verdict_is_enough_to_be_a_real_answer(self):
        v = {i: self._blank(i) for i in range(30)}
        v[7] = _v(7, True, matter="NBFIRA want an answer by Friday")
        self.assertFalse(bp._is_vacuous(v))

    def test_a_genuinely_quiet_inbox_with_reasons_is_not_hollow(self):
        """Every item not significant is fine IF the model explained itself."""
        v = {i: {"i": i, "significant": False, "category": "other",
                 "severity": "Watch", "matter": "A tyre newsletter.",
                 "why": "Marketing, no ask."} for i in range(5)}
        self.assertFalse(bp._is_vacuous(v))

    def test_a_hollow_model_is_dropped_from_the_panel(self):
        real = _engine([[_v(0, True, matter="NBFIRA escalation", why="regulator")]])
        hollow = _engine([[self._blank(0)]])
        verdicts, note = _run((("Real", real), ("Hollow", hollow)))
        self.assertTrue(verdicts[0]["significant"])
        self.assertIn("Real alone", note)
        self.assertIn("said nothing", note)

    def test_both_hollow_falls_back_rather_than_reporting_a_quiet_day(self):
        a = _engine([[self._blank(0)]])
        b = _engine([[self._blank(0)]])
        verdicts, note = _run((("A", a), ("B", b)))
        self.assertIsNone(verdicts, "two hollow answers must NOT read as 'all clear'")
        self.assertIn("said nothing", note)


class SplitCardWordingTests(SimpleTestCase):
    """The branch that protects the CEO must not print the dissenter's view."""

    _URGENT = "NBFIRA demands P3.2m by Friday."
    _DISMISSAL = ("This appears to be a routine marketing newsletter about tyres "
                  "and requires no action from the CEO whatsoever.")

    def test_a_split_card_carries_the_significant_sides_words(self):
        a = _engine([[_v(0, True, severity="Critical", matter=self._URGENT,
                         why=self._URGENT)]] * 2)
        b = _engine([[_v(0, False, matter=self._DISMISSAL,
                         why=self._DISMISSAL)]] * 2)
        verdicts, _ = _run((("A", a), ("B", b)))
        self.assertTrue(verdicts[0]["significant"])
        self.assertEqual(verdicts[0]["matter"], self._URGENT,
                         "the card says urgent but reads as 'no action needed'")
        self.assertNotIn("no action", verdicts[0]["why"])


class SeverityNormalisationTests(SimpleTestCase):
    """An off-enum severity is a KeyError inside ceo_engine - i.e. NO brief."""

    def test_model_casing_is_normalised(self):
        for raw, expected in (("critical", "Critical"), ("HIGH", "High"),
                              ("watch", "Watch"), ("Critical", "Critical")):
            with self.subTest(raw=raw):
                self.assertEqual(bp._worse(raw, "Watch"), expected)

    def test_an_invented_severity_never_escapes(self):
        for junk in ("urgent", "", None, "SEV1", 7):
            with self.subTest(junk=junk):
                self.assertIn(bp._worse(junk, "High"), bp._SEVERITY)

    def test_an_invented_severity_does_not_outrank_a_real_one(self):
        self.assertEqual(bp._worse("urgent", "Critical"), "Critical")


class Gpt5ParameterTests(SimpleTestCase):
    """gpt-5 refuses 'max_tokens'; sending it returns HTTP 400, which the chain
    reads as 'engine down' and quietly falls through to a weaker model."""

    def _payload_for(self, model):
        import requests
        from core import ai_assist
        captured = {}

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": "{}"}}]}

        def fake_post(url, headers=None, data=None, timeout=None):
            captured.update(json.loads(data))
            return _Resp()

        with mock.patch.object(ai_assist, "get_llm_key", return_value="k"), \
                mock.patch.object(requests, "post", side_effect=fake_post):
            ai_assist.openai_complete("hi", model=model, max_tokens=1000)
        return captured

    def test_gpt5_gets_max_completion_tokens_and_no_temperature(self):
        p = self._payload_for("gpt-5")
        self.assertIn("max_completion_tokens", p)
        self.assertNotIn("max_tokens", p)
        self.assertNotIn("temperature", p)

    def test_the_reasoning_budget_is_not_the_plain_chat_budget(self):
        """It spends tokens thinking before answering; a chat-sized budget comes
        back BLANK, which the chain also reads as failure."""
        self.assertGreaterEqual(
            self._payload_for("gpt-5")["max_completion_tokens"], 2000)

    def test_an_ordinary_model_is_unchanged(self):
        p = self._payload_for("gpt-4o-mini")
        self.assertEqual(p["max_tokens"], 1000)
        self.assertEqual(p["temperature"], 0.2)
        self.assertNotIn("max_completion_tokens", p)

    def test_the_o_series_is_treated_the_same_way(self):
        for model in ("o3", "o4-mini", "gpt-5-mini"):
            with self.subTest(model=model):
                self.assertIn("max_completion_tokens", self._payload_for(model))
