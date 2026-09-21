# Alpha Direct — Color System Reference

## Source of Truth

Colors were extracted from the official brand assets:
- **Alpha Direct Logo Pantones.pdf** — Contains PANTONE 2738 C (navy), PANTONE 151 C (orange), PANTONE P 101-16 C (dark navy), PANTONE Reflex Blue C
- **Logo pixel analysis** — Navy `#000066` / `#0B0B3B`, Orange `#F07F00` / `#FD7E0C`

The hex values below are the standardized versions for screen use.

---

## CSS Custom Properties

Always define these at the `:root` level in every artifact and component:

```css
:root {
  /* ========== PRIMARY BRAND ========== */
  --ad-navy:          #0B0B3B;
  --ad-navy-light:    #1A1A5E;
  --ad-navy-dark:     #07074E;   /* PANTONE P 101-16 C */
  --ad-navy-50:       #EDEDF5;
  --ad-navy-100:      #D4D4E8;
  --ad-navy-200:      #A9A9D1;
  --ad-navy-300:      #7E7EBA;
  --ad-navy-400:      #4A4A8A;
  --ad-navy-500:      #0B0B3B;
  --ad-navy-600:      #090930;
  --ad-navy-700:      #070725;
  --ad-navy-800:      #05051A;
  --ad-navy-900:      #03030F;

  --ad-orange:        #F07F00;
  --ad-orange-light:  #FF9A2E;
  --ad-orange-dark:   #CC6C00;
  --ad-orange-50:     #FFF7ED;
  --ad-orange-100:    #FFEDD5;
  --ad-orange-200:    #FED7AA;
  --ad-orange-300:    #FDBA74;
  --ad-orange-400:    #FB923C;
  --ad-orange-500:    #F07F00;
  --ad-orange-600:    #CC6C00;
  --ad-orange-700:    #9A5200;
  --ad-orange-800:    #6B3900;
  --ad-orange-900:    #3D2100;

  /* ========== NEUTRALS (Gray Scale) ========== */
  --ad-white:         #FFFFFF;
  --ad-gray-50:       #F9FAFB;   /* Page backgrounds */
  --ad-gray-100:      #F3F4F6;   /* Table headers, subtle backgrounds */
  --ad-gray-200:      #E5E7EB;   /* Borders, dividers */
  --ad-gray-300:      #D1D5DB;   /* Input borders, disabled states */
  --ad-gray-400:      #9CA3AF;   /* Placeholder text, icons (inactive) */
  --ad-gray-500:      #6B7280;   /* Secondary text, captions */
  --ad-gray-600:      #4B5563;   /* Body text (secondary emphasis) */
  --ad-gray-700:      #374151;   /* Body text, labels */
  --ad-gray-800:      #1F2937;   /* Headings, strong text */
  --ad-gray-900:      #111827;   /* Maximum contrast text */
  --ad-black:         #000000;

  /* ========== SEMANTIC STATUS ========== */
  --ad-success:       #059669;
  --ad-success-light: #D1FAE5;
  --ad-success-bg:    #ECFDF5;

  --ad-warning:       #D97706;
  --ad-warning-light: #FEF3C7;
  --ad-warning-bg:    #FFFBEB;

  --ad-error:         #DC2626;
  --ad-error-light:   #FEE2E2;
  --ad-error-bg:      #FEF2F2;

  --ad-info:          #2563EB;
  --ad-info-light:    #DBEAFE;
  --ad-info-bg:       #EFF6FF;

  /* ========== FUNCTIONAL TOKENS ========== */
  --ad-bg-page:       var(--ad-gray-50);
  --ad-bg-card:       var(--ad-white);
  --ad-bg-sidebar:    var(--ad-navy);
  --ad-bg-topbar:     var(--ad-white);
  --ad-bg-modal:      var(--ad-white);
  --ad-bg-input:      var(--ad-white);
  --ad-bg-hover:      var(--ad-orange-50);
  --ad-bg-selected:   var(--ad-warning-light);
  --ad-bg-table-head: var(--ad-gray-100);
  --ad-bg-table-alt:  var(--ad-gray-50);

  --ad-text-primary:  var(--ad-gray-900);
  --ad-text-secondary:var(--ad-gray-500);
  --ad-text-heading:  var(--ad-navy);
  --ad-text-on-navy:  var(--ad-white);
  --ad-text-on-orange:var(--ad-white);
  --ad-text-link:     var(--ad-orange);
  --ad-text-link-hover:var(--ad-orange-dark);
  --ad-text-muted:    var(--ad-gray-400);
  --ad-text-label:    var(--ad-gray-700);
  --ad-text-error:    var(--ad-error);

  --ad-border:        var(--ad-gray-200);
  --ad-border-input:  var(--ad-gray-300);
  --ad-border-focus:  var(--ad-orange);
  --ad-border-error:  var(--ad-error);

  --ad-shadow-sm:     0 1px 2px rgba(0, 0, 0, 0.05);
  --ad-shadow-md:     0 1px 3px rgba(0, 0, 0, 0.08);
  --ad-shadow-lg:     0 4px 12px rgba(0, 0, 0, 0.1);

  /* ========== CHART COLORS (ordered series) ========== */
  --ad-chart-1:       #0B0B3B;   /* Navy — always first */
  --ad-chart-2:       #F07F00;   /* Orange — always second */
  --ad-chart-3:       #3B82F6;   /* Blue */
  --ad-chart-4:       #10B981;   /* Green */
  --ad-chart-5:       #8B5CF6;   /* Purple */
  --ad-chart-6:       #F59E0B;   /* Amber */
  --ad-chart-7:       #EF4444;   /* Red */
  --ad-chart-8:       #06B6D4;   /* Cyan */
}
```

---

## Usage Rules

### Navy (`--ad-navy`)
- Sidebar background
- Page headings and titles
- Primary buttons
- Table header text
- Chart primary series
- Footer background (if used)
- Never use as body text on white — use `--ad-gray-700` or `--ad-gray-900` instead for long paragraphs

### Orange (`--ad-orange`)
- Call-to-action buttons (one per screen ideally)
- Links and interactive text
- Focus rings on inputs
- Active/selected sidebar item background (at reduced opacity: `rgba(240, 127, 0, 0.15)`)
- Highlights, badges, notification dots
- Chart secondary series
- Never as large background fills in the application (OK for marketing hero sections)
- Never for error states — use `--ad-error` instead
- Orange-to-navy ratio on any screen should be roughly 1:4

### Status Colors
- Use the `*-bg` variant for badge/pill backgrounds
- Use the base color for text and icons
- Pair: e.g., `background: var(--ad-success-bg); color: var(--ad-success);`
- Status badges pattern: `padding: 2px 10px; border-radius: 9999px; font-size: 12px; font-weight: 500;`

### Sidebar Active Item
```css
.sidebar-item.active {
  background: rgba(240, 127, 0, 0.15);
  color: #FFFFFF;
  border-left: 3px solid var(--ad-orange);
}
.sidebar-item:hover {
  background: rgba(255, 255, 255, 0.08);
}
```

### Accessibility Notes
- Navy on white: contrast ratio ~15:1 (AAA) ✓
- Orange on white: contrast ratio ~3.5:1 (fails AA for small text) — use `--ad-orange-dark` (#CC6C00) for text on white backgrounds, which gives ~4.6:1 (AA compliant)
- White on navy: contrast ratio ~15:1 (AAA) ✓
- White on orange: contrast ratio ~3.5:1 — acceptable only for large text (18px+) or bold text (14px+ bold)
- For orange buttons, use white text at 16px bold minimum
