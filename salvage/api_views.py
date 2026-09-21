"""salvage/api_views.py — salvage portal endpoints.

Phase 1 (existing): read-only inventory + masterdata.
Phase 2 (2026-05-18): BuyerQuote create (public + staff review), Sale
record, SalvageApproval queue.
Photos (2026-09-16): upload / remove / make-main on an item, plus the
streaming photo view that actually makes them visible — see salvage_photo().
"""
import os

from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from .models import (
    BuyerQuote, PartCategory, Sale, SalvageApproval, SalvageImage, SalvageItem,
    VehicleBrand, VehicleModel,
)
from core.mixins import CompanyScopedViewSetMixin
from .permissions import IsSalvageUser, user_can_access_salvage
from .services import (
    maybe_create_approval_for_quote,
    maybe_create_approval_for_sale,
    post_sale_to_gl,
    suggest_reserve,
)
from .serializers import (
    BuyerQuoteCreateSerializer,
    BuyerQuoteSerializer,
    PartCategorySerializer,
    PublicSalvageItemSerializer,
    SaleSerializer,
    SalvageApprovalSerializer,
    SalvageImageSerializer,
    SalvageItemDetailSerializer,
    SalvageItemEditSerializer,
    SalvageItemListSerializer,
    VehicleBrandSerializer,
    VehicleModelSerializer,
)


# A real UUID, not [0-9a-fA-F-]{36} — that also matched 36 dashes, and a
# pk of '------...' makes get_object_or_404 raise Django's ValidationError,
# which DRF does not translate into a 404. It answered 500.
_UUID_RE = (r'(?P<image_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}'
            r'-[0-9a-f]{4}-[0-9a-f]{12})')


