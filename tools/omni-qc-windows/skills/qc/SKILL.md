---
name: qc
description: Run the Omni QC agent on WINDOWS — logs into Omni read-only, checks pages load and buttons work, catches dead pages / broken buttons / JS errors / stale data, and gives a plain-English verdict from DeepSeek (→ Gemini → Ollama). Use when Prathap types /qc, or says "qc <module>", "check omni", "quality check", "is <page> working". Read-only: it can never move money or change data. Modules: all, omni_all, uc_all, dashboard, payroll, hris_leave, commissions, collections (add more in qc.mjs TARGETS). Add --report to ping Telegram.
---

# /qc — the Omni QC agent (Windows)

The engine lives in the folder named by `%USERPROFILE%\.omni-qc-home` (written by
SETUP.bat; normally `%USERPROFILE%\OmniQC\` or the Desktop copy). Inside it:
`engine\qc.mjs`, `engine\act.mjs`, `engine\qa-token.ps1`.

It authenticates with the **read-only QA session** (`%USERPROFILE%\.omni-qa-token`,
self-refreshing from `.omni-qa-key`) — the server refuses writes on it, so nothing
this does can move money or change data.

## Run it
```bat
cd /d %USERPROFILE%\OmniQC
qc.bat dashboard
qc.bat all --report
```
or directly:
```bat
powershell -NoProfile -ExecutionPolicy Bypass -File engine\qa-token.ps1
node engine\qc.mjs <module|all> [--report]
```

- `<module>`: `all`, `omni_all`, `uc_all`, or one name from `TARGETS` in `qc.mjs`.
- `--report`: also send the verdict to Telegram. Needs BOTH
  `%USERPROFILE%\.omni-qc-tg-bot` (bot token) and `.omni-qc-tg-target` (chat id);
  without them the message is printed, not sent.
- Results: `%TEMP%\omni-qc\last-run.json` + one PNG per page in `%TEMP%\omni-qc\`.

## After running
1. READ the verdict line (`--- review (brain: …) ---`) — that is the plain-English answer for the CFO.
2. READ the failing page PNG(s) with your own eyes before reporting anything as broken.
3. If a page is BLANK, first check the route in `TARGETS` is correct (a wrong route blanks too — do not cry wolf).

## Click a button (`act.mjs`)
The app can CLICK, not just look. Read-only token → a click can never write.
```bat
click.bat "/dashboard" "Refresh"
node engine\act.mjs "/dashboard" "Search"
```
Reports what the click did (navigation / modal / JS error) + before+after screenshots.
`--ai` (fuzzy instructions via Stagehand) is NOT wired on Windows — deterministic
named-button clicking only.

## The plain-English paragraph (optional)
`qc.mjs` asks a model to summarise the failures: local gateway on
`http://localhost:4000` (DeepSeek → Gemini) if `%USERPROFILE%\.omni-gateway-key`
exists, else local Ollama on `http://localhost:11434`, else nothing. With none of
them the PASS/FAIL table still prints in full — only the summary paragraph is missing.
Never a paid Anthropic key.

## Give it a goal in words (`explore.mjs`) — the clever layer
Built 9-Sep-2026. `qc.mjs` looks, `act.mjs` clicks a button you NAME, `explore.mjs` works out
the clicks itself from a goal in plain words.
```bat
explore.bat "/dashboard" "go to the Commissions screen"
explore.bat "/commissions/brokers" "find the broker with the worst loss ratio"
node engine\explore.mjs "<route>" "<goal>" --dry            REM decide, never click
node engine\explore.mjs "<route>" "<goal>" --brain gateway  REM DeepSeek/Gemini instead of local
node engine\explore.mjs --selftest                          REM the guard's own test, no browser
```
- **Brain defaults to the LOCAL Ollama** (`qwen3:8b`) so nothing leaves the machine. If that PC has no
  Ollama the tool says so in plain words and stops — it never hops to an external model on its own.
  `--brain gateway` uses judge-deepseek → gemini off-subscription. Never a paid Anthropic key.
- **The model never sees page text** — only the URL path, headings and control labels, each scrubbed
  (emails, id-like digit runs, long tokens).
- **Two independent guards**: the read-only session AND its own blocklist — it refuses to even attempt
  approve / pay / post / delete / save / export / run and 20 more. Navigation words stay allowed.
- Dead-end breaker: a control that was clicked and changed nothing is removed from the next list.
- `OMNI_EXPLORE_UNSAFE=1` disables the blocklist and is honoured **only with `--dry`**, where nothing is
  ever clicked. It exists to prove the guard, never to fire one.
- **It is not a verdict.** Confirm anything it finds with `qc.mjs`.

## Prove-it
- Detector still catches broken: `node engine\qc.mjs _canary` → 0 PASS / 1 FAIL (BLANK + missing button).
- Real page renders + passes: `node engine\qc.mjs dashboard`.
- A run where the canary PASSES means the checker is blind — stop and fix it first.
- Explorer reaches a goal on the free local model: `explore.mjs "/dashboard" "go to the Commissions screen"` → 3 steps, `goal reached: /commissions` (proved on the Mac 9-Sep-2026).
- Explorer refuses a data-changing control: `explore.mjs "/commissions" "export the commission list to a file"` → `REFUSED to click "Export payout"`.
- The guard fails when broken: `explore.mjs --selftest` → 25 money labels blocked / 16 navigation allowed, exit 0. Cripple the blocklist and the same test goes 22 WRONG, exit 1.
