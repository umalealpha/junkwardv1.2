# Alpha Direct — Component Specifications

## Buttons

### Primary Button
```css
.btn-primary {
  background: var(--ad-navy);
  color: white;
  border: none;
  border-radius: 8px;
  height: 40px;
  padding: 0 16px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: background 150ms ease;
}
.btn-primary:hover { background: var(--ad-navy-light); }
.btn-primary:active { background: var(--ad-navy-dark); }
.btn-primary:disabled { background: var(--ad-gray-300); cursor: not-allowed; }
```

### Accent/CTA Button (Orange)
```css
.btn-accent {
  background: var(--ad-orange);
  color: white;
  border: none;
  border-radius: 8px;
  height: 40px;
  padding: 0 16px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.btn-accent:hover { background: var(--ad-orange-dark); }
```
Use only **once per screen** for the most important action (e.g., "Submit", "Create Invoice", "Approve").

### Secondary Button
```css
.btn-secondary {
  background: white;
  color: var(--ad-navy);
  border: 1px solid var(--ad-gray-300);
  border-radius: 8px;
  height: 40px;
  padding: 0 16px;
  font-size: 14px;
  font-weight: 500;
}
.btn-secondary:hover { background: var(--ad-gray-50); border-color: var(--ad-navy); }
```

### Ghost Button
```css
.btn-ghost {
  background: transparent;
  color: var(--ad-navy);
  border: none;
  height: 40px;
  padding: 0 12px;
  font-size: 14px;
  font-weight: 500;
}
.btn-ghost:hover { background: var(--ad-gray-100); border-radius: 6px; }
```

### Danger Button
```css
.btn-danger {
  background: var(--ad-error);
  color: white;
  border: none;
  border-radius: 8px;
  height: 40px;
  padding: 0 16px;
  font-size: 14px;
  font-weight: 500;
}
.btn-danger:hover { background: #B91C1C; }
```

### Button Sizes
| Size | Height | Padding | Font Size |
|------|--------|---------|-----------|
| Small | 32px | 0 12px | 13px |
| Medium (default) | 40px | 0 16px | 14px |
| Large | 48px | 0 24px | 16px |

### Icon Buttons
- Square: Same height as width (e.g., 40×40)
- Icon size: 20px
- Border-radius: 8px
- Always add `title` attribute for accessibility
- With text: 8px gap between icon and label

---

## Form Inputs

### Text Input
```css
.input {
  height: 40px;
  padding: 0 12px;
  font-size: 14px;
  color: var(--ad-gray-900);
  background: white;
  border: 1px solid var(--ad-gray-300);
  border-radius: 6px;
  width: 100%;
  transition: border-color 150ms ease, box-shadow 150ms ease;
}
.input:focus {
  outline: none;
  border-color: var(--ad-orange);
  box-shadow: 0 0 0 3px rgba(240, 127, 0, 0.1);
}
.input::placeholder { color: var(--ad-gray-400); }
.input:disabled {
  background: var(--ad-gray-100);
  color: var(--ad-gray-400);
  cursor: not-allowed;
}
.input.error {
  border-color: var(--ad-error);
  box-shadow: 0 0 0 3px rgba(220, 38, 38, 0.1);
}
```

### Form Field Layout
```html
<div class="form-field">
  <label class="form-label">
    Field Name <span class="required">*</span>
  </label>
  <input class="input" />
  <span class="form-hint">Helper text goes here</span>
  <span class="form-error">Error message</span>
</div>
```
```css
.form-label {
  display: block;
  font-size: 13px;
  font-weight: 500;
  color: var(--ad-gray-700);
  margin-bottom: 6px;
}
.required { color: var(--ad-orange); margin-left: 2px; }
.form-hint { font-size: 12px; color: var(--ad-gray-400); margin-top: 4px; }
.form-error { font-size: 12px; color: var(--ad-error); margin-top: 4px; }
```

### Select / Dropdown
Same styling as text input. Add a chevron-down icon on the right (color: gray-400).

### Textarea
Same border/focus styling. `min-height: 100px; padding: 10px 12px; resize: vertical;`

### Checkbox & Radio
- Size: 18×18px
- Border: `2px solid var(--ad-gray-300)`
- Checked fill: `var(--ad-orange)`
- Checked icon: White checkmark
- Border-radius: 4px (checkbox), 50% (radio)
- Label: 14px, 8px gap from control

### Toggle Switch
- Width: 44px, Height: 24px
- Off: `var(--ad-gray-300)` background
- On: `var(--ad-orange)` background
- Knob: white, 20px circle, 2px inset
- Transition: 0.2s ease

---

## Tables

Tables are the backbone of the ERP. They must be clear, scannable, and professional.

### Standard Data Table
```css
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}
.data-table thead th {
  background: var(--ad-gray-100);
  color: var(--ad-gray-700);
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 12px 16px;
  text-align: left;
  border-bottom: 2px solid var(--ad-gray-200);
  position: sticky;
  top: 0;
  z-index: 10;
}
.data-table tbody td {
  padding: 12px 16px;
  color: var(--ad-gray-900);
  border-bottom: 1px solid var(--ad-gray-200);
}
.data-table tbody tr:hover {
  background: var(--ad-orange-50);
}
.data-table tbody tr.selected {
  background: var(--ad-warning-light);
}
```