# ---------------------------------------------------------------------------
# Inventory (auth-gated, full detail)
# ---------------------------------------------------------------------------
class SalvageItemViewSet(CompanyScopedViewSetMixin,
                          mixins.ListModelMixin,
                          mixins.RetrieveModelMixin,
                          mixins.CreateModelMixin,
                          mixins.UpdateModelMixin,
                          viewsets.GenericViewSet):
    """Read + create + safe edit + void.

    Kgosi Seboko asked (2026-09-18) for edit + delete on inventory to fix
    mistakes. Approach:
      * PATCH allows a WHITELIST of typo fields (see SalvageItemEditSerializer);
        item_code / company / cost_basis / intake_journal_entry stay frozen so
        the audit trail is always reconstructable.
      * DELETE is deliberately absent — the `void` action posts a reversing JE
        and flips the row to status=voided instead, so nothing is silently
        removed from the ledger.
    """

    permission_classes = [IsSalvageUser]
    queryset           = SalvageItem.objects.select_related(
                            'category', 'vehicle_brand', 'vehicle_model', 'company',
                         ).prefetch_related('images', 'buyer_quotes').order_by('-created_at')
    filter_backends    = [filters.SearchFilter, filters.OrderingFilter]
    search_fields      = [
        'item_code', 'part_name', 'part_description',
        'claim_number', 'policy_number', 'vin_number',
    ]
    ordering_fields    = ['created_at', 'asking_price', 'item_code']

    def get_queryset(self):
        # super() = CompanyScopedViewSetMixin: strict entity isolation
        # (gates the requested ?company= against the user's UserCompanyAccess
        # and, with no param, scopes to their allowed set). Replaces the old
        # resolve_company_id_param call, which did NOT enforce isolation —
        # a scoped user could read another entity's salvage. (Audit 2026-06-20)
        qs = super().get_queryset()
        for f in ('status', 'condition'):
            v = self.request.query_params.get(f)
            if v:
                qs = qs.filter(**{f: v})
        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return SalvageItemListSerializer
        if self.action in ('update', 'partial_update'):
            return SalvageItemEditSerializer
        return SalvageItemDetailSerializer

    def update(self, request, *args, **kwargs):
        # Force PATCH semantics — a full PUT that omits fields would clear
        # everything the whitelist deliberately kept off. Kgosi's flow is
        # always "fix one field", never "rewrite the whole row".
        kwargs['partial'] = True
        return super().update(request, *args, **kwargs)

    def perform_create(self, serializer):
        """Save the item then auto-post the intake JE.

        CFO directive 2026-05-18: an insurer's salvage stock is an asset
        on the balance sheet from the moment the claim is written off.
        post_intake_to_gl creates the Dr Inventory / Cr Recoveries entry
        at item.cost_basis. Skips when cost_basis is zero.

        Kgosi bug 87a249f3 (2026-09-18): Salvage is recorded under Veritas
        Capital (VCM) only. If company is omitted, set it to VCM. If a
        different company is given, raise a validation error.
        """
        from .services import get_salvage_company, post_intake_to_gl
        from rest_framework.exceptions import ValidationError

        # Enforce Veritas Capital (VCM) as the only salvage company.
        from core.models import Company, allowed_company_ids
        from rest_framework.exceptions import PermissionDenied
        company_id = serializer.validated_data.get('company')
        try:
            veritas = get_salvage_company()
        except Company.DoesNotExist:
            raise ValidationError(
                'Salvage system not configured: Veritas Capital (VCM) not found.'
            )
        # Forcing the entity must not widen anyone's reach: the caller must
        # hold Veritas themselves (both live salvage users do, 18-Sep).
        allowed = allowed_company_ids(self.request.user)
        if allowed != {'*'} and str(veritas.pk) not in {str(x) for x in allowed}:
            raise PermissionDenied(
                'Salvage is recorded under Veritas Capital, and you do not have '
                'access to Veritas Capital.')

        if company_id is None:
            # No company specified — set to Veritas.
            serializer.validated_data['company'] = veritas
        elif company_id != veritas:
            # A different company was given — reject.
            company_code = getattr(company_id, 'code', str(company_id))
            raise ValidationError(
                f'Salvage is recorded under Veritas Capital only. '
                f'Cannot create under {company_code}.'
            )

        item = serializer.save(
            created_by=self.request.user,
            received_by=self.request.user,
        )
        try:
            post_intake_to_gl(item, user=self.request.user)
        except Exception:                                      # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception(
                'Salvage intake JE failed for item %s — row saved, GL untouched.',
                item.pk,
            )

    @action(detail=True, methods=['get'], url_path='suggest-reserve')
    def suggest_reserve(self, request, pk=None):
        """GET /api/v1/salvage-items/<id>/suggest-reserve/

        Returns a non-binding reserve-price suggestion sourced from:
          - claims gross amount for the linked claim_number, AND
          - the category's expected_recovery_pct (default 30%).

        Response:
            {
              "item_id": "<uuid>",
              "claim_number": "CLM-2026-...",
              "category": "<name|null>",
              "expected_recovery_pct": "0.3000",
              "suggested_reserve": "12345.67",
              "current_reserve": "10000.00"
            }

        The endpoint does NOT mutate the item — the CFO / yard manager
        applies it (or doesn't) via the regular PATCH flow.
        """
        item = self.get_object()
        from decimal import Decimal as _D
        pct = _D('0.30')
        cat_name = None
        if item.category_id and item.category is not None:
            cat_name = item.category.name
            if item.category.expected_recovery_pct is not None:
                pct = _D(item.category.expected_recovery_pct)

        suggested = suggest_reserve(item)
        return Response({
            'item_id':               str(item.pk),
            'claim_number':          item.claim_number or '',
            'category':              cat_name,
            'expected_recovery_pct': f'{pct:.4f}',
            'suggested_reserve':     f'{suggested:.2f}',
            'current_reserve':       f'{_D(item.reserve_price or 0):.2f}',
        })

    @action(detail=True, methods=['post'], url_path='void')
    def void(self, request, pk=None):
        """POST /api/v1/salvage-items/<id>/void/

        Soft-delete for a data-entry mistake. Kgosi's use case (2026-09-18)
        is "I typed the wrong claim / wrong VIN and want the row gone".

        Guard rails:
          * A row that has ever been sold, disposed or scrapped is frozen —
            those states have already posted their own JEs and buyer records,
            so voiding would leave the GL and the buyer ledger inconsistent.
          * The row is not deleted; status flips to VOIDED, the reason is
            captured, and a reversing JE is posted so the intake DR / CR is
            unwound. Reports that key off status='available' already exclude
            voided rows for free.
          * `reason` is required — an unaudited void is worse than a mistaken
            row that can be re-voided with context.
        """
        from .services import post_void_to_gl
        item = self.get_object()
        reason = (request.data.get('reason') or '').strip()
        if not reason:
            return Response(
                {'detail': 'A reason is required for a void — say why the row is being cancelled.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if item.status in (
            SalvageItem.Status.SOLD,
            SalvageItem.Status.DISPOSED,
            SalvageItem.Status.SCRAPPED,
        ):
            return Response(
                {'detail': f'Item is {item.get_status_display()} — cannot void a terminal row. '
                           'Book a reversal against the Sale / Disposal instead.'},
                status=status.HTTP_409_CONFLICT,
            )
        if item.status == SalvageItem.Status.VOIDED:
            return Response(
                {'detail': 'Item is already voided.'},
                status=status.HTTP_409_CONFLICT,
            )
        with transaction.atomic():
            stamp = timezone.now().strftime('%Y-%m-%d %H:%M')
            item.notes = (
                (item.notes + '\n' if item.notes else '')
                + f'VOIDED {stamp} by {request.user}: {reason}'
            )[:4000]
            item.status = SalvageItem.Status.VOIDED
            item.save(update_fields=['status', 'notes', 'updated_at'])
            try:
                post_void_to_gl(item, reason=reason, user=request.user)
            except Exception:                                    # noqa: BLE001
                import logging
                logging.getLogger(__name__).exception(
                    'Salvage void JE failed for item %s — row voided, GL untouched.',
                    item.pk,
                )
        return Response(
            SalvageItemDetailSerializer(item, context={'request': request}).data,
            status=status.HTTP_200_OK,
        )

    # ------------------------------------------------------------------
    # Photos — Bharath Balasubramanian, 16-Sep-2026: "We must be able to
    # attach photos to each salvage on our salvage management in OMNI."
    #
    # SalvageImage, the detail-page gallery and primary_image have all
    # existed since Phase 1. The missing piece was only ever the write
    # path — frontend/.../salvage/inventory/new/page.tsx said so in as
    # many words ("Phase 2 (deferred): photo upload UI wiring"), which is
    # why prod held 52 salvage items and 0 photos: nothing could put one
    # there. These three actions are that write path and nothing more.
    #
    # Photos are memorandum data — they post no journal and move no money
    # — so they carry the same IsSalvageUser gate as item create above
    # rather than a new maker-checker of their own.
    # ------------------------------------------------------------------
    MAX_PHOTO_BYTES     = 10 * 1024 * 1024          # 10 MB per photo
    MAX_PHOTOS_PER_ITEM = 20
    # HEIC/HEIF are deliberately NOT here, and refusing them is the kinder
    # option. Chrome, Edge and Firefox cannot render HEIC, so an iPhone photo
    # copied onto a PC would upload with a clean 201 and then show as a broken
    # tile in the gallery, the inventory list AND the public portal, with no
    # error anywhere to explain it. Refused at the door, the person gets a
    # sentence they can act on. (Fable, 16-Sep-2026.)
    ALLOWED_PHOTO_TYPES = {
        'image/jpeg', 'image/pjpeg', 'image/png', 'image/webp',
    }

    def _photos_payload(self, item):
        """The item's photos, in display order. Returned by all three actions
        so the caller never has to refetch the whole item.

        Queried through the MANAGER, never ``item.images.all()``: get_object()
        comes off a queryset carrying prefetch_related('images'), so the
        related cache was populated BEFORE these writes and item.images.all()
        replays that stale, empty list. The first run of this file's tests
        reported count=0 straight after a successful 201 for exactly that
        reason. Every photo query below does the same for the same reason.
        """
        # SalvageImage.Meta.ordering is already ['ordering', 'created_at'].
        imgs = list(SalvageImage.objects.filter(item=item))
        return {
            'count':  len(imgs),
            'images': SalvageImageSerializer(
                imgs, many=True, context=self.get_serializer_context()).data,
        }

    @action(detail=True, methods=['post'], url_path='images',
            parser_classes=[MultiPartParser, FormParser])
    def upload_images(self, request, pk=None):
        """POST /api/v1/salvage-items/<id>/images/ — multipart.

        ``images``  one or many files (repeat the field per file).
        ``caption`` optional, applied to every file in this batch.
        """
        item  = self.get_object()          # company-scoped: another entity 404s
        files = request.FILES.getlist('images')
        if not files:
            return Response(
                {'detail': 'Attach at least one photo in the "images" field.'},
                status=status.HTTP_400_BAD_REQUEST)

        existing = SalvageImage.objects.filter(item=item).count()
        if existing + len(files) > self.MAX_PHOTOS_PER_ITEM:
            return Response(
                {'detail': f'{self.MAX_PHOTOS_PER_ITEM} photos is the limit for one '
                           f'item — this one already has {existing}.'},
                status=status.HTTP_400_BAD_REQUEST)

        # Validate EVERY file before writing ANY of them, so a bad third
        # file cannot leave the first two half-attached.
        for f in files:
            ctype = (getattr(f, 'content_type', '') or '').lower()
            if ctype not in self.ALLOWED_PHOTO_TYPES:
                return Response(
                    {'detail': f'"{f.name}" is not a photo Omni can show. Use JPG, PNG or WEBP.'},
                    status=status.HTTP_400_BAD_REQUEST)
            if f.size > self.MAX_PHOTO_BYTES:
                return Response(
                    {'detail': f'"{f.name}" is {f.size / 1048576:.1f} MB — the limit '
                               f'is 10 MB per photo.'},
                    status=status.HTTP_400_BAD_REQUEST)

        caption = (request.data.get('caption') or '').strip()[:200]
        # Append after what is already there, so the first photo ever
        # uploaded stays the primary one unless somebody changes it.
        with transaction.atomic():
            for offset, f in enumerate(files):
                SalvageImage.objects.create(
                    item=item, image=f, caption=caption, ordering=existing + offset,
                )

        return Response(self._photos_payload(item), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['delete'],
            url_path=rf'images/{_UUID_RE}')
    def delete_image(self, request, pk=None, image_id=None):
        """DELETE /api/v1/salvage-items/<id>/images/<image_id>/"""
        item = self.get_object()
        img  = get_object_or_404(SalvageImage, pk=image_id, item=item)
        with transaction.atomic():
            img.delete()
            # Close the gap so ordering stays 0..n-1 and "primary" keeps
            # meaning "ordering 0" after any number of deletes.
            for i, remaining in enumerate(SalvageImage.objects.filter(item=item)):
                if remaining.ordering != i:
                    SalvageImage.objects.filter(pk=remaining.pk).update(ordering=i)
        return Response(self._photos_payload(item))

    @action(detail=True, methods=['post'],
            url_path=rf'images/{_UUID_RE}/primary')
    def set_primary_image(self, request, pk=None, image_id=None):
        """POST /api/v1/salvage-items/<id>/images/<image_id>/primary/

        Makes this the photo the inventory list and every summary shows.
        primary_image is ``images.order_by('ordering').first()``, so being
        primary is simply holding ordering 0.
        """
        item = self.get_object()
        img  = get_object_or_404(SalvageImage, pk=image_id, item=item)
        others = list(SalvageImage.objects.filter(item=item).exclude(pk=img.pk))
        with transaction.atomic():
            SalvageImage.objects.filter(pk=img.pk).update(ordering=0)
            for i, o in enumerate(others, start=1):
                SalvageImage.objects.filter(pk=o.pk).update(ordering=i)
        return Response(self._photos_payload(item))


# ---------------------------------------------------------------------------
# Serving a salvage photo — 16-Sep-2026
# ---------------------------------------------------------------------------
# THIS VIEW IS WHY THE UPLOAD IS ACTUALLY WORTH ANYTHING. `/media/` is NOT
# served in this deployment: alpha_finance/urls.py appends Django's
# `static(MEDIA_URL, ...)` helper, which only serves files when DEBUG is True,
# and prod runs DEBUG=False. claims/vault_views.py records the same trap in as
# many words — "/media/ is not served in this deployment — a raw file.url
# 404s". So without this view every photo would upload perfectly, report 201,
# and then show as a broken image: built, deployed, and useless.
#
# WHY AllowAny, and why that is not a hole:
#   * The public buy-salvage portal already renders `primary_image` in a plain
#     <img>/next/Image with no credentials at all (frontend/src/app/buy-salvage
#     /page.tsx), so an unauthenticated photo URL is this module's existing
#     design, not a new decision.
#   * The SPA authenticates with a bearer token held in localStorage, so an
#     <img src> carries no Authorization header — the second trap vault_views
#     records. An authenticated endpoint would 401 in every gallery tile.
#   * The photo id is a random UUID and is never listed anywhere a stranger
#     can reach, so holding the link IS the capability — the same pattern as
#     the PO and quotation verify pages already in alpha_finance/urls.py.
#   * The subject matter is a damaged vehicle being advertised for sale. No
#     personal data, no claim number, no policy number: the photo row carries
#     an image, a caption and a sort order and nothing else.
# If that trade ever stops being right, this is the ONE place to change it.
@api_view(['GET'])
@permission_classes([AllowAny])
def salvage_photo(request, image_id):
    """GET /api/v1/salvage/photo/<image_id>/ — stream one salvage photo."""
    img = SalvageImage.objects.filter(pk=image_id).first()
    if img is None or not img.image:
        raise Http404
    try:
        handle = img.image.open('rb')
    except (FileNotFoundError, OSError):
        # The row outlived its file (a restored DB against a fresh volume).
        # A 404 is the honest answer; a 500 would read as the site being down.
        raise Http404
    ext = os.path.splitext(img.image.name)[1].lower()
    content_type = {
        '.png': 'image/png', '.webp': 'image/webp',
        '.heic': 'image/heic', '.heif': 'image/heif',
    }.get(ext, 'image/jpeg')
    # filename= is a PRIVACY control, not a nicety: Django 5 otherwise puts
    # the stored file's own basename in Content-Disposition, and that is the
    # name the uploader chose — "CLM-2026-0123 B123ABC front.jpg" would
    # publish a claim number and a plate on an open url. (Fable, 16-Sep.)
    response = FileResponse(handle, content_type=content_type,
                            filename=f'{img.pk}{ext or '.jpg'}')
    # Immutable: a photo row's file never changes in place — a replacement is
    # a new row with a new id — so it is safe to cache hard.
    response['Cache-Control'] = 'public, max-age=604800, immutable'
    return response


class PartCategoryViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsSalvageUser]
    queryset           = PartCategory.objects.filter(is_active=True).order_by('name')
    serializer_class   = PartCategorySerializer
    pagination_class   = None


class VehicleBrandViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsSalvageUser]
    queryset           = VehicleBrand.objects.filter(is_active=True).order_by('name')
    serializer_class   = VehicleBrandSerializer
    pagination_class   = None


class VehicleModelViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsSalvageUser]
    queryset           = VehicleModel.objects.select_related('brand') \
                                              .filter(is_active=True) \
                                              .order_by('brand__name', 'name')
    serializer_class   = VehicleModelSerializer
    pagination_class   = None

    def get_queryset(self):
        qs = super().get_queryset()
        brand = self.request.query_params.get('brand')
        if brand:
            qs = qs.filter(brand_id=brand)
        return qs


class SalvageAccessProbeView(APIView):
    """GET /api/v1/salvage/me-can-access/ — sidebar probe."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        allowed = user_can_access_salvage(request.user, request=request)
        return Response({
            'can_access': allowed,
            'reason': (
                'VCM/ADIC user or CFO/superuser' if allowed
                else 'restricted to Veritas (VCM) and Alpha Direct (ADIC)'
            ),
        })


# ---------------------------------------------------------------------------
# Buyer quotes (staff review)
# ---------------------------------------------------------------------------
class BuyerQuoteViewSet(viewsets.ModelViewSet):
    """Staff review queue for offers submitted via the public storefront."""
    permission_classes = [IsSalvageUser]
    queryset           = BuyerQuote.objects.select_related(
                             'item', 'reviewed_by',
                         ).order_by('-created_at')
    serializer_class   = BuyerQuoteSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        for f in ('status',):
            v = self.request.query_params.get(f)
            if v:
                qs = qs.filter(**{f: v})
        item = self.request.query_params.get('item')
        if item:
            qs = qs.filter(item_id=item)
        return qs

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def review(self, request, pk=None):
        """POST /api/v1/salvage/buyer-quotes/<id>/review/

        Body: { decision: 'accepted'|'rejected'|'countered',
                counter_price?: number, notes?: string }
        """
        quote = self.get_object()
        decision = (request.data.get('decision') or '').lower()
        if decision not in {'accepted', 'rejected', 'countered'}:
            return Response(
                {'detail': 'decision must be accepted, rejected, or countered.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        quote.status = decision
        quote.review_notes = request.data.get('notes', '') or ''
        quote.reviewed_by = request.user
        quote.reviewed_at = timezone.now()
        if decision == 'countered':
            cp = request.data.get('counter_price')
            if cp in (None, ''):
                return Response(
                    {'detail': 'counter_price is required when countering.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            quote.counter_price = cp

        quote.save()

        # When a quote is accepted, flip the item to QUOTED so other buyers
        # see it isn't freely available anymore. The sale step later moves
        # it to SOLD.
        approval = None
        if decision == 'accepted':
            SalvageItem.objects.filter(pk=quote.item_id).update(
                status=SalvageItem.Status.QUOTED,
            )
            # Threshold-driven approval — see salvage.services for the rules.
            approval = maybe_create_approval_for_quote(quote, user=request.user)

        data = BuyerQuoteSerializer(quote).data
        data['approval_required'] = bool(approval)
        if approval:
            data['approval'] = SalvageApprovalSerializer(approval).data
        return Response(data)


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------
class SaleViewSet(viewsets.ModelViewSet):
    permission_classes = [IsSalvageUser]
    queryset           = Sale.objects.select_related(
                             'item', 'buyer_quote', 'approved_by', 'sold_by',
                         ).order_by('-sale_date', '-created_at')
    serializer_class   = SaleSerializer

    def perform_create(self, serializer):
        with transaction.atomic():
            sale = serializer.save(sold_by=self.request.user)
            SalvageItem.objects.filter(pk=sale.item_id).update(
                status=SalvageItem.Status.SOLD,
                sold_date=sale.sale_date,
            )
            # Threshold approval: sale < reserve_price or > config threshold.
            maybe_create_approval_for_sale(sale, user=self.request.user)
            # GL posting: DR cash, CR salvage income. Silent no-op if the
            # configured account codes aren't in the CoA.
            try:
                post_sale_to_gl(sale, user=self.request.user)
            except Exception:  # noqa: BLE001
                # Sale row stays; the GL failure is logged inside the service.
                pass


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------
class SalvageApprovalViewSet(viewsets.ModelViewSet):
    permission_classes = [IsSalvageUser]
    queryset           = SalvageApproval.objects.select_related(
                             'item', 'requested_by', 'approved_by',
                         ).order_by('-created_at')
    serializer_class   = SalvageApprovalSerializer

    def perform_create(self, serializer):
        serializer.save(requested_by=self.request.user)

    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        """POST /api/v1/salvage/approvals/<id>/resolve/
        Body: { decision: 'approved'|'rejected', notes?: string }"""
        appr = self.get_object()
        decision = (request.data.get('decision') or '').lower()
        if decision not in {'approved', 'rejected'}:
            return Response(
                {'detail': 'decision must be approved or rejected.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        appr.status = decision
        appr.approved_by = request.user
        appr.resolved_at = timezone.now()
        notes = request.data.get('notes', '')
        if notes:
            appr.notes = (appr.notes + '\n---\n' + notes) if appr.notes else notes
        appr.save()
        return Response(SalvageApprovalSerializer(appr).data)


# ---------------------------------------------------------------------------
# PUBLIC STOREFRONT — no auth required
# ---------------------------------------------------------------------------
class PublicStorefrontThrottle(AnonRateThrottle):
    rate = '60/minute'


class PublicSalvageListView(APIView):
    """GET /api/v1/salvage/public/items/

    Returns the catalogue of available salvage items for the public-
    facing storefront. No auth required. Read-only.

    Filters: ?q=... (full-text), ?brand=<id>, ?category=<id>, ?max_price=
    """
    permission_classes = [AllowAny]
    throttle_classes   = [PublicStorefrontThrottle]
    authentication_classes = []

    def get(self, request):
        qs = SalvageItem.objects.filter(
            status=SalvageItem.Status.AVAILABLE,
        ).select_related('category', 'vehicle_brand', 'vehicle_model') \
         .prefetch_related('images')

        q = request.query_params.get('q')
        if q:
            qs = qs.filter(part_name__icontains=q) \
              | qs.filter(part_description__icontains=q)
        brand = request.query_params.get('brand')
        if brand:
            qs = qs.filter(vehicle_brand_id=brand)
        category = request.query_params.get('category')
        if category:
            qs = qs.filter(category_id=category)
        max_price = request.query_params.get('max_price')
        if max_price:
            try:
                qs = qs.filter(asking_price__lte=float(max_price))
            except (TypeError, ValueError):
                pass

        # Cap to 200 items — no full enumeration of inventory exposed.
        qs = qs.order_by('-created_at')[:200]
        ser = PublicSalvageItemSerializer(qs, many=True, context={'request': request})
        return Response({'count': len(ser.data), 'results': ser.data})


class PublicSalvageDetailView(APIView):
    """GET /api/v1/salvage/public/items/<item_code>/"""
    permission_classes = [AllowAny]
    throttle_classes   = [PublicStorefrontThrottle]
    authentication_classes = []

    def get(self, request, item_code):
        item = get_object_or_404(
            SalvageItem.objects.select_related(
                'category', 'vehicle_brand', 'vehicle_model',
            ).prefetch_related('images'),
            item_code=item_code,
            status=SalvageItem.Status.AVAILABLE,
        )
        return Response(
            PublicSalvageItemSerializer(item, context={'request': request}).data
        )


class PublicSalvageQuoteSubmitView(APIView):
    """POST /api/v1/salvage/public/quotes/

    Public buyer submits an offer. No auth required. Captures IP + UA
    on the quote for audit / abuse triage.
    """
    permission_classes = [AllowAny]
    throttle_classes   = [PublicStorefrontThrottle]
    authentication_classes = []

    def post(self, request):
        ser = BuyerQuoteCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        # Re-fetch the item to ensure it's still available — protects
        # against a race where two buyers submit on the same item.
        item = SalvageItem.objects.filter(
            pk=ser.validated_data['item'].pk,
            status__in=[SalvageItem.Status.AVAILABLE, SalvageItem.Status.QUOTED],
        ).first()
        if not item:
            return Response(
                {'detail': 'Item is no longer available for quotation.'},
                status=status.HTTP_409_CONFLICT,
            )

        ip = (request.META.get('HTTP_X_FORWARDED_FOR', '')
              .split(',')[0].strip() or request.META.get('REMOTE_ADDR'))
        ua = request.META.get('HTTP_USER_AGENT', '')[:400]
        quote = ser.save(submitter_ip=ip or None, submitter_ua=ua)

        return Response(
            {
                'id': str(quote.pk),
                'received_at': quote.created_at.isoformat(),
                'message': ('Thank you. Your offer has been received and will '
                            'be reviewed within one business day. We will '
                            'contact you on the phone or email you provided.'),
            },
            status=status.HTTP_201_CREATED,
        )
