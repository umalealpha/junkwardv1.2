"""A caller's kwarg must not kill the fallback chain.

Found 20-Sep-2026 by running the training-course generator against a real
document: `reasoning_complete(..., max_tokens=6000)` reached `grok_complete`,
which has no `max_tokens`, and the TypeError escaped the `except exc_cls`
handler — so the feature reported "AI unavailable" while Grok and Anthropic were
never actually tried.
"""
from __future__ import annotations

from django.test import TestCase

from core.ai_assist import _accepted_kwargs


def takes_everything(prompt, *, system_prompt=None, timeout=30.0,
                     response_format=None, max_tokens=None):
    return 'ok'


def no_max_tokens(prompt, *, system_prompt=None, timeout=30.0,
                  response_format=None):
    return 'ok'


def takes_kwargs(prompt, **kwargs):
    return 'ok'


class AcceptedKwargsTests(TestCase):
    CALL = {'system_prompt': 's', 'timeout': 45.0,
            'response_format': 'json_object', 'max_tokens': 6000}

    def test_an_engine_that_takes_max_tokens_still_gets_it(self):
        self.assertEqual(_accepted_kwargs(takes_everything, self.CALL), self.CALL)

    def test_an_engine_without_max_tokens_has_it_dropped(self):
        got = _accepted_kwargs(no_max_tokens, self.CALL)
        self.assertNotIn('max_tokens', got)
        self.assertEqual(got['timeout'], 45.0)
        self.assertEqual(got['response_format'], 'json_object')

    def test_the_filtered_call_actually_works(self):
        """The point of the filter: the call must not raise."""
        with self.assertRaises(TypeError):
            no_max_tokens('p', **self.CALL)
        self.assertEqual(no_max_tokens('p', **_accepted_kwargs(no_max_tokens, self.CALL)),
                         'ok')

    def test_an_engine_taking_kwargs_gets_everything(self):
        self.assertEqual(_accepted_kwargs(takes_kwargs, self.CALL), self.CALL)

    def test_the_real_engines_survive_a_max_tokens_caller(self):
        from core import ai_assist
        for name in ('ollama_complete', 'deepseek_complete', 'gemini_complete',
                     'openai_complete', 'grok_complete', 'anthropic_complete'):
            fn = getattr(ai_assist, name)
            got = _accepted_kwargs(fn, self.CALL)
            self.assertIn('timeout', got, f'{name} lost timeout')
            # Must be callable with what survived — signature check only.
            import inspect
            inspect.signature(fn).bind('prompt', **got)
