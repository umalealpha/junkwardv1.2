"""Every serializer a viewset uses for `create` must return the new row's id.

Found three times now, always the same way — a screen creates a record, reads
`.id` off the 201, gets `undefined`, and routes the user to `/thing/undefined`:

  * GRN, fixed in 95bcfa86
  * Purchase orders, missed the same day — "Create and submit" wrote a DRAFT,
    never submitted it for approval, and dropped the raiser on a dead page
    (found 2026-09-10 in the live access log, not by a test)
  * Assets — `router.push('/assets/' + created.id)` on the same 201

Nothing that runs without a browser sees it: the write succeeds, the response
is a valid 201, and every test that checks the database passes. So this walks
the viewsets instead and fails on the shape of the response.
"""
from django.test import TestCase


class CreateSerializersReturnIdTest(TestCase):
    """Any serializer returned for action='create' must expose 'id'."""

    # Viewsets whose create response is deliberately not a single record —
    # add a case here with the reason, never by widening the rule.
    EXEMPT: set[str] = set()

    def _create_serializers(self):
        """(label, serializer_class) for every viewset that overrides create."""
        from rest_framework.viewsets import GenericViewSet

        import django.apps
        django.apps.apps.check_apps_ready()

        found = []
        seen = set()

        def walk(module_name):
            import importlib
            try:
                mod = importlib.import_module(module_name)
            except Exception:
                return
            for name in dir(mod):
                obj = getattr(mod, name, None)
                if not isinstance(obj, type) or not issubclass(obj, GenericViewSet):
                    continue
                if obj.__module__ != module_name or name in seen:
                    continue
                seen.add(name)
                get_sc = obj.__dict__.get('get_serializer_class')
                if get_sc is None:
                    continue
                # Ask the viewset itself what it uses for a create.
                try:
                    inst = obj()
                    inst.action = 'create'
                    inst.request = None
                    ser = get_sc(inst)
                except Exception:
                    continue
                if ser is not None:
                    found.append((f'{module_name}.{name}', ser))

        from django.apps import apps
        for cfg in apps.get_app_configs():
            for suffix in ('api_views', 'views', 'viewsets'):
                walk(f'{cfg.name}.{suffix}')
        return found

    def test_every_create_serializer_exposes_id(self):
        offenders = []
        for label, ser in self._create_serializers():
            if label in self.EXEMPT:
                continue
            meta = getattr(ser, 'Meta', None)
            fields = getattr(meta, 'fields', None)
            # '__all__' includes the pk; an explicit list must name it.
            if isinstance(fields, (list, tuple)) and 'id' not in fields:
                offenders.append(f'{label} -> {ser.__name__}')

        self.assertEqual(
            offenders, [],
            'These viewsets answer a create with a serializer that omits '
            "'id', so the page reads undefined off the 201 and navigates to "
            '/thing/undefined:\n  ' + '\n  '.join(offenders))

    def test_the_walk_actually_found_viewsets(self):
        # A guard that silently finds nothing is worse than no guard: it goes
        # green forever. The PO and Asset viewsets must both be in the sweep.
        labels = [label for label, _ in self._create_serializers()]
        self.assertGreater(len(labels), 5, labels)
        self.assertTrue(
            any('procurement' in label for label in labels),
            f'procurement viewsets not swept: {labels}')
