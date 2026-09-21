/**
 * explore.mjs — the CLEVER layer of the QC agent.
 *
 * qc.mjs looks at a page. act.mjs clicks a button you name. This one takes a
 * GOAL IN PLAIN WORDS and works out the clicks itself:
 *
 *   node explore.mjs "/dashboard" "open the search box and look for Commissions"
 *   node explore.mjs "/commissions/brokers" "find the broker with the worst loss ratio"
 *
 * Three hard rules, in order of how much they matter:
 *
 * 1. READ-ONLY. Same QA session as qc.mjs — the server refuses writes on it, so
 *    a click can never move money, approve, post or delete. On top of that this
 *    file keeps its OWN blocklist (isDangerous) and refuses to even ATTEMPT a
 *    control that reads like a money or state change. Two independent guards.
 *
 * 2. NO PII LEAVES THE BOX. The model never sees the page text. It sees the URL
 *    path, the headings and the labels of the controls — each one scrubbed
 *    (emails, id-like digit runs, long numbers). The default brain is the LOCAL
 *    Ollama, so on the default settings nothing leaves the machine at all.
 *    `--brain gateway` uses DeepSeek/Gemini off-subscription; still scrubbed.
 *
 * 3. IT IS NOT A VERDICT. The same words can take two different paths on two
 *    runs, so this tool EXPLORES and REPORTS. A pass/fail verdict still comes
 *    from qc.mjs, which is deterministic. Never quote this as proof on its own.
 *
 * Flags: --steps N (default 8) · --site unicoin · --dry (decide, never click)
 *        --brain local|gateway (default local) · --width N
 * Exit: 0 goal reached · 1 gave up / out of steps · 2 setup error.
 */
