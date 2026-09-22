/**
 * Alpha Nexus customer-app design tokens (from the approved Stitch direction).
 * Light, premium, futuristic: light surfaces, navy hero cards, orange (Drive)
 * + teal (Wellness/Redeem) accents, Playfair Display headings + Inter body.
 */
/* Nexus (the customer app at /m) keeps its own palette: nothing sets these
 * variables there, so every fallback below applies unchanged. Under /app the
 * staff shell sets them (see (app)/appThemes.ts), which is how the SHARED staff
 * screens follow the staff member's chosen theme without touching Nexus. */
export const C = {
  surface: 'var(--ao-surface, #F6F7F9)',
  card: 'var(--ao-card, #FFFFFF)',
  ink: 'var(--ao-ink, #191C1E)',
  inkSoft: 'var(--ao-ink-soft, #5B6068)',
  navy: 'var(--ao-navy, #0F1C2C)',
  navy2: 'var(--ao-navy2, #16263A)',
  head: 'var(--ao-head, #0F1C2C)',
  orange: 'var(--ao-orange, #F4A623)',
  orangeDeep: 'var(--ao-orange-deep, #E2700B)',
  teal: 'var(--ao-teal, #0E9488)',
  tealLight: 'var(--ao-teal-light, #2DD4BF)',
  line: 'var(--ao-line, #ECEEF1)',
}

export const serif = 'var(--font-playfair), Georgia, "Times New Roman", serif'
export const sans = 'var(--font-inter), system-ui, -apple-system, sans-serif'

// iOS notch / Dynamic Island (and the home-indicator) safe areas. The layout
// uses viewport-fit=cover, so the webview extends UNDER the camera cut-out —
// without these insets the top logo is clipped by the iPhone 17 camera and the
// bottom tab bar sits under the home indicator. Headers add the top inset to
// their own padding so their background fills the notch strip.
export const safeTop = 'env(safe-area-inset-top, 0px)'
export const safeBottom = 'env(safe-area-inset-bottom, 0px)'
// Standard header padding with the top safe-area folded in.
export const headerPad = `calc(16px + ${safeTop}) 20px 16px`

/** True when running INSIDE an installed app shell rather than a browser tab:
 * iOS/Android Tauri webview, or an installed (standalone) PWA. Used to hide
 * "Get the app" install prompts — Apple rejected v1.0.0 under Guideline
 * 2.3.10 for showing an Android download button inside the iOS app.
 * Detection: standalone display-mode / navigator.standalone (installed PWA),
 * iOS WKWebView (no "Safari/" token in the UA), or Android webview ("; wv"). */
export function isInAppShell(): boolean {
  if (typeof window === 'undefined') return false
  try {
    // Tauri injects its IPC globals into every page (including remote URLs) —
    // the strongest possible signal that we're inside the packaged app.
    const w = window as Window & { __TAURI_INTERNALS__?: unknown; __TAURI__?: unknown }
    if (w.__TAURI_INTERNALS__ !== undefined || w.__TAURI__ !== undefined) return true
    const nav = window.navigator as Navigator & { standalone?: boolean }
    if (nav.standalone === true) return true
    if (window.matchMedia?.('(display-mode: standalone)')?.matches) return true
    const ua = nav.userAgent || ''
    // WKWebView (any Apple device) ships an AppleWebKit UA with NO "Safari/"
    // token; every real browser (Safari, Chrome, Edge, Firefox-iOS) includes
    // "Safari/". Crucially this also catches iPADS, which report a desktop
    // "Macintosh" UA — Apple's reviewer used an iPad Air, so an iPhone-only
    // pattern would have shown the Android button on their review device.
    if (/AppleWebKit\//.test(ua) && !/Safari\//.test(ua)) return true
    if (/Android/.test(ua) && /;\s*wv\b/.test(ua)) return true             // Android webview
  } catch { /* detection is best-effort */ }
  return false
}

// Reusable style fragments.
export const card: React.CSSProperties = {
  background: C.card, borderRadius: 22, border: `1px solid ${C.line}`,
  boxShadow: '0 8px 30px rgba(16,27,42,0.05)',
}
export const pill = (bg: string, fg: string): React.CSSProperties => ({
  display: 'inline-flex', alignItems: 'center', gap: 6, padding: '5px 12px',
  borderRadius: 999, fontSize: 11, fontWeight: 700, letterSpacing: '0.04em',
  textTransform: 'uppercase', background: bg, color: fg,
})
export const h = (size: number): React.CSSProperties => ({
  fontFamily: serif, fontWeight: 700, color: C.ink, fontSize: size, lineHeight: 1.12, margin: 0,
})
