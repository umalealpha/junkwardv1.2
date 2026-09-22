import type { UserProfile } from '@/lib/api'

export type ApprovalClass =
  | 'dpo'
  | 'bonu'
  | 'cfo'
  | 'coo'
  | 'cto'
  | 'finance_manager'
  | 'accountant'
  | 'claims_manager'
  | 'claims_senior'
  | 'claims_junior'
  | 'underwriting_manager'
  | 'underwriting_senior'
  | 'underwriting_agent'
  | 'hr'
  | 'operations_manager'
  | 'operations'
  | 'administration'
  | 'data_analytics'
  | 'audit_qc'
  | 'business_customer'
  | 'executive_readonly'
  | 'system'
  | 'general'

export type ApprovalAction = 'approve' | 'reject'

export interface ApprovalPreset {
  key: string
  label: string
  action: ApprovalAction
  tone: 'yes' | 'no' | 'info'
  note?: string
}

export interface CompletionPreset {
  label: string
  text: string
}

export interface CompletionProfile {
  heading: string
  intro: string
  primaryLabel: string
  presets: CompletionPreset[]
}

const correction: CompletionPreset = {
  label: 'Return for correction',
  text: 'Returned for correction. Please address the outstanding issue and re-submit with the required evidence.',
}

const reviewInfo: CompletionPreset = {
  label: 'Request more information',
  text: 'More information is required before this action can be completed. Please add the owner, purpose, dates, and supporting documents.',
}

