/**
 * qc.mjs — the Omni QC agent engine.
 *
 * Loads each target page as the READ-ONLY QA session and detects the two bug
 * classes the CFO named — pages that don't load, and buttons that don't work —
 * plus JavaScript crashes. Emits a deterministic PASS/FAIL verdict, an optional
 * plain-English review from the local Fable gateway, and (with --report) sends
 * the verdict to the CFO's Android via openclaw Telegram.
 *
 * READ-ONLY BY DESIGN: it authenticates with ~/.omni-qa-token. The server
 * refuses writes on that session, so nothing this agent does can move money,
 * post a journal, or change data. It looks; it never touches.
 *
 * Reuses the exact working render pattern from omni-shot.mjs (token injection,
 * login-redirect detection, modal dismissal, blank-content detection).
 *
 *   node qc.mjs <module|all> [--report] [--dark] [--width 1280]
 *
 * Exit code: 0 = all targets PASS, 1 = at least one FAIL, 2 = setup error.
 */
import { chromium } from 'playwright';
import { readFileSync, mkdirSync, writeFileSync, chmodSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join, dirname } from 'node:path';

const BASE = process.env.OMNI_BASE || 'https://omni.alphadirect.co.bw';
// --- sites: the QC agent watches more than Omni (CFO 7-Sep-2026: "manage the
// UniCoin portal also"). A target names its site; omni is the default.
const UC_BASE = process.env.UC_BASE || 'https://unicoin.alphadirect.co.bw';
const SITES = { omni: { base: BASE }, unicoin: { base: UC_BASE } };
// A work-in-progress server never goes network-idle (hot reload holds a socket
// open), so a local target waits for the DOM instead. The LIVE default is
// unchanged on purpose: page timings are compared against history for the
// slow-page alarm, and switching the wait everywhere would move every number.
const NAV_WAIT = /localhost|127\.0\.0\.1/.test(BASE) ? 'domcontentloaded' : 'networkidle';
// Windows has no /tmp — screenshots go to the OS temp folder
// (C:\Users\<you>\AppData\Local\Temp\omni-qc). Override with OMNI_QC_OUT.
const OUT_DIR = process.env.OMNI_QC_OUT || join(tmpdir(), 'omni-qc');
mkdirSync(OUT_DIR, { recursive: true });
try { chmodSync(OUT_DIR, 0o700); } catch {} // screenshots hold staff/financial data (mkdir mode is ignored if dir exists)

// Per-module run history of a tracked total — lets QC tell "page loaded fine"
// from "the feed behind it stopped updating" (CFO 7-Sep: "QC should be able to
// see whether these are getting updated"). OMNI_QC_HISTORY overrides the path
// so the frozen check can be tested against a seeded file.
const HIST_FILE = process.env.OMNI_QC_HISTORY || join(homedir(), '.omni-qc', 'history.json');
function loadHistory() { try { return JSON.parse(readFileSync(HIST_FILE, 'utf8')); } catch { return {}; } }
function saveHistory(h) {
  try { mkdirSync(dirname(HIST_FILE), { recursive: true, mode: 0o700 }); writeFileSync(HIST_FILE, JSON.stringify(h, null, 1), { mode: 0o600 }); } catch {}
}

