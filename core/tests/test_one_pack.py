"""Gate for the approval detail packs (core/approval_packs/*.py).

Every registered stream is checked. Set PACK_STREAM to narrow it to one, which
is what each cheap-lane builder ran against while it was being written:

    PACK_STREAM=petty_cash python manage.py test core.tests.test_one_pack --keepdb

Each builder is asked for an id that does not exist. That is deliberate: the
queryset is COMPILED against the real models, so a field the model does not have
(`select_related` on a CharField, a `.filter` on a renamed column) raises here,
with no fixtures to build. It caught exactly that on the generated `po` pack.

What it does NOT prove is the row rendering, which needs real rows — that is
proved for commissions in commissions/test_review_email_detail.py, and for the
rest against real data on prod after deploy, per the standing rule that a screen
which displays figures is only verified by reading the figures.
"""
import ast
import importlib
import os
import uuid

from django.test import SimpleTestCase

from core.approval_pack import build_pack, make_pack, pack_html

REQUIRED_KEYS = {"stream", "title", "subtitle", "summary", "columns", "rows",
                 "row_count", "shown_count", "row_total", "checks", "note"}


def _streams():
    from core import approval_packs
    only = os.environ.get("PACK_STREAM", "")
    if only:
        return [only]
    return sorted(approval_packs.BUILDERS)


class OnePackTests(SimpleTestCase):
    databases = {"default"}

    def test_every_builder_compiles_and_returns_none_for_a_missing_row(self):
        from core import approval_packs
        for stream in _streams():
            with self.subTest(stream=stream):
                self.assertIn(stream, approval_packs.BUILDERS,
                              f"{stream} is not registered")
                mod = importlib.import_module(f"core.approval_packs.{stream}")
                self.assertTrue(callable(getattr(mod, "build", None)),
                                f"core/approval_packs/{stream}.py must expose build(pk)")
                self.assertIsNone(mod.build(uuid.uuid4()),
                                  "build() must return None when the row does not exist")
                self.assertIsNone(build_pack(stream, uuid.uuid4()))

    def test_no_builder_swallows_its_own_errors(self):
        """A builder that wraps its query in `except Exception: return None`
        turns a wrong field name into a silently empty pack — and passes the
        test above while doing it (caught on the generated `po` pack, which
        select_related'd a CharField). core.approval_pack.build_pack already
        catches at the boundary; a builder must let its own errors out so this
        gate can see them. Optional extra context may still use try/except as
        long as the handler does not return."""
        for stream in _streams():
            with self.subTest(stream=stream):
                mod = importlib.import_module(f"core.approval_packs.{stream}")
                with open(mod.__file__) as fh:
                    tree = ast.parse(fh.read())
                for handler in ast.walk(tree):
                    if not isinstance(handler, ast.ExceptHandler):
                        continue
                    for node in ast.walk(handler):
                        if isinstance(node, ast.Return):
                            self.fail(f"core/approval_packs/{stream}.py returns from an "
                                      f"except handler (line {node.lineno}) — that hides "
                                      f"a broken field. Let the error out.")

    def test_contract_shape_and_html(self):
        pack = make_pack("demo", title="t", rows=[["a", "1"]],
                         columns=["c1", "c2"], summary=[{"label": "L", "value": "V"}])
        self.assertEqual(REQUIRED_KEYS, set(pack))
        self.assertEqual(pack["row_count"], 1)
        self.assertIn("L", pack_html(pack))
        self.assertEqual(pack_html(None), "")

    def test_a_bare_list_of_checks_is_normalised(self):
        """Three builders handed `checks` in as a plain list. Unnormalised it
        reached the renderer as a list and blew up .get() — taking the whole
        email body with it."""
        pack = make_pack("demo", checks=["something to look at"])
        self.assertEqual(pack["checks"]["level"], "check")
        self.assertIn("Worth a look", pack_html(pack))
        self.assertEqual(make_pack("demo", checks=[])["checks"]["level"], "clean")

    def test_rows_are_capped_but_the_true_count_is_kept(self):
        pack = make_pack("demo", columns=["a"], rows=[[i] for i in range(60)])
        self.assertEqual(pack["row_count"], 60)
        self.assertEqual(pack["shown_count"], 40)
        self.assertIn("first 40 of 60", pack_html(pack))
