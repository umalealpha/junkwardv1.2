"""boardroom/api_views.py — DRF endpoints for Alpha Rooms.

Company-wide by design: meeting rooms are shared physical spaces, so these
viewsets deliberately do NOT inherit CompanyScopedViewSetMixin and the models
carry no `company` FK (mirrors assets.AssetCategoryViewSet). Isolation is
per-viewset, so this changes nothing for the entity-scoped modules.
"""

from datetime import date
from uuid import UUID

from django.db import transaction
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Booking, Room
from .serializers import BookingSerializer, RoomSerializer
from django.utils import timezone


def _display_name(user) -> str:
    return (user.get_full_name() or '').strip() or user.get_username()


def _is_admin(user) -> bool:
    """Superuser / Django staff / an Omni administrator may cancel any booking."""
    if user.is_superuser or user.is_staff:
        return True
    try:
        from core.models import get_user_profile
        profile = get_user_profile(user)
        return bool(profile and getattr(profile, 'is_administrator', False))
    except Exception:
        # Fail closed: any profile-lookup issue means "not an admin", never the
        # other way round — a broken lookup must not grant cancel rights.
        return False


class RoomViewSet(viewsets.ReadOnlyModelViewSet):
    # Company-wide: shared rooms, no entity scoping (see module docstring).
    queryset = Room.objects.filter(is_active=True)
    serializer_class = RoomSerializer
    permission_classes = [IsAuthenticated]


class BookingViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    # Company-wide: shared rooms, no entity scoping (see module docstring).
    # Deliberately NOT a full ModelViewSet: bookings are created and cancelled,
    # never edited in place, so PUT/PATCH are not exposed — one user must not be
    # able to rewrite another's booking.
    serializer_class = BookingSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = Booking.objects.select_related('room', 'booked_by')
        # The board filters below are for the day LIST only. destroy() (and any
        # non-list action) must see every booking — a future booking is still
        # cancellable; ownership is enforced in destroy(), so this exposes
        # nothing extra.
        if self.action != 'list':
            return qs
        params = self.request.query_params

        # "My bookings" — everything the caller booked, from today forward.
        if params.get('mine') in ('1', 'true', 'True'):
            return qs.filter(booked_by=self.request.user, day__gte=timezone.localdate())

        # Board view — one day at a time (default today). Guard the params: a
        # bad date or room id must return nothing, never raise a 500.
        day = (params.get('day') or '').strip()
        if day:
            try:
                date.fromisoformat(day)
            except ValueError:
                return qs.none()
            qs = qs.filter(day=day)
        else:
            qs = qs.filter(day=timezone.localdate())

        room = (params.get('room') or '').strip()
        if room:
            try:
                UUID(room)
            except ValueError:
                return qs.none()
            qs = qs.filter(room_id=room)
        return qs

    def perform_create(self, serializer):
        v = serializer.validated_data
        with transaction.atomic():
            # Serialize concurrent bookings for the same room by locking its row,
            # then re-check the clash inside the lock — this closes the
            # check-then-insert race the serializer's friendly check cannot (H22).
            Room.objects.select_for_update().get(pk=v['room'].pk)
            clash = Booking.find_clash(
                room=v['room'], day=v['day'],
                start_min=v['start_min'], end_min=v['end_min'],
            )
            if clash is not None:
                who = clash.booked_by_name or 'someone'
                raise serializers.ValidationError(f'Clashes with "{clash.title}" ({who}).')
            serializer.save(
                booked_by=self.request.user,
                booked_by_name=_display_name(self.request.user),
            )

    def destroy(self, request, *args, **kwargs):
        booking = self.get_object()
        is_owner = (
            booking.booked_by_id is not None
            and booking.booked_by_id == request.user.id
        )
        if not (is_owner or _is_admin(request.user)):
            return Response(
                {'detail': 'You can only cancel your own bookings.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().destroy(request, *args, **kwargs)
