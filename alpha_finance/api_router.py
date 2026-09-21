"""
alpha_finance/api_router.py

Central DRF router for /api/v1/
"""
from django.urls import path
from rest_framework.routers import DefaultRouter

from documents.views import (
    DocumentConfirmView,
    DocumentDetailView,
    DocumentListView,
    DocumentRejectView,
    DocumentUploadView,
)
from company_cards import api_views as company_cards_api
from banking.api_views import (
    BankAccountViewSet,
    BankRecRuleViewSet,
    BankStatementLineViewSet,
    BankStatementViewSet,
)
from records.api_views import RecordCategoryViewSet, RecordItemViewSet
from assets.control_api import (
    AssetControlPolicyViewSet,
    AssetCountReconciliationView,
    AssetHandoverViewSet,
    AssetRequisitionViewSet,
    MyAssetsView,
    SparePoolUploadView,
    SparePoolView,
)
from assets.api_views import (
    AssetCategoryViewSet,
    AssetDisposalViewSet,
    AssetImportBatchViewSet,
    AssetSignOffViewSet,
    AssetViewSet,
    DepreciationEntryViewSet,
)
from billing.api_views import ContactViewSet, InvoiceViewSet, ReverseChargeEntryViewSet
from billing.vendor_address import (
    vendor_address_access, vendor_address_list, vendor_address_update,
    vendor_address_upload, vendor_address_template,
)
from claims.api_views import (
    ClientLossRatioView, LargeLossClientsView,
    RecoveryImportBatchViewSet, SalvageViewSet, SubrogationViewSet,
    SubrogationPanelViewSet, SubrogationReceiptViewSet,
    SubrogationGLConfigView, SubrogationRealPayImportView,
)
from claims.payment_movement_views import ClaimsPaymentMovementView
from claims.vault_views import (
    claim_forms as claim_forms_vault_list,
    claim_form_download,
    claim_form_upload,
)
from payroll.api_views import (
    EmployeeViewSet, PayrollImportBatchViewSet, PayrollPeriodViewSet,
    PayslipComponentViewSet, PayslipViewSet, TaxBracketViewSet,
)
from payroll.bank_import_views import (
    bank_import_upload, bank_import_list, bank_import_detail,
    bank_import_approve, bank_import_reject,
)
from regulatory.api_views import (
    CapitalCheckView, CapitalRequirementParameterViewSet, RegulatoryCapitalSnapshotViewSet,
)
from regulatory import tax_api
from billing.views import InvoicePDFView
from budgets.views import BudgetViewSet
from budgets.spend_actions import spend_action_page, spend_action_submit
from budgets.spend_views import (
    spend_levy_tracker, spend_request_actual, spend_request_bqa_claim,
    spend_request_claim_pack, spend_request_decide, spend_request_detail,
    spend_request_file, spend_requests,
)
from core.snap_views import SnapClassifyView
from core.mobile_home_views import mobile_home
from core.approvals_views import (my_approvals, my_approval_items, bulk_approve,
                                   decide as approvals_decide, my_approval_history,
                                   approval_brief, approval_pack, push_vapid_key,
                                   push_subscribe)
from core.request_tracker_views import my_requests_summary_view, my_requests_view
from core.team_glance_views import team_glance
from core.dpa_dashboard import dpa_dashboard, dpa_access
from core.security_dashboard import security_dashboard, security_access
from core.processor_dpa import my_processor_dpas
from core.dpo_workbook import dpo_workbook, dpo_workbook_upload
from core.ropa_auto import ropa_auto
from core.ropa_field_scan import RopaFieldScanView
from core.ropa_registry_api import (RopaQueueView, RopaScanView, RopaEntryView,
                                    VendorListView, VendorView, RulebookView)
from core.compliance_dashboard import compliance_dashboard, compliance_access
from iso_compliance.policy_library_api import PolicyLibraryView, LegalCitationView
from core.intel_api import IntelSummaryStaffView, IntelSummaryView
from core.adoption_views import adoption_scoreboard
from core.screen_view_views import screen_view_beacon, screen_view_report
from core.dpa_checklist import checklist, checklist_save, checklist_discipline
from core.dpa_breach import breaches, breach_update
from core.dpa_dsr import dsrs, dsr_update
from core.api_views import (
    AIAccountSuggestView,
    AIInsightView,
    AriaChatView,
    AriaConfirmActionView,
    AuditAskView,
    AuditLogViewSet,
    CompanyViewSet, CurrencyViewSet, ExchangeRateViewSet,
    TaxRateViewSet, UserProfileViewSet, NamedModuleAccessViewSet,
    me_companies, user_company_access_admin,
    user_company_access_bulk, user_company_access_matrix,
    user_company_access_upload, user_company_access_template,
    user_titles, user_titles_upload, user_titles_template,
    user_emails, user_emails_upload, user_emails_template,
    presence_online_users, omni_tasks, omni_task_detail, omni_task_handover,
    omni_task_assignees, omni_task_comment_file,
    assistant_asset_upload, assistant_asset_serve,
)
from integrations.api_views import IntegrationEventViewSet
from ledger.api_views import (
    AccountViewSet, BatchJournalUploadView, FiscalPeriodViewSet, JournalEntryViewSet,
    RecurringJournalEntryViewSet,
)
from core.hris_views import hris_access_status
from core.hris_unlock import hris_unlock, hris_lock, hris_lock_status
from ledger.cfo_upload import (
    cfo_upload_status, cfo_upload_tb,
    cfo_upload_coa, cfo_upload_coa_template,
    cfo_upload_gl, cfo_upload_gl_template,
)
from reinsurance.treaty_upload import (
    cfo_upload_treaty, cfo_upload_treaty_template,
)
from reinsurance.renewal_views import (
    renewal_document as _renewal_document,
    renewal_manifest as _renewal_manifest,
    renewal_ask as _renewal_ask,
)
from reinsurance.controls_api import (
    reinsurance_control_centre as _rc_control_centre,
    reinsurer_list as _rc_reinsurer_list,
    reinsurer_create as _rc_reinsurer_create,
    reinsurer_detail as _rc_reinsurer_detail,
    reinsurer_transition as _rc_reinsurer_transition,
    fac_risk_register as _rc_fac_register,
    fac_risk_detail as _rc_fac_detail,
    security_panel as _rc_security_panel,
)
from reinsurance.document_views import (
    reinsurer_documents as _re_documents,
    verify_document as _re_verify_document,
    document_download as _re_document_download,
)
from reinsurance.history_views import (
    reinsurance_history as _ri_history,
    reinsurance_history_amend as _ri_history_amend,
    reinsurance_history_snapshots as _ri_history_snapshots,
)
from reinsurance.api_views import (
    TreatyDocUploadCommitView, TreatyDocUploadParseView,
)
from payments.api_views import EFTBatchExportView, PaymentAllocationViewSet, PaymentViewSet
from procurement.api_views import (
    GoodsReceiptNoteViewSet,
    POBillMatchViewSet,
    PurchaseOrderViewSet,
    VariancePolicyViewSet,
    VendorBankAccountViewSet,
)
from procurement.claims_api import ClaimsAssessmentViewSet
from fx.api_views import FXRevaluationViewSet
from exceptions.api_views import ExceptionViewSet
from fnb.api_views import (
    fnb_exception_cockpit,
    FNBBatchSubmissionViewSet,
    FNBCredentialView,
    FNBHealthAskView,
    FNBHealthSummaryView,
    FNBRefreshBatchView,
    FNBStatusView,
    FNBSubmitBatchView, FNBBankViewPreview,
    FNBSyncLogViewSet,
    FNBTestConnectionView,
    FNBWebhookEventViewSet,
    FNBWebhookReceiverView,
)
from boardroom.api_views import (
    BookingViewSet,
    RoomViewSet,
)
from petty_cash.api_views import (
    PettyCashLocationViewSet,
    PettyCashReimbursementViewSet,
    PettyCashVoucherViewSet,
)
from reinsurance.api_views import (
    BordereauImportViewSet,
    CessionViewSet,
    ReinsurerViewSet,
    ReinsuranceRecoveryViewSet,
    ReinsuranceTreatyViewSet,
)
from investments.api_views import (
    InvestmentTransactionViewSet,
    InvestmentViewSet,
)
from bank_feeds.api_views import (
    BankFeedConfigViewSet,
    BankFeedRunViewSet,
)
from salvage.api_views import (
    BuyerQuoteViewSet,
    PartCategoryViewSet,
    PublicSalvageDetailView,
    PublicSalvageListView,
    PublicSalvageQuoteSubmitView,
    SaleViewSet,
    SalvageAccessProbeView,
    SalvageApprovalViewSet,
    SalvageItemViewSet,
    VehicleBrandViewSet,
    VehicleModelViewSet,
    salvage_photo,
)
from reporting.ap_aging_upload import ap_aging_upload, ap_aging_snapshot
from reporting.views import (
    ARAgingView,
    GraphiteAgeAnalysisView,
    RenewalReportView,
    GraphitePaymentsView,
    APAgingView,
    AssetMovementView,
    AssetRegisterView,
    AuditPackPdfView,
    AuditPackView,
    BalanceSheetView,
    BudgetVsActualView,
    CashFlowView,
    CashPositionView,
    PrudentialLimitsView,
    CoaMaTreeView,
    ReceivablesSummaryView,
    CFODashboardView,
    EntityProfitLossView,
    ExpenseAnalysisView,
    ExpenseAnalysisDetailView,
    ExpenseAnalysisXlsxView,
    ExpenseAnalysisAIView,
    GeneralLedgerExtractAllView,
    FrozenDriftView,
    FrozenDriftNarrativeView,
    PremiumLapseView,
    CostPerHourView,
    FrozenFigureAckView,
    GeneralLedgerView,
    MAProfitLossView,
    PeerBenchmarkView,
    ManagementPackView,
    ProfitLossView,
    RelatedPartyTransactionsView,
    TrialBalanceView,
    VATReturnView,
)

router = DefaultRouter()

# Core
router.register('companies',      CompanyViewSet,      basename='company')
router.register('currencies',     CurrencyViewSet,     basename='currency')
router.register('exchange-rates', ExchangeRateViewSet, basename='exchange-rate')
router.register('tax-rates',      TaxRateViewSet,      basename='tax-rate')
router.register('user-profiles',  UserProfileViewSet,  basename='user-profile')
router.register('module-access',   NamedModuleAccessViewSet, basename='module-access')
router.register('audit-log',      AuditLogViewSet,     basename='audit-log')

# RBAC (hierarchical roles, permissions, assignments)
from core.rbac_api import (
    RoleViewSet,
    PermissionViewSet,
    AssignmentViewSet,
    RBACUserViewSet,
    RBACAuditViewSet,
)
router.register('rbac/roles',       RoleViewSet,       basename='rbac-role')
router.register('rbac/permissions', PermissionViewSet, basename='rbac-permission')
router.register('rbac/assignments', AssignmentViewSet, basename='rbac-assignment')
router.register('rbac/users',       RBACUserViewSet,   basename='rbac-user')
router.register('rbac/audit',       RBACAuditViewSet,  basename='rbac-audit')

# Ledger
router.register('accounts',       AccountViewSet,       basename='account')
router.register('fiscal-periods', FiscalPeriodViewSet,  basename='fiscal-period')
router.register('journal-entries', JournalEntryViewSet, basename='journal-entry')
router.register('recurring-journal-entries', RecurringJournalEntryViewSet, basename='recurring-je')
# Voucher / TV clearing (maker-checker reversal queue) — CFO directive 2026-05-26
router.register(
    'je-clearings',
    __import__('ledger.api_views',
               fromlist=['JEClearingRequestViewSet']).JEClearingRequestViewSet,
    basename='je-clearing',
)

# Billing
router.register('contacts', ContactViewSet, basename='contact')
router.register('invoices',  InvoiceViewSet, basename='invoice')
# Reverse-charge VAT on imported remote services (VAT Amendment Act No.16
# of 2025, effective 1 June 2026) — see billing/reverse_charge_models.py.
router.register('reverse-charge-entries', ReverseChargeEntryViewSet,
                 basename='reverse-charge-entry')

# Payments
router.register('payments',            PaymentViewSet,           basename='payment')
router.register('payment-allocations', PaymentAllocationViewSet, basename='payment-allocation')

# Banking
router.register('bank-accounts',        BankAccountViewSet,        basename='bank-account')
router.register('bank-statements',      BankStatementViewSet,      basename='bank-statement')
router.register('bank-statement-lines', BankStatementLineViewSet,  basename='bank-statement-line')
# BankRecRuleViewSet was written (banking/api_views.py) with its own docstring
# naming this exact registration line, but the line was never added — so
# reconciliation-rule CRUD has been unreachable since the feature shipped
# (Manus nine-area retest P1, 2026-08-25). Registered; the viewset's existing
# CanViewFinancials gate and company scoping apply unchanged.
router.register('bank-rec-rules',       BankRecRuleViewSet,        basename='bank-rec-rule')

# Integrations
router.register('events', IntegrationEventViewSet, basename='event')

# Budgets
router.register('budgets', BudgetViewSet, basename='budget')

# IFRS 16 leases
from leases.views import LeaseViewSet
router.register('leases', LeaseViewSet, basename='lease')

# Fixed assets
router.register('asset-categories',     AssetCategoryViewSet,     basename='asset-category')
router.register('assets',               AssetViewSet,             basename='asset')
# Records register — physical files, storeroom and chain of custody
router.register('records',              RecordItemViewSet,        basename='record')
router.register('record-categories',    RecordCategoryViewSet,    basename='record-category')
router.register('depreciation-entries', DepreciationEntryViewSet, basename='depreciation-entry')
router.register('asset-disposals',      AssetDisposalViewSet,     basename='asset-disposal')
router.register('asset-imports',        AssetImportBatchViewSet,  basename='asset-import')
router.register('asset-signoffs',       AssetSignOffViewSet,      basename='asset-signoff')
# Asset Control & Handover (CFO spec 2026-09-02)
router.register('asset-requisitions',   AssetRequisitionViewSet,  basename='asset-requisition')
router.register('asset-handovers',      AssetHandoverViewSet,     basename='asset-handover')
router.register('asset-control-policy', AssetControlPolicyViewSet, basename='asset-control-policy')

# Claims recoveries
router.register('subrogations',       SubrogationViewSet,         basename='subrogation')
router.register('subrogation-panel',  SubrogationPanelViewSet,    basename='subrogation-panel')
router.register('subrogation-receipts', SubrogationReceiptViewSet, basename='subrogation-receipt')
router.register('salvages',           SalvageViewSet,             basename='salvage')
router.register('recovery-imports',   RecoveryImportBatchViewSet, basename='recovery-import')

# Payroll
router.register('employees',          EmployeeViewSet,            basename='employee')
router.register('tax-brackets',       TaxBracketViewSet,          basename='tax-bracket')
router.register('payslip-components', PayslipComponentViewSet,    basename='payslip-component')
router.register('payroll-periods',    PayrollPeriodViewSet,       basename='payroll-period')
router.register('payslips',           PayslipViewSet,             basename='payslip')
router.register('payroll-imports',    PayrollImportBatchViewSet,  basename='payroll-import')

# Regulatory (NBFIRA capital adequacy)
router.register('capital-parameters', CapitalRequirementParameterViewSet, basename='capital-parameter')
router.register('capital-snapshots',  RegulatoryCapitalSnapshotViewSet,   basename='capital-snapshot')

# Procurement
router.register('purchase-orders',      PurchaseOrderViewSet,     basename='purchase-order')
router.register('goods-receipt-notes',  GoodsReceiptNoteViewSet,  basename='grn')
router.register('po-bill-matches',      POBillMatchViewSet,       basename='po-bill-match')
router.register('vendor-bank-accounts', VendorBankAccountViewSet, basename='vendor-bank-account')
router.register('variance-policy',      VariancePolicyViewSet,    basename='variance-policy')
# Claims PO — assessment upload -> review -> 2 draft POs (CFO 2026-07-06 port)
router.register('claims-po',            ClaimsAssessmentViewSet,  basename='claims-po')

# FX
router.register('fx-revaluations',     FXRevaluationViewSet,    basename='fx-revaluation')

# Exceptions engine
router.register('exceptions',          ExceptionViewSet,        basename='exception')

# Petty cash (imprest float, voucher maker-checker, monthly reimbursement)
router.register('petty-cash-locations',      PettyCashLocationViewSet,
                basename='petty-cash-location')
router.register('petty-cash-vouchers',       PettyCashVoucherViewSet,
                basename='petty-cash-voucher')
router.register('petty-cash-reimbursements', PettyCashReimbursementViewSet,
                basename='petty-cash-reimbursement')

# Equity register — editable cap table / ESOP (Finance & EXCO gated)
from equity.api_views import (
    StakeholderViewSet, ShareHoldingViewSet, EsopGrantViewSet, VestingTrancheViewSet,
)
router.register('equity/stakeholders', StakeholderViewSet,   basename='equity-stakeholder')
router.register('equity/holdings',     ShareHoldingViewSet,  basename='equity-holding')
router.register('equity/grants',       EsopGrantViewSet,     basename='equity-grant')
router.register('equity/vesting',      VestingTrancheViewSet, basename='equity-vesting')

# Alpha Rooms — boardroom booking (company-wide, shared meeting rooms)
router.register('boardroom-rooms',    RoomViewSet,    basename='boardroom-room')
router.register('boardroom-bookings', BookingViewSet, basename='boardroom-booking')

# Reinsurance (cessions, recoveries, bordereaux)
router.register('reinsurers',              ReinsurerViewSet,
                basename='reinsurer')
router.register('reinsurance-treaties',    ReinsuranceTreatyViewSet,
                basename='reinsurance-treaty')
router.register('reinsurance-cessions',    CessionViewSet,
                basename='reinsurance-cession')
router.register('reinsurance-recoveries',  ReinsuranceRecoveryViewSet,
                basename='reinsurance-recovery')
router.register('reinsurance-bordereaux',  BordereauImportViewSet,
                basename='reinsurance-bordereau')

# Investment register (FVTPL / FVOCI / amortised cost)
router.register('investments',             InvestmentViewSet,
                basename='investment')
router.register('investment-transactions', InvestmentTransactionViewSet,
                basename='investment-transaction')

# Bank feeds (statement-pull layer; complementary to fnb real-time payments)
router.register('bank-feed-configs',       BankFeedConfigViewSet,
                basename='bank-feed-config')
router.register('bank-feed-runs',          BankFeedRunViewSet,
                basename='bank-feed-run')

# FNB Botswana banking integration
router.register('fnb/sync-logs',          FNBSyncLogViewSet,
                basename='fnb-sync-log')
router.register('fnb/batch-submissions',  FNBBatchSubmissionViewSet,
                basename='fnb-batch-submission')
router.register('fnb/webhook-events',     FNBWebhookEventViewSet,
                basename='fnb-webhook-event')

# Salvage Portal (Veritas + ADIC only — see salvage.permissions.IsSalvageUser)
router.register('salvage-items',         SalvageItemViewSet,     basename='salvage-item')
router.register('salvage-categories',    PartCategoryViewSet,    basename='salvage-category')
router.register('salvage-brands',        VehicleBrandViewSet,    basename='salvage-brand')
router.register('salvage-models',        VehicleModelViewSet,    basename='salvage-model')
router.register('salvage/buyer-quotes',  BuyerQuoteViewSet,      basename='salvage-buyer-quote')
router.register('salvage/sales',         SaleViewSet,            basename='salvage-sale')
router.register('salvage/approvals',     SalvageApprovalViewSet, basename='salvage-approval')
# Veritas · Parts & Savings register — people type into these (CFO 2026-09-10).
router.register('salvage/parts/savings',
                __import__('salvage.parts_register',
                           fromlist=['AssessmentSavingViewSet']).AssessmentSavingViewSet,
                basename='salvage-parts-saving')
router.register('salvage/parts/spend',
                __import__('salvage.parts_register',
                           fromlist=['PartsSpendViewSet']).PartsSpendViewSet,
                basename='salvage-parts-spend')
router.register('salvage/parts/contract-pricing',
                __import__('salvage.parts_register',
                           fromlist=['PartsContractPricingViewSet']).PartsContractPricingViewSet,
                basename='salvage-parts-contract')

# NBFIRA Compliance (Phase 2 — quarterly return)
from nbfira.api_views import NBFIRAReturnViewSet, NBFIRACapitalFactorViewSet
router.register('nbfira/returns',         NBFIRAReturnViewSet,         basename='nbfira-return')
router.register('nbfira/capital-factors', NBFIRACapitalFactorViewSet,  basename='nbfira-capital-factor')

