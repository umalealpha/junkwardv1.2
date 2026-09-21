# Alpha Direct — Layout System Reference

## Grid Foundation

Base unit: **8px**. All spacing, sizing, and positioning should align to multiples of 8px (with 4px allowed for fine adjustments like icon padding).

## Application Shell

The ERP uses a fixed sidebar + scrollable content layout:

```
┌──────────────────────────────────────────────────────────────┐
│  TOP BAR (h: 56px, bg: white, border-bottom: 1px #E5E7EB)   │
│  [Logo/Mark]  [Search ────────]        [Bell] [User Avatar]  │
├──────────┬───────────────────────────────────────────────────┤
│          │  BREADCRUMB BAR (h: 48px, bg: white)              │
│          │  Home > Module > Current Page                     │
│          ├───────────────────────────────────────────────────┤
│          │  PAGE HEADER (py: 24px)                           │
│ SIDEBAR  │  Title                        [Action Buttons]    │
│ w: 240px │  Subtitle / description                           │
│ bg: navy ├───────────────────────────────────────────────────┤
│          │                                                   │
│ [icon] Dashboard │  CONTENT AREA                             │
│ [icon] Policies  │  bg: #F9FAFB                              │
│ [icon] Claims    │  padding: 24px                            │
│ [icon] Finance ▾ │  max-width: 1440px                        │
│    ├ Invoices    │                                           │
│    ├ Payments    │  ┌─Card──────┐  ┌─Card──────┐             │
│    ├ Reports     │  │           │  │           │             │
│    └ Journal     │  └───────────┘  └───────────┘             │
│ [icon] Settings  │                                           │
│          │                                                   │
├──────────┴───────────────────────────────────────────────────┤
│  FOOTER (optional, h: 40px, text: gray-400, center)          │
│  © 2026 Alpha Direct Insurance Co. — Gaborone, Botswana      │
└──────────────────────────────────────────────────────────────┘
```

### Top Bar (56px)
- Background: white
- Left: Logo mark (AD compact or full logo, max-height 36px)
- Center: Optional search bar (max-width 480px)
- Right: Notification bell icon, user avatar with dropdown
- Bottom border: `1px solid var(--ad-border)`
- Padding: `0 24px`
- z-index: 50 (sticky top)

### Sidebar (240px)
- Background: `var(--ad-navy)` / `#0B0B3B`
- Width: 240px expanded, 64px collapsed
- Position: fixed left, full height below topbar
- Transition: `width 0.2s ease`
- Logo at top: White version, padded `16px 20px`, height 48px area
- Nav items: `height: 40px`, `padding: 0 20px`, white text at 14px
- Active item: Orange left border (3px), subtle orange bg tint
- Hover: `rgba(255,255,255,0.08)` background
- Section dividers: `1px solid rgba(255,255,255,0.1)`, margin `8px 0`
- Sub-menu items: Indented 20px, font-size 13px
- Collapse trigger: Hamburger icon or chevron at bottom
- Scrollable: If nav items exceed viewport, the nav section scrolls independently

### Breadcrumb Bar (48px)
- Background: white
- Text: 13px, `var(--ad-gray-500)`
- Current page: `var(--ad-gray-900)`, font-weight 500
- Separator: `/` or chevron icon, color `var(--ad-gray-300)`
- Padding: `0 24px`
- Border-bottom: `1px solid var(--ad-border)`

### Page Header
- Padding: `24px 0`
- Title: 24px, weight 700, color `var(--ad-navy)`
- Subtitle: 14px, color `var(--ad-gray-500)`, margin-top 4px
- Action buttons: Aligned right, same vertical level as title

### Content Area
- Background: `var(--ad-gray-50)` / `#F9FAFB`
- Padding: `24px`
- Max-width: `1440px`
- Scroll: Vertical scroll, independent of sidebar

## Responsive Breakpoints