### Table Features
- **Sortable columns:** Show up/down chevron icon next to header text. Active sort: navy icon. Inactive: gray-300
- **Row actions:** Right-aligned column with icon buttons (edit, view, delete) — show on hover or always visible
- **Empty state:** Center-aligned message with illustration: "No records found"
- **Loading state:** Skeleton rows (gray-200 animated bars) or spinner
- **Pagination:** Below table, right-aligned. Show: "Showing 1-20 of 156 results" + page controls
- **Number columns:** Right-aligned, use tabular-nums font-feature
- **Currency columns:** Right-aligned, format `BWP 1,234.56`
- **Status columns:** Use pill badges (see Status Badges below)
- **Date columns:** Format `23 Mar 2026` or `23/03/2026`

---

## Cards

### Standard Card
```css
.card {
  background: white;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  overflow: hidden;
}
.card-header {
  padding: 16px 20px;
  border-bottom: 1px solid var(--ad-gray-200);
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--ad-navy);
}
.card-body { padding: 20px; }
.card-footer {
  padding: 12px 20px;
  border-top: 1px solid var(--ad-gray-200);
  background: var(--ad-gray-50);
}
```

### KPI Card (Dashboard)
```
┌──────────────────────┐
│ ○ Total Premiums     │  ← Icon (20px, gray-400) + Label (13px, gray-500)
│                      │
│ BWP 2,456,890.00     │  ← Value (24px, weight 700, navy)
│ ↑ 12.5% from last   │  ← Trend (13px, green=up / red=down) + period
└──────────────────────┘
```
- Min-width: 220px
- Padding: 20px
- Icon: Top-left, 20px, in a 36px circle with `var(--ad-gray-100)` background

---

## Status Badges / Pills

```css
.badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border-radius: 9999px;
  font-size: 12px;
  font-weight: 500;
  line-height: 20px;
}
.badge-success { background: var(--ad-success-bg); color: var(--ad-success); }
.badge-warning { background: var(--ad-warning-bg); color: var(--ad-warning); }
.badge-error   { background: var(--ad-error-bg);   color: var(--ad-error); }
.badge-info    { background: var(--ad-info-bg);     color: var(--ad-info); }
.badge-neutral { background: var(--ad-gray-100);    color: var(--ad-gray-500); }
```

### Insurance Status Mappings
| Status | Badge Type | Label |
|--------|-----------|-------|
| Active | success | Active |
| Pending Approval | warning | Pending |
| Expired | error | Expired |
| Draft | neutral | Draft |
| Cancelled | error | Cancelled |
| Under Review | info | Under Review |
| Paid | success | Paid |
| Overdue | error | Overdue |
| Partially Paid | warning | Partial |
| Lapsed | error | Lapsed |
| Renewed | success | Renewed |
| Claimed | info | Claimed |
| Settled | success | Settled |

---

## Modals / Dialogs

```css
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 200;
}
.modal {
  background: white;
  border-radius: 12px;
  max-width: 560px;
  width: 90%;
  max-height: 85vh;
  overflow-y: auto;
  z-index: 210;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.15);
}
.modal-header {
  padding: 20px 24px;
  border-bottom: 1px solid var(--ad-gray-200);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.modal-body { padding: 24px; }
.modal-footer {
  padding: 16px 24px;
  border-top: 1px solid var(--ad-gray-200);
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
```

### Confirmation Modal
Title + description + two buttons (Cancel secondary, Confirm primary or danger).
For destructive actions: Use danger button, require typing confirmation for bulk deletes.

---

## Navigation — Tabs

```css
.tabs {
  display: flex;
  border-bottom: 2px solid var(--ad-gray-200);
  gap: 0;
}
.tab {
  padding: 12px 20px;
  font-size: 14px;
  font-weight: 500;
  color: var(--ad-gray-500);
  border-bottom: 2px solid transparent;
  margin-bottom: -2px;
  cursor: pointer;
  transition: color 150ms ease;
}
.tab:hover { color: var(--ad-navy); }
.tab.active {
  color: var(--ad-navy);
  border-bottom-color: var(--ad-orange);
}
```

---

## Toast / Snackbar Notifications

- Position: Top-right, 24px from edges
- Width: 360px
- Border-radius: 8px
- Shadow: `var(--ad-shadow-lg)`
- Left border: 4px solid (status color)
- Background: white
- Auto-dismiss: 5 seconds (with progress bar)
- Stack: Up to 3 visible, newest on top

---

## Empty States

Center in content area:
- Illustration or icon: 64px, `var(--ad-gray-300)`
- Title: 16px, weight 600, `var(--ad-gray-700)`
- Description: 14px, `var(--ad-gray-500)`, max-width 400px
- Action button: Primary or secondary, below description

Example: "No invoices yet — Create your first invoice to get started" + [Create Invoice] button

---

## Loading States

- **Full page:** Centered spinner (navy, 32px) with "Loading..." text
- **Table:** Skeleton rows — gray-200 animated bars, 8 rows
- **Card:** Skeleton shimmer matching card layout
- **Button:** Replace text with 16px spinner, maintain button width
- Spinner color: `var(--ad-navy)` on light backgrounds, white on dark backgrounds

---

## Breadcrumbs in Context

Format: `Home / Finance / Invoices / INV-2026-0042`
- Clickable items: `var(--ad-gray-500)`, hover `var(--ad-navy)`
- Current page: `var(--ad-gray-900)`, weight 500, not clickable
- Separator: `>` or `/`, `var(--ad-gray-300)`

---

## Currency & Number Formatting (Botswana)

- Currency: `BWP` prefix, space before number: `BWP 1,234.56`
- Thousands separator: comma
- Decimal separator: period
- Negative amounts: `(BWP 1,234.56)` or `BWP -1,234.56` — use parentheses in financial reports
- Percentages: `12.50%`
- Dates: `23 Mar 2026` for display, `2026-03-23` for data attributes
- Time: `14:30` (24-hour format) for internal systems
