# Alpha Direct ERP — Frontend Redesign Implementation Prompts

These are step-by-step Claude Code prompts to implement the approved redesign layout. Run them in order. Each prompt is self-contained and should be run as a single Claude Code session.

---

## Pre-requisites

Before running any prompts, make sure:
- Backend is running: `python manage.py runserver` on port 8000
- You're in the frontend directory: `cd C:\Users\OTHER\alpha_finance\frontend`
- Node.js path is set: `set PATH=C:\Users\OTHER\AppData\Local\node\node-v22.11.0-win-x64;%PATH%`

---

## PROMPT 1: Add Brand Assets (Mascots + Logos)

```
I need you to set up the brand image assets for our Alpha Direct ERP frontend redesign.

1. Create the directory: `frontend/public/brand/`

2. Copy these mascot images from `C:\Users\OTHER\Downloads\Alpha Graphics for teams\Alpha Grpahics for teams\Super alphas full Characters\` into `frontend/public/brand/mascots/`:
   - "Alpha Boy.png" → rename to `boy-dab.png`
   - "Alpha Boy 2.png" → rename to `boy-stand.png`
   - "Alpha Man Massive.png" → rename to `boy-massive.png`
   - "Alpha Lady 1.png" → rename to `girl-fly.png`
   - "Alpha Lady 3.png" → rename to `girl-stand.png`
   - "Alpha Lady pose.png" → rename to `girl-pose.png`
   - "Super Alpha 1.png" → rename to `super-1.png`
   - "Super Alpha 3.png" → rename to `super-3.png`
   - "Alpha Family.png" → rename to `family.png`

3. Copy logo images from `C:\Users\OTHER\Downloads\Alpha Graphics for teams\Alpha Grpahics for teams\` into `frontend/public/brand/`:
   - "AlphaDirect_Logo_without_reflection-01.png" → rename to `logo-full-color.png`
   - "AlphaDirect_Monotone-04.png" → rename to `logo-monotone-white.png`

4. Use Python Pillow to optimize ALL images for web:
   - Mascots: resize to max width 260px, maintain aspect ratio, LANCZOS resampling, keep RGBA/transparency
   - Logos: resize to max width 400px, same settings
   - Save as optimized PNG (compress_level=6)

5. Create `frontend/src/lib/brand.ts`:
```typescript
// Brand asset paths
export const BRAND = {
  logo: {
    full: '/brand/logo-full-color.png',
    white: '/brand/logo-monotone-white.png',
  },
  mascots: {
    boyDab: '/brand/mascots/boy-dab.png',
    boyStand: '/brand/mascots/boy-stand.png',
    boyMassive: '/brand/mascots/boy-massive.png',
    girlFly: '/brand/mascots/girl-fly.png',
    girlStand: '/brand/mascots/girl-stand.png',
    girlPose: '/brand/mascots/girl-pose.png',
    super1: '/brand/mascots/super-1.png',
    super3: '/brand/mascots/super-3.png',
    family: '/brand/mascots/family.png',
  },
} as const;

// All mascot images as array for random selection
export const ALL_MASCOTS = Object.values(BRAND.mascots);
```

Do NOT modify any existing files. Only create new files/directories.
```

---

## PROMPT 2: Add Theme System and Quotes Library

```
I need you to create the theme system and quotes library for our Alpha Direct ERP frontend.

IMPORTANT: Read the design system at `docs/design-system/SKILL.md` before starting.

1. Create `frontend/src/lib/themes.ts`:

Define exactly TWO themes (no dark mode):

```typescript
export type ThemeKey = 'light' | 'fun';

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
  oL: string;      // orange light bg
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
}

