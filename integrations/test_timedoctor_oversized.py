"""
integrations/test_timedoctor_oversized.py — the crash that silently killed the manager report.

Live on 2026-08-03 the 07:00 manager email failed SIX times against four successful sends:

    requests.exceptions.JSONDecodeError: Unterminated string starting at:
    line 473467 column 14 (char 14200415)

Time Doctor had returned roughly 14 MB of JSON cut off mid-string. Because the crash happened
inside cron, nobody was told — managers simply received nothing, and silence reads as "everyone
was on time". Two behaviours are locked here: a truncated response is retried with a smaller
page, and a genuine failure says what actually went wrong instead of showing a parser trace.
"""
import json
from unittest.mock import patch

from django.test import SimpleTestCase

from integrations.timedoctor import TimeDoctorClient, TimeDoctorError


class FakeResponse:
    """A requests-like response. `body` of None means "will not parse"."""

    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    @property
    def content(self):
        return b'x' * 14_200_415 if self._body is None else json.dumps(self._body).encode()

    @property
    def text(self):
        return '' if self._body is None else json.dumps(self._body)

    def json(self):
        if self._body is None:
            # Exactly what requests raises on a truncated body.
            raise json.JSONDecodeError('Unterminated string starting at', 'x' * 10, 14_200_415)
        return self._body


def client():
    return TimeDoctorClient(token='t', company_id='c', base='https://api2.timedoctor.com')


class OversizedResponseTests(SimpleTestCase):

    def test_a_truncated_response_is_retried_with_a_smaller_page(self):
        calls = []

        def fake_get(url, params=None, timeout=None, headers=None):
            calls.append(dict(params or {}))
            # Fail the first (limit 500), succeed once the page has been halved.
            return FakeResponse(None if len(calls) == 1 else {'data': [{'id': 'u1'}]})

        with patch('integrations.timedoctor.requests.get', side_effect=fake_get):
            data = client()._get('/api/1.0/users', {'limit': 500})

        self.assertEqual(data, [{'id': 'u1'}])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]['limit'], 500)
        self.assertEqual(calls[1]['limit'], 250)      # halved

    def test_it_keeps_halving_and_gives_up_with_a_plain_explanation(self):
        def always_broken(url, params=None, timeout=None, headers=None):
            return FakeResponse(None)

        with patch('integrations.timedoctor.requests.get', side_effect=always_broken):
            with self.assertRaises(TimeDoctorError) as caught:
                client()._get('/api/1.0/users', {'limit': 500})

        msg = str(caught.exception)
        self.assertIn('14,200,415 bytes', msg)
        self.assertIn('could not be read as JSON', msg)
        # It must not be mistaken for the other common failure.
        self.assertIn('not a token problem', msg)

    def test_it_stops_rather_than_retrying_for_ever(self):
        calls = []

        def always_broken(url, params=None, timeout=None, headers=None):
            calls.append(1)
            return FakeResponse(None)

        with patch('integrations.timedoctor.requests.get', side_effect=always_broken):
            with self.assertRaises(TimeDoctorError):
                client()._get('/api/1.0/users', {'limit': 500})
        self.assertEqual(len(calls), TimeDoctorClient.MAX_PARSE_ATTEMPTS)

    def test_a_request_with_no_page_size_gets_one_on_the_retry(self):
        calls = []

        def fake_get(url, params=None, timeout=None, headers=None):
            calls.append(dict(params or {}))
            return FakeResponse(None if len(calls) == 1 else {'data': []})

        with patch('integrations.timedoctor.requests.get', side_effect=fake_get):
            client()._get('/api/1.0/activity/worklog', {'from': '2026-08-01'})

        self.assertNotIn('limit', calls[0])
        self.assertEqual(calls[1]['limit'], 250)

    def test_the_page_never_shrinks_below_something_useful(self):
        calls = []

        def fake_get(url, params=None, timeout=None, headers=None):
            calls.append(dict(params or {}))
            return FakeResponse(None if len(calls) < 3 else {'data': []})

        with patch('integrations.timedoctor.requests.get', side_effect=fake_get):
            client()._get('/api/1.0/users', {'limit': 60})

        # 60 -> 50 (the floor), not 30 then 15: a page too small never completes.
        self.assertEqual(calls[1]['limit'], 50)
        self.assertEqual(calls[2]['limit'], 50)

    def test_a_normal_response_is_not_retried(self):
        calls = []

        def fake_get(url, params=None, timeout=None, headers=None):
            calls.append(1)
            return FakeResponse({'data': [{'id': 'u1'}, {'id': 'u2'}]})

        with patch('integrations.timedoctor.requests.get', side_effect=fake_get):
            data = client()._get('/api/1.0/users', {'limit': 500})

        self.assertEqual(len(data), 2)
        self.assertEqual(len(calls), 1)

    def test_a_real_http_error_still_reads_as_an_http_error(self):
        def five_hundred(url, params=None, timeout=None, headers=None):
            return FakeResponse({'message': 'boom'}, status_code=500)

        with patch('integrations.timedoctor.requests.get', side_effect=five_hundred):
            with self.assertRaises(TimeDoctorError) as caught:
                client()._get('/api/1.0/users', {'limit': 500})
        self.assertIn('500', str(caught.exception))