| Breakpoint | Width | Sidebar | Layout Notes |
|-----------|-------|---------|--------------|
| Desktop XL | ≥1440px | 240px expanded | Full layout, content max-width |
| Desktop | ≥1024px | 240px expanded | Standard layout |
| Tablet | ≥768px | 64px collapsed (icon-only) | Cards stack to single column |
| Mobile | <768px | Hidden (hamburger menu overlay) | Full-width, stacked |

The ERP is primarily a desktop application, so optimize for 1024px+ first. Mobile is secondary but should not break.

## Page Templates

### Dashboard Page
```
[KPI Card] [KPI Card] [KPI Card] [KPI Card]     ← 4-column grid
[Chart Card ─────────────] [Activity Feed ──]     ← 2:1 ratio
[Recent Table ───────────────────────────────]    ← Full width
```

### List/Table Page (e.g., Invoices, Policies)
```
[Filter Bar ─────────────────────────────────]    ← Full width
[Data Table ─────────────────────────────────]    ← Full width
[Pagination ─────────────────────────────────]    ← Full width
```

### Detail Page (e.g., Single Policy, Single Invoice)
```
[Header: Title + Status Badge + Actions ─────]
[Summary Card ─────────] [Side Info Card ────]    ← 2:1 ratio
[Tab Bar: Details | History | Documents ─────]
[Tab Content ────────────────────────────────]    ← Full width
```

### Form Page (e.g., Create Invoice, New Policy)
```
[Form Title ─────────────────────────────────]
[Form Card ──────────────────────────────────]    ← Max-width 800px, centered
  Section 1: Basic Info
  [Input] [Input]                                 ← 2-column grid within card
  [Input] [Input]
  Section 2: Details
  [Input] [Input] [Input]                         ← 3-column where appropriate
  [Textarea ─────────────────────────────────]    ← Full width within card
  [Cancel]                           [Save Draft] [Submit]
```

### Report Page
```
[Date Range Picker] [Filter] [Filter] [Export]    ← Filter bar
[Summary KPIs ───────────────────────────────]    ← Full width, small cards
[Chart ──────────────────────────────────────]    ← Full width
[Detailed Table ─────────────────────────────]    ← Full width
```

## Card Grid System

Use CSS Grid with consistent gaps:

```css
/* KPI row — 4 equal columns */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}

/* Dashboard main — 2:1 split */
.dashboard-main {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 16px;
}

/* Form — 2-column inputs */
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

/* At tablet (< 1024px), collapse to single column */
@media (max-width: 1023px) {
  .kpi-grid { grid-template-columns: repeat(2, 1fr); }
  .dashboard-main { grid-template-columns: 1fr; }
}

@media (max-width: 767px) {
  .kpi-grid { grid-template-columns: 1fr; }
  .form-grid { grid-template-columns: 1fr; }
}
```

## Z-Index Scale

| Layer | z-index | Usage |
|-------|---------|-------|
| Base content | 0 | Normal page flow |
| Sticky elements | 10 | Sticky table headers |
| Sidebar | 30 | Fixed sidebar |
| Top bar | 50 | Fixed top bar |
| Dropdowns | 100 | Menus, selects |
| Modal backdrop | 200 | Dimmed overlay |
| Modal | 210 | Modal dialog |
| Toast/Snackbar | 300 | Notification toasts |
| Tooltip | 400 | Hover tooltips |

## Animation & Transitions

Keep animations subtle and functional — this is a business tool, not a marketing site.

```css
/* Standard transition for interactive elements */
--ad-transition-fast: 150ms ease;
--ad-transition-base: 200ms ease;
--ad-transition-slow: 300ms ease;

/* Use for: */
/* Fast: button hover, focus rings */
/* Base: sidebar collapse, dropdown open */
/* Slow: modal appear, page transitions */
```

- Sidebar collapse: `width 0.2s ease`
- Dropdown menus: `opacity 0.15s ease, transform 0.15s ease` (translate-y from -4px to 0)
- Modal: fade backdrop 0.2s, scale dialog from 0.95 to 1.0 in 0.2s
- Toast: slide in from right, 0.3s ease
- No bouncing, no spring physics, no playful animations