const profiles: Record<ApprovalClass, CompletionProfile> = {
  dpo: {
    heading: 'Complete compliance action',
    intro: 'Use a compliance decision. No payment, payroll, journal, or bank action is taken here.',
    primaryLabel: 'Save compliance decision',
    presets: [
      { label: 'Mark compliant', text: 'Reviewed — COMPLIANT. Evidence checked and saved to the compliance file.' },
      { label: 'Mark non-compliant', text: 'Reviewed — NON-COMPLIANT. Gaps identified; remediation is required before sign-off.' },
      { label: 'Request remediation', text: 'Remediation requested. The owner must address the listed data-protection gaps before re-submission.' },
      { label: 'Escalate breach', text: 'Potential data-protection incident escalated for formal breach assessment and response.' },
      correction,
    ],
  },
  bonu: {
    heading: 'Complete BONU ERP action',
    intro: 'Record a BONU member, customer, revenue, or lawyer-bill outcome. Payment release is not performed here.',
    primaryLabel: 'Save BONU action',
    presets: [
      { label: 'Record member/customer data', text: 'BONU member/customer data recorded and checked against the source document.' },
      { label: 'Record revenue', text: 'BONU revenue transaction recorded with the entity, customer, amount, and supporting reference.' },
      { label: 'Record lawyer bill', text: 'BONU lawyer bill recorded with the firm, matter, invoice, amount, and supporting document.' },
      { label: 'Submit for review', text: 'BONU record prepared and submitted for the appropriate review and approval.' },
      correction,
    ],
  },
  cfo: {
    heading: 'Complete CFO authorisation',
    intro: 'This action may authorise a controlled payment or approval. Confirm the final bank state before calling a payment settled.',
    primaryLabel: 'Authorise selected action',
    // The CFO's own one-tap completion presets, restored (CFO 2026-07-18, PR #398).
    // The withdrawn "09:00 cut-off" wording is intentionally removed from the
    // first preset; the rest are the buttons the CFO uses daily.
    presets: [
      { label: 'Approved — all', text: 'Approved — all payments in this batch are authorised. Please process without delay.' },
      { label: 'Approved except…', text: 'Approved all payments EXCEPT: (list them here). Process the rest; the exceptions stay on hold.' },
      { label: 'Questions — Teams meet', text: 'I have questions on these payments — let us meet. Set it up here: https://teams.microsoft.com/l/meeting/new?subject=Payment%20queries%20-%20CFO' },
      { label: 'Rejected — explain', text: 'Rejected — I need more explanation. Reply with payee, amount, purpose and supporting documents, then re-submit.' },
      { label: 'Approved if docs attached', text: 'Approved on condition the supporting documents are attached before the payment is released.' },
      { label: 'Hold — next run', text: 'On hold for cash-flow timing — include this in the next payment run and re-submit.' },
      { label: 'Duplicate / already paid?', text: 'This looks like a duplicate or already-paid item — check with Finance and confirm before re-submitting.' },
    ],
  },
  coo: {
    heading: 'Complete COO operations action',
    intro: 'Record an operational or supplier decision. Finance and bank authorisation remain subject to separate controls.',
    primaryLabel: 'Save COO decision',
    presets: [
      { label: 'Approve operational request', text: 'Operational request reviewed and approved within the documented authority and budget.' },
      { label: 'Approve supplier/PO', text: 'Supplier or purchase order reviewed and approved subject to Finance verification.' },
      { label: 'Confirm operational readiness', text: 'Operational readiness confirmed; owner, timing, risks, and fallback are recorded.' },
      { label: 'Escalate risk', text: 'Operational risk escalated for management review before proceeding.' },
      correction,
    ],
  },
  cto: {
    heading: 'Complete technology action',
    intro: 'Record a technical or access decision. No finance, payroll, journal, or bank action is taken here.',
    primaryLabel: 'Save technology decision',
    presets: [
      { label: 'Approve technical change', text: 'Technical change reviewed and approved subject to the documented test and rollback plan.' },
      { label: 'Approve access request', text: 'Access request reviewed and approved at the minimum required privilege.' },
      { label: 'Confirm deployment readiness', text: 'Deployment readiness confirmed; testing, rollback, monitoring, and owner are documented.' },
      { label: 'Escalate security issue', text: 'Security issue escalated for investigation and remediation before release.' },
      correction,
    ],
  },
  finance_manager: {
    heading: 'Complete Finance Manager review',
    intro: 'Review the accounting or payment control. The CFO remains the final payment authoriser where required.',
    primaryLabel: 'Save Finance Manager decision',
    presets: [
      { label: 'Approve to CFO', text: 'Finance Manager review complete. Approved to proceed to the CFO stage, subject to all controls.' },
      { label: 'Flag duplicate', text: 'Possible duplicate identified. Held for verification before any further approval.' },
      { label: 'Hold for documents', text: 'Held pending supporting documents and confirmation of the business purpose.' },
      correction,
      reviewInfo,
    ],
  },
  accountant: {
    heading: 'Complete accounting action',
    intro: 'Record the accounting outcome. Final payment and bank release remain outside this role.',
    primaryLabel: 'Save accounting action',
    presets: [
      { label: 'Submit for finance review', text: 'Prepared and submitted for Finance Manager review with supporting documents attached.' },
      { label: 'Reconciled', text: 'Reconciled to the source document and supporting evidence; exception status reviewed.' },
      { label: 'Flag exception', text: 'Accounting exception identified and held for Finance Manager review.' },
      { label: 'Attach documents', text: 'Supporting documents attached and the accounting record is ready for review.' },
      correction,
    ],
  },
  claims_manager: {
    heading: 'Complete Claims Manager review',
    intro: 'Record a claims decision. Payment release and bank authorisation remain separate Finance controls.',
    primaryLabel: 'Save claims decision',
    presets: [
      { label: 'Approve claim review', text: 'Claims review completed and approved within the documented claims authority.' },
      { label: 'Approve eligible PO variance', text: 'Eligible claims PO variance reviewed and approved within the server-enforced tier-1 limit.' },
      { label: 'Request claim evidence', text: 'Additional claim evidence requested before approval.' },
      { label: 'Escalate fraud/control issue', text: 'Claims fraud or control concern escalated for investigation.' },
      correction,
    ],
  },
  claims_senior: {
    heading: 'Complete senior claims review',
    intro: 'Record a senior claims review. Final payment and bank action are not available here.',
    primaryLabel: 'Save senior claims review',
    presets: [
      { label: 'Submit senior review', text: 'Senior claims review completed and submitted to the Claims Manager.' },
      { label: 'Request claim evidence', text: 'Additional claim evidence requested before the claims review can proceed.' },
      { label: 'Flag claims risk', text: 'Claims risk or control exception flagged for Claims Manager review.' },
      correction,
    ],
  },
  claims_junior: {
    heading: 'Complete claims preparation',
    intro: 'Prepare the claims file and submit it for senior review. Approval and payment controls are not available here.',
    primaryLabel: 'Save claims preparation',
    presets: [
      { label: 'Submit for senior review', text: 'Claims file prepared and submitted for senior claims review.' },
      { label: 'Request claimant documents', text: 'Claimant or supplier documents requested before the file can proceed.' },
      { label: 'Flag issue', text: 'Claims issue flagged for senior review.' },
      correction,
    ],
  },
  underwriting_manager: {
    heading: 'Complete Underwriting Manager review',
    intro: 'Record an underwriting decision. Payment, journal, payroll, and bank controls remain separate.',
    primaryLabel: 'Save underwriting decision',
    presets: [
      { label: 'Approve underwriting review', text: 'Underwriting review completed and approved within the documented authority.' },
      { label: 'Request underwriting evidence', text: 'Additional underwriting evidence requested before approval.' },
      { label: 'Escalate underwriting risk', text: 'Underwriting risk escalated for management review.' },
      correction,
    ],
  },
  underwriting_senior: {
    heading: 'Complete senior underwriting review',
    intro: 'Record a senior underwriting review. Final approval, payment, and bank actions remain separately controlled.',
    primaryLabel: 'Save senior underwriting review',
    presets: [
      { label: 'Submit senior review', text: 'Senior underwriting review completed and submitted to the Underwriting Manager.' },
      { label: 'Request underwriting documents', text: 'Additional underwriting documents requested before the file can proceed.' },
      { label: 'Flag underwriting risk', text: 'Underwriting risk or exception flagged for manager review.' },
      correction,
    ],
  },
  underwriting_agent: {
    heading: 'Complete underwriting preparation',
    intro: 'Prepare the underwriting file and submit it for senior review. Approval and payment controls are not available here.',
    primaryLabel: 'Save underwriting preparation',
    presets: [
      { label: 'Submit for underwriting review', text: 'Underwriting file prepared and submitted for senior review.' },
      { label: 'Request customer documents', text: 'Customer or broker documents requested before the file can proceed.' },
      { label: 'Flag risk', text: 'Underwriting risk flagged for senior review.' },
      correction,
    ],
  },
  hr: {
    heading: 'Complete HR action',
    intro: 'Record an employee, leave, or payroll-input decision. HR does not release payments or approve journals.',
    primaryLabel: 'Save HR decision',
    presets: [
      { label: 'Approve leave request', text: 'Leave request approved after checking the employee record, dates, and available balance.' },
      { label: 'Approve employee change', text: 'Employee change reviewed and approved; supporting HR documentation is filed.' },
      { label: 'Approve payroll input', text: 'Payroll input reviewed and approved for Finance processing; source documents are filed.' },
      { label: 'Request HR documents', text: 'Additional HR documentation requested before the employee action can be approved.' },
      correction,
    ],
  },
  operations_manager: {
    heading: 'Complete Operations Manager review',
    intro: 'Record an operational decision. Finance, payroll, journal, and final bank controls remain separate.',
    primaryLabel: 'Save operations decision',
    presets: [
      { label: 'Approve operational request', text: 'Operational request reviewed and approved within the documented authority.' },
      { label: 'Approve supplier/PO', text: 'Supplier or purchase order reviewed and approved subject to Finance verification.' },
      { label: 'Confirm readiness', text: 'Operational readiness confirmed; owner, timing, risk, and fallback are recorded.' },
      { label: 'Escalate risk', text: 'Operational risk escalated for management review before proceeding.' },
      correction,
    ],
  },
  operations: {
    heading: 'Complete operations task',
    intro: 'Prepare the operational work and submit it for the appropriate manager review.',
    primaryLabel: 'Save operations action',
    presets: [
      { label: 'Submit for manager review', text: 'Operational work prepared and submitted for manager review.' },
      { label: 'Confirm completed work', text: 'Operational work completed and evidence recorded for the owner.' },
      { label: 'Flag operational risk', text: 'Operational risk flagged for manager review.' },
      correction,
    ],
  },
  administration: {
    heading: 'Complete administration action',
    intro: 'Prepare the administrative request and submit it to the correct owner. No financial approval is granted here.',
    primaryLabel: 'Save administration action',
    presets: [
      { label: 'Submit admin request', text: 'Administrative request prepared and submitted to the correct owner.' },
      { label: 'Attach supporting document', text: 'Supporting administrative document attached to the request.' },
      { label: 'Request correction', text: 'Administrative request returned for correction or missing information.' },
      correction,
    ],
  },
  data_analytics: {
    heading: 'Complete data and analytics action',
    intro: 'Validate the data and report the result. No approval, payment, payroll, or journal posting is performed here.',
    primaryLabel: 'Save data review',
    presets: [
      { label: 'Validate report', text: 'Report validated against the source data and the result is recorded.' },
      { label: 'Request source correction', text: 'Source-data correction requested before the analysis can be relied upon.' },
      { label: 'Escalate data-quality issue', text: 'Data-quality issue escalated to the responsible owner.' },
      correction,
    ],
  },
  audit_qc: {
    heading: 'Complete audit or QC review',
    intro: 'Record the finding and evidence. Audit/QC does not approve, pay, post, or change financial records.',
    primaryLabel: 'Save QC finding',
    presets: [
      { label: 'Record finding', text: 'QC finding recorded with the evidence, impact, and recommended action.' },
      { label: 'Request evidence', text: 'Additional evidence requested before the control can be assessed.' },
      { label: 'Escalate control issue', text: 'Control issue escalated to the accountable owner.' },
      { label: 'Mark review complete', text: 'QC review completed; evidence and outstanding actions are recorded.' },
    ],
  },
  business_customer: {
    heading: 'Complete customer or policy action',
    intro: 'Record the customer or policy work and submit any financial consequence to Finance for controlled processing.',
    primaryLabel: 'Save customer action',
    presets: [
      { label: 'Submit customer request', text: 'Customer or policy request prepared and submitted for the appropriate review.' },
      { label: 'Request customer documents', text: 'Additional customer or policy documents requested.' },
      { label: 'Confirm service action', text: 'Customer service action completed and the supporting record is updated.' },
      correction,
    ],
  },
  executive_readonly: {
    heading: 'Complete executive review',
    intro: 'This profile is read-only. Record a note or escalation; no approval, payment, or status-changing action is available.',
    primaryLabel: 'Save executive note',
    presets: [
      { label: 'Record observation', text: 'Executive observation recorded for the accountable owner.' },
      { label: 'Request management review', text: 'Management review requested from the accountable owner.' },
      { label: 'Escalate risk', text: 'Risk escalated for accountable-owner review.' },
    ],
  },
  system: {
    heading: 'System action',
    intro: 'System/API profiles do not receive interactive approval controls.',
    primaryLabel: 'Record system result',
    presets: [],
  },
  general: {
    heading: 'Complete task',
    intro: 'Record what was done. Role-specific approval and payment actions are not available here.',
    primaryLabel: 'Mark task complete',
    presets: [
      { label: 'Done — completed', text: 'Task completed and the outcome has been recorded for the assigner.' },
      reviewInfo,
      { label: 'Escalate', text: 'Task escalated to the appropriate owner for review.' },
      correction,
    ],
  },
}

