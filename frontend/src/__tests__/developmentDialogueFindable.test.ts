/**
 * CFO 19-Sep-2026: "When I search in development dialog, it is not coming.
 * It's hard to find... It is one of the areas my boss likes to look at."
 *
 * The central all-employee Development Dialogue (the 9-grid, /hris/talent-cockpit)
 * was labelled "Talent Cockpit" in search and had no sidebar link at all. These
 * tests pin BOTH fixes: it must be reachable from the sidebar, and typing what
 * he types must put it first.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { score } from '../components/CommandPalette';

const read = (p: string) => readFileSync(join(process.cwd(), p), 'utf8');

interface Dest { label: string; href: string; group: string; kw: string }

function destinations(): Dest[] {
  const src = read('src/components/CommandPalette.tsx');
  const rows = src.matchAll(
    /\{\s*label:\s*'([^']+)',\s*href:\s*'([^']+)',\s*group:\s*'([^']+)'(?:,\s*kw:\s*'([^']*)')?/g);
  return [...rows].map(([, label, href, group, kw]) => ({ label, href, group, kw: kw ?? '' }));
}

describe('the all-employee Development Dialogue is findable', () => {
  it('has a sidebar link', () => {
    expect(read('src/lib/navModules.ts')).toContain("href: '/hris/talent-cockpit'");
  });

  it.each([
    'development dialog',
    'development dialogue',
    'dialogue all employees',
    '9 grid',
    'nine grid',
  ])('searching "%s" ranks it first', (query) => {
    const ranked = destinations()
      .map((d) => ({ d, s: score(d, query) }))
      .filter((x) => x.s >= 0)
      .sort((a, b) => b.s - a.s);
    expect(ranked[0]?.d.href).toBe('/hris/talent-cockpit');
  });

  it('is named the way the CFO says it, not "Talent Cockpit"', () => {
    const dest = destinations().find((d) => d.href === '/hris/talent-cockpit');
    expect(dest?.label).toBe('Development Dialogue (All Employees)');
  });
});
