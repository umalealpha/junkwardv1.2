---
id: deploy-traps
title: "Deploy + dev traps and guardrails"
category: reference
status: active
tags: [deploy, traps, ci]
created: "2026-08-22T21:32:23"
updated: "2026-08-22T21:32:23"
---

<!-- compiled_truth -->
- **Windows sqlite test suite DIES on migrations** (Postgres-only SQL) — rely on CI + isolation proofs, not the local Windows full suite.
- **A test must fail without its fix** (red-first) — revert and watch it go red.
- **Verify the served bundle / running container, not git HEAD.** Code-only deploys are NOT auto-rebuilt.
- **An open PR / stale worktree is NOT pending work** (H75) — diff against current origin/main before "re-fixing".
- **Never accuse staff on unverified automated matching** (G15) — the FNB orphan script falsely flagged loaded payments.
- **"Field exists but never populated"** family (settled_at, account_name on 0/99, etc.) reads as "nothing happened".
- **DRF tokens on Windows are purged fast** — mint and use in the same breath. omni@alphadirect.co.bw no longer exists.
- **Never guess a money limit** — fail closed.
- Graphite ECS-exec defaults to the TEST db — be explicit for prod.


## Timeline

- time: 2026-08-22T21:32:23
  kind: decision
  summary: "Created this page: Deploy + dev traps and guardrails"
  source: MACHINE-TALK.md
  affects: [deploy-traps]

- time: 2026-08-22T21:32:23
  kind: decision
  summary: Recurring traps to avoid
  source: MACHINE-TALK.md
  affects: [deploy-traps]
