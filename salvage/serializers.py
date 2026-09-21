"""salvage/serializers.py — DRF serializers for the salvage portal."""
from rest_framework import serializers

from .models import (
    BuyerQuote, PartCategory, Sale, SalvageApproval, SalvageImage,
    SalvageInspection, SalvageItem, VehicleBrand, VehicleModel,
)


# ---------------------------------------------------------------------------
# Photo URLs — 16-Sep-2026
# ---------------------------------------------------------------------------
# NEVER hand out `img.image.url`. That is a /media/ path, and /media/ is not
# served in this deployment (alpha_finance/urls.py uses Django's static()
# helper, which does nothing when DEBUG is False — prod runs DEBUG=False).
# claims/vault_views.py records the same trap: "a raw file.url 404s". Every
# photo would have loaded as a broken image.
#
# Serve them through salvage.api_views.salvage_photo instead, which streams
# the bytes. Absolute where we have the request, so the public buy-salvage
# portal and the phone app both get a URL they can actually fetch.
PHOTO_URL = '/api/v1/salvage/photo/{id}/'


def photo_url(img, request=None):
    """The fetchable URL for one SalvageImage, or None."""
    if img is None or not img.image:
        return None
    path = PHOTO_URL.format(id=img.pk)
    return request.build_absolute_uri(path) if request else path


class PartCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model  = PartCategory
        fields = ['id', 'name', 'description', 'is_active']


class VehicleBrandSerializer(serializers.ModelSerializer):
    class Meta:
        model  = VehicleBrand
        fields = ['id', 'name', 'is_active']


class VehicleModelSerializer(serializers.ModelSerializer):
    brand_name = serializers.CharField(source='brand.name', read_only=True)

    class Meta:
        model  = VehicleModel
        fields = ['id', 'brand', 'brand_name', 'name', 'is_active']


class SalvageImageSerializer(serializers.ModelSerializer):
    # `image` is the STREAMING url, not the /media/ path the ImageField would
    # have produced — see the note at the top of this module.
    image = serializers.SerializerMethodField()

    class Meta:
        model  = SalvageImage
        fields = ['id', 'image', 'caption', 'ordering']

    def get_image(self, obj):
        return photo_url(obj, self.context.get('request'))


class SalvageItemListSerializer(serializers.ModelSerializer):
    """Trimmed payload for list views."""
    category_name      = serializers.CharField(source='category.name', read_only=True)
    vehicle_brand_name = serializers.CharField(source='vehicle_brand.name', read_only=True)
    vehicle_model_name = serializers.CharField(source='vehicle_model.name', read_only=True)
    company_code       = serializers.CharField(source='company.code', read_only=True)
    primary_image      = serializers.SerializerMethodField()
    open_quote_count   = serializers.SerializerMethodField()

    class Meta:
        model  = SalvageItem
        fields = [
            'id', 'item_code', 'claim_number', 'part_name',
            'category', 'category_name',
            'vehicle_brand', 'vehicle_brand_name',
            'vehicle_model', 'vehicle_model_name',
            'vehicle_year', 'vehicle_colour',
            'condition', 'status',
            'asking_price', 'reserve_price',
            'quantity', 'location', 'yard_section', 'shelf_row',
            'received_date', 'sold_date', 'disposed_date',
            'company', 'company_code',
            'primary_image', 'open_quote_count',
            'created_at',
        ]

    def get_primary_image(self, obj):
        # Streaming url, never img.image.url — see photo_url() above.
        return photo_url(obj.images.order_by('ordering', 'created_at').first(),
                         self.context.get('request'))

    def get_open_quote_count(self, obj):
        return obj.buyer_quotes.filter(
            status__in=[BuyerQuote.Status.PENDING, BuyerQuote.Status.UNDER_REVIEW,
                        BuyerQuote.Status.COUNTERED]
        ).count()


class SalvageItemDetailSerializer(SalvageItemListSerializer):
    images = SalvageImageSerializer(many=True, read_only=True)

    class Meta(SalvageItemListSerializer.Meta):
        fields = SalvageItemListSerializer.Meta.fields + [
            'part_description', 'vin_number',
            # cost_basis is what hits the BS on intake; intake_journal_entry
            # surfaces the auto-posted JE link so the UI can deep-link to it.
            'cost_basis', 'intake_journal_entry', 'intake_posted_at',
            'images', 'created_by', 'received_by', 'posted_at', 'notes',
        ]
        # `created_by`/`received_by`/intake_* are server-managed.
        read_only_fields = [
            'id', 'created_at',
            'created_by', 'received_by', 'posted_at',
            'intake_journal_entry', 'intake_posted_at',
            'company_code', 'category_name', 'vehicle_brand_name',
            'vehicle_model_name', 'primary_image', 'open_quote_count',
        ]


class SalvageItemEditSerializer(serializers.ModelSerializer):
    """Whitelist serializer for PATCH — the 'typo correction' fields only.

    Kgosi Seboko asked (2026-09-18) for edit on salvage inventory. We do NOT
    allow arbitrary edits: item_code, company, cost_basis, intake_* stay
    read-only because they anchor the intake JE and the audit trail. Status
    is only editable to VOIDED via the dedicated `void` action so a reversing
    JE is always posted.
    """
    class Meta:
        model  = SalvageItem
        fields = [
            'part_name', 'part_description',
            'category', 'quantity',
            'vehicle_brand', 'vehicle_model', 'vehicle_year',
            'vehicle_colour', 'vin_number',
            'condition', 'asking_price', 'reserve_price',
            'location', 'yard_section', 'shelf_row',
            'notes',
        ]

    def validate(self, attrs):
        item = self.instance
        # Terminal rows are frozen — a sold/disposed/scrapped item cannot
        # be silently rewritten because its GL entries are already booked.
        if item and item.status in (
            SalvageItem.Status.SOLD,
            SalvageItem.Status.DISPOSED,
            SalvageItem.Status.SCRAPPED,
            SalvageItem.Status.VOIDED,
        ):
            raise serializers.ValidationError(
                f'Item is {item.get_status_display()}; edits are locked. '
                'Void the row instead if it was entered by mistake.'
            )
        return attrs


