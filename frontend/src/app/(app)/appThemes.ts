/** Omni staff app — the three looks a staff member can choose between.
 *
 * Mirrors the desktop ERP's lib/themes.ts (professional / light / fun / heavenly)
 * but for the phone. Colours are the REAL Alpha Direct brand, verified against
 * the logo file itself: navy #0B0B3B, orange #F07F00. The app previously used
 * #0D1B2A / #F4A623 — a grey-slate and a muted amber that were never the brand.
 *
 * Applied as CSS custom properties on the app shell, so the shared staff screens
 * (which /m/staff also renders for Nexus) pick the theme up under /app ONLY —
 * the (customer)/ui tokens fall back to the Nexus palette when nothing sets them.
 */
export type AppThemeKey = 'direct' | 'warm' | 'night'

export interface AppTheme {
  key: AppThemeKey
  name: string
  blurb: string
  /** swatches shown in the picker: [chrome, accent, ground] */
  swatch: [string, string, string]
  vars: Record<string, string>
}

/** Every colour the app draws with. `navy` is chrome (headers, dark blocks);
 * `head` is heading TEXT — they part company in a dark theme. */
const tokens = (t: {
  surface: string; card: string; ink: string; inkSoft: string
  navy: string; navy2: string; head: string
  orange: string; orangeDeep: string; line: string
  amber: string; red: string; green: string; teal: string; tealLight: string
  orangeWash: string; navyWash: string
}) => ({
  '--ao-surface': t.surface, '--ao-card': t.card, '--ao-ink': t.ink, '--ao-ink-soft': t.inkSoft,
  '--ao-navy': t.navy, '--ao-navy2': t.navy2, '--ao-head': t.head,
  '--ao-orange': t.orange, '--ao-orange-deep': t.orangeDeep, '--ao-line': t.line,
  '--ao-amber': t.amber, '--ao-red': t.red, '--ao-green': t.green,
  '--ao-teal': t.teal, '--ao-teal-light': t.tealLight,
  // Soft washes behind small icons — must follow the theme or they go
  // dark-on-dark in Night.
  '--ao-orange-wash': t.orangeWash, '--ao-navy-wash': t.navyWash,
})

export const APP_THEMES: Record<AppThemeKey, AppTheme> = {
  // The house look. Brand navy chrome, white cards, orange kept for the one
  // thing that matters most on a screen.
  direct: {
    key: 'direct',
    name: 'Direct',
    blurb: 'The Alpha Direct look — navy, white, orange where it counts.',
    swatch: ['#0B0B3B', '#F07F00', '#F9FAFB'],
    vars: tokens({
      surface: '#F9FAFB', card: '#FFFFFF', ink: '#111827', inkSoft: '#6B7280',
      navy: '#0B0B3B', navy2: '#1A1A5E', head: '#0B0B3B',
      orange: '#F07F00', orangeDeep: '#CC6C00', line: '#E5E7EB',
      amber: '#9A5200', red: '#B91C1C', green: '#047857',
      teal: '#0E9488', tealLight: '#2DD4BF',
      orangeWash: 'rgba(240,127,0,0.14)', navyWash: 'rgba(11,11,59,0.06)',
    }),
  },
  // Softer and warmer for people who look at this all day. Same brand colours,
  // a paper-like ground instead of cool grey.
  warm: {
    key: 'warm',
    name: 'Warm',
    blurb: 'Easier on the eyes all day. Same brand colours, a warmer paper.',
    swatch: ['#0B0B3B', '#F07F00', '#FAF7F2'],
    vars: tokens({
      surface: '#FAF7F2', card: '#FFFFFF', ink: '#1C1608', inkSoft: '#7A6A55',
      navy: '#0B0B3B', navy2: '#1A1A5E', head: '#0B0B3B',
      orange: '#F07F00', orangeDeep: '#B45A00', line: '#EDE6DB',
      amber: '#9A5200', red: '#B3261E', green: '#0F6B4B',
      teal: '#0E7C74', tealLight: '#2DD4BF',
      orangeWash: 'rgba(240,127,0,0.16)', navyWash: 'rgba(11,11,59,0.06)',
    }),
  },
  // For evenings and dim rooms. Cards become deep navy, headings turn white.
  night: {
    key: 'night',
    name: 'Night',
    blurb: 'For dim rooms and late approvals. Deep navy throughout.',
    swatch: ['#12124F', '#F07F00', '#07074E'],
    vars: tokens({
      surface: '#07074E', card: '#0F0F45', ink: '#F3F4FB', inkSoft: '#A9A9D1',
      navy: '#12124F', navy2: '#1A1A5E', head: '#FFFFFF',
      orange: '#F07F00', orangeDeep: '#FF9A2E', line: '#25255E',
      amber: '#E0A63C', red: '#F0736A', green: '#4FBF8B',
      teal: '#4FD1C5', tealLight: '#2DD4BF',
      orangeWash: 'rgba(240,127,0,0.22)', navyWash: 'rgba(255,255,255,0.09)',
    }),
  },
}

export const DEFAULT_THEME: AppThemeKey = 'direct'
const KEY = 'omni_app_theme'

export function readTheme(): AppThemeKey {
  if (typeof window === 'undefined') return DEFAULT_THEME
  try {
    const v = window.localStorage.getItem(KEY)
    if (v && v in APP_THEMES) return v as AppThemeKey
  } catch { /* private mode — fall through to the default */ }
  return DEFAULT_THEME
}

export function applyTheme(key: AppThemeKey) {
  if (typeof document === 'undefined') return
  const theme = APP_THEMES[key] ?? APP_THEMES[DEFAULT_THEME]
  const root = document.documentElement
  for (const [name, value] of Object.entries(theme.vars)) root.style.setProperty(name, value)
  root.setAttribute('data-omni-theme', theme.key)
  // The browser paints scrollbars, form controls and the overscroll gutter from
  // this — without it Night shows white bars at the edges of the app.
  root.style.colorScheme = theme.key === 'night' ? 'dark' : 'light'
}

export function saveTheme(key: AppThemeKey) {
  try { window.localStorage.setItem(KEY, key) } catch { /* private mode — applies for this visit only */ }
  applyTheme(key)
}
