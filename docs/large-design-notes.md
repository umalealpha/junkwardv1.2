# Large items: design, data impact and rollback notes (18-Sep-2026)

CFO instruction file 3 of 3 asks for these notes before any Large code lands. Each Large item is its own release with its own go/no-go.
Draft: Gemini 2.5 Pro, reading the real code. Review and corrections: Claude Opus 5. No Fable 5.1 was used.

## Decisions the CFO took on 18-Sep (override the draft below)

- **L-PULSE:** sends Sunday 18:00 CAT. While Time Doctor data is stale the real send is **HELD**; the draft's "continue sending" is wrong. A preview goes to the CFO only. Recipients: To Arun Iyer (aiyer@); cc CFO, Arjun (arjuniyer@) and Unami (ubutale@).
- **L-WCC:** opens on the **last full month**. Correction to the draft: Omni does have tasks (`core.OmniTask`), so unfinished tasks are included. Time Doctor figures come from the stored `TimeDoctorDailySnapshot` rows; the API has refused Omni since 17-Sep. Late-start times are not stored, so that tile shows "not available" instead of a number. AI plausibility is deferred.
- **L-BANKAI:** learned history **never auto-matches**; a person always confirms through the existing dry-run → checkbox → Confirm flow. Correction to the draft: **no external AI**, because statement descriptions contain customer names (AD-POL-AI-GOV-001). "Learning" is a deterministic history store with templated explanations, compared side by side with the deterministic confidence.
- **L-AGENT** (not in the draft): payments only. The confirm token goes to the user's Confirm card and never to the model. `POST /api/v1/ai/aria/confirm-action/` is the only completion path.
- **L-DEPT** (not in the draft): the `fold_employee_departments` command. It is a dry run by default, idempotent, and audited with the old value, and it never guesses a blank or unknown value. Nothing that controls access, PO approvals or SOD reads these spellings.

---

Here are the design, data-impact, and rollback notes for the three CFO "Large" items, prepared before any code is written.

---

### 1. Workforce Command Center

A monthly dashboard for senior management providing a single view of workforce productivity, engagement, and risk signals.

#### DESIGN NOTES

*   **New Files:**
    *   `hris/command_center_views.py`: New API view `CommandCenterView` to aggregate and serve the monthly data.
    *   `hris/command_center_services.py`: New functions to compute monthly aggregates (e.g., `get_monthly_late_starts`, `get_salary_at_risk_estimate`).
    *   `frontend/pages/workforce/command-center.tsx`: New Next.js page for the dashboard.
    *   `frontend/components/workforce/CommandCenter/...(widgets).tsx`: New React components for each dashboard metric (e.g., `LateStartsWidget`, `FlightRiskWidget`).

*   **Existing Files/Functions to REUSE:**
    *   `hris.exceptions_report.late_days_by_user()`: To get per-user late day counts.
    *   `hris.exceptions_report.LATE_START_AFTER`: As the official definition of a late start (08:15).
    *   `hris.workforce_pulse.shortfall_board()`: To identify individuals with Time Doctor shortfalls.
    *   `hris.flight_risk_views.compute_flight_risk()`: To power the flight-risk register widget.
    *   Models: `hris.models.WorkdayJustification`, `hris.models.Recognition`, `payroll.Employee`, `integrations.models.TimeDoctorDailySnapshot`.

*   **Data Sources:**
    *   **Time Doctor:** `integrations.models.TimeDoctorDailySnapshot` using the last good data from 16-Sep. A prominent banner will state: "Time Doctor data is stale as of 16-Sep. Metrics are HELD pending API restoration."
    *   **HRIS:** `WorkdayJustification` (for excuses), `Recognition` (for kudos).
    *   **Payroll:** `payroll.Employee` (for roster, salary data).
    *   **Flight Risk:** Live calculation via `hris.flight_risk_views.compute_flight_risk`.
    *   **Tasks:** Unfinished tasks require a **new** data source; no task model exists in the provided code. This widget will be deferred until a task management module is implemented.

