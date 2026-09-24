from django.urls import path

from . import (amendment_views, api_views, career_views, co_review_views,
               roster_flag_views,
               disciplinary_views,
               exec_signoff_actions, exec_signoff_views,
               feature_views, incentive_actions, incentive_views, late_notice_views,
               leave_actions,
               leave_admin,
               leave_exceptions_read_views,
               leave_excuse_views, letter_views, leave_encash_views,
               leave_reversal_views, leave_upload_views,
               manager_accountability_views, manager_feedback_actions, manager_return_views,
               onboarding_views,
               perf_monthly_views, performance_views, pip_views, talent_cockpit_views,
               talent_views, training_views, transfer_views, views)


app_name = 'hris'

urlpatterns = [
    # Manager Accountability — no-login answer page (token in ?t=). CFO 2026-07-25.
    # MUST live under 'api/': the reverse proxy only forwards /hris/api/* to
    # Django, so a bare /hris/... page is served by the Next.js frontend and
    # 404s. Bug d3caf386 (kbotana, 2026-07-30) — every manager's "Answer now"
    # link was dead, and they were escalated for the resulting silence.
    path('api/manager-accountability/answer/', manager_accountability_views.answer_page,
         name='manager_accountability_answer'),
    # Legacy path kept alive for notes already emailed before the fix.
    path('manager-accountability/answer/', manager_accountability_views.answer_page,
         name='manager_accountability_answer_legacy'),
    path('', views.hris_app, name='app'),
    # JSON endpoints used by the HRIS SPA (Phase 2: token-auth gated)
    path('api/employees/', api_views.employees, name='api-employees'),
    path('api/onboard-employee/', onboarding_views.onboard_employee,
         name='api-onboard-employee'),
    path('api/onboarding/options/', onboarding_views.onboarding_options,
         name='api-onboarding-options'),
    path('api/onboarding/queue/', onboarding_views.onboarding_queue,
         name='api-onboarding-queue'),
    path('api/onboarding/<uuid:request_id>/decide/', onboarding_views.decide_onboarding,
         name='api-onboarding-decide'),
    path('api/grades/',    api_views.grades,    name='api-grades'),
    # Unami TMS Orbit feature pack (CFO directive 2026-05-18).
    path('api/late-notice/',     late_notice_views.late_notice,  name='api-late-notice'),
    path('api/late-notice/pattern/', late_notice_views.late_notice_pattern, name='api-late-notice-pattern'),
    path('api/leave-balances/',  feature_views.leave_balances,    name='api-leave-balances'),
    path('api/leave-requests/',  feature_views.apply_leave,       name='api-leave-requests'),
    # Discretionary-leave question set — compassionate / study / special only
    # (CFO 2026-09-10). Drives the form; the same module validates the submit.
    path('api/leave-policy/',    feature_views.leave_policy_spec, name='api-leave-policy'),
    # Choose-your-manager picker + HR dashboard daily summary (CFO 2026-07-15).
    path('api/leave-managers/',  feature_views.leave_managers,      name='api-leave-managers'),
    path('api/leave-daily-summary/', feature_views.leave_daily_summary, name='api-leave-daily-summary'),
    # HR bulk-xlsx uploads (CFO 2026-06-16): leave opening balances (bug
    # 9c0aa7d3) + leave-approver assignment (bug 2cdd6333).
    path('api/leave-opening-balances/upload/',
         leave_upload_views.upload_leave_opening_balances, name='api-leave-opening-upload'),
    path('api/leave-approvers/upload/',
         leave_upload_views.upload_leave_approvers, name='api-leave-approver-upload'),
    path('api/assessments/',     feature_views.submit_assessment, name='api-assessments'),
    path('api/my-itw8/',         feature_views.my_itw8,           name='api-my-itw8'),
    path('api/bonus-simulate/',  feature_views.bonus_simulate,    name='api-bonus-simulate'),

    # CFO directive 2026-05-20 (Manus HRIS audit Part 3) — 5 new endpoints
    path('api/leave-requests/queue/',
         feature_views.leave_queue,         name='api-leave-queue'),
    path('api/leave-requests/<uuid:leave_id>/decide/',
         feature_views.decide_leave,        name='api-decide-leave'),
    # Controlled override of a locked maternity date (bug f4464440 req 3):
    # HR proposes; a DIFFERENT approver confirms before the date moves.
    path('api/leave-requests/<uuid:leave_id>/maternity-override/',
         __import__('hris.maternity_override_views', fromlist=['propose_override']).propose_override,
         name='api-maternity-override-propose'),
    path('api/maternity-overrides/<uuid:override_id>/decide/',
         __import__('hris.maternity_override_views', fromlist=['decide_override']).decide_override,
         name='api-maternity-override-decide'),
    # Employee self-cancel of their own still-pending request (Kago 2026-07-16).
    path('api/leave-requests/<uuid:leave_id>/cancel/',
         feature_views.cancel_leave,        name='api-cancel-leave'),
    # HR leave administration (Unami Butale 2026-07-27) — view the sick-leave
    # certificate, all approved / pending leave, department analytics, and the
    # HR dual-approval verification queue.
    path('api/leave-requests/<uuid:leave_id>/certificate/',
         leave_admin.leave_certificate,     name='api-leave-certificate'),
    path('api/leave-admin/all/',
         leave_admin.leave_admin_all,       name='api-leave-admin-all'),
    path('api/leave-admin/analytics/',
         leave_admin.leave_admin_analytics, name='api-leave-admin-analytics'),
    path('api/leave-admin/hr-queue/',
         leave_admin.leave_hr_queue,        name='api-leave-hr-queue'),
    path('api/leave-admin/<uuid:leave_id>/hr-verify/',
         leave_admin.hr_verify_leave,       name='api-leave-hr-verify'),
    # One-click leave approval from the notification email (CFO 2026-07-14).
    # Public (signed token = credential); under /hris/api/ so Caddy proxies it
    # to Django. GET = side-effect-free confirmation page; POST = the decision.
    # Apply for leave with NO sign-in (CFO 2026-08-07). Per-person signed token —
    # unlike the shared report-a-problem link, this files a request in a named
    # person's name and spends their balance.
    path('api/apply-leave/<str:token>/',
         __import__('hris.leave_apply_nologin', fromlist=['apply_leave']).apply_leave,
         name='apply-leave-nologin'),
    path('api/leave-action/<str:token>/',
         leave_actions.leave_action_page,   name='leave-action-page'),
    path('api/leave-action/<str:token>/submit/',
         leave_actions.leave_action_submit, name='leave-action-submit'),
    # CEO/CFO countersignature demanded by the long-overdue-task gate
    # (CFO 2026-08-07) — same login-free signed-token pattern as leave-action.
    path('api/exec-signoff/<str:token>/',
         exec_signoff_actions.exec_signoff_page,    name='exec-signoff-page'),
    path('api/exec-signoff/<str:token>/submit/',
         exec_signoff_actions.exec_signoff_submit,  name='exec-signoff-submit'),
    path('api/exec-signoffs/',
         exec_signoff_views.exec_signoff_list,      name='api-exec-signoffs'),
    path('api/exec-signoffs/<uuid:pk>/decide/',
         exec_signoff_views.exec_signoff_decide,    name='api-exec-signoff-decide'),
    # My own overdue work — read by the apply screens so a person is warned
    # BEFORE they fill the form in (CFO 2026-08-07).
    path('api/my-overdue-tasks/',
         exec_signoff_views.my_overdue_tasks,       name='api-my-overdue-tasks'),
    # One-click monthly performance feedback from the manager email
    # (CFO 2026-08-07) — Bharath could not reach the in-app screen at all.
    path('api/manager-feedback/<str:token>/',
         manager_feedback_actions.manager_feedback_page,   name='manager-feedback-page'),
    path('api/manager-feedback/<str:token>/submit/',
         manager_feedback_actions.manager_feedback_submit, name='manager-feedback-submit'),
    path('api/manager-feedback/<str:token>/not-mine/',
         manager_feedback_actions.manager_feedback_not_mine, name='manager-feedback-not-mine'),
    # One-click incentive approval from the email (CFO 2026-07-24) — same
    # login-free signed-token pattern as leave-action above.
    path('api/incentive-action/<str:token>/',
         incentive_actions.incentive_action_page,   name='incentive-action-page'),
    path('api/incentive-action/<str:token>/submit/',
         incentive_actions.incentive_action_submit, name='incentive-action-submit'),
    # Leave history + employee status (bug 3af04928) + persistent upload
    # status for the HR bulk-upload tiles (bug ecfc3f5a).
    path('api/leave-requests/mine/',
         feature_views.my_leave_requests,   name='api-leave-mine'),
    path('api/leave-requests/decisions/',
         feature_views.my_leave_decisions,  name='api-leave-decisions'),
    path('api/leave-uploads/status/',
         feature_views.leave_upload_status, name='api-leave-upload-status'),

    # Post-leave reversal (Ontlametse Mogomotsi, ref AD/HR/IA/2026/001).
    # The employee says how many days they actually worked; their manager decides.
    path('api/leave-requests/<uuid:leave_id>/reversals/',
         leave_reversal_views.create_reversal,   name='api-leave-reversal-create'),
    path('api/leave-reversals/mine/',
         leave_reversal_views.my_reversals,      name='api-leave-reversals-mine'),
    path('api/leave-reversals/pending/',
         leave_reversal_views.pending_reversals, name='api-leave-reversals-pending'),
    path('api/leave-reversals/<uuid:reversal_id>/decide/',
         leave_reversal_views.decide,            name='api-leave-reversal-decide'),
    path('api/leave-reversals/<uuid:reversal_id>/attachment/',
         leave_reversal_views.reversal_attachment,
         name='api-leave-reversal-attachment'),

    # HRIS-002 (Oprah Mogomotsi, 2026-06-10) — manager/HR leave report.
    path('api/leave-report/',
         feature_views.leave_report,        name='api-leave-report'),
    path('api/me/',
         feature_views.me_profile,          name='api-me-profile'),
    path('api/my-talent/',
         feature_views.my_talent,           name='api-my-talent'),
    path('api/my-payslips/',
         feature_views.my_payslips,         name='api-my-payslips'),
    path('api/my-disciplinary/',
         feature_views.my_disciplinary,     name='api-my-disciplinary'),
    # Natural justice (CFO directive 2026-08-11): the employee's own response to
    # a case about themselves. Subject-only, keyed on employee id.
    path('api/my-disciplinary/<uuid:case_id>/respond/',
         feature_views.my_disciplinary_respond, name='api-my-disciplinary-respond'),
    path('api/kudos/',
         feature_views.recognition_feed,    name='api-kudos'),
    path('api/calibrate-talent/',
         feature_views.calibrate_talent,    name='api-calibrate-talent'),

    # CFO directive 2026-05-20 (Manus HRIS audit Part 4) — wow-factor extensions
    path('api/inbox/',
         feature_views.manager_inbox,       name='api-manager-inbox'),
    path('api/onboarding/',
         feature_views.onboarding_journey,  name='api-onboarding'),

    # CFO directive 2026-05-26 (Unami: TMS Orbit talent-management parity).
    # 9-Box / Succession / IDP / AI-Readiness module.
    # Development Dialogue "Talent Cockpit" — editable performance-evaluation
    # system of record (CFO directive 2026-07-17). GET list + PUT bulk-save.
    path('api/talent/cockpit/',
         talent_cockpit_views.cockpit,      name='api-talent-cockpit'),
    # Copy current dialogue into a new period (archives old, kept on record).
    path('api/talent/new-period/',
         talent_cockpit_views.new_period,   name='api-talent-new-period'),
    # Open a new period for everyone in scope (year-end roll).
    path('api/talent/new-period-all/',
         talent_cockpit_views.new_period_all, name='api-talent-new-period-all'),
    # Sign off a dialogue (employee / manager / moderator); manager sign locks.
    path('api/talent/sign/',
         talent_cockpit_views.sign,         name='api-talent-sign'),
    # Explicit single-person removal (bulk save never deletes).
    path('api/talent/delete/',
         talent_cockpit_views.delete_person, name='api-talent-delete'),
    # Active employees the caller may START a dialogue for — the picker that
    # replaced "+ Add person" creating a blank "New team member" (board [DD]).
    path('api/talent/employees/',
         talent_cockpit_views.talent_employees, name='api-talent-employees'),
    # All periods on record for one person (read-only history).
    path('api/talent/history/',
         talent_cockpit_views.history,      name='api-talent-history'),
    # Self-service: a staff member sees ONLY their own dialogue.
    path('api/talent/my-dialogue/',
         talent_cockpit_views.my_dialogue,  name='api-talent-my-dialogue'),
    # A manager sees their team's dialogues (downward reporting chain, read-only).
    path('api/talent/team/',
         talent_cockpit_views.team_dialogues, name='api-talent-team'),
    # Live-review rebuild (board dd515fa8): section-by-section save that MERGES
    # (never wipes the other sections), the moderator's challenge-alongside layer,
    # and the one canonical nine-box list.
    path('api/talent/save-section/',
         talent_cockpit_views.save_section,   name='api-talent-save-section'),
    path('api/talent/moderator-challenge/',
         talent_cockpit_views.moderator_challenge, name='api-talent-moderator-challenge'),
    path('api/talent/nine-box-labels/',
         talent_cockpit_views.nine_box_labels, name='api-talent-nine-box-labels'),
    path('api/talent/nine-box/',
         talent_views.nine_box,             name='api-talent-nine-box'),
    path('api/talent/succession/',
         talent_views.succession,           name='api-talent-succession'),
    path('api/talent/succession/<uuid:incumbent_id>/nominees/',
         talent_views.succession_nominees,  name='api-talent-succession-nominees'),
    path('api/talent/idp/',
         talent_views.my_idp,               name='api-talent-my-idp'),
    path('api/talent/idp/<uuid:profile_id>/',
         talent_views.idp_for_profile,      name='api-talent-idp'),
    path('api/talent/ai-readiness/',
         talent_views.ai_readiness,         name='api-talent-ai-readiness'),

    # CFO directive 2026-07-13 — Career Tracks (promotion-readiness records,
    # triggered by M. Tlagae's development-path request to HR).
    path('api/talent/career-tracks/',
         career_views.career_tracks,        name='api-talent-career-tracks'),
    path('api/talent/career-tracks/milestones/<uuid:milestone_id>/',
         career_views.update_milestone,     name='api-talent-career-milestone'),

    # CFO directive 2026-07-13 — Staff Incentive Approval (manager submits →
    # CFO + HR dual signatures → Finance payroll queue).
    path('api/incentives/',
         incentive_views.incentives,        name='api-incentives'),
    path('api/incentives/push-to-payroll/',
         incentive_views.push_incentives_to_payroll, name='api-incentives-push-payroll'),
    path('api/incentives/employees/',
         incentive_views.incentive_employees, name='api-incentive-employees'),
    path('api/incentives/parse-upload/',
         incentive_views.parse_upload,      name='api-incentive-parse-upload'),
    path('api/incentives/<uuid:incentive_id>/approve/',
         incentive_views.approve,           name='api-incentive-approve'),
    path('api/incentives/<uuid:incentive_id>/reject/',
         incentive_views.reject,            name='api-incentive-reject'),
    path('api/incentives/<uuid:incentive_id>/mark-processed/',
         incentive_views.payroll_mark,      name='api-incentive-mark'),
    # Amend a still-pending request (fix a wrong amount) + recurring templates
    # (Bharath pays the same people monthly) (CFO 2026-07-22).
    path('api/incentives/recurring/',
         incentive_views.recurring,         name='api-incentive-recurring'),
    path('api/incentives/recurring/generate/',
         incentive_views.recurring_generate, name='api-incentive-recurring-generate'),
    path('api/incentives/recurring/<uuid:template_id>/',
         incentive_views.recurring_detail,  name='api-incentive-recurring-detail'),
    path('api/incentives/<uuid:incentive_id>/amend/',
         incentive_views.amend,             name='api-incentive-amend'),
    # CFO directive 2026-07-21 — Leave Encashment (basic ÷ 22 × days) + the
    # leave-pay provision register. HR/Finance raise → CFO signs → payroll.
    path('api/leave-encashment/',
         leave_encash_views.encashments,    name='api-leave-encashment'),
    path('api/leave-encashment/provision/',
         leave_encash_views.provision,      name='api-leave-encash-provision'),
    path('api/leave-encashment/<uuid:encashment_id>/approve/',
         leave_encash_views.approve,        name='api-leave-encash-approve'),
    path('api/leave-encashment/<uuid:encashment_id>/reject/',
         leave_encash_views.reject,         name='api-leave-encash-reject'),
    path('api/leave-encashment/<uuid:encashment_id>/mark-paid/',
         leave_encash_views.mark_paid,      name='api-leave-encash-paid'),
    # Standalone offboarding — HR raises a leaver's final settlement (66b1e7a3).
    path('api/leave-encashment/leaver-settlement/',
         leave_encash_views.leaver_settlement, name='api-leave-encash-leaver'),
    # CFO directive 2026-07-22 — Leave Excuse Response dashboard (Exec/HR only).
    # Read-only: low/no productive-hours people for a day + their explanations +
    # Aria's anonymised read. The auto-responder is the separate
    # process_leave_excuses command (gated by LEAVE_EXCUSE_AUTOSEND).
    path('api/leave-excuse/',
         leave_excuse_views.leave_excuse_dashboard, name='api-leave-excuse'),
    # READ-ONLY Time Doctor guard visibility for line managers + own row for
    # every employee (CFO 2026-09-18, Easy PR E). GET only; no thresholds
    # changed and no deduction is triggered from viewing.
    path('api/leave-exceptions/',
         leave_exceptions_read_views.leave_exceptions_read,
         name='api-leave-exceptions-read'),
    path('api/leave-excuse/decide/',
         leave_excuse_views.leave_excuse_decide, name='api-leave-excuse-decide'),
    # CFO directive 2026-07-22 — Disciplinary Action. Manager raises → HR (Unami)
    # reviews → CFO signs off on suspension / dismissal.
    path('api/disciplinary/',
         disciplinary_views.cases,          name='api-disciplinary'),
    path('api/disciplinary/<uuid:case_id>/issue-inquiry/',
         disciplinary_views.issue_inquiry,  name='api-disciplinary-inquiry'),
    path('api/disciplinary/<uuid:case_id>/record-response/',
         disciplinary_views.record_response_on_behalf,
         name='api-disciplinary-record-response'),
    path('api/disciplinary/<uuid:case_id>/hr-review/',
         disciplinary_views.hr_review,      name='api-disciplinary-hr'),
    path('api/disciplinary/<uuid:case_id>/cfo-signoff/',
         disciplinary_views.cfo_signoff,    name='api-disciplinary-cfo'),
    path('api/disciplinary/<uuid:case_id>/reject/',
         disciplinary_views.reject,         name='api-disciplinary-reject'),
    path('api/disciplinary/<uuid:case_id>/cfo-override/',
         disciplinary_views.cfo_override,   name='api-disciplinary-cfo-override'),
    # Evidence attachments (CFO 2026-07-22) — upload / delete / gated download.
    path('api/disciplinary/<uuid:case_id>/attach/',
         disciplinary_views.attach_evidence,   name='api-disciplinary-attach'),
    path('api/disciplinary/attachment/<uuid:attachment_id>/delete/',
         disciplinary_views.delete_evidence,   name='api-disciplinary-attach-delete'),
    path('api/disciplinary/attachment/<uuid:attachment_id>/download/',
         disciplinary_views.download_evidence, name='api-disciplinary-attach-download'),

    # CFO directive 2026-06-07 — HRIS dual-approval amendments (maker-checker).
    path('api/amendments/',
         amendment_views.submit,            name='api-amendment-submit'),
    path('api/amendments/pending/',
         amendment_views.pending,           name='api-amendments-pending'),
    path('api/amendments/<uuid:amendment_id>/approve/',
         amendment_views.approve,           name='api-amendment-approve'),
    path('api/amendments/<uuid:amendment_id>/reject/',
         amendment_views.reject,            name='api-amendment-reject'),
    path('api/amendments/<uuid:amendment_id>/reverse/',
         amendment_views.reverse,           name='api-amendment-reverse'),

    # Feature c0d110b6 (Oprah 2026-06-12) — inter-entity employee transfer
    # with two-sided (Transfer Out → Transfer In) approval.
    path('api/transfers/',
         transfer_views.transfers,          name='api-transfers'),
    path('api/transfers/settlement-preview/',
         transfer_views.settlement_preview, name='api-transfer-settlement-preview'),
    path('api/transfers/<uuid:transfer_id>/approve-out/',
         transfer_views.approve_out_view,   name='api-transfer-approve-out'),
    path('api/transfers/<uuid:transfer_id>/approve-in/',
         transfer_views.approve_in_view,    name='api-transfer-approve-in'),
    path('api/transfers/<uuid:transfer_id>/reject/',
         transfer_views.reject_view,        name='api-transfer-reject'),

    # Employment / HR letters — staff request a letter about themselves, their
    # manager (or HR) signs it off, omni issues a branded-letterhead PDF.
    # Oprah Mogomotsi document-bank request + CFO 2026-07-15.
    path('api/letters/',
         letter_views.letters,             name='api-letters'),
    path('api/letters/letterhead/',
         letter_views.letterhead_template, name='api-letters-letterhead'),
    path('api/letters/<uuid:letter_id>/decide/',
         letter_views.decide_letter,       name='api-letters-decide'),
    path('api/letters/<uuid:letter_id>/pdf/',
         letter_views.letter_pdf_view,     name='api-letters-pdf'),
    # PUBLIC (QR target — no login): verify an issued letter.
    path('api/letters/<uuid:letter_id>/verify/',
         letter_views.letter_verify,       name='api-letters-verify'),

    # ELRA-2025 monthly performance feedback + PIP (CFO 2026-06-25). DORMANT
    # behind settings.ELRA_PERF_ENABLED until the DPIA / counsel sign-off.
    path('api/performance/checkins/',
         performance_views.monthly_checkins,       name='api-perf-checkins'),
    path('api/performance/checkins/<uuid:pk>/',
         performance_views.monthly_checkin_detail, name='api-perf-checkin-detail'),
    path('api/performance/pips/',
         performance_views.pips,                   name='api-perf-pips'),
    path('api/performance/reportable/',
         performance_views.reportable_employees,   name='api-perf-reportable'),

    # Monthly manager-feedback layer (CFO 2026-07-20) — rides on MonthlyCheckIn.
    path('api/performance/monthly/',
         perf_monthly_views.monthly_feedback,      name='api-perf-monthly'),
    path('api/performance/monthly/league/',
         perf_monthly_views.manager_league,        name='api-perf-monthly-league'),
    # Who still owes feedback — read by the QC agent for Telegram nudges (idea #8).
    path('api/performance/monthly/owed/',
         perf_monthly_views.owed_feedback,         name='api-perf-monthly-owed'),
    path('api/performance/monthly/confirm-target/',
         perf_monthly_views.confirm_target,        name='api-perf-confirm-target'),
    path('api/performance/targets/',
         perf_monthly_views.targets,               name='api-perf-targets'),
    path('api/performance/targets/<uuid:pk>/',
         perf_monthly_views.target_detail,         name='api-perf-target-detail'),

    # Monthly Manager Return (CFO 2026-07-26) — the SHORT monthly accountability
    # return a people-manager files about their own team, upward to their own
    # manager. Task on the 1st -> submit by the 5th -> cleared by the 10th.
    path('api/manager-return/',
         manager_return_views.my_return,        name='api-manager-return'),
    path('api/manager-return/review/',
         manager_return_views.review_queue,     name='api-manager-return-review'),
    path('api/manager-return/league/',
         manager_return_views.league,           name='api-manager-return-league'),
    # The year's returns, condensed — feeds the Development Dialogue.
    path('api/manager-return/rollup/',
         manager_return_views.dialogue_rollup,  name='api-manager-return-rollup'),
    path('api/manager-return/<uuid:pk>/decide/',
         manager_return_views.review_decide,    name='api-manager-return-decide'),

    # Joint 50/50 monthly rating (CFO 2026-07-26, after Kago's roster question).
    # The senior accountants report to the CFO but the Finance Manager works with
    # them daily, so both rate them and the score is split.
    path('api/co-review/',
         co_review_views.my_ratings,            name='api-co-review'),
    path('api/co-review/rate/',
         co_review_views.rate,                  name='api-co-review-rate'),
    path('api/co-review/blend/',
         co_review_views.blend,                 name='api-co-review-blend'),

    # Roster flags (CFO 2026-07-26) — a manager says "not mine" / "resigned" in
    # one click; HR actions it. Raising hides them from the raiser's roster at
    # once, but the org chart only moves when HR acts.
    path('api/roster-flags/',
         roster_flag_views.flags,               name='api-roster-flags'),
    path('api/roster-flags/<uuid:pk>/decide/',
         roster_flag_views.decide,              name='api-roster-flags-decide'),

    # CFO-directed PIP (2026-07-15) — visible to the subject + a named audience,
    # independent of the dormant ELRA gate. Subject records their own explanation.
    path('api/pips/directed/',
         pip_views.directed_pips,                  name='api-pips-directed'),
    path('api/pips/directed/<uuid:pk>/explain/',
         pip_views.pip_explain,                    name='api-pips-explain'),

    # Induction & Training Academy (CFO 2026-09-20) — Dorothy's induction talk,
    # turned into a 30-minute self-service course + a 30-question exam with 15
    # different papers in circulation + a certificate. "Upload Here" lets HR
    # drop in any PDF/Word document and have the AI draft a course from it.
    path('api/training/courses/',
         training_views.courses,               name='api-training-courses'),
    path('api/training/upload/',
         training_views.upload_course,         name='api-training-upload'),
    path('api/training/verify/',
         training_views.verify_certificate,    name='api-training-verify'),
    path('api/training/attempts/<uuid:pk>/',
         training_views.attempt_paper,         name='api-training-attempt'),
    path('api/training/attempts/<uuid:pk>/submit/',
         training_views.submit_exam,           name='api-training-submit'),
    path('api/training/certificates/<uuid:pk>/pdf/',
         training_views.certificate_pdf,       name='api-training-certificate'),
    path('api/training/courses/<slug:slug>/',
         training_views.course_detail,         name='api-training-course'),
    path('api/training/courses/<slug:slug>/progress/',
         training_views.save_progress,         name='api-training-progress'),
    path('api/training/courses/<slug:slug>/start/',
         training_views.start_exam,            name='api-training-start'),
    path('api/training/courses/<slug:slug>/publish/',
         training_views.publish_course,        name='api-training-publish'),
    path('api/training/courses/<slug:slug>/assign/',
         training_views.assign_course,         name='api-training-assign'),
    path('api/training/courses/<slug:slug>/results/',
         training_views.course_results,        name='api-training-results'),
]
