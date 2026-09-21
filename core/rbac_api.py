"""
core/rbac_api.py — DRF endpoints for the RBAC layer.

Read-mostly endpoints (roles, permissions, users-with-roles, audit log) are
visible to any authenticated user with the `roles.view` permission OR the
legacy `can_administer_users` admin flag (so the existing /settings/users
page keeps working during the rollout).

Write endpoints (assign / revoke) route through `core.rbac_service` which
enforces the governance rules — never call ORM .create() on UserRoleAssignment
directly from a view.

URL space (registered in alpha_finance.api_router):
    /api/v1/rbac/roles/                 GET, GET-detail
    /api/v1/rbac/permissions/           GET
    /api/v1/rbac/assignments/           GET (list, with ?user=, ?role=, ?active=true)
    /api/v1/rbac/assignments/           POST { user, role, justification, scope_department?, expires_at? }
    /api/v1/rbac/assignments/{id}/      GET, DELETE-replaced-by-revoke
    /api/v1/rbac/assignments/{id}/revoke/  POST { reason }
    /api/v1/rbac/users/                 GET — every user + their active assignments + max authority level
    /api/v1/rbac/me/                    GET — caller's effective roles + permissions + authority level
    /api/v1/rbac/audit/                 GET — recent RBAC mutations (read-only AuditLog slice)
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import (
    PermissionDenied as DjangoPermissionDenied,
    ValidationError as DjangoValidationError,
)
from django.db.models.functions import Lower
from django.utils.dateparse import parse_datetime
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.serializers import (
    BooleanField,
    CharField,
    DateTimeField,
    IntegerField,
    ListField,
    ModelSerializer,
    PrimaryKeyRelatedField,
    Serializer,
    SerializerMethodField,
)

from core.models import (
    AuditLog,
    Permission,
    Role,
    UserRoleAssignment,
    get_user_profile,
    user_has_permission,
    user_max_authority_level,
    user_roles,
)
from core.rbac_service import assign_role, revoke_role
from core.access_parse import parse_access_request

User = get_user_model()


# ---------------------------------------------------------------------------
# Permission classes
# ---------------------------------------------------------------------------

class HasRolesView(BasePermission):
    """Either the new `roles.view` perm OR legacy admin (during transition)."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        if user_has_permission(request.user, 'roles.view'):
            return True
        profile = get_user_profile(request.user)
        return bool(profile and profile.can_administer_users)


