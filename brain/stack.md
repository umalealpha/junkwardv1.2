---
slug: stack
title: Tech stack
role: tech-stack choices
updated: "2026-08-22T21:32:21"
---

# Tech stack

Backend: **Django / DRF**, 47 apps (core, payments, payroll, procurement, banking, billing, claims, records, salvage, hris, customer_refunds, commissions, bonu). Frontend: **Next.js** (TypeScript; tsc + vitest). DB: **Postgres**. Runtime: **Docker Compose**; a local Python `.venv` (repo carries none).
Prod: EC2 **i-02a5d76a61f4f09a5**, region **af-south-1**, domain omni.alphadirect.co.bw. Deploys via **AWS SSM** as IAM user **claude-cli**. Repo: github.com/alphadirectinsurance/alpha-finance.
CI (ci.yml) hard-gates migration state + tests. Gaps: frontend vitest NOT wired into CI; CI validates the branch, not the merge; a PR sometimes gets NO workflow run (absent != green).
