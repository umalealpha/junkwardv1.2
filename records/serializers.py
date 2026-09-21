from rest_framework import serializers

from .models import RecordCategory, RecordItem, RecordMovement


class RecordCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = RecordCategory
        fields = ['id', 'name', 'description', 'retention_months', 'active']


class RecordMovementSerializer(serializers.ModelSerializer):
    from_name   = serializers.CharField(source='from_employee.full_name',
                                        read_only=True, default='')
    to_name     = serializers.CharField(source='to_employee.full_name',
                                        read_only=True, default='')
    recorded_by_name = serializers.CharField(source='recorded_by.get_full_name',
                                             read_only=True, default='')

    class Meta:
        model = RecordMovement
        fields = ['id', 'record', 'kind', 'moved_at', 'due_back_on', 'reason',
                  'from_employee', 'from_name', 'from_custodian', 'from_location',
                  'to_employee', 'to_name', 'to_custodian', 'to_location',
                  'recorded_by', 'recorded_by_name', 'created_at']
        read_only_fields = ['recorded_by', 'created_at']


class RecordItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    holder_name   = serializers.CharField(source='current_holder.full_name',
                                          read_only=True, default='')
    is_out        = serializers.BooleanField(read_only=True)
    days_out      = serializers.IntegerField(read_only=True)
    may_be_destroyed = serializers.BooleanField(read_only=True)
    # Whoever holds it is either a member of staff or an outside party; the
    # register has to show one column, not two half-empty ones.
    held_by       = serializers.SerializerMethodField()

    class Meta:
        model = RecordItem
        fields = ['id', 'reference', 'title', 'category', 'category_name',
                  'company', 'confidentiality', 'status', 'department',
                  'current_holder', 'holder_name', 'current_custodian',
                  'current_location', 'held_by',
                  'due_back_on', 'opened_on', 'closed_on', 'retention_until',
                  'legal_hold', 'legal_hold_note', 'notes',
                  'is_out', 'days_out', 'out_since',
                  'may_be_destroyed', 'created_at', 'updated_at']
        # Where a record IS may only change by recording a movement. Leaving these
        # writable let a PATCH move a file with no movement row at all, which
        # defeats the single write path in services.move_record() entirely.
        # legal_hold is here too: un-hold then destroy was a two-call sequence for
        # anyone who could open the register.
        read_only_fields = ['status', 'current_holder', 'current_custodian',
                            'current_location', 'due_back_on', 'out_since',
                            'legal_hold', 'legal_hold_note']

    def get_held_by(self, obj):
        if obj.current_holder_id:
            return obj.current_holder.full_name
        return obj.current_custodian or ''