class HasRolesAssign(BasePermission):
    """Either the `roles.assign` perm OR legacy admin."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        if user_has_permission(request.user, 'roles.assign'):
            return True
        profile = get_user_profile(request.user)
        return bool(profile and profile.can_administer_users)


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

class PermissionSerializer(ModelSerializer):
    class Meta:
        model  = Permission
        fields = ['code', 'category', 'description', 'is_active']


class RoleSerializer(ModelSerializer):
    permission_count = SerializerMethodField()
    department_label = SerializerMethodField()

    class Meta:
        model  = Role
        fields = [
            'id', 'code', 'name', 'description', 'level',
            'department', 'department_label',
            'is_system', 'is_active',
            'permission_count',
        ]

    def get_permission_count(self, obj):
        return obj.permissions.count()

    def get_department_label(self, obj):
        return obj.get_department_display() if obj.department else None


class RoleDetailSerializer(RoleSerializer):
    permissions = PermissionSerializer(many=True, read_only=True)

    class Meta(RoleSerializer.Meta):
        fields = RoleSerializer.Meta.fields + ['permissions']


class UserMiniSerializer(ModelSerializer):
    class Meta:
        model  = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'is_active']


class AssignmentSerializer(ModelSerializer):
    user             = UserMiniSerializer(read_only=True)
    role             = RoleSerializer(read_only=True)
    assigned_by      = UserMiniSerializer(read_only=True)
    revoked_by       = UserMiniSerializer(read_only=True)
    is_currently_active = BooleanField(read_only=True)
    effective_department = CharField(read_only=True)

    class Meta:
        model  = UserRoleAssignment
        fields = [
            'id',
            'user', 'role',
            'scope_department', 'effective_department',
            'justification', 'notes',
            'assigned_by', 'assigned_at',
            'expires_at',
            'revoked_by', 'revoked_at', 'revocation_reason',
            'is_currently_active',
        ]


class AssignmentCreateSerializer(ModelSerializer):
    """Write-side serializer — accepts user_id + role_id; uses service for write."""

    user             = PrimaryKeyRelatedField(queryset=User.objects.all())
    role             = PrimaryKeyRelatedField(queryset=Role.objects.all())

    class Meta:
        model  = UserRoleAssignment
        fields = ['user', 'role', 'scope_department', 'justification', 'notes', 'expires_at']


class BulkAssignSerializer(Serializer):
    """Assign ONE role to MANY users in a single confirmed action.

    Targets are given as explicit `user_ids` and/or `emails` (case-insensitive,
    e.g. a pasted column / CSV). The view resolves both into a deduped user set,
    then loops core.rbac_service.assign_role — so every grant is governed +
    audit-logged individually, with the caller as the grantor. No autonomous
    grant: the caller (a human with roles.assign) initiates the batch.
    """
    role             = PrimaryKeyRelatedField(queryset=Role.objects.all())
    user_ids         = ListField(child=IntegerField(), required=False, default=list)
    emails           = ListField(child=CharField(), required=False, default=list)
    scope_department = CharField(required=False, allow_blank=True, allow_null=True, default=None)
    justification    = CharField(required=False, allow_blank=True, default='')
    expires_at       = DateTimeField(required=False, allow_null=True, default=None)


def _exc_msg(exc):
    """Flatten a DRF/Django exception into a short human string."""
    d = getattr(exc, 'detail', None) or getattr(exc, 'messages', None) or str(exc)
    if isinstance(d, (list, tuple)):
        return '; '.join(str(x) for x in d)
    return str(d)


def _resolve_targets(user_ids, emails):
    """Resolve user_ids + emails (case-insensitive) into a deduped [User] list,
    plus the emails that matched no user. Pure read — no mutation."""
    ids = [int(x) for x in (user_ids or []) if str(x).strip()]
    addrs = [str(e).strip().lower() for e in (emails or []) if str(e).strip()]
    found = {}
    if ids:
        for u in User.objects.filter(id__in=ids):
            found[u.pk] = u
    matched = set()
    if addrs:
        for u in User.objects.annotate(_le=Lower('email')).filter(_le__in=addrs):
            found[u.pk] = u
            matched.add((u.email or '').strip().lower())
    unmatched = sorted(set(addrs) - matched)
    return list(found.values()), unmatched


def _resolve_names(names):
    """Conservative name -> User resolution for the Aria proposal. A name maps to
    a user only on a SINGLE confident match (normalised full-name or email-local
    prefix); 0 matches -> unmatched, >1 -> ambiguous (operator picks). Read-only."""
    import re as _re

    def _norm(s):
        return _re.sub(r'[^a-z]', '', (s or '').lower())

    idx = []
    for u in User.objects.filter(is_active=True):
        idx.append((_norm(f"{u.first_name} {u.last_name}"),
                    _norm((u.email or '').split('@')[0]), u))
    resolved, ambiguous, unmatched, seen = [], [], [], set()
    for raw in names:
        n = _norm(raw)
        if not n:
            continue
        cands = {}
        for full, elocal, u in idx:
            if (full and (full == n or full.startswith(n) or n.startswith(full))) or \
               (elocal and (elocal == n or elocal.startswith(n) or n.startswith(elocal))):
                cands[u.pk] = u
        cl = list(cands.values())
        if len(cl) == 1:
            if cl[0].pk not in seen:
                seen.add(cl[0].pk)
                resolved.append(cl[0])
        elif len(cl) > 1:
            ambiguous.append({'name': raw, 'candidates': [
                {'id': c.pk, 'email': c.email,
                 'name': (f"{c.first_name} {c.last_name}".strip() or c.username)}
                for c in cl[:6]]})
        else:
            unmatched.append(raw)
    return resolved, ambiguous, unmatched


class UserRoleViewSerializer(ModelSerializer):
    """User list with their active assignments and computed authority."""

    active_assignments     = SerializerMethodField()
    max_authority_level    = SerializerMethodField()
    legacy_title           = SerializerMethodField()

    class Meta:
        model  = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name', 'is_active',
            'last_login', 'date_joined',
            'active_assignments', 'max_authority_level', 'legacy_title',
        ]

    def get_active_assignments(self, obj):
        active = [a for a in obj.role_assignments.select_related('role').all() if a.is_currently_active]
        return AssignmentSerializer(active, many=True).data

    def get_max_authority_level(self, obj):
        return user_max_authority_level(obj)

    def get_legacy_title(self, obj):
        profile = get_user_profile(obj)
        return profile.title if profile else None


class AuditLogSerializer(ModelSerializer):
    user = UserMiniSerializer(read_only=True)

    class Meta:
        model  = AuditLog
        fields = ['id', 'table_name', 'record_id', 'action',
                  'old_values', 'new_values',
                  'user', 'description', 'ip_address',
                  'created_at']


# ---------------------------------------------------------------------------
# ViewSets
# ---------------------------------------------------------------------------

class RoleViewSet(viewsets.ReadOnlyModelViewSet):
    """List + retrieve roles. Custom-role create/edit is deferred to a later phase."""
    queryset = Role.objects.prefetch_related('permissions').order_by('level', 'department', 'name')
    permission_classes = [IsAuthenticated, HasRolesView]
    filter_backends    = [filters.SearchFilter]
    search_fields      = ['code', 'name', 'description']

    def get_serializer_class(self):
        return RoleDetailSerializer if self.action == 'retrieve' else RoleSerializer


class PermissionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Permission.objects.order_by('category', 'code')
    permission_classes = [IsAuthenticated, HasRolesView]
    serializer_class = PermissionSerializer
    filter_backends  = [filters.SearchFilter]
    search_fields    = ['code', 'category', 'description']


class AssignmentViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """
    List / retrieve / create role assignments. Revoke is a custom action so
    we can call rbac_service.revoke_role (audit-logged) rather than DRF's
    default destroy.
    """

    queryset = (
        UserRoleAssignment.objects
        .select_related('user', 'role', 'assigned_by', 'revoked_by')
        .order_by('-assigned_at')
    )
    permission_classes = [IsAuthenticated, HasRolesView]

    def get_permissions(self):
        if self.action == 'create' or self.action == 'revoke':
            return [IsAuthenticated(), HasRolesAssign()]
        return [perm() if isinstance(perm, type) else perm for perm in [IsAuthenticated(), HasRolesView()]]

    def get_serializer_class(self):
        return AssignmentCreateSerializer if self.action == 'create' else AssignmentSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        user_id = self.request.query_params.get('user')
        role_id = self.request.query_params.get('role')
        active  = self.request.query_params.get('active')
        if user_id:
            qs = qs.filter(user_id=user_id)
        if role_id:
            qs = qs.filter(role_id=role_id)
        if active in ('true', '1', 'yes'):
            qs = qs.filter(revoked_at__isnull=True)
        elif active in ('false', '0', 'no'):
            qs = qs.filter(revoked_at__isnull=False)
        return qs

    def create(self, request, *args, **kwargs):
        write_ser = AssignmentCreateSerializer(data=request.data)
        write_ser.is_valid(raise_exception=True)
        payload = write_ser.validated_data
        try:
            assignment = assign_role(
                target_user     = payload['user'],
                role            = payload['role'],
                granted_by      = request.user,
                scope_department= payload.get('scope_department'),
                justification   = payload.get('justification', ''),
                expires_at      = payload.get('expires_at'),
                notes           = payload.get('notes', ''),
                request_ip      = request.META.get('REMOTE_ADDR'),
            )
        except (DjangoPermissionDenied, PermissionDenied) as e:
            return Response({'detail': str(e)}, status=status.HTTP_403_FORBIDDEN)
        except (DjangoValidationError, ValidationError) as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(AssignmentSerializer(assignment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, HasRolesAssign])
    def revoke(self, request, pk=None):
        assignment = self.get_object()
        reason = request.data.get('reason', '')
        try:
            revoke_role(
                assignment=assignment,
                revoked_by=request.user,
                reason=reason,
                request_ip=request.META.get('REMOTE_ADDR'),
            )
        except (DjangoPermissionDenied, PermissionDenied) as e:
            return Response({'detail': str(e)}, status=status.HTTP_403_FORBIDDEN)
        except (DjangoValidationError, ValidationError) as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        assignment.refresh_from_db()
        return Response(AssignmentSerializer(assignment).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='resolve-targets',
            permission_classes=[IsAuthenticated, HasRolesView])
    def resolve_targets(self, request):
        """Preview a bulk batch: resolve user_ids + emails into the affected user
        set, flag emails that matched nobody, and (if a role is given) flag who
        already holds it — so the UI shows exactly who will be granted vs skipped
        BEFORE the operator confirms. Read-only."""
        users, unmatched = _resolve_targets(
            request.data.get('user_ids'), request.data.get('emails'))
        role_id = request.data.get('role')
        already = set()
        if role_id and users:
            already = set(
                UserRoleAssignment.objects
                .filter(role_id=role_id, user__in=users, revoked_at__isnull=True)
                .values_list('user_id', flat=True)
            )
        return Response({
            'resolved':          UserMiniSerializer(users, many=True).data,
            'count':             len(users),
            'unmatched_emails':  unmatched,
            'already_have_role': sorted(already),
        })

    @action(detail=False, methods=['post'], url_path='bulk-assign',
            permission_classes=[IsAuthenticated, HasRolesAssign])
    def bulk_assign(self, request):
        """Assign ONE role to MANY users in a single confirmed action. Loops
        rbac_service.assign_role so each grant is governed + audit-logged with
        request.user as the grantor. Per-user errors (already-holds, justification
        needed, hierarchy) are reported, never silently swallowed; one bad user
        never blocks the rest. NOT autonomous — a human with roles.assign fires it."""
        ser = BulkAssignSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        role = data['role']
        users, unmatched = _resolve_targets(data.get('user_ids'), data.get('emails'))

        BULK_MAX = 1000
        if not users:
            return Response(
                {'detail': 'No users resolved from user_ids / emails.',
                 'unmatched_emails': unmatched},
                status=status.HTTP_400_BAD_REQUEST)
        if len(users) > BULK_MAX:
            return Response(
                {'detail': f'Batch too large: {len(users)} users (max {BULK_MAX}). Split it.'},
                status=status.HTTP_400_BAD_REQUEST)

        granted, skipped, failed = [], [], []
        for u in users:
            try:
                assign_role(
                    target_user      = u,
                    role             = role,
                    granted_by       = request.user,
                    scope_department = data.get('scope_department') or None,
                    justification    = data.get('justification', ''),
                    expires_at       = data.get('expires_at'),
                    request_ip       = request.META.get('REMOTE_ADDR'),
                )
                granted.append({'id': u.pk, 'username': u.username, 'email': u.email})
            except (DjangoValidationError, ValidationError) as e:
                skipped.append({'id': u.pk, 'email': u.email, 'reason': _exc_msg(e)})
            except (DjangoPermissionDenied, PermissionDenied) as e:
                failed.append({'id': u.pk, 'email': u.email, 'reason': _exc_msg(e)})

        return Response({
            'role':             role.code,
            'granted_by':       request.user.username,
            'totals':           {'granted': len(granted), 'skipped': len(skipped),
                                 'failed': len(failed), 'unmatched_emails': len(unmatched)},
            'granted':          granted,
            'skipped':          skipped,
            'failed':           failed,
            'unmatched_emails': unmatched,
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='parse-request',
            permission_classes=[IsAuthenticated, HasRolesView])
    def parse_request(self, request):
        """Aria: turn a plain-English request into a PROPOSAL (best-match role +
        the people named, resolved to users) for the operator to review. Read-only
        — it never grants. The grant is the separate, human-confirmed bulk-assign."""
        text = (request.data.get('text') or '').strip()
        if not text:
            return Response({'detail': 'Describe who should get access.'},
                            status=status.HTTP_400_BAD_REQUEST)
        roles = list(Role.objects.filter(is_active=True).values('id', 'code', 'name'))
        parsed = parse_access_request(text, roles)
        rc = (parsed.get('role_code') or '').strip()
        role_obj = None
        if rc:
            role_obj = (Role.objects.filter(code__iexact=rc, is_active=True).first()
                        or Role.objects.filter(name__icontains=rc, is_active=True).first())
        resolved, ambiguous, unmatched_names = _resolve_names(parsed.get('names') or [])
        return Response({
            'source':          parsed.get('source', 'Aria advisor'),
            'role':            ({'id': role_obj.id, 'code': role_obj.code, 'name': role_obj.name}
                                if role_obj else None),
            'department':      parsed.get('department') or '',
            'resolved':        UserMiniSerializer(resolved, many=True).data,
            'ambiguous':       ambiguous,
            'unmatched_names': unmatched_names,
        })


class RBACUserViewSet(viewsets.ReadOnlyModelViewSet):
    """Convenience endpoint that returns every user with their assignments inlined."""

    queryset = (
        User.objects
        .prefetch_related('role_assignments__role__permissions')
        .order_by('username')
    )
    serializer_class   = UserRoleViewSerializer
    permission_classes = [IsAuthenticated, HasRolesView]
    filter_backends    = [filters.SearchFilter]
    search_fields      = ['username', 'email', 'first_name', 'last_name']

    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    def me(self, request):
        """Current user's effective roles, permissions, authority level."""
        u = request.user
        roles = user_roles(u)
        permission_codes = sorted({p.code for r in roles for p in r.permissions.all()})
        return Response({
            'user':                UserMiniSerializer(u).data,
            'is_superuser':        u.is_superuser,
            'roles':               RoleSerializer(roles, many=True).data,
            'permissions':         permission_codes,
            'max_authority_level': user_max_authority_level(u),
            'legacy_admin':        bool(get_user_profile(u) and get_user_profile(u).can_administer_users),
        })


class RBACAuditViewSet(viewsets.ReadOnlyModelViewSet):
    """RBAC slice of the audit log — read-only, paginated, ordered newest first."""

    queryset = (
        AuditLog.objects
        .filter(table_name='core.UserRoleAssignment')
        .select_related('user')
        .order_by('-created_at')
    )
    serializer_class   = AuditLogSerializer
    permission_classes = [IsAuthenticated, HasRolesView]
