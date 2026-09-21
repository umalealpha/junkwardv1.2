---
name: alphahrsuit
description: The Alpha Direct Human Capital & Payroll suite in Omni — every screen, rule, job and file built 19/20-Sep-2026. Use when Prathap types /alphahrsuit, or asks about HR screens (contracts, probation, new joiners, leavers/offboarding, offer letters, HR Settings, My Sign-offs), Omni logins and the "no payroll no Omni" rule, the leaver Microsoft 365 switch-off, the Development Dialogue (All Employees) 9-grid, the Group Payroll Report, or the HR user guide and SOP. Carries the CFO's wording and approval rules, the verification recipes, and what is still open.
---

# /alphahrsuit — the Human Capital & Payroll suite

Everything below is LIVE in prod unless it says otherwise. Built 19–20 Sep 2026 across
PRs #1281–#1285, #1288, #1291, #1292, #1297, #1301, #1304.

## 🔴 Rules that override anything you infer from the code

1. **Say "management approval", never "the CFO approves"** for incentives, staff loans and
   leave encashment — in documents, emails and UI copy. He IS the first gate (see
   `feedback_cfo_first_gate_incentive_leavepay_loans`), but the documentation must not name him.
2. **No payroll, no Omni.** A first Microsoft sign-in by anyone not on payroll is REFUSED.
   This is a PROD ENV fact, not a code default: `/etc/alpha-finance/.env` carries
   `SSO_NEW_LOGIN_POLICY=enforce` (verified 20-Sep); git ships `report`. Check the env before quoting it. insurance.co.bw = UniCoin agents, never in
   Omni; the only exception is a UniCoin employee on payroll (e.g. Bakang Mhusiwa, whose login
   is @insurance.co.bw but payroll email is @alphadirect).
3. **Omni never moves money.** Approvals here are recorded decisions; cash leaves via the bank.
4. **Never delete a person** — close or archive. History must stay.
5. **HR runs its own settings** (Unami + Dorothy). Do not build an approval step in front of them.
6. **System emails never name the CFO as the enforcer** — say "the HR department".

## The screens (exact menu labels)

| Menu path | Route | What it does |
|---|---|---|
| People → Contracts | `/hris/contracts` | Register, reminders 2/4/6 months, renewal decision + "carry it through" (next contract + letter), probation decisions |
| People → New Joiners | `/hris/joiners` | 30-day checklist, documents (ID, bank letter, police clearance, NBFIRA for controllers), systems→IT, JD/policy/monthly-review sign-offs, "Start joiner pack" |
| People → Leavers | `/hris/offboarding` | 6 steps (resignation letter, IT doc, HC doc, HC + manager + Finance sign-off) — Omni REFUSES to archive until all six are done; access by seniority; Microsoft status |
| People → HR Settings | `/hris/settings` | HR team roles, reminder recipients/timing, lead times, controller/expat flags, systems per department, orphan-login classification |
| People → Talent Management → Development Dialogue (All Employees) | `/hris/talent-cockpit` | THE 9-grid + every dialogue. Filter bar, drag placement, sign-off lock. Siblings: `/hris/my-dialogue` (mine), `/hris/team-dialogues` (my team), `/hris/ninebox` (read-only) |
| People → Offer Letters | `/recruitment/offers` | Draft from an approved Authority to Recruit → send → accepted starts onboarding |
| My Work → My Sign-offs | `/my-signoffs` | Where every employee signs JD, policies, monthly review |
| Payroll → Group Payroll Report | `/payroll/group-report` | 6 companies, one month, versioned; MoM + YTD (FY Jul–Jun), trend, departments, people, UniCoin agent commissions |

## Code map

- `hris/contract_module.py`, `contract_views.py`, `contract_followup.py` — contracts, probation, renewals, digest
- `hris/lifecycle_models.py` — OffboardingCase/Step, EmployeeAcknowledgement, ProbationDecision, OfferLetter, RoleSystemRequirement, LoginClassification
- `hris/offboarding_service.py` (`archive_block_reason` is the archive gate), `offboarding_views.py`
- `hris/joiner_pack.py` (`start_joiner_pack`, `house_email`), `joiner_views.py`
- `hris/m365_offboarding.py` + `management/commands/disable_leaver_m365.py` — leaver Microsoft switch-off
- `hris/talent_cockpit_views.py` (`_payload`, `_filter_keys`, `_row_from_person`) + `frontend/public/talent-cockpit-app.html` (the whole cockpit UI, one file, iframed)
- `core/login_gate.py`, `core/azure_auth.py`, `core/management/commands/close_agent_logins.py`
- `licensing/management/commands/alert_new_m365_accounts.py`
- `payroll/group_report.py`, `group_report_extra.py`, `group_report_views.py`
- `payroll/archive_service.py` — terminate + archive (the gate lives here)
- Cron: `infra/cron/hr-contracts-and-group-payroll.cron` (registered in `infra/install-crons.sh`)

