"""core/api_views.py"""
import re
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from core.permissions import CanViewFinancials
from rest_framework.response import Response
from rest_framework.views import APIView

from .ai_assist import DeepSeekUnavailable, deepseek_complete, is_safe_for_ai, suggest_je_accounts
from .audit import log_user_admin_change
from .mixins import resolve_company
from .models import (
    AuditLog, Company, Currency, ExchangeRate, TaxRate, UserProfile, get_user_profile,
)
from .serializers import (
    AuditLogSerializer,
    CompanySerializer, CurrencySerializer,
    ExchangeRateSerializer, TaxRateSerializer,
    UserProfileSerializer, UserProfileWriteSerializer, UserProfileMeSerializer,
)
from django.utils import timezone


class CompanyViewSet(viewsets.ModelViewSet):
    queryset         = Company.objects.select_related('base_currency').order_by('code')
    serializer_class = CompanySerializer
    filter_backends  = [filters.SearchFilter, filters.OrderingFilter]
    search_fields    = ['code', 'name', 'legal_name', 'registration_number']
    ordering_fields  = ['code', 'name', 'created_at']

    def get_queryset(self):
        from core.models import allowed_company_ids
        qs = super().get_queryset()
        active = self.request.query_params.get('is_active')
        if active is not None:
            qs = qs.filter(is_active=active.lower() == 'true')

        # CFO directive 2026-05-22: even GET /companies/ must filter to
        # the user's allowed entities. Sidebar picker reads this list.
        allowed = allowed_company_ids(getattr(self.request, 'user', None))
        if allowed == {'*'}:
            return qs
        if not allowed:
            return qs.none()
        return qs.filter(id__in=allowed)

    def _enforce_single_default(self, instance):
        """If this instance is_default, unset is_default on every other row."""
        if instance.is_default:
            Company.objects.exclude(pk=instance.pk).filter(is_default=True).update(is_default=False)

    def perform_create(self, serializer):
        instance = serializer.save()
        self._enforce_single_default(instance)

    def perform_update(self, serializer):
        instance = serializer.save()
        self._enforce_single_default(instance)


class CurrencyViewSet(viewsets.ReadOnlyModelViewSet):
    queryset         = Currency.objects.all().order_by('code')
    serializer_class = CurrencySerializer
    filter_backends  = [filters.SearchFilter]
    search_fields    = ['code', 'name']