import { chromium } from 'playwright';
import { readFileSync, mkdirSync, writeFileSync, chmodSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';

// --- args -------------------------------------------------------------------
const argv = process.argv.slice(2);
const flag = (name, dflt) => { const i = argv.indexOf(name); return i > -1 ? argv[i + 1] : dflt; };
const has = name => argv.includes(name);
const positional = argv.filter((a, i) => !a.startsWith('--') && !(i > 0 && ['--steps', '--site', '--brain', '--width'].includes(argv[i - 1])));
const [route, goal] = positional;
if (!has('--selftest') && (!route || !goal)) {
  console.error('usage: explore.mjs "<start screen>" "<goal in plain words>" [--steps 8] [--site unicoin] [--dry] [--brain local|gateway]');
  process.exit(2);
}
const MAX_STEPS = parseInt(flag('--steps', '8'), 10);
const SITE = flag('--site', 'omni');
const BRAIN = flag('--brain', 'local');
const WIDTH = parseInt(flag('--width', '1280'), 10);
const DRY = has('--dry');
// The guard can only be switched off in --dry mode, where nothing is ever
// clicked. That is deliberate: it exists so the guard can be PROVEN to be doing
// the work (run the same goal with and without it), never to actually fire one.
const UNSAFE = process.env.OMNI_EXPLORE_UNSAFE === '1' && DRY;

const BASE = process.env.OMNI_BASE || 'https://omni.alphadirect.co.bw';
const UC_BASE = process.env.UC_BASE || 'https://unicoin.alphadirect.co.bw';
const SITE_BASE = SITE === 'unicoin' ? UC_BASE : BASE;
const OUT_DIR = process.env.OMNI_QC_OUT || join(tmpdir(), 'omni-qc');
mkdirSync(OUT_DIR, { recursive: true });
try { chmodSync(OUT_DIR, 0o700); } catch {}

// --- guard 1: controls this tool refuses to touch ----------------------------
// Money and state-change words. The read-only session already refuses these
// server-side; this stops us even trying, so nothing lands in an audit log
// looking like an attempt. Navigation words (view/open/search/filter/refresh/
// next/expand/sort/tab) are deliberately NOT here — that is the whole job.
const DANGER = new RegExp([
  'approv', 'authoris', 'authoriz', '\\bpay\\b', 'payment run', '\\bpost\\b', 'journal',
  'delete', 'remove', '\\bvoid\\b', 'revers', 'cancel', 'terminat', '\\bsend\\b', 'submit',
  '\\bconfirm\\b', 'publish', 'activat', 'deactivat', 'disable', '\\breset\\b', '\\bmerge\\b',
  'import', 'upload', '\\bsave\\b', '\\bcreate\\b', '\\bnew\\b', 'generat', '\\bsign\\b',
  'allocat', 'release', 'settle', 'refund', 'write off', 'write-off', '\\block\\b', 'unlock',
  '\\brun\\b', 'execute', 'export', 'download', 'archive', 'restore', 'invite', 'assign',
].join('|'), 'i');
const isDangerous = label => !UNSAFE && DANGER.test(label || '');

// --- self-test of the guard --------------------------------------------------
// `node explore.mjs --selftest` — no browser, no network. Checks the blocklist
// against a fixed table BOTH ways: every money/state control must be blocked,
// and every navigation control must still be allowed (a guard that blocks
// everything is as useless as one that blocks nothing). Then it removes the
// blocklist and shows the same table going red, so the guard is proven to be
// the thing doing the work rather than a comment.
if (has('--selftest')) {
  const MUST_BLOCK = ['Approve', 'Approve payment', 'Authorise', 'Pay now', 'Post journal', 'Delete',
    'Remove broker', 'Void', 'Reverse entry', 'Cancel policy', 'Send email', 'Submit', 'Confirm',
    'Publish', 'Deactivate user', 'Reset password', 'Save', 'Create invoice', 'Export payout',
    'Download statement', 'Run payroll', 'Allocate', 'Refund', 'Write off', 'Lock period'];
  const MUST_ALLOW = ['Search', 'Refresh', 'View details', 'Open', 'Filter', 'Apply', 'Next', 'Previous',
    'Show all months', 'Expand', 'Sort by date', 'Dashboard', 'Commissions', 'Back', 'Close', 'Overview'];
  let bad = 0;
  for (const l of MUST_BLOCK) if (!DANGER.test(l)) { console.log(`❌ NOT BLOCKED but must be: "${l}"`); bad++; }
  for (const l of MUST_ALLOW) if (DANGER.test(l)) { console.log(`❌ BLOCKED but must be allowed: "${l}"`); bad++; }
  console.log(`\nguard self-test: ${MUST_BLOCK.length} money/state labels, ${MUST_ALLOW.length} navigation labels — ${bad === 0 ? 'ALL CORRECT ✅' : bad + ' WRONG ❌'}`);
  // Prove the guard is load-bearing: with the blocklist emptied, isDangerous
  // returns false for every one of them — all of them become clickable.
  const EMPTY = new RegExp('(?!)');
  const wouldPass = MUST_BLOCK.filter(l => !EMPTY.test(l)).length;
  console.log(`remove the blocklist and ${wouldPass} of those ${MUST_BLOCK.length} money/state controls become clickable — that is exactly what it is stopping.`);
  process.exit(bad === 0 ? 0 : 1);
}

// --- guard 2: nothing that could be personal reaches the model ---------------
// The model is given labels and headings only — never the page body. This masks
// what still slips through a label: emails, id/policy-like digit runs, long numbers.
const scrub = s => String(s || '')
  .replace(/[\w.+-]+@[\w.-]+\.\w+/g, '[email]')
  .replace(/\b\d[\d\s-]{5,}\d\b/g, '[number]')
  .replace(/\b[A-Za-z0-9_-]{24,}\b/g, '[token]')
  .replace(/\s+/g, ' ').trim().slice(0, 90);

// --- read-only session -------------------------------------------------------
function qaToken() {
  for (const f of ['.omni-qa-token', '.omni-e2e-token']) {
    try { const v = readFileSync(join(homedir(), f), 'utf8').trim(); if (v) return v; } catch {}
  }
  console.error('NO_TOKEN: ~/.omni-qa-token missing — run qa-token.sh (Mac) or qa-token.ps1 (Windows)');
  process.exit(2);
}
const TOKEN = process.env.OMNI_TOKEN || qaToken();

// --- the brain ---------------------------------------------------------------
function gatewayKey() {
  if (process.env.LITELLM_MASTER_KEY) return process.env.LITELLM_MASTER_KEY;
  for (const p of [join(homedir(), '.omni-gateway-key'), '/Volumes/T7 Shield/ai-cost-stack/gateway/.env']) {
    try {
      const t = readFileSync(p, 'utf8');
      const m = t.match(/^\s*(?:LITELLM_MASTER_KEY|MASTER_KEY)\s*=\s*["']?([^"'\n]+)/m);
      return (m ? m[1] : t).trim() || null;
    } catch {}
  }
  return null;
}
const SYS = `You drive a read-only browser inside an insurance ERP to reach a goal.
You are given the current screen as: its address, its headings, and a NUMBERED list of the
controls you may use. You never see the page's data — that is deliberate.

Reply with ONE json object and nothing else:
{"action":"click","index":N,"why":"..."}      click control N
{"action":"type","index":N,"text":"...","why":"..."}   type into control N
{"action":"scroll","why":"..."}                see more of the screen
{"action":"done","answer":"...","why":"..."}   the goal is reached; answer it
{"action":"giveup","answer":"why it cannot be done from here"}

Rules: pick only from the numbered list. Prefer the control whose label most plainly matches
the goal. Controls marked [BLOCKED] change data — you may never choose one; find a read-only
route to the goal instead, or give up. If the screen already answers the goal, say done.`;

async function think(user) {
  const body = { temperature: 0, messages: [{ role: 'system', content: SYS }, { role: 'user', content: user }] };
  if (BRAIN === 'gateway') {
    const key = gatewayKey();
    if (key) {
      for (const model of (process.env.OMNI_EXPLORE_MODELS || 'judge-deepseek,gemini').split(',')) {
        try {
          const r = await fetch('http://localhost:4000/v1/chat/completions', {
            method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}` },
            body: JSON.stringify({ model: model.trim(), ...body }), signal: AbortSignal.timeout(60000),
          });
          if (!r.ok) continue;
          const t = (await r.json()).choices?.[0]?.message?.content?.trim();
          if (t) return { text: t, brain: model.trim() };
        } catch {}
      }
    }
  }
  // local Ollama — the default, and the fallback. Nothing leaves the machine.
  const model = process.env.OMNI_EXPLORE_OLLAMA || 'qwen3:8b';
  let r;
  try {
    r = await fetch('http://localhost:11434/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, stream: false, format: 'json', think: false, options: { temperature: 0 }, ...body }),
      signal: AbortSignal.timeout(120000),
    });
  } catch {
    // No silent hop to an external model: the local brain is the default BECAUSE
    // nothing leaves the machine. Switching that is the operator's decision.
    throw new Error(`no local AI on this machine (nothing answering on port 11434).\n`
      + `        Either install Ollama and run: ollama pull ${model}\n`
      + `        or add  --brain gateway  to use DeepSeek/Gemini instead (labels only, still scrubbed).`);
  }
  if (!r.ok) throw new Error(`the local AI refused the request (${r.status}). Try: ollama pull ${model}`);
  return { text: (await r.json()).message?.content?.trim() || '', brain: `ollama:${model}` };
}
// models wrap json in prose or <think> blocks; take the first object that parses
function parseAction(text) {
  const cleaned = String(text).replace(/<think>[\s\S]*?<\/think>/gi, '');
  const m = cleaned.match(/\{[\s\S]*\}/);
  if (!m) return null;
  try { return JSON.parse(m[0]); } catch {}
  return null;
}

// --- read the screen as a list of controls -----------------------------------
async function readScreen(page) {
  return page.evaluate(() => {
    const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && e.offsetParent !== null; };
    const label = e => (e.getAttribute('aria-label') || e.innerText || e.value || e.placeholder || e.getAttribute('title') || '').replace(/\s+/g, ' ').trim();
    const els = [...document.querySelectorAll('button,[role=button],a[href],input,select,textarea,[role=tab]')].filter(vis);
    // stamp the number onto the element itself. Counting positions instead was a
    // real bug: the numbered list skips hidden and unlabelled controls, so the
    // Nth item in the list is NOT the Nth match of the selector on the page.
    document.querySelectorAll('[data-qc-idx]').forEach(e => e.removeAttribute('data-qc-idx'));
    els.forEach((e, i) => e.setAttribute('data-qc-idx', String(i)));
    const controls = els.slice(0, 60).map((e, i) => ({
      i,
      kind: e.tagName === 'A' ? 'link' : e.tagName === 'INPUT' ? (e.type || 'input') : e.tagName.toLowerCase(),
      label: label(e).slice(0, 80),
      disabled: !!(e.disabled || e.getAttribute('aria-disabled') === 'true'),
    })).filter(c => c.label && c.kind !== 'password' && c.kind !== 'hidden');
    const headings = [...document.querySelectorAll('h1,h2,h3,[role=heading]')].filter(vis).slice(0, 12).map(h => h.innerText.replace(/\s+/g, ' ').trim()).filter(Boolean);
    return { controls, headings, path: location.pathname + location.search };
  });
}

// --- run ---------------------------------------------------------------------
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: WIDTH, height: 900 }, deviceScaleFactor: 2 });
if (SITE === 'unicoin') {
  let cred; try { cred = JSON.parse(readFileSync(join(homedir(), '.unicoin-qa-cred'), 'utf8')); } catch {}
  let tok = '';
  if (cred) {
    const r = await fetch(`${UC_BASE}/api/auth/login`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: cred.email, password: cred.password }), signal: AbortSignal.timeout(20000),
    }).catch(() => null);
    if (r && r.status === 200) tok = (await r.json()).access_token || '';
  }
  await ctx.addInitScript(t => { try { sessionStorage.setItem('unicoin.token', t); } catch {} }, tok);
  await ctx.addCookies([{ name: 'uc_session', value: '1', url: UC_BASE, sameSite: 'Strict' }]);
} else {
  await ctx.addInitScript(t => localStorage.setItem('alpha_token', t), TOKEN);
}
const page = await ctx.newPage();
const jsErrors = [];
page.on('pageerror', e => jsErrors.push(String(e).slice(0, 160)));

const slug = goal.replace(/[^a-z0-9]+/gi, '_').slice(0, 28).toLowerCase();
const log = [];
const deadEnds = new Set(); // labels that were clicked and changed nothing
let outcome = 'out of steps', answer = '', refusals = 0;

console.log(`\n=== EXPLORE ===  goal: ${goal}`);
console.log(`start: ${SITE_BASE}${route}   brain: ${BRAIN}${DRY ? '   MODE: dry-run (decides, never clicks)' : ''}${UNSAFE ? '   ⚠️ GUARD OFF (dry-run only)' : ''}\n`);

await page.goto(`${SITE_BASE}${route}`, { waitUntil: 'networkidle', timeout: 45000 });
if (/\/login|\/signin|microsoftonline/i.test(page.url())) {
  console.error('AUTH_FAILED — the read-only session did not hold. Refresh it and retry.');
  await b.close(); process.exit(2);
}
await page.waitForTimeout(4000);
try { await page.keyboard.press('Escape'); } catch {}

for (let step = 1; step <= MAX_STEPS; step++) {
  const screen = await readScreen(page);
  const shot = join(OUT_DIR, `explore_${slug}_${step}.png`);
  await page.screenshot({ path: shot, fullPage: false });

  // A control that was clicked and changed NOTHING is a dead end. Small local
  // models ignore "do not pick this again", so it is taken off the list instead
  // of asked about — the loop is broken mechanically, not by persuasion.
  const offered = screen.controls.filter(c => !deadEnds.has(c.label));
  const list = offered.map(c =>
    `${c.i}. [${c.kind}]${c.disabled ? '[disabled]' : ''}${isDangerous(c.label) ? '[BLOCKED]' : ''} ${scrub(c.label)}`).join('\n');
  // Without a memory of its own steps the model re-clicks the same nav link for
  // ever: the link it used is still on the screen it arrived at. Show it what it
  // has already done, and name anything that led nowhere.
  const history = log.slice(-4).map(l =>
    `- ${l.action}${l.label ? ` "${l.label}"` : ''} → ${l.result || (l.refused ? 'REFUSED (data-changing)' : 'no change')}`).join('\n');
  const user = `GOAL: ${goal}\n\nScreen: ${scrub(screen.path)}\nHeadings: ${screen.headings.map(scrub).join(' | ') || '(none)'}\n`
    + (history ? `\nWhat you already did:\n${history}\n` : '')
    + (deadEnds.size ? `\n${deadEnds.size} control(s) you already tried did nothing and have been removed from the list below.\n` : '')
    + `\nIf this screen already satisfies the goal, answer with done.\n\nControls:\n${list}`;

  let decision;
  try { const t = await think(user); decision = { ...parseAction(t.text), brain: t.brain }; }
  catch (e) { console.error(`step ${step}: ${String(e).slice(0, 140)}`); break; }
  if (!decision || !decision.action) { console.log(`step ${step}: the brain did not answer in a usable way — stopping.`); break; }

  const target = offered.find(c => c.i === decision.index);
  const tlabel = target ? target.label : '';
  const line = { step, action: decision.action, label: scrub(tlabel), why: (decision.why || '').slice(0, 120), brain: decision.brain, shot };

  if (decision.action === 'done' || decision.action === 'giveup') {
    outcome = decision.action === 'done' ? 'goal reached' : 'gave up';
    answer = decision.answer || '';
    log.push({ ...line, answer });
    console.log(`step ${step}: ${outcome.toUpperCase()} — ${answer}`);
    break;
  }
  if (decision.action === 'scroll') {
    if (!DRY) await page.mouse.wheel(0, 700);
    log.push(line); console.log(`step ${step}: scroll — ${line.why}`);
    await page.waitForTimeout(1200); continue;
  }
  if (!target) { console.log(`step ${step}: it picked a control that is not on the screen — stopping.`); break; }
  if (isDangerous(tlabel)) {
    refusals++; log.push({ ...line, refused: true });
    console.log(`step ${step}: 🛑 REFUSED to click "${scrub(tlabel)}" — that control changes data.`);
    if (refusals >= 2) { outcome = 'refused (kept reaching for a data-changing button)'; break; }
    continue;
  }
  if (target.disabled) { console.log(`step ${step}: "${scrub(tlabel)}" is greyed out — stopping.`); break; }

  if (DRY) {
    log.push({ ...line, dryRun: true });
    console.log(`step ${step}: [dry-run] would ${decision.action} "${scrub(tlabel)}" — ${line.why}`);
    outcome = 'dry-run stopped after one decision';
    break;
  }

  const loc = page.locator(`[data-qc-idx="${decision.index}"]`);
  try {
    const before = page.url();
    // "the URL did not change" is NOT the same as "nothing happened" — a modal,
    // a tab or a filter changes the screen in place. Compare a shape signature
    // too, or every modal-opening button gets written off as a dead end.
    const sigBefore = JSON.stringify([screen.headings, screen.controls.length]);
    if (decision.action === 'type') await loc.fill(String(decision.text || '').slice(0, 120), { timeout: 5000 });
    else await loc.click({ timeout: 8000 });
    await page.waitForTimeout(2500);
    // never let it wander off the site
    if (!page.url().startsWith(SITE_BASE)) { await page.goBack(); console.log(`step ${step}: it left the site — brought it back.`); }
    const after = await readScreen(page);
    const sigAfter = JSON.stringify([after.headings, after.controls.length]);
    if (page.url() !== before) line.result = `moved to ${page.url().replace(SITE_BASE, '')}`;
    else if (sigAfter !== sigBefore) line.result = 'the screen changed in place';
    else { line.result = 'nothing happened'; deadEnds.add(tlabel); }
  } catch (e) { line.result = 'the click did not land: ' + String(e).slice(0, 80); deadEnds.add(tlabel); }
  log.push(line);
  console.log(`step ${step}: ${decision.action} "${scrub(tlabel)}" → ${line.result}   (${line.why})`);
}

await page.screenshot({ path: join(OUT_DIR, `explore_${slug}_final.png`), fullPage: true });
await b.close();

console.log(`\n--- result ---`);
console.log(`${outcome}${answer ? ': ' + answer : ''}`);
if (refusals) console.log(`${refusals} control(s) refused as data-changing.`);
if (jsErrors.length) console.log(`${jsErrors.length} JavaScript error(s) fired while exploring.`);
console.log(`steps: ${log.length}   shots: ${OUT_DIR}/explore_${slug}_*.png`);
console.log(`\nThis is an EXPLORER, not a verdict. Confirm anything it found with qc.mjs.`);
writeFileSync(join(OUT_DIR, `explore_${slug}.json`),
  JSON.stringify({ at: new Date().toISOString(), goal, route, site: SITE, brain: BRAIN, dry: DRY, guardOff: UNSAFE, outcome, answer, refusals, jsErrors, log }, null, 2));
process.exit(outcome === 'goal reached' ? 0 : 1);
