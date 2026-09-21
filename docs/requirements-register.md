# CFO requirements register

One row per requirement in the CFO's instruction files. This is the checked-in
control the CFO asked for on 18-Sep-2026 (files 1–3 of 3, "Why the master
instruction was missed — mandatory control").

## Why this file exists

On 17-Sep the CFO's master document was only partly delivered. On 18-Sep the
prevention rule was logged to MACHINE-TALK (PR #1218, titled "master feature
inventory") — **but that PR changed only MACHINE-TALK.md. The inventory itself
never landed.** The FNB / Broker Commission / UniCoin approval-chain /
payment-copy rows it described as "recorded honestly" existed nowhere in the
repo. `docs/feature-inventory.md` (added by #1222) is a different table — who
may open a feature — and holds one row. A control that is announced but not
checked in is exactly the failure it was meant to stop.

Root causes, in order:
1. **The requirement text lived outside the repo** (Word files on one machine),
   so the other machine and every later session could not compare work against it.
2. **Rows were closed on a merge or a PR description**, not on a live check.
3. **The announcement stood in for the artefact** — nobody read the file back.

## Rules (enforced by `core/tests/test_requirements_register.py`)

- Every row has an **owner** and an **acceptance test** (the thing that proves it).
- A row may say **DONE** only when **PR/commit**, **test evidence** and **live
  evidence** are all filled in. Otherwise it is TODO / BUILDING / PARTIAL /
  BLOCKED / DEFERRED, and *Reason if pending* says why.
- Session open: read this file before touching a row. Session close: write back
  what changed. Never flip a row to DONE from a code search or a PR title.

Status words: `TODO` · `BUILDING` · `PARTIAL` · `BLOCKED` · `DEFERRED` · `DONE`.

## Register

| ID | Requirement | File | Owner | PR / commit | Acceptance test | Test evidence | Live evidence | Status | Reason if pending |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| E-TDVIS | Time Doctor guard visibility for managers (read-only) | 1 Easy | Windows | #1222 | hris/tests/test_leave_exceptions_read_only.py | | | PARTIAL | Merged; live route check not yet recorded by owner |
| E-FNBWORD | FNB AC06 wording routes to exception committee | 1 Easy | Windows | #1220 | fnb test_reject_code_wording.py | | | PARTIAL | Merged; live check not yet recorded by owner |
| E-PAYCOPY | Payment Copy keeps "copied from" link; six CFO acceptance tests | 1 Easy | Windows | #1221 | payments/tests/test_payment_copy.py | | | PARTIAL | Merged; live check not yet recorded by owner |
| E-UCCHAIN | UniCoin agent commission → Omni approval chain (Bharath, then Legakwa/Pako/Keetile) → FNB | master 17-Sep | Windows | | approved commission lands in Omni for Bharath, then Legakwa OR Pako OR Keetile, then loads to FNB via UniCoin | | | TODO | Carried over from 17-Sep, not started per 18-Sep log |
| M-AMEND | HRIS Amendments: searchable keyboard-accessible employee picker; exact identity; role/company scope; prefill intact | 2 Medium | Mac | #1242 (d2637a4c) | search by name/email/number; duplicate names need exact pick; no cross-entity leak; prefill correct | employeeSearch.test.ts 5/5; test_entity_scope AmendmentPickerIdentityFieldsTest red-on-revert; tsc+eslint clean; CI green | Live on cc09ecff (zero-downtime release 18-Sep 23:11 CAT): employee list 106 rows all carry number, 103 carry email; served /hris/amendments bundle has the combobox (aria-activedescendant, same-name warning, Change employee) | DONE |  |
| M-ONBDEPT | Onboarding: canonical required Department dropdown; server rejects blank/invalid; no Operations fallback; dept in approval preview; no silent overwrite | 2 Medium | Mac | #1241 (81de0fcc) | Finance/Claims/Underwriting accepted; blank + invalid refused; exact-email reuse; independent approval | hris/tests/test_onboarding_department.py red-on-revert; CI green | Live on cc09ecff (zero-downtime release 18-Sep 23:11 CAT): options return the 15 departments; POST with blank dept → 400 "Choose a department."; "Operations Team" → 400 not approved; served /hris/onboard bundle has onboard-department + "No department" | DONE |  |
| M-PEOPLE | People / My HR consolidated; current route drives active highlight; sidebar + command palette grouped together | 2 Medium | Mac (Omni chat) | #1260 | deep links HRIS/payroll/recruitment/rooms/equity/adoption/forgiveness; role gates, mobile drawer, back/forward, company scope unchanged | navActive.test.ts 4/4 (2 red on old rule); Opus SHIP | Live on 8b29b5da (zero-downtime release 19-Sep 08:38 CAT; FY25 GWP 125148691.61 intact): served bundles carry "My HR" inside People, "People (HRIS)" gone | DONE | Eyes-on screenshots not possible from the sandbox |
| M-FNB | FNB: branch amendment after raise; part-settled split flag; advisory (not block) for valid-format unknown branch code | 2 Medium | Mac (backlog chat) | #1247 (86e96a5f); correction = #1214 | no payment permanently blocked; failed/unknown/part-settled stay actionable + audited; committee can still approve | fnb/test_part_paid_and_branch_note.py 12/12, 6 red on revert; fnb + taskboard FNB list 357 OK; CI green; Opus 5 SHIP | Live cc09ecff 23:11 CAT 18-Sep: branch_code_advisory("771234") flags, "281267" does not; advisory is note-only (never soft_reasons) | PARTIAL | Part-paid text cannot be shown live yet: prod holds 0 requests with more than one stamped FNB batch (payment_request stamp added 17-Sep). Close on the first real split request |
| M-CLAIMS | Claims/Graphite same-timestamp bridge / as-of note; supplier PEP / list-date / notes persistence after reproducing | 2 Medium | Mac | #1243 (b6f58902) + #1244 (71f03040) | same extraction timestamp visible; bridge ties; supplier fields survive save/reload | test_aml_registers 52/52, 5 of 11 new red on unfixed code; test_graphite_claims_bridge 9/9; CI green | Live on cc09ecff (zero-downtime release 18-Sep 23:11 CAT): claims-bridge 200, count 4,646 = 4,646 ties, paid P133,092,895.59 vs P163,536,081.35 (gap P30.44M, note shown, never forced); served /graphite-feeds bundle has the bridge; supplier list returns list_version/list_source (13 of 16 recent suppliers carry a version) | DONE | Paid-gap root cause is a separate follow-up |
| M-BANKREC | Bank reconciliation: match explanation, confidence, preview, explicit human confirmation; no JE / no reconcile from AI alone | 2 Medium | Mac (backlog chat) | #1248 (24ed360d) | dry-run mutates nothing; confirm required; approved match audit-logged; deterministic matcher still available | banking/test_match_dry_run_confidence.py 15/15, 10 red on the original endpoint; CI green; Opus 5 SHIP | Live cc09ecff 18-Sep 23:11: dry-run on a real unmatched line returned 200 {amount_equal:true, date_gap 0, confidence 90} and the line was unchanged; served bundle contains "I have checked this match"; Auto-Match untouched | DONE |  |
| L-DEPT | One canonical Employee.department across all modules; correct Kgosi Tebogo Mojela + dept variants after exact email/company confirmation | 3 Large | Mac | #1258 | read-only before/after; access/PO/SOD zero-diff; backfill idempotent, reversible, audited | hris.tests.test_fold_employee_departments 9/9 (red without fix); Opus SHIP | Live on 8b29b5da (zero-downtime release 19-Sep 08:38 CAT; FY25 GWP 125148691.61 intact): fold dry-run 83 to move / 2 unknown shown, then --commit moved 83 (audited), re-run moves 0. Kgosi Tebogo Mojela active/Veritas | DONE | 2 records left for a human: department '"' and 'Finance and Planning' |
| L-WCC | Workforce Command Center (monthly) | 3 Large | Mac | #1261 | aggregate salary by default; totals cross-check to source; no automatic employment/leave decision | hris.tests.test_command_center 10/10; Opus SHIP | Live on 8b29b5da (zero-downtime release 19-Sep 08:38 CAT; FY25 GWP 125148691.61 intact): GET /hris/command-center/ 200 — Aug 2026, 31 snapshot days, salary at risk P284,378 / 8 people | DONE | Late starts not stored in Omni (shown n/a); AI plausibility deferred |
| L-PULSE | Sunday CEO Workforce Pulse (To Arun Iyer, cc CFO/Arjun/Unami; preview to CFO first) | 3 Large | Mac | #1262 | recipients, HTML render, aggregate salary, timezone window, stale-data guard, no send on incomplete data | hris.tests.test_ceo_pulse 12/12; Opus findings fixed | Live on 8b29b5da (zero-downtime release 19-Sep 08:38 CAT; FY25 GWP 125148691.61 intact): --preview sent to CFO only; /etc/cron.d/ceo-pulse installed (0 16 * * 0); dry-run HELD: Time Doctor data for the week | DONE | Real CEO send stays HELD until Time Doctor API works again (down since 17-Sep) |
| L-BANKAI | Bank reconciliation learns from approved matches (suggest + explain only) | 3 Large | Mac | #1263 | no silent posting; confirm, audit/reversal, entity scope, duplicate protection, dry-run; AI vs deterministic compared | banking.test_match_memory 12/12 + M6 25/25; Opus findings fixed | Live on 8b29b5da (zero-downtime release 19-Sep 08:38 CAT; FY25 GWP 125148691.61 intact): migration banking 0008 applied; GET bank-statement-lines/<id>/suggestions/ 200 | DONE | Follow-up: chips also show 0% candidates — hide below 70% |
| L-AGENT | Omni AI action agent: payment approve/reject confirmed server-side by a short-lived intent that only the user's Confirm tap can complete (CFO 18-Sep: payments only) | 3 Large | Mac | #1259 | payment + task approve/reject, SOD/self-approval, wrong user, duplicate, stale record, audit, rollback; no live payment | core.tests.test_aria_action_intents 40/40 (red without fix); Opus findings fixed | Live on 8b29b5da (zero-downtime release 19-Sep 08:38 CAT; FY25 GWP 125148691.61 intact): POST /ai/aria/confirm-action/ refuses an unauthorised caller; served bundle carries the Confirm card | DONE | Payments only (CFO). A real Confirm tap by a finance approver not yet observed live |
| L-GOV | Release/model/QC governance: no Fable anywhere; cheap/local models; clean-checkout gate; QCBot after changes | 3 Large | Mac | | model names recorded per release; MACHINE-TALK entry; CFO go/no-go | | | BUILDING | Fable gate switched off for this batch on the CFO's written instruction |
