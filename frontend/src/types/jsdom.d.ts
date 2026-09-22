/**
 * Minimal types for `jsdom`, which ships without them and has no @types package
 * installed here. Same approach as next-compiled-path-to-regexp.d.ts: declare the
 * small surface the tests actually use rather than add a dependency.
 *
 * Used by src/lib/__tests__/talentCockpitFilters.test.ts, which runs the real
 * public/talent-cockpit-app.html so the Development Dialogue filters are tested
 * against the shipped page, not a copy of it.
 */
declare module 'jsdom' {
  export interface JSDOMOptions {
    runScripts?: 'dangerously' | 'outside-only';
    pretendToBeVisual?: boolean;
    url?: string;
  }
  export class JSDOM {
    constructor(html: string, options?: JSDOMOptions);
    readonly window: Window & typeof globalThis & Record<string, unknown>;
  }
}
