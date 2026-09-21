# RELEASE CHECKLIST — how to make an Omni change live safely

Follow this exactly. It exists so a release is **seconds** of downtime, not hours, and so no
half-finished work ever goes live next to someone else's.

---

## 🟢 NORMAL lane (features, tidy-ups, non-urgent fixes)

**Before you start**
- [ ] Add your row to `CHANGE-LIST.md` (Status `building`).
- [ ] Scan `CHANGE-LIST.md` — if another chat is `building`/`waiting` on the **same area**, coordinate first.

**Build**
- [ ] Work on your own branch / worktree, off the **latest** `main` — never on top of another chat's uncommitted work.
- [ ] Keep your change small and focused (only what was asked).

**Check it's actually right (do NOT skip — this is what stops the clashes)**
- [ ] Run the ship gate `/fabe` — it builds, runs the tests, and reviews the change before it can go live.
- [ ] Run the quality check on the changed page(s): `qc.sh "/the/page"` (see prat-skill §16). Read the pictures.
- [ ] For anything money/GL/payroll: keep the financial-invariant tests green; reconcile any number to the MA workbook.
- [ ] Confirm your branch is up to date with `main` and merges cleanly (no conflicts).

**Go live (quiet time — ideally after hours)**
- [ ] Merge to `main`.
- [ ] Deploy via the normal path (EC2 `i-02a5d76a61f4f09a5` via SSM: `git pull` → build → `up -d`).
      Entrypoint auto-runs migrations + chart-of-accounts setup.
- [ ] ⚠️ **Front-end change?** The deploy script rebuilds the BACKEND only. A front-end change needs the
      front-end rebuilt with `build --no-cache frontend`, or your change won't actually appear.
- [ ] ⚠️ **Migration / model change?** The backend **image must be rebuilt**, not just restarted.

**Prove it's live (never say "done" without this)**
- [ ] Health check the site is up.
- [ ] Open the changed page in the browser and confirm the change is really there — check the **served page**, not the code.
- [ ] Re-do the exact thing that was asked for, and watch it work.
- [ ] Update your `CHANGE-LIST.md` row to `live`, then remove it.

---

## 🔴 EMERGENCY lane (live payment / payroll / money problem right now)

Same safety, stripped to the minimum, because speed matters.

- [ ] Add a 🔴 row to `CHANGE-LIST.md` so everyone knows an emergency release is happening.
- [ ] **GOLDEN RULE: this fix goes out ALONE.** Do not bundle any other change with it. Do not merge anyone
      else's waiting work at the same time. One change only.
- [ ] Make the smallest possible fix, off the **latest** `main`.
- [ ] Quick check: run `/fabe` if there's time; at minimum run the change through the tests for the area you touched.
- [ ] Deploy (same path as above; remember the front-end / migration warnings).
- [ ] Immediately prove it in the browser — do the failing action again and watch it succeed.
- [ ] Note the "undo": because it went out alone, if it misbehaves you revert **just this one change** and redeploy.
- [ ] Update / remove your `CHANGE-LIST.md` row.

---

## The traps that have each cost a day (don't relearn them)

- Prod `git pull` runs as the `ubuntu` user, not root.
- A migration change needs the backend **image rebuilt**, not restarted.
- The deploy script rebuilds the **backend only** — a front-end change needs `build --no-cache frontend`.
- Verify the **served page / running image**, not the latest code in git.
- Never restart the prod web front-door (Caddy) blind.
- Never write test entries into live prod records.

---

## Why this keeps Omni up

Omni already builds the new version quietly **beside** the running one and then flips to it — so a
clean release is near-instant. The long outages were never the swap; they were **clashing,
untested changes breaking the build**. Fix the clashing (one change at a time, checked first) and
the downtime disappears.
