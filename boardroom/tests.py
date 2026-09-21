"""boardroom/tests.py — Alpha Rooms booking behaviour.

Covers the rules the standalone app proved by hand, now server-side and shared:
double-booking refused, booker stamped from the signed-in user, and only the
owner (or an admin) can cancel.
"""

from datetime import date, timedelta

from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Booking, Room


class BoardroomSeedTests(APITestCase):
    def test_rooms_seeded(self):
        # 0002_seed_rooms runs in the test DB.
        self.assertGreaterEqual(Room.objects.count(), 6)
        exco = Room.objects.get(name='Exco Board Room')
        self.assertTrue(exco.is_prominent)


class BookingApiTests(APITestCase):
    def setUp(self):
        self.room = Room.objects.get(name='Exco Board Room')
        self.day = date.today().isoformat()
        self.alice = User.objects.create_user('alice', password='x', first_name='Alice', last_name='M')
        self.bob = User.objects.create_user('bob', password='x')

    def _book(self, user, start_min, end_min, title='Meeting'):
        self.client.force_authenticate(user)
        return self.client.post('/api/v1/boardroom-bookings/', {
            'room': str(self.room.id),
            'day': self.day,
            'start_min': start_min,
            'end_min': end_min,
            'title': title,
            'attendees': 4,
        }, format='json')

    def test_booker_is_the_signed_in_user(self):
        res = self._book(self.alice, 540, 600)  # 09:00-10:00
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        b = Booking.objects.get()
        self.assertEqual(b.booked_by, self.alice)
        self.assertEqual(b.booked_by_name, 'Alice M')  # full name preferred
        self.assertTrue(res.data['is_mine'])

    def test_double_booking_refused_naming_the_clash(self):
        self._book(self.alice, 540, 600, title='ExCo Weekly')
        res = self._book(self.bob, 570, 630)  # 09:30-10:30 overlaps
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('ExCo Weekly', str(res.data))
        self.assertEqual(Booking.objects.count(), 1)

    def test_adjacent_slots_do_not_clash(self):
        self._book(self.alice, 540, 600)          # 09:00-10:00
        res = self._book(self.bob, 600, 660)       # 10:00-11:00 back-to-back
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

    def test_outside_day_window_refused(self):
        res = self._book(self.alice, 6 * 60, 7 * 60)  # 06:00 — before 07:00
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_only_owner_or_admin_can_cancel(self):
        self._book(self.alice, 540, 600)
        booking = Booking.objects.get()
        url = f'/api/v1/boardroom-bookings/{booking.id}/'

        self.client.force_authenticate(self.bob)          # not the owner
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.alice)         # owner
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Booking.objects.exists())

    def test_admin_can_cancel_anyones_booking(self):
        self._book(self.alice, 540, 600)
        booking = Booking.objects.get()
        admin = User.objects.create_user('fac', password='x', is_staff=True)
        self.client.force_authenticate(admin)
        res = self.client.delete(f'/api/v1/boardroom-bookings/{booking.id}/')
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

    def test_owner_can_cancel_a_future_booking(self):
        # The day filter on the board list must NOT reach destroy — a booking on
        # another day is still cancellable by its owner (was a 404 before).
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        self.client.force_authenticate(self.alice)
        res = self.client.post(
            '/api/v1/boardroom-bookings/',
            {'room': str(self.room.id), 'day': tomorrow, 'start_min': 540,
             'end_min': 600, 'title': 'Tomorrow', 'attendees': 2},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        bid = res.data['id']
        self.assertEqual(
            self.client.delete(f'/api/v1/boardroom-bookings/{bid}/').status_code,
            status.HTTP_204_NO_CONTENT,
        )

    def test_bookings_cannot_be_edited(self):
        # H5: a booking is create-or-cancel only. No one may PUT/PATCH it —
        # not even the owner — so nobody can rewrite another user's meeting.
        self._book(self.alice, 540, 600)
        booking = Booking.objects.get()
        url = f'/api/v1/boardroom-bookings/{booking.id}/'
        self.client.force_authenticate(self.bob)
        self.assertEqual(
            self.client.patch(url, {'title': 'hijack'}, format='json').status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.put(url, {'title': 'hijack'}, format='json').status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_mine_filter_returns_only_my_bookings(self):
        self._book(self.alice, 540, 600)
        self._book(self.bob, 600, 660)
        self.client.force_authenticate(self.alice)
        res = self.client.get('/api/v1/boardroom-bookings/?mine=1')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get('results', res.data)
        self.assertEqual(len(results), 1)
