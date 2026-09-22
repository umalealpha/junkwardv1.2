/**
 * The colour contract for <BankBalancesPanel/>.
 *
 * The panel takes its colours as a prop instead of reaching for a theme, so the
 * SAME component serves the desktop (which repaints itself through the four
 * ERP themes) and the staff phone app (which is brand-only and deliberately
 * takes no desktop theme). One component, two palettes — never two components.
 *
 * 🔴 Why the panel uses inline colours and not `bg-[#0D1B2A]` utilities:
 * `html.theme-professional` in globals.css repaints every hardcoded brand hex
 * by CLASS substring (`[class*="bg-[#0D1B2A"]` → #4F6BED), which turns the navy
 * headline into mid-blue and makes orange or grey text on it unreadable. Inline
 * styles are not matched by those rules, and `fromTheme()` hands the panel each
 * theme's OWN navy and accent, so the professional theme gets its neutral
 * near-black headline rather than an accidental blue one. Text on the dark
 * headline is always `onNavy` (white) with the accent used only as a 1px ring.
 */
import type { Theme } from '@/lib/themes'

export interface BalancesPalette {
  /** Page background behind the cards. */
  surface: string
  card: string
  cardBorder: string
  cardShadow: string
  line: string
  ink: string
  inkSoft: string
  heading: string
  /** Dark block behind the headline. */
  navy: string
  /** The ONLY text colour used on `navy`. */
  onNavy: string
  /** Brand accent — rings, rails, small marks. Never used as text on navy. */
  accent: string
  ok: string
  warn: string
  warnBg: string
  danger: string
  dangerBg: string
  /** Heading typeface for this surface. */
  headingFont: string
}

const SERIF = '"Book Antiqua", "Palatino Linotype", Palatino, Georgia, serif'

/** WCAG relative luminance of a #rgb / #rrggbb colour. 0 = black, 1 = white. */
function luminance(hex: string): number {
  const h = hex.replace('#', '')
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h
  if (!/^[0-9a-fA-F]{6}$/.test(full)) return 1   // unparseable → treat as light
  const [r, g, b] = [0, 2, 4]
    .map((i) => parseInt(full.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/**
 * The dark block behind the headline, for a theme.
 *
 * NOT simply `theme.navy`: in Fun Mode `navy` is the HEADING TEXT colour
 * (#E0E7FF, near-white) because the cards there are already dark. Using it as a
 * background would have painted the headline white-on-white. So take the first
 * of navy → sidebar → the brand navy that white actually reads on.
 */
export function darkSurface(theme: Theme): string {
  const candidates = [theme.navy, theme.sidebar, '#0D1B2A']
  return candidates.find((c) => luminance(c) < 0.18) ?? '#0D1B2A'
}

/** Desktop / ERP: follow whichever of the four themes the viewer has chosen. */
export function fromTheme(theme: Theme): BalancesPalette {
  return {
    surface: theme.bg,
    card: theme.card,
    cardBorder: theme.cardBdr,
    cardShadow: theme.cardSh,
    line: theme.g200,
    ink: theme.text,
    inkSoft: theme.t2,
    heading: theme.navy,
    navy: darkSurface(theme),
    onNavy: '#FFFFFF',
    accent: theme.orange,
    ok: theme.ok,
    warn: theme.wr,
    warnBg: theme.wrB,
    danger: theme.er,
    dangerBg: theme.erB,
    headingFont: SERIF,
  }
}