export const themes: Record<ThemeKey, Theme> = {
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
    t2: "#6B7280",
    t3: "#9CA3AF",
    navy: "#0B0B3B",
    orange: "#F07F00",
    oL: "#FFF7ED",
    g50: "#F9FAFB",
    g100: "#F3F4F6",
    g200: "#E5E7EB",
    g700: "#374151",
    ok: "#059669",
    okB: "#ECFDF5",
    wr: "#D97706",
    wrB: "#FFFBEB",
    er: "#DC2626",
    erB: "#FEF2F2",
    inf: "#2563EB",
    inB: "#EFF6FF",
    funBg: false,
  },
  fun: {
    name: "Fun Mode",
    sidebar: "#0D0D42",
    topbar: "#13134D",
    topbarBdr: "#2D2D7A",
    bg: "#0A0A30",
    card: "#151555",
    cardBdr: "#2D2D7A",
    cardSh: "0 2px 12px rgba(240,127,0,0.12)",
    text: "#FFFFFF",
    t2: "#C4B5FD",
    t3: "#8B7FD4",
    navy: "#E0E7FF",
    orange: "#FF9F2E",
    oL: "#1A1050",
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
  },
};
```

2. Create `frontend/src/lib/quotes.ts`:

This must contain page-contextual funny quotes. MINIMUM 15 quotes per major page, 5+ for sub-pages. Every page load should feel fresh. Here are the categories and sample quotes (use these EXACTLY, they were approved):

- `home` (15 quotes): "You're back! The books were getting lonely!", "Ready to save the day, one policy at a time!", "Welcome to HQ! The numbers await your command!", "Alpha team assembled! Let's go!", "The Pula stops here! Let's get to work!", "Insurance heroes never rest... but coffee helps!", "Look who's here! The finance department just got stronger!", "Botswana's finest financial heroes at your service!", "Every great insurance company starts with a great login!", "Dumela! Ready to make some financial magic happen?", ... (add 5 more unique ones)

- `dashboard` (16 quotes): "Let me check the numbers... looking MAAASSSIVE!", "Counting every last thebe! Almost done...", "Dashboard time! Let's review the financial kingdom!", "Numbers looking strong! The shield is proud!", "Pulling the latest data — hang on to your cape!", "Financial overview coming in hot!", "Every Pula accounted for. Every thebe tracked!", "The squad ran the numbers — you're gonna love this!", "Dashboard powered up! Let's see what we're working with!", "Gross written premium loading... and it's looking GOOD!", "Alpha Boy says: these numbers are worth dabbing for!", "Claims vs premiums — let the battle begin!", "Cash position check: we're still flying high!", "Reinsurance recovery? That's money coming HOME!", "The finance dashboard: where heroes check their stats!", "Loading your financial superpowers..."

- `invoices` (15 quotes): "Ka-ching! Let's see who owes us!", "Every invoice tells a story. This one says: PAY ME!", "Sorting invoices... alphabetically or by vibes?", "Invoice time! Time to make it rain (Pula)!", "The superhero's real power? Getting paid on time!", "Overdue invoices beware — the squad is HERE!", "Faster invoicing, faster payments, faster service!", "Let's turn those drafts into Posted invoices!", "Alpha Lady says: no invoice goes unfollowed!", "Bill them! Bill them all! (Professionally, of course.)", "INV-2026-AWESOME coming right up!", "Dear customer: please pay. Love, the AD squad.", "Premium invoices are our love language.", "The invoice list: a superhero's to-do list!", "Time to chase that money! Capes ready!"

- `banking` (15 quotes): "That's... MAAASSSIVE! Let's reconcile!", "Matching transactions like a hero matches capes to outfits!", "Let's make these bank lines behave!", "Bank recon mode: ACTIVATED!", "Every cent must be accounted for. Every thebe!", "FNB statements? We eat those for breakfast!", "Auto-matching engaged! Stand back!", "The shield protects your bank balance!", "Unmatched lines? Not on our watch!", "Reconciliation is our middle name! (Not really, but still.)", "Statement imported! Time to match and dispatch!", "Alpha Boy vs unmatched transactions: Round 1!", "Balancing the books, one bank line at a time!", "The squad never leaves a transaction unmatched!", "Bank recon: boring for others, heroic for us!"

- `quick-entry` (15 quotes): "Quick entry! Because superheroes don't wait!", "Faster, cheaper, smarter entry!", "Zap! Your transaction is almost done!", "Speed mode: ON! Let's record this!", "Quick like lightning, accurate like a laser!", "Alpha Lady doesn't have time for slow forms!", "One entry, two entry, three entry — done!", "The fastest entry in all of Botswana!", "Quick entry: saving you time since 2026!", "Need speed? You came to the right page!", "Blink and you'll miss it — that's how fast we are!", "The express lane of financial transactions!", "No capes required for quick entry... but they help!", "Record it, post it, done. Superhero style!", "Alpha Direct: where quick doesn't mean careless!"

- `settings` (15 quotes): "Welcome to HQ — handle with care!", "The engine room of Alpha Direct. Mind the buttons!", "Where the real magic happens behind the scenes.", "Settings: the superhero's utility belt!", "Chart of accounts? Audit log? We've got it all!", "Careful in here — great power, great responsibility!", "Alpha Boy says: only admins beyond this point!", "System configuration mode: engaged!", "The backbone of your financial system lives here!", "Fiscal periods, tax rates, users — the holy trinity!", "This is where finance admins become finance heroes!", "Settings page: no cape required, just admin access!", "Configure wisely, young hero!", "The control room of the AD financial universe!", "Behind every great dashboard is a well-configured settings page!"

- `trial-balance` (9 quotes): "Debits = Credits. That's the superhero's creed!", "Trial balance loading... please be balanced!", "If it doesn't balance, Alpha Boy will find out why!", "Opening + movements = closing. Simple as that!", "The ultimate test: does it balance?", "Trial balance: where every account tells its truth!", "Alpha Lady checks the TB before anyone else!", "A balanced TB is a happy TB!", "Dr + Cr = Harmony in the financial universe!"

- `profit-loss` (9 quotes): "Revenue minus expenses = the moment of truth!", "P&L time! Please be profitable, please be profitable...", "Alpha Boy says: keep revenue UP, expenses DOWN!", "The profit dance is about to begin!", "Income statement loading... fingers crossed!", "Gross result looking good? Time to celebrate!", "Net profit is the real superhero here!", "P&L: the report every CFO reads first!", "Revenue is vanity, profit is sanity!"

- `balance-sheet` (6 quotes): "Assets = Liabilities + Equity. The golden equation!", "Balance sheet: the financial snapshot of truth!", "Alpha Lady says: assets strong, equity growing!", "A=L+E — the superhero's formula!", "Checking the health of the company, one account at a time!", "Total equity looking solid? That's the goal!"

- `ar-aging` (5 quotes): "Who owes us? Let's find out!", "AR Aging: where overdue invoices can't hide!", "Current, 31-60, 61-90, 90+... the buckets of destiny!", "Alpha Boy is coming for those overdue payments!", "Receivables aging? More like receivables hunting!"

- `ap-aging` (4 quotes): "What do WE owe? Time to check!", "AP Aging: paying our heroes (vendors) on time!", "Vendor bills waiting patiently... or not so patiently!", "The squad always pays its debts!"

- `cash-position` (5 quotes): "Show me the money! All of it!", "Cash is king! Let's see the kingdom!", "BWP balance check: how's our vault looking?", "Alpha Boy guards the cash with his life!", "Cash position: the heartbeat of the business!"

- `tax-calendar` (5 quotes): "BURS deadlines wait for no hero!", "VAT, PAYE, WHT — the tax trio!", "Tax calendar: because penalties are the real villain!", "Stay compliant, stay heroic!", "Due dates approaching? The squad is on it!"

- `default` (12 quotes): "Another page, another adventure!", "Shield Hero is watching over your data!", "Faster, cheaper, smarter insurance!", "The AD family has your back!", "Let's do this! One Pula at a time!", "Cape: on. Mask: ready. Let's go!", "Every page is a new mission for the squad!", "Loading with superhero speed!", "Alpha Direct: where insurance meets awesome!", "The financial universe at your fingertips!", "Heroes don't skip pages — they conquer them!", "Botswana's insurance heroes, at your service!"

Also add KPI card-specific quotes:
```typescript
export const kpiQuotes: Record<string, string[]> = {
  "Total Cash": ["Cash reserves looking healthy!", "That's... MAAASSSIVE!", "The vault is strong!", "Ka-ching! Sweet cash!"],
  "Gross Written Premium": ["Premiums looking strong!", "Writing policies like a boss!", "GWP is the hero metric!", "Keep writing, keep earning!"],
  "Total Premiums Collected": ["Collections on track! Ka-ching!", "Keep the Pula flowing!", "Collected and accounted for!", "Money in, shield up!"],
  "Total Claims": ["Claims creeping up... watch this one!", "Nothing the squad can't handle!", "Claims managed, risk controlled!", "Stay vigilant, heroes!"],
  "Reinsurance Ceded": ["Spreading risk wisely!", "Reinsurance doing its job!", "Smart risk management!", "Shield shared, risk halved!"],
  "Reinsurance Recoveries": ["Getting some back! Nice!", "Recoveries coming through!", "Reinsurers paying up!", "Money coming home!"],
};
```

Export a `getQuote(page: string): string` function that picks a random quote from the matching page category (fallback to `default`).

Do NOT modify any existing files.
```