const words = (profile: UserProfile | null): string => {
  if (!profile) return ''
  return [profile.title, profile.title_display, profile.role, profile.role_display, profile.department, profile.job_title]
    .filter(Boolean).join(' ').toLowerCase()
}

export function classifyApprovalClass(profile: UserProfile | null): ApprovalClass {
  const text = words(profile)
  if (!text) return 'general'
  if (/(system api|system_api)/.test(text)) return 'system'
  if (/(data protection|\bdpo\b|compliance officer)/.test(text)) return 'dpo'
  if (/\bbonu\b/.test(text)) return 'bonu'
  if (/(chief financial officer|\bcfo\b|title cfo)/.test(text)) return 'cfo'
  if (/(chief operating officer|\bcoo\b)/.test(text)) return 'coo'
  if (/(chief technology officer|\bcto\b|information technology|software development|technical specialist)/.test(text)) return 'cto'
  if (/(finance manager|financial controller)/.test(text)) return 'finance_manager'
  if (/(human resources|human resource|human capital|\bhr manager\b)/.test(text)) return 'hr'
  if (/(claims manager)/.test(text)) return 'claims_manager'
  if (/(claims team leader|senior claims associate|senior_claims_associate)/.test(text)) return 'claims_senior'
  if (/(junior claims associate|claims intern|junior_claims_associate|claims_intern)/.test(text)) return 'claims_junior'
  if (/(underwriting manager)/.test(text)) return 'underwriting_manager'
  if (/(senior underwriting|senior_underwriting)/.test(text)) return 'underwriting_senior'
  if (/(underwriting agent|junior underwriting|underwriting intern|underwriting_agent)/.test(text)) return 'underwriting_agent'
  if (/(operations manager|technical manager: operations)/.test(text)) return 'operations_manager'
  if (/(senior accountant|\baccountant\b|bookkeeper|finance analyst|accounts assistant)/.test(text)) return 'accountant'
  if (/(data analyst|data analytics)/.test(text)) return 'data_analytics'
  if (/(auditor|quality control|\bqc\b)/.test(text)) return 'audit_qc'
  if (/(administration|admin|executive assistant|receptionist|messenger)/.test(text)) return 'administration'
  if (/(business development|sales|marketing|health insurance|customer|policy)/.test(text)) return 'business_customer'
  if (/(executive \(read-only\)|executive_readonly)/.test(text)) return 'executive_readonly'
  if (/(senior operations|operations staff|operations|project|special projects)/.test(text)) return 'operations'
  return 'general'
}