// --- targets: module -> { route, expect: [visible button/link labels that MUST exist] }
// Start with the modules the CFO already watches; extend freely.
const TARGETS = {
  dashboard:   { route: '/dashboard',                    expect: [] },
  payroll:     { route: '/payroll',                      expect: [] }, // add VERIFIED button labels only
  hris_leave:  { route: '/hris/leave',                   expect: [] },
  commissions: { route: '/commissions',                  expect: [] },
  tasks:       { route: '/tasks',                        expect: [] }, // watched for human-confusion smells (see confuse scan)
  // RealPay collections — no "as of" date on the page, so freshness is judged by
  // MEMORY: the tracked total must move across runs (see finding.frozen).
  collections: { route: '/banking/realpay/collections/dashboard', expect: [], track: /Total Collected\s*BWP\s*([\d,]+\.\d{2})/i },
  // --- Added 2026-09-08 (/qctest of that day's build). Every route below was
  // SEEN rendering live on prod in that session before being added here —
  // a guessed route cries wolf and makes the whole sweep worthless.
  uw_adoption:   { route: '/underwriting/adoption', expect: ['Tool Adoption'] },
  payment_reqs:  { route: '/payment-requests',      expect: [] },
  // --- Added 2026-09-08: the Alpha Nexus tester competition board, after the
  // tracker was re-based to 7-Sep-2026 (route SEEN rendering live that day).
  nexus_testers: { route: '/rewards/nexus-testers', expect: ['Email standings'] },
  brokers:     { route: '/commissions/brokers',           expect: [] },
  // --- Added 2026-09-09 (/qctest of Medu's ADH work). Both routes SEEN
  // answering 200 on prod in that session before being added here.
  health_adh:       { route: '/health/adh-dashboard',    expect: ['Refresh'] },
  health_providers: { route: '/health/service-providers', expect: [] },
  // Add more verified routes here as needed. (payroll/hris_leave/commissions
  // above are confirmed live-rendering; unverified guesses are left out so the
  // default sweep never cries wolf.)
  // --- UniCoin portal (site: unicoin) — every route below VERIFIED live-rendering
  // for the QA manager fixture on 7-Sep-2026 (HTTP 200, no bounce, no JS error).
  uc_app:           { site: 'unicoin', route: '/app.html',                expect: [] }, // prototype "one login" bridge
  uc_dashboard:     { site: 'unicoin', route: '/dashboard',               expect: [] },
  uc_commissions:   { site: 'unicoin', route: '/dashboard/commissions',   expect: [] },
  uc_audit:         { site: 'unicoin', route: '/dashboard/audit',         expect: [] },
  uc_agents:        { site: 'unicoin', route: '/dashboard/agents',        expect: [] },
  uc_collections:   { site: 'unicoin', route: '/dashboard/collections',   expect: [] },
  uc_policies:      { site: 'unicoin', route: '/dashboard/policies',      expect: [] },
  uc_performance:   { site: 'unicoin', route: '/dashboard/performance',   expect: [] },
  uc_team:          { site: 'unicoin', route: '/dashboard/team',          expect: [] },
  uc_kyc:           { site: 'unicoin', route: '/dashboard/kyc',           expect: [] },
  uc_field_sales:   { site: 'unicoin', route: '/dashboard/field-sales',   expect: [] },
  uc_upload:        { site: 'unicoin', route: '/dashboard/upload',        expect: [] },
  uc_cancellations: { site: 'unicoin', route: '/dashboard/cancellations', expect: [] },
  uc_conversions:   { site: 'unicoin', route: '/dashboard/conversions',   expect: [] },
  uc_reactivation:  { site: 'unicoin', route: '/dashboard/reactivation',  expect: [] },
  uc_upsell:        { site: 'unicoin', route: '/dashboard/upsell',        expect: [] },
  uc_blt:           { site: 'unicoin', route: '/dashboard/blt',           expect: [] },
  uc_categorise:    { site: 'unicoin', route: '/dashboard/categorise',    expect: [] },
  uc_archive:       { site: 'unicoin', route: '/dashboard/archive',       expect: [] },
  uc_add:           { site: 'unicoin', route: '/dashboard/add',           expect: [] },
  // canary: a route that cannot exist + a button that cannot be there. MUST fail.
  // Proves the detector still catches broken pages / missing buttons.
  _canary:     { route: '/__qc_canary_no_such_route__',   expect: ['__no_such_button__'] },
  _uc_canary:  { site: 'unicoin', route: '/dashboard/__qc_canary_no_such_route__', expect: ['__no_such_button__'] },
  // confusion canary: a fixture page that DUMPS a raw link + token blob + junk
  // into visible text — MUST be flagged. Proves the human-confusion detector fires.
  _canary_confuse: { route: 'data:text/html,' + encodeURIComponent(
    '<main style="padding:40px;font-size:16px">Tap a name to write two lines about them:<br>' +
    '- John Doe: https://omni.alphadirect.co.bw/hris/api/manager-feedback/' +
    'eyJtIjoiNWVmOTdhMTgtNGYwZi00Nzk4LWFlOTAtMjc3Y2I4ZDUwMGJiIiwicHJvZmlsZSI6InRlc3QifQ/?profile=abc<br>' +
    'Balance: undefined · Total: [object Object]</main>'), expect: [] },
  // freshness canary: a fixture whose "as of" date is far in the past — MUST be
  // flagged stale. Proves the freshness detector fires (the data-stopped-updating case).
  _canary_stale: { route: 'data:text/html,' + encodeURIComponent(
    '<main style="padding:40px;font-size:16px">Collections Dashboard. Total Collected BWP 3,913,717.16. ' +
    'Reconciled to import as of 01-JAN-2024. Everything looks fine here.</main>'), expect: [] },
};

