# PLAN — CFO backend actions in Omni (no Chrome)

**From:** Windows seat (Claude Code) · **For:** Mac Mini "Prat" to implement · **Date:** 2026-08-12
**CFO ask (verbatim):** *"I should have full access to Omni, using Claude code from the backend, to do whatever I need. Currently I cannot close tasks, submit IT requests, or approve purchase orders. Claude code relies on Chrome for these actions, which I am not happy about. I want backend access with the CFO's authority to perform these tasks."*

---

## 1. The goal in one line
Let Claude Code perform Omni actions **from the backend (SSM → Django)** as the CFO — starting with **close a task, submit an IT request, approve a PO** — with **no Chrome, no browser, no magic-link click**, and a full audit trail. Extensible to "whatever I need" via the same pattern.

## 2. REUSE — do NOT build a new action engine
Two things already exist on `origin/main`. Build on them; do not re-invent.

1. **`core/magic_action.py`** (PR #635, live+proven on prod). It already has an **action registry** (`ACTIONS = {...}`) where each action is a pair: `describe(user, ctx)` (the confirm page) and `act(user, ctx)` (does the work and **re-checks the same in-app permission**). Today it holds `dd_sign` and `ping`. This is the vetted-action pattern we extend.
2. **Clean service-layer functions** the in-app UI already calls — each with its own permission + SoD checks:
   - Close task → `taskboard/services.py:291  complete_task(task, user, body, interaction_seconds)`
   - Approve PO → `procurement/services.py:410  cfo_approve(po, user)` (and `fm_approve` at :316; SoD is enforced inside — creator/submitter/FM can't be the CFO approver)
   - Submit IT request → the IT Help Desk model in `core` (precedent: CLI commands already exist — `core/management/commands/helpdesk_add_comment.py`, `helpdesk_pending_reminder.py`). Reuse the same create path the `/helpdesk` API uses.

## 3. The design — ONE registry, TWO front doors
Refactor the action handlers into a shared registry, then give it two entrances that call the **identical** `act()` logic:

- **Door A (exists):** the magic-link — email → Confirm page → POST. For staff.
- **Door B (new):** a Django **management command** Claude runs via SSM, as the CFO, with **no click**:

  ```
  python manage.py omni_do --actor cfo --action po_approve   --id 123 --note "…"
  python manage.py omni_do --actor cfo --action task_close    --id 456 --note "reviewed, approved all"
  python manage.py omni_do --actor cfo --action it_request    --subject "…" --body "…" --category hardware
  ```

  The command resolves `--actor cfo` to the CFO user, calls the **same** `act()` handler (which calls the **same** `complete_task` / `cfo_approve` / helpdesk-create service function the button calls), and writes one audit row. No business logic is duplicated → SoD and every validation stay intact. Chrome is gone.

**Why this and not "let Claude poke the ORM directly":** the service functions carry the guards (duplicate-payment gate on task-close, SoD on PO approval, the PAY-* controls). Going through them keeps every control the UI has. Raw ORM writes would bypass them — never do that.

## 4. Initial actions to register (the three the CFO named)
| Action key | Calls | Permission re-check (already in the service fn) |
|---|---|---|
| `task_close` | `taskboard.services.complete_task` | note ≥ MIN_NOTE_CHARS; refuses already-done; **duplicate-payment gate fires on payment tasks** |
| `po_approve` | `procurement.services.cfo_approve` | must be CFO; SoD (not creator/submitter/FM); status must be PENDING_CFO_APPROVAL |
| `it_request` | helpdesk create (same as `/helpdesk`) | normal create; stamps requester = CFO |

## 5. Safety rails — HARD, do not soften
- **These stay OFF this tool, full stop:** changing a user's access/permissions/roles, entering credentials/keys, and **deleting live data**. Not scriptable here — those need a human with authority.
- **Money:** approving a PO or completing a normal task in Omni is fine — **Omni approval ≠ money leaving** (money only leaves via the FNB app). **BUT** `complete_task` on a **payment_request** task is "the click that releases the payment" and runs the duplicate gate. So: `task_close` must **detect a payment_request task and refuse by default**, printing "payment task — use the payment flow, not omni_do". Money egress is never a side effect of this tool.
- **Audit:** every `omni_do` run writes a record — who (CFO), via (claude-backend), when, action, target id, result. Same trail as a UI action, plus the "via backend" marker so it's never invisible.
- **Actor is fixed to real users:** `--actor cfo` maps to the CFO's actual Omni user; the action is done AS that user, so all their normal gates apply. No superuser bypass.

## 6. Implementation steps (Mac)
1. In `core/magic_action.py`, split the handler registry so `act()` bodies are importable independently of the HTTP layer (they already are functions — just expose them).
2. Add handlers `task_close`, `po_approve`, `it_request` (each re-checks permission via its service fn, exactly like `dd_sign` does).
3. Add `core/management/commands/omni_do.py` — parses `--actor/--action/--id/--note/--subject/--body/--category`, resolves the actor user, dispatches to the shared registry, prints a clear OK/FAILED line, writes the audit row. Payment-task guard in `task_close`.
4. Tests (`core/tests/…`): one per action — a happy path AND a **revert-goes-red** negative (e.g. remove the SoD check → the PO test must fail; wrong-status task → refused; payment task → refused).
5. Wire the magic-link's same three actions too (optional, cheap) so staff email links and backend both run one code path.

## 7. Proof before "done" (non-negotiable)
- Run each command against a **safe target** on prod via SSM and show the audit row + resulting state change with your own eyes (not "it ran").
- **A test must fail without its fix** — revert the SoD/guard, watch it go red, restore.
- Do NOT write throwaway rows to live records; use a genuine pending item or a disposable one you own.
- Deploy path: normal Omni deploy (SSM → git pull as ubuntu → build backend → up -d; migrations auto-run). Backend-only change → the backend-only build is enough.

## 8. After it's live
Reply on MACHINE-TALK + tell the CFO in plain English: *"You can now close tasks, raise IT tickets and approve POs straight from Claude — no browser. Money still only moves through FNB."* Then extend the registry for the next action he needs — that's now a one-handler job.
