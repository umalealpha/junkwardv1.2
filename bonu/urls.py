from django.urls import path

from bonu.schedule_views import (schedule_export, schedule_gap, schedule_insights,
                                 schedule_kpis, schedule_row_detail,
                                 schedule_row_history, schedule_rows,
                                 schedule_sheets, schedule_upload, schedule_validate)
from bonu.capture import capture_forms, capture_read_invoice, capture_submit
from bonu.cases import case_detail, case_event_add, cases
from bonu.payment_bridge import bonu_payables, link_firm_vendor, raise_bonu_payment
from bonu.legal import (legal_create, legal_detail, legal_monthly_bonus,
                        legal_overview, legal_report, legal_settings)
from bonu.legal_bills import (legal_bill_allocate, legal_bill_read, legal_bill_stage,
                              legal_bills, legal_cap_board, legal_client_search)
from bonu.views import (ai_review, confirm_document, dashboard, discard_document,
                        document_detail, documents, forensics, invoice_lines,
                        member_case, panel,
                        queries, query_update, retainers, upload_invoice)
from bonu.member_views import (member_check, member_edit, member_list,
                               member_upload)

urlpatterns = [
    path('bonu/dashboard/', dashboard, name='v1-bonu-dashboard'),
    path('bonu/forensics/', forensics, name='v1-bonu-forensics'),
    # The transactions behind a consolidated billed figure (Kutlo Keitumele,
    # 11 Aug 2026) — so Finance can reconcile rather than take a total on trust.
    path('bonu/lines/', invoice_lines, name='v1-bonu-lines'),
    path('bonu/ai-review/', ai_review, name='v1-bonu-ai-review'),
    path('bonu/retainers/', retainers, name='v1-bonu-retainers'),
    path('bonu/panel/', panel, name='v1-bonu-panel'),
    path('bonu/upload-invoice/', upload_invoice, name='v1-bonu-upload-invoice'),
    # The waiting room: a bill that has been read but not yet agreed by a person.
    path('bonu/documents/', documents, name='v1-bonu-documents'),
    path('bonu/documents/<uuid:doc_id>/', document_detail, name='v1-bonu-document'),
    path('bonu/documents/<uuid:doc_id>/confirm/', confirm_document, name='v1-bonu-doc-confirm'),
    path('bonu/documents/<uuid:doc_id>/discard/', discard_document, name='v1-bonu-doc-discard'),
    # Asking the firm — the only step that recovers money.
    path('bonu/queries/', queries, name='v1-bonu-queries'),
    path('bonu/queries/<uuid:query_id>/', query_update, name='v1-bonu-query-update'),
    path('bonu/member-case/', member_case, name='v1-bonu-member-case'),
    # Call-centre claim intake + the forward-looking case register (Phase 1).
    # A matter is opened and allocated to a firm here, instead of only becoming
    # visible once its first bill arrives. Feeds the Panel league + Retainer scorecard.
    path('bonu/cases/', cases, name='v1-bonu-cases'),
    path('bonu/cases/<uuid:case_id>/', case_detail, name='v1-bonu-case-detail'),
    path('bonu/cases/<uuid:case_id>/events/', case_event_add, name='v1-bonu-case-event'),
    # Lawyer payment area (Phase 2) — raise a payment for a confirmed bill through
    # the vendor-bank vault + maker-checker, then load it to FNB for CFO release.
    path('bonu/payables/', bonu_payables, name='v1-bonu-payables'),
    path('bonu/invoices/<uuid:invoice_id>/raise-payment/', raise_bonu_payment, name='v1-bonu-raise-payment'),
    path('bonu/firms/<uuid:firm_id>/link-vendor/', link_firm_vendor, name='v1-bonu-firm-link-vendor'),
    # The accountant's workbook, captured in Omni and editable — so the schedule
    # stops living in an emailed Excel file (CFO, 12 Aug 2026).
    # Guided ERP capture — revenue, supplier bills, admin fees, other expenses —
    # so the accountant records each without touching the raw grid (CFO 17 Aug 2026).
    path('bonu/capture/', capture_forms, name='v1-bonu-capture'),
    # Read a supplier invoice (Excel/PDF) and pre-fill the form — DeepSeek via
    # reasoning_complete, on PII-redacted text; saves nothing (CFO 17 Aug 2026).
    path('bonu/capture/supplier/read/', capture_read_invoice, name='v1-bonu-capture-read'),
    path('bonu/capture/<slug:form_key>/', capture_submit, name='v1-bonu-capture-submit'),
    path('bonu/schedule/', schedule_sheets, name='v1-bonu-schedule'),
    path('bonu/schedule/upload/', schedule_upload, name='v1-bonu-schedule-upload'),
    # Pre-flight validator: every check Kutlo's file surfaced on 13-Aug, run against
    # the currently loaded schedule so exceptions are on screen next to the data.
    path('bonu/schedule/validate/', schedule_validate, name='v1-bonu-schedule-validate'),
    # Omni-computed KPIs beside the author's — CFO 13-Aug: "hide the loss" trap.
    path('bonu/schedule/kpis/', schedule_kpis, name='v1-bonu-schedule-kpis'),
    path('bonu/schedule/<slug:key>/rows/', schedule_rows, name='v1-bonu-schedule-rows'),
    path('bonu/schedule/<slug:key>/export/', schedule_export, name='v1-bonu-schedule-export'),
    path('bonu/schedule/rows/<uuid:row_id>/', schedule_row_detail, name='v1-bonu-schedule-row'),
    path('bonu/schedule/rows/<uuid:row_id>/history/', schedule_row_history, name='v1-bonu-schedule-row-history'),
    # The in-house legal office's own book — matters handled here instead of
    # being referred out, and external bills argued down before they are paid.
    # Claims Legal Office, 18 Aug 2026: the daily work of the office was not
    # on any BONU screen.
    path('bonu/legal/', legal_overview, name='v1-bonu-legal'),
    path('bonu/legal/settings/', legal_settings, name='v1-bonu-legal-settings'),
    path('bonu/legal/monthly-bonus/', legal_monthly_bonus, name='v1-bonu-legal-monthly-bonus'),
    # The two office reports (Monthly Fee Note, Quarterly Savings & Bonus) as
    # Word or PDF. Ahead of the generic <slug> create route so it is never eaten.
    path('bonu/legal/report/<slug:kind>/<slug:fmt>/', legal_report, name='v1-bonu-legal-report'),
    # Legal bill capture + the per-client 80K spend cap (Kelvin Kimani, 9 Sep 2026).
    # ALL of these sit ahead of the generic 'bonu/legal/<slug>/' create route
    # below, which would otherwise swallow 'bills', 'cap' and 'clients' as slugs.
    path('bonu/legal/bills/', legal_bills, name='v1-bonu-legal-bills'),
    # Read a bill off the document instead of typing it. Saves NOTHING - it
    # returns a draft, so the duplicate guard and the 80K ceiling still run
    # when the person records it. Ahead of the <uuid> routes below.
    path('bonu/legal/bills/read/', legal_bill_read, name='v1-bonu-legal-bill-read'),
    path('bonu/legal/bills/<uuid:bill_id>/allocate/', legal_bill_allocate,
         name='v1-bonu-legal-bill-allocate'),
    path('bonu/legal/bills/<uuid:bill_id>/stage/', legal_bill_stage,
         name='v1-bonu-legal-bill-stage'),
    # Find a client by NAME or by number — bills arrive carrying a name.
    path('bonu/legal/clients/', legal_client_search, name='v1-bonu-legal-clients'),
    # Who is approaching the cap, before a new bill tips them over.
    path('bonu/legal/cap/', legal_cap_board, name='v1-bonu-legal-cap'),
    path('bonu/legal/<slug:slug>/', legal_create, name='v1-bonu-legal-create'),
    path('bonu/legal/<slug:slug>/<uuid:row_id>/', legal_detail, name='v1-bonu-legal-detail'),
    # Insights — the money-protecting reads over the schedule.
    # The membership roll — who the scheme may actually pay for (CFO 18-Aug-2026).
    path('bonu/members/', member_list, name='v1-bonu-members'),
    path('bonu/members/check/', member_check, name='v1-bonu-member-check'),
    path('bonu/members/upload/', member_upload, name='v1-bonu-member-upload'),
    path('bonu/members/new/', member_edit, name='v1-bonu-member-new'),
    path('bonu/members/<uuid:member_id>/', member_edit, name='v1-bonu-member-edit'),

    path('bonu/insights/', schedule_insights, name='v1-bonu-insights'),
    path('bonu/insights/gap/', schedule_gap, name='v1-bonu-insights-gap'),
]