// --- args
const arg = (process.argv[2] || 'all').toLowerCase();
const REPORT = process.argv.includes('--report');
const FILE_BUGS = process.argv.includes('--file-bugs'); // idea #6: file a bug on the Omni board per broken page
const ONLY_FAIL = process.argv.includes('--only-fail'); // quiet when all green (nightly)
const DARK = process.argv.includes('--dark');
const wIdx = process.argv.indexOf('--width');
const WIDTH = wIdx > -1 ? parseInt(process.argv[wIdx + 1], 10) : 1280;
// an "as of / updated / reconciled" date older than this = the data stopped updating
const sdIdx = process.argv.indexOf('--stale-days');
const STALE_DAYS = sdIdx > -1 ? parseInt(process.argv[sdIdx + 1], 10) : 3;
// idea #2: a page is "slow" when it takes more than SLOW_FACTOR × its own usual load
// time AND at least SLOW_MIN_MS outright (so a fast page wobbling 200→500 ms never
// pings anyone). SLOW_MIN_MS=0 is the test knob that lets the check go red on demand.
const SLOW_FACTOR = Number(process.env.SLOW_FACTOR || 2);
const SLOW_MIN_MS = process.env.SLOW_MIN_MS !== undefined ? Number(process.env.SLOW_MIN_MS) : 3000;
const ALL = Object.keys(TARGETS).filter(k => !k.startsWith('_'));
const modules = arg === 'all' ? ALL
  : arg === 'uc_all' ? ALL.filter(k => TARGETS[k].site === 'unicoin')
  : arg === 'omni_all' ? ALL.filter(k => !TARGETS[k].site)
  : [arg];
for (const m of modules) if (!TARGETS[m]) { console.error(`UNKNOWN module "${m}". Known: ${Object.keys(TARGETS).join(', ')}, all, omni_all, uc_all`); process.exit(2); }

// --- read-only QA token (value never printed)
function qaToken() {
  for (const f of ['.omni-qa-token', '.omni-e2e-token']) {
    try { const v = readFileSync(join(homedir(), f), 'utf8').trim(); if (v) return v; } catch {}
  }
  console.error('NO_TOKEN: ~/.omni-qa-token missing'); process.exit(2);
}
const TOKEN = process.env.OMNI_TOKEN || qaToken();

