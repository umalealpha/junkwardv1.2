# Omni Onboarding Control Upgrade — Implementation Scope

This package converts the current immediate-create onboarding endpoint into a **maker-checker workflow**. A submission creates a pending onboarding request; a different authorised HR user must approve it before an Employee or HRISProfile is created or healed.

| Included control | Acceptance criterion |
|---|---|
| Approval gate | Submission returns a pending request and does not activate an employee. A different HR-authorised user must approve or reject it. |
| Retry safety | An identical retry returns the same pending request. A different payload for the same pending email is rejected as a conflict. Repeating an approval returns the same result without creating duplicates. |
| Duplicate-risk review | Exact email is the hard identity key. Existing name, phone, employee number, and similar email matches are shown as warnings to the approver. |
| Manager assignment | The request captures a line manager; approval sets HRISProfile.manager. |
| Leave routing | The request captures an optional default leave approver; otherwise the manager's active login is used. The approved HRIS profile stores this default and leave application routing honours it. |
| Auditability | Submit, approve, reject, and idempotent replay events write native Omni AuditLog entries with a correlation ID and no bank, tax, national-ID, or secret data. |
| Checklist continuity | Approval creates the existing onboarding checklist tasks with deterministic titles so retries cannot duplicate them. |
| Company isolation | Request creation and queue visibility respect the caller's allowed-company scope. |
| Compatibility | The existing `/hris/api/onboard-employee/` POST route remains the submission endpoint; new queue and decision routes are additive. |

Bulk spreadsheet onboarding, bank/tax/payroll master-data capture, and offboarding are intentionally excluded from this ZIP. They are separate high-risk workflows that should be designed and audited independently rather than bundled into one deployment.
