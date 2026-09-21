---
slug: architecture
title: System architecture
role: system architecture
updated: "2026-08-22T21:32:21"
---

# System architecture

**Deploy is zero-downtime blue/green** (infra/host/deploy-zero-downtime.sh; cutover to :8000). Land work by **worktree cherry-pick onto latest origin/main — never rebase or force-push**, never disturb the other machine's uncommitted work. One release + one review per batch.
Rules: a **migration change ⇒ rebuild the backend IMAGE** (not restart); a **code-only change is NOT auto-picked-up** (image bakes /app — needs build backend + up -d); a **frontend change ⇒ build --no-cache frontend**. Always verify the **served bundle / running container**, never git HEAD. See [[deploy-traps]].