export function completionProfileFor(approvalClass: ApprovalClass): CompletionProfile {
  return profiles[approvalClass] || profiles.general
}

const streamLabel = (stream: string | undefined): string => {
  switch (stream) {
    case 'payments': return 'payment'
    case 'journal_entries': return 'journal entry'
    case 'leave': return 'leave'
    case 'refunds': return 'refund'
    case 'petty_cash': return 'petty-cash request'
    case 'po': return 'purchase order'
    case 'incentives': return 'incentive request'
    case 'staff_loans': return 'staff-loan request'
    case 'leave_encash': return 'leave-encashment request'
    default: return 'approval'
  }
}

export function decisionButtonLabel(approvalClass: ApprovalClass, stream?: string): string {
  if (approvalClass === 'dpo') return 'Review compliance'
  if (approvalClass === 'bonu') return 'Approve BONU record'
  if (approvalClass === 'accountant') return 'Submit for finance review'
  if (approvalClass === 'finance_manager') return stream === 'payments' ? 'Approve to CFO' : 'Complete Finance review'
  if (approvalClass === 'cfo') return stream === 'payments' ? 'Authorise payment' : 'Approve'
  if (approvalClass === 'coo') return 'Approve operational action'
  if (approvalClass === 'cto') return 'Approve technical action'
  if (approvalClass === 'claims_manager') return 'Approve claims review'
  if (approvalClass === 'claims_senior') return 'Submit senior claims review'
  if (approvalClass === 'underwriting_manager') return 'Approve underwriting review'
  if (approvalClass === 'underwriting_senior') return 'Submit senior underwriting review'
  if (approvalClass === 'hr') return stream === 'leave' ? 'Approve leave request' : 'Approve HR action'
  if (approvalClass === 'operations_manager') return 'Approve operational request'
  if (approvalClass === 'audit_qc') return 'Review control evidence'
  if (approvalClass === 'executive_readonly' || approvalClass === 'system') return 'View details'
  if (approvalClass === 'claims_junior') return 'Open claims file'
  if (approvalClass === 'underwriting_agent') return 'Open underwriting file'
  if (approvalClass === 'operations' || approvalClass === 'administration' || approvalClass === 'data_analytics' || approvalClass === 'business_customer') return 'Review request'
  return `Decide ${streamLabel(stream)}`
}

