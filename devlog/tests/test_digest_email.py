"""The digest must actually name the work that was claimed done.

An email that goes out every day and says nothing is worse than no email: it
reads as "all clear" and it is the thing the CFO would stop opening. On the day
this was written production held 55 open items and exactly one confirmed live,
so an empty-looking digest would have been flatly wrong.

These run against build_html directly — no database, no mail server — because
the failure being guarded is in the writing, not the counting. The counting is
day_report's, already covered, and deliberately not duplicated here.
"""
import datetime as dt

from django.test import SimpleTestCase

from devlog.management.commands.devlog_digest import MAX_ROWS, build_html

BASE = 'https://omni.alphadirect.co.bw'


def _item(title, hours, who='pganesharajah', area=''):
    return {'title': title, 'who_asked': who, 'area': area, 'age_hours': hours}


def _day(**over):
    d = {
        'day': dt.date(2026, 9, 11),
        'went_live': [],
        'in_flight': [],
        'still_to_do': [],
        'unlinked_commits': [],
        'counts': {'went_live_today': 0, 'still_open': 0, 'deploys_today': 0,
                   'shipped_without_a_request': 0},
    }
    d.update(over)
    return d


class TheDigestNamesTheWork(SimpleTestCase):
    def test_it_names_the_items_waiting_to_go_live(self):
        html = build_html(_day(in_flight=[
            _item('Telegram Last seen shows an IP', 30),
            _item('CFO morning brief', 26),
        ], counts={'went_live_today': 0, 'still_open': 2, 'deploys_today': 1,
                   'shipped_without_a_request': 0}), BASE)
        self.assertIn('Telegram Last seen shows an IP', html)
        self.assertIn('CFO morning brief', html)
        self.assertIn('Waiting to go live', html)

    def test_the_count_is_the_real_count_even_when_the_list_is_trimmed(self):
        many = [_item(f'thing {n}', 50) for n in range(MAX_ROWS + 7)]
        html = build_html(_day(in_flight=many), BASE)
        self.assertIn(f'({len(many)})', html)
        self.assertIn('and 7 more on the board.', html)

    def test_work_shipped_with_no_request_is_shown_not_hidden(self):
        html = build_html(_day(
            unlinked_commits=[{'subject': 'quietly changed the payroll screen',
                               'author': 'someone'}],
            counts={'went_live_today': 0, 'still_open': 0, 'deploys_today': 1,
                    'shipped_without_a_request': 1}), BASE)
        self.assertIn('quietly changed the payroll screen', html)
        self.assertIn('no request behind it', html)

    def test_a_clean_day_still_says_so_rather_than_rendering_blank(self):
        html = build_html(_day(), BASE)
        self.assertIn('Waiting to go live', html)
        self.assertIn('none.', html)
        self.assertIn('/cfo/build-log', html)

    def test_a_title_cannot_inject_markup_into_his_inbox(self):
        html = build_html(_day(in_flight=[_item('<script>x</script>', 5)]), BASE)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
