# SESSION END CHECKLIST — Claude must read this BEFORE claiming a session is "done"

**For every Claude Code session that touches Alpha Direct Insurance omni / alpha-finance code.**

The CFO has been seeing the same pattern for 5+ days: Claude declares a fix done, the dashboard shows the wrong value, the CFO has to escalate. This file exists so Claude self-checks at the end of every session and the pattern stops repeating.

**Read each item. Answer "Y/N + how I verified". If any answer is N, the session is NOT done.**

---

## 1. Did the user-visible number / screen actually change?

- [ ] I navigated to the affected page on `https://omni.alphadirect.co.bw` via Chrome MCP **after the deploy**.
- [ ] I took a screenshot of that page.
- [ ] I compared the displayed value to the FROZEN NUMBER or to the CFO's stated expectation.
- [ ] The values match (within stated tolerance).

A Python shell that prints the right number is **not** verification. The CFO sees the dashboard, not the shell.

---

## 2. Did the deploy actually reach production?

- [ ] I ran `git log --oneline origin/main -1` on prod via Session Manager AND confirmed the SHA matches the PR I merged.
- [ ] I ran `docker compose ... ps` and confirmed the relevant container (`backend`, `frontend`, or both) shows `Up Xs (healthy)` with a recent timestamp.
- [ ] If this is a frontend change, I also confirmed the new JS bundle is being served (not a cached `_next/static/chunks/*.js`).

---

## 3. Did I trace the actual code path?

For any UI-visible fix:

- [ ] I read the **frontend** file that renders the affected tile / page (not just the backend function I changed).
- [ ] I traced which API endpoint that frontend calls.
- [ ] I traced which backend function that endpoint invokes.
- [ ] My change is on that exact path — not on a parallel function that nobody calls.

Many tiles read from `cashPosition.total_bwp`, `maProfitLoss.totals.gross_written_premium`, `balanceSheet.totals.total_assets`, etc. — different endpoints. Don't assume.

---

## 4. Did I think about caches and refresh?

- [ ] The dashboard auto-refreshes every 60s when visible. If the CFO's tab was open during my deploy, his view may still be stale until the next refresh tick.
- [ ] Cloudflare may cache static JS bundles (frontend changes). A hard reload (Ctrl+Shift+R) on the CFO's browser is required if I expect immediate visual change.
- [ ] If the CFO took a screenshot showing the OLD value, I checked the time stamp against my deploy time before assuming the fix didn't work.

---

## 5. Did I run the FROZEN NUMBERS / TB sanity checks?

After any change that could affect ADIC financials:

- [ ] Bank end-Dr for FY25 (2025-06-30) ≈ **12,883,982.11 BWP**
- [ ] GWP for FY25 ≈ **125,202,058.61 BWP** (CSV) / 125.15M (workbook)
- [ ] PAT for FY25 ≈ **+0.292 BWP Mn**
- [ ] PAT for FY26-9M ≈ **+0.950 BWP Mn**
- [ ] Net cash position (build_cash_position) ≈ **10.4M BWP** (FY26-9M close)

If any check fails, the session is NOT done.

---

## 6. Did I respect the hard lock?

- [ ] My changes do not write to ADIC JEs in FY25 or FY26-9M periods outside the `tb_csv_import` whitelist, OR I used the documented override path.
- [ ] The `LOCKED_FINANCIALS.md` doc still accurately reflects the lock state.

---

## 7. Did I handle the "I made a mistake" cycle honestly?

If during the session the CFO had to correct me on the same point twice:

- [ ] I saved a **memory feedback rule** for that specific mistake pattern (in `~/.claude/projects/.../memory/`).
- [ ] I referenced that rule in the session summary.
- [ ] I changed my workflow for the remainder of the session — not just promised to.

---

## 8. Are the PRs clean?

- [ ] Each PR has a clear title (`fix|feat(scope): description`) and a description with **Why** and **What**.
- [ ] Each PR's test plan was actually executed (not just listed).
- [ ] Any destructive prod operation (bulk delete, schema change) is documented in the PR body.

---

## 9. Final status summary

End every session with a 5-line status to the CFO:

1. **What ships:** PR numbers + commit SHAs on main.
2. **What's visibly different on the dashboard:** the specific tile + new value + screenshot proof.
3. **What I left open:** any residuals, follow-ups, or "this won't show right until the Odoo backfill runs".
4. **What I learned:** if any new memory rule was saved.
5. **What you need to click:** if anything (hard refresh, run `/ultrareview`, log in to AWS, etc.).

If any of section 1, 2, 5 is missing or wrong, the CFO will catch it within minutes and the cycle restarts. Don't restart the cycle.

---

## How Claude must use this file

- At the START of every alpha-finance session: read this file once.
- At the END of every alpha-finance session, BEFORE writing the final status message: walk through items 1-9 explicitly. Either tick each box in the final message, or list which ones failed and what's needed to clear them.
- This is enforced via memory binding (`~/.claude/projects/.../memory/`); claude must self-check.
