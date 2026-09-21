"""
Smoke tests for the training app. Covers the critical paths:
- correct answers score 100%, mint a certificate number, PDF renders
- wrong answers score 0%, do NOT mint a certificate
- a trainee cannot see correct_letter in the API
- roster respects Compliance-only permission
"""
import datetime as dt

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from training.models import TrainingAttempt, TrainingModule, TrainingQuestion


User = get_user_model()


def _make_module(**overrides) -> TrainingModule:
    defaults = dict(
        slug="test-mod",
        title="Test Module",
        pass_mark_pct=80,
        open_from=timezone.localdate() - dt.timedelta(days=1),
        open_until=timezone.localdate() + dt.timedelta(days=30),
        is_published=True,
        mandatory_for_all=True,
    )
    defaults.update(overrides)
    return TrainingModule.objects.create(**defaults)


def _make_qs(module, n=5):
    qs = []
    for i in range(n):
        qs.append(TrainingQuestion.objects.create(
            module=module, order=i, prompt=f"Q{i}?",
            option_a="a", option_b="b", option_c="c", option_d="d",
            correct_letter="A",
        ))
    return qs


class TrainingApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("trainee", "t@example.com", "pw")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.mod = _make_module()
        self.qs  = _make_qs(self.mod)

    def test_module_endpoint_hides_correct_letter(self):
        r = self.client.get(f"/api/v1/training/modules/{self.mod.slug}/")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for q in body["questions"]:
            self.assertNotIn("correct_letter", q)
            self.assertIn("options", q)

    def test_all_correct_passes_and_mints_certificate(self):
        answers = {str(q.id): "A" for q in self.qs}
        r = self.client.post(
            f"/api/v1/training/modules/{self.mod.slug}/submit/",
            {"answers": answers}, format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertEqual(data["score_pct"], 100)
        self.assertTrue(data["passed"])
        self.assertTrue(data["certificate_number"])
        att = TrainingAttempt.objects.get(pk=data["attempt_id"])
        self.assertTrue(att.passed)
        self.assertTrue(att.certificate_number)

    def test_all_wrong_fails_no_certificate(self):
        answers = {str(q.id): "B" for q in self.qs}
        r = self.client.post(
            f"/api/v1/training/modules/{self.mod.slug}/submit/",
            {"answers": answers}, format="json",
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["score_pct"], 0)
        self.assertFalse(data["passed"])
        self.assertIsNone(data["certificate_number"])

    def test_certificate_pdf_renders_for_owner_only(self):
        answers = {str(q.id): "A" for q in self.qs}
        r = self.client.post(
            f"/api/v1/training/modules/{self.mod.slug}/submit/",
            {"answers": answers}, format="json",
        ).json()
        att_id = r["attempt_id"]

        # Owner gets the PDF.
        r = self.client.get(f"/api/v1/training/attempts/{att_id}/certificate.pdf")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertGreater(len(r.content), 500)
        self.assertTrue(r.content.startswith(b"%PDF"))

        # A random other user gets 403.
        other = User.objects.create_user("nosy", "n@example.com", "pw")
        c2 = APIClient(); c2.force_authenticate(other)
        r2 = c2.get(f"/api/v1/training/attempts/{att_id}/certificate.pdf")
        self.assertEqual(r2.status_code, 403)

    def test_roster_compliance_only(self):
        # Ordinary trainee is refused.
        r = self.client.get(f"/api/v1/training/modules/{self.mod.slug}/roster/")
        self.assertEqual(r.status_code, 403)

        # Add them to Compliance and it lets them in.
        grp, _ = Group.objects.get_or_create(name="Compliance")
        self.user.groups.add(grp)
        r = self.client.get(f"/api/v1/training/modules/{self.mod.slug}/roster/")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("rows", body)
        self.assertIn("done", body)
        self.assertIn("total", body)

    def test_submit_refused_outside_window(self):
        self.mod.open_from  = timezone.localdate() - dt.timedelta(days=60)
        self.mod.open_until = timezone.localdate() - dt.timedelta(days=1)
        self.mod.save()
        answers = {str(q.id): "A" for q in self.qs}
        r = self.client.post(
            f"/api/v1/training/modules/{self.mod.slug}/submit/",
            {"answers": answers}, format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_certificate_numbers_are_sequential_within_module(self):
        answers = {str(q.id): "A" for q in self.qs}
        r1 = self.client.post(
            f"/api/v1/training/modules/{self.mod.slug}/submit/",
            {"answers": answers}, format="json",
        ).json()
        u2 = User.objects.create_user("t2", "t2@example.com", "pw")
        c2 = APIClient(); c2.force_authenticate(u2)
        r2 = c2.post(
            f"/api/v1/training/modules/{self.mod.slug}/submit/",
            {"answers": answers}, format="json",
        ).json()
        n1 = int(r1["certificate_number"].rsplit("-", 1)[-1])
        n2 = int(r2["certificate_number"].rsplit("-", 1)[-1])
        self.assertEqual(n2, n1 + 1)
