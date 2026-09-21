"""Morning-brief note spaces (CFO 2026-09-10)."""
import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core import brief_note_access as access
from core.brief_note_delivery import mark_delivered, notes_for_brief
from core.brief_note_views import next_brief_date
from core.models import BriefNote, OmniTask

User = get_user_model()


def _u(local, **kw):
    return User.objects.create(username=local, email=f"{local}@alphadirect.co.bw",
                               is_active=True, **kw)


class CeoSpaceAccessTests(TestCase):
    """Exactly the seven the CFO named: "me, unami, arjun, and Paul, kakale,
    bharath, wangu"."""

    def test_each_of_the_seven_can_post(self):
        for local in ("pganesharajah", "ubutale", "arjuniyer", "pbeka",
                      "kbotana", "bbalasubramanian", "wmoses"):
            with self.subTest(who=local):
                self.assertTrue(access.can_post_to_ceo(_u(local)))

    def test_an_ordinary_employee_cannot_post(self):
        self.assertFalse(access.can_post_to_ceo(_u("someone")))

    def test_a_named_cfo_space_person_is_not_thereby_in_the_ceo_space(self):
        """Oprah and Kago may write to the CFO but NOT to the CEO."""
        for local in ("omogomotsi", "ktshutlhedi", "pkago", "btendani"):
            with self.subTest(who=local):
                u = _u(local)
                self.assertTrue(access.can_post_to_cfo(u))
                self.assertFalse(access.can_post_to_ceo(u))

    def test_a_superuser_is_NOT_admitted(self):
        """No superuser arm on purpose: the QC robots run as superusers and this
        space is confidential to the seven."""
        self.assertFalse(access.can_post_to_ceo(_u("robot", is_superuser=True,
                                                   is_staff=True)))

    def test_a_deactivated_exec_loses_access(self):
        u = _u("wmoses")
        u.is_active = False
        self.assertFalse(access.can_post_to_ceo(u))

    def test_the_space_is_shared_between_the_seven_and_readable_by_the_ceo(self):
        self.assertTrue(access.can_read_space(_u("ubutale"), "ceo"))
        self.assertTrue(access.can_read_space(_u("aiyer"), "ceo"))
        self.assertFalse(access.can_read_space(_u("someone"), "ceo"))

    def test_matching_works_on_username_when_sso_leaves_email_blank(self):
        u = User.objects.create(username="wmoses", email="", is_active=True)
        self.assertTrue(access.can_post_to_ceo(u))


class CfoSpaceEntityTests(TestCase):
    """"any of ADIC, Unicoin, Veritas, Risk Software staff"."""

    def _employee_at(self, local, code):
        from core.models import Company
        from payroll.models import Employee
        u = _u(local)
        company, _ = Company.objects.get_or_create(
            code=code, defaults={"name": f"Test {code}", "country": "BW"})
        Employee.objects.create(user=u, company=company,
                                full_name=f"Test {local}",
                                employee_number=f"E-{local}")
        return u

    def test_staff_of_each_named_entity_can_post(self):
        for code in ("ADIC", "UNI", "VCM", "RSA"):
            with self.subTest(entity=code):
                self.assertTrue(access.can_post_to_cfo(
                    self._employee_at(f"staff{code.lower()}", code)))

    def test_an_unrelated_entity_cannot_post(self):
        """Alpha Direct South Africa is ADSA and was NOT in the CFO's list.
        Guards the RSA/South-Africa naming trap."""
        self.assertFalse(access.can_post_to_cfo(
            self._employee_at("zaperson", "ADSA")))

    def test_a_user_with_NO_company_cannot_post(self):
        """M365 roster imports land with a null company. Defaulting null to ADIC
        would hand ~every login the right to write into the CFO's brief."""
        self.assertFalse(access.can_post_to_cfo(_u("nocompany")))

    def test_the_named_list_still_works_without_an_employee_record(self):
        self.assertTrue(access.can_post_to_cfo(_u("tchimidza")))

    def test_both_of_modiris_accounts_are_admitted(self):
        """CFO 2026-09-10 chose "give both accounts access" so he cannot lock
        himself out with the duplicate."""
        for name in ("ceooffice", "ceooffice2"):
            with self.subTest(acct=name):
                self.assertTrue(access.can_post_to_cfo(
                    User.objects.create(username=name,
                                        email="ceooffice@alphadirect.co.bw",
                                        is_active=True)))

    def test_dorothy_and_gaolebale_are_both_named(self):
        """The CFO wrote one name, "Dorothy Gaolabale", and confirmed he meant
        both people."""
        self.assertTrue(access.can_post_to_cfo(_u("dikgopoleng")))
        self.assertTrue(access.can_post_to_cfo(_u("gmachobane")))

    def test_the_cfo_space_is_not_shared_with_its_posters(self):
        """A junior's note to the CFO is between the two of them."""
        self.assertFalse(access.can_read_space(_u("btendani"), "cfo"))
        self.assertTrue(access.can_read_space(_u("pganesharajah"), "cfo"))

    def test_an_unknown_space_is_always_refused(self):
        u = _u("pganesharajah")
        self.assertFalse(access.can_post(u, "board"))
        self.assertFalse(access.can_read_space(u, "board"))