// --- one page check ---------------------------------------------------------
async function checkPage(ctx, name, spec) {
  const finding = { name, site: spec.site || 'omni', route: spec.route, ok: false, auth: true, blank: false,
                    pageerrors: [], neterrors: [], buttons: 0, disabled: 0, missing: [],
                    confuse: [], stale: [], total: null, frozen: '', ms: 0, slow: '', shot: '' };
  const page = await ctx.newPage();
  const errs = [];
  const nets = [];
  page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
  // Failed data calls are the real "unable to load" signal — a page can render
  // its chrome and still be broken because its API 500'd. Catch server errors on
  // the page's own data calls (xhr/fetch/document). 4xx is left out: auth probes
  // legitimately 401/403 and would cry wolf.
  page.on('response', r => {
    try { const rt = r.request().resourceType();
      if (r.status() >= 500 && (rt === 'xhr' || rt === 'fetch' || rt === 'document')) nets.push(`${r.status()} ${r.url().slice(0, 120)}`);
    } catch {}
  });
  // route is a path on the target's site, or an absolute data:/http URL (fixtures/canaries)
  const site = SITES[spec.site || 'omni'];
  const NAV = /^(data:|https?:)/.test(spec.route) ? spec.route : `${site.base}${spec.route}`;
  // a login bounce: Omni sends to /login or Microsoft; UniCoin's native app sends an
  // unauthenticated user back to its root sign-in page
  const bounced = () => /\/login|\/signin|microsoftonline/i.test(page.url())
    || (spec.site === 'unicoin' && spec.route !== '/' && page.url().replace(/\/$/, '') === site.base);
  try {
    const t0 = Date.now();
    await page.goto(NAV, { waitUntil: NAV_WAIT, timeout: 45000 });
    finding.ms = Date.now() - t0; // idea #2: how long a human waited for this screen
    // one retry on a login bounce, so a transient session/CDN blip doesn't cry wolf
    if (bounced()) {
      await page.waitForTimeout(3000);
      try { await page.goto(NAV, { waitUntil: NAV_WAIT, timeout: 45000 }); } catch {}
    }
    if (bounced()) { finding.auth = false; finding.pageerrors = errs; finding.neterrors = nets; await page.close(); return finding; }
    try { await page.waitForLoadState('networkidle', { timeout: 15000 }); } catch {}
    await page.waitForTimeout(6000);
    // dismiss the global "tasks need your action" login popup so it doesn't hide the page
    try { await page.keyboard.press('Escape'); } catch {}
    try {
      await page.evaluate(() => {
        const hit = [...document.querySelectorAll('div')].find(d => /need your action/i.test(d.textContent || '') && d.offsetParent);
        if (hit) { let n = hit; for (let i = 0; i < 8 && n; i++) { const cs = getComputedStyle(n); if (cs.position === 'fixed' || parseInt(cs.zIndex || '0', 10) >= 40) { n.remove(); break; } n = n.parentElement; } }
      });
    } catch {}
    await page.waitForTimeout(2500);
    // blank content = the page did not load
    const mainLen = () => page.evaluate(() => ((document.querySelector('main') || document.body).innerText || '').replace(/\s+/g, ' ').trim().length);
    let len = await mainLen();
    if (len < 120) { await page.waitForTimeout(6000); len = await mainLen(); }
    finding.blank = len < 120;
    // buttons present / disabled + expected-button check
    const btns = await page.evaluate(() => {
      const els = [...document.querySelectorAll('button,[role=button],a.btn,input[type=submit]')].filter(e => e.offsetParent);
      return { total: els.length, disabled: els.filter(e => e.disabled || e.getAttribute('aria-disabled') === 'true').length };
    });
    finding.buttons = btns.total;
    finding.disabled = btns.disabled;
    // expected buttons must be a VISIBLE + ENABLED button (a hidden/disabled one,
    // or a nav link that merely contains the text, does not count as working)
    for (const label of (spec.expect || [])) {
      let present = false;
      // a real "button" can be a <button> or a link/anchor acting as one — check both
      for (const role of ['button', 'link']) {
        try {
          const loc = page.getByRole(role, { name: label, exact: false });
          const n = await loc.count();
          for (let i = 0; i < n; i++) { const el = loc.nth(i); if (await el.isVisible() && await el.isEnabled().catch(() => true)) { present = true; break; } }
        } catch {}
        if (present) break;
      }
      if (!present) finding.missing.push(label);
    }
    // --- content health: things that confuse a human even when the page LOADS ---
    // (1) human-confusion: a raw link, an access-token blob, or developer junk
    //     dumped into the visible text. (2) freshness: an "as of / updated" date
    //     that is older than STALE_DAYS = the data stopped updating. Deterministic,
    //     no AI. Conservative thresholds so clean pages never cry wolf.
    const health = await page.evaluate((staleDays) => {
      const txt = ((document.querySelector('main') || document.body).innerText || '');
      const confuse = [], stale = [];
      // raw URL shown AS TEXT (>=60 chars) — clean UI shows a name/button, not a long link
      const longUrls = txt.match(/https?:\/\/[^\s]{60,}/g) || [];
      if (longUrls.length) confuse.push(`${longUrls.length} raw link(s) shown as text`);
      // access-token / JWT blob visible in text (contiguous run >= 40, outside any URL)
      const tokens = txt.replace(/https?:\/\/[^\s]+/g, ' ').match(/[A-Za-z0-9_-]{40,}/g) || [];
      if (tokens.length) confuse.push(`${tokens.length} access-token blob(s) in view`);
      // developer junk that must never reach a user
      const junk = [/\bundefined\b/, /\bNaN\b/, /\[object Object\]/, /\{\{/, /\}\}/]
        .filter(re => re.test(txt)).map(re => re.source.replace(/\\b|\\/g, ''));
      if (junk.length) confuse.push(`developer placeholder(s): ${junk.join(' ')}`);
      // freshness: find "as of / as at / updated / last run / reconciled ... <date>"
      const now = Date.now();
      const re = /(as of|as at|updated|last updated|last run|reconciled)[^0-9]{0,20}(\d{1,2}[-\/ ][A-Za-z]{3,9}[-\/ ]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}[-\/]\d{1,2}[-\/]\d{2,4})/gi;
      let m; const seen = new Set();
      while ((m = re.exec(txt))) {
        const label = m[1].toLowerCase(), raw = m[2];
        const d = new Date(raw.replace(/-/g, ' '));
        if (isNaN(d)) continue;
        const days = Math.floor((now - d.getTime()) / 86400000);
        const key = label + raw;
        if (days > staleDays && !seen.has(key)) { seen.add(key); stale.push(`"${label}" date ${raw} is ${days} days old`); }
      }
      return { confuse, stale };
    }, STALE_DAYS);
    finding.confuse = health.confuse;
    finding.stale = health.stale;
    // --- idea #2: slow-page alarm — is this screen much slower than it usually is? ---
    // Rolling history per page (same history file, key _timings). Judged only with
    // ≥3 earlier samples, and only when it is both > SLOW_FACTOR× the median AND
    // > 3 s outright, so a fast page wobbling 200→500 ms never pings anyone.
    if (!name.startsWith('_') && finding.ms > 0) {
      const hist = loadHistory(); hist._timings = hist._timings || {};
      const prev = (hist._timings[name] || []).filter(s => Date.now() - Date.parse(s.at) < 7 * 86400000);
      if (prev.length >= 3) {
        const med = [...prev.map(s => s.ms)].sort((a, b) => a - b)[Math.floor(prev.length / 2)];
        if (finding.ms > SLOW_FACTOR * med && finding.ms > SLOW_MIN_MS) finding.slow = `${(finding.ms / 1000).toFixed(1)}s to load vs its usual ${(med / 1000).toFixed(1)}s`;
      }
      hist._timings[name] = prev.concat([{ at: new Date().toISOString(), ms: finding.ms }]).slice(-60);
      saveHistory(hist);
    }
    // --- freshness by memory: is the tracked total actually moving run to run? ---
    // Walk back through this module's history while the total is unchanged; the
    // earliest unchanged run dates the freeze. Older than STALE_DAYS = frozen feed.
    if (spec.track) {
      const txt = await page.evaluate(() => ((document.querySelector('main') || document.body).innerText || ''));
      const m = txt.match(spec.track);
      if (m) {
        finding.total = m[1];
        const hist = loadHistory();
        const runs = (hist[name] || []).slice();
        const nowMs = Date.now();
        let earliest = nowMs;
        for (let i = runs.length - 1; i >= 0; i--) { if (runs[i].total !== finding.total) break; earliest = Date.parse(runs[i].at); }
        const frozenDays = Math.floor((nowMs - earliest) / 86400000);
        if (frozenDays > STALE_DAYS) finding.frozen = `total ${finding.total} unchanged for ${frozenDays} days — feed may have stopped`;
        runs.push({ at: new Date(nowMs).toISOString(), total: finding.total });
        hist[name] = runs.slice(-60);
        saveHistory(hist);
      }
    }
    finding.shot = join(OUT_DIR, `${name}.png`);
    await page.screenshot({ path: finding.shot, fullPage: true });
    finding.pageerrors = errs;
    finding.neterrors = nets;
    finding.ok = finding.auth && !finding.blank && errs.length === 0 && nets.length === 0
                 && finding.missing.length === 0 && finding.confuse.length === 0 && finding.stale.length === 0 && !finding.frozen;
  } catch (e) {
    finding.pageerrors = errs.concat(['NAV_FAIL: ' + String(e).slice(0, 160)]);
    finding.neterrors = nets;
  } finally {
    await page.close();
  }
  return finding;
}

