/**
 * Types for `next/dist/compiled/path-to-regexp`.
 *
 * `cacheHeaders.test.ts` compiles the real exported `NO_STORE_PAGES` pattern
 * with NEXT'S OWN matcher, deliberately: a test that re-implemented the
 * matching could drift from what the server actually does, and the whole point
 * of that test is that a drift cannot hide. But Next vendors this dependency
 * inside `dist/compiled` with no `.d.ts` beside it, so TypeScript reports
 * `TS7016: Could not find a declaration file` and the typecheck fails on an
 * import that is correct.
 *
 * The alternatives were worse: importing the standalone `path-to-regexp`
 * package would test a DIFFERENT matcher from the one Next runs, and a
 * `@ts-expect-error` would silence the checker on a line whose return value the
 * test then casts. Declaring the one function actually used keeps the real
 * import and keeps it typed.
 *
 * Only the signature this repo uses is declared. If more of the module is ever
 * needed, add it here rather than widening this to `any`.
 */
declare module 'next/dist/compiled/path-to-regexp' {
  /**
   * Compile a path pattern (or a list of them) to a RegExp.
   *
   * Next's vendored copy is path-to-regexp v6, whose `pathToRegexp` returns a
   * RegExp directly. The caller casts the result, so the loose return type here
   * stays honest about the vendored version being an implementation detail.
   */
  export function pathToRegexp(
    path: string | RegExp | Array<string | RegExp>,
    keys?: Array<{ name: string | number; prefix: string; suffix: string; pattern: string; modifier: string }>,
    options?: {
      sensitive?: boolean
      strict?: boolean
      end?: boolean
      start?: boolean
      delimiter?: string
      endsWith?: string
      encode?: (value: string) => string
      prefixes?: string
    },
  ): RegExp
}
