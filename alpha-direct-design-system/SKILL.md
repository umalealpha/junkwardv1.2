---
name: alpha-direct-design-system
description: >
  The official design system and UI/UX guidelines for Alpha Direct Insurance Company's ERP system and all digital products.
  Use this skill whenever building, designing, styling, or generating ANY user interface, frontend component, dashboard,
  page, screen, React artifact, HTML output, presentation, document, or visual asset for Alpha Direct Insurance.
  This includes: ERP modules (financial management, policy management, claims, reports), internal tools, dashboards,
  login screens, forms, tables, charts, navigation, sidebars, modals, buttons, cards, and any other UI element.
  Also trigger when the user mentions "Alpha Direct", "our brand", "our design", "our colors", "our style",
  "match our branding", "ERP interface", "insurance UI", or asks to build anything visual for their system.
  This skill overrides default styling choices — never invent new colors, fonts, or patterns unless explicitly asked.
---

# Alpha Direct Insurance — Design System

This is the single source of truth for all visual design at Alpha Direct Insurance Company (Botswana).
Every UI, artifact, component, document, and visual output must conform to these rules.

## Quick Start — The 5 Non-Negotiable Rules

1. **Navy and Orange only.** Primary palette is Navy `#0B0B3B` and Orange `#F07F00`. No purples, no teals, no random gradients.
2. **Clean, professional, simple.** Alpha Direct serves everyday insurance customers. The UI must feel trustworthy and easy to understand — not flashy or overly technical.
3. **Consistent spacing and hierarchy.** Use the 8px grid. White space is your friend.
4. **Readable typography.** Use Inter for UI text, with clear size hierarchy. Never use decorative or playful fonts in the application.
5. **Insurance context always.** Every screen should feel like it belongs in a professional insurance company's internal system.

## Brand Identity

**Company:** Alpha Direct Insurance Co.™
**Sub-brand:** Alpha Direct Insurtech
**Location:** Gaborone, Botswana
**Industry:** Insurance (motor, property, life, commercial)
**Tagline positioning:** Direct, fast, trustworthy insurance
**Brand personality:** Professional, approachable, modern, reliable, direct

The brand uses a distinctive logo with "Alpha" in navy and "Direct" in orange, connected by a swooping arrow motif beneath the word "Direct". This arrow conveys forward momentum and directness.

## Color System

Read `references/colors.md` for the full color specification including all shades, semantic tokens, and usage rules.

### Primary Colors (use these most)
| Token | Hex | Usage |
|-------|-----|-------|
| `--ad-navy` | `#0B0B3B` | Primary brand color. Headers, sidebar, primary buttons, key text |
| `--ad-orange` | `#F07F00` | Accent/CTA color. Action buttons, highlights, active states, links |

### Critical Ratios
- Navy should appear 3–4× more than orange in any given screen
- Orange is for drawing attention — use it sparingly and intentionally
- Never use orange for large background fills (except hero banners with white text)

## Typography

**Primary UI Font:** `Inter` (Google Fonts) — used for all application interfaces
**Fallback stack:** `Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`
**Logo/Brand font:** Myriad Pro Bold (logo use only — do not use in UI)

### Type Scale (8px grid)
| Role | Size | Weight | Line Height | Usage |
|------|------|--------|-------------|-------|
| Display | 32px | 700 | 40px | Page titles, dashboard headlines |
| Heading 1 | 24px | 600 | 32px | Section headers |
| Heading 2 | 20px | 600 | 28px | Card titles, sub-sections |
| Heading 3 | 16px | 600 | 24px | Widget headers, group labels |
| Body | 14px | 400 | 20px | Default text, descriptions, table cells |
| Body Small | 13px | 400 | 18px | Secondary info, help text |
| Caption | 12px | 400 | 16px | Labels, timestamps, metadata |
| Overline | 11px | 600 | 16px | Category labels, uppercase tags |

## Layout System

Read `references/layout.md` for the full layout specification.

### Core Structure
All ERP screens follow this shell:
```
┌─────────────────────────────────────────────────┐
│  Top Bar (56px) — logo, search, user, notifs    │
├────────┬────────────────────────────────────────┤
│        │  Breadcrumb / Page Header              │
│ Side   ├────────────────────────────────────────┤
│ bar    │                                        │
│ (240px)│  Main Content Area                     │
│ navy   │  (padded 24px, max-width: 1440px)      │
│ bg     │                                        │
│        │                                        │
├────────┴────────────────────────────────────────┤
│  (optional) Status bar / footer                 │
└─────────────────────────────────────────────────┘
```

- **Sidebar:** Navy background (`#0B0B3B`), white text, 240px wide, collapsible to 64px (icon-only)
- **Top bar:** White background, subtle bottom border `#E5E7EB`, 56px height
- **Content area:** `#F9FAFB` background, 24px padding
- **Cards:** White background, `border-radius: 8px`, `box-shadow: 0 1px 3px rgba(0,0,0,0.08)`

### Spacing Scale (8px base)
`4px · 8px · 12px · 16px · 24px · 32px · 48px · 64px`