// --- plain-English review via the local Fable gateway (best-effort, off-sub) --
// On Windows the key lives in %USERPROFILE%\.omni-gateway-key (one line: the key,
// or MASTER_KEY=...). No key = no paragraph; the PASS/FAIL table still prints.
function gatewayKey() {
  if (process.env.LITELLM_MASTER_KEY) return process.env.LITELLM_MASTER_KEY;
  try {
    const t = readFileSync(join(homedir(), '.omni-gateway-key'), 'utf8');
    const m = t.match(/^\s*(?:LITELLM_MASTER_KEY|MASTER_KEY)\s*=\s*["']?([^"'\n]+)/m);
    return (m ? m[1] : t).trim() || null;
  } catch {}
  return null;
}
// Clever brain, off-subscription: DeepSeek (primary) -> Gemini (secondary) ->
// free local Ollama (last). Tries each in order until one answers. Only the
// findings text (page names/routes/errors) leaves the box — never screenshots
// and never customer/staff PII.
const REVIEW_MODELS = (process.env.OMNI_QC_MODELS || 'judge-deepseek,gemini').split(',').map(s => s.trim()).filter(Boolean);
const SYS = 'You are a QC reviewer for an insurance ERP. Given raw findings, write ONE short plain-English paragraph for a non-technical CFO: what is broken and the most likely cause, and the ONE thing to check first. No jargon. Under 90 words.';

async function callGateway(model, user, key) {
  const r = await fetch('http://localhost:4000/v1/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}` },
    body: JSON.stringify({ model, temperature: 0, messages: [{ role: 'system', content: SYS }, { role: 'user', content: user }] }),
    signal: AbortSignal.timeout(30000),
  });
  if (!r.ok) throw new Error(`gw ${model} ${r.status}`);
  const j = await r.json();
  const t = j.choices?.[0]?.message?.content?.trim();
  if (!t) throw new Error(`gw ${model} empty`);
  return t;
}
async function callOllama(user) {
  const r = await fetch('http://localhost:11434/api/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: 'llama3.1:8b', stream: false, messages: [{ role: 'system', content: SYS }, { role: 'user', content: user }] }),
    signal: AbortSignal.timeout(20000),
  });
  if (!r.ok) throw new Error('ollama ' + r.status);
  const j = await r.json();
  return j.message?.content?.trim() || null;
}
async function review(findings) {
  // PII SAFETY: this text goes to an EXTERNAL model. Send only the page name and
  // the issue TYPE + counts — never raw error strings, URLs or button labels,
  // which can carry customer names / policy numbers / emails. Full detail stays
  // LOCAL in the console + last-run.json for the CFO's own debugging.
  const fails = findings.filter(f => !f.ok).map(f => {
    const why = [];
    if (!f.auth) why.push('session expired (login redirect)');
    if (f.blank) why.push('page rendered blank / no content');
    if (f.pageerrors.length) why.push(`${f.pageerrors.length} JavaScript error(s)`);
    if (f.neterrors?.length) why.push(`${f.neterrors.length} failed server call(s) (5xx)`);
    if (f.missing.length) why.push(`${f.missing.length} expected button(s) missing`);
    if (f.confuse?.length) why.push(`${f.confuse.length} human-confusion issue(s) (raw links/tokens/placeholders shown to the user)`);
    if (f.stale?.length) why.push(`${f.stale.length} stale-data issue(s) (an "as of/updated" date is old — the feed may have stopped)`);
    if (f.frozen) why.push('tracked total has not changed for several days (the feed behind the page may have stopped)');
    return `- ${f.name}: ${why.join('; ')}`;
  }).join('\n');
  if (!fails) return { text: 'All checked pages loaded and their key buttons are present.', brain: 'rule' };
  const user = `Broken pages found by the QC sweep:\n${fails}`;
  const key = gatewayKey();
  if (key) for (const m of REVIEW_MODELS) {
    try { return { text: await callGateway(m, user, key), brain: m }; } catch { /* next */ }
  }
  try { const t = await callOllama(user); if (t) return { text: t, brain: 'ollama:llama3.1' }; } catch {}
  return { text: null, brain: 'none' };
}