class WordLimitTests(TestCase):
    def setUp(self):
        self.me = _u("ubutale")

    def _note(self, body):
        return BriefNote(audience="ceo", author=self.me, body=body,
                         for_date=datetime.date(2026, 9, 11))

    def test_twenty_five_words_is_accepted(self):
        self._note(" ".join(["word"] * 25)).save()
        self.assertEqual(BriefNote.objects.count(), 1)

    def test_twenty_six_words_is_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            self._note(" ".join(["word"] * 26)).save()
        self.assertIn("25 words", str(ctx.exception))

    def test_an_empty_note_is_refused(self):
        with self.assertRaises(ValidationError):
            self._note("   ").save()

    def test_the_limit_cannot_be_bypassed_by_writing_the_model_directly(self):
        """The cap lives in the model, so a shell or an importer cannot route
        around it — the limit IS the feature."""
        with self.assertRaises(ValidationError):
            BriefNote.objects.create(audience="cfo", author=self.me,
                                     body=" ".join(["x"] * 40),
                                     for_date=datetime.date(2026, 9, 11))

    def test_one_note_per_person_per_morning(self):
        from django.db.utils import IntegrityError
        self._note("first note").save()
        with self.assertRaises((IntegrityError, ValidationError)):
            BriefNote.objects.create(audience="ceo", author=self.me,
                                     body="second note",
                                     for_date=datetime.date(2026, 9, 11))

    def test_the_same_person_may_write_to_both_spaces(self):
        self._note("to the ceo").save()
        BriefNote.objects.create(audience="cfo", author=self.me,
                                 body="to the cfo",
                                 for_date=datetime.date(2026, 9, 11))
        self.assertEqual(BriefNote.objects.count(), 2)


class CutoffTests(TestCase):
    """The brief is built at 04:30 UTC. A note written after that belongs to
    TOMORROW, or the author is told "queued" for a brief already gone."""

    def test_before_the_cutoff_the_note_is_for_today(self):
        now = timezone.now().replace(2026, 9, 10, 3, 0, 0, 0)
        self.assertEqual(next_brief_date(now), datetime.date(2026, 9, 10))

    def test_after_the_cutoff_the_note_rolls_to_tomorrow(self):
        now = timezone.now().replace(2026, 9, 10, 5, 0, 0, 0)
        self.assertEqual(next_brief_date(now), datetime.date(2026, 9, 11))


class DeliveryTests(TestCase):
    def setUp(self):
        # The UTC day, matching how the views stamp for_date.
        self.day = timezone.now().date()
        self.a = _u("ubutale")
        self.b = _u("pbeka")
        for u, body in ((self.a, "first"), (self.b, "second")):
            BriefNote.objects.create(audience="ceo", author=u, body=body,
                                     for_date=self.day)

    def test_queued_notes_are_handed_over_oldest_first(self):
        rows, overflow = notes_for_brief("ceo", self.day)
        self.assertEqual([r["body"] for r in rows], ["first", "second"])
        self.assertEqual(overflow, 0)

    def test_a_withdrawn_note_is_never_delivered(self):
        n = BriefNote.objects.get(author=self.a)
        n.status = BriefNote.Status.WITHDRAWN
        n.save()
        rows, _ = notes_for_brief("ceo", self.day)
        self.assertEqual([r["body"] for r in rows], ["second"])

    def test_marking_delivered_freezes_and_is_idempotent(self):
        ids = [r["id"] for r in notes_for_brief("ceo", self.day)[0]]
        self.assertEqual(mark_delivered("ceo", ids), 2)
        self.assertEqual(mark_delivered("ceo", ids), 0)
        self.assertTrue(BriefNote.objects.get(author=self.a).is_locked)

    def test_a_delivered_note_is_not_handed_to_the_next_brief(self):
        ids = [r["id"] for r in notes_for_brief("ceo", self.day)[0]]
        mark_delivered("ceo", ids)
        rows, _ = notes_for_brief("ceo", self.day)
        self.assertEqual(rows, [])

    def test_marking_one_space_does_not_touch_the_other(self):
        BriefNote.objects.create(audience="cfo", author=self.a, body="cfo one",
                                 for_date=self.day)
        mark_delivered("ceo", [r["id"] for r in notes_for_brief("ceo", self.day)[0]])
        rows, _ = notes_for_brief("cfo", self.day)
        self.assertEqual([r["body"] for r in rows], ["cfo one"])

    def test_the_block_is_capped_and_reports_the_overflow(self):
        from core import brief_note_delivery as d
        for i in range(15):
            BriefNote.objects.create(audience="cfo", author=_u(f"p{i}"),
                                     body=f"note {i}", for_date=self.day)
        rows, overflow = notes_for_brief("cfo", self.day)
        self.assertEqual(len(rows), d.MAX_NOTES_IN_BRIEF)
        self.assertEqual(overflow, 15 - d.MAX_NOTES_IN_BRIEF)


