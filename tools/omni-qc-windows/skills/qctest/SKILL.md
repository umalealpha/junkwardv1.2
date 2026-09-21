---
name: qctest
description: Test EVERYTHING built in the current chat through the Omni QC agent (Windows), look up the best-practice fix for each finding (web search), get Fable 5.1's suggestions, and hand the CFO plain-English recommendations a non-coder can act on. Use when Prathap types /qctest, or says "qc test this", "test what we built", "run the qc on this chat", "check everything we did today". Works for Omni, the UniCoin portal and any endpoint touched in the chat. Never claims "works" without the QC having SEEN it (PNG read, 2xx shown, click landed).
---

# /qctest — test this chat's work through the QC, then explain it like a human (Windows)

**Why this exists.** CFO, 7-Sep-2026: *"whenever I say /qctest the chat will test all the features
I have created in that chat through the qc and suggest its findings … google and find best possible
actions and ask Fable 5.1 for suggestions and give recommendations … understandable for a simple human
who doesn't know coding."*

**Output he wants:** one table — per feature: Works? · What's wrong · What it means for him · Recommended fix
(plain words) · Effort · Risk — then a /recc pop-up for what to fix. No jargon to his face (no API, endpoint,
JSON, HTTP codes, file paths, repo, deploy). Say "the screen", "the button", "the server", "the link".

---

## Step 1 — Inventory what THIS chat built (never test from memory)
Build the list from evidence in the session, in this order:
1. Goal contracts written this chat: `dir /b /o-d %TEMP%\goal_contract*.json` — every criterion is a feature to re-test.
2. Files changed this chat (Edit/Write in the transcript) → derive the surface: a screen route, a button
   label, a bot command, a scheduled job, a server endpoint.
3. Commits landed this chat: `git -C %USERPROFILE%\work\alpha-finance log --oneline --since="<session start>"` and
   MACHINE-TALK lines written today (`git -C %USERPROFILE%\work\alpha-finance show origin/main:MACHINE-TALK.md`).
Write the inventory as a numbered list **in the CFO's words** for each item (what he asked), then the
machine surface next to it. Anything he asked for that has no surface = a finding already ("not built").

## Step 2 — Test each item through the QC (real path, own eyes)
**Engine location:** the folder named by `%USERPROFILE%\.omni-qc-home` (SETUP.bat wrote it). All commands below run from there. Never a bare "200 = works".
| Surface | How the QC tests it |
|---|---|
| Omni screen | `node engine\qc.mjs <module>` (add a target ONLY for a route you have seen render; guessed routes cry wolf). READ the PNG. |
| UniCoin screen | `node engine\qc.mjs uc_<module>` (site: unicoin, QA-manager fixture). READ the PNG. |
| A button | `node engine\act.mjs "<route>" "<button text>"` (deterministic click, read-only token) — report what happened. |
| A whole task / flow | `node engine\explore.mjs "<route>" "<goal in plain words>"` — works the clicks out itself (local Ollama by default; `--brain gateway` for DeepSeek). Use it to FIND what to test and to walk a flow end-to-end. It refuses money/state controls and never sees page text. **Its result is never the PASS** — turn what it found into a `qc.mjs` target or an `act.mjs` click and prove it deterministically. |
| A server call | hit it with the READ-ONLY identity and show the response shape; a write call is proven by its tests on prod, never by writing real data. |
| A scheduled job | `schtasks /query /tn "<name>"` + run the script once by hand (`--dry` where it has one) and read the output. |
| A Telegram delivery | the send confirmation line — and say the CFO can confirm on his phone. |
Every canary in the engine must still FAIL (`_canary`, `_uc_canary`, `_canary_confuse`, `_canary_stale`) —
run them first; a passing canary means the QC is blind and NOTHING else in the run counts. If the run
uses `explore.mjs`, also run `node engine\explore.mjs --selftest` first (guard must be ALL CORRECT, exit 0).

Record per item: PASS / FAIL / NOT TESTABLE (say why — e.g. needs the CFO's own inbox, needs a key only he has).

## Step 3 — For every FAIL or weak spot: look it up, then ask Fable 5.1
1. **Web search** (WebSearch tool) the failure in general terms — the technology + the symptom, never our
   data, never a customer name, never a key. Take the 2–3 best-practice fixes people actually use.
2. **Ask Fable 5.1.** If this session is already Fable 5.1, reason it out yourself; otherwise dispatch a
   Fable agent with the finding + what the search said + our constraints. Constraints to pass along every time: Omni never moves money; read-only QC identity; no
   Anthropic/paid keys; frozen ADIC figures; no customer PII to any external model; reuse before build.
3. Turn the answer into ONE recommended fix per finding, in plain words: what we change, what he will see
   afterwards, how long, what could go wrong. Mark `Effort: minutes / hour / half-day` and `Risk: none / low / medium`.

## Step 4 — Report (the only format)
Findings at the TOP, passes below. Then the table:

| # | You asked for | Works? | What's wrong (plain) | What it means for you | Recommended fix | Effort | Risk |
|---|---|---|---|---|---|---|---|

Then **/recc**: one pop-up (AskUserQuestion, recommended option first, "(Recommended)" in its label) asking
which fixes to do now — max 4 questions, the ones that block the most.

## Step 5 — After his answers
Fix what he picked, re-run `/qctest` on those items only, and close with the standing `/pending` table
(every item, ✅ / ⚠️ / ❌, proof column). Log the run in MACHINE-TALK.

## Rules
- Prove by RUNNING. "It compiled", "it deployed", "the page loads" are not a PASS.
- A test that cannot fail proves nothing — if a canary passes, stop and fix the QC first.
- Never fire shutdown / restart / approve / send-money-like actions as a "test".
- Never put a credential, a customer name or a policy number into a web search or an external model.
- Plain English to the CFO. If a word would not appear in a newspaper, translate it.
