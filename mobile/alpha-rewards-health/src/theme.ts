/**
 * theme.ts — Light "Alpha" skin.
 * Warm-white background, dark-navy text, orange primary, rounded cards.
 */

export const colors = {
  bg: '#F7F8FB', // warm white
  surface: '#FFFFFF', // card surface
  text: '#0D1B2A', // dark navy
  textMuted: '#5B6472', // muted navy-grey
  primary: '#F4A623', // Alpha orange
  primaryText: '#0D1B2A', // text on orange
  border: '#E4E8F0',
  success: '#1E8E5A',
  danger: '#C0392B',
  disabled: '#C9CFD9',
} as const;

export const radius = {
  card: 18,
  button: 14,
  pill: 999,
} as const;

export const spacing = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
} as const;

export const fontSize = {
  hero: 44,
  title: 22,
  subtitle: 16,
  body: 15,
  caption: 13,
} as const;