---

## PROMPT 3: Create Theme Context Provider

```
Create a React context provider for theme state management in our Alpha Direct ERP frontend.

Create `frontend/src/contexts/ThemeContext.tsx`:

```typescript
'use client';

import { createContext, useContext, useState, useCallback, ReactNode } from 'react';
import { themes, type ThemeKey, type Theme } from '@/lib/themes';

interface ThemeContextValue {
  themeKey: ThemeKey;
  theme: Theme;
  setTheme: (key: ThemeKey) => void;
  isFun: boolean;
  reduceMotion: boolean;
  toggleReduceMotion: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [themeKey, setThemeKey] = useState<ThemeKey>('light');
  const [reduceMotion, setReduceMotion] = useState(false);

  const setTheme = useCallback((key: ThemeKey) => {
    setThemeKey(key);
    // Persist preference
    if (typeof window !== 'undefined') {
      localStorage.setItem('alpha_theme', key);
    }
  }, []);

  const toggleReduceMotion = useCallback(() => {
    setReduceMotion(prev => {
      const next = !prev;
      if (typeof window !== 'undefined') {
        localStorage.setItem('alpha_reduce_motion', String(next));
      }
      return next;
    });
  }, []);

  // Load persisted preferences on mount
  // (add useEffect to load from localStorage)