// --- report to Android via openclaw Telegram --------------------------------
function tgTarget() {
  if (process.env.OMNI_QC_TG_TARGET) return process.env.OMNI_QC_TG_TARGET.trim();
  try { const v = readFileSync(join(homedir(), '.omni-qc-tg-target'), 'utf8').trim(); if (v) return v; } catch {}
  return null;
}
// Windows has no openclaw, so this talks to Telegram directly. It needs BOTH
// %USERPROFILE%\.omni-qc-tg-bot (the bot token) and .omni-qc-tg-target (chat id).
// Missing either = the message is printed here instead of sent. Nothing else changes.
function tgBot() {
  if (process.env.OMNI_QC_TG_BOT) return process.env.OMNI_QC_TG_BOT.trim();
  try { const v = readFileSync(join(homedir(), '.omni-qc-tg-bot'), 'utf8').trim(); if (v) return v; } catch {}
  return null;
}
async function reportTelegram(text) {
  const target = tgTarget(), bot = tgBot();
  if (!target || !bot) { console.log('\n[report] no Telegram bot/target set — message would be:\n' + text); return; }
  try {
    const r = await fetch(`https://api.telegram.org/bot${bot}/sendMessage`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: target, text }), signal: AbortSignal.timeout(20000),
    });
    console.log(r.ok ? '[report] sent to Telegram chat ' + target : '[report] Telegram send FAILED: HTTP ' + r.status);
  } catch (e) { console.error('[report] Telegram send FAILED: ' + String(e).slice(0, 160)); }
}

