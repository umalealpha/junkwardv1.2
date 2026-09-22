// Alpha Direct ERP — Theme definitions
// Two themes only: Light (default corporate) and Fun (deep navy/purple with orange glow)

export type ThemeKey = 'professional' | 'light' | 'fun' | 'heavenly';

export interface Theme {
  name: string;
  sidebar: string;
  topbar: string;
  topbarBdr: string;
  bg: string;
  card: string;
  cardBdr: string;
  cardSh: string;
  text: string;
  t2: string;      // secondary text
  t3: string;      // muted text
  navy: string;
  orange: string;
  orangeText: string; // accent used AS TEXT / small-text button bg — must hold ≥4.5:1 vs card
  oL: string;      // orange light bg
  teal: string;    // accent for positive metrics + AI elements
  tealL: string;   // teal light bg
  g50: string;
  g100: string;
  g200: string;
  g700: string;
  ok: string;      // success
  okB: string;     // success bg
  wr: string;      // warning
  wrB: string;     // warning bg
  er: string;      // error
  erB: string;     // error bg
  inf: string;     // info
  inB: string;     // info bg
  funBg: boolean;  // whether fun background is active
  // ── Aliases used by a handful of pages (kept for back-compat) ─────────
  input: string;   // form input background  (= card on light, deeper on fun)
  b1: string;      // primary border         (= cardBdr)
  t1: string;      // primary text           (= text)
}

