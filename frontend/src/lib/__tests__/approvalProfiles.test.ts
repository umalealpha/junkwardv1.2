import { describe, expect, it } from 'vitest'
import {
  classifyApprovalClass,
  completionProfileFor,
  decisionButtonLabel,
  decisionPresetsFor,
} from '../approvalProfiles'
import type { UserProfile } from '../api'

const profile = (overrides: Partial<UserProfile>): UserProfile => ({
  id: '1', username: 'test', first_name: 'Test', last_name: 'User', email: 'test@example.com',
  role: 'operations_staff', role_display: 'Operations Staff', title: 'operations',
  title_display: 'Operations Staff', department: null, job_title: '', is_administrator: false,
  is_active: true, is_user_active: true, can_approve_journal_entries: false,
  can_create_journal_entries: false, can_administer_users: false, can_post_directly: false,
  created_at: '', updated_at: '', ...overrides,
})

describe('complete Omni approval profiles', () => {
  it('classifies the live executive, finance, and specialist vocabulary', () => {
    const cases: Array<[Partial<UserProfile>, string]> = [
      [{ job_title: 'Data Protection Officer', department: 'Compliance' }, 'dpo'],
      [{ job_title: 'BONU Accountant', department: 'Finance' }, 'bonu'],
      [{ title: 'finance_manager', title_display: 'Finance Manager' }, 'finance_manager'],
      [{ title: 'cfo', title_display: 'Chief Financial Officer' }, 'cfo'],
      [{ job_title: 'Chief Technology Officer', department: 'Information Technology' }, 'cto'],
      [{ title: 'coo', title_display: 'Chief Operating Officer' }, 'coo'],
      [{ title: 'operations', title_display: 'HR Manager', department: 'Human Capital' }, 'hr'],
      [{ title: 'accountant', title_display: 'Accountant' }, 'accountant'],
      [{ title: 'operations', title_display: 'Claims Manager', department: 'Claims' }, 'claims_manager'],
      [{ title: 'operations', title_display: 'Senior Claims Associate', department: 'Claims' }, 'claims_senior'],
      [{ title: 'operations', title_display: 'Junior Claims Associate', department: 'Claims' }, 'claims_junior'],
      [{ job_title: 'Underwriting Manager', department: 'Underwriting' }, 'underwriting_manager'],
      [{ job_title: 'Senior Underwriting Associate', department: 'Underwriting' }, 'underwriting_senior'],
      [{ job_title: 'Underwriting Agent', department: 'Underwriting' }, 'underwriting_agent'],
      [{ title: 'operations', title_display: 'Operations Manager' }, 'operations_manager'],
      [{ title: 'operations', title_display: 'Operations Staff' }, 'operations'],
      [{ job_title: 'Executive Assistant', department: 'Administration' }, 'administration'],
      [{ job_title: 'Senior Data Analyst', department: 'Data Analytics' }, 'data_analytics'],
      [{ title: 'auditor', title_display: 'Auditor (read-only)' }, 'audit_qc'],
      [{ job_title: 'Health Insurance Associate', department: 'Health Insurance' }, 'business_customer'],
      [{ title: 'system_api', title_display: 'System / API' }, 'system'],
    ]
    for (const [overrides, expected] of cases) {
      expect(classifyApprovalClass(profile(overrides))).toBe(expected)
    }
  })

  it('keeps payment release wording exclusive to the CFO payment context', () => {
    expect(decisionButtonLabel('cfo', 'payments')).toBe('Authorise payment')
    for (const role of ['dpo', 'hr', 'cto', 'claims_junior', 'underwriting_agent', 'audit_qc'] as const) {
      expect(decisionButtonLabel(role, 'payments')).not.toMatch(/payment|FNB|authorise/i)
    }
  })

  it('provides distinct completion buttons for each major family', () => {
    expect(completionProfileFor('dpo').primaryLabel).toBe('Save compliance decision')
    expect(completionProfileFor('bonu').primaryLabel).toBe('Save BONU action')
    expect(completionProfileFor('claims_manager').primaryLabel).toBe('Save claims decision')
    expect(completionProfileFor('underwriting_agent').primaryLabel).toBe('Save underwriting preparation')
    expect(completionProfileFor('hr').primaryLabel).toBe('Save HR decision')
    expect(completionProfileFor('cfo').primaryLabel).toBe('Authorise selected action')
    expect(completionProfileFor('general').primaryLabel).toBe('Mark task complete')
  })

  it('does not reuse the withdrawn 09:00 wording', () => {
    const texts = Object.keys(completionProfileFor('cfo').presets)
      .map(key => completionProfileFor('cfo').presets[Number(key)].text)
      .concat(decisionPresetsFor('cfo', 'payments').map(p => p.note || ''))
      .join(' ')
    expect(texts).not.toMatch(/09:00|09\.00|cut-off/i)
  })
})
