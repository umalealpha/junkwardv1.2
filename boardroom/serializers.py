"""boardroom/serializers.py — DRF serializers for Alpha Rooms."""

from rest_framework import serializers

from .models import DAY_END_MIN, DAY_START_MIN, Booking, Room


class RoomSerializer(serializers.ModelSerializer):
    class Meta:
        model = Room
        fields = [
            'id', 'name', 'floor', 'seats', 'kit',
            'is_prominent', 'is_active',
        ]
        read_only_fields = fields


class BookingSerializer(serializers.ModelSerializer):
    room_name = serializers.CharField(source='room.name', read_only=True)
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = [
            'id', 'room', 'room_name', 'day', 'start_min', 'end_min',
            'title', 'attendees', 'booked_by', 'booked_by_name', 'is_mine',
            'created_at',
        ]
        # booked_by / booked_by_name are stamped server-side from the signed-in
        # user, never trusted from the client.
        read_only_fields = [
            'id', 'room_name', 'booked_by', 'booked_by_name', 'is_mine',
            'created_at',
        ]

    def get_is_mine(self, obj) -> bool:
        request = self.context.get('request')
        return bool(request and obj.booked_by_id == request.user.id)

    def validate_title(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Give the meeting a name.')
        return value

    def validate(self, attrs):
        start = attrs.get('start_min')
        end = attrs.get('end_min')
        room = attrs.get('room')
        day = attrs.get('day')

        if start is None or end is None:
            raise serializers.ValidationError('Start and end are required.')
        if end <= start:
            raise serializers.ValidationError('The meeting must end after it starts.')
        if start < DAY_START_MIN:
            raise serializers.ValidationError('Bookings start from 07:00.')
        if end > DAY_END_MIN:
            raise serializers.ValidationError('That runs past the end of the day (19:00).')

        # Clash check — same room, same day, overlapping window. Mirrors the
        # standalone app's findClash, and names the meeting in the way. This is
        # the friendly pre-save check; perform_create re-checks under a row lock
        # to close the check-then-insert race.
        clash = Booking.find_clash(
            room=room, day=day, start_min=start, end_min=end,
            exclude_id=getattr(self.instance, 'pk', None),
        )
        if clash is not None:
            who = clash.booked_by_name or 'someone'
            raise serializers.ValidationError(
                f'Clashes with "{clash.title}" ({who}).'
            )
        return attrs