export const themes: Record<ThemeKey, Theme> = {
  // Professional — clean, calm, neutral light theme (CFO directive 2026-08-10).
  // White surfaces, a LIGHT sidebar, ONE soft slate-blue accent (#4F6BED) for
  // everything clickable, soft pastel status chips, Inter/system sans (not the
  // Book-Antiqua serif). No navy chrome, no orange. Light-based, so hardcoded
  // light Tailwind classes stay correct; globals.css `.theme-professional`
  // recolours the hardcoded navy/orange → slate, lightens the sidebar, and
  // swaps the serif → sans. Mirrors the .theme-heavenly override pattern.
  professional: {
    name: "Professional",
    sidebar: "#FFFFFF",
    topbar: "#FFFFFF",
    topbarBdr: "#ECEEF1",
    bg: "#F7F8FA",
    card: "#FFFFFF",
    cardBdr: "#ECEEF1",
    cardSh: "0 1px 3px rgba(16,24,40,0.06)",
    text: "#1A1D21",
    t2: "#686F7D",   // 4.54:1 on g100 #F1F3F5 — #6B7280 was 4.35:1 (WCAG AA fail)
    // 4.54:1 on g100 — #6B7280 was 4.35:1 there (QC 13-Sep-2026, /hris/directory)
    t3: "#686F7D",
    navy: "#1A1D21",         // headings / KPI numbers → neutral near-black (no navy)
    orange: "#4F6BED",       // THE accent — soft slate-blue (drives active nav, focus, chips)
    orangeText: "#3F58CC",   // accent AS text — 5.1:1 on white (AA-safe)
    oL: "#EEF2FC",           // accent pale wash (active item / selected row)
    teal: "#5FB6A8",         // calm secondary (positive metrics / AI)
    tealL: "#EAF5F3",
    g50: "#F7F8FA",
    g100: "#F1F3F5",
    g200: "#ECEEF1",
    g700: "#374151",
    ok: "#1E7A44",
    okB: "#E7F6EC",
    wr: "#966609",   // 4.54:1 on wrB #FDF3E1 — #9A6A11 was 4.29:1 (WCAG AA fail)
    wrB: "#FDF3E1",
    er: "#B4232D",
    erB: "#FCEBEC",
    inf: "#3B57C7",
    inB: "#EEF2FC",
    funBg: false,
    input: "#FFFFFF",
    b1: "#ECEEF1",
    t1: "#1A1D21",
  },
  light: {
    name: "Light",
    sidebar: "#0B0B3B",
    topbar: "#FFFFFF",
    topbarBdr: "#E5E7EB",
    bg: "#F9FAFB",
    card: "#FFFFFF",
    cardBdr: "#E5E7EB",
    cardSh: "0 1px 3px rgba(0,0,0,0.08)",
    text: "#111827",
    t2: "#686F7D",   // 4.59:1 on g100 #F3F4F6 — #6B7280 was 4.39:1 (WCAG AA fail)
    // 4.83:1 on white — #9CA3AF (2.53:1) failed WCAG AA for the muted-text role
    t3: "#686F7D",   // 4.59:1 on g100 — #6B7280 was 4.39:1 (QC 13-Sep-2026)
    navy: "#0D1B2A",
    orange: "#F07F00",
    orangeText: "#B04E00", // 5.3:1 on white; white text on it also 5.3:1
    oL: "#FFF7ED",
    teal: "#00C9B7",
    tealL: "#E6FBF8",
    g50: "#F9FAFB",
    g100: "#F3F4F6",
    g200: "#E5E7EB",
    g700: "#374151",
    ok: "#059669",
    okB: "#ECFDF5",
    wr: "#AE6005",   // 4.53:1 on wrB #FFFBEB — #D97706 was 3.07:1 (WCAG AA fail)
    wrB: "#FFFBEB",
    er: "#DC2626",
    erB: "#FEF2F2",
    inf: "#2563EB",
    inB: "#EFF6FF",
    funBg: false,
    input: "#FFFFFF",
    b1: "#E5E7EB",
    t1: "#111827",
  },
  fun: {
    name: "Fun Mode",
    sidebar: "#0D0D42",
    topbar: "#13134D",
    topbarBdr: "#2D2D7A",
    bg: "#121235",
    card: "#1A1A5E",
    cardBdr: "#2D2D7A",
    cardSh: "0 2px 8px rgba(240,127,0,0.06)",
    text: "#FFFFFF",
    t2: "#D4CCFF",
    t3: "#9B91E0",
    navy: "#E0E7FF",
    orange: "#FF9F2E",
    orangeText: "#FF9F2E", // on the dark fun cards this already clears 4.5:1
    oL: "#1A1050",
    teal: "#5EEAD4",
    tealL: "#042F2E",
    g50: "#121245",
    g100: "#1A1A5E",
    g200: "#2D2D7A",
    g700: "#E0E7FF",
    ok: "#4ADE80",
    okB: "#052E16",
    wr: "#FDE047",
    wrB: "#3B2504",
    er: "#FB7185",
    erB: "#4C0519",
    inf: "#818CF8",
    inB: "#1E1B4B",
    funBg: true,
    input: "#1A1A5E",
    b1: "#2D2D7A",
    t1: "#FFFFFF",
  },
  // Heavenly — airy sky-blue + white glass, black fonts, single sky-blue accent
  // (no orange). Light-based, so hardcoded light Tailwind classes stay correct;
  // globals.css `.theme-heavenly` recolours the hardcoded orange to sky-blue and
  // paints the sky gradient. The rail stays deep-sky-navy so its white nav text
  // remains readable (the white-rail mockup detail is part of the nav redesign).
  heavenly: {
    name: "Heavenly",
    sidebar: "#0E2A47",
    topbar: "#FFFFFF",
    topbarBdr: "#DCEBFB",
    bg: "#EAF4FF",
    card: "#FFFFFF",
    cardBdr: "#DCEBFB",
    cardSh: "0 10px 30px rgba(46,139,230,0.10)",
    text: "#0F172A",
    t2: "#475569",
    t3: "#5F6F84",   // 4.61:1 on the sky bg — #94A3B8 was 2.31:1 (QC 13-Sep-2026)
    navy: "#0F172A",
    orange: "#2E8BE6",
    orangeText: "#1D6FC2", // 5.12:1 on white (sky accent, text-safe)
    oL: "#EAF4FF",
    teal: "#2E8BE6",
    tealL: "#EAF4FF",
    g50: "#F8FBFF",
    g100: "#EFF6FF",
    g200: "#DCEBFB",
    g700: "#334155",
    ok: "#059669",
    okB: "#ECFDF5",
    wr: "#AE6005",   // 4.53:1 on wrB #FFFBEB — #D97706 was 3.07:1 (WCAG AA fail)
    wrB: "#FFFBEB",
    er: "#DC2626",
    erB: "#FEF2F2",
    inf: "#2E8BE6",
    inB: "#EAF4FF",
    funBg: false,
    input: "#FFFFFF",
    b1: "#DCEBFB",
    t1: "#0F172A",
  },
};