# Taskboard — reminder engine + enforced completion (meeting-discipline Phase 1)
from taskboard.api_views import (
    AckNotificationView,
    CompleteTaskView,
    CompletionRateView,
    MyTasksView,
    NotificationsView,
)
from taskboard.cfo_views import (
    AcknowledgeFeedbackView,
    IngestPlanView,
    MyLeagueView,
    PersonalBoardView,
    TaskEvidenceView,
    TaskFeedbackView,
    TaskLeaderboardView,
    TaskOverviewView,
    TaskStatusUpdateView,
)
from taskboard.dropbox_views import (
    drop_box, drafts, draft_detail, submit_draft, payment_request_duplicate,
)
from taskboard.payment_views import (
    payment_requests, payment_request_detail, payment_request_decide,
    payment_request_decide_lines,
    payment_load_override, payment_load_override_decide,
    payment_request_clear, payment_request_parse,
    payment_request_amend,
    payment_request_correct_branch_code, payment_request_cancel,
    payee_bank_lookup, payee_bank_check, payment_request_read_invoice,
    pop_recipient_options,
    payment_request_attachments, payment_request_attachment_file,
    payment_summary, payment_history, reports_index,
    payment_request_fnb_reconcile,
    payment_request_email_catchup, payment_request_mark_paid_bank,
    payment_request_mark_rejected_fnb,
    payment_request_bulk_parse, payment_request_fnb_file,
    payment_request_reconcile_fnb_list,
    payment_exceptions, payment_exception_signoff, payment_exception_clear,
)
from hris.expense_api import (
    ExpenseApproversView, ExpenseApproverQueueView, ExpenseApproveView,
    ExpenseCfoQueueView, ExpenseClaimSubmitView, ExpenseFileView,
    ExpenseGlAccountsView, ExpenseMarkPaidView, ExpenseProcessView,
    ExpenseRejectView, ExpenseSuggestGlView, MyExpenseClaimsView,
)
from customer_refunds.api_views import (
    RefundAiFraudView, RefundApproveView, RefundDetailView, RefundFraudScanView,
    RefundInboundView, RefundLoadToFnbView, RefundMarkPaidView, RefundQueueView,
    RefundRejectView,
)

# Staff privacy notice — login pop-up + acknowledgement (CFO 2026-07-09)
from core.privacy_views import privacy_notice, privacy_notice_ack, privacy_notices_public
from core.qa_view_login import qa_view_open

