"""
integrations/test_timedoctor_batched_pull.py — ask for fewer people, not a smaller page.

On 2026-09-20 the 07:00 Morning Brief was HELD for the whole company:

    Time Doctor returned 11,887,954 bytes for /api/1.0/activity/timeuse that
    could not be read as JSON, after 3 attempts. This is their API truncating
    a large response, not a token problem.

The existing defence halves `limit` and retries (test_timedoctor_oversized.py),
which cannot help here: the body is already cut off in transit and the retry asks
for the same company-wide window again. The window itself is the problem, so the
pull is split by PEOPLE.

The two endpoints are split DIFFERENTLY, and the reason is attribution:

* worklog rows carry `userId`. Who a row belongs to is read off the row, so the
  order of the response is irrelevant and a batch of ten is free.
* timeuse rows carry no userId at all. aggregate(), workforce_pulse.focus_stats()
  and td_integrity all attribute each bucket by its POSITION against the ordered
  id list. A batch of ten returned in a different order would credit one person's
  productive hours to a colleague — silently, with the right number of buckets,
  on the data that docks pay. So timeuse asks about ONE PERSON PER REQUEST, where
  position is a fact about the request rather than a hope about the response.
"""
import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from integrations.timedoctor import TimeDoctorClient, aggregate
import datetime


class FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    @property
    def content(self):
        return json.dumps(self._body).encode()

    @property
    def text(self):
        return json.dumps(self._body)

    def json(self):
        return self._body


def client():
    return TimeDoctorClient(token='t', company_id='c', base='https://api2.timedoctor.com')


def _ids(n, prefix='u'):
    return [f'{prefix}{i}' for i in range(1, n + 1)]


class _Pull(SimpleTestCase):

    def _run(self, ids, responder, *, method='timeuse'):
        """Call the endpoint with `ids`; `responder(users)` returns the buckets."""
        seen = []

        def fake_get(url, params=None, timeout=None, headers=None):
            p = dict(params or {})
            users = [u for u in (p.get('user') or '').split(',') if u]
            seen.append(users)
            return FakeResponse({'data': responder(users)})

        with patch('integrations.timedoctor.requests.get', side_effect=fake_get):
            out = getattr(client(), method)(
                datetime.date(2026, 9, 19), datetime.date(2026, 9, 19), ids)
        return out, seen


class WorklogBatchedPullTests(_Pull):
    """worklog: batches of ten, because the rows name their own owner."""

    def _run(self, ids, responder, **kw):
        return super()._run(ids, responder, method='worklog', **kw)

    def test_the_company_is_asked_for_ten_people_at_a_time(self):
        ids = _ids(55)
        _, seen = self._run(ids, lambda users: [[{'time': 60, 'score': 3}] for _ in users])
        self.assertEqual(len(seen), 6, '55 people in batches of 10')
        self.assertTrue(all(len(batch) <= 10 for batch in seen))
        # every person asked for exactly once, in roster order
        self.assertEqual([u for batch in seen for u in batch], ids)

    def test_no_single_request_carries_the_whole_company(self):
        _, seen = self._run(_ids(55), lambda users: [[] for _ in users])
        self.assertTrue(all(len(batch) < 55 for batch in seen))

    @override_settings(TIMEDOCTOR_USER_BATCH=3)
    def test_the_batch_size_is_configurable(self):
        _, seen = self._run(_ids(7), lambda users: [[] for _ in users])
        self.assertEqual([len(b) for b in seen], [3, 3, 1])

    @override_settings(TIMEDOCTOR_USER_BATCH=0)
    def test_an_empty_batch_size_falls_back_to_the_default(self):
        # Same convention as TIMEDOCTOR_TIMEOUT_SECONDS: a falsy value means
        # "unset", not "zero people per request".
        _, seen = self._run(_ids(3), lambda users: [[] for _ in users])
        self.assertEqual([len(b) for b in seen], [3], 'one batch, default size 10')

    @override_settings(TIMEDOCTOR_USER_BATCH=-5)
    def test_a_negative_batch_size_is_clamped_to_one(self):
        _, seen = self._run(_ids(3), lambda users: [[] for _ in users])
        self.assertEqual([len(b) for b in seen], [1, 1, 1])

    @override_settings(TIMEDOCTOR_USER_BATCH='not a number')
    def test_an_unparseable_batch_size_falls_back_to_the_default(self):
        _, seen = self._run(_ids(3), lambda users: [[] for _ in users])
        self.assertEqual([len(b) for b in seen], [3])

    def test_no_user_ids_is_a_single_call_as_before(self):
        out, seen = self._run(None, lambda users: [[{'time': 1, 'score': 3}]])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0], [], 'no user= param at all')
        self.assertEqual(len(out), 1)

    def test_a_short_batch_is_refetched_one_user_at_a_time(self):
        ids = _ids(20)

        def responder(users):
            # The second batch drops a person.
            if users[0] == 'u11' and len(users) > 1:
                return [[{'time': 60, 'score': 3, 'who': u}] for u in users[1:]]
            return [[{'time': 60, 'score': 3, 'who': u}] for u in users]

        out, seen = self._run(ids, responder)
        # batch 1, the short batch 2, then one call per user in that batch
        self.assertEqual(len(seen), 2 + 10)
        self.assertTrue(all(len(b) == 1 for b in seen[2:]))
        self.assertEqual(len(out), len(ids))
        self.assertEqual([b[0]['who'] for b in out], ids)

    def test_a_full_length_batch_is_not_refetched(self):
        _, seen = self._run(_ids(20), lambda users: [[{'time': 1, 'score': 3}] for _ in users])
        self.assertEqual(len(seen), 2, 'two batches, no per-user fallback')