# ---------------------------------------------------------------------------
# Public-facing serializer — limited fields, no internal IDs
# ---------------------------------------------------------------------------
class PublicInspectionSummarySerializer(serializers.ModelSerializer):
    """Public-facing inspection summary — buyer-visible fields only.

    Deliberately strips inspector identity (inspected_by) and free-form
    notes. Buyers see structured condition signals + title status.
    """
    class Meta:
        model  = SalvageInspection
        fields = [
            'id', 'inspected_at',
            'runs_drives', 'mileage_km', 'key_present',
            'engine_status', 'transmission_status',
            'airbags_deployed', 'salvage_title_status',
            'body_panels_jsonb',
        ]
        read_only_fields = fields


class PublicSalvageItemSerializer(serializers.ModelSerializer):
    """What a member of the public sees on the salvage portal (no auth).

    Deliberately strips internal references (created_by, claim_number) and
    cost basis (reserve_price). Only asking_price is shown.

    2026-05-24: also surfaces the latest SalvageInspection summary so
    buyers can assess condition before bidding.
    """
    category_name      = serializers.CharField(source='category.name', read_only=True)
    vehicle_brand_name = serializers.CharField(source='vehicle_brand.name', read_only=True)
    vehicle_model_name = serializers.CharField(source='vehicle_model.name', read_only=True)
    primary_image      = serializers.SerializerMethodField()
    inspection         = serializers.SerializerMethodField()

    class Meta:
        model  = SalvageItem
        fields = [
            'id', 'item_code', 'part_name', 'part_description',
            'category_name', 'vehicle_brand_name', 'vehicle_model_name',
            'vehicle_year', 'vehicle_colour',
            'condition', 'asking_price',
            'primary_image',
            'inspection',
        ]
        read_only_fields = fields

    def get_primary_image(self, obj):
        # Streaming url, never img.image.url — see photo_url() above.
        return photo_url(obj.images.order_by('ordering', 'created_at').first(),
                         self.context.get('request'))

    def get_inspection(self, obj):
        """Latest inspection or None. Falls back gracefully if the
        reverse manager isn't available (e.g. mid-migration)."""
        try:
            insp = obj.inspections.order_by('-inspected_at').first()
        except Exception:                                      # noqa: BLE001
            return None
        if insp is None:
            return None
        return PublicInspectionSummarySerializer(insp).data


# ---------------------------------------------------------------------------
# Buyer quotes
# ---------------------------------------------------------------------------
class BuyerQuoteCreateSerializer(serializers.ModelSerializer):
    """Public-facing offer submission. No auth required.

    Used by the buyer.html-style storefront. The view layer captures IP +
    User-Agent into submitter_ip / submitter_ua.
    """
    class Meta:
        model  = BuyerQuote
        fields = [
            'item', 'buyer_name', 'buyer_email', 'buyer_phone',
            'buyer_company', 'offered_price', 'message',
        ]

    def validate_offered_price(self, value):
        if value <= 0:
            raise serializers.ValidationError('Offered price must be > 0.')
        return value


class BuyerQuoteSerializer(serializers.ModelSerializer):
    """Internal serializer for staff review."""
    item_code  = serializers.CharField(source='item.item_code', read_only=True)
    part_name  = serializers.CharField(source='item.part_name', read_only=True)
    reviewer   = serializers.CharField(source='reviewed_by.username', read_only=True)

    class Meta:
        model  = BuyerQuote
        fields = [
            'id', 'item', 'item_code', 'part_name',
            'buyer_name', 'buyer_email', 'buyer_phone', 'buyer_company',
            'offered_price', 'message',
            'status', 'counter_price',
            'reviewed_by', 'reviewer', 'review_notes', 'reviewed_at',
            'created_at',
        ]
        read_only_fields = ['reviewed_by', 'reviewer', 'reviewed_at', 'created_at']


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------
class SaleSerializer(serializers.ModelSerializer):
    item_code   = serializers.CharField(source='item.item_code', read_only=True)
    part_name   = serializers.CharField(source='item.part_name', read_only=True)
    approver    = serializers.CharField(source='approved_by.username', read_only=True)
    seller      = serializers.CharField(source='sold_by.username', read_only=True)

    class Meta:
        model  = Sale
        fields = [
            'id', 'item', 'item_code', 'part_name',
            'buyer_quote',
            'buyer_name', 'buyer_phone', 'buyer_email',
            'sale_price', 'payment_method', 'payment_ref',
            'approved_by', 'approver',
            'sold_by', 'seller',
            'sale_date', 'notes',
            'journal_entry',
            'created_at',
        ]


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------
class SalvageApprovalSerializer(serializers.ModelSerializer):
    item_code      = serializers.CharField(source='item.item_code', read_only=True)
    requester_name = serializers.CharField(source='requested_by.username', read_only=True)
    approver_name  = serializers.CharField(source='approved_by.username', read_only=True)

    class Meta:
        model  = SalvageApproval
        fields = [
            'id', 'kind', 'item', 'item_code', 'buyer_quote', 'sale',
            'requested_by', 'requester_name',
            'approved_by', 'approver_name',
            'status', 'requested_amount', 'threshold_amount',
            'notes', 'resolved_at', 'created_at',
        ]