urlpatterns = [
    # No-sign-in READ-ONLY quality-control view (CFO 2026-07-29). Hands the
    # browser a token for the locked `omni-qa-view` identity, which core.
    # token_auth refuses on anything but GET. Off unless OMNI_QA_VIEW_KEY is set.
    path('auth/qa-view/', qa_view_open, name='v1-qa-view-open'),
    # Payments exception cockpit (CFO's FNB improvement plan, 13-Sep-2026):
    # every payment whose Omni status disagrees with its FNB batch, plus the
    # batches the bank has not settled, with age, amount and owner. Read only.
    path('fnb/exception-cockpit/', fnb_exception_cockpit,
         name='v1-fnb-exception-cockpit'),
    # Claim Forms Vault (CFO 2026-08-12) — Omni's reference library of every
    # blank claim form. Blank templates only (no PII), readable by any staff;
    # upload is admin-gated in the view. Downloads STREAM through the authed view
    # (never MEDIA_URL). Specific segments precede the <uuid> download.
    path('claim-forms/',                  claim_forms_vault_list, name='v1-claim-forms-list'),
    path('claim-forms/upload/',           claim_form_upload,      name='v1-claim-forms-upload'),
    path('claim-forms/<uuid:pk>/download/', claim_form_download,  name='v1-claim-forms-download'),
    # Supplier-address editor (CFO / Kao 2026-07-10) — gated, address-only tool
    # for staff to add or fix a supplier's address individually or by upload.
    # Specific segments precede the <uuid> pattern.
    path('vendor-address/access/',    vendor_address_access,   name='v1-vendor-address-access'),
    path('vendor-address/list/',      vendor_address_list,     name='v1-vendor-address-list'),
    path('vendor-address/template/',  vendor_address_template, name='v1-vendor-address-template'),
    path('vendor-address/upload/',    vendor_address_upload,   name='v1-vendor-address-upload'),
    path('vendor-address/<uuid:pk>/', vendor_address_update,   name='v1-vendor-address-update'),
    # Staff privacy notice pop-up: fetch the current notice + whether the
    # signed-in user has acknowledged it, and record their acknowledgement.
    path('privacy-notice/',     privacy_notice,     name='v1-privacy-notice'),
    path('privacy-notice/ack/', privacy_notice_ack, name='v1-privacy-notice-ack'),
    # Taskboard (meeting-discipline Phase 1): my open tasks, the server-side
    # completion gate, reminders that drive the force-modal, and (DPA-gated)
    # per-person completion rate. Specific segments precede any <uuid> patterns.
    path('taskboard/my-tasks/',        MyTasksView.as_view(),        name='v1-taskboard-my-tasks'),
    path('taskboard/tasks/<uuid:task_id>/complete/', CompleteTaskView.as_view(), name='v1-taskboard-complete'),
    path('taskboard/notifications/',   NotificationsView.as_view(),  name='v1-taskboard-notifications'),
    path('taskboard/notifications/<uuid:notif_id>/ack/', AckNotificationView.as_view(), name='v1-taskboard-ack'),
    path('taskboard/completion-rate/', CompletionRateView.as_view(), name='v1-taskboard-completion-rate'),
    # CFO task oversight + performance feedback + weekly-plan ingest (2026-07-13).
    path('taskboard/overview/',        TaskOverviewView.as_view(),   name='v1-taskboard-overview'),
    path('taskboard/hall-of-fame/',    TaskLeaderboardView.as_view(), name='v1-taskboard-hall-of-fame'),
    # Staff-facing 'My League' (CFO 2026-08-26) — every staff member sees THEIR
    # own score, incentive progress + what is waiting on a manager to confirm.
    path('taskboard/my-league/',       MyLeagueView.as_view(),       name='v1-taskboard-my-league'),
    path('taskboard/my-board/',        PersonalBoardView.as_view(),  name='v1-taskboard-my-board'),
    path('taskboard/ingest-plan/',     IngestPlanView.as_view(),     name='v1-taskboard-ingest'),
    path('taskboard/comments/<uuid:comment_id>/evidence/', TaskEvidenceView.as_view(), name='v1-taskboard-evidence'),
    path('taskboard/tasks/<uuid:task_id>/status/',   TaskStatusUpdateView.as_view(), name='v1-taskboard-status'),
    path('taskboard/tasks/<uuid:task_id>/feedback/', TaskFeedbackView.as_view(),     name='v1-taskboard-feedback'),
    path('taskboard/feedback/<uuid:feedback_id>/ack/', AcknowledgeFeedbackView.as_view(), name='v1-taskboard-feedback-ack'),
    # Employee expense-refund workflow (CFO 2026-07-13). Specific segments first.
    path('expense-claims/approvers/', ExpenseApproversView.as_view(),     name='v1-expense-approvers'),
    path('expense-claims/mine/',      MyExpenseClaimsView.as_view(),      name='v1-expense-mine'),
    path('expense-claims/queue/',     ExpenseApproverQueueView.as_view(), name='v1-expense-queue'),
    # Company credit-card spending (CFO 2026-08-07). Upload-only for the four
    # cardholders; Finance codes the GL account and loads the statement.
    path('company-cards/',            company_cards_api.my_cards,        name='v1-company-cards'),
    path('company-cards/spends/',     company_cards_api.card_spends,     name='v1-card-spends'),
    path('company-cards/my-open-items/',
         company_cards_api.my_open_items,                                name='v1-card-open-items'),
    path('company-cards/nudge/',      company_cards_api.nudge_holders,   name='v1-card-nudge'),
    path('company-cards/spends/<uuid:pk>/code/',
         company_cards_api.code_spend,                                   name='v1-card-spend-code'),
    path('company-cards/spends/<uuid:pk>/explain/',
         company_cards_api.explain_spend,                                name='v1-card-spend-explain'),
    path('company-cards/spends/<uuid:pk>/receipt/',
         company_cards_api.spend_receipt,                                name='v1-card-spend-receipt'),
    path('company-cards/statements/upload/',
         company_cards_api.upload_statement,                             name='v1-card-stmt-upload'),
    path('company-cards/statements/',
         company_cards_api.statements,                                   name='v1-card-statements'),
    path('company-cards/register/',   company_cards_api.card_register,   name='v1-card-register'),
    path('company-cards/statements/<uuid:pk>/gaps/',
         company_cards_api.statement_gaps,                               name='v1-card-stmt-gaps'),
    # Fix a statement filed against the wrong month, or loaded in error
    # (Laone Thebe 2026-09-11). Both are Finance-only and both write an audit row.
    path('company-cards/statements/<uuid:pk>/reallocate/',
         company_cards_api.reallocate_statement,                         name='v1-card-stmt-reallocate'),
    path('company-cards/statements/<uuid:pk>/delete/',
         company_cards_api.delete_statement,                             name='v1-card-stmt-delete'),
    path('company-cards/statement-lines/<uuid:pk>/waive/',
         company_cards_api.waive_line,                                   name='v1-card-line-waive'),
    path('expense-claims/cfo-queue/', ExpenseCfoQueueView.as_view(),      name='v1-expense-cfo-queue'),
    path('expense-claims/gl-accounts/', ExpenseGlAccountsView.as_view(),  name='v1-expense-gl-accounts'),
    path('expense-claims/suggest-gl/',  ExpenseSuggestGlView.as_view(),   name='v1-expense-suggest-gl'),
    path('expense-claims/',           ExpenseClaimSubmitView.as_view(),   name='v1-expense-submit'),
    path('expense-claims/<uuid:pk>/file/',    ExpenseFileView.as_view(),    name='v1-expense-file'),
    path('expense-claims/<uuid:pk>/process/', ExpenseProcessView.as_view(), name='v1-expense-process'),
    path('expense-claims/<uuid:pk>/reject/',  ExpenseRejectView.as_view(),  name='v1-expense-reject'),
    # Close out a refund that was actually PAID, instead of rejecting it with
    # the reason typed "PAID" (Bharath Balasubramanian 2026-08-17).
    path('expense-claims/<uuid:pk>/mark-paid/', ExpenseMarkPaidView.as_view(), name='v1-expense-mark-paid'),
    # MIS customer refunds (Graphite → Omni → FNB), CFO directive 2026-07-24.
    # Specific segments first, then <uuid:pk> actions.
    path('customer-refunds/inbound/', RefundInboundView.as_view(), name='v1-refund-inbound'),
    path('customer-refunds/queue/',   RefundQueueView.as_view(),   name='v1-refund-queue'),
    path('customer-refunds/<uuid:pk>/approve/',     RefundApproveView.as_view(),   name='v1-refund-approve'),
    path('customer-refunds/<uuid:pk>/reject/',      RefundRejectView.as_view(),    name='v1-refund-reject'),
    path('customer-refunds/<uuid:pk>/fraud-scan/',  RefundFraudScanView.as_view(),  name='v1-refund-fraud-scan'),
    path('customer-refunds/<uuid:pk>/ai-fraud-review/', RefundAiFraudView.as_view(), name='v1-refund-ai-fraud'),
    path('customer-refunds/<uuid:pk>/load-to-fnb/', RefundLoadToFnbView.as_view(), name='v1-refund-load-fnb'),
    path('customer-refunds/<uuid:pk>/mark-paid/',   RefundMarkPaidView.as_view(),  name='v1-refund-mark-paid'),
    path('customer-refunds/<uuid:pk>/',             RefundDetailView.as_view(),    name='v1-refund-detail'),
    path('expense-claims/<uuid:pk>/approve/', ExpenseApproveView.as_view(), name='v1-expense-approve'),
    # Report a System Bug (CFO directive 2026-06-10) — staff-facing bug channel
    # that emails excoboard@ so people stop emailing the CFO directly.
    path('bug-reports/',
         __import__('core.bug_report_views',
                    fromlist=['BugReportView']).BugReportView.as_view(),
         name='v1-bug-report'),
    # CFO-only: fire the safe triage runner on demand (button on /bug-reports).
    # Must precede the <uuid:pk> pattern so 'run-triage' isn't parsed as a pk.
    path('bug-reports/run-triage/',
         __import__('core.bug_report_views',
                    fromlist=['BugTriageRunView']).BugTriageRunView.as_view(),
         name='v1-bug-report-run-triage'),
    # Manus posts its QC finding back onto an item (CFO 2026-08-29). Before the
    # <uuid:pk>/ pattern so 'qc-result' is matched, not swallowed as a sub-path.
    path('bug-reports/<uuid:pk>/qc-result/',
         __import__('core.bug_report_views',
                    fromlist=['BugReportQCResultView']).BugReportQCResultView.as_view(),
         name='v1-bug-report-qc-result'),
    path('bug-reports/<uuid:pk>/',
         __import__('core.bug_report_views',
                    fromlist=['BugReportDetailView']).BugReportDetailView.as_view(),
         name='v1-bug-report-detail'),
    # Omni User Manual — live "What's New" feed, refreshed nightly by the
    # update_user_manual command (core/management/commands).
    path('manual/whats-new/',
         __import__('core.manual_views',
                    fromlist=['WhatsNewView']).WhatsNewView.as_view(),
         name='v1-manual-whats-new'),
    # Batch journal upload — must be before router.urls so it isn't
    # swallowed by the journal-entries/{pk}/ pattern.
    path('journal-entries/batch-upload/', BatchJournalUploadView.as_view(), name='v1-batch-upload'),
    # Draft JE triage (ADSA port — CFO directive 2026-05-18). Same
    # ordering caveat: must beat the {pk} retrieve pattern.
    path('journal-entries/triage/',       __import__('reporting.ma_trace_views',
                                              fromlist=['DraftTriageView']).DraftTriageView.as_view(),
         name='v1-je-triage'),
    path('payments/eft-export/',          EFTBatchExportView.as_view(),     name='v1-eft-export'),
    # Bulk bank-details upload (views built 2026-07-15 but never route-wired —
    # the /hris/bank-upload page 404'd; registered 2026-07-24). Specific paths
    # precede the <uuid:pk> catch-all.
    # IFRS 17 — the valuation cockpit, the disclosures and the MA bridge.
    # Read-only and gated to finance/management; nothing here writes to the ledger
    # or touches a frozen figure.
    path('ifrs17/valuation/',
         __import__('ifrs17.api_views', fromlist=['valuation']).valuation,
         name='v1-ifrs17-valuation'),
    path('ifrs17/disclosures/',
         __import__('ifrs17.api_views', fromlist=['disclosures']).disclosures,
         name='v1-ifrs17-disclosures'),
    path('ifrs17/statistics/',
         __import__('ifrs17.api_views', fromlist=['statistics']).statistics,
         name='v1-ifrs17-statistics'),
    path('ifrs17/bridge/',
         __import__('ifrs17.api_views', fromlist=['bridge']).bridge,
         name='v1-ifrs17-bridge'),
    path('ifrs17/levers/',
         __import__('ifrs17.api_views', fromlist=['levers']).levers,
         name='v1-ifrs17-levers'),
    path('ifrs17/exceptions/',
         __import__('ifrs17.api_views', fromlist=['exceptions']).exceptions,
         name='v1-ifrs17-exceptions'),
    path('ifrs17/exceptions/seed/',
         __import__('ifrs17.api_views', fromlist=['exceptions_seed']).exceptions_seed,
         name='v1-ifrs17-exceptions-seed'),
    path('ifrs17/exceptions/export/xlsx/',
         __import__('ifrs17.api_views', fromlist=['exceptions_export_xlsx']).exceptions_export_xlsx,
         name='v1-ifrs17-exceptions-export-xlsx'),
    path('ifrs17/exceptions/<uuid:pk>/answer/',
         __import__('ifrs17.api_views', fromlist=['exceptions_answer']).exceptions_answer,
         name='v1-ifrs17-exceptions-answer'),
    path('ifrs17/exceptions/<uuid:pk>/review/',
         __import__('ifrs17.api_views', fromlist=['exceptions_review']).exceptions_review,
         name='v1-ifrs17-exceptions-review'),
    path('ifrs17/export/xlsx/',
         __import__('ifrs17.api_views', fromlist=['export_xlsx']).export_xlsx,
         name='v1-ifrs17-export-xlsx'),
    path('ifrs17/export/docx/',
         __import__('ifrs17.api_views', fromlist=['export_docx']).export_docx,
         name='v1-ifrs17-export-docx'),
    path('ifrs17/data-request/',
         __import__('ifrs17.api_views', fromlist=['data_request']).data_request,
         name='v1-ifrs17-data-request'),
    path('payroll/bank-imports/upload/',            bank_import_upload,  name='v1-bank-import-upload'),
    path('payroll/bank-imports/',                   bank_import_list,    name='v1-bank-import-list'),
    path('payroll/bank-imports/<uuid:pk>/approve/', bank_import_approve, name='v1-bank-import-approve'),
    path('payroll/bank-imports/<uuid:pk>/reject/',  bank_import_reject,  name='v1-bank-import-reject'),
    path('payroll/bank-imports/<uuid:pk>/',         bank_import_detail,  name='v1-bank-import-detail'),
    # Payroll sign-off (CFO directive 2026-07-26, option A): payroll is
    # submitted per company x month and the CFO signs it. Specific paths first.
    path('payroll/sign-off/sign/',
         __import__('payroll.signoff_views',
                    fromlist=['signoff_sign']).signoff_sign,
         name='v1-payroll-signoff-sign'),
    path('payroll/sign-off/reject/',
         __import__('payroll.signoff_views',
                    fromlist=['signoff_reject']).signoff_reject,
         name='v1-payroll-signoff-reject'),
    path('payroll/sign-off/',
         __import__('payroll.signoff_views',
                    fromlist=['signoff_board']).signoff_board,
         name='v1-payroll-signoff-board'),
    # Payroll "Add Employee" with Finance sign-off (Pako Kago 2026-08-12).
    # Specific paths first so /payroll-* router patterns don't swallow them.
    path('payroll/additions/meta/',
         __import__('payroll.addition_views',
                    fromlist=['addition_meta']).addition_meta,
         name='v1-payroll-additions-meta'),
    path('payroll/additions/<uuid:pk>/approve/',
         __import__('payroll.addition_views',
                    fromlist=['addition_approve']).addition_approve,
         name='v1-payroll-addition-approve'),
    path('payroll/additions/<uuid:pk>/reject/',
         __import__('payroll.addition_views',
                    fromlist=['addition_reject']).addition_reject,
         name='v1-payroll-addition-reject'),
    path('payroll/additions/',
         __import__('payroll.addition_views',
                    fromlist=['addition_list_create']).addition_list_create,
         name='v1-payroll-additions'),
    # Staff file requests — must precede router.urls so /records/file-requests/
    # isn't caught by the records/<pk>/ retrieve (Tshepo Maswabi 2026-08-11).
    path('records/file-requests/',
         __import__('records.request_views',
                    fromlist=['request_list_create']).request_list_create,
         name='v1-record-file-requests'),
    path('records/file-requests/<uuid:pk>/approve/',
         __import__('records.request_views',
                    fromlist=['request_approve']).request_approve,
         name='v1-record-file-request-approve'),
    path('records/file-requests/<uuid:pk>/deny/',
         __import__('records.request_views',
                    fromlist=['request_deny']).request_deny,
         name='v1-record-file-request-deny'),
    # Salvage bulk import — must precede router.urls so /salvage-items/<pk>/
    # retrieve doesn't catch `import` (DRF pk regex is [^/.]+).
    path('salvage-items/import/',         __import__('salvage.import_view',
                                              fromlist=['SalvageItemImportView']).SalvageItemImportView.as_view(),
         name='v1-salvage-items-import'),

    # Payroll amendments (CFO directive 2026-05-21) — must precede router.urls
    # so /payroll-* paths don't capture the action segments.
    path('payroll/access/',
         __import__('payroll.amendment_views',
                    fromlist=['payroll_access']).payroll_access,
         name='v1-payroll-access'),
    path('payroll/amendments/upload/',
         __import__('payroll.amendment_views',
                    fromlist=['upload_amendments']).upload_amendments,
         name='v1-payroll-amendments-upload'),
    path('payroll/amendments/from-text/',
         __import__('payroll.amendment_views',
                    fromlist=['amendments_from_text']).amendments_from_text,
         name='v1-payroll-amendments-from-text'),
    path('payroll/export/',
         __import__('payroll.amendment_views',
                    fromlist=['export_payroll']).export_payroll,
         name='v1-payroll-export'),
    # Monthly payroll pack + 3-way control check (CFO 2026-08-24): totals vs
    # prior month, incentive-vs-module reconciliation, and salary-change vs
    # signed Authority to Recruit/Regrade. JSON for the page, xlsx to download.
    path('payroll/monthly-pack/',
         __import__('payroll.monthly_pack_views',
                    fromlist=['monthly_pack']).monthly_pack,
         name='v1-payroll-monthly-pack'),
    path('payroll/monthly-pack/export/',
         __import__('payroll.monthly_pack_views',
                    fromlist=['monthly_pack_export']).monthly_pack_export,
         name='v1-payroll-monthly-pack-export'),
    # Group Payroll Report — one report across every payroll company (CFO 19-Sep-2026).
    # Static paths before any catch-all; viewers = CFO, CEO, COO, Unami, Legakwa.
    path('payroll/group-report/regenerate/',
         __import__('payroll.group_report_views',
                    fromlist=['group_report_regenerate']).group_report_regenerate,
         name='v1-payroll-group-report-regenerate'),
    path('payroll/group-report/export/',
         __import__('payroll.group_report_views',
                    fromlist=['group_report_export']).group_report_export,
         name='v1-payroll-group-report-export'),
    path('payroll/group-report/',
         __import__('payroll.group_report_views',
                    fromlist=['group_report']).group_report,
         name='v1-payroll-group-report'),
    # Backlog release (CFO payroll.docx §5, 2026-09-16). The automatic feeds may
    # only write into the CURRENT month, so an earlier month's approved
    # incentives and commissions now wait here for a person: preview with
    # duplicate detection, then an explicit per-person release into this month.
    path('payroll/backlog/',
         __import__('payroll.backfill_views',
                    fromlist=['backlog_preview']).backlog_preview,
         name='v1-payroll-backlog'),
    path('payroll/backlog/release/',
         __import__('payroll.backfill_views',
                    fromlist=['backlog_release']).backlog_release,
         name='v1-payroll-backlog-release'),
    # Component-line backfill (CFO 2026-07-14): self-serve register upload +
    # coverage map so the payroll team fixes totals-only payslips for ANY run.
    path('payroll/register-upload/',
         __import__('payroll.amendment_views',
                    fromlist=['register_upload']).register_upload,
         name='v1-payroll-register-upload'),
    path('payroll/component-coverage/',
         __import__('payroll.amendment_views',
                    fromlist=['component_coverage']).component_coverage,
         name='v1-payroll-component-coverage'),
    # Bank-details bulk upload (CFO 2026-07-15): uploader files it, bank name is
    # derived from the branch code, Dorothy (HR Manager) approves before commit.
    path('payroll/bank-imports/upload/',
         __import__('payroll.bank_import_views',
                    fromlist=['bank_import_upload']).bank_import_upload,
         name='v1-payroll-bank-import-upload'),
    path('payroll/bank-imports/',
         __import__('payroll.bank_import_views',
                    fromlist=['bank_import_list']).bank_import_list,
         name='v1-payroll-bank-import-list'),
    path('payroll/bank-imports/<uuid:pk>/',
         __import__('payroll.bank_import_views',
                    fromlist=['bank_import_detail']).bank_import_detail,
         name='v1-payroll-bank-import-detail'),
    path('payroll/bank-imports/<uuid:pk>/approve/',
         __import__('payroll.bank_import_views',
                    fromlist=['bank_import_approve']).bank_import_approve,
         name='v1-payroll-bank-import-approve'),
    path('payroll/bank-imports/<uuid:pk>/reject/',
         __import__('payroll.bank_import_views',
                    fromlist=['bank_import_reject']).bank_import_reject,
         name='v1-payroll-bank-import-reject'),
    # Payroll Assistant (CFO 2026-07-14): NL Q&A over a PII-free completeness snapshot.
    path('payroll/assistant/',
         __import__('payroll.assistant',
                    fromlist=['payroll_assistant']).payroll_assistant,
         name='v1-payroll-assistant'),
    path('payroll/summary/',
         __import__('payroll.amendment_views',
                    fromlist=['payroll_summary']).payroll_summary,
         name='v1-payroll-summary'),
    path('payroll/amendment-batches/',
         __import__('payroll.amendment_views',
                    fromlist=['list_amendment_batches']).list_amendment_batches,
         name='v1-payroll-amendment-batches'),
    path('payroll/staff-loans/',
         __import__('payroll.api_views', fromlist=['staff_loans_list']).staff_loans_list,
         name='v1-payroll-staff-loans'),
    path('payroll/staff-loans/opening-balance/',
         __import__('payroll.api_views', fromlist=['staff_loans_opening_balance']).staff_loans_opening_balance,
         name='v1-payroll-staff-loans-opening'),
    path('payroll/amendment-batches/<uuid:batch_id>/preflight/',
         __import__('payroll.amendment_views',
                    fromlist=['preflight_amendment_batch']).preflight_amendment_batch,
         name='v1-payroll-amendments-preflight'),
    path('payroll/amendment-batches/<uuid:batch_id>/apply/',
         __import__('payroll.amendment_views',
                    fromlist=['apply_amendment_batch']).apply_amendment_batch,
         name='v1-payroll-amendments-apply'),
    path('payroll/periods/<uuid:period_id>/ai-review/',
         __import__('payroll.amendment_views',
                    fromlist=['ai_review_period']).ai_review_period,
         name='v1-payroll-ai-review'),
    # Pre-spend approval requests (CFO 2026-07-13) — events / training / travel /
    # travel-advance; DeepSeek reads the attached budget. Separate prefix, so no
    # clash with the BudgetViewSet routes.
    path('my-requests/',                       my_requests_view,      name='v1-my-requests'),
    path('my-requests/summary/',               my_requests_summary_view,
         name='v1-my-requests-summary'),
    path('my-approvals/',                      my_approvals,          name='v1-my-approvals'),
    path('mobile/home/',                       mobile_home,           name='v1-mobile-home'),
    # Phone Team tab (CFO 2026-09-03): my direct reports — in / on leave / dark /
    # overdue. Empty 200 for a user with no reports (it doubles as the probe).
    path('team/glance/',                       team_glance,           name='v1-team-glance'),
    path('my-approvals/items/',                my_approval_items,     name='v1-my-approval-items'),
    path('my-approvals/bulk-approve/',         bulk_approve,          name='v1-my-approvals-bulk'),
    path('my-approvals/decide/',               approvals_decide,      name='v1-my-approvals-decide'),
    path('my-approvals/history/',              my_approval_history,   name='v1-my-approvals-history'),
    path('my-approvals/brief/',                approval_brief,        name='v1-my-approvals-brief'),
    path('my-approvals/pack/',                 approval_pack,         name='v1-my-approvals-pack'),
    path('my-approvals/push/vapid-key/',       push_vapid_key,        name='v1-my-approvals-vapid'),
    path('my-approvals/push/subscribe/',       push_subscribe,        name='v1-my-approvals-push-sub'),
    # "Snap anything" (CFO 2026-09-03): one photo → what it is + which phone flow to open.
    path('snap/classify/',                     SnapClassifyView.as_view(), name='v1-snap-classify'),
    path('dpa-dashboard/',                      dpa_dashboard,         name='v1-dpa-dashboard'),
    path('dpa-dashboard/access/',              dpa_access,            name='v1-dpa-access'),
    path('security-dashboard/',                 security_dashboard,    name='v1-security-dashboard'),
    path('security-dashboard/access/',          security_access,       name='v1-security-access'),
    path('my-dpas/',                           my_processor_dpas,     name='v1-my-dpas'),
    path('dpo-workbook/',                       dpo_workbook,          name='v1-dpo-workbook'),
    path('dpo-workbook/upload/',                dpo_workbook_upload,   name='v1-dpo-workbook-upload'),
    path('ropa-auto/',                          ropa_auto,             name='v1-ropa-auto'),
    path('ropa-field-scan/',                    RopaFieldScanView.as_view(), name='v1-ropa-field-scan'),
    path('ropa/queue/',            RopaQueueView.as_view(),   name='v1-ropa-queue'),
    path('ropa/scan/',             RopaScanView.as_view(),    name='v1-ropa-scan'),
    path('ropa/entries/<uuid:pk>/',RopaEntryView.as_view(),   name='v1-ropa-entry'),
    path('ropa/vendors/',          VendorListView.as_view(),  name='v1-ropa-vendors'),
    path('ropa/vendors/<uuid:pk>/',VendorView.as_view(),      name='v1-ropa-vendor'),
    path('ropa/rulebook/',         RulebookView.as_view(),    name='v1-ropa-rulebook'),
    path('compliance-dashboard/',               compliance_dashboard,  name='v1-compliance-dashboard'),
    path('compliance-dashboard/access/',        compliance_access,     name='v1-compliance-access'),
    path('policy-library/',          PolicyLibraryView.as_view(),  name='v1-policy-library'),
    path('policy-library/citation/', LegalCitationView.as_view(),  name='v1-policy-library-citation'),
    # Machine-to-machine aggregate feed OUT to Alpha Brain (counts/totals only,
    # shared-token gated) — the mirror of core/compliance_brain.py's inbound pull.
    path('intel/summary/',                      IntelSummaryView.as_view(),
         name='v1-intel-summary'),
    # The staff-facing surface behind the Intelligence Summary page — normal
    # login, adds Alpha Brain's own figures next to ours.
    path('intel/staff-summary/',                IntelSummaryStaffView.as_view(),
         name='v1-intel-staff-summary'),
    path('adoption/',                           adoption_scoreboard,   name='v1-adoption'),
    # Screen-usage telemetry (CFO 2026-09-03): the SPA beacons each screen change;
    # the report is management-only, same gate as the scoreboard above.
    path('adoption/screen-view/',               screen_view_beacon,    name='v1-adoption-screen-view'),
    path('adoption/screens/',                   screen_view_report,    name='v1-adoption-screens'),
    path('dpa-checklist/',                      checklist,             name='v1-dpa-checklist'),
    path('dpa-checklist/save/',                 checklist_save,        name='v1-dpa-checklist-save'),
    path('dpa-checklist/<int:pk>/discipline/', checklist_discipline,  name='v1-dpa-checklist-discipline'),
    path('breaches/',                           breaches,              name='v1-breaches'),
    path('breaches/<int:pk>/',                  breach_update,         name='v1-breach-update'),
    path('dsrs/',                               dsrs,                  name='v1-dsrs'),
    path('dsrs/<int:pk>/',                      dsr_update,            name='v1-dsr-update'),
    path('privacy-notices/',                    privacy_notices_public, name='v1-privacy-notices'),
    # Nexus Staff Portal self-service (CFO 2026-07-14): own payslips + the
    # internal work directory. Strictly self/internal data — the payroll
    # register itself stays behind CanViewPayroll.
    path('payroll/my-payslips/',
         __import__('core.staff_mobile_views', fromlist=['my_payslips']).my_payslips,
         name='v1-my-payslips'),
    path('staff/phonebook/',
         __import__('core.staff_mobile_views', fromlist=['phonebook']).phonebook,
         name='v1-staff-phonebook'),
    path('payment-requests/',                  payment_requests,      name='v1-payment-requests'),
    path('payment-requests/parse/',            payment_request_parse, name='v1-payment-request-parse'),
    path('payment-requests/payee-bank/',       payee_bank_lookup,     name='v1-payee-bank-lookup'),
    path('payment-requests/payee-bank-check/', payee_bank_check,      name='v1-payee-bank-check'),
    # The POP Recipient dropdown for one payment line (Finance spec 2026-09-08).
    # A lookup over the vendor register / contact book / claim mirror — no AI.
    path('payment-requests/pop-recipients/',   pop_recipient_options, name='v1-pop-recipients'),
    # Read an uploaded invoice to pre-fill the form (CFO 2026-09-01). Reuses the
    # existing payments.invoice_read engine; saves nothing.
    path('payment-requests/read-invoice/',     payment_request_read_invoice, name='v1-payment-request-read-invoice'),
    # Drop Box (CFO handover 2026-09-02): drop invoice(s) or a ZIP -> one filled
    # DRAFT payment request each, to check + submit. Reuses the reader + supplier
    # memory; submit goes through the normal create endpoint. Omni moves no money.
    path('payment-requests/drop-box/',                drop_box,     name='v1-payment-request-drop-box'),
    path('payment-requests/drafts/',                  drafts,       name='v1-payment-request-drafts'),
    path('payment-requests/drafts/<uuid:pk>/submit/', submit_draft, name='v1-payment-request-draft-submit'),
    path('payment-requests/drafts/<uuid:pk>/',        draft_detail, name='v1-payment-request-draft-detail'),
    # Exception committee (CFO 2026-09-02): a fraud-risk exception (a changed
    # bank account) never blocks the raiser — three of six committee members
    # decide it; the CFO clears it for records only. Omni moves no money.
    path('payment-requests/exceptions/',                    payment_exceptions,        name='v1-payment-exceptions'),
    path('payment-requests/<uuid:pk>/exception-signoff/',   payment_exception_signoff, name='v1-payment-exception-signoff'),
    path('payment-requests/<uuid:pk>/exception-clear/',     payment_exception_clear,   name='v1-payment-exception-clear'),
    path('payment-requests/fnb-reconcile/',    payment_request_fnb_reconcile, name='v1-payment-request-fnb-reconcile'),
    path('payment-requests/email-catchup/',    payment_request_email_catchup, name='v1-payment-request-email-catchup'),
    path('payment-requests/reconcile-fnb-list/', payment_request_reconcile_fnb_list, name='v1-payment-request-reconcile-fnb-list'),
    # The CFO's one-look summary — same figures as the 09:30 email, from the
    # same function, so the screen and the email can never disagree.
    path('payment-requests/summary/',          payment_summary,       name='v1-payment-summary'),
    # Payment History (Finance spec 2026-09-08) — the per-line view of the same
    # line records the register and the duplicate sweep already read. Read-only.
    path('payment-requests/history/',          payment_history,       name='v1-payment-history'),
    path('payment-requests/load-override/',     payment_load_override, name='v1-payment-load-override'),
    path('payment-requests/load-override/<uuid:pk>/decide/', payment_load_override_decide, name='v1-payment-load-override-decide'),
    # /api/v1/reports/ 404'd while the scope map granted it (Manus 2026-08-09).
    path('reports/',                           reports_index,         name='v1-reports-index'),
    path('payment-requests/<uuid:req_id>/',    payment_request_detail, name='v1-payment-request-detail'),
    path('payment-requests/<uuid:req_id>/decide/', payment_request_decide, name='v1-payment-request-decide'),
    path('payment-requests/<uuid:req_id>/decide-lines/', payment_request_decide_lines, name='v1-payment-request-decide-lines'),
    path('payment-requests/<uuid:req_id>/clear/', payment_request_clear, name='v1-payment-request-clear'),
    # Copy / duplicate (B7, CFO Build Spec — Bokani Makosha): any request, any
    # status, may be a source. Opens a NEW request in DRAFT with a newly
    # generated reference; nothing is submitted. Reuses the Drop Box's draft
    # machinery, so submitting the copy goes through the normal create
    # endpoint and every control (PAY-DUP-01 included) fires again exactly as
    # it would on a freshly typed request.
    path('payment-requests/<uuid:req_id>/duplicate/', payment_request_duplicate, name='v1-payment-request-duplicate'),
    # Amend / cancel BEFORE finance sign-off (Kelvin Kimani spec 2026-09-08).
    # Nothing is ever hard-deleted; a cancel flags and keeps the record.
    path('payment-requests/<uuid:req_id>/amend/',  payment_request_amend,  name='v1-payment-request-amend'),
    # The committee's way to FIX a branch code PAY-BANK-05 flagged. Without it
    # the control could only ever report the fault (CFO 18-Sep-2026).
    path('payment-requests/<uuid:req_id>/correct-branch-code/',
         payment_request_correct_branch_code,
         name='v1-payment-request-correct-branch-code'),
    path('payment-requests/<uuid:req_id>/cancel/', payment_request_cancel, name='v1-payment-request-cancel'),
    path('payment-requests/<uuid:req_id>/mark-paid-bank/', payment_request_mark_paid_bank, name='v1-payment-request-mark-paid-bank'),
    # FNB tells Omni nothing when a human DECLINES an authorisation in the app,
    # so the batch sat on 'submitted' for ever and the queue kept saying
    # "Waiting for your authorisation in the FNB app" (Leano Makwapa 2026-09-11).
    path('payment-requests/<uuid:req_id>/mark-rejected-fnb/', payment_request_mark_rejected_fnb, name='v1-payment-request-mark-rejected-fnb'),
    # Read a list of payments out of a file. CREATES NOTHING - the rows are then
    # created through the ordinary gated endpoint, one call each, so every money
    # control applies to every row (Legakwa Ntabeni 2026-09-11).
    path('payment-requests/bulk-parse/', payment_request_bulk_parse, name='v1-payment-request-bulk-parse'),
    path('payment-requests/<uuid:req_id>/fnb-file/', payment_request_fnb_file, name='v1-payment-request-fnb-file'),
    path('payment-requests/<uuid:req_id>/attachments/', payment_request_attachments, name='v1-payment-request-attachments'),
    path('payment-request-attachments/<uuid:att_id>/file/', payment_request_attachment_file, name='v1-payment-request-attachment-file'),
    path('spend-requests/',                    spend_requests,        name='v1-spend-requests'),
    # literal segment before the <uuid> detail route
    path('spend-requests/levy-tracker/',       spend_levy_tracker,    name='v1-spend-levy-tracker'),
    path('spend-requests/<uuid:req_id>/',      spend_request_detail,  name='v1-spend-request-detail'),
    path('spend-requests/<uuid:req_id>/decide/', spend_request_decide, name='v1-spend-request-decide'),
    path('spend-requests/<uuid:req_id>/file/', spend_request_file,    name='v1-spend-request-file'),
    path('spend-requests/<uuid:req_id>/bqa-claim/',  spend_request_bqa_claim,  name='v1-spend-request-bqa-claim'),
    path('spend-requests/<uuid:req_id>/claim-pack/', spend_request_claim_pack, name='v1-spend-request-claim-pack'),
    path('spend-requests/<uuid:req_id>/actual/',     spend_request_actual,     name='v1-spend-request-actual'),
    # One-click spend-request approval from the notification email (CFO
    # 2026-07-14). Public (signed token = credential) — mirrors the leave
    # one-click pattern (hris/leave_actions.py). GET = side-effect-free
    # confirmation page; POST = the decision.
    path('spend-action/<str:token>/',          spend_action_page,     name='v1-spend-action-page'),
    path('spend-action/<str:token>/submit/',   spend_action_submit,   name='v1-spend-action-submit'),
    # Budget Library (CFO 2026-06-27) — must precede router.urls so /budgets/packs/
    # isn't captured by the BudgetViewSet detail route (/budgets/<pk>/).
    path('budgets/packs/',
         __import__('budgets.views', fromlist=['budget_packs']).budget_packs,
         name='v1-budget-packs'),
    path('budgets/packs/<uuid:pk>/files/',
         __import__('budgets.views', fromlist=['budget_pack_upload']).budget_pack_upload,
         name='v1-budget-pack-upload'),
    path('budgets/pack-files/<uuid:fid>/download/',
         __import__('budgets.views', fromlist=['budget_pack_file_download']).budget_pack_file_download,
         name='v1-budget-pack-file-download'),
    path('budgets/advisor/',
         __import__('budgets.views', fromlist=['budget_advisor']).budget_advisor,
         name='v1-budget-advisor'),
    # Strategic Plan Library (Finance request 2026-08-04) — the archive behind the
    # 5-Year Plan Cockpit. Same shape as the Budget Library routes above.
    # Report a problem from an email — NO sign-in (CFO 2026-08-07). Under /api/
    # so Caddy routes it to Django; a bare path would hit the Next frontend.
    path('report-problem/<str:token>/',
         __import__('core.bug_quick', fromlist=['report_problem']).report_problem,
         name='v1-report-problem'),
    path('plan-packs/',
         __import__('budgets.views', fromlist=['plan_packs']).plan_packs,
         name='v1-plan-packs'),
    path('plan-packs/<uuid:pk>/',
         __import__('budgets.views', fromlist=['plan_pack_detail']).plan_pack_detail,
         name='v1-plan-pack-detail'),
    path('plan-packs/<uuid:pk>/files/',
         __import__('budgets.views', fromlist=['plan_pack_upload']).plan_pack_upload,
         name='v1-plan-pack-upload'),
    path('plan-packs/files/<uuid:fid>/download/',
         __import__('budgets.views', fromlist=['plan_pack_file_download']).plan_pack_file_download,
         name='v1-plan-pack-file-download'),
    path('desktop-app/',
         __import__('core.desktop_download', fromlist=['desktop_app']).desktop_app,
         name='v1-desktop-app'),
] + router.urls + [
    # Invoice PDF
    path('invoices/<uuid:pk>/pdf/', InvoicePDFView.as_view(), name='invoice-pdf'),

    # Payslip PDF — CFO directive 2026-05-19 (Final Verification Audit § 5)
    path('payslips/<uuid:pk>/pdf/',
         __import__('payroll.pdf_view', fromlist=['PayslipPDFView']).PayslipPDFView.as_view(),
         name='payslip-pdf'),

    # Purchase Order PDF + reporting — CFO directive 2026-05-20 (Manus PO audit)
    path('purchase-orders/<uuid:pk>/pdf/',
         __import__('procurement.pdf_view', fromlist=['POPDFView']).POPDFView.as_view(),
         name='po-pdf'),
    # One-click "email PO to supplier" — sent from the logged-in user's own
    # mailbox with the PDF attached (CFO directive 2026-07-07).
    path('purchase-orders/<uuid:pk>/email/',
         __import__('procurement.pdf_view', fromlist=['POEmailView']).POEmailView.as_view(),
         name='po-email'),
    path('reports/po-outstanding/',
         __import__('procurement.report_views',
                    fromlist=['POOutstandingView']).POOutstandingView.as_view(),
         name='v1-po-outstanding'),
    path('reports/po-commitment/',
         __import__('procurement.report_views',
                    fromlist=['POCommitmentView']).POCommitmentView.as_view(),
         name='v1-po-commitment'),
    path('reports/gr-ir-reconciliation/',
         __import__('procurement.report_views',
                    fromlist=['GRIRReconciliationView']).GRIRReconciliationView.as_view(),
         name='v1-gr-ir-recon'),
    # Claims-PO analytics dashboard (CFO 2026-07-07) — read-only aggregation
    # over department=claims POs + ClaimsAssessment turnaround.
    path('reports/claims-po-analytics/',
         __import__('procurement.claims_analytics_view',
                    fromlist=['ClaimsPOAnalyticsView']).ClaimsPOAnalyticsView.as_view(),
         name='v1-claims-po-analytics'),
    # Aria's pre-send checks (CFO feature #4, 2026-07-08) — deterministic,
    # local sanity checks on a claims assessment's two POs before they go out.
    path('claims-po/<uuid:pk>/review-check/',
         __import__('procurement.claims_review_check',
                    fromlist=['ClaimsReviewCheckView']).ClaimsReviewCheckView.as_view(),
         name='v1-claims-review-check'),

    # Equity / Cap Table — "Capital Story" (CFO/Legakwa 2026-06-19, OCF-aligned)
    path('reports/equity/',
         __import__('reporting.equity_views', fromlist=['equity_capital_story']).equity_capital_story,
         name='v1-equity-capital-story'),
    # Self-service: a holder's own equity + one-page statement (any logged-in user)
    path('equity/my-equity/',
         __import__('equity.api_views', fromlist=['my_equity']).my_equity,
         name='v1-my-equity'),
    path('equity/my-equity/statement.pdf',
         __import__('equity.api_views', fromlist=['my_equity_statement_pdf']).my_equity_statement_pdf,
         name='v1-my-equity-pdf'),
    # Vesting schedule helper (Finance): preview a standard schedule, then apply
    path('equity/grants/<int:pk>/generate-schedule/',
         __import__('equity.api_views', fromlist=['grant_generate_schedule']).grant_generate_schedule,
         name='v1-equity-generate-schedule'),
    path('equity/grants/<int:pk>/apply-schedule/',
         __import__('equity.api_views', fromlist=['grant_apply_schedule']).grant_apply_schedule,
         name='v1-equity-apply-schedule'),

    # Manus PO Audit Phase-B routes — CFO 2026-05-20
    path('approval-delegations/',
         __import__('procurement.phase_b_views',
                    fromlist=['approval_delegations']).approval_delegations,
         name='v1-approval-delegations'),
    path('approval-delegations/<uuid:pk>/',
         __import__('procurement.phase_b_views',
                    fromlist=['revoke_delegation']).revoke_delegation,
         name='v1-approval-delegation-revoke'),
    path('purchase-orders/<uuid:pk>/amend/',
         __import__('procurement.phase_b_views',
                    fromlist=['amend_po']).amend_po,
         name='v1-po-amend'),
    path('purchase-orders/<uuid:pk>/amend/<uuid:aid>/approve/',
         __import__('procurement.phase_b_views',
                    fromlist=['approve_amendment']).approve_amendment,
         name='v1-po-amend-approve'),
    # PO Attachments — list/create on /purchase-orders/<pk>/attachments/,
    # destroy on /purchase-orders/<pk>/attachments/<aid>/.
    path('purchase-orders/<uuid:po_pk>/attachments/',
         __import__('procurement.phase_b_views',
                    fromlist=['POAttachmentViewSet']).POAttachmentViewSet.as_view(
                        {'get': 'list', 'post': 'create'}),
         name='v1-po-attachments'),
    path('purchase-orders/<uuid:po_pk>/attachments/<uuid:pk>/',
         __import__('procurement.phase_b_views',
                    fromlist=['POAttachmentViewSet']).POAttachmentViewSet.as_view(
                        {'delete': 'destroy'}),
         name='v1-po-attachment-detail'),
    # Authenticated open — the phone's PO pack cannot use the raw /media/ url
    # (prod never serves it). Same gate as GET /purchase-orders/<pk>/.
    path('purchase-orders/<uuid:po_pk>/attachments/<uuid:pk>/file/',
         __import__('procurement.phase_b_views',
                    fromlist=['POAttachmentViewSet']).POAttachmentViewSet.as_view(
                        {'get': 'file'}),
         name='v1-po-attachment-file'),

    # Permanent API keys for headless agents (Manus).
    # CFO directive 2026-05-19. Superuser-only management.
    path('admin/api-keys/',
         __import__('core.api_key_views',
                    fromlist=['api_keys_collection']).api_keys_collection,
         name='v1-admin-api-keys'),
    path('admin/api-keys/<uuid:pk>/',
         __import__('core.api_key_views',
                    fromlist=['api_keys_detail']).api_keys_detail,
         name='v1-admin-api-keys-detail'),

    # CFO-only Secrets Vault — encrypted credential store (HRIS / portal /
    # integration passwords). CFO directive 2026-06. Reveal is audit-logged.
    path('admin/vault/',
         __import__('core.vault_views',
                    fromlist=['vault_collection']).vault_collection,
         name='v1-admin-vault'),
    path('admin/vault/<uuid:pk>/',
         __import__('core.vault_views',
                    fromlist=['vault_detail']).vault_detail,
         name='v1-admin-vault-detail'),
    path('admin/vault/<uuid:pk>/reveal/',
         __import__('core.vault_views',
                    fromlist=['vault_reveal']).vault_reveal,
         name='v1-admin-vault-reveal'),
    # QC agent's read of a qc/-namespaced secret (scoped 'qc-bot' key) — CFO 2026-09-07.
    path('admin/vault/qc/<str:name>/',
         __import__('core.vault_views',
                    fromlist=['vault_qc_get']).vault_qc_get,
         name='v1-admin-vault-qc-get'),

    # WhatsApp reminder console — CFO-only staff phonebook + send (CFO 2026-07-14).
    # API key lives in the Secrets Vault (WHATSAPP_TOKEN / WHATSAPP_PHONE_ID).
    path('whatsapp/config/',
         __import__('taskboard.whatsapp_views', fromlist=['whatsapp_config']).whatsapp_config,
         name='v1-whatsapp-config'),
    path('whatsapp/contacts/',
         __import__('taskboard.whatsapp_views', fromlist=['whatsapp_contacts']).whatsapp_contacts,
         name='v1-whatsapp-contacts'),
    path('whatsapp/contacts/<uuid:pk>/',
         __import__('taskboard.whatsapp_views', fromlist=['whatsapp_contact_detail']).whatsapp_contact_detail,
         name='v1-whatsapp-contact-detail'),
    path('whatsapp/overdue/',
         __import__('taskboard.whatsapp_views', fromlist=['whatsapp_overdue']).whatsapp_overdue,
         name='v1-whatsapp-overdue'),
    path('whatsapp/templates/',
         __import__('taskboard.whatsapp_views', fromlist=['whatsapp_templates']).whatsapp_templates,
         name='v1-whatsapp-templates'),
    path('whatsapp/templates/submit/',
         __import__('taskboard.whatsapp_views', fromlist=['whatsapp_submit_templates']).whatsapp_submit_templates,
         name='v1-whatsapp-templates-submit'),
    path('whatsapp/register/',
         __import__('taskboard.whatsapp_views', fromlist=['whatsapp_register']).whatsapp_register,
         name='v1-whatsapp-register'),
    path('whatsapp/send/',
         __import__('taskboard.whatsapp_views', fromlist=['whatsapp_send']).whatsapp_send,
         name='v1-whatsapp-send'),
    path('whatsapp/log/',
         __import__('taskboard.whatsapp_views', fromlist=['whatsapp_log']).whatsapp_log,
         name='v1-whatsapp-log'),

    # Admin IP allowlist — self-service "lock /admin to my IP" endpoint.
    # CFO directive 2026-05-19 (Final Verification Audit § 4).
    path('admin/lock-to-my-ip/',
         __import__('core.admin_ip_views', fromlist=['lock_to_my_ip']).lock_to_my_ip,
         name='v1-admin-lock-to-my-ip'),
    path('admin/allowlist/',
         __import__('core.admin_ip_views', fromlist=['list_allowlist']).list_allowlist,
         name='v1-admin-allowlist-list'),
    path('admin/allowlist/clear/',
         __import__('core.admin_ip_views', fromlist=['clear_allowlist']).clear_allowlist,
         name='v1-admin-allowlist-clear'),

    # FNB integration — status + connection test + webhook receiver + submit
    path('fnb/proofs/',
         __import__('fnb.pop_api', fromlist=['proofs']).proofs,
         name='v1-fnb-proofs'),
    path('fnb/proofs/<uuid:pk>/confirm/',
         __import__('fnb.pop_api', fromlist=['proof_confirm']).proof_confirm,
         name='v1-fnb-proof-confirm'),
    path('fnb/proofs/<uuid:pk>/pdf/',
         __import__('fnb.pop_api', fromlist=['proof_pdf']).proof_pdf,
         name='v1-fnb-proof-pdf'),
    path('fnb/status/',           FNBStatusView.as_view(),         name='v1-fnb-status'),
    path('fnb/health-summary/',   FNBHealthSummaryView.as_view(),  name='v1-fnb-health-summary'),
    path('fnb/health-ask/',       FNBHealthAskView.as_view(),      name='v1-fnb-health-ask'),
    path('fnb/credentials/',      FNBCredentialView.as_view(),     name='v1-fnb-credentials'),
    path('fnb/test-connection/',  FNBTestConnectionView.as_view(), name='v1-fnb-test-connection'),
    path('fnb/webhook/',          FNBWebhookReceiverView.as_view(),name='v1-fnb-webhook'),
    path('fnb/submit-batch/',     FNBSubmitBatchView.as_view(),    name='v1-fnb-submit-batch'),
    path('fnb/bank-view-preview/', FNBBankViewPreview.as_view(),  name='v1-fnb-bank-view-preview'),
    path('fnb/batches/<uuid:batch_id>/refresh/',
                                   FNBRefreshBatchView.as_view(),   name='v1-fnb-refresh-batch'),
    path('fnb/pull-statements/',
         __import__('fnb.api_views', fromlist=['FNBPullStatementsView'])
            .FNBPullStatementsView.as_view(),
         name='v1-fnb-pull-statements'),
    path('fnb/quick-transfer/',
         __import__('fnb.api_views', fromlist=['FNBQuickTransferView'])
            .FNBQuickTransferView.as_view(),
         name='v1-fnb-quick-transfer'),
    # Express Pay (CFO 2026-09-04) — CFO/CEO load ONE payment to FNB from the
    # phone with a single authorisation. Stages only; the money leaves when the
    # CFO releases it in the FNB app (phone 2FA). fnb/express_pay.py.
    path('fnb/express-pay/',
         __import__('fnb.express_pay', fromlist=['ExpressPayView'])
            .ExpressPayView.as_view(),
         name='v1-fnb-express-pay'),
    path('fnb/express-pay/can/',
         __import__('fnb.express_pay', fromlist=['ExpressPayCanView'])
            .ExpressPayCanView.as_view(),
         name='v1-fnb-express-pay-can'),
    path('fnb/express-pay/history/',
         __import__('fnb.express_pay', fromlist=['PaymentHistoryView'])
            .PaymentHistoryView.as_view(),
         name='v1-fnb-express-pay-history'),
    path('fnb/express-pay/payees/',
         __import__('fnb.express_pay', fromlist=['ExpressPayeeListView'])
            .ExpressPayeeListView.as_view(),
         name='v1-fnb-express-pay-payees'),
    path('fnb/express-pay/payees/<uuid:pk>/',
         __import__('fnb.express_pay', fromlist=['ExpressPayeeDetailView'])
            .ExpressPayeeDetailView.as_view(),
         name='v1-fnb-express-pay-payee-detail'),
    path('fnb/express-pay/<uuid:batch_id>/status/',
         __import__('fnb.express_pay', fromlist=['ExpressPayStatusView'])
            .ExpressPayStatusView.as_view(),
         name='v1-fnb-express-pay-status'),
    path('fnb/recent-payees/',
         __import__('fnb.express_pay', fromlist=['RecentPayeesView'])
            .RecentPayeesView.as_view(),
         name='v1-fnb-recent-payees'),
    path('fnb/claim-payee/',
         __import__('fnb.express_pay', fromlist=['ClaimPayeeView'])
            .ClaimPayeeView.as_view(),
         name='v1-fnb-claim-payee'),
    path('banking/bank-accounts/<uuid:ba_id>/set-account-number/',
         __import__('fnb.api_views', fromlist=['BankAccountSetNumberView'])
            .BankAccountSetNumberView.as_view(),
         name='v1-bank-account-set-number'),
    path('banking/bank-accounts/<uuid:ba_id>/toggle-hide/',
         __import__('fnb.api_views', fromlist=['BankAccountToggleHideView'])
            .BankAccountToggleHideView.as_view(),
         name='v1-bank-account-toggle-hide'),

    # Morning Bank Balances (CFO 2026-09-20) — read-only, GET only, for ever.
    path('banking/balances/',
         __import__('banking.balance_views', fromlist=['BankBalancesView'])
            .BankBalancesView.as_view(),
         name='v1-banking-balances'),

    # Health Care quick-quote — CFO directive 2026-05-26 (sheet 13)
    path('health/quick-quote/',
         __import__('healthcare.api_views',
                    fromlist=['HealthQuickQuoteView'])
            .HealthQuickQuoteView.as_view(),
         name='v1-health-quick-quote'),
    path('health/quick-quote/plans/',
         __import__('healthcare.api_views',
                    fromlist=['HealthQuickQuotePlansView'])
            .HealthQuickQuotePlansView.as_view(),
         name='v1-health-quick-quote-plans'),

    # Group Health Quotations — persisted quotes + invoice (CFO/Tlamelo 2026-06-17)
    path('health/quotes/',
         __import__('healthcare.quote_views', fromlist=['quotes']).quotes,
         name='v1-health-quotes'),
    path('health/quotes/rate-card/',
         __import__('healthcare.quote_views', fromlist=['rate_card']).rate_card,
         name='v1-health-quotes-rate-card'),
    path('health/quotes/price/',
         __import__('healthcare.quote_views', fromlist=['quote_price']).quote_price,
         name='v1-health-quote-price'),
    path('health/quotes/parse-members/',
         __import__('healthcare.quote_views', fromlist=['quote_members_parse']).quote_members_parse,
         name='v1-health-quotes-parse-members'),
    # --- ADH to AFA member load file (healthcare) ---------------------
    path('health/afa/runs/',
         __import__('healthcare.afa_views', fromlist=['afa_runs']).afa_runs,
         name='v1-health-afa-runs'),
    path('health/afa/runs/build/',
         __import__('healthcare.afa_views', fromlist=['afa_build']).afa_build,
         name='v1-health-afa-build'),
    path('health/afa/runs/<uuid:run_id>/',
         __import__('healthcare.afa_views', fromlist=['afa_run_detail']).afa_run_detail,
         name='v1-health-afa-run-detail'),
    path('health/afa/runs/<uuid:run_id>/release/',
         __import__('healthcare.afa_views', fromlist=['afa_release']).afa_release,
         name='v1-health-afa-release'),
    # --- ADH claims EFT settlement loader (B4) ------------------------
    path('health/adh-settlements/runs/',
         __import__('healthcare.claims_settlement_views',
                    fromlist=['adh_settlement_runs']).adh_settlement_runs,
         name='v1-health-adh-settlement-runs'),
    path('health/adh-settlements/access/',
         __import__('healthcare.claims_settlement_views',
                    fromlist=['adh_settlement_access']).adh_settlement_access,
         name='v1-health-adh-settlement-access'),
    path('health/afa/groups/',
         __import__('healthcare.afa_views', fromlist=['afa_groups']).afa_groups,
         name='v1-health-afa-groups'),
    path('health/quotes/dashboard/',
         __import__('healthcare.quote_views', fromlist=['quote_dashboard']).quote_dashboard,
         name='v1-health-quotes-dashboard'),
    path('health/quotes/<uuid:pk>/',
         __import__('healthcare.quote_views', fromlist=['quote_detail']).quote_detail,
         name='v1-health-quote-detail'),
    path('health/quotes/<uuid:pk>/submit/',
         __import__('healthcare.quote_views', fromlist=['quote_submit']).quote_submit,
         name='v1-health-quote-submit'),
    path('health/quotes/<uuid:pk>/review/',
         __import__('healthcare.quote_views', fromlist=['quote_review']).quote_review,
         name='v1-health-quote-review'),
    path('health/quotes/<uuid:pk>/reject/',
         __import__('healthcare.quote_views', fromlist=['quote_reject']).quote_reject,
         name='v1-health-quote-reject'),
    path('health/quotes/<uuid:pk>/approve/',
         __import__('healthcare.quote_views', fromlist=['quote_approve']).quote_approve,
         name='v1-health-quote-approve'),
    path('health/quotes/<uuid:pk>/invoice/',
         __import__('healthcare.quote_views', fromlist=['quote_invoice']).quote_invoice,
         name='v1-health-quote-invoice'),
    path('health/quotes/<uuid:pk>/email/',
         __import__('healthcare.quote_views', fromlist=['quote_email']).quote_email,
         name='v1-health-quote-email'),
    path('health/quotes/<uuid:pk>/pdf/',
         __import__('healthcare.quote_views', fromlist=['quote_pdf']).quote_pdf,
         name='v1-health-quote-pdf'),
    path('health/quotes/<uuid:pk>/xlsx/',
         __import__('healthcare.quote_views', fromlist=['quote_xlsx']).quote_xlsx,
         name='v1-health-quote-xlsx'),
    path('health/quotes/<uuid:pk>/consolidated-xlsx/',
         __import__('healthcare.quote_views', fromlist=['quote_consolidated_xlsx']).quote_consolidated_xlsx,
         name='v1-health-quote-consolidated-xlsx'),

    # Healthcare vendor onboarding — Ankete-only (CFO/EXCO directive 2026-06-04)
    path('health/vendor-onboarding/extract/',
         __import__('healthcare.vendor_views',
                    fromlist=['VendorFieldExtractView'])
            .VendorFieldExtractView.as_view(),
         name='v1-health-vendor-onboarding-extract'),
    path('health/vendor-onboarding/submit/',
         __import__('healthcare.vendor_views',
                    fromlist=['VendorOnboardingSubmitView'])
            .VendorOnboardingSubmitView.as_view(),
         name='v1-health-vendor-onboarding-submit'),
    path('health/vendor-onboarding/access/',
         __import__('healthcare.vendor_views',
                    fromlist=['VendorOnboardingAccessView'])
            .VendorOnboardingAccessView.as_view(),
         name='v1-health-vendor-onboarding-access'),
    # Staff-assisted intake + remote e-sign (CFO 2026-06-05)
    path('health/vendor-onboarding/draft/',
         __import__('healthcare.vendor_esign_views', fromlist=['VendorDraftSaveView'])
            .VendorDraftSaveView.as_view(), name='v1-health-vendor-draft'),
    path('health/vendor-onboarding/send-signature/',
         __import__('healthcare.vendor_esign_views', fromlist=['VendorSendSignatureView'])
            .VendorSendSignatureView.as_view(), name='v1-health-vendor-send-signature'),
    # PUBLIC (no login) — token-gated remote sign
    path('health/vendor-sign/<str:token>/',
         __import__('healthcare.vendor_esign_views', fromlist=['VendorSignDetailView'])
            .VendorSignDetailView.as_view(), name='v1-health-vendor-sign-detail'),
    path('health/vendor-sign/<str:token>/submit/',
         __import__('healthcare.vendor_esign_views', fromlist=['VendorSignSubmitView'])
            .VendorSignSubmitView.as_view(), name='v1-health-vendor-sign-submit'),

    # Self-service onboarding by invite (CFO 2026-07-28): staff mint a private
    # link; the provider fills in the WHOLE form themselves with no login.
    path('health/vendor-onboarding/invite/',
         __import__('healthcare.vendor_invite_views', fromlist=['VendorInviteCreateView'])
            .VendorInviteCreateView.as_view(), name='v1-health-vendor-invite-create'),
    path('health/vendor-invite/<str:token>/',
         __import__('healthcare.vendor_invite_views', fromlist=['VendorInviteDetailView'])
            .VendorInviteDetailView.as_view(), name='v1-health-vendor-invite-detail'),
    path('health/vendor-invite/<str:token>/submit/',
         __import__('healthcare.vendor_invite_views', fromlist=['VendorInviteSubmitView'])
            .VendorInviteSubmitView.as_view(), name='v1-health-vendor-invite-submit'),

    # Speaker audience feedback (CFO 2026-08-03) — a public, no-login form the
    # event organiser circulates, and a CFO-ONLY page to read the answers. The
    # two form endpoints are AllowAny with their own anon throttle; the reader
    # endpoints are gated by core.speaker_feedback_views.user_can_read_feedback
    # (positive match, NO superuser/admin bypass — "confidential, my eyes").
    path('speaker-feedback/form/<slug:slug>/',
         __import__('core.speaker_feedback_views', fromlist=['SpeakerFeedbackFormView'])
            .SpeakerFeedbackFormView.as_view(), name='v1-speaker-feedback-form'),
    path('speaker-feedback/form/<slug:slug>/submit/',
         __import__('core.speaker_feedback_views', fromlist=['SpeakerFeedbackSubmitView'])
            .SpeakerFeedbackSubmitView.as_view(), name='v1-speaker-feedback-submit'),
    path('speaker-feedback/access/',
         __import__('core.speaker_feedback_views', fromlist=['speaker_feedback_access'])
            .speaker_feedback_access, name='v1-speaker-feedback-access'),
    path('speaker-feedback/responses/<slug:slug>/',
         __import__('core.speaker_feedback_views', fromlist=['SpeakerFeedbackResponsesView'])
            .SpeakerFeedbackResponsesView.as_view(), name='v1-speaker-feedback-responses'),

    # ADH service-provider registry (readiness tracker) — Meduduetso 2026-09-01.
    path('health/service-providers/',
         __import__('healthcare.provider_registry_views', fromlist=['ServiceProviderListView'])
            .ServiceProviderListView.as_view(), name='v1-health-service-providers'),
    path('health/service-providers/import/preview/',
         __import__('healthcare.provider_registry_views', fromlist=['ServiceProviderImportPreviewView'])
            .ServiceProviderImportPreviewView.as_view(), name='v1-health-service-providers-import-preview'),
    path('health/service-providers/import/commit/',
         __import__('healthcare.provider_registry_views', fromlist=['ServiceProviderImportCommitView'])
            .ServiceProviderImportCommitView.as_view(), name='v1-health-service-providers-import-commit'),
    path('health/service-providers/export/',
         __import__('healthcare.provider_registry_views', fromlist=['ServiceProviderExportView'])
            .ServiceProviderExportView.as_view(), name='v1-health-service-providers-export'),
    # PUBLIC (no login): a new provider applies to join the ADH network.
    path('health/provider-apply/',
         __import__('healthcare.provider_registry_views', fromlist=['ProviderApplyView'])
            .ProviderApplyView.as_view(), name='v1-health-provider-apply'),
    # Evening exec-dashboard ON/OFF switch (ADH team controls it).
    path('health/service-providers/dashboard-switch/',
         __import__('healthcare.provider_registry_views', fromlist=['ProviderDashboardToggleView'])
            .ProviderDashboardToggleView.as_view(), name='v1-health-provider-dashboard-switch'),
    # ADH staff review of applications.
    path('health/service-providers/applications/',
         __import__('healthcare.provider_registry_views', fromlist=['ProviderApplicationListView'])
            .ProviderApplicationListView.as_view(), name='v1-health-provider-applications'),
    path('health/service-providers/applications/<uuid:pk>/',
         __import__('healthcare.provider_registry_views', fromlist=['ProviderApplicationDetailView'])
            .ProviderApplicationDetailView.as_view(), name='v1-health-provider-application-detail'),
    path('health/service-providers/trend/',
         __import__('healthcare.provider_registry_views', fromlist=['ProviderTrendView'])
            .ProviderTrendView.as_view(), name='v1-health-provider-trend'),
    path('health/service-providers/<uuid:pk>/',
         __import__('healthcare.provider_registry_views', fromlist=['ServiceProviderDetailView'])
            .ServiceProviderDetailView.as_view(), name='v1-health-service-provider-detail'),

    # Healthcare bordereaux / claims / treaty uploads (CFO + Tlamelo 2026-06-05).
    path('health/upload/',
         __import__('healthcare.upload_views', fromlist=['HealthcareUploadView'])
            .HealthcareUploadView.as_view(), name='v1-health-upload'),
    path('health/uploads/',
         __import__('healthcare.upload_views', fromlist=['HealthcareUploadListView'])
            .HealthcareUploadListView.as_view(), name='v1-health-uploads-list'),
    path('health/uploads/<uuid:pk>/',
         __import__('healthcare.upload_views', fromlist=['HealthcareUploadDetailView'])
            .HealthcareUploadDetailView.as_view(), name='v1-health-upload-detail'),
    path('health/summary/',
         __import__('healthcare.upload_views', fromlist=['HealthcareSummaryView'])
            .HealthcareSummaryView.as_view(), name='v1-health-summary'),
    path('health/dashboard/',
         __import__('healthcare.dashboard_views', fromlist=['health_dashboard'])
            .health_dashboard, name='v1-health-dashboard'),

    # Calendar invites — Omni writes only into the caller's own calendar.
    # The Instant Insurance book, live (Build Brief 1, Stage D).
    path('integrations/instant-insurance-summary/',
         __import__('integrations.instant_insurance_views',
                    fromlist=['instant_insurance_summary'])
            .instant_insurance_summary, name='v1-instant-insurance-summary'),

    # RealPay collections vs the payment ledger — the check that was missing when
    # ~22,500 receipts went unrecorded in Jul-Aug 2026.
    path('integrations/realpay-ledger-reconciliation/',
         __import__('integrations.realpay_reconcile_views',
                    fromlist=['realpay_ledger_reconciliation'])
            .realpay_ledger_reconciliation, name='v1-realpay-ledger-reconciliation'),

    path('calendar/status/',
         __import__('core.calendar_views', fromlist=['calendar_status'])
            .calendar_status, name='v1-calendar-status'),
    path('calendar/invites/',
         __import__('core.calendar_views', fromlist=['create_invite'])
            .create_invite, name='v1-calendar-invite-create'),

    # Reports under /api/v1/reports/
    path('reports/trial-balance/',  TrialBalanceView.as_view(),  name='v1-trial-balance'),
    path('reports/profit-loss/',    ProfitLossView.as_view(),    name='v1-profit-loss'),
    path('reports/ma-profit-loss/', MAProfitLossView.as_view(),  name='v1-ma-profit-loss'),
    path('reports/entity-pl/',      EntityProfitLossView.as_view(), name='v1-entity-pl'),
    path('reports/balance-sheet/',  BalanceSheetView.as_view(),  name='v1-balance-sheet'),
    # CF-404-01 (CFO 2026-08-15, Manus QC R2): cash flow was mounted under
    # /api/reports/ (see reporting/urls.py) but not under /api/v1/reports/,
    # so the frontend's /reports/cash-flow page raised a raw HTTP 404.
    # Every other report already lives here — this is the missing line.
    path('reports/cash-flow/',      CashFlowView.as_view(),      name='v1-cash-flow'),
    # Same omission as CF-404-01 above, caught on 2026-09-10 by running the
    # page in a real browser: the market benchmark was mounted only under
    # /api/reports/, so the frontend — which prefixes /api/v1 — got a 404 and
    # the screen showed "Could not load the benchmark" forever.
    path('reports/peer-benchmark/', PeerBenchmarkView.as_view(), name='v1-peer-benchmark'),
    path('reports/ar-aging/',       ARAgingView.as_view(),       name='v1-ar-aging'),
    # Graphite premium-debtors feeds were mounted only under /api/reports/
    # (reporting/urls.py) but not under /api/v1/reports/, so the frontend's
    # /reports/age-analysis page raised a raw HTTP 404 — same class as the
    # cash-flow miss above. These are the missing versioned lines (CFO 2026-08-30).
    path('reports/graphite-age-analysis/', GraphiteAgeAnalysisView.as_view(), name='v1-graphite-age-analysis'),
    path('reports/renewals/', RenewalReportView.as_view(), name='v1-renewals'),
    path('reports/graphite-payments/',     GraphitePaymentsView.as_view(),    name='v1-graphite-payments'),
    path('reports/ap-aging/',       APAgingView.as_view(),       name='v1-ap-aging'),
    # AP age-analysis upload (CFO 2026-07-13) — Finance uploads the maintained
    # schedule (e.g. as at 30 June) as an as-at snapshot the AP Aging page shows.
    path('reports/ap-aging/upload/',   ap_aging_upload,   name='v1-ap-aging-upload'),
    path('reports/ap-aging/snapshot/', ap_aging_snapshot, name='v1-ap-aging-snapshot'),
    path('reports/cash-position/',  CashPositionView.as_view(),  name='v1-cash-position'),
    path('compliance/prudential-limits/', PrudentialLimitsView.as_view(),
         name='v1-prudential-limits'),
    path('reports/receivables-summary/', ReceivablesSummaryView.as_view(), name='v1-receivables-summary'),
    path('reports/coa-ma-tree/',         CoaMaTreeView.as_view(),         name='v1-coa-ma-tree'),
    path('reports/general-ledger/',      GeneralLedgerView.as_view(),    name='v1-general-ledger'),
    path('reports/general-ledger/extract-all/',
         GeneralLedgerExtractAllView.as_view(),
         name='v1-general-ledger-extract-all'),
    # CFO directive 2026-05-20 — Excel export for every report
    path('reports/export-xlsx/',
         __import__('reporting.views', fromlist=['ReportXlsxExportView']).ReportXlsxExportView.as_view(),
         name='v1-report-export-xlsx'),

    # CFO directive 2026-05-21 — pending-approval notification feed
    path('notifications/pending/',
         __import__('core.notifications_view',
                    fromlist=['PendingNotificationsView']).PendingNotificationsView.as_view(),
         name='v1-notifications-pending'),
    path('reports/budget-vs-actual/',    BudgetVsActualView.as_view(),   name='v1-budget-vs-actual'),
    path('reports/vat-return/',          VATReturnView.as_view(),        name='v1-vat-return'),
    path('reports/expense-analysis/',    ExpenseAnalysisView.as_view(),  name='v1-expense-analysis'),
    path('reports/expense-analysis/detail/',     ExpenseAnalysisDetailView.as_view(), name='v1-expense-analysis-detail'),
    path('reports/expense-analysis/export-xlsx/', ExpenseAnalysisXlsxView.as_view(),  name='v1-expense-analysis-xlsx'),
    path('reports/expense-analysis/analyse/',    ExpenseAnalysisAIView.as_view(),     name='v1-expense-analysis-ai'),
    path('reports/management-pack/',     ManagementPackView.as_view(),   name='v1-management-pack'),
    path('reports/audit-pack/',          AuditPackView.as_view(),        name='v1-audit-pack'),
    path('reports/audit-pack/pdf/',      AuditPackPdfView.as_view(),     name='v1-audit-pack-pdf'),
    # CFO-locked headline figures with drift detection (PR P1 handover 2026-05-17)
    path('reports/frozen-drift/',                FrozenDriftView.as_view(),    name='v1-frozen-drift'),
    path('reports/frozen-drift/narrative/',      FrozenDriftNarrativeView.as_view(), name='v1-frozen-drift-narrative'),
    path('reports/premium-lapse/',               PremiumLapseView.as_view(),   name='v1-premium-lapse'),
    path('reports/cost-per-hour/',               CostPerHourView.as_view(),    name='v1-cost-per-hour'),
    # B6 Claims Payment Movement (Bontle Tendani). Finance-only — the
    # gate is permission_classes on the view itself, not the sidebar entry;
    # the rows carry payee names against claim amounts.
    path('reports/claims-payment-movement/',     ClaimsPaymentMovementView.as_view(),
         name='v1-claims-payment-movement'),
    path('reports/frozen-drift/acknowledge/',    FrozenFigureAckView.as_view(), name='v1-frozen-drift-ack'),
    # ADSA pipeline port (CFO directive 2026-05-18) — TB → BS trace +
    # draft-JE triage. See reporting.ma_trace_views.
    path('reports/ma-trace/',                    __import__('reporting.ma_trace_views',
                                                     fromlist=['MaTraceView']).MaTraceView.as_view(),
         name='v1-ma-trace'),
    path('reports/asset-register/',      AssetRegisterView.as_view(),    name='v1-asset-register'),
    path('reports/asset-movement/',      AssetMovementView.as_view(),    name='v1-asset-movement'),
    # Asset Control & Handover — spare pool, my assets, quarterly reconciliation
    path('asset-control/spare-pool/',    SparePoolView.as_view(),        name='v1-asset-spare-pool'),
    path('asset-control/spare-pool/upload/', SparePoolUploadView.as_view(), name='v1-asset-spare-pool-upload'),
    path('asset-control/my-assets/',     MyAssetsView.as_view(),         name='v1-asset-my-assets'),
    path('asset-control/reconciliation/', AssetCountReconciliationView.as_view(), name='v1-asset-reconciliation'),
    path('reports/related-party-transactions/', RelatedPartyTransactionsView.as_view(), name='v1-rpt-disclosure'),

    # Regulatory (NBFIRA capital adequacy — live computation; snapshots via /capital-snapshots/)
    path('regulatory/capital-check/', CapitalCheckView.as_view(), name='v1-capital-check'),

    # Statutory tax compliance workflow (VAT / PAYE / OWHT / SAT) — the Tax
    # Calendar page. Dates computed in regulatory/tax_calendar.py, not the browser.
    path('tax-compliance/calendar/',              tax_api.tax_calendar,        name='v1-tax-calendar'),
    path('tax-compliance/owners/',                tax_api.tax_owners,          name='v1-tax-owners'),
    path('tax-compliance/regenerate/',            tax_api.tax_regenerate,      name='v1-tax-regenerate'),
    path('tax-compliance/tasks/<uuid:pk>/complete/',     tax_api.tax_task_complete,     name='v1-tax-task-complete'),
    path('tax-compliance/tasks/<uuid:pk>/verify/',       tax_api.tax_task_verify,       name='v1-tax-task-verify'),
    path('tax-compliance/tasks/<uuid:pk>/close-breach/', tax_api.tax_task_close_breach, name='v1-tax-task-close-breach'),
    path('tax-compliance/tasks/<uuid:pk>/change-date/',  tax_api.tax_task_change_date,  name='v1-tax-task-change-date'),
    path('tax-compliance/date-editors/',                 tax_api.tax_date_editors,      name='v1-tax-date-editors'),

    # VAT reconciliation (build spec B2) — output VAT, input VAT, net payable
    # or refundable, tied back to the ledger. READ-ONLY: it posts nothing and
    # changes no GL mapping. Finance-gated on the endpoint itself.
    path('tax-compliance/vat-recon/',             tax_api.vat_reconciliation,  name='v1-vat-recon'),

    # AI assist (DeepSeek — CFO-authorised exception, non-sensitive only)
    path('ai/suggest-accounts/', AIAccountSuggestView.as_view(), name='v1-ai-suggest-accounts'),
    path('ai/insight/',          AIInsightView.as_view(),        name='v1-ai-insight'),
    path('ai/aria/chat/',        AriaChatView.as_view(),         name='v1-ai-aria-chat'),
    path('ai/aria/confirm-action/', AriaConfirmActionView.as_view(), name='v1-ai-aria-confirm-action'),
    # ARIA Phase 1 — compliance deadline probe for the sprite badge.
    path('aria/popups/',
         __import__('core.aria.popup_views', fromlist=['unread_popups']).unread_popups,
         name='v1-aria-popups'),
    path('aria/popups/<uuid:popup_id>/read/',
         __import__('core.aria.popup_views', fromlist=['mark_popup_read']).mark_popup_read,
         name='v1-aria-popup-read'),
    path('aria/urgent-deadlines/',
         __import__('core.aria.api', fromlist=['urgent_deadlines_view']).urgent_deadlines_view,
         name='v1-aria-urgent-deadlines'),
    path('aria/deadlines/',
         __import__('core.aria.api', fromlist=['deadlines_view']).deadlines_view,
         name='v1-aria-deadlines'),
    path('aria/news/',
         __import__('core.aria.api', fromlist=['news_view']).news_view,
         name='v1-aria-news'),
    # Ledger integrity scanner — DeepSeek-backed narrative / vendor /
    # amount checks (CFO directive 2026-05-18). DISABLED 2026-05-18: the
    # Ledger integrity scanner — DeepSeek-backed narrative / vendor /
    # amount checks (CFO directive 2026-05-18). The module landed in
    # PR #147; this route was temporarily commented out by the
    # fix-forward eaf5f89 to keep prod from crash-looping on the
    # missing import. Re-enabled now that core.ai_integrity_views is
    # on main.
    path('ai/integrity-scan/',   __import__('core.ai_integrity_views',
                                       fromlist=['AIIntegrityScanView']).AIIntegrityScanView.as_view(),
         name='v1-ai-integrity-scan'),

    # Monthly "10 Commandments" — DeepSeek-rotated per category (CFO 2026-05-18).
    path('commandments/',            __import__('core.commandments_views',
                                          fromlist=['CommandmentCategoryListView']).CommandmentCategoryListView.as_view(),
         name='v1-commandment-categories'),
    path('commandments/<str:category>/',
                                     __import__('core.commandments_views',
                                          fromlist=['MonthlyCommandmentsView']).MonthlyCommandmentsView.as_view(),
         name='v1-commandments-month'),

    # Salvage access probe (frontend uses it to decide sidebar visibility)
    path('salvage/me-can-access/', SalvageAccessProbeView.as_view(), name='v1-salvage-can-access'),

    # One salvage photo, streamed (Bharath's photo request, 16-Sep-2026).
    # /media/ is NOT served in this deployment, so a raw file.url 404s and
    # every gallery tile would be a broken image — the full reasoning, and why
    # this one is deliberately open, is on salvage.api_views.salvage_photo.
    path('salvage/photo/<uuid:image_id>/', salvage_photo,
         name='v1-salvage-photo'),

    # Veritas Parts & Assessment Savings (CFO 2026-09-10) — monthly workbooks
    # from the Parts & Assessments team, read-only management figures.
    path('salvage/parts/upload/',
         __import__('salvage.parts_views',
                    fromlist=['PartsUploadView']).PartsUploadView.as_view(),
         name='v1-salvage-parts-upload'),
    path('salvage/parts/summary/',
         __import__('salvage.parts_views',
                    fromlist=['parts_summary']).parts_summary,
         name='v1-salvage-parts-summary'),

    # Motor Liquidators V3 integration (ApiKey scope: motor-liquidators)
    path('salvage/external/sale-completed/',
         __import__('salvage.external_views',
                    fromlist=['MLSaleCompletedView']).MLSaleCompletedView.as_view(),
         name='v1-salvage-external-sale-completed'),
    path('salvage/external/vehicle-lookup/',
         __import__('salvage.external_views',
                    fromlist=['MLVehicleLookupView']).MLVehicleLookupView.as_view(),
         name='v1-salvage-external-vehicle-lookup'),
    path('salvage/external/recoveries-summary/',
         __import__('salvage.external_views',
                    fromlist=['MLRecoveriesSummaryView']).MLRecoveriesSummaryView.as_view(),
         name='v1-salvage-external-recoveries-summary'),

    # Salvage public storefront — no auth required, rate-limited.
    path('salvage/public/items/',                PublicSalvageListView.as_view(),
         name='v1-salvage-public-items'),
    path('salvage/public/items/<str:item_code>/', PublicSalvageDetailView.as_view(),
         name='v1-salvage-public-item-detail'),
    path('salvage/public/quotes/',               PublicSalvageQuoteSubmitView.as_view(),
         name='v1-salvage-public-quote'),

    # CFO TB upload (CFO/admin only — uploads alpha_direct_full_tb_complete.csv
    # and runs import_tb_csv --commit on prod, with override-password re-auth)
    path('admin/cfo-upload-tb/',     cfo_upload_tb,     name='v1-cfo-upload-tb'),
    path('admin/cfo-upload-coa/',    cfo_upload_coa,    name='v1-cfo-upload-coa'),
    path('admin/cfo-upload-coa/template/', cfo_upload_coa_template, name='v1-cfo-upload-coa-template'),
    path('admin/cfo-upload-gl/',     cfo_upload_gl,     name='v1-cfo-upload-gl'),
    path('admin/cfo-upload-gl/template/', cfo_upload_gl_template, name='v1-cfo-upload-gl-template'),
    path('admin/cfo-upload-treaty/',          cfo_upload_treaty,          name='v1-cfo-upload-treaty'),
    path('admin/cfo-upload-treaty/template/', cfo_upload_treaty_template, name='v1-cfo-upload-treaty-template'),

    # Drop-any-document → DeepSeek extract → review → commit
    path('reinsurance/treaties/upload-parse/',  TreatyDocUploadParseView.as_view(),
         name='v1-treaty-doc-upload-parse'),
    path('reinsurance/treaties/upload-commit/', TreatyDocUploadCommitView.as_view(),
         name='v1-treaty-doc-upload-commit'),
    # Reinsurance renewal pack (2026/27) — download + manifest (CFO 2026-06-05)
    path('reinsurance/renewal/document/', _renewal_document, name='v1-renewal-document'),
    path('reinsurance/renewal/manifest/', _renewal_manifest, name='v1-renewal-manifest'),
    path('reinsurance/renewal/ask/',      _renewal_ask,      name='v1-renewal-ask'),
    # 11-Year historical treaty performance — locked dataset (CFO 2026-08-14)
    path('reinsurance/history/',           _ri_history,            name='v1-reinsurance-history'),
    path('reinsurance/history/amend/',     _ri_history_amend,      name='v1-reinsurance-history-amend'),
    path('reinsurance/history/snapshots/', _ri_history_snapshots,  name='v1-reinsurance-history-snapshots'),
    # Reinsurer Controls, Security Oversight & FAC Risk Register
    # (Arun P. Iyer control brief via the CFO, 15-Sep-2026). Read-only reporting
    # plus the onboarding state machine — nothing here posts a journal.
    path('reinsurance/controls/',              _rc_control_centre,  name='v1-reinsurance-control-centre'),
    path('reinsurance/counterparties/',        _rc_reinsurer_list,  name='v1-reinsurance-counterparties'),
    path('reinsurance/counterparties/new/',    _rc_reinsurer_create,
         name='v1-reinsurance-counterparty-create'),
    path('reinsurance/counterparties/<uuid:reinsurer_id>/',
         _rc_reinsurer_detail, name='v1-reinsurance-counterparty-detail'),
    path('reinsurance/counterparties/<uuid:reinsurer_id>/transition/',
         _rc_reinsurer_transition, name='v1-reinsurance-counterparty-transition'),
    path('reinsurance/fac-risk/',              _rc_fac_register,    name='v1-reinsurance-fac-risk'),
    path('reinsurance/fac-risk/<uuid:exposure_id>/',
         _rc_fac_detail, name='v1-reinsurance-fac-risk-detail'),
    path('reinsurance/security-panel/',        _rc_security_panel,  name='v1-reinsurance-security-panel'),
    # KYC / evidence register (control brief §5). The file is STREAMED, never
    # handed out as a /media/ URL — that path is not served in this deployment.
    path('reinsurance/counterparties/<uuid:reinsurer_id>/documents/',
         _re_documents, name='v1-reinsurance-counterparty-documents'),
    path('reinsurance/documents/<uuid:doc_id>/verify/',
         _re_verify_document, name='v1-reinsurance-document-verify'),
    path('reinsurance/documents/<uuid:doc_id>/download/',
         _re_document_download, name='v1-reinsurance-document-download'),
    path('admin/cfo-upload-status/', cfo_upload_status, name='v1-cfo-upload-status'),
    # HRIS access probe (CFO directive 2026-05-18: 5-person whitelist)
    path('admin/hris-access/',   hris_access_status, name='v1-hris-access'),
    # HRIS password gate (CFO directive 2026-05-18). Whitelist check lives
    # in core/hris_access.py; the password gate is an additional second
    # factor that times out every HRIS_UNLOCK_HOURS (default 8).
    path('hris/unlock/',         hris_unlock,       name='v1-hris-unlock'),
    path('hris/lock/',           hris_lock,         name='v1-hris-lock'),
    path('hris/lock-status/',    hris_lock_status,  name='v1-hris-lock-status'),

    # CFO landing-page aggregator
    path('dashboard/cfo/', CFODashboardView.as_view(), name='v1-dashboard-cfo'),

    # Documents
    path('documents/upload/',          DocumentUploadView.as_view(),  name='v1-document-upload'),
    path('documents/',                 DocumentListView.as_view(),    name='v1-document-list'),
    path('documents/<uuid:pk>/',       DocumentDetailView.as_view(),  name='v1-document-detail'),
    path('documents/<uuid:pk>/confirm/', DocumentConfirmView.as_view(), name='v1-document-confirm'),
    path('documents/<uuid:pk>/reject/',  DocumentRejectView.as_view(),  name='v1-document-reject'),

    # Smart Upload (CFO directive 2026-05-18) — drop any xlsx/csv/pdf/docx
    # into the per-section uploader; DeepSeek maps the columns, omni
    # previews canonical rows, CFO commits. See core/smart_upload/.
    path('smart-upload/sections/', __import__('core.smart_upload.api_views',
                                       fromlist=['sections_list']).sections_list,
         name='v1-smart-upload-sections'),
    path('smart-upload/template/', __import__('core.smart_upload.api_views',
                                       fromlist=['section_template']).section_template,
         name='v1-smart-upload-template'),
    path('smart-upload/autodetect/', __import__('core.smart_upload.api_views',
                                       fromlist=['smart_upload_autodetect']).smart_upload_autodetect,
         name='v1-smart-upload-autodetect'),
    path('smart-upload/preview/',  __import__('core.smart_upload.api_views',
                                       fromlist=['smart_upload_preview']).smart_upload_preview,
         name='v1-smart-upload-preview'),
    path('smart-upload/commit/',   __import__('core.smart_upload.api_views',
                                       fromlist=['smart_upload_commit']).smart_upload_commit,
         name='v1-smart-upload-commit'),
    # CFO directive 2026-05-20: one-click "wipe prior TB uploads for this
    # company" so a re-upload doesn't pile on duplicates. Works alongside
    # the 409-on-duplicate guard in /smart-upload/commit/.
    path('smart-upload/tb/clear/', __import__('core.smart_upload.api_views',
                                       fromlist=['smart_upload_tb_clear']).smart_upload_tb_clear,
         name='v1-smart-upload-tb-clear'),

    # CFO directive 2026-06-21 — read-only NL Q&A over the user-admin audit trail
    path('admin/audit-ask/',             AuditAskView.as_view(),
         name='v1-audit-ask'),

    # CFO directive 2026-05-22 — per-user entity allowlist
    path('me/companies/',                me_companies,
         name='v1-me-companies'),
    path('admin/user-company-access/',   user_company_access_admin,
         name='v1-user-company-access'),
    path('admin/user-company-access/bulk/',   user_company_access_bulk,
         name='v1-user-company-access-bulk'),
    path('admin/user-company-access/matrix/', user_company_access_matrix,
         name='v1-user-company-access-matrix'),
    path('admin/user-company-access/upload/', user_company_access_upload,
         name='v1-user-company-access-upload'),
    path('admin/user-company-access/template/', user_company_access_template,
         name='v1-user-company-access-template'),
    path('admin/user-titles/',          user_titles,
         name='v1-user-titles'),
    path('admin/user-titles/upload/',   user_titles_upload,
         name='v1-user-titles-upload'),
    path('admin/user-titles/template/', user_titles_template,
         name='v1-user-titles-template'),
    path('admin/user-emails/',          user_emails,
         name='v1-user-emails'),
    path('admin/user-emails/upload/',   user_emails_upload,
         name='v1-user-emails-upload'),
    path('admin/user-emails/template/', user_emails_template,
         name='v1-user-emails-template'),
    path('presence/online/',      presence_online_users, name='v1-presence-online'),
    path('tasks/',                omni_tasks,            name='v1-omni-tasks'),
    path('tasks/assignees/',      omni_task_assignees,   name='v1-omni-task-assignees'),
    path('tasks/handover/',       omni_task_handover,    name='v1-omni-task-handover'),
    path('tasks/<uuid:task_id>/', omni_task_detail,      name='v1-omni-task-detail'),
    path('tasks/<uuid:task_id>/comments/<uuid:comment_id>/file/',
         omni_task_comment_file,  name='v1-omni-task-comment-file'),
    # In-app team chat (polled). /task @user in a message → OmniTask. 2026-06-09.
    path('chat/messages/',
         __import__('core.chat_views', fromlist=['chat_messages']).chat_messages,
         name='v1-chat-messages'),
    path('chat/messages/<uuid:pk>/',
         __import__('core.chat_views', fromlist=['chat_message_detail']).chat_message_detail,
         name='v1-chat-message-detail'),
    path('admin/digital-assistant/upload/',        assistant_asset_upload,
         name='v1-assistant-asset-upload'),
    path('admin/digital-assistant/asset/<str:slug>', assistant_asset_serve,
         name='v1-assistant-asset-serve'),
]

