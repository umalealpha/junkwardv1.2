# Omni feature inventory

One row per feature, keeping only the facts a reviewer needs to find it and
know who may open it. Add rows as features land; do not restate policy.

| Feature | Where | Who may open it | Read/Write | Notes |
| --- | --- | --- | --- | --- |
| Time Doctor guard visibility | `GET /hris/api/leave-exceptions/` (Django: `hris.leave_exceptions_read_views.leave_exceptions_read`); frontend page `/hris/leave-exceptions` | HR / exec / superuser see everyone; a line or co-manager sees their direct reports (plus own row); every other employee sees only their own row | READ-ONLY | Reuses `hris.leave_excuse_service.build_day` verbatim. Passes the service's existing `status` through as `guard_state` — no new states. Thresholds are NOT read or written here; the deduction path (`process_leave_excuses`) is untouched. Added CFO 2026-09-18 (Easy PR E, "Time Doctor visibility"). |