  return (
    <ThemeContext.Provider value={{
      themeKey,
      theme: themes[themeKey],
      setTheme,
      isFun: themeKey === 'fun',
      reduceMotion,
      toggleReduceMotion,
    }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
  return ctx;
}
```

Then wrap the app in `frontend/src/app/layout.tsx` — add `<ThemeProvider>` around `{children}` inside the body tag. Import from `@/contexts/ThemeContext`.

Also create `frontend/src/contexts/QuoteContext.tsx` that provides:
- `quote: string` — the current page quote
- `refreshQuote(page: string): void` — picks a new random quote for the given page
- Use the `getQuote` function from `@/lib/quotes`

Wrap the app with both providers in layout.tsx.
```

---

## PROMPT 4: Redesign the Sidebar Component

```
Completely rewrite the sidebar component at `frontend/src/components/layout/Sidebar.tsx`.

Read the current file first, then rewrite it with these changes:

CRITICAL: Read `docs/design-system/SKILL.md` and the design system references before starting.

**New Navigation Structure (EXACT order):**

Top-level items:
1. Home (icon: Home from lucide-react) — navigates to `/`
2. Dashboard (icon: LayoutDashboard) — navigates to `/dashboard`
3. Quick Entry (icon: Zap) — navigates to `/quick-entry`

--- divider ---

Collapsible groups:
4. Debtors (icon: Users) — collapsible, contains:
   - Customer Invoices (icon: FileText) → `/invoices`
   - AR Aging (icon: Clock) → `/reports/ar-aging`
   - Receipts (icon: Receipt) → `/payments` (filtered)
   - Customers (icon: UserCheck) → `/contacts` (filtered)

5. Payables (icon: CreditCard) — collapsible, contains:
   - Vendor Bills (icon: FileText) → `/invoices` (filtered for bills)
   - AP Aging (icon: Clock) → `/reports/ap-aging`
   - Payments (icon: Banknote) → `/payments` (filtered)
   - Vendors & Brokers (icon: Building2) → `/contacts` (filtered)

6. Reporting (icon: BarChart3) — collapsible, contains:
   - Trial Balance (icon: Scale) → `/reports/trial-balance`
   - Profit & Loss (icon: TrendingUp) → `/reports/profit-loss`
   - Balance Sheet (icon: Layers) → `/reports/balance-sheet`
   - Cash Position (icon: CircleDollarSign) → `/reports/cash-position`
   - General Ledger (icon: BookOpen) → `/reports/general-ledger`

Standalone items AFTER the Reporting group (NOT inside any group):
7. Bank Reconciliation (icon: Landmark) → `/banking`
8. Tax Calendar (icon: Calendar) → `/tax-calendar`

--- flex spacer ---

Bottom:
9. Settings (icon: Settings) → `/settings` (above the divider)
10. User profile section with avatar initials, name, role label, logout button

**Styling:**
- Use the theme from `useTheme()` context — apply `theme.sidebar` as background
- Active item: orange left border 3px, `rgba(240,127,0,0.15)` background, orange text
- Inactive items: `rgba(255,255,255,0.7)` text
- Collapsible groups: ChevronDown/ChevronRight toggle, subtle background on expanded header
- Sidebar width: 260px expanded, 64px collapsed
- Logo area at top: Use `BRAND.logo.white` from `@/lib/brand` — show full logo when expanded (width 160px), just the logo mark when collapsed (width 36px)
- Smooth width transition: `transition: width 0.2s`
- The sidebar should NOT render at all when on the home page (path === '/')

**Technical:**
- `'use client'` component
- Use `usePathname()` from `next/navigation` for active state detection
- Use `useRouter()` for navigation
- Accept `collapsed` and `onToggle` props from parent layout
- The existing collapsible behavior should be preserved
- Mobile: hamburger menu overlay (same as current)
```

---

## PROMPT 5: Redesign the TopBar Component

```
Rewrite the topbar component at `frontend/src/components/layout/TopBar.tsx`.

Read the current file first, then rewrite:

CRITICAL: Read `docs/design-system/SKILL.md` before starting.

**Structure:**
- Height: 56px
- Background: `theme.topbar` from useTheme()
- Border bottom: `1px solid theme.topbarBdr`
- Flexbox: space-between

**Left side:**
- Collapse toggle button (ChevronLeft when expanded, Menu when collapsed)
- Breadcrumb: "Home" if on home, otherwise "Home / Page Name" (derive from pathname)
- Funny quote: italic, orange color, `opacity: 0.8`, preceded by em-dash. Use `useQuote()` from QuoteContext — the quote changes on every page navigation

**Right side:**
- View Mode toggle: a single Eye icon button. On click, shows a dropdown with:
  - "View Mode" header (small, muted)
  - "Light" option (Sun icon) — shows "Active" tag when selected
  - "Fun Mode" option (Sparkles icon) — shows "Active" tag when selected
  - Divider line
  - "Reduce Motion" toggle (Eye/EyeOff icon) — shows "On" tag when active
  - Dropdown: absolute positioned, top: 110%, right: 0, card background, border, rounded 10px, shadow, z-index 999, min-width 150px
  - Click outside should close the dropdown
- Search placeholder: gray background pill with Search icon and "Search..." text
- Notification bell with red dot indicator
- The topbar should NOT render when on the home page

**Technical:**
- `'use client'` component
- Use `useTheme()` for all colors
- Use `useQuote()` for the funny quote text
- The dropdown should close when clicking outside (useRef + useEffect pattern)
```

---

## PROMPT 6: Create the Home Page (Landing)

```
Create a new home page that replaces the current dashboard at `frontend/src/app/(dashboard)/page.tsx`.

BUT FIRST — we need to restructure routing. The home page should be the ROOT page and should NOT show the sidebar/topbar.

**Approach:**

1. Move the current `frontend/src/app/(dashboard)/page.tsx` (dashboard) to `frontend/src/app/(dashboard)/dashboard/page.tsx`

2. Create a NEW `frontend/src/app/page.tsx` (outside the dashboard group) — this is the Home page:

This page renders OUTSIDE the (dashboard) layout, so it has NO sidebar and NO topbar. It's a full-screen landing.

**Home Page Design:**
- Full viewport height, centered content
- Background: `theme.bg` from useTheme()
- If fun mode: render `<FunBackground />` behind the content (see Prompt 7)
- Top-right corner: View Mode toggle (Eye icon dropdown, same as topbar but standalone)
- Center content (vertically + horizontally):
  - Alpha Direct logo: full color in light mode, white monotone in fun mode (width ~340px)
  - Two mascot images flanking a speech bubble:
    - Left: boy-dab mascot (width ~140px)
    - Center: speech bubble card with the current funny quote (from home quotes)
    - Right: girl-fly mascot (width ~140px)
  - Title: "Financial Management System" (font-size 26px, bold, navy)
  - Subtitle: "Your insurance finance hub — premiums, claims, reconciliation, and reporting all in one place." (font-size 14px, muted, max-width 420px)
  - Two action buttons:
    - "Quick Entry" (orange background, white text, Zap icon, box-shadow) → navigates to `/quick-entry`
    - "Go to Dashboard" (navy background, white text, LayoutDashboard icon) → navigates to `/dashboard`

**Auth check:** This page should still check for auth token. If no token, redirect to `/login`. If token exists, show the home page.

**Technical:**
- `'use client'` component
- Use `useTheme()` and `useQuote()` contexts
- Use `next/image` for the logo and mascot images (import from `@/lib/brand`)
- Use `useRouter()` for button navigation
```

---

## PROMPT 7: Create Fun Mode Background Component

```
Create `frontend/src/components/FunBackground.tsx`:

This is a Gmail-style themed background with scattered mascot images and fun text at very low opacity. It renders as a fixed, full-screen overlay behind all content (pointer-events: none).

**Design:**
- Fixed position, full viewport, z-index 0, pointer-events: none, overflow: hidden
- Deep gradient background: `radial-gradient(ellipse at 30% 20%, #1A1060 0%, #0A0A30 50%, #050520 100%)`
- 16 scattered elements, mix of mascot images and fun text phrases:
  - Every 4th element is a text phrase, the rest are mascot images
  - Text phrases: "Faster, Cheaper, Smarter!", "That's MAAASSSIVE!", "Ka-ching!", "Shield Up!", "Go Alpha!", "Insure it!", "We got you!", "One Pula at a time!", "Cape mode: ON!", "Dumela!"
  - Mascots: cycle through all mascot images from BRAND.mascots
- Each element positioned with pseudo-random placement:
  - x = (i * 17 + 5) % 85 + 5 (as percentage)
  - y = (i * 21 + 8) % 82 + 5 (as percentage)
  - rotation = (i * 11) % 30 - 15 degrees
  - scale = 0.3 + (i % 3) * 0.15
  - opacity: 0.07 for text, 0.05 for images
  - Image width: 100 + (i % 3) * 20 px
- Bottom gradient fade: 30% height, from transparent to rgba(10,10,48,0.6)

**Props:**
- `active: boolean` — if false, return null (only renders in fun mode)

**Technical:**
- `'use client'` component
- Import mascot paths from `@/lib/brand`
- Use next/image for the mascot images
```

---

## PROMPT 8: Create Loading Screen Component

```
Create `frontend/src/components/LoadingScreen.tsx`:

This replaces the current loading states. It shows a mascot image cycling with a page-contextual funny quote.

**Design:**
- Centered flex column with padding 60px, gap 16px
- Mascot image: cycles through 4 mascots (boy-stand, girl-fly, super-1, boy-dab) every 800ms
  - Image width: 120px, auto height
- Quote text: page-specific funny quote from the quotes library
  - Font-size 15px, bold, orange color, centered
  - This quote is picked ONCE when the component mounts (using `useState(() => getQuote(page))`)
  - It should be DIFFERENT from the topbar quote (both are random picks)
- Progress bar:
  - Container: width 180px, height 4px, rounded, gray background (theme.g200)
  - Fill: orange, 70% width, rounded

**Props:**
- `page: string` — the page key for contextual quotes (e.g., "dashboard", "invoices", "banking")

**Technical:**
- `'use client'` component
- Use `useTheme()` for colors
- Use `getQuote()` from `@/lib/quotes` (NOT the context — this is independent)
- Use `useState` + `useEffect` with `setInterval` for mascot cycling
- If `reduceMotion` is true from theme context, don't cycle mascots (just show first one)
```

---

## PROMPT 9: Create KPI Mascot Popup Component

```
Create `frontend/src/components/KpiPopup.tsx`:

When the user hovers over a KPI card on the dashboard, a small mascot with a funny speech bubble pops up in the bottom-right corner.

**Design:**
- Fixed position, bottom 20px, right 20px, z-index 100
- Flex row: speech bubble card on left, boy-stand mascot (52px wide) on right
- Speech bubble: card background, border, rounded 14px, padding 8px 14px, font-size 13px, max-width 200px, card shadow
- Quote text comes from `kpiQuotes` in quotes.ts, matched by the KPI card label
- Popup appears on hover with a fade-in, disappears after 3.5 seconds or when hover leaves
- If `reduceMotion` is true, don't show (return null)

**Props:**
- `cardLabel: string | null` — the label of the hovered KPI card (null = hide)

**Technical:**
- `'use client'` component
- Use `useTheme()` for styling
- Use `useEffect` to auto-hide after 3500ms
- Import `kpiQuotes` from `@/lib/quotes`
- Import `BRAND` from `@/lib/brand` for mascot image
```

---

## PROMPT 10: Update Dashboard Layout to Support Home Page

```
Update the dashboard layout at `frontend/src/app/(dashboard)/layout.tsx`:

The dashboard layout wraps all pages EXCEPT the home page (which is now at the root level outside the group).

Changes needed:
1. Import and use `useTheme()` for dynamic background color: `style={{ backgroundColor: theme.bg }}`
2. The sidebar and topbar should use theme colors
3. Add the `<FunBackground active={theme.funBg} />` component inside the layout (before sidebar)
4. Pass the current quote to the topbar via QuoteContext (the quote should refresh on every page navigation)
5. The layout continues to handle auth checking (redirect to /login if no token)
6. The main content area should show `<LoadingScreen page={currentPage} />` during page transitions (use a brief loading state when navigating)

Also update `frontend/src/app/layout.tsx` (root layout):
- Wrap everything with `<ThemeProvider>` and `<QuoteProvider>`
- The body tag should NOT have hardcoded background colors — let the theme handle it
```

---

## PROMPT 11: Create Settings Page

```
Create a proper settings page at `frontend/src/app/(dashboard)/settings/page.tsx`.

**Design:**
- Page title: "Settings" (28px, bold, navy)
- Subtitle: "System configuration, chart of accounts, audit log, and administration"
- Grid of setting cards (2 columns):

Each card is clickable and navigates to the relevant page:
1. Chart of Accounts (icon: BookOpen, desc: "GL accounts") → `/accounts`
2. Audit Log (icon: ClipboardList, desc: "System activity") → `/audit-log` (placeholder)
3. Fiscal Periods (icon: Calendar, desc: "Accounting periods") → `/fiscal-periods` (placeholder)
4. Tax Rates (icon: Receipt, desc: "VAT, WHT config") → `/tax-rates` (placeholder)
5. Bank Accounts (icon: Landmark, desc: "Bank details") → `/bank-accounts` (placeholder)
6. Currencies & Rates (icon: DollarSign, desc: "Exchange rates") → `/currencies` (placeholder)
7. Users & Roles (icon: UserCheck, desc: "Permissions") → `/users` (placeholder)

**Card styling:**
- Card background, border, shadow from theme
- 42x42px icon container with orange-light background, orange icon
- Title: 14px, bold, navy
- Description: 12px, muted
- Cursor pointer, flex row with icon left and text right

**Technical:**
- `'use client'` component
- Use `useTheme()` for all colors
- Use `useRouter()` for navigation
```

---

## PROMPT 12: Wire Up Navigation and Route Changes

```
Now we need to ensure all the navigation routing works correctly with the new structure.

1. Create `/frontend/src/app/(dashboard)/dashboard/page.tsx` — move the current dashboard page content here (currently at `/frontend/src/app/(dashboard)/page.tsx`)

2. Update `/frontend/src/app/(dashboard)/page.tsx` to redirect to `/dashboard`:
```typescript
'use client';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
export default function DashboardIndex() {
  const router = useRouter();
  useEffect(() => { router.replace('/dashboard'); }, [router]);
  return null;
}
```

3. Create the root home page at `/frontend/src/app/page.tsx` (outside dashboard group) with the home page design from Prompt 6. Make sure it:
   - Does NOT use the (dashboard) layout (no sidebar/topbar)
   - Has its own auth check
   - Has the View Mode eye icon in the top-right corner
   - Has the FunBackground when in fun mode

4. Update the root layout at `frontend/src/app/layout.tsx` to NOT hardcode any background colors on the body — let ThemeProvider handle it.

5. Make sure the login page at `/frontend/src/app/login/page.tsx` redirects to `/` (home) instead of `/dashboard` after login.

6. Verify all sidebar navigation links point to correct routes:
   - Home → `/`
   - Dashboard → `/dashboard`
   - Quick Entry → `/quick-entry`
   - Customer Invoices → `/invoices`
   - AR Aging → `/reports/ar-aging`
   - etc. (all routes from Prompt 4)

Test by running `npm run dev` and navigating between pages.
```

---

## PROMPT 13: Polish and Integration Testing

```
Do a final integration pass on the frontend redesign:

1. **Test all navigation paths**: Visit every route and verify:
   - Home page shows without sidebar/topbar
   - Clicking "Go to Dashboard" or "Quick Entry" shows sidebar/topbar
   - All sidebar nav items navigate correctly
   - Collapsible groups expand/collapse properly
   - Active state highlights correctly in sidebar
   - "Home" nav item returns to full-screen home page

2. **Test theme system**:
   - Eye icon dropdown works on both home page and topbar
   - Switching to Fun Mode applies dark color scheme everywhere
   - Fun Mode shows scattered mascot background
   - Switching back to Light clears fun background
   - Theme preference persists across page reloads (localStorage)
   - Reduce Motion toggle works

3. **Test quotes**:
   - Every page navigation shows a new contextual quote in the topbar breadcrumb
   - Loading screen shows a page-specific quote
   - Home page speech bubble shows a home quote
   - KPI card hover shows mascot popup with relevant quote (dashboard only)
   - Quotes are randomized (reload several times to verify variety)

4. **Test responsive behavior**:
   - Mobile hamburger menu works
   - Sidebar collapses properly
   - KPI cards stack on small screens

5. **Fix any TypeScript errors** — run `npx tsc --noEmit` and fix all type issues.

6. **Fix any ESLint warnings** — run `npx eslint src/ --fix`.

7. **Verify the existing functionality still works**:
   - Login/logout flow
   - Dashboard data loading from API
   - Invoice list, payment list, etc. still fetch and display data
   - Report pages still work
   - Bank reconciliation page still works

Report any issues found and fix them.
```

---

## Summary — Prompt Execution Order

| # | Prompt | What it does | Dependencies |
|---|--------|-------------|--------------|
| 1 | Brand Assets | Copy + optimize images, create brand.ts | None |
| 2 | Themes + Quotes | Create themes.ts and quotes.ts libraries | None |
| 3 | Theme Context | Create ThemeProvider + QuoteProvider, wrap app | Prompts 1-2 |
| 4 | Sidebar | Rewrite sidebar with new nav structure | Prompts 1-3 |
| 5 | TopBar | Rewrite topbar with eye icon dropdown + quotes | Prompts 1-3 |
| 6 | Home Page | Create full-screen landing page | Prompts 1-3 |
| 7 | Fun Background | Gmail-style mascot background component | Prompt 1 |
| 8 | Loading Screen | Page-contextual loading with mascots | Prompts 1-2 |
| 9 | KPI Popup | Hover mascot popup for dashboard | Prompts 1-2 |
| 10 | Dashboard Layout | Update layout for themes + fun bg | Prompts 3-9 |
| 11 | Settings Page | New settings hub page | Prompts 3-5 |
| 12 | Route Wiring | Fix all routes, redirects, navigation | Prompts 4-11 |
| 13 | Polish & Test | Integration testing, fix issues | All above |

**Prompts 1 and 2 can run in parallel.**
**Prompts 4, 5, 6, 7, 8, 9 can run in parallel after Prompt 3.**
**Prompts 10-13 must run sequentially.**

---

## Notes for Claude Code

- Always read `docs/design-system/SKILL.md` before any styling work
- The design system colors are: Navy `#0B0B3B`, Orange `#F07F00`
- All existing API integration must continue working — do NOT change `src/lib/api.ts`
- Do NOT change any Django backend code
- Auth token is in `localStorage` as `alpha_token`
- The `redesign-final.jsx` in the project root is the approved visual reference — match it exactly
- Use Tailwind CSS v4 classes where possible, inline styles only for theme-dynamic values
- Font is Inter (already configured)
- Test on port 3001 (3000 may be occupied)
