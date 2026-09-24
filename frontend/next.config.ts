import type { NextConfig } from 'next'
import { randomBytes } from 'node:crypto'

// A unique id per build. `generateBuildId` writes it to .next/BUILD_ID (the
// Dockerfile copies that to public/build-id.txt, served no-store), and `env`
// bakes the SAME value into the client bundle as NEXT_PUBLIC_BUILD_ID. The
// UpdateChecker compares the baked-in id against the served file, so an
// already-open OR cache-stale tab detects a new deploy even on its first read
// (the previous first-fetch baseline missed a bundle served from cache after a
// deploy — the "changes don't show until you refresh" bug). Fresh random each
// build; an unchanged (docker-cached) rebuild keeps the same id, so no false
// nag. OMNI_BUILD_ID env override is available if a deploy wants to pin it.
const OMNI_BUILD_ID = process.env.OMNI_BUILD_ID || randomBytes(8).toString('hex')

// Backend URL is read from env so the same image runs in dev and prod.
// In docker-compose: NEXT_PUBLIC_API_BASE=http://backend:8000 (server-side rewrites).
// On AWS: point this at the ECS/RDS-fronted Django service or ALB DNS.
const backendUrl =
  process.env.BACKEND_INTERNAL_URL ||
  process.env.NEXT_PUBLIC_API_BASE ||
  'http://127.0.0.1:8000'

// The one source pattern that marks every HTML PAGE as never-store while
// leaving hashed build output and public/ files cacheable. Exported so
// frontend/src/app/__tests__/cacheHeaders.test.ts pins the real value rather
// than a copy that can drift. See the long note in headers() below.
export const NO_STORE_PAGES = '/:path((?!_next/|api/|api-token-auth/|trpc/)[^.]*)'