class ExchangeRateViewSet(viewsets.ModelViewSet):
    queryset = ExchangeRate.objects.select_related(
        'from_currency', 'to_currency'
    ).order_by('-effective_date')
    serializer_class = ExchangeRateSerializer
    filter_backends  = [filters.SearchFilter, filters.OrderingFilter]
    search_fields    = ['from_currency__code', 'to_currency__code']
    ordering_fields  = ['effective_date', 'from_currency', 'rate']

    def get_queryset(self):
        qs = super().get_queryset()
        fc = self.request.query_params.get('from_currency')
        tc = self.request.query_params.get('to_currency')
        if fc:
            qs = qs.filter(from_currency_id=fc.upper())
        if tc:
            qs = qs.filter(to_currency_id=tc.upper())
        ed = self.request.query_params.get('effective_date')
        if ed:
            qs = qs.filter(effective_date=ed)
        if self.request.query_params.get('approved_only', '').lower() == 'true':
            qs = qs.filter(approved_by__isnull=False, approved_at__isnull=False)
        return qs

    def _require_maker(self, request):
        from rest_framework.exceptions import PermissionDenied
        from .models import get_user_profile
        prof = get_user_profile(request.user)
        if not (prof and prof.can_originate_controlled_txn):
            raise PermissionDenied(
                'Loading FX rates requires a maker title (Financial Controller / '
                'Senior Accountant / Accountant). Finance Managers approve rates, '
                'they do not load them.')

    def create(self, request, *args, **kwargs):
        # SoD #5: authority-first — reject non-makers with 403 BEFORE payload
        # validation, so the block is unambiguous regardless of the body.
        self._require_maker(request)
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        self._require_maker(request)
        return super().update(request, *args, **kwargs)

    def perform_create(self, serializer):
        # BLOCKER-002 fix 2026-05-28: ExchangeRate inherits BaseModel only
        # (not AuditableMixin), so save() does NOT accept audit_user kwarg.
        # Passing it raised TypeError → HTTP 500 with empty body. We now
        # write the AuditLog row explicitly (same pattern as approve_rate
        # below) so the audit trail is preserved without a model change.
        from rest_framework.exceptions import PermissionDenied
        from .models import AuditLog as _AL, get_user_profile
        user = self.request.user
        # SoD (Workstream A #5): only a MAKER (Financial Controller / Senior
        # Accountant / Accountant) may load FX rates. Finance Managers APPROVE
        # rates — they must not also load them (self load-and-approve was the
        # audited SoD failure). Approval below is Finance-Manager-only.
        prof = get_user_profile(user)
        if not (prof and prof.can_originate_controlled_txn):
            raise PermissionDenied(
                'Loading FX rates requires a maker title (Financial Controller / '
                'Senior Accountant / Accountant). Finance Managers approve rates, '
                'they do not load them.')
        data = dict(serializer.validated_data)
        instance = serializer.Meta.model(**data)
        # FX-001: stamp loader; re-approval is required on every edit/load.
        instance.loaded_by = user
        instance.approved_by = None
        instance.approved_at = None
        instance.save()
        serializer.instance = instance
        _AL.objects.create(
            table_name='ExchangeRate', record_id=str(instance.pk),
            action=_AL.Action.CREATE,
            new_values={
                'pair': f'{instance.from_currency_id}/{instance.to_currency_id}',
                'rate': str(instance.rate),
                'effective_date': instance.effective_date.isoformat(),
                'source': instance.source,
            },
            user=user,
            description=(
                f"Loaded FX rate {instance.from_currency_id}/{instance.to_currency_id} "
                f"{instance.rate} for {instance.effective_date}"
            ),
        )

    def perform_update(self, serializer):
        from .models import AuditLog as _AL
        user = self.request.user
        # Any edit re-opens approval (CFO/Oprah directive — re-approve after change).
        serializer.instance.approved_by = None
        serializer.instance.approved_at = None
        for attr, val in serializer.validated_data.items():
            setattr(serializer.instance, attr, val)
        serializer.instance.save()
        _AL.objects.create(
            table_name='ExchangeRate', record_id=str(serializer.instance.pk),
            action=_AL.Action.UPDATE,
            new_values={
                'pair': f'{serializer.instance.from_currency_id}/{serializer.instance.to_currency_id}',
                'rate': str(serializer.instance.rate),
                'effective_date': serializer.instance.effective_date.isoformat(),
            },
            user=user,
            description=(
                f"Edited FX rate (approval cleared) "
                f"{serializer.instance.from_currency_id}/{serializer.instance.to_currency_id} "
                f"{serializer.instance.rate} for {serializer.instance.effective_date}"
            ),
        )

    @action(detail=True, methods=['post'], url_path='approve')
    def approve_rate(self, request, pk=None):
        """FX-001 (CFO/Oprah directive 2026-05-28). Finance Manager approves
        a loaded BoB mid-rate so it can be used in revaluation. Same-user
        SoD: the loader cannot approve their own rate."""
        from django.utils import timezone
        from django.core.exceptions import ValidationError as _VErr
        from .models import AuditLog as _AL, UserProfile as _UP, get_user_profile
        rate = self.get_object()
        prof = get_user_profile(request.user)
        # CHECKER authority: Finance Manager or CFO (Workstream A #5).
        if not (prof is not None and prof.can_check_controlled_txn):
            return Response({'detail': 'Approval requires Finance Manager or CFO title.'}, status=status.HTTP_403_FORBIDDEN)
        # Record-level SoD — the loader can NEVER approve their own rate, not
        # even a superuser. (Removing the old `and not is_super` bypass was the
        # #5 fix: omogomotsi is both Finance Manager and a Django superuser, so
        # the bypass let her self-approve.)
        if rate.loaded_by_id and rate.loaded_by_id == request.user.id:
            return Response({'detail': 'Segregation of duties: the loader of a rate cannot approve it.'},
                            status=status.HTTP_400_BAD_REQUEST)
        rate.approved_by = request.user
        rate.approved_at = timezone.now()
        rate.save()  # ExchangeRate is BaseModel only; no audit_user kwarg.
        _AL.objects.create(
            table_name='ExchangeRate', record_id=str(rate.pk),
            action=_AL.Action.APPROVE,
            new_values={
                'pair': f'{rate.from_currency_id}/{rate.to_currency_id}',
                'rate': str(rate.rate), 'effective_date': rate.effective_date.isoformat(),
                'source': rate.source,
            },
            user=request.user,
            description=f"Approved FX rate {rate.from_currency_id}/{rate.to_currency_id} {rate.rate} for {rate.effective_date}",
        )
        return Response(ExchangeRateSerializer(rate).data)

    @action(detail=True, methods=['post'], url_path='clear-approval')
    def clear_approval(self, request, pk=None):
        """Clear approval on a rate (e.g. discovered wrong number). Forces
        re-approval before re-use."""
        rate = self.get_object()
        rate.approved_by = None
        rate.approved_at = None
        rate.save()  # ExchangeRate is BaseModel only; no audit_user kwarg.
        return Response(ExchangeRateSerializer(rate).data)

    @action(detail=False, methods=['get'], url_path='latest')
    def latest(self, request):
        """Latest APPROVED rate for a currency pair — powers the New PO form's
        auto-fill so a foreign-currency PO defaults to the real BoB mid-rate
        instead of 1.0 (bug c9355408: a 1.0 default let a requester understate a
        foreign PO's BWP value and duck the P5,000 approval threshold).

        GET /api/v1/exchange-rates/latest/?from_currency=USD&to_currency=BWP
          → {rate, effective_date, source} of the newest approved rate, or
            {rate: null} when none is approved yet (caller then keeps manual entry).
        Only APPROVED rates are returned — an unapproved/loaded-only rate must
        never silently drive a PO total.
        """
        fc = (request.query_params.get('from_currency') or '').upper().strip()
        tc = (request.query_params.get('to_currency') or 'BWP').upper().strip()
        if not fc:
            return Response({'detail': 'from_currency is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if fc == tc:
            return Response({'rate': '1.00000000', 'effective_date': None, 'source': 'identity'})
        rate = (ExchangeRate.objects
                .filter(from_currency_id=fc, to_currency_id=tc,
                        approved_by__isnull=False, approved_at__isnull=False)
                .order_by('-effective_date')
                .first())
        if not rate:
            return Response({'rate': None, 'effective_date': None, 'source': None})
        return Response({
            'rate': str(rate.rate),
            'effective_date': rate.effective_date.isoformat(),
            'source': rate.source,
        })


class TaxRateViewSet(viewsets.ReadOnlyModelViewSet):
    queryset         = TaxRate.objects.all().order_by('tax_code')
    serializer_class = TaxRateSerializer
    filter_backends  = [filters.SearchFilter]
    search_fields    = ['tax_code', 'name']

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get('active_only', '').lower() == 'true':
            qs = qs.filter(is_active=True)
        return qs


from .access_delegate import delegate_refusal, is_access_delegate
from .models import NamedModuleAccess
from .serializers import NamedModuleAccessSerializer


def _admin_q():
    """Q object matching any user with administrative authority."""
    return Q(is_administrator=True) | Q(title=UserProfile.Title.CFO) | Q(user__is_superuser=True)


class UserProfileViewSet(viewsets.ModelViewSet):
    """
    User profile management — admin only for writes; reads available to any
    authenticated user.

    GET    /api/v1/user-profiles/         list all
    POST   /api/v1/user-profiles/         create user + profile (admin only)
    GET    /api/v1/user-profiles/{id}/    retrieve
    PATCH  /api/v1/user-profiles/{id}/    update title / role / admin / active (admin only)
    DELETE /api/v1/user-profiles/{id}/    deactivate (admin only — soft-delete)

    GET    /api/v1/user-profiles/me/      current user's own profile
    """
    queryset         = UserProfile.objects.select_related('user').order_by('user__username')
    permission_classes = [IsAuthenticated]
    filter_backends  = [filters.SearchFilter]
    # Email added 2026-09-18: Unami Butale reported "only Bame appears" when
    # working through a list of leavers. The list she was given was a list of
    # EMAIL ADDRESSES, and email was the one identifier this search did not
    # cover, so every paste returned nothing. Proven on prod: searching
    # 'bsebape' returned 0 while 'Sebape' returned 1.
    search_fields    = ['user__username', 'user__first_name', 'user__last_name',
                        'user__email', 'department']

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return UserProfileWriteSerializer
        if self.action == 'me':
            return UserProfileMeSerializer
        return UserProfileSerializer

    def _require_admin(self, target=None, new_values=None):
        """
        Full administrator, or a delegated access administrator acting inside
        the allow-list (CFO directive 2026-09-15 — see core/access_delegate.py).
        """
        profile = get_user_profile(self.request.user)
        if profile and profile.can_administer_users:
            return
        refusal = delegate_refusal(profile, target, new_values or {})
        if refusal:
            raise PermissionDenied(refusal)

    def perform_create(self, serializer):
        self._require_admin(target=None, new_values=dict(serializer.validated_data))
        profile = serializer.save()
        log_user_admin_change(
            self.request.user, profile.user, AuditLog.Action.CREATE,
            new_values={
                'title':            profile.title,
                'role':             profile.role,
                'is_administrator': profile.is_administrator,
                'is_active':        profile.is_active,
            },
            request=self.request,
            description=f'Created user + profile for {profile.user.username}',
        )

    def perform_update(self, serializer):
        self._require_admin(target=self.get_object(),
                            new_values=dict(serializer.validated_data))
        # Self-protection — admins cannot strip themselves of admin power if
        # they would be the last remaining administrator.
        target = self.get_object()
        actor = self.request.user
        _old = {
            'title':            target.title,
            'role':             target.role,
            'is_administrator': target.is_administrator,
            'is_active':        target.is_active,
        }
        if target.user_id == actor.id:
            new_admin = serializer.validated_data.get('is_administrator', target.is_administrator)
            new_title = serializer.validated_data.get('title', target.title)
            still_admin = (
                new_admin
                or new_title == UserProfile.Title.CFO
                or actor.is_superuser
            )
            if not still_admin:
                last_admin = (
                    UserProfile.objects
                    .filter(is_active=True)
                    .filter(_admin_q())
                    .exclude(pk=target.pk)
                    .count() == 0
                )
                if last_admin:
                    raise PermissionDenied(
                        "You are the only remaining administrator. "
                        "Promote another user to admin first."
                    )
        profile = serializer.save()
        log_user_admin_change(
            actor, profile.user, AuditLog.Action.UPDATE,
            old_values=_old,
            new_values={
                'title':            profile.title,
                'role':             profile.role,
                'is_administrator': profile.is_administrator,
                'is_active':        profile.is_active,
            },
            request=self.request,
            description=f'Updated profile for {profile.user.username}',
        )

    def perform_destroy(self, instance):
        self._require_admin(target=instance, new_values={'is_active': False})
        # Soft delete — deactivate instead of removing the auth record
        was_active = instance.is_active
        instance.is_active = False
        instance.save()
        log_user_admin_change(
            self.request.user, instance.user, AuditLog.Action.DELETE,
            old_values={'is_active': was_active},
            new_values={'is_active': False},
            request=self.request,
            description=f'Deactivated user {instance.user.username}',
        )

    @action(detail=False, methods=['get'])
    def me(self, request):
        """The caller's own profile — or an explicit, powerless 'no profile' answer.

        Asking who I am is not a failing request. This used to return 404 when the
        signed-in user had no UserProfile row, which the SPA surfaced as a red
        "Request failed" toast on every page carrying a personal panel. On prod
        2026-07-29 that was 58 of 188 active users — roughly a third of staff
        seeing an error for a state the system is perfectly happy with.

        It answers 200 with the same shape and EVERY capability false, so callers
        get a usable object and the app fails CLOSED. `has_profile` tells the
        caller which it got.

        Deliberately does NOT create the missing profile. UserProfile.title
        defaults to ACCOUNTANT, which sits in both CREATION_TITLES and
        FINANCIALS_VIEW_TITLES — auto-creating would silently hand those 58 people
        the right to raise journal entries and read financials. Who gets a title is
        the CFO's call, never a side effect of loading a page.
        """
        profile = get_user_profile(request.user)
        if profile is None:
            return Response(self._no_profile_payload(request.user))
        data = UserProfileMeSerializer(profile).data
        data['has_profile'] = True
        return Response(data)

    @staticmethod
    def _no_profile_payload(user) -> dict:
        """Identity only, no authority. Mirrors UserProfileMeSerializer's keys so
        the frontend needs no special case beyond reading has_profile."""
        from core.mobile_capabilities import empty_mobile_capabilities
        return {
            'has_profile': False,
            'id': None,
            'username':   getattr(user, 'username', '') or '',
            'first_name': getattr(user, 'first_name', '') or '',
            'last_name':  getattr(user, 'last_name', '') or '',
            'email':      getattr(user, 'email', '') or '',
            'is_user_active': bool(getattr(user, 'is_active', False)),
            'role': None, 'role_display': '',
            'title': None, 'title_display': '',
            'department': None, 'job_title': '',
            'is_administrator': False,
            # No row means no authority of any kind, the delegate flag included.
            'is_access_delegate': False,
            'is_active': bool(getattr(user, 'is_active', False)),
            # No row, so no timestamps — present as nulls to keep the shape.
            'created_at': None,
            'updated_at': None,
            # Every permission false — no profile means no authority.
            'can_approve_journal_entries': False,
            'can_create_journal_entries':  False,
            'can_approve_payroll':         False,
            'can_administer_users':        False,
            'can_post_directly':           False,
            'can_manage_periods':          False,
            'is_payroll_processor':        False,
            'can_view_internal_audit':     False,
            'can_edit_internal_audit':     False,
            # Mobile capability manifest — no profile ⇒ no authority.
            'mobile_capabilities':         empty_mobile_capabilities(),
        }


class AIAccountSuggestView(APIView):
    """
    POST /api/v1/ai/suggest-accounts/   {"description": "..."}

    Returns up to 3 chart-of-accounts suggestions based on a free-text
    description. Powered by DeepSeek (CFO-authorised exception, 2026-05-09).
    Only the description text and the active CoA are sent — no PII, no
    figures, no contact data. The is_safe_for_ai() filter scrubs anything
    that looks like an ID / phone / email / monetary amount before send.

    Response shape:
      { "suggestions": [{"code": "5400", "reason": "..." }, ...],
        "redactions_made": 0,
        "notes": [...] }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from ledger.models import Account

        description = (request.data.get('description') or '').strip()
        if not description:
            return Response(
                {'error': 'description is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        safety = is_safe_for_ai(description)
        if not safety.safe:
            return Response({
                'suggestions':     [],
                'redactions_made': safety.redactions_made,
                'notes':           safety.notes,
                'detail':          'Input contained too much sensitive data — refused.',
            }, status=status.HTTP_400_BAD_REQUEST)

        accounts = list(
            Account.objects.filter(is_active=True)
            .values('code', 'name', 'account_type', 'sub_type')
        )
        suggestions = suggest_je_accounts(description, accounts)

        return Response({
            'suggestions':     suggestions,
            'redactions_made': safety.redactions_made,
            'notes':           safety.notes,
        })


class AriaChatView(APIView):
    """
    POST /api/v1/ai/aria/chat/

    Aria — on-dashboard AI analyst, powered by DeepSeek (CFO directive
    2026-05-17 evening: embed for the CEO's "very good experience"). Takes
    the user's message + a dashboard snapshot of headline KPIs and returns
    a short, factual reply. History is optional and capped at 6 turns.

    Auth: IsAuthenticated. No additional gating — the AI is advisory and
    answers questions about figures the user already sees on screen.

    Request body:
      {
        "message": "How is cash doing this quarter?",
        "context": { "Total Cash": "BWP 12.9M", "GWP": "BWP 125.2M", ... },
        "history": [{"role":"user","content":"…"}, {"role":"assistant","content":"…"}]
      }

    Response:
      { "ok": true, "reply": "..." }            on success
      { "ok": false, "reason": "..." }          on failure (key not set,
                                                  safety filter, network)
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from core.ai_assist import aria_chat
        from core.models import AriaConversation, AriaMessage
        msg = (request.data.get('message') or '').strip()
        if not msg:
            return Response(
                {'ok': False, 'reason': 'message is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ctx = request.data.get('context') or {}
        if not isinstance(ctx, dict):
            ctx = {}
        history = request.data.get('history') or []
        if not isinstance(history, list):
            history = []
        first_name = (request.user.first_name or request.user.username or '').split(' ')[0]
        # Address the CEO by his preferred name.
        if request.user.email and request.user.email.lower() == 'aiyer@alphadirect.co.bw':
            first_name = 'Alpha Male'

        # ── Phase 1 enrichment (CFO directive 2026-05-22) ──────────────
        # Inject compliance deadlines + route hint + grounded tool reads
        # so ARIA's reply isn't pure-LLM hallucination.
        try:
            from core.aria.deadlines import next_deadlines, urgent_deadlines
            from core.aria.tools import (
                get_tb_status, get_pending_approvals, get_anomaly_count, route_hint,
            )
            ctx['upcoming_deadlines'] = next_deadlines(n=8)
            ctx['urgent_deadlines']   = urgent_deadlines(urgency_days=7)
            ctx['pending_approvals']  = get_pending_approvals(request.user)
            ctx['open_exceptions']    = get_anomaly_count().get('open_exceptions', 0)
            ctx['route_hint']         = route_hint(ctx.get('page', ''))
            tb = get_tb_status(company_id=ctx.get('selected_company_id'))
            ctx['tb_status'] = {k: tb[k] for k in ('balanced', 'rows') if k in tb}
        except Exception:        # noqa: BLE001 — never fail the chat on enrichment
            pass

        # Knowledge grounding (CFO 2026-08-31): answer "how do I…" / policy
        # questions from our OWN SOP bank, so staff stop asking these in the team
        # room. SOP bank ONLY — never the CFO notebook (internal management
        # facts). Best-effort: a miss just means a general answer, never an error.
        try:
            from core.aria_knowledge import knowledge_snippets
            kb = knowledge_snippets(msg)
            if kb:
                ctx['company_sop_answers'] = kb
        except Exception:        # noqa: BLE001
            pass

        # Phase 3 — personality dial. Frontend sends `X-Aria-Mood`
        # header in {formal, sharp, naughty}. Default = sharp.
        mood = (request.headers.get('X-Aria-Mood') or 'sharp').lower()
        if mood not in ('formal', 'sharp', 'naughty'):
            mood = 'sharp'

        # ── Persistent conversation (CFO directive 2026-05-24) ─────────
        # If the caller supplies ?conversation_id= (or `conversation_id`
        # in the body) and it belongs to this user, append to it.
        # Otherwise spin up a new thread. Stored history overrides the
        # body's `history` array — DB is the source of truth.
        conv_id = (
            request.query_params.get('conversation_id')
            or request.data.get('conversation_id')
        )
        conversation = None
        if conv_id:
            conversation = AriaConversation.objects.filter(
                id=conv_id, user=request.user,
            ).first()
        if conversation is None:
            conversation = AriaConversation.objects.create(
                user=request.user, mood=mood,
            )
        else:
            # mood may have flipped between turns; keep the row current.
            if conversation.mood != mood:
                conversation.mood = mood
                conversation.save(update_fields=['mood', 'last_activity_at'])

        # Replay prior turns from the DB (cap at 12 most-recent text turns).
        stored = list(
            conversation.messages
                .filter(role__in=['user', 'assistant'])
                .order_by('-created_at')[:12]
        )
        stored.reverse()
        db_history = [{'role': m.role, 'content': m.content} for m in stored]

        # Persist this user turn before the LLM call so it survives crashes.
        AriaMessage.objects.create(
            conversation=conversation, role='user', content=msg,
        )

        from core.aria.qc_tools import is_power_user
        result = aria_chat(
            msg, user_first_name=first_name,
            dashboard_context=ctx, history=db_history,
            mood=mood, user=request.user,
            power_user=is_power_user(request.user),
        )

        # Persist the assistant's final reply (and any tool-call trace).
        if result.get('ok'):
            AriaMessage.objects.create(
                conversation=conversation,
                role='assistant',
                content=str(result.get('reply', '')),
                tool_calls={'trace': result.get('tool_trace', [])}
                if result.get('tool_trace') else {},
            )
            # Tool iterations (if any) for full audit replay.
            for step in result.get('tool_trace', []) or []:
                AriaMessage.objects.create(
                    conversation=conversation,
                    role='tool',
                    content=str(step.get('result', ''))[:8000],
                    tool_calls={
                        'name': step.get('name'),
                        'arguments': step.get('arguments', {}),
                    },
                )

        # Always return conversation_id so the client can resume.
        result['conversation_id'] = str(conversation.id)
        return Response(result, status=status.HTTP_200_OK)


class AriaConfirmActionView(APIView):
    """
    POST /api/v1/ai/aria/confirm-action/   {action, ref, confirm_token, reason?, notes?}

    The only way an Aria payment approve/reject completes (L-AGENT, CFO
    18-Sep-2026). The chat hands the short-lived token to the user's Confirm
    card, never to the model, so this call is the human's tap. The tool re-runs
    every permission/SOD/stage check under a row lock and refuses a token that
    is expired, for another user, already used, or for a payment that changed.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from core.aria.qc_tools import approve_payment, is_power_user, reject_payment
        if not is_power_user(request.user):
            return Response({'ok': False, 'error': 'Aria actions are not enabled for your account.'},
                            status=status.HTTP_403_FORBIDDEN)
        d = request.data
        action = str(d.get('action') or '')
        ref = str(d.get('ref') or '').strip()
        token = str(d.get('confirm_token') or '')
        if action not in ('approve', 'reject') or not ref or not token:
            return Response({'ok': False, 'error': 'action, ref and confirm_token are required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if action == 'approve':
            result = approve_payment(ref=ref, user=request.user,
                                     notes=str(d.get('notes') or ''), confirm_token=token)
        else:
            result = reject_payment(ref=ref, user=request.user,
                                    reason=str(d.get('reason') or ''), confirm_token=token)
        code = status.HTTP_200_OK if result.get('ok') else status.HTTP_409_CONFLICT
        return Response(result, status=code)


class AIInsightView(APIView):
    """
    GET /api/v1/ai/insight/?ctx=<dashboard|cfo|hris|po>

    Returns one short, contextual AI-generated nudge for the dashboard
    hero ribbons. Powered by DeepSeek (non-sensitive aggregates only).
    Cached for 6 hours per (ctx, day) to keep API spend predictable.

    Response shape:
        {"ctx": "dashboard", "insight": "…", "cached": true|false}

    Never sends names, IDs, salary figures, or contact details to the
    model — only public counts and category labels.
    """
    permission_classes = [IsAuthenticated]

    # Each context provides its own non-sensitive snapshot + a short prompt.
    # Snapshots are computed at request time from Django models — no raw PII.
    # Hard formatting + style rules every prompt inherits. Lets us patch the
    # whole ribbon in one place when LLM drift produces ugly output.
    _STYLE_RULES = (
        ' STYLE RULES: use thousands separators for any number above 999 '
        '(e.g. write "17,121" not "17121"). When referencing the month-end '
        'close, always name the explicit date provided in the context. Use '
        'plain ASCII apostrophes. Do not wrap the line in quotes. Do not '
        'use emojis. Output one line only.'
    )

    _PROMPTS = {
        'dashboard': (
            'You are an experienced Botswana CFO writing a one-line tip for '
            'the finance team\'s home dashboard. Be concrete and actionable. '
            'Max 130 characters.'
        ) + _STYLE_RULES,
        'cfo': (
            'You are an experienced Botswana CFO. Write a one-line nudge for '
            'the CFO\'s command-centre dashboard about what to prioritise '
            'today (cash, approvals, controls). Max 130 characters.'
        ) + _STYLE_RULES,
        'hris': (
            'You are an experienced HR director at a Botswana insurer. Write '
            'a one-line, positive nudge for the HR Manager\'s landing page '
            'about people, culture, or productivity. Max 130 characters.'
        ) + _STYLE_RULES,
        'po': (
            'You are an experienced Botswana CFO. Write a one-line tip for '
            'the procurement screen about controls, vendor risk, or accurate '
            'GL coding. Max 130 characters.'
        ) + _STYLE_RULES,
    }

    @staticmethod
    def _month_end_date(today):
        """Return the last calendar day of `today`'s month."""
        from datetime import date as _date
        from calendar import monthrange
        last = monthrange(today.year, today.month)[1]
        return _date(today.year, today.month, last)

    def _snapshot(self, ctx: str) -> str:
        """Build a tiny non-sensitive context string for the model.

        Formats every count with thousands separators so the LLM does
        not have to do the formatting itself. Also pins the next
        month-end close as an explicit date string to stop "before
        month-end close" from drifting into a date-less stub.
        """
        from datetime import date
        from ledger.models import JournalEntry
        from core.models import Company

        today = timezone.localdate()
        close = self._month_end_date(today)
        bits = [
            f'Today is {today.isoformat()}.',
            f'Month-end close is {close.strftime("%B %d, %Y")}.',
        ]
        if ctx in ('dashboard', 'cfo'):
            companies = Company.objects.filter(is_active=True).count()
            jes_total = JournalEntry.objects.filter(status='posted').count()
            # Pre-format so the LLM cannot strip the thousands separator.
            bits.append(f'{companies:,} active group companies.')
            bits.append(f'{jes_total:,} posted journal entries to date.')
        if ctx == 'hris':
            # Bug 976f7aea: this used to hardcode "around 80 people, 5 cities" —
            # which contradicted the HRIS stat cards (live HRISProfile count).
            # Use the SAME live figures so the ribbon and the cards agree.
            try:
                from hris.models import HRISProfile
                from payroll.models import Employee
                ppl    = HRISProfile.objects.count()
                cities = (HRISProfile.objects
                          .exclude(location__isnull=True).exclude(location='')
                          .values('location').distinct().count())
                depts  = (Employee.objects.filter(status='active')
                          .exclude(department__isnull=True).exclude(department='')
                          .values('department').distinct().count())
                parts = [f'{ppl:,} {"person" if ppl == 1 else "people"} onboarded']
                if cities:
                    parts.append(f'{cities} {"city" if cities == 1 else "cities"}')
                if depts:
                    parts.append(f'{depts} {"department" if depts == 1 else "departments"}')
                bits.append('Workforce: ' + ', '.join(parts) + '.')
            except Exception:    # noqa: BLE001 — never break the ribbon on a count
                pass
        return ' '.join(bits)

    def get(self, request):
        from django.core.cache import cache
        from datetime import date

        ctx = (request.query_params.get('ctx') or 'dashboard').lower().strip()
        if ctx not in self._PROMPTS:
            return Response(
                {'error': f'unknown ctx {ctx!r}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Cache key version bumped 2026-05-26 to drop stale prose that lost
        # thousands separators + the month-end close date before the new
        # _STYLE_RULES + pre-formatted snapshot landed. Bump v on any prompt
        # change so the next request regenerates instead of replaying old cache.
        cache_key = f'ai_insight:v2:{ctx}:{timezone.localdate().isoformat()}'
        cached = cache.get(cache_key)
        if cached:
            return Response({'ctx': ctx, 'insight': cached, 'cached': True})

        system = self._PROMPTS[ctx]
        user_msg = self._snapshot(ctx) + ' Give one short tip.'

        try:
            raw = deepseek_complete(user_msg, system_prompt=system, timeout=10.0)
        except DeepSeekUnavailable:
            # Static fallback so the ribbon never breaks the UI.
            fallback = {
                'dashboard': 'Reconcile bank feeds before midday so cash KPIs reflect today.',
                'cfo':       'Clear the approvals inbox first — it gates 4 downstream workflows.',
                'hris':      'A two-minute thank-you outperforms a three-month review. Try one today.',
                'po':        'Match the GL account to the cost driver, not the vendor name.',
            }[ctx]
            return Response({'ctx': ctx, 'insight': fallback, 'cached': False, 'fallback': True})

        # Trim defensively — DeepSeek sometimes adds quotes or trailing whitespace.
        insight = raw.strip().strip('"').strip("'").strip()
        if len(insight) > 200:
            insight = insight[:197].rsplit(' ', 1)[0] + '…'

        cache.set(cache_key, insight, 60 * 60 * 6)  # 6 hours
        return Response({'ctx': ctx, 'insight': insight, 'cached': False})


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only viewer for the immutable AuditLog. Every authenticated user
    can read; the log itself is append-only at the model layer (writes
    happen automatically via AuditableMixin.save() and approval actions).
    """
    queryset           = AuditLog.objects.select_related('user').order_by('-created_at')
    serializer_class   = AuditLogSerializer
    # CFO directive 2026-06-20: the audit log is finance/management-only —
    # lower-level staff must not read who-did-what across the company.
    permission_classes = [IsAuthenticated, CanViewFinancials]
    filter_backends    = [filters.SearchFilter]
    search_fields      = ['table_name', 'record_id', 'description', 'user__username']

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get('table_name'):
            qs = qs.filter(table_name=params['table_name'])
        if params.get('action'):
            qs = qs.filter(action=params['action'])
        if params.get('user'):
            qs = qs.filter(user__username=params['user'])
        if params.get('from_date'):
            qs = qs.filter(created_at__date__gte=params['from_date'])
        if params.get('to_date'):
            qs = qs.filter(created_at__date__lte=params['to_date'])
        if params.get('record_id'):
            qs = qs.filter(record_id=params['record_id'])
        return qs


class AuditAskView(APIView):
    """
    POST /api/v1/admin/audit-ask/   (CFO directive 2026-06-21)

    A read-only, natural-language helper over the user-administration audit
    trail: "Who changed kago's permissions last week, and why?". It resolves
    person/entity names locally against omni's own User / UserProfile / Company
    tables, pulls the relevant recent AuditLog rows, builds a MINIMAL,
    name-resolved context (no bulk PII dump), and asks DeepSeek to answer
    strictly from that context.

    STRICTLY READ-ONLY — it never writes, deletes, or mutates anything, not even
    an AuditLog row. Same admin gating as the other /admin/ endpoints
    (superuser, is_administrator, or CFO).

    Request body:
        {
          "question": "Why did kago's company access change in June?",
          "filters": {                          # all optional
            "username":   "...",                # exact username/email to scope
            "table_name": "core.UserCompanyAccess",
            "from_date":  "2026-06-01",
            "to_date":    "2026-06-21"
          }
        }

    Response:
        {"answer": "...", "advice": "...", "sources": ["<audit-log-id>", ...]}

    If DEEPSEEK_API_KEY is unset, returns a graceful 200 with
    {"answer": "AI assistant not configured", "advice": "", "sources": []}
    rather than erroring.
    """
    permission_classes = [IsAuthenticated]

    # Only the user-administration slice of the trail is in scope here.
    _ADMIN_TABLES = ('core.UserProfile', 'core.UserCompanyAccess',
                     'core.UserRoleAssignment')
    _MAX_ROWS = 60

    SYSTEM_PROMPT = (
        'You are an audit analyst for a finance ERP. Answer questions about '
        'user-administration changes (titles, roles, company access, admin '
        'rights, user activation) STRICTLY from the provided audit-log context. '
        'Be concise and factual. Do not speculate or invent names, dates, or '
        'reasons. If the log does not answer the question, say so plainly. '
        'Respond ONLY with valid JSON of the form '
        '{"answer":"...","advice":"...","sources":["<id>", ...]}. '
        '"advice" is a one-line, optional governance suggestion (e.g. confirm a '
        'segregation-of-duties concern); leave it empty if none. "sources" lists '
        'the ids of the audit entries that informed the answer.'
    )

    def _is_admin(self, request):
        from core.models import allowed_company_ids
        user = request.user
        return bool(getattr(user, 'is_superuser', False)
                    or allowed_company_ids(user) == {'*'})

    def _resolve_subject(self, question, explicit_username):
        """Best-effort local resolution of the person the question is about.

        Returns (matched_user_or_None, note). NEVER calls an external service —
        names are disambiguated only against omni's own User table so e.g.
        'kago' maps to the real account(s)."""
        from django.contrib.auth import get_user_model
        User = get_user_model()
        if explicit_username:
            u = (User.objects.filter(username__iexact=explicit_username).first()
                 or User.objects.filter(email__iexact=explicit_username).first())
            if u:
                return u, f'scoped to {u.username}'
            return None, f"no user matched '{explicit_username}'"
        # Token-scan the question against usernames / first names. Only auto-scope
        # when exactly one user matches, to avoid silently narrowing too far.
        tokens = {t.strip('.,?!').lower() for t in (question or '').split() if len(t) > 2}
        if not tokens:
            return None, 'no subject token in question'
        cands = [u for u in User.objects.all()
                 if u.username.lower() in tokens
                 or (u.first_name or '').lower() in tokens]
        if len(cands) == 1:
            return cands[0], f'auto-scoped to {cands[0].username}'
        if len(cands) > 1:
            return None, ('ambiguous subject: '
                          + ', '.join(sorted(c.username for c in cands)))
        return None, 'subject not found in directory'

    def _build_context(self, rows):
        """A minimal, name-resolved view of each audit row — no bulk PII."""
        ctx = []
        for r in rows:
            # Only the human-readable description + metadata — never the raw
            # old_values/new_values blobs, so no bulk PII leaves the system.
            ctx.append({
                'id':     str(r.id),
                'when':   r.created_at.isoformat(),
                'who':    r.user.username if r.user_id else 'system',
                'action': r.action,
                'table':  r.table_name,
                'what':   r.description or '',
            })
        return ctx

    def post(self, request):
        import json
        if not self._is_admin(request):
            return Response(
                {'detail': 'Administrator privileges are required to query the audit trail.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        question = (request.data.get('question') or '').strip()
        if not question:
            return Response({'detail': 'question is required.'},
                            status=status.HTTP_400_BAD_REQUEST)

        filters = request.data.get('filters') or {}
        subject, subject_note = self._resolve_subject(
            question, (filters.get('username') or '').strip())

        # READ-ONLY query over the user-admin slice of the trail.
        qs = (AuditLog.objects
              .filter(table_name__in=self._ADMIN_TABLES)
              .select_related('user')
              .order_by('-created_at'))
        if subject is not None:
            qs = qs.filter(Q(new_values__user_id=str(subject.pk))
                           | Q(new_values__username=subject.username)
                           | Q(description__icontains=subject.username))
        if filters.get('table_name'):
            qs = qs.filter(table_name=filters['table_name'])
        if filters.get('from_date'):
            qs = qs.filter(created_at__date__gte=filters['from_date'])
        if filters.get('to_date'):
            qs = qs.filter(created_at__date__lte=filters['to_date'])

        rows = list(qs[:self._MAX_ROWS])
        context_rows = self._build_context(rows)

        if not context_rows:
            return Response({
                'answer':  'No matching user-administration changes were found in the audit log.',
                'advice':  '',
                'sources': [],
                'subject': subject_note,
            })

        user_prompt = (
            f'Question: {question}\n\n'
            f'Audit log (most recent first), JSON:\n'
            f'{json.dumps(context_rows, ensure_ascii=False)}'
        )

        try:
            raw = deepseek_complete(
                user_prompt, system_prompt=self.SYSTEM_PROMPT,
                response_format='json_object', timeout=20.0,
            )
        except DeepSeekUnavailable:
            # Graceful degradation — the page still works without the AI layer.
            return Response({
                'answer':  'AI assistant not configured',
                'advice':  '',
                'sources': [],
                'subject': subject_note,
            })

        answer, advice, ai_sources = '', '', []
        try:
            parsed = json.loads(raw)
            answer = str(parsed.get('answer', '')).strip()
            advice = str(parsed.get('advice', '')).strip()
            ai_sources = [str(s) for s in (parsed.get('sources') or [])]
        except (ValueError, TypeError):
            # Model returned prose instead of JSON — surface it as the answer.
            answer = raw.strip()

        # Only echo back source ids that really exist in the context we sent.
        valid_ids = {c['id'] for c in context_rows}
        sources = [s for s in ai_sources if s in valid_ids]
        if not sources:
            sources = [c['id'] for c in context_rows[:5]]

        return Response({
            'answer':  answer or 'No answer produced.',
            'advice':  advice,
            'sources': sources,
            'subject': subject_note,
        })


# ---------------------------------------------------------------------------
# /me/companies/ — companies the caller may access
# CFO directive 2026-05-22.
# ---------------------------------------------------------------------------
from rest_framework.decorators import api_view, permission_classes as _perm_cls
from rest_framework.response import Response as _Resp

@api_view(['GET'])
@_perm_cls([IsAuthenticated])
def me_companies(request):
    """Companies the caller can view, plus their write flag per company."""
    from core.models import UserCompanyAccess, allowed_company_ids
    user = request.user
    allowed = allowed_company_ids(user)
    qs = Company.objects.select_related('base_currency').order_by('code')
    if allowed != {'*'}:
        qs = qs.filter(id__in=allowed)
    writes = set()
    if allowed != {'*'}:
        writes = set(str(c) for c in
                     UserCompanyAccess.objects.filter(user=user, can_write=True)
                     .values_list('company_id', flat=True))
    rows = []
    for c in qs:
        rows.append({
            'id':            str(c.id),
            'code':          c.code,
            'name':          c.name,
            'base_currency': getattr(c.base_currency, 'code', 'BWP'),
            'is_active':     c.is_active,
            'can_write':     (allowed == {'*'}) or (str(c.id) in writes),
        })
    return _Resp({
        'unrestricted': allowed == {'*'},
        'companies':    rows,
    })


# ---------------------------------------------------------------------------
# Admin: grant / revoke user-company access
# ---------------------------------------------------------------------------
@api_view(['GET', 'POST', 'DELETE'])
@_perm_cls([IsAuthenticated])
def user_company_access_admin(request):
    """
    GET    ?user=<username|email>  → list grants
    POST   {user, company, can_view, can_write}  → upsert grant
    DELETE ?user=<u>&company=<id>  → revoke

    Caller must be superuser, administrator, or CFO.
    """
    from django.contrib.auth import get_user_model
    from core.models import UserCompanyAccess, allowed_company_ids

    requester = request.user
    if not (requester.is_superuser or allowed_company_ids(requester) == {'*'}):
        return _Resp({'detail': 'Permission denied.'}, status=403)

    User = get_user_model()

    def _resolve_user(ident):
        if not ident:
            return None
        return (User.objects.filter(username=ident).first()
                or User.objects.filter(email__iexact=ident).first())

    if request.method == 'GET':
        ident = request.query_params.get('user') or ''
        u = _resolve_user(ident)
        if not u:
            return _Resp({'detail': f"User '{ident}' not found."}, status=404)
        rows = [{
            'company_id':   str(g.company_id),
            'company_code': g.company.code,
            'can_view':     g.can_view,
            'can_write':    g.can_write,
            'granted_by':   g.granted_by.username if g.granted_by_id else None,
            'created_at':   g.created_at.isoformat(),
        } for g in UserCompanyAccess.objects.filter(user=u).select_related('company','granted_by')]
        return _Resp({'username': u.username, 'access': rows})

    if request.method == 'POST':
        body = request.data or {}
        u = _resolve_user(body.get('user') or '')
        if not u:
            return _Resp({'detail': 'User not found.'}, status=404)
        comp_id = body.get('company')
        comp = resolve_company(comp_id)
        if not comp:
            return _Resp({'detail': f"Company '{comp_id}' not found — "
                                    f"send its code (e.g. ADIC) or its id."}, status=404)
        obj, created = UserCompanyAccess.objects.update_or_create(
            user=u, company=comp,
            defaults={
                'can_view':   bool(body.get('can_view', True)),
                'can_write':  bool(body.get('can_write', False)),
                'granted_by': requester,
                'notes':      str(body.get('notes', ''))[:255],
            },
        )
        log_user_admin_change(
            requester, u,
            AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE,
            new_values={
                'company_code': comp.code,
                'can_view':     obj.can_view,
                'can_write':    obj.can_write,
            },
            request=request, table_name='core.UserCompanyAccess',
            record_id=str(obj.pk),
            description=(f'{"Granted" if created else "Updated"} company access '
                        f'{comp.code} for {u.username} '
                        f'(view={obj.can_view}, write={obj.can_write})'),
        )
        return _Resp({
            'status':       'created' if created else 'updated',
            'user':         u.username,
            'company_code': comp.code,
            'can_view':     obj.can_view,
            'can_write':    obj.can_write,
        }, status=201 if created else 200)

    # DELETE
    ident = request.query_params.get('user') or ''
    cident = request.query_params.get('company') or ''
    u = _resolve_user(ident)
    if not u:
        return _Resp({'detail': 'User not found.'}, status=404)
    comp = resolve_company(cident)
    if not comp:
        return _Resp({'detail': f"Company '{cident}' not found — "
                                f"send its code (e.g. ADIC) or its id."}, status=404)
    n = UserCompanyAccess.objects.filter(user=u, company=comp).delete()[0]
    if n:
        log_user_admin_change(
            requester, u, AuditLog.Action.DELETE,
            old_values={'company_code': comp.code},
            request=request, table_name='core.UserCompanyAccess',
            record_id=str(comp.id),
            description=f'Revoked company access {comp.code} for {u.username}',
        )
    return _Resp({'revoked': n})


@api_view(['POST'])
@_perm_cls([IsAuthenticated])
def user_company_access_bulk(request):
    """
    Bulk grant / revoke (CFO directive 2026-05-22).

    POST body:
      {
        "users":      ["pkago", "ktshutlhedi", "lntabeni"],  # usernames OR emails
        "companies":  ["ADIC", "QIH", "<uuid>"],             # codes OR uuids
        "can_view":   true,
        "can_write":  false,
        "action":     "grant" | "revoke"   (default grant)
      }

    Caller must be superuser, administrator, or CFO.
    Returns counts of touched rows.
    """
    from django.contrib.auth import get_user_model
    from core.models import UserCompanyAccess, allowed_company_ids

    requester = request.user
    if not (requester.is_superuser or allowed_company_ids(requester) == {'*'}):
        return _Resp({'detail': 'Permission denied.'}, status=403)

    body = request.data or {}
    User = get_user_model()
    action = (body.get('action') or 'grant').lower()
    if action not in ('grant', 'revoke'):
        return _Resp({'detail': "action must be 'grant' or 'revoke'."}, status=400)

    u_idents = [s for s in (body.get('users') or []) if s]
    c_idents = [s for s in (body.get('companies') or []) if s]
    if not u_idents or not c_idents:
        return _Resp({'detail': 'users[] and companies[] are required.'}, status=400)

    users = []
    misses_u = []
    for s in u_idents:
        u = (User.objects.filter(username=s).first()
             or User.objects.filter(email__iexact=s).first())
        (users if u else misses_u).append(u or s)
    users = [u for u in users if u is not None]

    companies = []
    misses_c = []
    for s in c_idents:
        # resolve_company() only applies the UUID branch when the value parses
        # as one. The hand-rolled `filter(id=s).first() or filter(code=…)` this
        # replaced raised ValidationError('"ADIC" is not a valid UUID') on the
        # FIRST half, so the code fallback the docstring promises never ran and
        # every code-bearing request 500'd. See core/mixins.resolve_company.
        c = resolve_company(s)
        (companies if c else misses_c).append(c or s)
    companies = [c for c in companies if c is not None]

    if not users or not companies:
        bits = []
        if not users:
            bits.append('users ' + ', '.join(f"'{s}'" for s in misses_u))
        if not companies:
            bits.append('companies ' + ', '.join(f"'{s}'" for s in misses_c))
        return _Resp({
            'detail': 'Could not resolve ' + ' or '.join(bits)
                      + '. Send a username or email for users, and a company '
                        'code (e.g. ADIC) or company id for companies.',
            'misses_users': misses_u,
            'misses_companies': misses_c,
        }, status=400)

    created = updated = revoked = 0
    if action == 'grant':
        can_view  = bool(body.get('can_view', True))
        can_write = bool(body.get('can_write', False))
        for u in users:
            for c in companies:
                obj, was_created = UserCompanyAccess.objects.update_or_create(
                    user=u, company=c,
                    defaults={
                        'can_view':   can_view,
                        'can_write':  can_write,
                        'granted_by': requester,
                    },
                )
                if was_created: created += 1
                else: updated += 1
                log_user_admin_change(
                    requester, u,
                    AuditLog.Action.CREATE if was_created else AuditLog.Action.UPDATE,
                    new_values={'company_code': c.code,
                                'can_view': can_view, 'can_write': can_write},
                    request=request, table_name='core.UserCompanyAccess',
                    record_id=str(obj.pk),
                    description=(f'Bulk {"granted" if was_created else "updated"} '
                                f'company access {c.code} for {u.username} '
                                f'(view={can_view}, write={can_write})'),
                )
    else:  # revoke
        for u in users:
            for c in companies:
                n = UserCompanyAccess.objects.filter(user=u, company=c).delete()[0]
                revoked += n
                if n:
                    log_user_admin_change(
                        requester, u, AuditLog.Action.DELETE,
                        old_values={'company_code': c.code},
                        request=request, table_name='core.UserCompanyAccess',
                        record_id=str(c.id),
                        description=f'Bulk revoked company access {c.code} for {u.username}',
                    )

    return _Resp({
        'action':            action,
        'users_resolved':    [u.username for u in users],
        'companies_resolved':[c.code for c in companies],
        'users_unresolved':  misses_u,
        'companies_unresolved': misses_c,
        'created':           created,
        'updated':           updated,
        'revoked':           revoked,
    })


@api_view(['POST'])
@_perm_cls([IsAuthenticated])
def user_company_access_upload(request):
    """
    Bulk grant via spreadsheet upload (CFO directive 2026-05-22).

    Accepts multipart/form-data with field 'file' (.xlsx / .csv) OR a
    JSON body {'rows': [...]} where each row is:

      {
        'name':    'Pako Kago'                     # display name / full
        'username':'pkago'                          # OR explicit username
        'email':   'pkago@alphadirect.co.bw'        # OR email
        'company': 'ADIC' | 'QIH' | '<uuid>',       # code or uuid
        'access':  'view' | 'rw' | 'revoke',
        'title':   'Finance Manager',               # optional, ignored
      }

    Resolution order for the user column:
      1. username exact match
      2. email exact match
      3. full_name exact match  (User.first_name + last_name)
      4. fuzzy: first-name token == provided 'name' (last-name optional)
      5. DeepSeek fuzzy name → username if DEEPSEEK_API_KEY is set

    Returns a per-row report (applied / skipped / why).
    """
    from django.contrib.auth import get_user_model
    from core.models import UserCompanyAccess, allowed_company_ids
    requester = request.user
    if not (requester.is_superuser or allowed_company_ids(requester) == {'*'}):
        return _Resp({'detail': 'Permission denied.'}, status=403)

    User = get_user_model()
    rows = []

    upload = request.FILES.get('file')
    if upload:
        fname = (upload.name or '').lower()
        try:
            if fname.endswith('.xlsx') or fname.endswith('.xlsm'):
                import openpyxl
                wb = openpyxl.load_workbook(upload, data_only=True)
                ws = wb.active
                headers = [str(c.value or '').strip().lower() for c in ws[1]]
                for r in ws.iter_rows(min_row=2, values_only=True):
                    if not any(r):
                        continue
                    rows.append({headers[i]: ('' if v is None else str(v).strip())
                                 for i, v in enumerate(r) if i < len(headers)})
            else:
                import csv, io
                txt = upload.read().decode('utf-8-sig', errors='replace')
                reader = csv.DictReader(io.StringIO(txt))
                for r in reader:
                    rows.append({(k or '').strip().lower(): (v or '').strip()
                                 for k, v in r.items()})
        except Exception as exc:    # noqa: BLE001
            return _Resp({'detail': f'Failed to parse upload: {exc}'}, status=400)
    else:
        rows = (request.data or {}).get('rows') or []

    if not rows:
        return _Resp({'detail': 'Upload an .xlsx / .csv with a header row, or POST JSON {rows:[]}.'}, status=400)

    all_users    = list(User.objects.all())
    all_companies = list(Company.objects.all())
    by_username  = {u.username.lower(): u for u in all_users}
    by_email     = {u.email.lower(): u for u in all_users if u.email}
    by_fullname  = {(u.get_full_name() or '').lower(): u for u in all_users}
    by_code      = {c.code.lower(): c for c in all_companies}
    by_uuid      = {str(c.id): c for c in all_companies}

    def _resolve_user(row):
        for key in ('username', 'user', 'login'):
            v = (row.get(key) or '').lower()
            if v in by_username:  return by_username[v], 'username'
            if v in by_email:     return by_email[v], 'email'
        em = (row.get('email') or '').lower()
        if em in by_email:        return by_email[em], 'email'
        full = (row.get('name') or row.get('full_name') or row.get('fullname') or '').strip().lower()
        if full in by_fullname:   return by_fullname[full], 'full_name'
        if full:
            # First-name fuzzy
            first = full.split()[0]
            cands = [u for u in all_users
                     if (u.first_name or '').lower() == first
                     or u.username.lower() == first]
            if len(cands) == 1:
                return cands[0], 'fuzzy_first_name'
        return None, ('unresolved: ' + (row.get('name') or row.get('email') or row.get('username') or '?'))

    def _resolve_company(row):
        for key in ('company', 'entity', 'company_code'):
            v = (row.get(key) or '').lower()
            if v in by_code:  return by_code[v], 'code'
            if v in by_uuid:  return by_uuid[v], 'uuid'
        return None, 'unresolved'

    def _resolve_access(row):
        a = (row.get('access') or row.get('level') or 'view').strip().lower()
        if a in ('revoke', 'remove', 'delete', 'none'):
            return 'revoke'
        if a in ('rw', 'write', 'view+write', 'full', 'admin', 'edit'):
            return 'rw'
        return 'view'

    report = []
    created = updated = revoked = 0
    for idx, row in enumerate(rows, start=2):       # row 1 was header
        u, u_src = _resolve_user(row)
        c, c_src = _resolve_company(row)
        if not u or not c:
            report.append({
                'row': idx, 'status': 'skipped',
                'reason': f'user={u_src}, company={c_src}',
                'raw': row,
            })
            continue
        access = _resolve_access(row)
        if access == 'revoke':
            n = UserCompanyAccess.objects.filter(user=u, company=c).delete()[0]
            revoked += n
            if n:
                log_user_admin_change(
                    requester, u, AuditLog.Action.DELETE,
                    old_values={'company_code': c.code},
                    request=request, table_name='core.UserCompanyAccess',
                    record_id=str(c.id),
                    description=f'Upload revoked company access {c.code} for {u.username}',
                )
            report.append({'row': idx, 'status': 'revoked',
                           'user': u.username, 'company': c.code})
        else:
            obj, was_created = UserCompanyAccess.objects.update_or_create(
                user=u, company=c,
                defaults={
                    'can_view':  True,
                    'can_write': access == 'rw',
                    'granted_by': requester,
                },
            )
            if was_created: created += 1
            else: updated += 1
            log_user_admin_change(
                requester, u,
                AuditLog.Action.CREATE if was_created else AuditLog.Action.UPDATE,
                new_values={'company_code': c.code,
                            'can_view': True, 'can_write': access == 'rw'},
                request=request, table_name='core.UserCompanyAccess',
                record_id=str(obj.pk),
                description=(f'Upload {"granted" if was_created else "updated"} '
                            f'company access {c.code} for {u.username} (access={access})'),
            )
            report.append({
                'row': idx, 'status': 'created' if was_created else 'updated',
                'user': u.username, 'company': c.code,
                'access': access,
            })

    return _Resp({
        'rows_processed': len(rows),
        'created': created,
        'updated': updated,
        'revoked': revoked,
        'report':  report,
    })


@api_view(['GET'])
@_perm_cls([IsAuthenticated])
def user_company_access_template(request):
    """
    Return a .xlsx template the CFO can fill and re-upload.
    Headers: name, username, email, company, access, title.
    """
    from django.http import HttpResponse
    from io import BytesIO
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return _Resp({'detail': 'openpyxl missing on backend image.'}, status=500)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Grants'
    headers = ['name', 'username', 'email', 'company', 'access', 'title']
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='0D1B2A')
        c.alignment = Alignment(horizontal='center')
    samples = [
        ['Pako Kago',  'pkago',       'pkago@alphadirect.co.bw', 'ADIC', 'rw',     'Finance Manager'],
        ['Oprah Mogomotsi','omogomotsi','omogomotsi@alphadirect.co.bw', 'ADIC', 'view', 'Accountant'],
        ['Bharath',    '',            'bharath@vcm.co.bw',      'VCM',  'rw',     'Salvage'],
        ['',           'ktshutlhedi', '',                       'QIH',  'revoke', ''],
    ]
    for s in samples:
        ws.append(s)
    notes = wb.create_sheet('README')
    notes.append(['Column', 'Meaning'])
    notes.append(['name',   'Optional. Display name — resolved fuzzily if username/email missing.'])
    notes.append(['username','Preferred. Exact match against User.username.'])
    notes.append(['email',  'Fallback resolver if username blank.'])
    notes.append(['company','Company code (ADIC / QIH / VCM / ADSA / …) OR UUID.'])
    notes.append(['access', "Either 'view', 'rw' (view+write), or 'revoke'."])
    notes.append(['title',  'Optional, ignored. Helpful for the human reviewer.'])
    for col, w in zip('ABCDEF', (24, 14, 30, 12, 10, 24)):
        ws.column_dimensions[col].width = w
        notes.column_dimensions[col].width = w if col == 'A' else 60
    buf = BytesIO(); wb.save(buf); buf.seek(0)
    resp = HttpResponse(
        buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = 'attachment; filename="user-access-template.xlsx"'
    return resp


@api_view(['GET', 'POST'])
@_perm_cls([IsAuthenticated])
def user_titles(request):
    """
    GET  → list every active user with current title + valid choices.
    POST → set/clear one or many user titles.

    Body for POST:
      {'rows': [{'username': 'pkago', 'title': 'finance_manager'}, ...]}
      OR
      {'username': 'pkago', 'title': 'finance_manager'}
      OR
      {'email': '...', 'title': '...'}

    Pass title='' or 'clear' to blank a title.

    Caller must be superuser, administrator, or CFO.
    """
    from django.contrib.auth import get_user_model
    from core.models import UserProfile, allowed_company_ids
    requester = request.user
    if not (requester.is_superuser or allowed_company_ids(requester) == {'*'}):
        return _Resp({'detail': 'Permission denied.'}, status=403)
    User = get_user_model()
    choices = [{'value': v, 'label': lbl} for v, lbl in UserProfile.Title.choices]

    if request.method == 'GET':
        rows = []
        for u in User.objects.filter(is_active=True).order_by('username'):
            p = UserProfile.objects.filter(user=u).first()
            rows.append({
                'username':  u.username,
                'email':     u.email,
                'full_name': (u.get_full_name() or u.username),
                'title':     (p.title if p else ''),
                'is_superuser': u.is_superuser,
            })
        return _Resp({'users': rows, 'choices': choices})

    # POST — either {rows:[...]} or a single record on the top level.
    body = request.data or {}
    rows = body.get('rows')
    if not rows:
        rows = [{
            'username': body.get('username') or '',
            'email':    body.get('email') or '',
            'name':     body.get('name') or '',
            'title':    body.get('title') or '',
        }]
    return _Resp(_apply_title_rows(rows, requester, request=request))


def _apply_title_rows(rows, requester, request=None):
    from django.contrib.auth import get_user_model
    from core.models import UserProfile
    User = get_user_model()
    valid = {v for v, _ in UserProfile.Title.choices}
    report, updated, skipped = [], 0, 0
    for idx, r in enumerate(rows, start=2):
        ident_u = (r.get('username') or '').strip().lower()
        ident_e = (r.get('email') or '').strip().lower()
        ident_n = (r.get('name') or '').strip().lower()
        u = (User.objects.filter(username__iexact=ident_u).first() if ident_u else None) \
            or (User.objects.filter(email__iexact=ident_e).first() if ident_e else None)
        if u is None and ident_n:
            cand = [x for x in User.objects.all()
                    if (x.get_full_name() or '').lower() == ident_n]
            if len(cand) == 1:
                u = cand[0]
        if u is None:
            report.append({'row': idx, 'status': 'skipped',
                           'reason': 'user not resolved', 'raw': r})
            skipped += 1
            continue
        t = (r.get('title') or '').strip().lower().replace(' ', '_').replace('-', '_')
        if t in ('', 'clear', 'none'):
            new_title = UserProfile.Title.ACCOUNTANT.value
        elif t in valid:
            new_title = t
        else:
            new_title = None
            for v, lbl in UserProfile.Title.choices:
                norm = lbl.lower().replace(' ', '_').replace('/', '').replace('(', '').replace(')', '')
                if t == norm:
                    new_title = v; break
            if new_title is None:
                report.append({'row': idx, 'status': 'skipped',
                               'reason': f"unknown title '{r.get('title')}'",
                               'username': u.username})
                skipped += 1
                continue
        prof, _ = UserProfile.objects.get_or_create(user=u, defaults={
            'role': UserProfile.Role.OPERATIONS_STAFF,
            'is_active': True,
        })
        old_title = prof.title
        prof.title = new_title
        prof.save(update_fields=['title', 'updated_at'])
        updated += 1
        log_user_admin_change(
            requester, u, AuditLog.Action.UPDATE,
            old_values={'title': old_title},
            new_values={'title': new_title},
            request=request, record_id=str(prof.pk),
            description=f"Set title for {u.username}: {old_title or '—'} → {new_title}",
        )
        report.append({'row': idx, 'status': 'updated',
                       'username': u.username, 'title': prof.title})
    return {'updated': updated, 'skipped': skipped, 'report': report}


@api_view(['POST'])
@_perm_cls([IsAuthenticated])
def user_titles_upload(request):
    """
    POST multipart .xlsx / .csv with columns:
      name | username | email | title
    """
    from core.models import allowed_company_ids
    requester = request.user
    if not (requester.is_superuser or allowed_company_ids(requester) == {'*'}):
        return _Resp({'detail': 'Permission denied.'}, status=403)
    upload = request.FILES.get('file')
    if not upload:
        return _Resp({'detail': 'Upload a file under field name `file`.'}, status=400)
    rows = []
    fname = (upload.name or '').lower()
    try:
        if fname.endswith('.xlsx') or fname.endswith('.xlsm'):
            import openpyxl
            wb = openpyxl.load_workbook(upload, data_only=True)
            ws = wb.active
            headers = [str(c.value or '').strip().lower() for c in ws[1]]
            for r in ws.iter_rows(min_row=2, values_only=True):
                if not any(r): continue
                rows.append({headers[i]: ('' if v is None else str(v).strip())
                             for i, v in enumerate(r) if i < len(headers)})
        else:
            import csv, io
            txt = upload.read().decode('utf-8-sig', errors='replace')
            for r in csv.DictReader(io.StringIO(txt)):
                rows.append({(k or '').strip().lower(): (v or '').strip()
                             for k, v in r.items()})
    except Exception as exc:    # noqa: BLE001
        return _Resp({'detail': f'Parse error: {exc}'}, status=400)
    return _Resp(_apply_title_rows(rows, requester, request=request))


def _apply_email_rows(rows, requester, create_if_missing=True):
    """Set email on user rows; optionally create users.

    Row keys: name, username, email, first_name, last_name, title.
    Resolution: username → existing email → full_name. If still
    unresolved and create_if_missing, build a new user from the row.
    Username defaults to email-local-part when missing.
    """
    import re
    from django.contrib.auth import get_user_model
    from core.models import UserProfile
    User = get_user_model()

    def _norm_email(s):
        return (s or '').strip().lower()

    def _slug(s):
        s = re.sub(r'[^a-z0-9._-]+', '', (s or '').lower())
        return s[:30] or 'user'

    report, updated, created, skipped = [], 0, 0, 0
    for idx, r in enumerate(rows, start=2):
        ident_u = (r.get('username') or '').strip().lower()
        new_email = _norm_email(r.get('email'))
        ident_n = (r.get('name') or r.get('full_name') or '').strip()
        first = (r.get('first_name') or '').strip()
        last  = (r.get('last_name')  or '').strip()

        u = None
        if ident_u:
            u = User.objects.filter(username__iexact=ident_u).first()
        if u is None and new_email:
            u = User.objects.filter(email__iexact=new_email).first()
        if u is None and ident_n:
            cand = [x for x in User.objects.all()
                    if (x.get_full_name() or '').lower() == ident_n.lower()]
            if len(cand) == 1:
                u = cand[0]

        if u is None:
            if not create_if_missing:
                report.append({'row': idx, 'status': 'skipped',
                               'reason': 'user not resolved', 'raw': r})
                skipped += 1
                continue
            # Build a new user
            if not (new_email or ident_u):
                report.append({'row': idx, 'status': 'skipped',
                               'reason': 'need email or username to create', 'raw': r})
                skipped += 1
                continue
            base = ident_u or new_email.split('@')[0]
            uname = _slug(base)
            i = 1
            while User.objects.filter(username=uname).exists():
                i += 1; uname = f'{_slug(base)}{i}'
            if not first and not last and ident_n:
                parts = ident_n.split()
                first = parts[0]
                last = ' '.join(parts[1:]) if len(parts) > 1 else ''
            u = User.objects.create(
                username   = uname,
                email      = new_email,
                first_name = first[:30],
                last_name  = last[:150],
                is_active  = True,
            )
            u.set_unusable_password()
            u.save()
            created += 1
            UserProfile.objects.get_or_create(user=u, defaults={
                'role':      UserProfile.Role.OPERATIONS_STAFF,
                'is_active': True,
            })
            report.append({'row': idx, 'status': 'created',
                           'username': u.username, 'email': u.email})
            continue

        # Existing user — update email + optional names
        changed = False
        if new_email and (u.email or '').lower() != new_email:
            u.email = new_email; changed = True
        if first and u.first_name != first[:30]:
            u.first_name = first[:30]; changed = True
        if last and u.last_name != last[:150]:
            u.last_name = last[:150]; changed = True
        if changed:
            u.save(update_fields=['email', 'first_name', 'last_name'])
            updated += 1
            report.append({'row': idx, 'status': 'updated',
                           'username': u.username, 'email': u.email})
        else:
            report.append({'row': idx, 'status': 'noop',
                           'username': u.username, 'email': u.email})
    return {'updated': updated, 'created': created, 'skipped': skipped, 'report': report}


@api_view(['GET', 'POST'])
@_perm_cls([IsAuthenticated])
def user_emails(request):
    """GET → list users with email. POST → set/create."""
    from django.contrib.auth import get_user_model
    from core.models import allowed_company_ids, UserProfile
    requester = request.user
    if not (requester.is_superuser or allowed_company_ids(requester) == {'*'}):
        return _Resp({'detail': 'Permission denied.'}, status=403)
    User = get_user_model()
    if request.method == 'GET':
        rows = []
        profs = {p.user_id: p.title for p in UserProfile.objects.all()}
        for u in User.objects.filter(is_active=True).order_by('username'):
            rows.append({
                'username':   u.username,
                'email':      u.email or '',
                'first_name': u.first_name or '',
                'last_name':  u.last_name or '',
                'full_name':  (u.get_full_name() or u.username),
                'title':      profs.get(u.id, ''),
                'is_superuser': u.is_superuser,
                'last_login': u.last_login.isoformat() if u.last_login else None,
            })
        return _Resp({'users': rows})

    body = request.data or {}
    rows = body.get('rows')
    if not rows:
        rows = [{
            'username':   body.get('username') or '',
            'email':      body.get('email') or '',
            'name':       body.get('name') or '',
            'first_name': body.get('first_name') or '',
            'last_name':  body.get('last_name') or '',
        }]
    return _Resp(_apply_email_rows(rows, requester,
                                   create_if_missing=bool(body.get('create_if_missing', True))))


@api_view(['POST'])
@_perm_cls([IsAuthenticated])
def user_emails_upload(request):
    """Upload .xlsx / .csv. Same row schema as POST user_emails."""
    from core.models import allowed_company_ids
    requester = request.user
    if not (requester.is_superuser or allowed_company_ids(requester) == {'*'}):
        return _Resp({'detail': 'Permission denied.'}, status=403)
    upload = request.FILES.get('file')
    if not upload:
        return _Resp({'detail': 'Upload a file under field name `file`.'}, status=400)
    rows = []
    fname = (upload.name or '').lower()
    try:
        if fname.endswith('.xlsx') or fname.endswith('.xlsm'):
            import openpyxl
            wb = openpyxl.load_workbook(upload, data_only=True)
            ws = wb.active
            headers = [str(c.value or '').strip().lower() for c in ws[1]]
            for r in ws.iter_rows(min_row=2, values_only=True):
                if not any(r): continue
                rows.append({headers[i]: ('' if v is None else str(v).strip())
                             for i, v in enumerate(r) if i < len(headers)})
        else:
            import csv, io
            txt = upload.read().decode('utf-8-sig', errors='replace')
            for r in csv.DictReader(io.StringIO(txt)):
                rows.append({(k or '').strip().lower(): (v or '').strip()
                             for k, v in r.items()})
    except Exception as exc:    # noqa: BLE001
        return _Resp({'detail': f'Parse error: {exc}'}, status=400)
    return _Resp(_apply_email_rows(rows, requester, create_if_missing=True))


@api_view(['GET'])
@_perm_cls([IsAuthenticated])
def user_emails_template(request):
    """Pre-filled .xlsx template for the email/master-user upload."""
    from django.http import HttpResponse
    from io import BytesIO
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return _Resp({'detail': 'openpyxl missing.'}, status=500)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Users'
    headers = ['name', 'username', 'email', 'first_name', 'last_name', 'title']
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='0D1B2A')
        c.alignment = Alignment(horizontal='center')
    samples = [
        ['Pako Kago',       'pkago',      'pkago@alphadirect.co.bw',      'Pako',      'Kago',       'finance_manager'],
        ['Oprah Mogomotsi', 'omogomotsi', 'omogomotsi@alphadirect.co.bw', 'Oprah',     'Mogomotsi',  'accountant'],
        ['Charmaine Bamusi','',           'cbamusi@insurance.co.bw',      'Charmaine', 'Bamusi',     'bookkeeper'],
        ['New Joiner',      '',           'newjoiner@alphadirect.co.bw',  'New',       'Joiner',     'accountant'],
    ]
    for s in samples:
        ws.append(s)
    notes = wb.create_sheet('README')
    notes.append(['Column', 'Meaning'])
    notes.append(['name',       'Optional full name. Resolved fuzzily when username + email blank.'])
    notes.append(['username',   'Preferred. Exact User.username match.'])
    notes.append(['email',      'Email address. Set on the user or used for resolution.'])
    notes.append(['first_name', 'Optional. Populates User.first_name.'])
    notes.append(['last_name',  'Optional. Populates User.last_name.'])
    notes.append(['title',      'Optional — pass to /admin/user-titles/ separately; ignored here.'])
    notes.append([])
    notes.append(['Notes'])
    notes.append(['If a user does not exist and email is set, a new user is created (username derived from email).'])
    notes.append(['Existing users have email + name fields updated in place.'])
    for col, w in zip('ABCDEF', (24, 14, 30, 14, 14, 20)):
        ws.column_dimensions[col].width = w
    for col, w in zip('AB', (22, 80)):
        notes.column_dimensions[col].width = w
    buf = BytesIO(); wb.save(buf); buf.seek(0)
    resp = HttpResponse(buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = 'attachment; filename="master-user-list-template.xlsx"'
    return resp


@api_view(['GET'])
@_perm_cls([IsAuthenticated])
def user_titles_template(request):
    """Pre-filled .xlsx template for user titles upload."""
    from django.http import HttpResponse
    from io import BytesIO
    from core.models import UserProfile
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return _Resp({'detail': 'openpyxl missing.'}, status=500)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Titles'
    ws.append(['name', 'username', 'email', 'title'])
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='0D1B2A')
        c.alignment = Alignment(horizontal='center')
    samples = [
        ['Pako Kago',       'pkago',        'pkago@alphadirect.co.bw',        'finance_manager'],
        ['Oprah Mogomotsi', 'omogomotsi',   'omogomotsi@alphadirect.co.bw',   'accountant'],
        ['',                'ktshutlhedi',  '',                                'finance_analyst'],
        ['Charmaine Bamusi','cbamusi',      'cbamusi@insurance.co.bw',         'bookkeeper'],
    ]
    for s in samples:
        ws.append(s)
    notes = wb.create_sheet('Valid titles')
    notes.append(['value', 'label'])
    for c in notes[1]:
        c.font = Font(bold=True)
    for v, lbl in UserProfile.Title.choices:
        notes.append([v, lbl])
    for col, w in zip('ABCD', (24, 14, 30, 22)):
        ws.column_dimensions[col].width = w
    for col, w in zip('AB', (24, 36)):
        notes.column_dimensions[col].width = w
    buf = BytesIO(); wb.save(buf); buf.seek(0)
    resp = HttpResponse(buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = 'attachment; filename="user-titles-template.xlsx"'
    return resp


@api_view(['GET'])
@_perm_cls([IsAuthenticated])
def user_company_access_matrix(request):
    """
    Returns the full grant matrix for the admin UI:
      {
        'users':      [{username, full_name, email, title}],
        'companies':  [{id, code, name}],
        'grants':     {username: {company_id: {can_view, can_write}}}
      }
    Caller must be superuser / administrator / CFO.
    """
    from django.contrib.auth import get_user_model
    from core.models import UserCompanyAccess, allowed_company_ids, UserProfile
    requester = request.user
    if not (requester.is_superuser or allowed_company_ids(requester) == {'*'}):
        return _Resp({'detail': 'Permission denied.'}, status=403)
    User = get_user_model()
    users = list(User.objects.filter(is_active=True).order_by('username'))
    companies = list(Company.objects.order_by('code'))
    grants = {}
    for g in UserCompanyAccess.objects.select_related('user', 'company'):
        u = grants.setdefault(g.user.username, {})
        u[str(g.company_id)] = {'can_view': g.can_view, 'can_write': g.can_write}
    # Pull profile titles in one query
    profs = {p.user_id: p.title for p in UserProfile.objects.all()}
    return _Resp({
        'users': [{
            'username':  u.username,
            'email':     u.email,
            'full_name': (u.get_full_name() or u.username),
            'title':     profs.get(u.id, ''),
            'is_superuser': u.is_superuser,
        } for u in users],
        'companies': [{
            'id':   str(c.id),
            'code': c.code,
            'name': c.name,
        } for c in companies],
        'grants': grants,
    })


# ---------------------------------------------------------------------------
# Internal tasking + presence (CFO directive 2026-05-24).
# ---------------------------------------------------------------------------

@api_view(['GET'])
@_perm_cls([IsAuthenticated])
def presence_online_users(request):
    """List users with a heartbeat in the last 5 minutes."""
    from datetime import timedelta
    from django.utils import timezone
    from core.models import OnlinePresence
    cutoff = timezone.now() - timedelta(minutes=5)
    rows = (OnlinePresence.objects
            .select_related('user')
            .filter(last_seen__gte=cutoff)
            .order_by('-last_seen'))
    out = []
    for p in rows:
        u = p.user
        out.append({
            'username':  u.username,
            'full_name': (u.get_full_name() or u.username),
            'email':     u.email or '',
            'last_seen': p.last_seen.isoformat(),
            'is_me':     u.pk == request.user.pk,
        })
    return _Resp({'users': out, 'cutoff_minutes': 5})


@api_view(['GET', 'POST'])
@_perm_cls([IsAuthenticated])
def omni_tasks(request):
    """Tasks for the current user.

    GET — returns {inbox, outbox, unread_count}.
      • inbox  = tasks where assignee=me (excludes done/cancelled by default;
                 pass ?include_done=1 to surface finished items).
      • outbox = tasks where assigner=me (always all states).
      • unread_count = inbox items with seen_at IS NULL.

    POST — create a new task. Body: {assignee_username, title, body,
      due_at, priority}. Returns the new task row.
    """
    from core.models import OmniTask
    from django.contrib.auth import get_user_model
    User = get_user_model()
    me = request.user

    if request.method == 'GET':
        inc_done = (request.query_params.get('include_done') or '').strip() in ('1', 'true', 'yes')
        inbox_q  = OmniTask.objects.filter(assignee=me).select_related('assigner', 'assignee')
        if not inc_done:
            inbox_q = inbox_q.exclude(status__in=[
                OmniTask.Status.DONE, OmniTask.Status.CANCELLED,
            ])
        outbox_q = OmniTask.objects.filter(assigner=me).select_related('assigner', 'assignee')

        def _ser(t):
            return {
                'id':              str(t.id),
                'title':           t.title,
                'body':            t.body,
                'priority':        t.priority,
                'status':          t.status,
                'due_at':          t.due_at.isoformat() if t.due_at else None,
                'created_at':      t.created_at.isoformat(),
                'completed_at':    t.completed_at.isoformat() if t.completed_at else None,
                'seen_at':         t.seen_at.isoformat() if t.seen_at else None,
                # Structured payment marker (CFO 2026-08-31: "don't mix Tasks and
                # payments"). Payment-authorisation tasks stamp source='payment_request'
                # at both create sites, so the inbox can split them onto their own
                # tab WITHOUT guessing from the title. is_payment drives that split.
                'source':          t.source or '',
                'is_payment':      t.source == 'payment_request',
                'assigner':        {'username': t.assigner.username,
                                    'full_name': t.assigner.get_full_name() or t.assigner.username},
                'assignee':        {'username': t.assignee.username,
                                    'full_name': t.assignee.get_full_name() or t.assignee.username},
            }
        return _Resp({
            'inbox':         [_ser(t) for t in inbox_q.order_by('-created_at')[:200]],
            'outbox':        [_ser(t) for t in outbox_q.order_by('-created_at')[:200]],
            'unread_count':  inbox_q.filter(seen_at__isnull=True).count(),
        })

    # POST — create
    body = request.data or {}
    assignee_username = (body.get('assignee_username') or '').strip()
    title             = (body.get('title') or '').strip()
    if not assignee_username:
        return _Resp({'detail': 'assignee_username is required.'}, status=400)
    if not title:
        return _Resp({'detail': 'title is required.'}, status=400)
    assignee = User.objects.filter(username__iexact=assignee_username, is_active=True).first()
    if assignee is None:
        return _Resp({'detail': f'No active user with username {assignee_username}.'},
                     status=404)
    from datetime import date as _date
    due_at_raw = (body.get('due_at') or '').strip()
    due_at = None
    if due_at_raw:
        try: due_at = _date.fromisoformat(due_at_raw)
        except ValueError:
            return _Resp({'detail': 'due_at must be ISO date YYYY-MM-DD.'}, status=400)
    priority = (body.get('priority') or OmniTask.Priority.NORMAL).strip()
    if priority not in {p.value for p in OmniTask.Priority}:
        priority = OmniTask.Priority.NORMAL
    # Quick Task (Omni Mobile G): a caller may stamp a known source and an
    # idempotency key so a double-tap / network retry resolves to the SAME task
    # instead of a duplicate. The source is ALLOW-LISTED so a client cannot spoof
    # a privileged origin (e.g. 'payment_request'); anything else drops to "".
    _ALLOWED_CLIENT_SOURCES = {'quick_task'}
    source = (body.get('source') or '').strip()
    if source not in _ALLOWED_CLIENT_SOURCES:
        source = ''
    client_key = (body.get('client_key') or '').strip()[:64]
    if client_key:
        _existing = OmniTask.objects.filter(assigner=me, client_key=client_key).first()
        if _existing is not None:
            return _Resp({'id': str(_existing.id), 'title': _existing.title,
                          'status': _existing.status,
                          'assignee': _existing.assignee.username,
                          'deduped': True}, status=200)
    # Protect the CFO's inbox (CFO 2026-07-27): staff may not put an Urgent or
    # short-notice task on the CFO — urgent needs 24h, non-urgent 48h notice.
    # Executives bypass. Only tasks assigned TO the CFO are governed, so this
    # never blocks the CFO handing out his own urgent, due-today work.
    from django.utils import timezone as _tz
    from core.task_guard import cfo_task_notice_error
    _notice_err = cfo_task_notice_error(me, assignee, priority, due_at, _tz.localdate())
    if _notice_err:
        return _Resp({'detail': _notice_err}, status=403)
    # Optional attachment (CFO 2026-07-15): a screenshot / doc sent with the
    # task. Validate BEFORE creating the task so a bad file can't leave an
    # orphan task in the assignee's inbox. Multipart only; JSON carries no file.
    attachment = request.FILES.get('attachment') if hasattr(request, 'FILES') else None
    if attachment is not None:
        err = _validate_task_attachment(attachment)
        if err:
            return _Resp({'detail': err}, status=400)
    t = OmniTask.objects.create(
        assigner=me, assignee=assignee,
        title=title[:200],
        body=(body.get('body') or '')[:5000],
        due_at=due_at, priority=priority,
        status=OmniTask.Status.PENDING,
        source=source, client_key=client_key,
    )
    if attachment is not None:
        from core.models import OmniTaskComment
        OmniTaskComment.objects.create(
            task=t, author=me,
            body=(body.get('attachment_caption') or '')[:5000],
            evidence=attachment, new_status='',
        )
    # Email the assignee the FULL task straight away — title, priority, due,
    # who assigned it, and all the details — not just an in-app toast (CFO
    # 2026-07-28). Best-effort: a mail failure must never fail task creation.
    try:
        from taskboard.services import email_task_assigned
        email_task_assigned(t)
    except Exception:  # noqa: BLE001
        pass
    return _Resp({
        'id': str(t.id), 'title': t.title, 'status': t.status,
        'assignee': assignee.username,
    }, status=201)


@api_view(['GET', 'PATCH'])
@_perm_cls([IsAuthenticated])
def omni_task_detail(request, task_id):
    """Single task view + transitions.

    PATCH body: {status, body, due_at, priority, comment}.
      • If `comment` set, append OmniTaskComment with optional new_status.
      • Status transition writes completed_at on DONE/PARTIAL/CANCELLED.
      • Privacy: only assigner or assignee can read/write.
    """
    from django.utils import timezone
    from core.models import OmniTask, OmniTaskComment
    me = request.user
    try:
        t = OmniTask.objects.select_related('assigner', 'assignee').get(pk=task_id)
    except OmniTask.DoesNotExist:
        return _Resp({'detail': 'Task not found.'}, status=404)
    if me.pk not in (t.assigner_id, t.assignee_id) and not me.is_superuser:
        return _Resp({'detail': 'Permission denied.'}, status=403)

    if request.method == 'GET':
        if me.pk == t.assignee_id and t.seen_at is None:
            t.seen_at = timezone.now()
            t.save(update_fields=['seen_at'])
        comments = list(t.comments.select_related('author').order_by('created_at'))
        return _Resp({
            'id': str(t.id), 'title': t.title, 'body': t.body,
            'priority': t.priority, 'status': t.status,
            'completion_pct': t.completion_pct,
            'due_at':       t.due_at.isoformat() if t.due_at else None,
            'created_at':   t.created_at.isoformat(),
            'completed_at': t.completed_at.isoformat() if t.completed_at else None,
            'seen_at':      t.seen_at.isoformat() if t.seen_at else None,
            'assigner': {'username': t.assigner.username,
                         'full_name': t.assigner.get_full_name() or t.assigner.username},
            'assignee': {'username': t.assignee.username,
                         'full_name': t.assignee.get_full_name() or t.assignee.username},
            'comments': [{
                'id': str(c.id),
                'author': c.author.username,
                'author_name': c.author.get_full_name() or c.author.username,
                'body': c.body,
                'new_status': c.new_status,
                'created_at': c.created_at.isoformat(),
                'has_evidence': bool(c.evidence),
                'evidence_name': (c.evidence.name or '').split('/')[-1] if c.evidence else '',
                # TASK-ATT-01: drives the Remove button, so the UI never offers a
                # control the server will refuse.
                'can_remove_evidence': bool(c.evidence) and _can_remove_task_evidence(me, t, c),
            } for c in comments],
        })

    # PATCH
    payload = request.data or {}
    # Validate any reply attachment BEFORE mutating the task, so a rejected
    # file can't leave the task in a changed state with no audit comment.
    evidence = request.FILES.get('evidence') if hasattr(request, 'FILES') else None
    if evidence is not None:
        err = _validate_task_attachment(evidence)
        if err:
            return _Resp({'detail': err}, status=400)
    changed = []
    new_status = (payload.get('status') or '').strip()
    # Taskboard completion gate (CFO 2026-07-09, PR #292): DONE must go
    # through POST /taskboard/tasks/<id>/complete/ — a completion note
    # (>=30 chars), validated server-side. Flipping
    # status=done here would bypass the "completion note that can't be
    # faked" control, so it is rejected for EVERYONE (superusers included).
    # partial / blocked / cancelled transitions are unchanged (Phase 1).
    if new_status == OmniTask.Status.DONE:
        return _Resp({
            'detail': ('Completing a task requires a completion note — use '
                       'the "Complete task" button (taskboard completion gate).'),
            'code': 'completion_gate',
        }, status=400)
    if new_status and new_status in {s.value for s in OmniTask.Status}:
        t.status = new_status
        if new_status in (OmniTask.Status.DONE, OmniTask.Status.PARTIAL,
                          OmniTask.Status.CANCELLED) and not t.completed_at:
            t.completed_at = timezone.now()
            changed.append('completed_at')
        changed.append('status')
    if 'due_at' in payload:
        from datetime import date as _date
        raw_val = payload.get('due_at')
        # Coerce to str first — a JSON body can send a dict/number/list, and
        # .strip() on those would 500 (AttributeError). str() makes every bad
        # shape a clean 400 via fromisoformat.
        raw = (str(raw_val) if raw_val is not None else '').strip()
        try:
            t.due_at = _date.fromisoformat(raw) if raw else None
        except ValueError:
            return _Resp({'detail': 'Due date is not valid (use YYYY-MM-DD).'}, status=400)
        changed.append('due_at')
    if 'priority' in payload:
        prio = (payload.get('priority') or '').strip()
        if prio in {p.value for p in OmniTask.Priority}:
            t.priority = prio; changed.append('priority')
    # Same CFO-inbox guard as create (CFO 2026-07-27): staff must not sneak a
    # task to urgent / short notice via an edit after raising a compliant one.
    # Only fires when the governed fields (priority / due date) are touched; the
    # in-memory mutations above are discarded because we return before save().
    if 'priority' in payload or 'due_at' in payload:
        from core.task_guard import cfo_task_notice_error
        _notice_err = cfo_task_notice_error(me, t.assignee, t.priority, t.due_at,
                                            timezone.localdate())
        if _notice_err:
            return _Resp({'detail': _notice_err}, status=403)
    # completion_pct: the assignee records how far along they are (CFO 2026-07-13
    # "let them record the % complete"). Any whole number 0-100. Kept in step with
    # status: done => 100, and moving off partial back to pending clears it.
    if 'completion_pct' in payload:
        raw_pct = payload.get('completion_pct')
        try:
            pct = max(0, min(100, int(raw_pct)))
        except (TypeError, ValueError):
            pct = None
        if pct is not None:
            t.completion_pct = pct; changed.append('completion_pct')
    if new_status == OmniTask.Status.PARTIAL and 'completion_pct' not in payload \
            and t.completion_pct is None:
        t.completion_pct = 50; changed.append('completion_pct')   # sensible default
    # Reassign to someone else (CFO 2026-07-14): the current assignee or the
    # assigner may hand a task to another person — e.g. "this isn't mine". The
    # move is recorded as a comment; the new person's seen flag is reset.
    reassign_to = (payload.get('reassign_to') or '').strip()
    if reassign_to:
        from django.contrib.auth.models import User as _User
        new_a = _User.objects.filter(username__iexact=reassign_to, is_active=True).first()
        if new_a is None:
            return _Resp({'detail': f'No active user “{reassign_to}”.'}, status=400)
        if new_a.id != t.assignee_id:
            # CFO-inbox guard (CFO 2026-07-27): reassigning is the third way a
            # non-exec could land an urgent / short-notice task on the CFO —
            # raise it to yourself, then hand it over. Check against the NEW
            # assignee and the task's final priority / due date, before mutating.
            from core.task_guard import cfo_task_notice_error
            _notice_err = cfo_task_notice_error(me, new_a, t.priority, t.due_at,
                                                timezone.localdate())
            if _notice_err:
                return _Resp({'detail': _notice_err}, status=403)
            old_a = t.assignee
            t.assignee = new_a
            t.seen_at = None
            changed += ['assignee', 'seen_at']
            reason = (payload.get('reassign_reason') or '').strip()
            note = (f'Reassigned from {old_a.get_full_name() or old_a.username} to '
                    f'{new_a.get_full_name() or new_a.username}')
            if reason:
                note += f' — {reason}'
            OmniTaskComment.objects.create(task=t, author=me, body=note[:5000], new_status='')
    if 'body' in payload and me.pk == t.assigner_id:
        t.body = (payload.get('body') or '')[:5000]; changed.append('body')
    if changed:
        t.save(update_fields=changed + ['updated_at'])

    comment_body = (payload.get('comment') or '').strip()
    # Optional attachment on a reply (CFO 2026-07-15) — e.g. proof of a blocker
    # or of completion (validated at the top of this branch). A file on its own
    # is enough to log a comment.
    if comment_body or new_status or evidence is not None:
        _c = OmniTaskComment.objects.create(
            task=t, author=me, body=comment_body[:5000],
            new_status=new_status if new_status in {s.value for s in OmniTask.Status} else '',
            evidence=evidence,
        )
        # Tell the people the comment is aimed at (CFO 2026-07-29). He asked the
        # loader for supporting documents on a payment authorisation and nobody
        # was notified, so the request sat in Omni unread. Payment
        # authorisations only for now — see taskboard.services.email_task_comment.
        if comment_body:
            from taskboard import services as _tb_services
            _tb_services.email_task_comment(t, _c, me)
    return _Resp({'id': str(t.id), 'status': t.status,
                  'completed_at': t.completed_at.isoformat() if t.completed_at else None})


@api_view(['POST'])
@_perm_cls([IsAuthenticated])
def omni_task_handover(request):
    """Hand ALL my open tasks to someone else — the 'going on leave' case
    (CFO 2026-07-14). Body: {to_username, reason?}. Reassigns every task
    currently assigned to me that isn't done/cancelled; records each move."""
    from django.contrib.auth.models import User
    from core.models import OmniTask, OmniTaskComment
    me = request.user
    to = (request.data.get('to_username') or '').strip()
    target = User.objects.filter(username__iexact=to, is_active=True).first()
    if target is None:
        return _Resp({'detail': f'No active user “{to}”.'}, status=400)
    if target.id == me.id:
        return _Resp({'detail': 'Pick someone other than yourself.'}, status=400)
    reason = (request.data.get('reason') or '').strip()
    qs = (OmniTask.objects.filter(assignee=me)
          .exclude(status__in=[OmniTask.Status.DONE, OmniTask.Status.CANCELLED]))
    n = 0
    for t in qs:
        t.assignee = target
        t.seen_at = None
        t.save(update_fields=['assignee', 'seen_at', 'updated_at'])
        note = (f'Handed over from {me.get_full_name() or me.username} to '
                f'{target.get_full_name() or target.username}')
        if reason:
            note += f' — {reason}'
        OmniTaskComment.objects.create(task=t, author=me, body=note[:5000], new_status='')
        n += 1
    return _Resp({'reassigned': n,
                  'to': target.get_full_name() or target.username})


# ---------------------------------------------------------------------------
# Task assignee picker + attachments (CFO 2026-07-15, "task issues" email).
#   1. The "select person" dropdown must list ONLY people on the payroll and
#      must NOT show the system/service accounts (System Admin, Administrator).
#      It is sourced from active payroll.Employee rows linked to an active login
#      — NOT from /admin/user-emails/ (which lists every account, is admin-only,
#      and silently 403s for ordinary staff so their picker came up empty).
#   2. Names carry job title + department so near-duplicates are told apart
#      (e.g. Kago Tshutlhedi, Manager – Finance vs Pako Kago, Senior Associate).
#   3. Screenshots/docs can be attached to a task (create) or a reply (comment)
#      via OmniTaskComment.evidence, served back through an auth-gated endpoint.
# ---------------------------------------------------------------------------

# System / service logins that are on the register for technical reasons but are
# never a person you hand a task to. Matched case-insensitively on username.
_TASK_SYSTEM_USERNAMES = {
    'admin', 'administrator', 'system', 'systemadmin', 'sysadmin',
    'root', 'omni', 'service', 'support', 'test', 'demo',
    'task-incentive-bot',   # the incentive automation maker (Manus QC 2026-08-27)
}
_TASK_SYSTEM_NAMES = {'system admin', 'administrator', 'system administrator'}

_TASK_ATTACH_MAX_BYTES = 10 * 1024 * 1024   # 10 MB (matches the model help text)
_TASK_ATTACH_EXTS = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.heic', '.bmp',      # images
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.txt',       # docs
    '.msg', '.eml',                                                 # emails
}


def _validate_task_attachment(f):
    """Return an error string if the uploaded file is not an allowed task
    attachment, else None. Keeps executables/scripts out and caps size."""
    import os
    if f.size and f.size > _TASK_ATTACH_MAX_BYTES:
        return 'Attachment is too large (max 10 MB).'
    ext = os.path.splitext(f.name or '')[1].lower()
    if ext not in _TASK_ATTACH_EXTS:
        return ('That file type is not allowed. Attach an image, PDF, Word/Excel '
                'doc, CSV, text or email file.')
    return None


# Relevance tiers for the assignee picker (CFO 2026-08-31): the viewer sees
# their OWN department first, then executives, then managers, then everyone
# else. VIEWER-RELATIVE and never name-hardcoded — it survives staff changes
# because every tier is derived from the person's title/department, not a list
# of people. A person lands in the FIRST tier they qualify for.
_ASSIGNEE_EXEC_RE = re.compile(r'\b(CEO|COO|CFO|CTO|CIO|CHRO|Chief|Executive)\b', re.I)
_ASSIGNEE_MGR_RE  = re.compile(r'\b(Manager|Head|Controller|Lead|Principal|Supervisor)\b', re.I)


def _assignee_tier(title: str, department: str, viewer_dept: str) -> int:
    dept = (department or '').strip().lower()
    vdep = (viewer_dept or '').strip().lower()
    if vdep and dept and dept == vdep:
        return 1                                # your team
    t = title or ''
    # C-suite only — an "Executive Assistant" / "PA to the CEO" / secretary is
    # support staff, not an executive, so exclude those even though the title
    # contains an exec word (Fable 5, 2026-08-31).
    if _ASSIGNEE_EXEC_RE.search(t) and not re.search(r'\b(assistant|secretary|pa)\b', t, re.I):
        return 2                                # executives / C-suite / Exco
    if _ASSIGNEE_MGR_RE.search(t):
        return 3                                # managers
    return 4                                    # everyone else


@api_view(['GET'])
@_perm_cls([IsAuthenticated])
def omni_task_assignees(request):
    """People a task can be handed to — payroll only, no system accounts.

    Any signed-in user may read it (the picker must work for every staff
    member, not just admins). Each row carries job title + department so the
    search can disambiguate people who share a name, plus a viewer-relative
    ``tier`` (1 your-team → 4 everyone-else) and ``recent_count`` (how many
    tasks THIS viewer handed this person in the last 90 days) so the picker can
    surface the right people first. Ordering itself is done client-side.
    """
    from payroll.models import Employee
    from core.models import OmniTask
    from django.db.models import Count
    from django.utils import timezone
    from datetime import timedelta

    # Tier 1 ("your team") = the viewer's own department, unless they have set
    # an assignee_home_department override (e.g. the CFO, whose HR department is
    # C-Suite, choosing to see Finance first). Override never changes their HR
    # record — it only reorders their own picker.
    from core.models import get_user_profile
    _prof = get_user_profile(request.user)
    viewer_dept = ((getattr(_prof, 'assignee_home_department', '') or '').strip()
                   or (getattr(getattr(request.user, 'employee_record', None),
                               'department', '') or ''))
    # How often the viewer has assigned to each person lately → the Frequent row.
    cutoff = timezone.now() - timedelta(days=90)
    recent = {r['assignee__username']: r['c'] for r in
              OmniTask.objects.filter(assigner=request.user, created_at__gte=cutoff)
              .values('assignee__username').annotate(c=Count('id'))
              if r['assignee__username']}

    # Exclude test / automated accounts (is_test_record) — the assignee list is
    # "payroll only, no system accounts", and a QA/bot Employee is neither a real
    # payroll person nor someone a task should ever be handed to. This is the
    # durable guard: the flag is set on the record itself, so a future bot that
    # is flagged never leaks here the way the "Manus Reviewer" QA account did
    # (Manus QC 2026-09-01). The username/name sets below stay as belt-and-braces
    # for an account that was never flagged.
    emps = (Employee.objects
            .filter(status='active', user__isnull=False, user__is_active=True)
            .exclude(is_test_record=True)
            .select_related('user')
            .order_by('full_name'))
    rows, seen = [], set()
    for e in emps:
        u = e.user
        if u.username.lower() in _TASK_SYSTEM_USERNAMES:
            continue
        if (e.full_name or '').strip().lower() in _TASK_SYSTEM_NAMES:
            continue
        if u.id in seen:                       # OneToOne, but guard anyway
            continue
        seen.add(u.id)
        rows.append({
            'username':   u.username,
            'full_name':  e.full_name or u.get_full_name() or u.username,
            'title':      e.job_title or '',
            'department': e.department or '',
            'tier':         _assignee_tier(e.job_title, e.department, viewer_dept),
            'recent_count': recent.get(u.username, 0),
        })
    return _Resp({'assignees': rows, 'count': len(rows),
                  'viewer_department': viewer_dept})


def _can_remove_task_evidence(user, task, comment):
    """TASK-ATT-01: who may take a task attachment back down.

    The uploader (comment author), the task's assigner, or a superuser. NOT the
    assignee when they did not upload it — evidence handed TO someone must not be
    destroyable by them.
    """
    return (user.pk in (comment.author_id, task.assigner_id)
            or bool(user.is_superuser))


@api_view(['GET', 'DELETE'])
@_perm_cls([IsAuthenticated])
def omni_task_comment_file(request, task_id, comment_id):
    """Read — or remove — a task comment's attachment.

    GET streams it. Auth-gated: only the task's assigner, assignee or a
    superuser may read it (never a bare MEDIA url — see the
    media-404/auth-endpoint fix, ba46fa6).

    DELETE removes it (TASK-ATT-01, bug 83594e5d, D. Ikgopoleng 2026-09-03:
    "no way to remove an attachment once uploaded to a task... incorrectly
    uploaded documents, especially those containing personal or confidential
    information, stay exposed until IT manually intervenes"). The BYTES are
    deleted from storage, not merely unlinked in the DB — a lingering file is
    the exposure the reporter is asking us to close. The comment row itself
    stays (the activity feed is append-only) and a new line records who took
    the file down and when, plus an AuditLog row.
    """
    from django.http import FileResponse, Http404

    from core.audit import _client_ip
    from core.models import AuditLog, OmniTask, OmniTaskComment
    me = request.user
    t = OmniTask.objects.filter(pk=task_id).first()
    if t is None:
        raise Http404
    if me.pk not in (t.assigner_id, t.assignee_id) and not me.is_superuser:
        return _Resp({'detail': 'Permission denied.'}, status=403)
    c = OmniTaskComment.objects.filter(pk=comment_id, task_id=t.id).first()
    if c is None or not c.evidence:
        raise Http404

    if request.method == 'DELETE':
        if not _can_remove_task_evidence(me, t, c):
            return _Resp({'detail': 'Only the person who attached the file, the '
                                    'task owner, or an administrator can remove it.'},
                         status=403)
        name = (c.evidence.name or 'attachment').split('/')[-1]
        c.evidence.delete(save=False)          # drops the bytes from storage
        c.evidence = None
        c.save(update_fields=['evidence', 'updated_at'])
        who = me.get_full_name() or me.username
        OmniTaskComment.objects.create(
            task=t, author=me, new_status='',
            body=f'Attachment "{name}" was removed by {who}.')
        AuditLog.objects.create(
            table_name='core.OmniTaskComment', record_id=str(c.id),
            action=AuditLog.Action.DELETE,
            old_values={'evidence_name': name},
            user=me, ip_address=_client_ip(request),
            description=f'Removed task attachment "{name}" from task {t.id}')
        return _Resp({'detail': 'Attachment removed.', 'removed': name})

    return FileResponse(c.evidence.open('rb'), as_attachment=False,
                        filename=(c.evidence.name or 'attachment').split('/')[-1])


# ---------------------------------------------------------------------------
# Digital assistant asset (CFO directive 2026-05-26).
# Upload a GLB / GLTF file to back the AriaRatGLB widget. One slot per slug;
# 'rat' is the only slug today. File lives at MEDIA_ROOT/aria/<slug>.glb so
# it survives container rebuilds (media_data is a docker volume).
# ---------------------------------------------------------------------------

ASSISTANT_ASSET_DIR = 'aria'
# 200 MB cap — Meshy-generated photoreal rats can exceed 60 MB once
# 600k+ tris come through with full PBR textures. Larger files just
# stream from the docker volume.
ASSISTANT_ASSET_MAX_BYTES = 200 * 1024 * 1024


def _assistant_path(slug: str) -> str:
    import os
    from django.conf import settings as _s
    safe = ''.join(c for c in slug if c.isalnum() or c in '-_').lower()[:32] or 'rat'
    d = os.path.join(_s.MEDIA_ROOT, ASSISTANT_ASSET_DIR)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f'{safe}.glb')


@api_view(['POST'])
@_perm_cls([IsAuthenticated])
def assistant_asset_upload(request):
    """Upload a GLB. Body: multipart, field 'file', optional 'slug' (default rat).

    Admin / CFO / superuser only.
    """
    requester = request.user
    if not (requester.is_superuser
            or getattr(getattr(requester, 'profile', None), 'is_administrator', False)
            or (getattr(getattr(requester, 'profile', None), 'title', '') or '').lower() == 'cfo'):
        return _Resp({'detail': 'Permission denied.'}, status=403)

    upload = request.FILES.get('file')
    if upload is None:
        return _Resp({'detail': "Attach a file under field name 'file'."}, status=400)
    if upload.size > ASSISTANT_ASSET_MAX_BYTES:
        return _Resp({'detail': f'File too large ({upload.size} bytes); '
                                f'cap is {ASSISTANT_ASSET_MAX_BYTES}.'}, status=400)
    name = (upload.name or '').lower()
    if not (name.endswith('.glb') or name.endswith('.gltf')):
        return _Resp({'detail': 'Expected a .glb or .gltf file.'}, status=400)

    slug = (request.data.get('slug') or 'rat').strip()
    path = _assistant_path(slug)
    with open(path, 'wb') as fh:
        for chunk in upload.chunks():
            fh.write(chunk)
    import os
    return _Resp({
        'ok':       True,
        'slug':     slug,
        'size':     os.path.getsize(path),
        'asset_url': f'/api/v1/admin/digital-assistant/asset/{slug}.glb',
    })


@api_view(['GET', 'HEAD'])
@_perm_cls([])
def assistant_asset_serve(request, slug: str):
    """Public — serves the GLB for the AriaRatGLB widget."""
    import os
    from django.http import FileResponse, Http404
    safe_slug = slug.replace('.glb', '').replace('.gltf', '')
    path = _assistant_path(safe_slug)
    if not os.path.exists(path):
        raise Http404('asset not found — upload via /settings/digital-assistant')
    resp = FileResponse(open(path, 'rb'),
                        content_type='model/gltf-binary',
                        as_attachment=False,
                        filename=f'{safe_slug}.glb')
    resp['Cache-Control'] = 'public, max-age=3600'
    resp['Access-Control-Allow-Origin'] = '*'
    return resp


def _user_for_email(email):
    """The person a module grant is ABOUT, so the monthly report names them
    rather than the administrator who made the change."""
    from django.contrib.auth.models import User as _U
    return _U.objects.filter(email__iexact=(email or '').strip()).first()


class NamedModuleAccessViewSet(viewsets.ModelViewSet):
    """
    Module access held as data — the thing that stops these requests reaching
    the CFO (directive 2026-09-15).

    GET/POST/PATCH/DELETE /api/v1/module-access/

    A full administrator or a delegated access administrator may grant and
    revoke here. Revoking sets is_active=False; rows are never deleted, because
    who could open a legal claims book last March is an audit question.
    """
    queryset           = NamedModuleAccess.objects.select_related('granted_by')
    serializer_class   = NamedModuleAccessSerializer
    permission_classes = [IsAuthenticated]
    filter_backends    = [filters.SearchFilter]
    search_fields      = ['email', 'module', 'note']

    def _require_access_admin(self, email=None):
        profile = get_user_profile(self.request.user)
        if profile and profile.can_administer_users:
            return
        if is_access_delegate(profile):
            # No self-grant — the same rule the profile guard applies, for the
            # same reason (Fable review, 15-Sep-2026: the two guards must not
            # disagree about whether a delegate can widen their own access).
            own = (getattr(self.request.user, 'email', '') or '').strip().lower()
            if email and own and email.strip().lower() == own:
                raise PermissionDenied(
                    'You cannot give yourself access. Ask the CFO.'
                )
            return
        raise PermissionDenied(
            'Only an administrator or the access administrator can change '
            'module access.'
        )

    def perform_create(self, serializer):
        self._require_access_admin(email=serializer.validated_data.get('email'))
        obj = serializer.save(granted_by=self.request.user)
        log_user_admin_change(
            self.request.user, _user_for_email(obj.email), AuditLog.Action.CREATE,
            new_values={'module': obj.module, 'email': obj.email, 'is_active': obj.is_active},
            request=self.request,
            description=f'Granted {obj.module} access to {obj.email}',
            table_name='core.NamedModuleAccess', record_id=obj.pk,
        )

    def perform_update(self, serializer):
        before = self.get_object()
        self._require_access_admin(
            email=serializer.validated_data.get('email', before.email))
        _old = {'module': before.module, 'email': before.email, 'is_active': before.is_active}
        obj = serializer.save()
        log_user_admin_change(
            self.request.user, _user_for_email(obj.email), AuditLog.Action.UPDATE,
            old_values=_old,
            new_values={'module': obj.module, 'email': obj.email, 'is_active': obj.is_active},
            request=self.request,
            description=(f'{"Restored" if obj.is_active else "Revoked"} '
                         f'{obj.module} access for {obj.email}'),
            table_name='core.NamedModuleAccess', record_id=obj.pk,
        )

    def perform_destroy(self, instance):
        # Soft delete — the audit trail keeps the history.
        self._require_access_admin(email=instance.email)
        instance.is_active = False
        instance.save(update_fields=['is_active'])
        log_user_admin_change(
            self.request.user, _user_for_email(instance.email), AuditLog.Action.DELETE,
            old_values={'is_active': True}, new_values={'is_active': False},
            request=self.request,
            description=f'Revoked {instance.module} access for {instance.email}',
            table_name='core.NamedModuleAccess', record_id=instance.pk,
        )