// --- idea #6: self-filing — one bug on the Omni board per broken page ---------
// Uses the scoped 'qc-bot' ApiKey (~/.omni-qc-bug-token; may only touch the bug
// board + the owed-feedback read). Dedupes: no second report while one is still
// open for the same page + issue signature. Description is plain findings — the
// same PII-safe text review() sends — plus the page capture as the screenshot.
function bugKey() { try { return readFileSync(join(homedir(), '.omni-qc-bug-token'), 'utf8').trim(); } catch { return null; } }
function issueSig(f) {
  const s = [];
  if (!f.auth) s.push('login-redirect'); if (f.blank) s.push('blank'); if (f.pageerrors.length) s.push('js-error');
  if (f.neterrors?.length) s.push('server-5xx'); if (f.missing.length) s.push('missing-button');
  if (f.confuse?.length) s.push('confusing-content'); if (f.stale?.length) s.push('stale-date'); if (f.frozen) s.push('frozen-total');
  return s.join('+') || 'unknown';
}
async function fileBugs(findings) {
  const key = bugKey();
  if (!key) { console.log('[bugs] no ~/.omni-qc-bug-token — not filing'); return; }
  const H = { Authorization: `ApiKey ${key}` };
  // board statuses: new / triaged / in_progress are "still open"; resolved / wont_fix are done
  let open = [];
  try { const r = await fetch(`${BASE}/api/v1/bug-reports/`, { headers: H, signal: AbortSignal.timeout(20000) }); const j = await r.json(); open = (j.results || []).filter(b => !['resolved', 'wont_fix'].includes(b.status)); } catch {}
  for (const f of findings.filter(x => !x.ok)) {
    const tag = `[QC] ${f.name} — ${issueSig(f)}`;
    if (open.some(b => (b.description || '').startsWith(tag))) { console.log(`[bugs] ${f.name}: already open — not refiling`); continue; }
    const why = [];
    if (!f.auth) why.push('the page bounced to the login screen'); if (f.blank) why.push('the page rendered blank');
    if (f.pageerrors.length) why.push(`${f.pageerrors.length} JavaScript error(s) fired`); if (f.neterrors?.length) why.push(`${f.neterrors.length} data call(s) failed with a server 5xx`);
    if (f.missing.length) why.push(`${f.missing.length} expected button(s) were missing`); for (const c of (f.confuse || [])) why.push(c);
    for (const s of (f.stale || [])) why.push(s); if (f.frozen) why.push(f.frozen);
    const desc = `${tag}\n\nFiled automatically by the Omni QC agent (read-only auditor) at ${new Date().toISOString()} after loading ${f.route} as the QA identity. ` +
      `What it found: ${why.join('; ')}. The attached capture is the page exactly as it rendered during the check, taken with the read-only session so nothing was changed. ` +
      `Please open the page as a normal user, confirm the same behaviour, then fix or close this report with the reason so the next nightly sweep can re-verify it.`;
    const fd = new FormData();
    fd.append('description', desc); fd.append('page_url', `${(SITES[f.site] || SITES.omni).base}${f.route}`);
    try { fd.append('screenshots', new Blob([readFileSync(f.shot)], { type: 'image/png' }), `${f.name}.png`); } catch {}
    try {
      const r = await fetch(`${BASE}/api/v1/bug-reports/`, { method: 'POST', headers: H, body: fd, signal: AbortSignal.timeout(30000) });
      console.log(`[bugs] ${f.name}: filed → HTTP ${r.status}` + (r.ok ? '' : ' ' + (await r.text()).slice(0, 160)));
    } catch (e) { console.log(`[bugs] ${f.name}: filing failed — ${String(e).slice(0, 120)}`); }
  }
}

// --- main -------------------------------------------------------------------
const b = await chromium.launch();

