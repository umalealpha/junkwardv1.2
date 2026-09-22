/** Omni staff app tokens — Omni brand (navy/orange, Book Antiqua headings),
 * NOT the Nexus Playfair palette. Same safe-area helpers as (customer)/ui.ts. */
/* Each token is a CSS variable the chosen theme sets on <html> (see appThemes.ts).
 * The fallback is the Direct theme, so the app renders correctly before the
 * theme is applied and if a viewer has no stored choice. `navy` is CHROME
 * (headers, dark blocks); `head` is heading TEXT — in Night they differ. */
export const C = {
  surface: 'var(--ao-surface, #F9FAFB)', card: 'var(--ao-card, #FFFFFF)',
  ink: 'var(--ao-ink, #111827)', inkSoft: 'var(--ao-ink-soft, #6B7280)',
  navy: 'var(--ao-navy, #0B0B3B)', navy2: 'var(--ao-navy2, #1A1A5E)',
  head: 'var(--ao-head, #0B0B3B)',
  orange: 'var(--ao-orange, #F07F00)', orangeDeep: 'var(--ao-orange-deep, #CC6C00)',
  line: 'var(--ao-line, #E5E7EB)', amber: 'var(--ao-amber, #9A5200)',
  red: 'var(--ao-red, #B91C1C)', green: 'var(--ao-green, #047857)',
}
export const serif = '"Book Antiqua", "Palatino Linotype", Palatino, Georgia, serif'
export const sans = 'var(--font-inter), system-ui, -apple-system, sans-serif'
export const safeTop = 'env(safe-area-inset-top, 0px)'
export const safeBottom = 'env(safe-area-inset-bottom, 0px)'
export const headerPad = `calc(16px + ${safeTop}) 20px 16px`
export const ease = 'cubic-bezier(0.16, 1, 0.3, 1)'          // ease-out-expo
export const dur = { fast: '150ms', base: '220ms' }
export const card: React.CSSProperties = {
  background: C.card, borderRadius: 20, border: `1px solid ${C.line}`,
  boxShadow: 'var(--ao-card-shadow, 0 6px 24px rgba(11,11,59,0.06))',
}
export const h = (size: number): React.CSSProperties => ({
  fontFamily: serif, fontWeight: 700, color: C.head, fontSize: size, lineHeight: 1.12, margin: 0,
})
import { isInAppShell as isInAppShellBase } from '@/app/(customer)/ui'

/** True when Omni is running as an INSTALLED app rather than a browser tab:
 * the shared detector (Tauri webview / navigator.standalone / display-mode
 * standalone / Android webview), plus the Android store wrapper, which loads
 * /app with document.referrer set to "android-app://<package>". Used to hide
 * the "Add to Home Screen" card — inside a store build that instruction is
 * wrong, and Apple rejects a listing that points at another platform's
 * install flow. Returns false during SSR, so the caller must set it in an
 * effect (never during render) or hydration will mismatch. */
export function isInAppShell(): boolean {
  if (typeof window === 'undefined') return false
  if (isInAppShellBase()) return true
  try { return (document.referrer || '').startsWith('android-app://') } catch { return false }
}
