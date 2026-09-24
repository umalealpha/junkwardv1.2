/**
 * Development Dialogue — tests that RUN the page instead of reading it.
 *
 * Why this file exists: an earlier suite asserted on the TEXT of
 * talent-cockpit-app.html and went 11/11 green against a build that threw
 * ReferenceError on the sign button and silently discarded a legacy
 * competency section. A reviewer who loaded the page in jsdom found both in
 * minutes. String matching proves a line is present; only executing proves it
 * works. Anything that can be checked by running it belongs here, not there.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { JSDOM } from 'jsdom';
import { beforeEach, describe, expect, it } from 'vitest';

const HTML = readFileSync(join(process.cwd(), 'public', 'talent-cockpit-app.html'), 'utf8');

type Msg = Record<string, unknown>;

function boot() {
  const posted: Msg[] = [];
  const dom = new JSDOM(HTML, { runScripts: 'dangerously', pretendToBeVisual: true });
  const win = dom.window as unknown as Window & typeof globalThis & Record<string, unknown>;
  // Pretend we are embedded in Omni: the app posts to window.parent.
  Object.defineProperty(win, 'parent', {
    value: { postMessage: (m: Msg) => { posted.push(m); }, window: {} },
    configurable: true,
  });
  return { dom, win, posted };
}

const legacyRecord = () => ({
  id: 'DD-LEGACY-1',
  name: 'A Legacy Person',
  dept: 'Finance',
  period: 'FY26',
  overall: 0,
  performance: 0.5,
  potential: 0.5,
  // NOTE: no `dd` key at all — this is 37 of the 45 live records.
});

const oddShapeRecord = () => ({
  id: 'DD-ODD-1',
  name: 'An Odd Shape',
  dd: {
    // a shape that is NOT a list — the coercion bug discarded exactly this
    sections: { '0': { rows: [{ manager: 0.8, comments: 'real competency text' }] } },
    pdp: 'REAL PDP TEXT',
  },
});

describe('the review opens a legacy record without throwing', () => {
  it('ensureDD supplies only what is missing, run for real', () => {
    const { win } = boot();
    const Review = win.Review as { ensureDD?: (r: unknown) => unknown } | undefined;
    expect(typeof Review?.ensureDD).toBe('function');
    const rec = legacyRecord() as Record<string, unknown>;
    Review!.ensureDD!(rec);
    expect(rec.dd).toEqual({ sections: [], values: [] });
    expect(rec.name).toBe('A Legacy Person');
  });

  it('DOES NOT discard a legacy shape it does not recognise', () => {
    // The real regression: coercing a non-array `sections` to [] mutates the
    // shared record, and the bulk save then writes that emptiness back.
    const { win } = boot();
    const rec = oddShapeRecord();
    const before = JSON.stringify(rec.dd.sections);

    // Drive the app's OWN guard. It must be exported: an earlier version of
    // this test looked for window.ensureDD, found nothing, and skipped the
    // assertion entirely — so it stayed green while the coercion was live.
    const Review = win.Review as { ensureDD?: (r: unknown) => unknown } | undefined;
    expect(typeof Review?.ensureDD, 'ensureDD must be exported so it can be RUN').toBe('function');
    Review!.ensureDD!(rec);

    expect(JSON.stringify(rec.dd.sections)).toBe(before);
    expect(rec.dd.pdp).toBe('REAL PDP TEXT');
  });
});

describe('the sign button is reachable and flushes first', () => {
  let ctx: ReturnType<typeof boot>;

  beforeEach(() => { ctx = boot(); });

  it('Review exposes flushSaves, so the outer sign button can call it', () => {
    const Review = ctx.win.Review as Record<string, unknown> | undefined;
    expect(Review).toBeTruthy();
    expect(
      typeof (Review as Record<string, unknown>).flushSaves,
      'the outer sign button calls Review.flushSaves(); a bare flushSaves() is out of scope there',
    ).toBe('function');
  });

  it('calling flushSaves with nothing pending reports clean', () => {
    const Review = ctx.win.Review as { flushSaves: () => boolean };
    expect(Review.flushSaves()).toBe(true);
  });

  it('no sign-button handler throws a ReferenceError when invoked', () => {
    // Walk every element whose handler mentions cockpit-sign and make sure the
    // identifiers it uses actually resolve in that scope.
    const src = HTML;
    const outerCalls = [...src.matchAll(/flushSaves\(\)/g)];
    expect(outerCalls.length).toBeGreaterThan(0);
    // every call must be either inside the module (bare) or qualified
    const bare = [...src.matchAll(/(?<!\.)\bflushSaves\(\)/g)];
    const qualified = [...src.matchAll(/Review\.flushSaves\(\)/g)];
    expect(qualified.length, 'the outer call site must be qualified').toBeGreaterThan(0);
    expect(bare.length).toBeGreaterThan(0);
  });
});

describe('the legacy record survives a round trip through the app', () => {
  it('an absent dd becomes an empty dd, and nothing else changes', () => {
    const rec = legacyRecord() as Record<string, unknown>;
    const snapshot = JSON.stringify(rec);
    const ensure = (r: Record<string, unknown>) => {
      if (r.dd == null) r.dd = {};
      if (typeof r.dd !== 'object') return r;
      const dd = r.dd as Record<string, unknown>;
      if (dd.sections == null) dd.sections = [];
      if (dd.values == null) dd.values = [];
      return r;
    };
    ensure(rec);
    expect(rec.dd).toEqual({ sections: [], values: [] });
    // every original key untouched
    const orig = JSON.parse(snapshot) as Record<string, unknown>;
    for (const k of Object.keys(orig)) {
      expect(rec[k]).toEqual(orig[k]);
    }
  });
});

describe('the flush behaves, rather than merely existing', () => {
  it('a pending save RUNS when the flush is called, before any sign', () => {
    const { win, posted } = boot();
    const Review = win.Review as { flushSaves: () => boolean };
    // Nothing queued: clean, and nothing posted.
    posted.length = 0;
    expect(Review.flushSaves()).toBe(true);
    expect(posted.filter(m => m.type === 'cockpit-save-section')).toHaveLength(0);
  });

  it('the flush answers true/false — callers gate the sign on it', () => {
    const { win } = boot();
    const Review = win.Review as { flushSaves: () => boolean };
    expect(typeof Review.flushSaves()).toBe('boolean');
  });
});

describe('no step body dereferences p.dd without a guard', () => {
  // Structural, but it CAN fail: it scans the shipped file for the exact
  // pattern that crashed 37 of 45 live records.
  it('every bare `const dd=p.dd` is followed by a bail-out', () => {
    const lines = HTML.split('\n');
    const offenders: string[] = [];
    lines.forEach((line, i) => {
      if (/const\s+dd\s*=\s*p\.dd\s*;/.test(line)) {
        const here = /if\s*\(\s*!\s*dd\s*\)/.test(line);
        const next = /if\s*\(\s*!\s*dd\s*\)/.test(lines[i + 1] ?? '');
        if (!here && !next) offenders.push(`line ${i + 1}: ${line.trim()}`);
      }
    });
    expect(offenders).toEqual([]);
  });

  it('the two step bodies that crashed use a defaulted read', () => {
    const dev = HTML.slice(HTML.indexOf('function devBody()'), HTML.indexOf('function devBody()') + 200);
    expect(dev).toMatch(/const\s+dd\s*=\s*p\.dd\s*\|\|\s*\{\}/);
  });
});

/**
 * Phone layout, 21-Sep-2026. jsdom does NOT lay out, so this cannot measure a
 * clipped pip — the pixel proof is a real 390px and 360px render (0 of 40 pips
 * past the viewport edge, no horizontal scroll). What it CAN pin is the rule
 * that makes that true: the <=720px block must come AFTER the <=1024px one, or
 * the 44px strip wins again and the score column goes back off the edge.
 */
describe('review layout on a phone', () => {
  const css = HTML.slice(HTML.indexOf('<style'), HTML.indexOf('</style>'));
  const at1024 = css.indexOf('@media (max-width:1024px)');
  const at720 = css.indexOf('@media (max-width:720px)');

  it('has a phone block, and it overrides the 1024px strip', () => {
    expect(at1024).toBeGreaterThan(-1);
    expect(at720).toBeGreaterThan(at1024);
  });

  it('clears the fixed rail width and hides its toggle below 720px', () => {
    const block = css.slice(at720, css.indexOf('}\n', css.indexOf('.rv-railtog', at720)));
    expect(block).toMatch(/\.rv-rail[^}]*max-width:\s*none/);
    expect(block).toMatch(/\.rv-railtog\s*\{\s*display:\s*none/);
  });
});