# RealPay debit-order collections
from realpay.api_views import (
    realpay_reports_list, realpay_report_detail,
    realpay_pull_month,   realpay_backfill, realpay_analytics,
    realpay_debit_tracker, realpay_collections_dashboard,
    realpay_collections_analytics, realpay_recon_upload, realpay_billing_upload,
    realpay_graphite_live,
)
urlpatterns += [
    path('realpay/reports/',          realpay_reports_list, name='v1-realpay-reports'),
    path('realpay/reports/<uuid:report_id>/', realpay_report_detail,
         name='v1-realpay-report-detail'),
    path('realpay/pull-month/',       realpay_pull_month,   name='v1-realpay-pull-month'),
    path('realpay/backfill/',         realpay_backfill,     name='v1-realpay-backfill'),
    path('realpay/analytics/',        realpay_analytics,    name='v1-realpay-analytics'),
    path('realpay/graphite-live/',     realpay_graphite_live, name='v1-realpay-graphite-live'),
    # RealPay Collections module (CFO 2026-06-05)
    path('realpay/collections/debit-tracker/', realpay_debit_tracker,
         name='v1-realpay-debit-tracker'),
    path('realpay/collections/dashboard/',     realpay_collections_dashboard,
         name='v1-realpay-collections-dashboard'),
    path('realpay/collections/analytics/',     realpay_collections_analytics,
         name='v1-realpay-collections-analytics'),
    # Upload-and-reconcile against Graphite (Keetile Mokhendo, 2026-08-17).
    # The only writer of tracking_start_date — without this route the billing
    # month basis can never switch on (Bokani, bug 6a48367f).
    path('realpay/collections/billing-upload/', realpay_billing_upload,
         name='v1-realpay-collections-billing-upload'),
    path('realpay/collections/recon-upload/',  realpay_recon_upload,
         name='v1-realpay-recon-upload'),
]

