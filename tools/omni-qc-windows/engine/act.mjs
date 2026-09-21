/**
 * act.mjs — the "click a button / do a task" tool for the Omni QC agent.
 *
 * Two modes, both on the READ-ONLY QA session (server refuses writes, so a click
 * can never move money, post, approve or delete — it can look and navigate only):
 *
 *   node act.mjs "<route>" "<button text>"            # deterministic (Playwright)
 *   node act.mjs "<route>" "<plain instruction>" --ai # AI (Stagehand + DeepSeek)
 *
 * Deterministic finds a real VISIBLE+ENABLED button by its text and clicks it.
 * --ai lets DeepSeek read the page and decide which control matches the words
 * ("open the FY2026 filter and apply") — the layer that "understands the screen".
 *
 * Reports what the click did: navigation, a modal opening, or a JS/data error.
 * Screenshots before + after into /tmp/omni-qc/.
 */
import { chromium } from 'playwright';
import { readFileSync, mkdirSync, chmodSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';

const route = process.argv[2];
const instruction = process.argv[3];
const AI = process.argv.includes('--ai');
if (!route || !instruction) { console.error('usage: act.mjs "<route>" "<button text | instruction>" [--ai]'); process.exit(2); }

const BASE = process.env.OMNI_BASE || 'https://omni.alphadirect.co.bw';
// A work-in-progress server never goes network-idle (hot reload holds a socket
// open), so a local target waits for the DOM instead. Live behaviour unchanged.
const NAV_WAIT = /localhost|127\.0\.0\.1/.test(BASE) ? 'domcontentloaded' : 'networkidle';
// Windows has no /tmp — shots go to the OS temp folder. Override with OMNI_QC_OUT.
const OUT = process.env.OMNI_QC_OUT || join(tmpdir(), 'omni-qc'); mkdirSync(OUT, { recursive: true }); try { chmodSync(OUT, 0o700); } catch {} // shots hold staff/financial data
const TOKEN = process.env.OMNI_TOKEN || (() => {
  for (const f of ['.omni-qa-token', '.omni-e2e-token']) { try { const v = readFileSync(join(homedir(), f), 'utf8').trim(); if (v) return v; } catch {} }
  console.error('NO_TOKEN'); process.exit(2);
})();
const SAFE = instruction.replace(/[^a-z0-9]+/gi, '_').slice(0, 30);

function gatewayKey() {
  if (process.env.LITELLM_MASTER_KEY) return process.env.LITELLM_MASTER_KEY;
  try { const t = readFileSync(join(homedir(), '.omni-gateway-key'), 'utf8'); const m = t.match(/^\s*(?:LITELLM_MASTER_KEY|MASTER_KEY)\s*=\s*["']?([^"'\n]+)/m); return (m ? m[1] : t).trim() || null; } catch {}
  return null;
}

// ---- AI mode: Stagehand + DeepSeek via the local gateway --------------------
async function runAI() {
  // Stagehand is installed and the browser wiring works; what remains is binding
  // its planner to the OFF-SUBSCRIPTION gateway. Stagehand v4 only accepts real
  // provider model-names (openai/gpt-*, google/gemini-*) and needs a custom
  // ClientLLM adapter to emit its structured-output protocol from DeepSeek. That
  // adapter is the one scoped follow-on; until then, use deterministic mode.
  // Stagehand v4 hard-routes inference to OpenAI (no custom baseURL in its config).
  // To run OFF the Claude subscription on the local DeepSeek gateway it needs a
  // custom ClientLLM `generate` adapter (message/tool-call/json_schema protocol).
  // That adapter is the scoped follow-on. Until it exists, --ai does NOT call out
  // (never leak a gateway key to real OpenAI). Deterministic clicking covers
  // named-button commands today.
  console.error('AI (fuzzy-instruction) clicking is not wired yet: Stagehand v4 offers no custom baseURL,');
  console.error('so the off-subscription gateway needs a ClientLLM adapter (next task).');
  console.error('Deterministic clicking works now:  node act.mjs "' + route + '" "<button text>"');
  process.exit(4);
}

// ---- deterministic mode: Playwright click by text ---------------------------
async function runPlain() {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 });
  await ctx.addInitScript(t => localStorage.setItem('alpha_token', t), TOKEN);
  const page = await ctx.newPage();
  const errs = []; page.on('pageerror', e => errs.push(String(e).slice(0, 160)));
  await page.goto(`${BASE}${route}`, { waitUntil: NAV_WAIT, timeout: 45000 });
  if (/\/login|\/signin|microsoftonline/i.test(page.url())) { console.error('AUTH_FAILED'); await b.close(); process.exit(3); }
  await page.waitForTimeout(6000);
  try { await page.keyboard.press('Escape'); } catch {}
  await page.waitForTimeout(1500);
  await page.screenshot({ path: join(OUT, `act_${SAFE}_before.png`), fullPage: true });
  const urlBefore = page.url();
  const dialogsBefore = await page.locator('[role=dialog]').count();

  let clicked = false;
  try {
    const loc = page.getByRole('button', { name: instruction, exact: false });
    const n = await loc.count();
    for (let i = 0; i < n; i++) { const el = loc.nth(i); if (await el.isVisible() && await el.isEnabled()) { await el.click({ timeout: 5000 }); clicked = true; break; } }
  } catch {}
  await page.waitForTimeout(2500);
  const dialogsAfter = await page.locator('[role=dialog]').count();
  await page.screenshot({ path: join(OUT, `act_${SAFE}_after.png`), fullPage: true });

  const result = !clicked ? 'BUTTON NOT FOUND (no visible+enabled button with that text)'
    : errs.length ? `clicked but ${errs.length} JS error(s): ${errs[0]}`
    : page.url() !== urlBefore ? `clicked — navigated to ${page.url()}`
    : dialogsAfter > dialogsBefore ? 'clicked — a dialog/modal opened'
    : 'clicked — page responded (no nav/modal; likely in-place update)';
  console.log(`ACT "${instruction}" on ${route}: ${result}`);
  console.log(`shots: ${OUT}/act_${SAFE}_before.png , _after.png`);
  await b.close();
  process.exit(clicked ? 0 : 1);
}

if (AI) await runAI(); else await runPlain();