class ApiTests(TestCase):
    def setUp(self):
        self.exec_ = _u("ubutale")
        self.staff = _u("tchimidza")
        self.outsider = _u("nobody")

    def test_access_endpoint_tells_the_ui_which_spaces_to_show(self):
        self.client.force_login(self.exec_)
        r = self.client.get("/api/v1/brief-notes/access/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["can_post_ceo"])
        self.assertTrue(r.json()["can_post_cfo"])
        self.assertEqual(r.json()["word_limit"], 25)

    def test_a_named_staffer_sees_only_the_cfo_space(self):
        self.client.force_login(self.staff)
        body = self.client.get("/api/v1/brief-notes/access/").json()
        self.assertFalse(body["can_post_ceo"])
        self.assertTrue(body["can_post_cfo"])

    def test_posting_to_a_space_you_are_not_in_is_refused(self):
        self.client.force_login(self.staff)
        r = self.client.post("/api/v1/brief-notes/",
                             {"audience": "ceo", "body": "let me in"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(BriefNote.objects.count(), 0)

    def test_an_outsider_is_refused_everywhere(self):
        self.client.force_login(self.outsider)
        for aud in ("ceo", "cfo"):
            self.assertEqual(self.client.post(
                "/api/v1/brief-notes/", {"audience": aud, "body": "hi"}
            ).status_code, 403)

    def test_writing_then_rewriting_replaces_rather_than_duplicating(self):
        self.client.force_login(self.exec_)
        r1 = self.client.post("/api/v1/brief-notes/",
                              {"audience": "ceo", "body": "first version"})
        self.assertEqual(r1.status_code, 201)
        r2 = self.client.post("/api/v1/brief-notes/",
                              {"audience": "ceo", "body": "second version"})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(BriefNote.objects.count(), 1)
        self.assertEqual(BriefNote.objects.get().body, "second version")

    def test_an_over_long_note_is_refused_with_a_plain_reason(self):
        self.client.force_login(self.exec_)
        r = self.client.post("/api/v1/brief-notes/",
                             {"audience": "ceo", "body": " ".join(["w"] * 30)})
        self.assertEqual(r.status_code, 400)
        self.assertIn("25 words", r.json()["detail"])

    def test_a_sent_note_can_no_longer_be_changed(self):
        self.client.force_login(self.exec_)
        self.client.post("/api/v1/brief-notes/",
                         {"audience": "ceo", "body": "already read"})
        day = next_brief_date()
        mark_delivered("ceo", [r["id"] for r in notes_for_brief("ceo", day)[0]])
        r = self.client.post("/api/v1/brief-notes/",
                             {"audience": "ceo", "body": "rewriting history"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(BriefNote.objects.get().body, "already read")

    def test_i_can_withdraw_my_own_note_but_not_someone_elses(self):
        self.client.force_login(self.exec_)
        note_id = self.client.post(
            "/api/v1/brief-notes/", {"audience": "ceo", "body": "mine"}
        ).json()["id"]
        self.client.force_login(_u("pbeka"))
        self.assertEqual(self.client.delete(
            f"/api/v1/brief-notes/{note_id}/").status_code, 403)
        self.client.force_login(self.exec_)
        self.assertEqual(self.client.delete(
            f"/api/v1/brief-notes/{note_id}/").status_code, 200)
        self.assertEqual(BriefNote.objects.get().status,
                         BriefNote.Status.WITHDRAWN)

    def test_a_staffer_reading_the_cfo_space_sees_only_their_own_note(self):
        other = _u("btendani")
        BriefNote.objects.create(audience="cfo", author=other,
                                 body="someone elses private note",
                                 for_date=next_brief_date())
        self.client.force_login(self.staff)
        self.client.post("/api/v1/brief-notes/",
                         {"audience": "cfo", "body": "my own note"})
        body = self.client.get("/api/v1/brief-notes/?audience=cfo").json()
        self.assertEqual([n["body"] for n in body["notes"]], ["my own note"])
        self.assertFalse(body["shared"])

    def test_the_execs_see_each_others_notes_in_the_ceo_space(self):
        BriefNote.objects.create(audience="ceo", author=_u("pbeka"),
                                 body="pauls note", for_date=next_brief_date())
        self.client.force_login(self.exec_)
        self.client.post("/api/v1/brief-notes/",
                         {"audience": "ceo", "body": "unamis note"})
        body = self.client.get("/api/v1/brief-notes/?audience=ceo").json()
        self.assertTrue(body["shared"])
        self.assertEqual({n["body"] for n in body["notes"]},
                         {"pauls note", "unamis note"})

    def test_an_unknown_space_is_a_400_not_a_500(self):
        self.client.force_login(self.exec_)
        self.assertEqual(self.client.get(
            "/api/v1/brief-notes/?audience=board").status_code, 400)

    def test_anonymous_callers_are_refused(self):
        self.assertIn(self.client.get(
            "/api/v1/brief-notes/access/").status_code, (401, 403))


class BriefDayClockTests(TestCase):
    """The reader and the writer must agree on which day a note belongs to."""

    def test_delivery_uses_the_same_clock_as_the_writer(self):
        from core import brief_note_delivery as d
        self.assertEqual(d._brief_day(), timezone.now().date())

    def test_a_note_written_just_before_the_cutoff_is_picked_up(self):
        """03:00 UTC -> for_date is today, and the 04:30 build must find it."""
        from core import brief_note_delivery as d
        me = _u("pbeka")
        stamped = next_brief_date(timezone.now().replace(hour=3, minute=0))
        BriefNote.objects.create(audience="ceo", author=me, body="late night note",
                                 for_date=stamped)
        rows, _ = d.notes_for_brief("ceo", stamped)
        self.assertEqual([r["body"] for r in rows], ["late night note"])


class OverflowDeliveryTests(TestCase):
    """Fable, 2026-09-10 (H96). The brief prints 12 notes and counts the rest;
    marking the whole day's queue as SENT told the 13th author their note had
    gone out when the reader never saw it, and it could never resurface."""

    def setUp(self):
        from core import brief_note_delivery as d
        self.d = d
        self.day = timezone.now().date()
        self.authors = [_u(f"ov{i}") for i in range(15)]
        for i, u in enumerate(self.authors):
            BriefNote.objects.create(audience="cfo", author=u, body=f"note {i:02d}",
                                     for_date=self.day)

    def test_only_the_printed_notes_are_marked_delivered(self):
        rows, overflow = self.d.notes_for_brief("cfo", self.day)
        self.assertEqual(len(rows), self.d.MAX_NOTES_IN_BRIEF)
        self.assertEqual(overflow, 15 - self.d.MAX_NOTES_IN_BRIEF)
        marked = self.d.mark_delivered("cfo", [r["id"] for r in rows])
        self.assertEqual(marked, self.d.MAX_NOTES_IN_BRIEF)
        self.assertEqual(
            BriefNote.objects.filter(audience="cfo",
                                     status=BriefNote.Status.QUEUED).count(),
            15 - self.d.MAX_NOTES_IN_BRIEF)

    def test_the_overflow_is_first_in_the_next_brief(self):
        rows, _ = self.d.notes_for_brief("cfo", self.day)
        self.d.mark_delivered("cfo", [r["id"] for r in rows])
        tomorrow = self.day + datetime.timedelta(days=1)
        next_rows, _ = self.d.notes_for_brief("cfo", tomorrow)
        self.assertEqual([r["body"] for r in next_rows[:3]],
                         ["note 12", "note 13", "note 14"])

    def test_passing_a_date_instead_of_ids_raises_loudly(self):
        """The old signature took a date. A stale caller must not fall through
        to a silent 0 — that leaves notes unfrozen and re-sent every morning."""
        with self.assertRaises(TypeError):
            self.d.mark_delivered("cfo", self.day)

    def test_an_empty_id_list_marks_nothing(self):
        self.assertEqual(self.d.mark_delivered("cfo", []), 0)
        self.assertEqual(self.d.mark_delivered("cfo", None), 0)
        self.assertEqual(BriefNote.objects.filter(
            status=BriefNote.Status.SENT).count(), 0)


class ActorAttributionTests(TestCase):
    """Fable, 2026-09-10 (H97). The CFO brief is the CEO driver cloned; every
    signed link it rendered resolved the actor from a hardcoded "aiyer", so the
    CFO's own click was recorded as the CEO's."""

    def setUp(self):
        from core import ceo_monitor_views as V
        self.V = V
        self.ceo = User.objects.create(username="aiyer",
                                       email="aiyer@alphadirect.co.bw")
        self.cfo = User.objects.create(username="pganesharajah",
                                       email="pganesharajah@alphadirect.co.bw")
        self.staff = User.objects.create(username="raiser",
                                         email="raiser@alphadirect.co.bw")

    def _task(self):
        return OmniTask.objects.create(assigner=self.staff, assignee=self.cfo,
                                       title="Sign the treaty schedule")

    def test_a_cfo_token_records_the_decision_as_the_cfo(self):
        task = self._task()
        tok = self.V.make_decision_token(task_id=task.pk, action="decline",
                                         actor="pganesharajah")
        r = self.client.post("/api/ceo-monitor/decide/", {"t": tok})
        self.assertEqual(r.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, OmniTask.Status.CANCELLED)
        note = task.comments.first().body
        self.assertIn("the CFO", note)
        self.assertNotIn("the CEO", note)

    def test_a_token_with_no_actor_still_means_the_ceo(self):
        """Links already sitting in Arun's delivered briefs must keep working."""
        task = OmniTask.objects.create(assigner=self.staff, assignee=self.ceo,
                                       title="Old link task")
        tok = self.V.make_decision_token(task_id=task.pk, action="decline")
        r = self.client.post("/api/ceo-monitor/decide/", {"t": tok})
        self.assertEqual(r.status_code, 200)
        self.assertIn("the CEO", task.comments.first().body)

    def test_an_unknown_actor_falls_back_to_the_ceo_not_an_error(self):
        self.assertEqual(self.V._owner("nobody")[0], "aiyer")
        self.assertEqual(self.V._owner(None)[0], "aiyer")

    def test_the_detail_task_is_keyed_per_actor(self):
        """The CEO and the CFO each asking for detail on the same item are two
        instructions, not one."""
        task = self._task()
        self.assertNotEqual(self.V._detail_source(task, "aiyer"),
                            self.V._detail_source(task, "pganesharajah"))


class SpaceOwnerVisibilityTests(TestCase):
    """Fable, 2026-09-10. Keying the UI on `shared` alone hid every staff note
    from the CFO — he got a box to write a note to himself."""

    def setUp(self):
        self.cfo = _u("pganesharajah")
        self.staff = _u("tchimidza")

    def test_the_cfo_is_told_he_may_read_the_whole_space(self):
        BriefNote.objects.create(audience="cfo", author=self.staff,
                                 body="a staff message", for_date=next_brief_date())
        self.client.force_login(self.cfo)
        body = self.client.get("/api/v1/brief-notes/?audience=cfo").json()
        self.assertTrue(body["can_read_all"])
        self.assertFalse(body["shared"])          # posters still don't see each other
        self.assertEqual([n["body"] for n in body["notes"]], ["a staff message"])

    def test_a_poster_is_not_told_he_may_read_all(self):
        self.client.force_login(self.staff)
        body = self.client.get("/api/v1/brief-notes/?audience=cfo").json()
        self.assertFalse(body["can_read_all"])


class ConcurrentWriteTests(TestCase):
    """Fable, 2026-09-10 (H99). full_clean(exclude=["author"]) skips every
    constraint containing `author`, so a simultaneous first write from phone and
    web reached the DB constraint and surfaced as a 500."""

    def test_a_racing_duplicate_write_replaces_instead_of_erroring(self):
        me = _u("ubutale")
        day = next_brief_date()
        BriefNote.objects.create(audience="ceo", author=me, body="from my phone",
                                 for_date=day)
        self.client.force_login(me)
        # the web request was built before the phone row existed
        r = self.client.post("/api/v1/brief-notes/",
                             {"audience": "ceo", "body": "from the web"})
        self.assertIn(r.status_code, (200, 201))
        self.assertEqual(BriefNote.objects.filter(audience="ceo",
                                                  author=me).count(), 1)
        self.assertEqual(BriefNote.objects.get(audience="ceo", author=me).body,
                         "from the web")