# Weekly failed-debits report + the recipient list Debtors keep themselves
# (CFO 2026-09-11, replacing Rose Mokgware's hand-built email).
from realpay.failed_debits_api import (
    failed_debits_preview, report_recipient_detail, report_recipients,
)
urlpatterns += [
    path('realpay/failed-debits/preview/', failed_debits_preview,
         name='v1-failed-debits-preview'),
    path('report-recipients/', report_recipients,
         name='v1-report-recipients'),
    path('report-recipients/<uuid:recipient_id>/', report_recipient_detail,
         name='v1-report-recipient-detail'),
]

# Keetile Mokhendo's six Finance monitoring reports (handover note 2026-08-17).
from reporting.finance_monitoring_views import (
    finance_monitoring_detail, finance_monitoring_index,
)
urlpatterns += [
    path('finance-monitoring/', finance_monitoring_index,
         name='v1-finance-monitoring-index'),
    path('finance-monitoring/<slug:slug>/', finance_monitoring_detail,
         name='v1-finance-monitoring-detail'),
]

# Graphite Claims register (Bokani 2026-06-24) — full claims list + filters + export
from integrations.claims_views import (
    graphite_claims_register, claims_register_reconciliation_view)
from integrations.claim_insight_views import claim_insight
from integrations.graphite_ingest import GraphiteIngestView
urlpatterns += [
    path('claims-register/', graphite_claims_register, name='v1-claims-register'),
    path('claims-register/reconciliation/', claims_register_reconciliation_view,
         name='v1-claims-register-reconciliation'),
    # Claim insight for a payment request (CFO 2026-08-31) — facts + hard flags +
    # stored AI summary for one claim number. Read-only; never moves money.
    path('claims/insight/', claim_insight, name='v1-claim-insight'),
    # Graphite -> Omni analytics snapshots (Appendix A v1). Token-gated,
    # one-way, inert: arrival triggers nothing.
    path('graphite-ingest/', GraphiteIngestView.as_view(), name='v1-graphite-ingest'),
]