export function bulkButtonLabel(approvalClass: ApprovalClass): string {
  if (approvalClass === 'cfo') return 'Authorise selected'
  if (approvalClass === 'finance_manager') return 'Approve selected to CFO'
  if (approvalClass === 'dpo') return 'Review selected compliance items'
  if (approvalClass === 'bonu') return 'Approve selected BONU items'
  if (approvalClass === 'hr') return 'Approve selected HR items'
  if (approvalClass === 'cto') return 'Approve selected technical items'
  if (approvalClass === 'coo' || approvalClass === 'operations_manager') return 'Approve selected operational items'
  if (approvalClass === 'claims_manager') return 'Approve selected claims items'
  if (approvalClass === 'underwriting_manager') return 'Approve selected underwriting items'
  if (approvalClass === 'accountant') return 'Review selected accounting items'
  return 'Approve selected'
}

export function decisionPresetsFor(approvalClass: ApprovalClass, stream?: string): ApprovalPreset[] {
  const subject = streamLabel(stream)
  const approveLabel = decisionButtonLabel(approvalClass, stream)
  const capitalisedSubject = `${subject[0].toUpperCase()}${subject.slice(1)}`
  const approveNote = approvalClass === 'dpo'
    ? 'Compliance item reviewed. The appropriate compliance decision and supporting evidence are recorded.'
    : approvalClass === 'bonu'
      ? 'BONU record reviewed and approved. The member/customer, revenue, or lawyer-bill evidence is recorded.'
      : approvalClass === 'accountant'
        ? 'Accounting work reviewed and submitted for Finance Manager review with supporting documents.'
        : approvalClass === 'finance_manager'
          ? 'Finance Manager review complete. Approved to proceed to the next authorised stage, subject to all controls.'
          : approvalClass === 'cfo' && stream === 'payments'
            ? 'Payment authorised after review. This does not by itself confirm bank settlement.'
            : `${capitalisedSubject} approved after review.`
  // The approve preset performs a REAL approval (POSTs action:'approve'). Never
  // let its label read as a passive "Open file / View details / Review request":
  // if the role's button label is not itself an approve verb, use an explicit
  // one. Read-only and system profiles get NO approve preset at all (the matrix
  // promises them no approval button).
  const honestApprove = /\b(approve|authorise|sign|submit)/i.test(approveLabel) ? approveLabel : 'Approve / sign off'
  const canApprove = approvalClass !== 'executive_readonly' && approvalClass !== 'system'
  const presets: ApprovalPreset[] = []
  if (canApprove) {
    presets.push({ key: 'approve', label: honestApprove, action: 'approve', tone: 'yes', note: approveNote })
  }
  presets.push(
    { key: 'reject', label: `Return ${subject} for correction`, action: 'reject', tone: 'no', note: `Returned for correction. Please address the ${subject} issue and re-submit with the required evidence.` },
    { key: 'more_info', label: `Request more ${subject} information`, action: 'reject', tone: 'info', note: `More information is required before this ${subject} can be approved. Please add the owner, purpose, dates, and supporting documents, then re-submit.` },
    { key: 'hold', label: `Hold ${subject} for review`, action: 'reject', tone: 'info', note: `This ${subject} is on hold for further review. No final approval or payment settlement is confirmed.` },
    { key: 'duplicate', label: 'Possible duplicate — verify', action: 'reject', tone: 'info', note: 'This looks like a duplicate or an already-paid item — confirm with Finance, then re-submit if it is genuinely still owed.' },
  )
  return presets
}
