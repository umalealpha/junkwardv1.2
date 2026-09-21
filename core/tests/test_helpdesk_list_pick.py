"""The Help Desk reminder must read "IT Tickets", never the empty stock "Tickets".

Burned 2026-08-03: the ITHelpDesk SharePoint site carries BOTH lists, and the
stock "Tickets" list sorts first. The old fuzzy `"ticket" in displayName` pick
therefore matched the empty one, so `helpdesk_pending_reminder` printed
"Pending tickets: 0" on every run while 6 real tickets sat On Hold and nobody
was reminded. These tests pin the selection.
"""
from django.test import SimpleTestCase

from core.management.commands.helpdesk_pending_reminder import pick_list_id


class PickListIdTests(SimpleTestCase):
    def test_prefers_it_tickets_even_when_tickets_sorts_first(self):
        lists = [
            {"id": "docs", "displayName": "Documents"},
            {"id": "stock", "displayName": "Tickets"},
            {"id": "real", "displayName": "IT Tickets"},
            {"id": "dev", "displayName": "Devices"},
        ]
        self.assertEqual(pick_list_id(lists), "real")

    def test_never_falls_back_to_the_empty_stock_tickets_list(self):
        lists = [
            {"id": "docs", "displayName": "Documents"},
            {"id": "stock", "displayName": "Tickets"},
        ]
        self.assertIsNone(pick_list_id(lists))

    def test_fuzzy_fallback_when_renamed(self):
        lists = [
            {"id": "stock", "displayName": "Tickets"},
            {"id": "renamed", "displayName": "IT Tickets 2026"},
        ]
        self.assertEqual(pick_list_id(lists), "renamed")

    def test_case_and_whitespace_insensitive(self):
        self.assertEqual(
            pick_list_id([{"id": "real", "displayName": "  it tickets "}]), "real")

    def test_no_lists_returns_none(self):
        self.assertIsNone(pick_list_id([]))