# Read-only viewer for the snapshots above — so a person can SEE what Graphite
# has pushed into Omni. Reads only; writes nothing.
from integrations.graphite_feeds_views import (
    graphite_feeds_list, graphite_feed_detail, graphite_claims_bridge_view)
from integrations.claims_views import graphite_feed_claim_drill
urlpatterns += [
    path('graphite-feeds/', graphite_feeds_list, name='v1-graphite-feeds'),
    path('graphite-feeds/claims-bridge/', graphite_claims_bridge_view,
         name='v1-graphite-claims-bridge'),
    # Claim-level drill-down (names + subject, finance/exec only). MUST precede
    # the <dataset> catch-all, or 'claims' is captured as a dataset name.
    path('graphite-feeds/claims/', graphite_feed_claim_drill,
         name='v1-graphite-feed-claim-drill'),
    path('graphite-feeds/<str:dataset>/', graphite_feed_detail,
         name='v1-graphite-feed-detail'),
]

# Page-shaped reads over the same snapshots, for panels on working screens that
# stand empty today (renewals, broker loss ratios, KYC completeness, large loss).
from integrations.graphite_panels_views import (
    renewals_due, broker_loss_ratios, kyc_completeness, major_claims)
urlpatterns += [
    path('graphite-panels/renewals-due/', renewals_due,
         name='v1-graphite-panel-renewals-due'),
    path('graphite-panels/broker-loss-ratios/', broker_loss_ratios,
         name='v1-graphite-panel-broker-lr'),
    path('graphite-panels/kyc-completeness/', kyc_completeness,
         name='v1-graphite-panel-kyc'),
    path('graphite-panels/major-claims/', major_claims,
         name='v1-graphite-panel-major-claims'),
]

# The monthly Premium / Claims / Loss Ratio report. Upload the source workbooks,
# get the three tabs back. Computes and returns; stores nothing, posts nothing.
from finance_report.api_views import build_finance_report
urlpatterns += [
    path('finance-report/build/', build_finance_report, name='v1-finance-report-build'),
]

# Microsoft 365 active-licence users (Settings > M365 Active Users).
# Like the ISO / HRIS-alerts blocks below, `router.urls` was already snapshot
# into `urlpatterns` at the top of this file (line ~792), so a late
# router.register() is a silent no-op and the list route 404s. Use a dedicated
# router and append its urls. The static `last-run/` path MUST come first, or
# it is captured as a pk by the router's catch-all `m365-active-users/<pk>/`.
from licensing.api_views import M365ActiveUserViewSet, last_run as m365_last_run
from rest_framework.routers import DefaultRouter as _M365Router
m365_router = _M365Router()
m365_router.register(r'm365-active-users', M365ActiveUserViewSet,
                     basename='m365-active-users')
urlpatterns += [
    path('m365-active-users/last-run/', m365_last_run, name='v1-m365-last-run'),
]
urlpatterns += m365_router.urls

# ISO 27001 — 10 commandments audit register + auditor-grade artefacts.
from iso_compliance.views import (
    CommandmentListView   as ISOCommandmentListView,
    AuditRunListView      as ISOAuditRunListView,
    RunAuditView          as ISORunAuditView,
    ResolveFindingView    as ISOResolveFindingView,
    AcceptFindingView     as ISOAcceptFindingView,
    SoAControlViewSet, RiskViewSet, CAPAViewSet, PolicyViewSet,
    EvidenceViewSet, InternalAuditViewSet, ManagementReviewViewSet,
    DPIAViewSet, DPIAConditionViewSet, DPIARiskViewSet,
    seed_soa as iso_seed_soa, soa_summary as iso_soa_summary,
    auditor_pack as iso_auditor_pack,
    import_risks as iso_import_risks,
)
# `urlpatterns` was already built above as `[…] + router.urls + [...]` — at that
# moment the routes from `router.urls` were snapshot. Any router.register call
# after that point is a no-op as far as the URLConf is concerned. So we keep
# a SECOND DRF router dedicated to the ISO viewsets and append ITS urls.
from rest_framework.routers import DefaultRouter as _DRFRouter
iso_router = _DRFRouter()
iso_router.register(r'iso/soa',                  SoAControlViewSet,        basename='iso-soa')
iso_router.register(r'iso/risks',                RiskViewSet,              basename='iso-risk')
iso_router.register(r'iso/capas',                CAPAViewSet,              basename='iso-capa')
iso_router.register(r'iso/policies',             PolicyViewSet,            basename='iso-policy')
iso_router.register(r'iso/evidence',             EvidenceViewSet,          basename='iso-evidence')
iso_router.register(r'iso/internal-audits',      InternalAuditViewSet,     basename='iso-internal-audit')
iso_router.register(r'iso/management-reviews',   ManagementReviewViewSet,  basename='iso-management-review')
# DPO — Data Protection Impact Assessment register (CFO 2026-08-13).
iso_router.register(r'dpo/dpia',                 DPIAViewSet,              basename='dpo-dpia')
iso_router.register(r'dpo/dpia-conditions',      DPIAConditionViewSet,     basename='dpo-dpia-condition')
iso_router.register(r'dpo/dpia-risks',           DPIARiskViewSet,          basename='dpo-dpia-risk')

# AML/CFT registers (CFO 2026-09-16). The models shipped 2026-09-09 with no
# route and no page, so Django admin was the only way in — and the AML officer
# is not a staff user. Every objective counting these registers therefore read
# zero whatever work was actually done. These routes are what make them
# recordable from the app.
from iso_compliance.aml_views import (
    AMLTrainingRecordViewSet, ComplianceReportViewSet,
    SanctionsScreeningViewSet, VendorKYCScreeningViewSet,
)
iso_router.register(r'aml/sanctions-screenings', SanctionsScreeningViewSet, basename='aml-sanctions')
iso_router.register(r'aml/training',             AMLTrainingRecordViewSet,  basename='aml-training')
iso_router.register(r'aml/board-reports',        ComplianceReportViewSet,   basename='aml-board-report')
iso_router.register(r'aml/supplier-screening',   VendorKYCScreeningViewSet, basename='aml-supplier-screening')

# Risk register bulk upload — MUST sit above `iso_router.urls`, otherwise
# DRF's /iso/risks/<pk>/ lookup catches /iso/risks/import/ with pk='import'
# and answers 404 (Unopa Male, 2026-09-18).
urlpatterns += [
    path('iso/risks/import/',                iso_import_risks,         name='v1-iso-risks-import'),
]

urlpatterns += iso_router.urls

urlpatterns += [
    path('iso/commandments/',                ISOCommandmentListView.as_view(), name='v1-iso-commandments'),
    path('iso/runs/',                        ISOAuditRunListView.as_view(),    name='v1-iso-runs'),
    path('iso/run-audit/',                   ISORunAuditView.as_view(),        name='v1-iso-run-audit'),
    path('iso/findings/<uuid:finding_id>/resolve/', ISOResolveFindingView.as_view(),
         name='v1-iso-finding-resolve'),
    path('iso/findings/<uuid:finding_id>/accept/',  ISOAcceptFindingView.as_view(),
         name='v1-iso-finding-accept'),
    path('iso/seed-soa/',                    iso_seed_soa,             name='v1-iso-seed-soa'),
    path('iso/soa-summary/',                 iso_soa_summary,          name='v1-iso-soa-summary'),
    path('iso/auditor-pack/',                iso_auditor_pack,         name='v1-iso-auditor-pack'),
]

# SOP Bank — ISO 9001 QMS document library + read-acknowledgment evidence
# (CFO directive 2026-06-10, BOBS submission audit gap).
from iso_compliance.views import (
    sop_list as iso_sop_list, sop_download as iso_sop_download,
    sop_acknowledge as iso_sop_acknowledge, sop_coverage as iso_sop_coverage,
    sop_upload as iso_sop_upload, sop_confirm as iso_sop_confirm,
    sop_discard as iso_sop_discard,
)
urlpatterns += [
    path('iso/sops/',                          iso_sop_list,        name='v1-iso-sops'),
    path('iso/sops/coverage/',                 iso_sop_coverage,    name='v1-iso-sop-coverage'),
    path('iso/sops/upload/',                   iso_sop_upload,      name='v1-iso-sop-upload'),
    path('iso/sops/<uuid:sop_id>/download/',   iso_sop_download,    name='v1-iso-sop-download'),
    path('iso/sops/<uuid:sop_id>/acknowledge/', iso_sop_acknowledge, name='v1-iso-sop-acknowledge'),
    path('iso/sops/<uuid:sop_id>/confirm/',    iso_sop_confirm,     name='v1-iso-sop-confirm'),
    path('iso/sops/<uuid:sop_id>/discard/',    iso_sop_discard,     name='v1-iso-sop-discard'),
]

# HRIS alerts (Unami wishlist 2026-06-02).  Same router-after-snapshot
# pattern as the ISO block above — use a fresh DRF router and append
# its urls explicitly.
from hris.alerts_views import (
    HRISAlertViewSet, acknowledge as hris_alert_ack,
    dismiss as hris_alert_dismiss, resolve as hris_alert_resolve,
    generate_now as hris_alert_generate,
)
hris_alerts_router = _DRFRouter()
hris_alerts_router.register(r'hris/alerts', HRISAlertViewSet, basename='hris-alert')

# Static actions MUST precede the router's catch-all detail route
# (`hris/alerts/<pk>/`), or the literal 'generate' is captured as a pk — a
# POST then 405s and a GET 404s (the "Generate Now" button was dead).
# acknowledge/dismiss/resolve carry an extra path segment so they never
# collided, but they belong with generate for clarity.
urlpatterns += [
    path('hris/alerts/generate/',                    hris_alert_generate,
         name='v1-hris-alert-generate'),
    path('hris/alerts/<uuid:alert_id>/acknowledge/', hris_alert_ack,
         name='v1-hris-alert-ack'),
    path('hris/alerts/<uuid:alert_id>/dismiss/',     hris_alert_dismiss,
         name='v1-hris-alert-dismiss'),
    path('hris/alerts/<uuid:alert_id>/resolve/',     hris_alert_resolve,
         name='v1-hris-alert-resolve'),
]
urlpatterns += hris_alerts_router.urls

# HR contract module + HR self-service settings (CFO HR plan, 19-Sep-2026).
from hris import contract_views as _hr_contracts, hr_settings_views as _hr_settings
urlpatterns += [
    path('hris/contracts/template/', _hr_contracts.contract_template,
         name='v1-hris-contract-template'),
    path('hris/contracts/upload/', _hr_contracts.contract_upload,
         name='v1-hris-contract-upload'),
    path('hris/contracts/<uuid:contract_id>/decision/', _hr_contracts.contract_decision,
         name='v1-hris-contract-decision'),
    path('hris/contracts/', _hr_contracts.contract_register,
         name='v1-hris-contract-register'),
    path('hris/settings/update/', _hr_settings.hr_setting_update, name='v1-hris-settings-update'),
    path('hris/settings/lock/', _hr_settings.hr_setting_lock, name='v1-hris-settings-lock'),
    path('hris/settings/rule/', _hr_settings.hr_rule_update, name='v1-hris-settings-rule'),
    path('hris/settings/team/add/', _hr_settings.hr_team_add, name='v1-hris-settings-team-add'),
    path('hris/settings/team/remove/', _hr_settings.hr_team_remove,
         name='v1-hris-settings-team-remove'),
    path('hris/settings/person-flags/', _hr_settings.hr_person_flags,
         name='v1-hris-settings-person-flags'),
    path('hris/settings/', _hr_settings.hr_settings_overview, name='v1-hris-settings'),
]

# HR batch 2 (CFO 19-Sep-2026 "everything today"): leavers, joiners, sign-offs, probation,
# renewal follow-through, logins without payroll, systems per role, offer letters,
# Group Payroll phase 2. Static paths before <uuid> paths.
from hris import (offboarding_views as _hr_off, joiner_views as _hr_join,
                  contract_followup_views as _hr_cf)