## Component Patterns

Read `references/components.md` for detailed component specifications.

### Buttons
- **Primary:** Navy background, white text, 8px radius, 40px height
- **Secondary:** White background, navy border, navy text
- **Accent/CTA:** Orange background, white text — use for the single most important action on screen
- **Ghost:** Transparent background, navy text — for tertiary actions
- **Danger:** `#DC2626` background, white text — destructive actions only
- All buttons: `font-weight: 500`, `padding: 0 16px`, `font-size: 14px`

### Tables (critical for ERP)
- Header row: `#F3F4F6` background, `font-weight: 600`, `font-size: 12px`, uppercase
- Body rows: White background, `font-size: 14px`
- Alternating rows: Optional, use `#F9FAFB`
- Row hover: `#FFF7ED` (light orange tint)
- Selected row: `#FEF3C7` (warm highlight)
- Cell padding: `12px 16px`
- Borders: `1px solid #E5E7EB` horizontal only (no vertical grid lines)

### Cards
- Background: white
- Border-radius: 8px
- Padding: 20px (or 24px for large cards)
- Shadow: `0 1px 3px rgba(0,0,0,0.08)`
- Header area: Title left, action buttons right
- No colored top borders unless it's a status indicator card

### Forms
- Input height: 40px
- Border: `1px solid #D1D5DB`
- Border-radius: 6px
- Focus border: `2px solid #F07F00` (orange focus ring)
- Label: 13px, weight 500, color `#374151`, placed above input
- Error: `#DC2626` text, red left border on input
- Required field marker: orange asterisk

### Charts & Data Viz
- Primary data series: Navy `#0B0B3B`
- Secondary series: Orange `#F07F00`
- Additional series: `#3B82F6`, `#10B981`, `#8B5CF6`, `#F59E0B`
- Grid lines: `#E5E7EB`
- Background: white
- Use bar charts and line charts primarily — avoid pie charts for more than 4 segments
- Always include axis labels and a legend

## Status Colors (Semantic)
| Status | Color | Usage |
|--------|-------|-------|
| Success | `#059669` | Approved, paid, active, confirmed |
| Warning | `#D97706` | Pending, needs review, approaching deadline |
| Error | `#DC2626` | Rejected, overdue, failed, cancelled |
| Info | `#2563EB` | Informational, neutral notifications |
| Neutral | `#6B7280` | Draft, inactive, archived |

Use as pill/badge backgrounds at 10% opacity with full-color text: e.g., `background: #05966910; color: #059669`.

## Logo Usage

Logos are available in `assets/` directory. Key variants:
- **Full color** (navy + orange): For white/light backgrounds — default choice
- **Monotone navy:** For formal documents, single-color contexts
- **Monotone orange:** Rarely used — only for specific marketing
- **Monotone black:** For black-and-white printing
- **White:** For dark backgrounds (e.g., navy sidebar, dark hero)
- **Compact "AD" mark:** For favicons, small spaces, mobile headers

**Logo Rules:**
- Minimum clear space: Height of the "A" character around all sides
- Never stretch, rotate, or modify colors
- Never place full-color logo on a colored or busy background
- On the sidebar, use the white "AD" compact mark or full white logo

## Icons
- Use **Lucide React** icon set (already available in React artifacts)
- Style: 20px default size, `stroke-width: 1.5`
- Color: Inherit from parent text color
- In sidebar: 20px, white, with 8px gap before label text

## Dark Mode (Optional / Future)
When implementing dark mode:
- Sidebar: Stays navy (it's already dark)
- Background: `#111827`
- Cards: `#1F2937`
- Text: `#F9FAFB`
- Borders: `#374151`
- Keep orange accent unchanged

## Insurance-Specific UI Patterns

Since this is an insurance ERP, these domain patterns appear frequently:

- **Policy cards:** Show policy number, holder name, status badge, premium amount, expiry date
- **Claim status flows:** Use a horizontal stepper with status colors
- **Financial summaries:** Top-row KPI cards showing BWP amounts with trend arrows
- **Approval workflows:** Clear accept/reject buttons with confirmation modals
- **Document previews:** Thumbnail + filename + download button pattern
- **Currency:** Always format as `BWP 1,234.56` (Botswana Pula) unless told otherwise

## What NOT to Do

- Never use purple, teal, or pink as primary colors
- Never use gradient backgrounds on cards or content areas
- Never use dark-on-dark or low-contrast text
- Never use decorative fonts in the application UI
- Never use more than 2 font weights on a single component
- Never center-align body text (left-align always, except card stat numbers)
- Never use icon-only buttons without tooltips
- Never create a UI element that requires financial or accounting expertise to understand — always label clearly
- Never deviate from the color system without explicit user instruction

## File Reference

For deeper specifications, read these files in the `references/` directory:
- `references/colors.md` — Full color palette with all shades and semantic tokens
- `references/layout.md` — Detailed layout grid, responsive breakpoints, and page templates
- `references/components.md` — Detailed component specs, states, and code patterns

For logo files, check the `assets/` directory.