// UniCoin sign-in: the QA manager FIXTURE (qa.manager@ — no real staff), credentials
// only in ~/.unicoin-qa-cred (0600). Same recipe as uc-qc-clickthrough.mjs: same-origin
// /api/auth/login, token into sessionStorage 'unicoin.token' + the uc_session cookie.
// The password never enters findings, logs or last-run.json.
async function unicoinToken() {
  let cred;
  try { cred = JSON.parse(readFileSync(join(homedir(), '.unicoin-qa-cred'), 'utf8')); }
  catch { console.error('NO_UC_CRED: ~/.unicoin-qa-cred missing'); return ''; }
  const r = await fetch(`${UC_BASE}/api/auth/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: cred.email, password: cred.password }), signal: AbortSignal.timeout(20000),
  }).catch(() => null);
  if (!r || r.status !== 200) { console.error(`UC_LOGIN_FAILED: HTTP ${r ? r.status : 'no response'}`); return ''; }
  return (await r.json()).access_token || '';
}

// one browser context per site, created on first use
const ctxs = {};
async function ctxFor(siteKey) {
  if (ctxs[siteKey]) return ctxs[siteKey];
  const ctx = await b.newContext({ viewport: { width: WIDTH, height: 900 }, deviceScaleFactor: 2, colorScheme: DARK ? 'dark' : 'light' });
  if (siteKey === 'unicoin') {
    const tok = await unicoinToken();
    await ctx.addInitScript(t => { try { sessionStorage.setItem('unicoin.token', t); } catch {} }, tok);
    await ctx.addCookies([{ name: 'uc_session', value: '1', url: UC_BASE, sameSite: 'Strict' }]);
  } else {
    await ctx.addInitScript(t => localStorage.setItem('alpha_token', t), TOKEN);
    // Optional extra localStorage (e.g. the /qa read-only view flag) via OMNI_LS_EXTRA,
    // same contract as omni-shot.mjs. No invented flags.
    if (process.env.OMNI_LS_EXTRA) {
      const extra = JSON.parse(process.env.OMNI_LS_EXTRA);
      await ctx.addInitScript(kv => { for (const [k, v] of Object.entries(kv)) localStorage.setItem(k, v); }, extra);
    }
  }
  ctxs[siteKey] = ctx;
  return ctx;
}

const findings = [];
for (const m of modules) findings.push(await checkPage(await ctxFor(TARGETS[m].site || 'omni'), m, TARGETS[m]));
await b.close();

const pass = findings.filter(f => f.ok).length;
const fail = findings.length - pass;
console.log(`\n===== OMNI QC =====  ${pass} PASS / ${fail} FAIL`);
for (const f of findings) {
  const bits = [];
  if (!f.auth) bits.push('AUTH-FAIL');
  if (f.blank) bits.push('BLANK');
  if (f.pageerrors.length) bits.push(`${f.pageerrors.length} JS-err`);
  if (f.neterrors?.length) bits.push(`${f.neterrors.length} net-err`);
  if (f.missing.length) bits.push(`missing:${f.missing.join('/')}`);
  if (f.confuse?.length) bits.push(`CONFUSING(${f.confuse.length})`);
  if (f.stale?.length) bits.push(`STALE(${f.stale.length})`);
  if (f.frozen) bits.push('FROZEN');
  if (f.slow) bits.push('SLOW');
  console.log(`${f.ok ? '✅' : '❌'} ${f.name.padEnd(16)} ${String(f.buttons).padStart(3)} btns ${String(f.ms).padStart(6)}ms  ${bits.join(' ') || 'ok'}  -> ${f.shot}`);
  if (f.slow) console.log(`     🐢 slow: ${f.slow}`);
  for (const c of (f.confuse || [])) console.log(`     ⚠️  confusing: ${c}`);
  for (const s of (f.stale || [])) console.log(`     ⏰ stale: ${s}`);
  if (f.frozen) console.log(`     🧊 frozen: ${f.frozen}`);
  else if (f.total) console.log(`     📈 tracked total: BWP ${f.total}`);
}
const verdict = await review(findings);
if (verdict.text) console.log(`\n--- review (brain: ${verdict.brain}) ---\n` + verdict.text);
// A canary-only run is a detector self-test, not a verdict on Omni — it must never
// overwrite last-run.json, or the morning brief reports the canary as a broken page
// (caught by /qctest, 7-Sep-2026). Canaries record to last-canary.json instead.
const canaryOnly = modules.every(m => m.startsWith('_'));
writeFileSync(join(OUT_DIR, canaryOnly ? 'last-canary.json' : 'last-run.json'), JSON.stringify({ at: new Date().toISOString(), pass, fail, verdict, findings }, null, 2));

// idea #5: one line per real run in the ledger the Friday scorecard reads
if (!canaryOnly) {
  try {
    mkdirSync(dirname(HIST_FILE), { recursive: true, mode: 0o700 });
    const line = { at: new Date().toISOString(), modules: modules.length, pass, fail,
                   fails: findings.filter(f => !f.ok).map(f => f.name), slow: findings.filter(f => f.slow).map(f => f.name) };
    writeFileSync(join(dirname(HIST_FILE), 'runs.jsonl'), JSON.stringify(line) + '\n', { flag: 'a', mode: 0o600 });
  } catch {}
}
const slowOnes = findings.filter(f => f.slow);
if (FILE_BUGS && fail > 0) await fileBugs(findings);
if (REPORT && !(ONLY_FAIL && fail === 0 && slowOnes.length === 0)) {
  const head = fail === 0 ? `✅ Omni QC: all ${pass} pages OK` : `❌ Omni QC: ${fail} broken, ${pass} OK`;
  const slowLine = slowOnes.length ? `\n🐢 Slow today: ${slowOnes.map(f => `${f.name} (${f.slow})`).join('; ')}` : '';
  await reportTelegram(head + slowLine + (verdict.text ? '\n\n' + verdict.text : ''));
}
process.exit(fail === 0 ? 0 : 1);
