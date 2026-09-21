"""Cheap-first → premium reasoning cascade (CFO directive 2026-07-18).

Proves reasoning_complete: local/cheap tier answers first, and only escalates to a
stronger (paid) tier when the cheap one errors, returns blank, or (JSON mode)
returns unparseable JSON. No live API calls — every tier is patched.
"""
from unittest import mock

from django.test import SimpleTestCase, override_settings

from core import ai_assist
from core.ai_assist import (
    reasoning_complete, ollama_complete, _answer_inadequate,
    OllamaUnavailable, DeepSeekUnavailable, GeminiUnavailable,
    OpenAIUnavailable, GrokUnavailable, AnthropicUnavailable,
)

P = 'core.ai_assist.'


def _skip(exc):
    """A tier that is simply not configured (raises its Unavailable)."""
    def _fn(*a, **k):
        raise exc('not configured')
    return _fn


class AnswerInadequateTests(SimpleTestCase):
    def test_blank_is_inadequate(self):
        self.assertTrue(_answer_inadequate(''))
        self.assertTrue(_answer_inadequate('   \n '))
        self.assertTrue(_answer_inadequate(None))

    def test_real_text_is_adequate(self):
        self.assertFalse(_answer_inadequate('391'))
        self.assertFalse(_answer_inadequate('a full sentence answer'))

    def test_json_mode_requires_valid_json(self):
        self.assertTrue(_answer_inadequate('not json', 'json_object'))
        self.assertFalse(_answer_inadequate('{"suggestions": []}', 'json_object'))


class CascadeOrderTests(SimpleTestCase):
    def test_local_first_when_reachable(self):
        with mock.patch(P + 'ollama_complete', return_value='LOCAL') as loc, \
             mock.patch(P + 'deepseek_complete') as ds:
            self.assertEqual(reasoning_complete('hi'), 'LOCAL')
            loc.assert_called_once()
            ds.assert_not_called()            # cheap tier stopped it — no cloud spend

    def test_falls_through_to_cloud_deepseek_when_no_local(self):
        with mock.patch(P + 'ollama_complete', _skip(OllamaUnavailable)), \
             mock.patch(P + 'deepseek_complete', return_value='CLOUD') as ds, \
             mock.patch(P + 'gemini_complete') as gem:
            self.assertEqual(reasoning_complete('hi'), 'CLOUD')
            ds.assert_called_once()
            gem.assert_not_called()           # DeepSeek answered — no premium escalation

    def test_blank_cheap_answer_escalates_to_premium(self):
        with mock.patch(P + 'ollama_complete', _skip(OllamaUnavailable)), \
             mock.patch(P + 'deepseek_complete', return_value='   '), \
             mock.patch(P + 'gemini_complete', return_value='PREMIUM') as gem:
            self.assertEqual(reasoning_complete('hi'), 'PREMIUM')
            gem.assert_called_once()

    def test_json_mode_invalid_cheap_escalates(self):
        with mock.patch(P + 'ollama_complete', _skip(OllamaUnavailable)), \
             mock.patch(P + 'deepseek_complete', return_value='sorry, not json'), \
             mock.patch(P + 'gemini_complete', return_value='{"ok": 1}') as gem:
            out = reasoning_complete('hi', response_format='json_object')
            self.assertEqual(out, '{"ok": 1}')
            gem.assert_called_once()

    def test_json_mode_valid_cheap_stops(self):
        with mock.patch(P + 'ollama_complete', _skip(OllamaUnavailable)), \
             mock.patch(P + 'deepseek_complete', return_value='{"ok": 1}'), \
             mock.patch(P + 'gemini_complete') as gem:
            out = reasoning_complete('hi', response_format='json_object')
            self.assertEqual(out, '{"ok": 1}')
            gem.assert_not_called()

    def test_all_engines_fail_raises(self):
        with mock.patch(P + 'ollama_complete', _skip(OllamaUnavailable)), \
             mock.patch(P + 'deepseek_complete', _skip(DeepSeekUnavailable)), \
             mock.patch(P + 'gemini_complete', _skip(GeminiUnavailable)), \
             mock.patch(P + 'openai_complete', _skip(OpenAIUnavailable)), \
             mock.patch(P + 'grok_complete', _skip(GrokUnavailable)), \
             mock.patch(P + 'anthropic_complete', _skip(AnthropicUnavailable)):
            with self.assertRaises(DeepSeekUnavailable):
                reasoning_complete('hi')

    def test_weak_answer_is_floor_when_richer_tiers_fail(self):
        # JSON mode: DeepSeek gives weak-but-real output, every richer tier errors →
        # we return the weak answer rather than raising (no regression on a real reply).
        with mock.patch(P + 'ollama_complete', _skip(OllamaUnavailable)), \
             mock.patch(P + 'deepseek_complete', return_value='weak-json'), \
             mock.patch(P + 'gemini_complete', _skip(GeminiUnavailable)), \
             mock.patch(P + 'openai_complete', _skip(OpenAIUnavailable)), \
             mock.patch(P + 'grok_complete', _skip(GrokUnavailable)), \
             mock.patch(P + 'anthropic_complete', _skip(AnthropicUnavailable)):
            out = reasoning_complete('hi', response_format='json_object')
            self.assertEqual(out, 'weak-json')


class OllamaClientTests(SimpleTestCase):
    @override_settings(OLLAMA_API_BASE='')
    def test_unset_base_raises_so_cascade_skips(self):
        with self.assertRaises(OllamaUnavailable):
            ollama_complete('hi')

    @override_settings(OLLAMA_API_BASE='http://localhost:11434/v1',
                       OLLAMA_MODEL='deepseek-r1:14b')
    def test_strips_think_block(self):
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {
            'choices': [{'message': {'content':
                '<think>let me work it out</think>The answer is 42.'}}]
        }
        with mock.patch(P + 'requests.post', return_value=fake):
            self.assertEqual(ollama_complete('q'), 'The answer is 42.')