*   **Deterministic Metric Definitions:**
    *   **Late Starts:** Count of unique employees with at least one start time after `08:15` local time during the month.
    *   **Late Minutes:** Sum of minutes past `08:15` for all late starts in the month.
    *   **Time Doctor Shortfalls:** Sum of `gap_h` from `shortfall_board` for all days in the month.
    *   **No Tracking Days:** Sum of unexplained "did not track" instances from daily exception data for the month.
    *   **Excuse Categories:**
        *   Most-used: `GROUP BY reason, COUNT(*)` on `WorkdayJustification` for the month.
        *   Unresolved: `COUNT(*)` where `responded_at IS NULL`.
    *   **Manager Action SLA:** Average of `(responded_at - created_at)` in business days for all `WorkdayJustification` records created and resolved in the month.
    *   **Salary-at-Risk Estimate (Monthly):** `(Total "No Tracking" Days / Total Workdays in Month) * Sum of Monthly Salaries of "No Tracking" Staff` + `(Total Shortfall Hours / Total Required Hours) * Sum of Monthly Salaries of Staff with Shortfalls`.
    *   **Flight-Risk Register:** Count of employees in "high", "medium", and "low" bands, from `compute_flight_risk`.

*   **Permissions:**
    *   Access to the dashboard page requires the `hris.view_workforce_command_center` permission.
    *   All salary data is aggregate by default. Viewing the named list of employees in the "Salary-at-Risk" calculation requires the `payroll.view_employee_salary` permission.

*   **AI Usage:**
    *   **AI May:** Classify the free-text `justification` field in `WorkdayJustification` for "Plausibility" (e.g., High, Medium, Low) and generate a 1-2 sentence summary of key trends for the month. All calls will use the internal `reasoning_complete` function.
    *   **AI May Not:** Calculate any primary metric, make decisions, or determine risk scores. The dashboard must remain fully functional if the AI service is unavailable; plausibility scores would be shown as "N/A".

#### DATA-IMPACT

*   **Tables Written:** None. The command center is a read-only dashboard.
*   **Migrations:** No.

#### ROLLBACK

1.  Revert the feature's pull request.
2.  Remove the navigation link to `/workforce/command-center` from the main UI layout.
3.  The feature is self-contained in new files, so rollback carries no risk to other parts of the application.

#### TESTING

| CFO Acceptance Test | Test Case |
| :--- | :--- |
| Monthly dashboard with metrics | 1. Verify all widgets load and display data (or a zero/empty state). 2. Check that the Time Doctor "stale data" banner is present and metrics are HELD. |
| Deterministic numbers | 3. Using a fixed database snapshot, confirm that the calculated metrics match a manual spreadsheet calculation. |
| Salary aggregate by default | 4. Log in as a user with only `hris.view_workforce_command_center`. Verify salary-at-risk is a single number. 5. Log in as a user with `payroll.view_employee_salary`. Verify the ability to see a named breakdown. |
| AI only classifies/explains | 6. Verify the "Plausibility" column for excuses is populated by the AI. 7. Disable the AI service and reload the page. Verify the dashboard loads correctly, with "Plausibility" showing "N/A". |
| Cross-check totals | 8. Compare the flight-risk count on the dashboard to the dedicated Flight-Risk Radar page for the same employee scope. |

#### OPEN QUESTIONS FOR CFO

1.  **Unfinished Tasks:** The concept of "unfinished tasks" is mentioned, but Omni does not currently have a task management system. Should we build this component first, or launch the dashboard without this widget?
    *   **Recommendation:** Launch without it. Add the widget later to deliver value sooner. (Yes/No)
2.  **Historical Trends:** The dashboard will initially show the current month's data. Is a comparison to the prior month ("MoM trend") a requirement for the first release?
    *   **Recommendation:** Yes. It provides critical context and is achievable using the `TimeDoctorDailySnapshot` history. (Yes/No)
3.  **Default Date Range:** Should the dashboard default to the current, incomplete month, or the last completed calendar month?
    *   **Recommendation:** Last completed month. This provides a stable, complete dataset for review. (Yes/No)

---

### 2. Sunday CEO Workforce Pulse

A weekly, automated email summary of key workforce metrics, sent to the executive team.

#### DESIGN NOTES

*   **New Files:**
    *   `hris/management/commands/send_ceo_pulse.py`: A new Django management command to be run by cron every Sunday.
    *   `hris/ceo_pulse.py`: New module containing the business logic to gather and structure the pulse data.
    *   `templates/hris/emails/ceo_pulse.html`: A new Django template for the branded, mobile-first HTML email.

*   **Existing Files/Functions to REUSE:**
    *   `hris.workforce_pulse.team_leaderboard()`: For the department performance league table.
    *   `hris.flight_risk_views.compute_flight_risk()`: To identify new high-risk employees.
    *   `hris.exceptions_report._unexplained_by_manager()`: To highlight managers with unexplained absences.
    *   All data aggregation logic developed for the **Workforce Command Center** will be reused to calculate weekly totals.

