"""
iso_compliance/aml_views.py — the AML registers, usable from the app.

Written 2026-09-16. Background, because it explains every decision below:
the registers were created on 2026-09-09 and, until this file, had no route and
no page. The only way in was Django admin, which the AML/CFT officer cannot
reach (she is not a staff user and holds no groups). So the weekly objectives
that count these registers could only ever read zero — the officer was being
measured on a door that was locked.

Two rules the permissions follow:
  * READ is as wide as the compliance dashboard itself. Anyone already trusted
    to see the cockpit can see the registers behind it.
  * WRITE is the compliance function plus superusers. Deliberately NOT tied to
    the `cfo`/`iso_auditor` groups used elsewhere in this app — neither group
    exists in the database, so anything keyed on them is writable by superusers
    alone. That is the bug this file exists to stop repeating.
"""
from __future__ import annotations

from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.compliance_dashboard import (
    _in_compliance_dept,
    can_view_compliance_dashboard,
    is_aml_officer,
)
from iso_compliance.aml_models import (
    AMLTrainingRecord,
    ComplianceReport,
    SanctionsScreening,
    aml_training_outstanding_qs,
)
from iso_compliance.aml_serializers import (
    AMLTrainingRecordSerializer,
    ComplianceReportSerializer,
    SanctionsScreeningSerializer,
    VendorKYCScreeningSerializer,
)
from procurement.kyc_models import VendorKYC