from recruitment import offer_views as _rec_offer
from payroll import group_report_extra_views as _gp_extra
urlpatterns += [
    path('hris/settings/systems/', _hr_settings.hr_role_systems, name='v1-hris-settings-systems'),
    path('hris/offboarding/open/', _hr_off.offboarding_open, name='v1-hris-offboarding-open'),
    path('hris/offboarding/employees/', _hr_off.offboarding_employees,
         name='v1-hris-offboarding-employees'),
    path('hris/offboarding/<uuid:case_id>/step/', _hr_off.offboarding_step,
         name='v1-hris-offboarding-step'),
    path('hris/offboarding/<uuid:case_id>/cancel/', _hr_off.offboarding_cancel,
         name='v1-hris-offboarding-cancel'),
    path('hris/offboarding/<uuid:case_id>/', _hr_off.offboarding_detail,
         name='v1-hris-offboarding-detail'),
    path('hris/offboarding/', _hr_off.offboarding_list, name='v1-hris-offboarding'),
    path('hris/joiners/<uuid:employee_id>/start/', _hr_join.start_joiner, name='v1-hris-joiner-start'),
    path('hris/joiners/', _hr_join.joiners_list, name='v1-hris-joiners'),
    path('hris/my-signoffs/', _hr_join.my_signoffs, name='v1-hris-my-signoffs'),
    path('hris/acknowledgements/<uuid:ack_id>/sign/', _hr_join.sign_acknowledgement,
         name='v1-hris-ack-sign'),
    path('hris/contracts/<uuid:contract_id>/probation/', _hr_cf.contract_probation,
         name='v1-hris-contract-probation'),
    path('hris/contracts/<uuid:contract_id>/follow-through/', _hr_cf.contract_follow_through,
         name='v1-hris-contract-follow-through'),
    path('hris/contracts/<uuid:contract_id>/letter/', _hr_cf.contract_letter,
         name='v1-hris-contract-letter'),
    path('hris/logins-without-payroll/<int:user_id>/classify/', _hr_cf.classify_login_view,
         name='v1-hris-login-classify'),
    path('hris/logins-without-payroll/', _hr_cf.logins_without_payroll,
         name='v1-hris-logins-without-payroll'),
    path('recruitment/offers/<uuid:authority_id>/draft/', _rec_offer.offer_draft,
         name='v1-recruitment-offer-draft'),
    path('recruitment/offers/<uuid:authority_id>/letter/', _rec_offer.offer_letter_download,
         name='v1-recruitment-offer-letter'),
    path('recruitment/offers/<uuid:authority_id>/status/', _rec_offer.offer_status,
         name='v1-recruitment-offer-status'),
    path('recruitment/offers/', _rec_offer.offer_list, name='v1-recruitment-offers'),
    path('payroll/group-report/trend/', _gp_extra.trend_view, name='v1-payroll-gr-trend'),
    path('payroll/group-report/departments/', _gp_extra.departments_view,
         name='v1-payroll-gr-departments'),
    path('payroll/group-report/people/', _gp_extra.people_view, name='v1-payroll-gr-people'),
    path('payroll/group-report/agent-commissions/', _gp_extra.agent_commissions_view,
         name='v1-payroll-gr-agent-commissions'),
]

# Screen-integrity monitor (CFO 2026-09-06) — frozen-screen / weight-on-a-key
# Time Doctor exceptions, pulled from the stored daily sweep; re-scan one day live.
from hris.screen_integrity_views import (
    screen_integrity_list as hris_screen_integrity_list,
    screen_integrity_rescan as hris_screen_integrity_rescan,
)
urlpatterns += [
    path('hris/screen-integrity/',        hris_screen_integrity_list,
         name='v1-hris-screen-integrity'),
    path('hris/screen-integrity/rescan/', hris_screen_integrity_rescan,
         name='v1-hris-screen-integrity-rescan'),
]

# HR document vault (Dorothy 2026-06-26) — onboarding/policy/exit/disciplinary files.
from hris.document_views import (documents as hris_documents,
                                 document_download as hris_document_download,
                                 my_documents as hris_my_documents)
urlpatterns += [
    path('hris/documents/',                    hris_documents,          name='v1-hris-documents'),
    path('hris/documents/<uuid:pk>/download/', hris_document_download,  name='v1-hris-document-download'),
    # Employee / line-manager / C-suite / HR view of personal docs (Development
    # Dialogues) — scoped by hris.document_access. CFO 2026-07-18.
    path('hris/my-documents/',                 hris_my_documents,       name='v1-hris-my-documents'),
]

# HRIS "excite Unami" feature pack (CFO directive 2026-07-21): pulse/mood,
# flight-risk radar, skills & gaps map, OKR alignment tree, manager scorecard,
# plus the one-click "I am happy with this feature" adoption tracker.
from hris.pulse_views import pulse_dashboard, submit_pulse, my_pulse
from hris.flight_risk_views import flight_risk
from hris.command_center_views import command_center
from hris.skills_views import skills_matrix, create_skill, set_level
from hris.okr_tree_views import okr_tree, okr_create_objective, okr_periods
from bonu.urls import urlpatterns as bonu_urls
from hris.okr_self_views import (my_okrs, add_my_sub_objective, check_in as okr_check_in,
                                 key_result_history)
from hris.manager_scorecard_views import manager_scorecard as hris_manager_scorecard
from hris.feature_adoption_views import (
    feature_adoption, accept_feature, feature_adoption_team,
)
urlpatterns += [
    # Pulse / mood check
    path('hris/pulse/submit/',            submit_pulse,           name='v1-hris-pulse-submit'),
    path('hris/pulse/me/',                my_pulse,               name='v1-hris-pulse-me'),
    path('hris/pulse/dashboard/',         pulse_dashboard,        name='v1-hris-pulse-dashboard'),
    # Flight-risk radar
    path('hris/flight-risk/',             flight_risk,            name='v1-hris-flight-risk'),
    path('hris/command-center/',          command_center,         name='v1-hris-command-center'),
    # Skills & gaps map
    path('hris/skills/matrix/',           skills_matrix,          name='v1-hris-skills-matrix'),
    path('hris/skills/skill/',            create_skill,           name='v1-hris-skills-create'),
    path('hris/skills/set-level/',        set_level,              name='v1-hris-skills-set-level'),
    # OKR alignment tree
    path('hris/okr-tree/',                okr_tree,               name='v1-hris-okr-tree'),
    path('hris/okr-tree/objective/',      okr_create_objective,   name='v1-hris-okr-tree-objective'),
    path('hris/okr-tree/periods/',        okr_periods,            name='v1-hris-okr-tree-periods'),
    # "My objectives" — employee self-service (NOT behind the HRIS unlock; every
    # query is clamped to the caller). Unami 2026-07-30.
    path('hris/okr/mine/',                my_okrs,                name='v1-hris-okr-mine'),
    path('hris/okr/mine/sub-objective/',  add_my_sub_objective,   name='v1-hris-okr-mine-sub'),
    path('hris/okr/mine/check-in/',       okr_check_in,           name='v1-hris-okr-mine-checkin'),
    path('hris/okr/mine/history/<uuid:kr_id>/', key_result_history,
         name='v1-hris-okr-mine-history'),
    # Manager scorecard
    path('hris/manager-scorecard/',       hris_manager_scorecard, name='v1-hris-manager-scorecard'),
    # "I am happy with this feature" adoption tracker
    path('hris/feature-adoption/',        feature_adoption,       name='v1-hris-feature-adoption'),
    path('hris/feature-adoption/accept/', accept_feature,         name='v1-hris-feature-adoption-accept'),
    path('hris/feature-adoption/team/',   feature_adoption_team,  name='v1-hris-feature-adoption-team'),
]

# Loss-ratio reports (claims) — CFO directive 2026-06-15
urlpatterns += [
    path('claims/loss-ratio/client/', ClientLossRatioView.as_view(),
         name='v1-claims-loss-ratio-client'),
    path('claims/loss-ratio/large/',  LargeLossClientsView.as_view(),
         name='v1-claims-loss-ratio-large'),
    # Subrogation GL-account assignment + RealPay import space (CFO 2026-08-17)
    path('subrogation-gl-config/',    SubrogationGLConfigView.as_view(),
         name='v1-subrogation-gl-config'),
    path('subrogation-realpay-import/', SubrogationRealPayImportView.as_view(),
         name='v1-subrogation-realpay-import'),
]

# Time Doctor workforce report (CFO directive 2026-06-16) — read-only daily
# snapshot pulled by the pull_timedoctor cron; gated to managers / HR.
from integrations.timedoctor_views import (
    timedoctor_daily, timedoctor_history, timedoctor_ingest, timedoctor_run,
    timedoctor_reconciliation,
)
from hris.workforce_views import (
    workforce_brief_toggle, my_brief, justify_day, timedoctor_leaderboard,
    pending_justifications, review_justification, timedoctor_latecomers,
    tracking_setup, set_tracking, confirm_match, plan_absence, excuses_feed,
)
urlpatterns += [
    path('timedoctor/leaderboard/', timedoctor_leaderboard, name='v1-workforce-leaderboard'),
    path('timedoctor/latecomers/', timedoctor_latecomers, name='v1-workforce-latecomers'),
    path('timedoctor/daily/',   timedoctor_daily,   name='v1-timedoctor-daily'),
    path('timedoctor/history/', timedoctor_history, name='v1-timedoctor-history'),
    path('timedoctor/ingest/',  timedoctor_ingest,  name='v1-timedoctor-ingest'),
    path('timedoctor/run/',     timedoctor_run,     name='v1-timedoctor-run'),
    path('timedoctor/reconciliation/', timedoctor_reconciliation, name='v1-timedoctor-recon'),
    path('timedoctor/brief-toggle/', workforce_brief_toggle, name='v1-workforce-brief-toggle'),
    path('timedoctor/my-brief/',  my_brief,    name='v1-workforce-my-brief'),
    path('timedoctor/justify/',   justify_day, name='v1-workforce-justify'),
    path('timedoctor/plan-absence/', plan_absence, name='v1-workforce-plan-absence'),
    path('timedoctor/justifications/pending/', pending_justifications, name='v1-workforce-just-pending'),
    path('timedoctor/justifications/review/',  review_justification,  name='v1-workforce-just-review'),
    path('timedoctor/excuses/', excuses_feed, name='v1-workforce-excuses'),
    path('timedoctor/tracking-setup/', tracking_setup, name='v1-workforce-tracking-setup'),
    path('timedoctor/set-tracking/',   set_tracking,   name='v1-workforce-set-tracking'),
    path('timedoctor/confirm-match/',  confirm_match,  name='v1-workforce-confirm-match'),
]

# My Omni — personal employee home (2026-08-29). Own tracked hours + company
# announcements. Everything else on the home reuses existing endpoints.
from core.my_omni_views import my_hours, announcements_view, announcement_detail, weather
urlpatterns += [
    path('timedoctor/my-hours/',        my_hours,           name='v1-timedoctor-my-hours'),
    path('my-omni/weather/',            weather,            name='v1-my-omni-weather'),
    path('announcements/',              announcements_view, name='v1-announcements'),
    path('announcements/<uuid:pk>/',    announcement_detail, name='v1-announcement-detail'),
]

# Alpha Rewards (Project Nexus) — CFO directive 2026-06-22. Telematics-driven
# customer rewards folded into Omni as a separate tab.
from rest_framework.routers import DefaultRouter as _RewardsRouter
from rewards.api_views import (
    RewardProgramViewSet, RewardPartnerViewSet, RewardMemberViewSet,
    PointsTransactionViewSet, DrivingScoreViewSet, RewardsSummaryView,
)
rewards_router = _RewardsRouter()
rewards_router.register(r'reward-programs',     RewardProgramViewSet,     basename='reward-program')
rewards_router.register(r'reward-partners',     RewardPartnerViewSet,     basename='reward-partner')
rewards_router.register(r'reward-members',      RewardMemberViewSet,      basename='reward-member')
rewards_router.register(r'points-transactions', PointsTransactionViewSet, basename='points-transaction')
rewards_router.register(r'driving-scores',      DrivingScoreViewSet,      basename='driving-score')
urlpatterns += rewards_router.urls
from rewards.api_views import health_consent as rewards_health_consent
from rewards.api_views import health_metrics as rewards_health_metrics
urlpatterns += [
    path('rewards/summary/', RewardsSummaryView.as_view(), name='v1-rewards-summary'),
    # Health Points (Alpha Rewards Health App). NOTE: kept under 'rewards/' on
    # purpose — '/health/...' is owned by the healthcare INSURANCE quotes
    # module and must not be collided with.
    path('rewards/health-consent/', rewards_health_consent, name='v1-rewards-health-consent'),
    path('rewards/health-metrics/', rewards_health_metrics, name='v1-rewards-health-metrics'),
]
# Alpha Thrive — finger-PPG vitals, wellness score, trend, coach (2026-06-26).
# Wellness only, never diagnosis. Derived numbers only; coach is non-Anthropic.
from rewards.api_views import (
    thrive_scan, thrive_alpha_score, thrive_risk_trend, thrive_coach,
    nexus_leaderboard, nexus_standings_send,
)
urlpatterns += [
    path('rewards/thrive/scan/',        thrive_scan,        name='v1-rewards-thrive-scan'),
    path('rewards/thrive/alpha-score/', thrive_alpha_score, name='v1-rewards-thrive-alpha-score'),
    path('rewards/thrive/risk-trend/',  thrive_risk_trend,  name='v1-rewards-thrive-risk-trend'),
    path('rewards/thrive/coach/',       thrive_coach,       name='v1-rewards-thrive-coach'),
    # Staff/CFO leaderboard + standings email for the Alpha Nexus tester competition (SSO).
    path('rewards/nexus-leaderboard/',  nexus_leaderboard,  name='v1-rewards-nexus-leaderboard'),
    path('rewards/nexus-standings-send/', nexus_standings_send, name='v1-rewards-nexus-standings-send'),
]
# Customer app (Nexus + Rewards + Thrive) — email-OTP auth, member-scoped (2026-06-26).
# Public surface for the customer-facing /m app; NOT staff SSO. Each endpoint
# resolves the member from the login token, never from the client.
from rewards.customer_views import (
    customer_request_otp, customer_verify_otp, customer_logout, customer_me,
    customer_rewards, customer_drive, customer_drive_trip, customer_activity,
    customer_thrive_scan, customer_thrive_score, customer_thrive_trend, customer_thrive_coach,
    customer_delete_account, customer_feedback,
    customer_health_consent, customer_health_metrics,
    customer_growth, customer_policy,
    customer_pair_start, customer_pair_complete, customer_steps_sync, customer_workouts_sync,
    customer_team, customer_team_join, customer_team_leave,
    customer_screening, customer_screening_claim, customer_referral,
)
from integrations.claim_status_views import (
    claim_request_code, claim_verify_code,
    cfo_request_code, cfo_verify_code, cfo_check_session,
    claim_lookup,
)
urlpatterns += [
    # Internal claim-detail lookup by number (twin CFO/staff chat). ApiKey-auth.
    path('reports/claim-lookup/',                claim_lookup,         name='v1-reports-claim-lookup'),
    # Staff "look up a client" from the phone (CFO 2026-09-04): claims + policies by
    # number or name, and one policy's card. Read-only Graphite replica.
    path('graphite/search/',
         __import__('integrations.graphite_lookup_views', fromlist=['graphite_search']).graphite_search,
         name='v1-graphite-search'),
    path('graphite/policy/',
         __import__('integrations.graphite_lookup_views', fromlist=['graphite_policy']).graphite_policy,
         name='v1-graphite-policy'),
    path('graphite/claim-brief/',
         __import__('integrations.graphite_lookup_views', fromlist=['graphite_claim_brief']).graphite_claim_brief,
         name='v1-graphite-claim-brief'),
    # [B1] Renewal report — who renews in a chosen month, read from Graphite.
    path('graphite/renewals/',
         __import__('integrations.renewal_report_views', fromlist=['renewal_list']).renewal_list,
         name='v1-graphite-renewals'),
    # Public "check my claim status" line (email one-time code). No auth.
    path('public/claim/request-code/',           claim_request_code,   name='v1-public-claim-request-code'),
    path('public/claim/verify-code/',            claim_verify_code,    name='v1-public-claim-verify-code'),
    # Digital-CFO twin 2-factor login (password + emailed code -> session token).
    path('public/cfo-twin/request-code/',        cfo_request_code,     name='v1-cfo-twin-request-code'),
    path('public/cfo-twin/verify-code/',         cfo_verify_code,      name='v1-cfo-twin-verify-code'),
    path('public/cfo-twin/check/',               cfo_check_session,    name='v1-cfo-twin-check'),
    path('rewards/customer/request-otp/',        customer_request_otp, name='v1-customer-request-otp'),
    path('rewards/customer/verify-otp/',         customer_verify_otp,  name='v1-customer-verify-otp'),
    path('rewards/customer/logout/',             customer_logout,      name='v1-customer-logout'),
    path('rewards/customer/delete-account/',     customer_delete_account, name='v1-customer-delete-account'),
    path('rewards/customer/feedback/',           customer_feedback,    name='v1-customer-feedback'),
    path('rewards/customer/me/',                 customer_me,          name='v1-customer-me'),
    path('rewards/customer/rewards/',            customer_rewards,     name='v1-customer-rewards'),
    path('rewards/customer/drive/',              customer_drive,       name='v1-customer-drive'),
    path('rewards/customer/drive/trip/',         customer_drive_trip,  name='v1-customer-drive-trip'),
    path('rewards/customer/activity/',           customer_activity,    name='v1-customer-activity'),
    # Family & friend teams, the pulse-check screening voucher and referrals
    # (CFO 2026-09-08). Every one is member-scoped: the signed-in token IS the
    # scope, so there is no path to another member's team or voucher.
    path('rewards/customer/team/',               customer_team,        name='v1-customer-team'),
    path('rewards/customer/team/join/',          customer_team_join,   name='v1-customer-team-join'),
    path('rewards/customer/team/leave/',         customer_team_leave,  name='v1-customer-team-leave'),
    path('rewards/customer/screening/',          customer_screening,   name='v1-customer-screening'),
    path('rewards/customer/screening/claim/',    customer_screening_claim, name='v1-customer-screening-claim'),
    path('rewards/customer/referral/',           customer_referral,    name='v1-customer-referral'),
    path('rewards/customer/pair/start/',         customer_pair_start,    name='v1-customer-pair-start'),
    path('rewards/customer/pair/complete/',      customer_pair_complete, name='v1-customer-pair-complete'),
    path('rewards/customer/steps/sync/',         customer_steps_sync,    name='v1-customer-steps-sync'),
    path('rewards/customer/workouts/sync/',      customer_workouts_sync, name='v1-customer-workouts-sync'),
    path('rewards/customer/thrive/scan/',        customer_thrive_scan, name='v1-customer-thrive-scan'),
    path('rewards/customer/thrive/alpha-score/', customer_thrive_score, name='v1-customer-thrive-score'),
    path('rewards/customer/thrive/risk-trend/',  customer_thrive_trend, name='v1-customer-thrive-trend'),
    path('rewards/customer/thrive/coach/',       customer_thrive_coach, name='v1-customer-thrive-coach'),
    path('rewards/customer/health-consent/',     customer_health_consent, name='v1-customer-health-consent'),
    path('rewards/customer/health-metrics/',     customer_health_metrics, name='v1-customer-health-metrics'),
    path('rewards/customer/growth/',             customer_growth,      name='v1-customer-growth'),
    path('rewards/customer/policy/',             customer_policy,      name='v1-customer-policy'),
]

# Alpha Staff Rewards (employee programme) — separate ledger + tier ladder
# from the customer rewards above. Three pillars (Innovation / Business Impact
# / Health & Wellness); identity is payroll.Employee; meal+steps gated off
# pending DPIA. Approver endpoints gated via core.hris_access.hris_role.
from staff_rewards.api_views import (
    submit as staff_rewards_submit,
    dashboard as staff_rewards_dashboard,
    pending as staff_rewards_pending,
    approve as staff_rewards_approve,
    reject as staff_rewards_reject,
    score_meal as staff_rewards_score_meal,
)
urlpatterns += [
    path('staff-rewards/submit/',                      staff_rewards_submit,    name='v1-staff-rewards-submit'),
    path('staff-rewards/dashboard/',                   staff_rewards_dashboard, name='v1-staff-rewards-dashboard'),
    path('staff-rewards/pending/',                     staff_rewards_pending,   name='v1-staff-rewards-pending'),
    path('staff-rewards/submissions/<uuid:pk>/approve/', staff_rewards_approve, name='v1-staff-rewards-approve'),
    path('staff-rewards/submissions/<uuid:pk>/reject/',  staff_rewards_reject,  name='v1-staff-rewards-reject'),
    path('staff-rewards/score-meal/',                  staff_rewards_score_meal, name='v1-staff-rewards-score-meal'),
]

# Nexus — drive-rewards / telematics (taken over into Omni 2026-06-22)
from nexus.api_views import (nexus_summary, nexus_drivers, nexus_ingest_trip,
                             nexus_live_vehicles, nexus_phone_ping, fleet_vehicles)
urlpatterns += [
    path('nexus/summary/', nexus_summary, name='v1-nexus-summary'),
    path('nexus/drivers/', nexus_drivers, name='v1-nexus-drivers'),
    path('nexus/trips/',   nexus_ingest_trip, name='v1-nexus-ingest'),
    path('nexus/live/',    nexus_live_vehicles, name='v1-nexus-live'),
    path('nexus/phone-ping/', nexus_phone_ping, name='v1-nexus-phone-ping'),
    path('nexus/fleet/',   fleet_vehicles,   name='v1-nexus-fleet'),
]