*   **Data Sources:**
    *   The same sources as the Workforce Command Center, but data will be filtered for the preceding week (e.g., Monday 00:00 to Saturday 23:59, Botswana time).
    *   **Time Doctor:** The pulse will use the last good week of data (week ending 16-Sep) and carry the "stale data" banner.

*   **Email Content & Logic:**
    *   **Recipients:** The recipient list will be hard-coded inside `send_ceo_pulse.py` as per the CFO's directive: `To: Arun Iyer`, `Cc: CFO, Arjun, Unami`. A safeguard function will check this list before sending.
    *   **Preview:** The command will accept a `--preview` flag, which overrides the recipient list and sends the email only to the CFO's address.
    *   **KPI Cards:** Weekly aggregate figures for Productive Hours, Late Starts, and Salary-at-Risk.
    *   **Circuit Breaker:** The command will abort and not send an email if key data sources (like Time Doctor snapshots for the period) are missing or return empty, preventing a blank or misleading report. This fulfills the "no send on incomplete data" requirement.

*   **Permissions:** Not user-facing. The command is run by a system process.

*   **AI Usage:**
    *   **AI May:** Generate the "CEO Question of the Week" by analyzing the final, deterministic KPI data for the week. The prompt to `reasoning_complete` will include the week's metrics and ask for one insightful question. Example: "Sales department productivity fell 10% while their shortfall hours doubled. Should we investigate?"
    *   **AI May Not:** Calculate any metrics or determine the email recipients. If the AI call fails, the question section of the email will be omitted.

#### DATA-IMPACT

*   **Tables Written:** None. This process reads data and sends an email.
*   **Migrations:** No.

#### ROLLBACK

1.  Disable the cron job that runs the `send_ceo_pulse` command.
2.  Revert the pull request containing the new files.
3.  This is a non-interactive, outbound-only feature, making rollback extremely low-risk.

#### TESTING

| CFO Acceptance Test | Test Case |
| :--- | :--- |
| Test recipient list | 1. Run `manage.py send_ceo_pulse --dry-run`. Verify logs show the correct To/CC list. 2. Run with `--preview`. Verify the email is sent only to the CFO. |
| Test HTML rendering | 3. Send a test email and verify it renders correctly on both desktop (Outlook) and mobile (iOS Mail, Gmail) clients. Check header/title colors match house style. |
| Test aggregate salary default | 4. Inspect the sent email's content. Verify "Salary-at-Risk" is a single aggregate figure. |
| Test timezone/date window | 5. Execute the command on a Sunday. Verify the data queries use a date range from the previous Monday 00:00 to Saturday 23:59, respecting the `LOCAL_OFFSET`. |
| Test empty/stale data guard | 6. Run the command for a future week where no data exists. Verify the command aborts and sends no email. 7. Verify the current stale TD data results in the "data stale as of 16-Sep" banner in the email. |

#### OPEN QUESTIONS FOR CFO

1.  **"Actions" Metric:** The requirement lists "actions". Can you clarify what this metric should represent? My proposal is to report the "Number of manager reviews of excuses completed this week". Is this correct?
    *   **Recommendation:** Yes. This is a clear, actionable metric derived from existing data (`WorkdayJustification.responded_at`). (Yes/No)
2.  **Sending Time:** What time on Sunday should the pulse be sent?
    *   **Recommendation:** 18:00 Botswana time. This allows for the full week's data to settle while still arriving ahead of the Monday morning start. (Yes/No)
3.  **Frequency on Stale Data:** Should we continue sending the pulse with stale Time Doctor data each week, or pause it until the API is restored?
    *   **Recommendation:** Continue sending. Other metrics (flight risk, excuses) remain valuable, and the stale banner provides clear context. (Yes/No)

---

### 3. AI-assisted Bank Reconciliation

Enhance the existing bank reconciliation tool by adding an AI-powered suggestion engine that learns from user confirmations.

#### DESIGN NOTES

*   **New Files:**
    *   `banking/models.py`: A new model, `BankMatchRule`, with fields like `company`, `text_pattern` (from statement description), `matched_entity_type` (Payment/JE), `confidence_score`.
    *   `banking/ai_matching.py`: New service module containing a function `suggest_best_match(statement_line)`.