const nextConfig: NextConfig = {
  output: 'standalone',
  // Deploy-detection: pin the build id and expose it to the client (see the
  // OMNI_BUILD_ID note above). Both point at the same per-build value.
  generateBuildId: async () => OMNI_BUILD_ID,
  env: { NEXT_PUBLIC_BUILD_ID: OMNI_BUILD_ID },
  skipTrailingSlashRedirect: true,
  eslint: {
    // Lint runs in CI / pre-commit, not as a deploy gate.
    ignoreDuringBuilds: true,
  },
  typescript: {
    // Build gate ENABLED 2026-05-26 (CR-03 readiness-report finding):
    // the previous `ignoreBuildErrors: true` was hiding 50+ real type
    // mismatches — stale API field renames, dead-stub argument signatures,
    // missing Theme keys — every one of which rendered as `undefined` at
    // runtime. All cleared in the same commit. Keep this false so the next
    // drift surfaces in CI, not on Pako's screen.
    ignoreBuildErrors: false,
  },
  async rewrites() {
    return [
      // IT Help Desk — Ikanyeng Sechele's SharePoint/Power-Automate SPA, brought
      // under omni as a static app (frontend/public/helpdesk/index.html). Serve
      // /helpdesk and /helpdesk/ from the static file for a clean URL. NB: the app's
      // MSAL redirectUri = origin + pathname, so the Entra app "Alpha Direct IT Help
      // Desk" must register https://omni.alphadirect.co.bw/helpdesk/ as an SPA
      // redirect URI for sign-in to complete (Azure app-owner action — pending).
      { source: '/helpdesk', destination: '/helpdesk/index.html' },
      { source: '/helpdesk/', destination: '/helpdesk/index.html' },
      // CFO Snapshot — moved to a real gated page at app/(dashboard)/cfo-snapshot
      // (security fix 2026-07-14: the old public static file bypassed login
      // entirely). /cfo-snapshot/ still needs a rewrite so the trailing-slash
      // nav href (kept for compatibility) resolves to the non-trailing-slash
      // page route instead of 404ing.
      { source: '/cfo-snapshot/', destination: '/cfo-snapshot' },
      {
        source: '/api-token-auth/',
        destination: `${backendUrl}/api-token-auth/`,
      },
      {
        // Preserve a trailing slash when proxying to Django (belt-and-braces
        // with the backend's ApiTrailingSlashMiddleware). path-to-regexp drops
        // the empty trailing segment from :path*, so /api/x/ would reach Django
        // as /api/x and APPEND_SLASH raises on a slash-less POST. Match the
        // slash-terminated form first and re-add the slash to the destination.
        source: '/api/:path*/',
        destination: `${backendUrl}/api/:path*/`,
      },
      {
        source: '/api/:path*',
        destination: `${backendUrl}/api/:path*`,
      },
      {
        // Clean public link for the Windows desktop installer (org rollout).
        source: '/download/omni-desktop',
        destination: `${backendUrl}/api/v1/desktop-app/`,
      },
    ]
  },
  async redirects() {
    return [
      // Smart Entry is the marketing/audit name for the AI document upload +
      // GL coding flow. The implementation lives at /quick-entry so that the
      // route shares URL space with the rest of the data-entry surface.
      // Keep a permanent redirect from the audit-quoted URL so old links
      // and audit checklists continue to resolve.
      { source: '/smart-entry', destination: '/quick-entry', permanent: true },
      { source: '/smart-entry/:path*', destination: '/quick-entry/:path*', permanent: true },
      // BUG-009 (bug batch 2026-06-03): four valid-looking nav URLs 404.
      // Redirect each to its real route so breadcrumbs / deep-links resolve.
      { source: '/cfo-dashboard',      destination: '/cfo',                 permanent: true },
      { source: '/health-care',        destination: '/health/quick-quote', permanent: true },
      { source: '/receivables',        destination: '/reports/ar-aging',    permanent: true },
      { source: '/reports/pnl-entity', destination: '/reports/entity-pl',   permanent: true },
      // CFO directive 2026-05-19 (Final Verification Audit § 5): the
      // Manus audit and the HRIS hub link to legacy URLs that 404 today.
      // Keep them working by redirecting to the active routes.
      { source: '/hris/apply-leave',     destination: '/hris/leave',     permanent: true },
      { source: '/hris/people-directory', destination: '/hris/directory', permanent: true },
      // Removed 2026-06-25: the /reports/cash-flow → /reports/cash-position
      // stopgap redirect. The real IAS 7 cash-flow page has since shipped, so
      // the redirect was SHADOWING it — the sidebar's new "Cash Flow" link
      // landed on Cash Position. Caught by the post-#253 regression review.
      // Recurring JEs: the implementation lives at
      // /settings/recurring-journal-entries. The audit checklist
      // referenced /accounting/recurring-jes — same feature, different URL.
      { source: '/accounting/recurring-jes', destination: '/settings/recurring-journal-entries', permanent: false },
      // CFO directive 2026-05-20 (Salvage Enhancements memo): the basic
      // /claims/salvages form is deprecated. Redirect to the rich
      // /salvage/inventory module which already carries VIN, policy
      // number, brand/model/year/colour, condition, asking/reserve
      // pricing, yard location, and image uploads on the data model.
      { source: '/claims/salvages',          destination: '/salvage/inventory',         permanent: false },
      { source: '/claims/salvages/new',      destination: '/salvage/inventory/new',     permanent: false },
      { source: '/claims/salvages/:id',      destination: '/salvage/inventory/:id',     permanent: false },
    ]
  },
  async headers() {
    // The marketing/landing routes must never live in a shared cache — old
    // HTML pointing at obsolete JS bundle hashes was getting served from
    // Cloudflare and mobile-carrier proxies for hours after each redeploy.
    const noStoreHeaders = [
      { key: 'Cache-Control', value: 'no-store, no-cache, must-revalidate, max-age=0' },
      { key: 'Pragma', value: 'no-cache' },
      { key: 'Expires', value: '0' },
      { key: 'Surrogate-Control', value: 'no-store' },
    ]
    return [
      { source: '/', headers: noStoreHeaders },
      { source: '/login', headers: noStoreHeaders },
      // Build-id beacon for the UpdateChecker — must always be fetched fresh
      // so open clients notice a new deploy.
      { source: '/build-id.txt', headers: noStoreHeaders },
      // Public salvage storefront — no auth required, must always pull fresh
      // so price changes propagate without intermediary caches stalling.
      { source: '/buy-salvage', headers: noStoreHeaders },
      // HRIS — must always re-fetch so the password-gate layout wakes
      // up immediately after deploy (no stale bundle keeps an
      // unguarded build alive past a rollout).
      { source: '/hris', headers: noStoreHeaders },
      { source: '/hris/:path*', headers: noStoreHeaders },
      // CFO directive 2026-05-21: Pako + Kago reported the new GL
      // 'Extract all' banner was invisible after the deploy. Root
      // cause: Next.js's default x-nextjs-cache HIT on /reports/*
      // served the stale HTML shell (referencing the pre-banner
      // client bundle) for up to s-maxage=year on Caddy. Mark every
      // /reports/* route no-store so each visit re-hits the
      // freshly-built shell with the current bundle hash.
      { source: '/reports', headers: noStoreHeaders },
      { source: '/reports/:path*', headers: noStoreHeaders },
      // Dashboard surface — same hazard, same fix.
      { source: '/dashboard', headers: noStoreHeaders },
      { source: '/dashboard/:path*', headers: noStoreHeaders },
      // CFO Snapshot static page — always pull fresh so a re-run of the
      // generator (a new month's figures) propagates without a cache stall.
      { source: '/cfo-snapshot', headers: noStoreHeaders },
      { source: '/cfo-snapshot/:path*', headers: noStoreHeaders },
      // ── EVERY OTHER PAGE (CFO 2026-09-09) ──────────────────────────────
      // The rules above are a hand-kept ALLOW-LIST, and every one of them was
      // added AFTER a person reported "my change isn't showing" (see the Pako
      // + Kago note above). Whatever nobody had complained about yet kept
      // Next's default for a statically-prerendered page,
      // `Cache-Control: s-maxage=31536000` — a ONE-YEAR shared-cache licence on
      // an authenticated ERP page. Measured on prod 9-Sep-2026: /app and
      // /app/work (the whole staff phone app), /my-omni, /approvals,
      // /payroll/payslips, /banking/realpay and /m were all serving that,
      // while /dashboard, /hris/* and /reports/* were correctly no-store. That
      // asymmetry is exactly why the CFO saw the web ask for a refresh while
      // the phone and desktop apps kept showing the old build: the installed
      // apps are long-lived and cache hard, and the page underneath the
      // refresh banner was a year-old copy.
      //
      // So stop maintaining a list. Omni is an authenticated single-page app:
      // NO html document should ever be stored. This one rule covers every
      // route, present and future, so a new page can no longer be forgotten.
      //
      // The negative lookahead is load-bearing: it must NOT match the hashed,
      // content-addressed build output under /_next/static (or /_next/image,
      // or any file with an extension such as .js/.css/.png/.woff2). Those are
      // immutable per build and are what makes Omni fast — no-storing them
      // would re-download the whole bundle on every page load for all 126
      // staff. `frontend/src/app/__tests__/cacheHeaders.test.ts` pins both
      // sides of that.
      // `[^.]*` is what keeps the big files cached: a page route never contains
      // a dot, every static file does. public/ holds a 32 MB AlphaNexus.apk, a
      // 17 MB aria-rat-3d.glb and 4 MB brand PNGs — no-storing those would be
      // far worse than the bug being fixed. Verified against 41 real paths in
      // frontend/src/app/__tests__/cacheHeaders.test.ts, which compiles this
      // exact source with Next's own matcher.
      { source: NO_STORE_PAGES, headers: noStoreHeaders },
    ]
  },
}

export default nextConfig