# Vehicle Register — pool-car checkout / check-in with dual sign-off
# (CFO/EXCO 2026-07-16). Extends the nexus FleetVehicle register; no GL.
# Literal segments precede the <uuid:pk> detail route.
from nexus.vehicle_register import (
    vehicles as vr_vehicles, vehicle_detail as vr_vehicle_detail,
    clear_maintenance as vr_clear_maintenance, purposes as vr_purposes,
    register_board as vr_board, trips as vr_trips, checkout as vr_checkout,
    trip_checkin as vr_checkin, trip_signoff as vr_signoff,
    trip_correct_odometer as vr_correct_odo,
    trip_photo_upload as vr_photo_upload, trip_photo as vr_photo,
)
from nexus.vehicle_reports import reports as vr_reports, reports_export as vr_reports_export
urlpatterns += [
    path('nexus/vehicle-register/board/',     vr_board,     name='v1-vr-board'),
    path('nexus/vehicle-register/purposes/',  vr_purposes,  name='v1-vr-purposes'),
    path('nexus/vehicle-register/trips/',     vr_trips,     name='v1-vr-trips'),
    path('nexus/vehicle-register/checkout/',  vr_checkout,  name='v1-vr-checkout'),
    path('nexus/vehicle-register/trips/<uuid:pk>/checkin/', vr_checkin,       name='v1-vr-checkin'),
    path('nexus/vehicle-register/trips/<uuid:pk>/signoff/', vr_signoff,       name='v1-vr-signoff'),
    path('nexus/vehicle-register/trips/<uuid:pk>/correct-odometer/', vr_correct_odo, name='v1-vr-correct-odo'),
    path('nexus/vehicle-register/trips/<uuid:pk>/photo/',   vr_photo_upload,  name='v1-vr-photo-upload'),
    path('nexus/vehicle-register/photo/<uuid:pk>/',         vr_photo,         name='v1-vr-photo'),
    path('nexus/vehicle-register/reports/',        vr_reports,        name='v1-vr-reports'),
    path('nexus/vehicle-register/reports/export/', vr_reports_export, name='v1-vr-reports-export'),
    # Vehicle CRUD — clear-maintenance (longer path) before the detail route.
    path('nexus/vehicles/<uuid:pk>/clear-maintenance/', vr_clear_maintenance, name='v1-vr-clear-maintenance'),
    path('nexus/vehicles/<uuid:pk>/', vr_vehicle_detail, name='v1-vr-vehicle-detail'),
    path('nexus/vehicles/',           vr_vehicles,       name='v1-vr-vehicles'),
]

# Recruitment / ATS — native Omni, local CV↔job matching (CFO 2026-07-11)
from recruitment.api_views import (requisitions, requisition_applications,
                                   upload_candidate, application_stage, analyse_application,
                                   application_scorecards)
urlpatterns += [
    path('recruitment/requisitions/', requisitions, name='v1-recruitment-reqs'),
    path('recruitment/requisitions/<uuid:rid>/applications/', requisition_applications,
         name='v1-recruitment-req-apps'),
    path('recruitment/requisitions/<uuid:rid>/candidates/', upload_candidate,
         name='v1-recruitment-upload'),
    path('recruitment/applications/<uuid:aid>/stage/', application_stage,
         name='v1-recruitment-stage'),
    path('recruitment/applications/<uuid:aid>/analyse/', analyse_application,
         name='v1-recruitment-analyse'),
    path('recruitment/applications/<uuid:aid>/scorecards/', application_scorecards,
         name='v1-recruitment-scorecards'),
]

# Authority to Recruit (CFO 2026-08-03) — restricted to the five signatories.
from recruitment.authority_views import (authorities, authority_detail,
                                         authority_sign, authority_document,
                                         authority_convert)
urlpatterns += [
    path('recruitment/authorities/', authorities, name='v1-recruitment-authorities'),
    path('recruitment/authorities/<uuid:auth_id>/', authority_detail,
         name='v1-recruitment-authority-detail'),
    path('recruitment/authorities/<uuid:auth_id>/sign/', authority_sign,
         name='v1-recruitment-authority-sign'),
    path('recruitment/authorities/<uuid:auth_id>/document/', authority_document,
         name='v1-recruitment-authority-doc'),
    path('recruitment/authorities/<uuid:auth_id>/convert/', authority_convert,
         name='v1-recruitment-authority-convert'),
]

# Position tiers + job-title tagging (Unami Hiring-SOP, CFO 2026-09-02).
from recruitment.authority_tiers_views import (position_tiers, position_tier_update,
                                               job_title_tiers)
urlpatterns += [
    path('recruitment/position-tiers/', position_tiers, name='v1-recruitment-position-tiers'),
    path('recruitment/position-tiers/<int:tier>/', position_tier_update,
         name='v1-recruitment-position-tier-update'),
    path('recruitment/job-title-tiers/', job_title_tiers, name='v1-recruitment-job-title-tiers'),
]

# Public (no-login) job apply page — candidates reach this from a LinkedIn /
# Facebook link. AllowAny, hardened in the views (open-only, honeypot, rate limit).
from recruitment.public_views import public_job, public_apply
urlpatterns += [
    path('recruitment/public/jobs/<uuid:rid>/', public_job,
         name='v1-recruitment-public-job'),
    path('recruitment/public/jobs/<uuid:rid>/apply/', public_apply,
         name='v1-recruitment-public-apply'),
]

# Frozen-component governance (Internal Audit, 2026-06-23) — maker-checker gate
from core.frozen_views import frozen_components, frozen_requests, frozen_request_decide
urlpatterns += [
    path('frozen/components/', frozen_components, name='v1-frozen-components'),
    path('frozen/requests/',   frozen_requests,   name='v1-frozen-requests'),
    path('frozen/requests/<uuid:request_id>/decide/', frozen_request_decide,
         name='v1-frozen-request-decide'),
]

# Source-to-Ledger Reconciliation Hub (CFO 2026-07-03) — Phase 1.
# Sub-router so ViewSet routes register after the initial router.urls snapshot.
from reconciliation_hub.api_views import (
    ReconDashboardView,
    ReconRunView,
    ReconRunDetailView,
    ReconRunListView,
    MetricSourceMapViewSet,
    SourceFigureViewSet,
)
recon_router = DefaultRouter()
recon_router.register('recon/metric-map', MetricSourceMapViewSet, basename='recon-metric-map')
recon_router.register('recon/source-figures', SourceFigureViewSet, basename='recon-source-figure')
urlpatterns += recon_router.urls
urlpatterns += [
    path('recon/dashboard/',      ReconDashboardView.as_view(),  name='v1-recon-dashboard'),
    path('recon/run/',            ReconRunView.as_view(),        name='v1-recon-run'),
    path('recon/runs/',           ReconRunListView.as_view(),    name='v1-recon-runs'),
    path('recon/runs/<uuid:pk>/', ReconRunDetailView.as_view(),  name='v1-recon-run-detail'),
]

# Agent Portal — UNICOIN Instant Insurance sales-agent commissions (standalone;
# CFO 2026-07-06; moved under UniCoin branding 2026-07-27).
from agent_portal.api_views import AgentViewSet, CommissionCycleViewSet, AgentPayslipViewSet
agent_portal_router = DefaultRouter()
agent_portal_router.register('agent-portal/agents', AgentViewSet, basename='agent-portal-agent')
agent_portal_router.register('agent-portal/cycles', CommissionCycleViewSet, basename='agent-portal-cycle')
agent_portal_router.register('agent-portal/payslips', AgentPayslipViewSet, basename='agent-portal-payslip')
urlpatterns += agent_portal_router.urls

# Commissions — monthly agent commission submission (independent / in-house /
# BDU), one online form replacing emailed Excel workbooks. Standalone: submit →
# review → approve → export a payout file (no GL / payments coupling).
from commissions.api_views import CommissionGroupViewSet, CommissionSubmissionViewSet
commissions_router = DefaultRouter()
commissions_router.register('commissions/groups', CommissionGroupViewSet, basename='commission-group')
commissions_router.register('commissions/submissions', CommissionSubmissionViewSet, basename='commission-submission')
urlpatterns += commissions_router.urls

# Broker Commission register (Rose Mokgware / CFO 2026-09-08) — replaces the
# hand-kept 28-tab workbook. Plain function views, not a ViewSet: the register
# is read-mostly with three explicit actions, and the DefaultRouter above is
# already carrying the submission chain.
from commissions.broker_views import (            # noqa: E402
    broker_add_policy, broker_delete_policy, broker_detail, broker_list,
    broker_policy_lookup, broker_sync, broker_upload, broker_absorb,
    broker_commission_summary,
)
from commissions.broker_month_views import (      # noqa: E402
    broker_close_month, broker_set_compliance, broker_status_view,
)
urlpatterns += [
    path('commissions/brokers/', broker_list, name='v1-broker-list'),
    path('commissions/brokers/sync/', broker_sync, name='v1-broker-sync'),
    # Read-only commission preview (C7). Sits ABOVE the <uuid:pk> route so
    # the literal 'summary' segment is never swallowed as a broker id.
    path('commissions/brokers/summary/', broker_commission_summary,
         name='v1-broker-commission-summary'),
    path('commissions/brokers/policy-lookup/', broker_policy_lookup,
         name='v1-broker-policy-lookup'),
    # C5 — Finance's "Close month" (Full Access only). Above <uuid:pk> too.
    path('commissions/brokers/close-month/', broker_close_month,
         name='v1-broker-close-month'),
    path('commissions/brokers/upload/', broker_upload, name='v1-broker-upload'),
    path('commissions/brokers/<uuid:pk>/', broker_detail, name='v1-broker-detail'),
    path('commissions/brokers/<uuid:pk>/absorb/', broker_absorb, name='v1-broker-absorb'),
    path('commissions/brokers/<uuid:pk>/status/', broker_status_view,
         name='v1-broker-status'),
    path('commissions/brokers/<uuid:pk>/compliance/', broker_set_compliance,
         name='v1-broker-compliance'),
    path('commissions/brokers/<uuid:pk>/policies/', broker_add_policy,
         name='v1-broker-add-policy'),
    path('commissions/brokers/<uuid:pk>/policies/<uuid:policy_id>/', broker_delete_policy,
         name='v1-broker-delete-policy'),
]

# Alpha Aware — executive NL Q&A over the Graphite read replica (read-only,
# whitelisted; CFO directive 2026-06-30).
from django.urls import include as _aw_include
urlpatterns += [path('aware/', _aw_include('aware.urls'))]

# Underwriting document generator — Cover Notes + WCA certificates (CFO
# handover 2026-07-08). Ported tool served at /api/v1/underwriting/tool/.
from underwriting.api_views import QuoteViewSet, UnderwritingDocumentViewSet
from underwriting.views import underwriting_tool_html
underwriting_router = DefaultRouter()
underwriting_router.register('underwriting/documents', UnderwritingDocumentViewSet,
                             basename='underwriting-document')
# Quotations — one standard template, a register, and the ask box that fills it
# in from plain English (CFO/EXCO decision 2026-08-08).
underwriting_router.register('underwriting/quotes', QuoteViewSet,
                             basename='underwriting-quote')
urlpatterns += underwriting_router.urls
urlpatterns += [
    path('underwriting/render/', UnderwritingDocumentViewSet.as_view({'post': 'render'}),
         name='v1-underwriting-render'),
    path('underwriting/issue/', UnderwritingDocumentViewSet.as_view({'post': 'issue'}),
         name='v1-underwriting-issue'),
    path('underwriting/extract/', UnderwritingDocumentViewSet.as_view({'post': 'extract'}),
         name='v1-underwriting-extract'),
    # Phone flow (CFO 2026-09-04): one sentence → quote / cover note / WCA fields.
    path('underwriting/parse-text/', UnderwritingDocumentViewSet.as_view({'post': 'parse_text'}),
         name='v1-underwriting-parse-text'),
    path('underwriting/tool/', underwriting_tool_html, name='v1-underwriting-tool'),
]

# ---------------------------------------------------------------------------
# Staff Loans (HRIS) — staff + vehicle loans; employee applies → CFO approves
# → employee signs → HR disburses. Reuses the payroll EmployeeLoan engine for
# the monthly salary deduction. CFO directive 2026-07-15.
# ---------------------------------------------------------------------------
from staff_loans.views import StaffLoanApplicationViewSet  # noqa: E402
staff_loans_router = DefaultRouter()
staff_loans_router.register('staff-loans', StaffLoanApplicationViewSet, basename='staff-loan')
urlpatterns += staff_loans_router.urls

# ---------------------------------------------------------------------------
# Internal Audit — full audit-management module (spec: Internal Audit Module,
# GIAS Jan-2024). Phase 1: Findings register, Follow-up tracking, Dashboard.
# Independence gate: Internal Audit edits; CEO/COO/CFO/board view-only
# (internal_audit/access.py). Own DRF router (router.urls is snapshotted above).
# ---------------------------------------------------------------------------
from internal_audit.views import (  # noqa: E402
    EngagementViewSet, FindingViewSet, ManagementResponseViewSet,
    FollowUpViewSet, dashboard as internal_audit_dashboard,
)
internal_audit_router = _DRFRouter()
internal_audit_router.register(r'internal-audit/engagements', EngagementViewSet, basename='ia-engagement')
internal_audit_router.register(r'internal-audit/findings', FindingViewSet, basename='ia-finding')
internal_audit_router.register(r'internal-audit/responses', ManagementResponseViewSet, basename='ia-response')
internal_audit_router.register(r'internal-audit/followups', FollowUpViewSet, basename='ia-followup')
urlpatterns += internal_audit_router.urls
urlpatterns += [
    path('internal-audit/dashboard/', internal_audit_dashboard, name='v1-internal-audit-dashboard'),
]

urlpatterns += bonu_urls


# ---------------------------------------------------------------------------
# Supplier Payables Reconciliation — monthly per-supplier "who was paid, who
# was not, and why" board (CFO ask 2026-07-25, from Bharath's spec). Every
# unpaid or held bill must carry a reason code + written justification before
# the month can be finalised; escalation-worthy items must be escalated.
# Reads billing / payments / procurement only — moves no money, posts no GL.
# Own DRF router (the module-level router.urls is snapshotted above).
# ---------------------------------------------------------------------------
from supplier_recon.api_views import (  # noqa: E402
    EscalationViewSet, InvoiceReconItemViewSet, ReasonCodeViewSet,
    ReconSupplierProfileViewSet, SupplierReconLineViewSet,
    SupplierReconRunViewSet,
)
supplier_recon_router = _DRFRouter()
supplier_recon_router.register(r'supplier-recon/runs', SupplierReconRunViewSet,
                               basename='supplier-recon-run')
supplier_recon_router.register(r'supplier-recon/lines', SupplierReconLineViewSet,
                               basename='supplier-recon-line')
supplier_recon_router.register(r'supplier-recon/items', InvoiceReconItemViewSet,
                               basename='supplier-recon-item')
supplier_recon_router.register(r'supplier-recon/escalations', EscalationViewSet,
                               basename='supplier-recon-escalation')
supplier_recon_router.register(r'supplier-recon/reason-codes', ReasonCodeViewSet,
                               basename='supplier-recon-reason-code')
supplier_recon_router.register(r'supplier-recon/supplier-profiles',
                               ReconSupplierProfileViewSet,
                               basename='supplier-recon-profile')
urlpatterns += supplier_recon_router.urls

# Supplier-statement ingestion + statement-to-ledger matching (2026-08-24).
# Static paths carry an extra segment beyond the router's detail routes
# (lines/<pk>/, statements are a new prefix), so ordering is safe.
from supplier_recon.statement_views import (  # noqa: E402
    upload_statement as sr_upload_statement,
    list_statements as sr_list_statements,
    statement_detail as sr_statement_detail,
    rematch_statement as sr_rematch_statement,
)
urlpatterns += [
    path('supplier-recon/lines/<uuid:line_id>/statements/upload/',
         sr_upload_statement, name='v1-supplier-recon-statement-upload'),
    path('supplier-recon/lines/<uuid:line_id>/statements/',
         sr_list_statements, name='v1-supplier-recon-statement-list'),
    path('supplier-recon/statements/<uuid:statement_id>/match/',
         sr_rematch_statement, name='v1-supplier-recon-statement-match'),
    path('supplier-recon/statements/<uuid:statement_id>/',
         sr_statement_detail, name='v1-supplier-recon-statement-detail'),
]

# Bulk authorisation of clean payment packs (CFO 2026-08-04) — his signature,
# one press. CFO-only; each pack still goes through the single-approval path.
from taskboard.payment_bulk_views import payment_bulk_preview, payment_bulk_approve
urlpatterns += [
    path('payment-requests/bulk/preview/', payment_bulk_preview, name='v1-payment-bulk-preview'),
    path('payment-requests/bulk/approve/', payment_bulk_approve, name='v1-payment-bulk-approve'),
]

# FX Payment Planning (CFO 2026-08-24) — forward forecast + planning calendar for
# the USD/ZAR foreign payments captured on FNB. Read = finance-viewers, write =
# Finance + CFO. Specific paths precede the <uuid> ones.
from fx_planning import api_views as _fxp
urlpatterns += [
    path('fx-planning/calendar/',            _fxp.fx_calendar,        name='v1-fxp-calendar'),
    path('fx-planning/backtest/',            _fxp.fx_backtest,        name='v1-fxp-backtest'),
    path('fx-planning/payees/',              _fxp.fx_payees,          name='v1-fxp-payees'),
    path('fx-planning/payees/<uuid:pk>/',    _fxp.fx_payee_detail,    name='v1-fxp-payee'),
    path('fx-planning/history/',             _fxp.fx_history,         name='v1-fxp-history'),
    path('fx-planning/import/',              _fxp.fx_import,          name='v1-fxp-import'),
    path('fx-planning/rebuild/',             _fxp.fx_rebuild,         name='v1-fxp-rebuild'),
    path('fx-planning/planned/',             _fxp.fx_planned_create,  name='v1-fxp-planned-create'),
    path('fx-planning/planned/<uuid:pk>/',   _fxp.fx_planned_detail,  name='v1-fxp-planned'),
    path('fx-planning/planned/<uuid:pk>/raise/', _fxp.fx_planned_raise, name='v1-fxp-planned-raise'),
]

# Large Payment Authorisation (CFO 2026-09-11) — the "request large claim
# payment" button. Selects claim payments that have already been raised, signed
# off and loaded to FNB through the ordinary gated path, pulls the claim detail
# from the Graphite read-only replica with live progress, and puts one
# authorisation request on the CFO as a task. It creates no payment and amends
# none. Raise = finance approvers + CFO; decide = CFO alone.
from large_payments import api_views as _lp
urlpatterns += [
    path('large-payments/candidates/',          _lp.lp_candidates,   name='v1-lp-candidates'),
    path('large-payments/',                     _lp.lp_list_create,  name='v1-lp-list-create'),
    path('large-payments/<uuid:pk>/',           _lp.lp_detail,       name='v1-lp-detail'),
    path('large-payments/<uuid:pk>/retry/',     _lp.lp_retry_enrich, name='v1-lp-retry'),
    path('large-payments/<uuid:pk>/decide/',    _lp.lp_decide,       name='v1-lp-decide'),
]

# Large Payment Authorisation — Piece 2 (CFO 2026-09-12). Re-send the CEO's
# authorisation email after a failed or lost send. The CEO's own approve /
# refuse / ask buttons are NOT routed here: they are no-login magic links served
# by core.magic_action, because "Arun won't use omni".
urlpatterns += [
    path('large-payments/<uuid:pk>/resend/',    _lp.lp_resend,       name='v1-lp-resend'),
]

# ── GENRIC monthly reporting pack (B3, Bokani Makosha / Finance) ───────────
# ONE button: Generate GENRIC Pack. Produces the 13-report regulatory pack, the
# reinsurance submission with the GENRIC invoice, and the cancellations report.
# Today this is a full day of manual work.
#
# READS ONLY. Nothing here posts a journal, changes a GL mapping, or cancels a
# policy — the cancellations report recommends and a human decides.
from genric import api_views as _genric
urlpatterns += [
    path('genric/config/',                   _genric.genric_config,      name='v1-genric-config'),
    path('genric/generate/',                 _genric.genric_generate,    name='v1-genric-generate'),
    path('genric/runs/',                     _genric.genric_runs,        name='v1-genric-runs'),
    path('genric/runs/<uuid:pk>/',           _genric.genric_run_detail,  name='v1-genric-run-detail'),
    path('genric/runs/<uuid:pk>/xlsx/',      _genric.genric_export_xlsx, name='v1-genric-xlsx'),
    path('genric/runs/<uuid:pk>/invoice.pdf/',
         _genric.genric_export_invoice_pdf,  name='v1-genric-invoice-pdf'),
]

# ── Transformation Board (CFO 2026-09-20) ─────────────────────────────────
# "First AI Insurance Company in Botswana" — the living four-month board for
# the CEO, CFO, COO and the Chief Human Capital Officer.
#
# READS ONLY, except the CFO moving a step's progress. Nothing here posts a
# journal, touches payroll, or writes to Graphite. Access is a closed viewer
# list, not a title gate — see transformation/permissions.py.
from transformation import api_views as _tx
urlpatterns += [
    path('transformation/board/',    _tx.board,   name='v1-transformation-board'),
    path('transformation/history/',  _tx.history, name='v1-transformation-history'),
    path('transformation/initiatives/<str:code>/progress/',
         _tx.set_progress, name='v1-transformation-progress'),
    path('transformation/initiatives/<str:code>/assign/',
         _tx.assign, name='v1-transformation-assign'),
    path('transformation/people/', _tx.people, name='v1-transformation-people'),
]