*   **Existing Files/Functions to REUSE:**
    *   `banking.api_views.BankStatementLineViewSet`: A new `@action`, `suggest_match`, will be added. The existing `match` action will be used for final, user-driven confirmation.
    *   `banking.api_views.compute_match_confidence()`: The deterministic score will be displayed alongside the AI suggestion to provide a baseline comparison.
    *   `core.models.AuditLog`: To log when a match is made, with a new metadata field indicating it was AI-suggested.

*   **Logic Flow:**
    1.  On the reconciliation screen, an unmatched line has a new "Suggest with AI" button.
    2.  Clicking it calls the new `suggest_match` endpoint.
    3.  The backend `suggest_best_match` function executes a waterfall logic:
        a. **Rule-based:** First, it searches `BankMatchRule` for high-confidence rules matching the statement line's text. If found, it returns that suggestion.
        b. **AI-based:** If no rule matches, it queries for candidate Payments/JEs (similar amount, recent date) and passes them with the statement line details to the `reasoning_complete` function. The AI is prompted to return the best match ID and an explanation.
    4.  The frontend displays the suggestion, explanation, and both the AI and deterministic confidence scores.
    5.  The user confirms by clicking "Match", which calls the existing, unmodified `match` endpoint.
    6.  On successful confirmation of an AI-found match, a new `BankMatchRule` is created in the background, enabling the system to "learn".

*   **Permissions:** Existing financial permissions on the `BankAccountViewSet` (e.g., `CanViewFinancials`) are sufficient.

*   **AI Usage:**
    *   **AI May:** Suggest a match from a pre-filtered list of candidates and provide a natural language explanation for its choice.
    *   **AI May Not:** Confirm a match, post a transaction, or create a journal entry. Its function is strictly advisory. The reconciliation screen must remain fully operable if the AI service fails.

#### DATA-IMPACT

*   **Tables Written:**
    *   `banking.BankMatchRule` (New Table): Records are created/updated when a user confirms a match, forming the system's "memory".
    *   `banking.BankStatementLine` (Existing): Updated upon user confirmation (existing behavior).
*   **Migrations:** Yes, a migration is required to create the `banking.BankMatchRule` table.

#### ROLLBACK

1.  Execute the reverse Django migration to drop the `BankMatchRule` table.
2.  Revert the feature's pull request to remove the new endpoint and UI button.
3.  Human-confirmed matches made via AI suggestions will persist, as they are valid, audited actions. The core reconciliation functionality will be unaffected.

#### TESTING

| CFO Acceptance Test | Test Case |
| :--- | :--- |
| Learning from approved matches | 1. Match statement line "Payment from ACME" to "Payment #101". 2. On a new line "Payment from ACME Inc", click "Suggest". Verify it suggests a similar ACME payment based on the new rule, not the LLM. |
| AI suggests and explains | 2. For a line with no rule, click "Suggest". Verify the UI shows a candidate match with an AI-generated explanation and confidence score. |
| No silent posting / explicit confirmation | 3. Verify that clicking "Suggest with AI" does not change any data. 4. Verify only clicking the final "Confirm Match" button updates the `BankStatementLine` and writes to the `AuditLog`. |
| Compare AI vs deterministic result | 5. For an AI-suggested pair, verify the UI displays both the AI's confidence and the score from `compute_match_confidence` side-by-side. |
| Dry-run, entity scope, duplicate protection | 6. Confirm the existing `dry_run` functionality works with AI-suggested pairs. 7. Test that suggestions never cross company/entity boundaries. 8. Verify a matched payment no longer appears as a candidate. |

#### OPEN QUESTIONS FOR CFO

1.  **Rule Automation:** When the system "learns" a new rule from a user's confirmation, should this rule be applied automatically in the future for 100% confident matches, or should it always remain a suggestion requiring human confirmation?
    *   **Recommendation:** Always require human confirmation. This maintains the "no silent posting" principle. (Yes/No)
2.  **Initial Rule Seeding:** The AI will learn over time. Would you like the finance team to pre-populate the `BankMatchRule` table with a list of common, known transaction patterns (e.g., "SALARY", "BURS") to make the system effective from day one?
    *   **Recommendation:** Yes. This will significantly improve initial adoption and usefulness. (Yes/No)
3.  **Feedback Mechanism:** If the AI suggests a poor match, should there be a "Wrong Suggestion" button for the user to provide feedback, helping to refine the model's prompts over time?
    *   **Recommendation:** Yes. A simple thumbs-down feedback loop is low-effort to build and provides valuable data for future improvements. (Yes/No)