class TimeusePerUserPullTests(_Pull):
    """timeuse: one person per request, because the rows name nobody."""

    def test_every_request_asks_about_exactly_one_person(self):
        ids = _ids(55)
        _, seen = self._run(ids, lambda users: [[{'time': 60, 'score': 3}] for _ in users])
        self.assertEqual(len(seen), 55, 'one call per person')
        self.assertEqual([len(batch) for batch in seen], [1] * 55)
        self.assertEqual([batch[0] for batch in seen], ids, 'in roster order, once each')

    @override_settings(TIMEDOCTOR_USER_BATCH=10)
    def test_the_batch_setting_does_not_group_timeuse_users(self):
        # TIMEDOCTOR_USER_BATCH tunes worklog. Grouping timeuse users would put
        # attribution back at the mercy of Time Doctor's response order.
        _, seen = self._run(_ids(6), lambda users: [[] for _ in users])
        self.assertEqual([len(b) for b in seen], [1] * 6)

    def test_no_user_ids_is_a_single_call_as_before(self):
        out, seen = self._run(None, lambda users: [[{'time': 1, 'score': 3}]])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0], [], 'no user= param at all')
        self.assertEqual(len(out), 1)

    # ── alignment, which is the whole ballgame ────────────────────────────
    def test_buckets_come_back_in_the_order_the_users_were_asked_for(self):
        ids = _ids(25)
        out, _ = self._run(ids, lambda users: [[{'time': 60, 'score': 3, 'who': u}]
                                               for u in users])
        self.assertEqual(len(out), len(ids))
        self.assertEqual([b[0]['who'] for b in out], ids)

    def test_a_person_with_no_activity_still_holds_their_place(self):
        # A silent day must not shift everybody after it onto a colleague.
        ids = _ids(5)

        def responder(users):
            if users[0] == 'u3':
                return []
            return [[{'time': 60, 'score': 3, 'who': u}] for u in users]

        out, _ = self._run(ids, responder)
        self.assertEqual(len(out), 5)
        self.assertEqual(out[2], [], 'u3 keeps an empty bucket at index 2')
        self.assertEqual(out[3][0]['who'], 'u4')

    def test_a_single_response_carrying_extra_buckets_does_not_shift_anyone(self):
        ids = _ids(4)

        def responder(users):
            if users[0] == 'u2':
                return [[{'who': 'u2'}], [{'who': '???'}]]   # more than asked for
            return [[{'time': 60, 'score': 3, 'who': u}] for u in users]

        out, _ = self._run(ids, responder)
        self.assertEqual(len(out), 4, 'still one entry per person')
        self.assertEqual(out[3][0]['who'], 'u4')

    # ── the payoff: nobody is credited with a colleague's time ────────────
    def test_productive_seconds_land_on_the_right_person(self):
        ids = _ids(25)
        users = [{'id': u, 'name': f'Person {u}', 'email': f'{u}@alphadirect.co.bw'}
                 for u in ids]

        def responder(asked):
            # Give each person a distinct number of productive seconds so a
            # one-place shift would be obvious.
            return [[{'time': 60 * (ids.index(u) + 1), 'score': 3}] for u in asked]

        timeuse, _ = self._run(ids, responder)
        worklog = [[{'userId': u, 'time': 3600, 'mode': 'computer'}] for u in ids]

        agg = aggregate(users, worklog, timeuse, [], [],
                        as_of=datetime.date(2026, 9, 19), td_user_ids=ids)
        by_email = {m['email']: m for m in agg['members']}
        for i, u in enumerate(ids, start=1):
            self.assertEqual(by_email[f'{u}@alphadirect.co.bw']['productive_seconds'],
                             60 * i, f'{u} got somebody else\'s productive time')

    def test_time_doctor_answering_out_of_order_cannot_misattribute(self):
        """The failure a length check cannot see.

        Ten buckets for ten people, right length, wrong order. Under batching
        every one of them lands on a colleague. Asking one person at a time,
        the response order is not a thing that exists.
        """
        ids = _ids(10)
        users = [{'id': u, 'name': u, 'email': f'{u}@alphadirect.co.bw'} for u in ids]

        def responder(asked):
            # The mock returns the answer to the question actually asked. With
            # one id per request there is only one right answer to return.
            return [[{'time': 60 * (ids.index(u) + 1), 'score': 3}] for u in asked]

        timeuse, seen = self._run(ids, responder)
        self.assertTrue(all(len(b) == 1 for b in seen),
                        'no request bundles people whose buckets could be swapped')
        worklog = [[{'userId': u, 'time': 3600, 'mode': 'computer'}] for u in ids]
        agg = aggregate(users, worklog, timeuse, [], [],
                        as_of=datetime.date(2026, 9, 19), td_user_ids=ids)
        by_email = {m['email']: m for m in agg['members']}
        for i, u in enumerate(ids, start=1):
            self.assertEqual(by_email[f'{u}@alphadirect.co.bw']['productive_seconds'], 60 * i)
