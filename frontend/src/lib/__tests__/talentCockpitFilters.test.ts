/**
 * CFO 19-Sep-2026 on the Development Dialogue (All Employees): "improve it,
 * filter, 10 different animations, etc".
 *
 * These drive the REAL public/talent-cockpit-app.html in jsdom: feed it the
 * cockpit-init message the parent page sends, then filter it the way the CEO
 * would. They exist because a filter bug here hides people from a board-level
 * review, and because the filters must never touch the saved data.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { JSDOM } from 'jsdom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const HTML = readFileSync(join(process.cwd(), 'public/talent-cockpit-app.html'), 'utf8');

const person = (id: number, over: Record<string, unknown> = {}) => ({
  id,
  name: `Person ${id}`,
  initials: `P${id}`,
  position: 'Accountant',
  dept: 'typed',
  deptCanonical: 'Finance',
  company: 'ADIC',
  grade: 'C-3',
  manager: 'Kago Boss',
  periodLabel: 'FY27',
  performance: 0.8,
  potential: 0.8,
  overall: 72,
  color: '#2F55A8',
  targets: [],
  signoff: {},
  ...over,
});

let dom: JSDOM;
let win: any;

function boot(people: unknown[]) {
  dom = new JSDOM(HTML, {
    runScripts: 'dangerously',
    pretendToBeVisual: true, // the page uses requestAnimationFrame for the KPI count-up
    url: 'https://omni.test/hris/talent-cockpit',
  });
  win = dom.window as any;
  win.dispatchEvent(new win.MessageEvent('message', {
    data: { type: 'cockpit-init', people, can_manage: true },
  }));
}

const pins = () => [...win.document.querySelectorAll('.pin')];
const shownPins = () => pins().filter((p: Element) => !p.classList.contains('dimmed'));
const countText = () => win.document.querySelector('.cfcount')?.textContent ?? '';

describe('Development Dialogue (All Employees) — filters', () => {
  beforeEach(() => boot([
    person(1),
    person(2, { name: 'Bob Claims', deptCanonical: 'Claims', company: 'UNI', grade: 'C-1', overall: null, manager: 'Wangu Moses' }),
    person(3, { name: 'Carol Star', performance: 0.9, potential: 0.9, signoff: { manager: 'x' } }),
  ]));

  it('draws one pin per person and a filter bar above every view', () => {
    expect(pins()).toHaveLength(3);
    expect(win.document.getElementById('cfbar')?.children.length).toBeGreaterThan(3);
  });

  it('search narrows to the person typed, and says "of" the full headcount', () => {
    win.setFilter('q', 'bob');
    expect(shownPins()).toHaveLength(1);
    expect(countText()).toContain('of 3');
  });

  it('department, entity and grade each filter', () => {
    win.setFilter('dept', 'Claims');
    expect(shownPins()).toHaveLength(1);
    win.clearFilters();
    win.setFilter('company', 'ADIC');
    expect(shownPins()).toHaveLength(2);
    win.clearFilters();
    win.setFilter('grade', 'C-1');
    expect(shownPins()).toHaveLength(1);
  });

  it('unscored finds the person with no score', () => {
    win.setFilter('scored', 'unscored');
    expect(shownPins()).toHaveLength(1);
    expect(shownPins()[0].getAttribute('aria-label')).toContain('Bob Claims');
  });

  it('a filtered-out person is faded, never removed, so drag and save are untouched', () => {
    win.setFilter('dept', 'Claims');
    expect(pins()).toHaveLength(3);
    const dimmed = pins().filter((p: Element) => p.classList.contains('dimmed'));
    expect(dimmed).toHaveLength(2);
    expect((dimmed[0] as HTMLElement).style.pointerEvents).not.toBe('auto');
  });

  it('KPI headcount follows the filter (after the count-up settles)', async () => {
    win.setFilter('dept', 'Claims');
    // The tile counts up over 400ms (animation 6), so read it once it settles.
    await vi.waitFor(
      () => expect(win.document.querySelector('.kpi .val')?.textContent).toBe('1'),
      { timeout: 2000, interval: 25 },
    );
  });

  it('empty results explain themselves instead of showing a blank page', () => {
    win.setFilter('q', 'nobody at all');
    expect(win.document.getElementById('gridEmpty')?.textContent).toContain('Nothing matches');
    win.setView('people');
    expect(win.document.getElementById('pgrid')?.textContent).toContain('Nothing matches');
  });

  it('keeps the filter in the url so the CEO can bookmark a view', () => {
    win.setFilter('dept', 'Claims');
    expect(win.location.hash).toContain('dept=Claims');
  });

  it('every person is a real control: pins are buttons with a label', () => {
    const pin = pins()[0] as HTMLElement;
    expect(pin.tagName).toBe('BUTTON');
    expect(pin.getAttribute('aria-label')).toMatch(/Person 1/);
  });
});

describe('keyboard', () => {
  beforeEach(() => boot([person(1), person(2, { name: 'Bob Claims' })]));

  it('opening a person launches the full-screen live review, and Back closes to a neutral curtain (board dd515fa8)', () => {
    const pin = pins()[0] as HTMLElement;
    pin.focus();
    // Opening a person now launches the full-screen review, not the 560px drawer.
    win.openDrawer(1);
    const review = win.document.getElementById('review') as HTMLElement;
    expect(review.classList.contains('on')).toBe(true);

    // Back mid-review must NOT flash the cockpit's names — it shows a neutral
    // "Review closed" curtain with a deliberate way back (privacy curtain, T7).
    win.Review.close();
    expect(review.classList.contains('on')).toBe(true);
    expect(review.querySelector('#rvExit')).toBeTruthy();
    expect(review.textContent).toContain('Review closed');

    // Leaving the curtain returns to the cockpit.
    (win.document.getElementById('rvExit') as HTMLElement).click();
    expect(review.classList.contains('on')).toBe(false);
  });

  it('a person hidden by the filter cannot be moved or tabbed to', () => {
    win.setFilter('q', 'Person 1');
    const hidden = pins().find((p: Element) => p.classList.contains('dimmed')) as HTMLElement;
    const before = hidden.style.left;
    hidden.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(hidden.style.left).toBe(before);
    expect(hidden.tabIndex).toBe(-1);
  });

  it('an arrow key moves a pin and saves', () => {
    const pin = pins()[0] as HTMLElement;
    const before = pin.style.left;
    pin.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(pin.style.left).not.toBe(before);
    expect(win.document.getElementById('gridStatus')?.textContent).toContain('Saved');
  });
});

describe('with no dialogues at all', () => {
  it('says so instead of drawing an empty grid', () => {
    boot([]);
    expect(win.document.getElementById('gridEmpty')?.textContent).toContain('No dialogues yet');
  });
});