def can_write_compliance_registers(user) -> bool:
    """The compliance function maintains the compliance registers.

    Superuser is kept as the break-glass, but it must never be the ONLY holder
    again — see the module docstring.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    return is_aml_officer(user) or _in_compliance_dept(user)


def _screening_payload(data, subject_name: str, sanctions_status: str) -> dict:
    """One shape for a supplier screening, whichever way the supplier is known
    (vendor record or payee name), so both go through the same serializer and
    the same list-version rule."""
    pep = data.get('pep_status')
    return {
        'subject_type': SanctionsScreening.SubjectType.SUPPLIER,
        'subject_name': subject_name,
        'list_source': data.get('list_source') or 'UNSC Consolidated',
        'list_version': data.get('list_version') or '',
        'result': (SanctionsScreening.Result.CLEAR
                   if sanctions_status == VendorKYC.SanctionsStatus.CLEAN
                   else SanctionsScreening.Result.POSSIBLE),
        'pep_status': pep if pep in dict(SanctionsScreening.PEP.choices)
        else SanctionsScreening.PEP.UNCHECKED,
        'notes': data.get('notes') or '',
    }


def _sanctions_from_result(screening) -> str:
    """A sanctions-register verdict said in the supplier screen's words.

    `clear` is the only result that means screened-and-clean; `possible` and
    `match` both mean a human still has to look, which is `flagged`.
    """
    if screening is None:
        return VendorKYC.SanctionsStatus.UNCHECKED
    from iso_compliance.aml_models import SanctionsScreening as _S
    if screening.result == _S.Result.CLEAR:
        return VendorKYC.SanctionsStatus.CLEAN
    return VendorKYC.SanctionsStatus.FLAGGED


class _ComplianceRW(IsAuthenticated):
    """Read for anyone who may see the dashboard; write for the compliance function."""

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if not can_view_compliance_dashboard(request.user):
            return False
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        return can_write_compliance_registers(request.user)


class _BaseRegisterViewSet(viewsets.ModelViewSet):
    """A regulatory register: append and correct, NEVER delete.

    `http_method_names` is the point of this class. Shipped as a plain
    ModelViewSet, every one of these registers hands DELETE to everyone who can
    write — and the row most worth deleting is a confirmed sanctions match that
    has not yet been reported to the FIA, because deleting it also silences the
    alarm that says so. A VendorKYC row carries the TIN, the certificate of
    incorporation, the bank letter and the beneficial owners with it.

    Correcting a mistake is a PATCH, and the audit log keeps the old value.
    """

    permission_classes = [_ComplianceRW]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    # Which field records the person who did the work. Stamped from the
    # request so the register cannot claim someone else did it.
    actor_field = None

    def perform_create(self, serializer):
        if self.actor_field:
            serializer.save(**{self.actor_field: self.request.user})
        else:
            serializer.save()


class SanctionsScreeningViewSet(_BaseRegisterViewSet):
    queryset = SanctionsScreening.objects.select_related('screened_by').all()
    serializer_class = SanctionsScreeningSerializer
    actor_field = 'screened_by'

    @action(detail=False, methods=['get'])
    def summary(self, request):
        qs = SanctionsScreening.objects.all()
        return Response({
            'total': qs.count(),
            'clear': qs.filter(result=SanctionsScreening.Result.CLEAR).count(),
            'possible': qs.filter(result=SanctionsScreening.Result.POSSIBLE).count(),
            'match': qs.filter(result=SanctionsScreening.Result.MATCH).count(),
            'awaiting_fia_report': qs.filter(
                result=SanctionsScreening.Result.MATCH,
                reported_to_fia_at__isnull=True).count(),
            'pep_unassessed': qs.filter(
                pep_status=SanctionsScreening.PEP.UNCHECKED).count(),
        })


class AMLTrainingRecordViewSet(_BaseRegisterViewSet):
    queryset = AMLTrainingRecord.objects.select_related('employee').all()
    serializer_class = AMLTrainingRecordSerializer
    actor_field = 'recorded_by'

    @action(detail=False, methods=['get'])
    def outstanding(self, request):
        """Active employees with no CURRENT AML training on record.

        The population comes from `aml_training_outstanding_qs`, the SAME
        callable the weekly objective counts, so this screen and the scoreboard
        cannot disagree. The only thing added here is the lapsed date, so the
        officer can tell "never trained" from "trained, expired".
        """
        latest = (AMLTrainingRecord.objects
                  .filter(employee=OuterRef('pk'))
                  .order_by('-completed_on')
                  .values('completed_on')[:1])
        qs = (aml_training_outstanding_qs()
              .annotate(last_training=Subquery(latest))
              .order_by('full_name'))
        rows = [
            {'employee': e.pk, 'name': str(e),
             'last_training': e.last_training.isoformat() if e.last_training else None}
            for e in qs
        ]
        return Response({'count': len(rows), 'results': rows})


class ComplianceReportViewSet(_BaseRegisterViewSet):
    queryset = ComplianceReport.objects.select_related('prepared_by').all()
    serializer_class = ComplianceReportSerializer
    actor_field = 'prepared_by'

    def get_queryset(self):
        qs = super().get_queryset()
        kind = self.request.query_params.get('kind')
        return qs.filter(kind=kind) if kind else qs


class VendorKYCScreeningViewSet(_BaseRegisterViewSet):
    """Supplier sanctions screening. The document side of VendorKYC is not touched here."""

    queryset = VendorKYC.objects.select_related('contact').all()
    serializer_class = VendorKYCScreeningSerializer

    def create(self, request, *args, **kwargs):
        """Creating a KYC record is `screen`'s job, never a bare POST.

        A plain POST here either duplicates a OneToOne (a 500) or opens a second
        way to make the same record, which is how two callers end up disagreeing
        about which one is authoritative.
        """
        raise MethodNotAllowed('POST', detail='Use the screen action to record a screening.')

    def perform_update(self, serializer):
        """Stamp the screening time when a real verdict is recorded.

        Left to the caller it would drift, and an undated screening is the same
        as no screening as far as a regulator is concerned.
        """
        obj = serializer.save()
        new_status = serializer.validated_data.get('sanctions_status')
        if (new_status and new_status != VendorKYC.SanctionsStatus.UNCHECKED
                and 'sanctions_checked_at' not in serializer.validated_data):
            obj.sanctions_checked_at = timezone.now()
            obj.save(update_fields=['sanctions_checked_at'])

    @action(detail=False, methods=['get'])
    def suppliers(self, request):
        """Who we actually PAID recently — not the whole vendor master.

        The first cut of this listed every active vendor row and returned
        9,410: the same supplier master is replicated per company, so one
        supplier appeared eleven times, and Alpha Direct's own share was 1,018.
        A list nobody can finish is a list nobody starts, and the register had
        already sat at zero for a week.

        Worse, it screened the wrong population. Payments name their payee as
        FREE TEXT and never point at a vendor record, so of the counterparties
        Alpha Direct actually paid in the last 60 days, only two existed in the
        register at all. Screening the register would have missed almost every
        company we sent money to — which is the one thing supplier screening is
        for.

        So this reads the payments. Anything loaded to the bank in the window
        is in scope, whether or not a vendor record exists for it; the ones
        without a record are screened by name in the sanctions register, which
        takes any party. `already_screened` is best-effort by name, because a
        name is the only thing the two sides share.
        """
        import datetime as dt

        from django.db.models import Sum
        from django.utils import timezone

        from billing.models import Contact
        from iso_compliance.aml_models import SanctionsScreening
        from taskboard.models import PaymentRequest

        try:
            days = min(int(request.query_params.get('days', 60)), 365)
        except (TypeError, ValueError):
            days = 60
        cutoff = timezone.now() - dt.timedelta(days=days)
        # Windowed on fnb_loaded_at, not created_at: a request raised 70 days
        # ago and paid 10 days ago is a payment from ten days ago.

        # fnb_loaded_at is the honest marker: it means the instruction reached
        # the bank. An approval in Omni is a workflow record, not a payment.
        # `entity` is free text, so it is resolved POSITIVELY rather than
        # matched on a substring. 'Alpha Direct' as a fragment also matches
        # Alpha Direct Life, Alpha Direct South Africa and Alpha Direct
        # Insurtech - all real group entities - and MISSES a payment raised as
        # the code 'ADIC'. Wrong in both directions and silent in both. The
        # resolver below already exists for exactly this rule (Fable review
        # 2026-07-29, after a substring on this same field burned the
        # claims-are-ADIC control); unknown input returns False rather than
        # falling back to ADIC.
        from taskboard.payment_views import _resolves_to_adic

        window = (PaymentRequest.objects
                  .exclude(fnb_loaded_at=None)
                  .filter(fnb_loaded_at__gte=cutoff)
                  .exclude(payee=''))
        adic_entities = [e for e in set(window.values_list('entity', flat=True))
                         if _resolves_to_adic(e)]
        paid = window.filter(entity__in=adic_entities)

        agg = (paid.values('payee')
               .annotate(n=Sum('total'))
               .order_by('payee'))
        totals = {r['payee']: r['n'] for r in agg}
        counts = {}
        for name in paid.values_list('payee', flat=True):
            counts[name] = counts.get(name, 0) + 1

        vendors = {c.name: c for c in Contact.objects.filter(
            name__in=list(totals), contact_type=Contact.ContactType.VENDOR)}
        kyc_by_contact = {k.contact_id: k for k in VendorKYC.objects.filter(
            contact__in=list(vendors.values()))}
        # The screening ITSELF, not just the fact one happened. A payee with no
        # vendor record is filed in the sanctions register by name, so VendorKYC
        # has nothing to show and every column here read back 'unchecked' —
        # the PEP answer, the notes and the screened date all vanished the
        # moment the dialog closed, and re-opening it offered 'Not yet
        # assessed' again (kbotana, bug 7dc9716b, 2026-09-17). Nothing was lost;
        # this screen simply read a different table from the one it wrote to.
        # `-screened_at` ordering means the FIRST row seen per name is the
        # latest one, which is the one that counts.
        latest_by_name = {}
        for row in SanctionsScreening.objects.filter(
                subject_name__in=list(totals),
                subject_type=SanctionsScreening.SubjectType.SUPPLIER,
                screened_at__gte=cutoff).order_by('-screened_at'):
            latest_by_name.setdefault(row.subject_name, row)
        screened_names = set(latest_by_name)

        rows = []
        for name in sorted(totals):
            c = vendors.get(name)
            k = kyc_by_contact.get(c.pk) if c else None
            scr = latest_by_name.get(name)
            rows.append({
                'name': name,
                'payments': counts.get(name, 0),
                'total_paid': str(totals[name] or 0),
                'contact': c.pk if c else None,
                'has_vendor_record': c is not None,
                'kyc_id': str(k.pk) if k else None,
                'sanctions_status': (k.sanctions_status if k
                                     else _sanctions_from_result(scr)),
                'pep_status': (k.pep_status if k
                               else (scr.pep_status if scr else 'unchecked')),
                'sanctions_checked_at': (
                    k.sanctions_checked_at.isoformat()
                    if (k and k.sanctions_checked_at)
                    else (scr.screened_at.isoformat() if scr else None)),
                # Read from the register for EVERY supplier. A supplier with a
                # vendor record used to show a blank list version even right
                # after it was typed in, because VendorKYC has no such field
                # (rest of bug 7dc9716b, CFO file 2 Medium 18-Sep-2026).
                'list_source': scr.list_source if scr else '',
                'list_version': scr.list_version if scr else '',
                'screening_notes': (k.notes if k
                                    else (scr.notes if scr else '')),
                'already_screened': name in screened_names,
            })

        done = sum(1 for r in rows
                   if r['already_screened'] or r['sanctions_status'] != 'unchecked')
        return Response({
            'days': days,
            'count': len(rows),
            'screened': done,
            'unscreened': len(rows) - done,
            'no_vendor_record': sum(1 for r in rows if not r['has_vendor_record']),
            'results': rows,
        })

    @action(detail=False, methods=['post'])
    def screen(self, request):
        """Record a screening for one supplier, creating the KYC record if needed."""
        if not can_write_compliance_registers(request.user):
            raise PermissionDenied('Only the compliance function may record a screening.')
        sanctions_status = request.data.get('sanctions_status')
        # Recording a screening means it was RUN: the answer is clean or
        # flagged. 'unchecked' would be written to the register as a possible
        # match and then count the supplier as screened (review, 18-Sep-2026).
        if sanctions_status not in (VendorKYC.SanctionsStatus.CLEAN,
                                    VendorKYC.SanctionsStatus.FLAGGED):
            return Response({'detail': 'Record the result: clean or flagged.'}, status=400)

        contact_id = request.data.get('contact')
        if not contact_id:
            # A payee with no vendor record. Fourteen of the sixteen companies
            # Alpha Direct actually paid in the last 60 days are in this state,
            # because payments name their payee as free text. Refusing to
            # screen them would leave almost everything we pay unscreened, so
            # the screening goes into the sanctions register by name, which
            # takes any party. No vendor master data is invented.
            name = (request.data.get('name') or '').strip()
            if not name:
                return Response({'detail': 'Either contact or name is required.'}, status=400)
            # Written THROUGH the serializer, not around it. Writing straight
            # to the ORM here skipped `validate_list_version`, which is the one
            # rule that makes a screening evidence rather than an assertion -
            # a search nobody can repeat cannot be shown to a regulator. That
            # back door would have been in the register the same day the rule
            # was added to it.
            payload = _screening_payload(request.data, name, sanctions_status)
            ser = SanctionsScreeningSerializer(data=payload)
            ser.is_valid(raise_exception=True)
            row = ser.save(screened_by=request.user)
            return Response({'screened_by_name': True, 'id': str(row.pk),
                             'subject_name': row.subject_name}, status=201)
        # An unknown or non-UUID id reached get_or_create as a raw FK write and
        # came back a 500; a customer's id would have opened a VENDOR KYC file
        # against them. Check it is a real, active vendor first.
        from billing.models import Contact
        vendor = Contact.objects.filter(pk=contact_id,
                                        contact_type=Contact.ContactType.VENDOR,
                                        is_active=True).first()
        if vendor is None:
            return Response({'detail': 'No active vendor with that id.'}, status=400)
        # A supplier WITH a vendor record used to skip the register entirely:
        # the list source and version were dropped on the floor (VendorKYC has
        # no field for them), so the dialog re-opened blank and the screening
        # could not be evidenced — the list-version rule the by-name path
        # enforces was never applied here. Found live 18-Sep-2026: both
        # vendor-record screenings in the prior 14 days had no list version
        # anywhere. Now the same evidence row is written, through the same
        # serializer, and the KYC verdict is updated with it.
        ser = SanctionsScreeningSerializer(
            data=_screening_payload(request.data, vendor.name, sanctions_status))
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            row = ser.save(screened_by=request.user)
            kyc, _ = VendorKYC.objects.get_or_create(contact=vendor)
            kyc.sanctions_status = sanctions_status
            kyc.sanctions_checked_at = row.screened_at or timezone.now()
            if request.data.get('pep_status') in dict(VendorKYC.PEPStatus.choices):
                kyc.pep_status = request.data['pep_status']
            if 'notes' in request.data:
                # Present-but-empty means "cleared", not "unchanged".
                kyc.notes = request.data.get('notes') or ''
            kyc.save()
        return Response(VendorKYCScreeningSerializer(kyc).data)