## What runs on its own

| When (local) | Job |
|---|---|
| Weekdays 07:00 | `send_contract_reminders --commit` (contracts + probation) |
| Weekdays 07:15 | `send_joiner_reminders --commit` |
| Monday 06:30 | `hr_monday_digest --commit` |
| :25 past the hour, 07:25–19:25 | `alert_new_m365_accounts --commit` → Dorothy + Unami |
| Daily 05:30 | `disable_leaver_m365 --commit` — prod env `M365_LEAVER_DISABLE=report` (verified 20-Sep); git ships `off`; `enforce` only after admin consent |
| 28th 05:00 | `generate_group_payroll_report --commit --notify` |

## Documents

- **User guide v1.0** — `Gods Eye/Omni-HR-Payroll-Suite-User-Guide-v1.pdf` (+ Word), source
  `Gods Eye/hr-suite-manual.html`. In Omni: People → Documents (PDF `1e8b45a5`, Word `f1c8d19d`).
  14 parts, designed cover. Emailed to Unami + Dorothy 20-Sep.
- **SOP AD-SOP-HC-001 v1.0** — same vault, `Gods Eye/HR-Staff-Lifecycle-and-Group-Payroll-SOP-v1.docx`.
- Rebuild the PDF: open the HTML in Playwright and `page.pdf({format:'A4', printBackground:true, margin:0})`.
  Do NOT run Ghostscript over it — /ebook wrecks the cover gradient.

## Open / waiting on someone

- 🔴 **Microsoft admin consent** for `User.EnableDisableAccount.All` + `User.RevokeSessions.All` on the app
  `omni-m365-license-sync`. Runbook: `Gods Eye/MANUS-grant-omni-m365-switch-off-2026-09-19.md`.
  When granted: set `M365_LEAVER_DISABLE=enforce` in `/etc/alpha-finance/.env`, redeploy, verify the 05:30 run.
- The 14-Oct fixed-term renewal waits for Dorothy's contract sheet.
- Duplicate stub employee rows "Unopa" and "Lorato" — HR to tidy.
- Group payroll figures are Omni's own; reconcile to source before quoting outside.

## Verification recipes (prove it, do not assume)

```bash
# what prod is actually serving
bash ~/.claude/bin/omni-ssm.sh --raw "cd /opt/alpha-finance && sudo docker compose --env-file /etc/alpha-finance/.env exec -T frontend sh -c 'grep -c cfbar public/talent-cockpit-app.html'"
# dry-run any HR job (never assume the cron works)
... exec -T backend python manage.py disable_leaver_m365        # no --commit = dry run
# eyes-on a screen (the qa account cannot see the cockpit or the notebook — say so rather than claiming)
bash ~/.claude/skills/prat-skill/e2e/eyes-on.sh "/hris/offboarding"
```

- Frontend tests: `frontend/node_modules/.bin/vitest run` (the cockpit ones drive the REAL html in jsdom;
  it needs `pretendToBeVisual: true`, and `let STATE` is NOT on `window`).
- Backend: `DB_ENGINE=sqlite SECRET_KEY=test "/Volumes/T7 Shield/omni-local/venv/bin/python" manage.py test hris payroll`.

## Traps this suite has already taught

- **The archive gate starts 19-Sep-2026** — tests that build a leaver from "yesterday" cross it at midnight
  (checklist H121). Pin literal dates on both sides of any dated rule.
- **A filter must never hide someone from their approver** — the cockpit fades people, never removes them.
- **`_row_from_person` strips derived keys** (`company`, `grade`, `manager`, `deptCanonical`); adding a key to the
  payload without stripping it lets metadata accrete into live appraisal data.
- **SSM caps a command at 97KB** — push big files as ~60KB base64 chunks, then `docker cp` INTO the backend
  container (a host `/tmp` path is invisible to `compose exec`).
- **Uploading a policy-category HR document creates signature requests for every employee** — file guides and SOPs
  as category `other`.

Related memory: `project_hr_lifecycle_plan_2026_09_19`, `project_group_payroll_report_2026_09_19`,
`project_dd_all_employees_2026_09_19`, `feedback_cfo_first_gate_incentive_leavepay_loans`.

---

## Installing this skill on the other machine

This is the shared copy. Each machine keeps its skills outside the repo, so after a pull:

```bash
# macOS / Linux
mkdir -p ~/.claude/skills/alphahrsuit && cp alphahrsuit/SKILL.md ~/.claude/skills/alphahrsuit/
```
```powershell
# Windows
New-Item -ItemType Directory -Force "$HOME\.claude\skills\alphahrsuit" | Out-Null
Copy-Item alphahrsuit\SKILL.md "$HOME\.claude\skills\alphahrsuit\"
```

Then `/alphahrsuit` works on that machine. The user guide itself is NOT in git — download it from
Omni (People → Documents) so there is one copy of it, or find it on the Mac Desktop under `Gods Eye`.
