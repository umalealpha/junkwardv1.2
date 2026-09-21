# Salvage Portal — operating manual

End-to-end runbook for the salvage parts pipeline on omni: inventory → public
offers → staff review → EXCO approval → sale → GL posting.

## Roles & access

| Role | Sidebar Salvage entry | Inventory | Quotes | Sales | Approvals |
|---|---|---|---|---|---|
| Superuser / CFO | ✅ | view + edit | review + decide | record sale | resolve |
| VCM / ADIC staff | ✅ | view + edit | review + decide | record sale | resolve |
| Other companies | ❌ | — | — | — | — |
| Public buyer (no login) | accesses `/buy-salvage` only | catalogue + offer submit | — | — | — |

Access enforcement: `salvage/permissions.py:IsSalvageUser` — VCM + ADIC + CFO + superuser.

## Data model (Postgres)

```
SalvageItem  ──< SalvageImage
   │
   ├──< BuyerQuote  ──< Sale ────────> ledger.JournalEntry
   │       │            │
   │       └─────< SalvageApproval >───┘
   │
   └─ FK to PartCategory, VehicleBrand, VehicleModel, Company
```

| Model | Purpose |
|---|---|
| `SalvageItem` | A single piece of stock — vehicle or part. Has yard_section / shelf_row / received_date / sold_date / disposed_date / received_by / asking_price / reserve_price. Status: available, quoted, reserved, sold, written_off, disposed, scrapped, on_hold. |
| `BuyerQuote` | A public offer. Status: pending → under_review → accepted / rejected / countered (with counter_price). Captures submitter IP + User-Agent for audit. |
| `Sale` | Terminal posting. Links to BuyerQuote (optional) and ledger.JournalEntry (auto-created). Payment methods: cash, eft, cheque, mobile_money, card. |
| `SalvageApproval` | EXCO sign-off queue. Kind: sale (below reserve / above threshold), quote_accept (over threshold), disposal. Status: pending → approved / rejected. |

## Workflow

### 1. Receive stock
1. AP / claims team creates a `SalvageItem` (manually or via the Motor Liquidators import).
2. Set `asking_price` (what we ask) and `reserve_price` (the floor below which a sale needs EXCO approval).
3. Photos go on `SalvageImage`; the first one with the lowest `ordering` is the public hero.

### 2. Public sees catalogue
- `/buy-salvage` lists every item where `status='available'`.
- Detail page strips internal IDs (`claim_number`, `reserve_price`).
- Buyer submits an offer via `POST /api/v1/salvage/public/quotes/`.
  - Rate-limited at **60 requests/minute per IP** (`AnonRateThrottle`).
  - Server captures IP (X-Forwarded-For aware) + User-Agent on the row.

### 3. Staff review queue
- Staff hit `/salvage/quotes` in omni.
- Three terminal actions: **Accept** / **Counter** (with `counter_price`) / **Reject**.
- On **Accept**:
  - `SalvageItem.status` flips to `quoted` so other buyers don't double-offer.
  - If `offered_price > SALVAGE_APPROVAL_THRESHOLD_BWP` (default P10,000): a
    `SalvageApproval(kind=quote_accept, status=pending)` row auto-creates.
  - The API response includes `approval_required: true` so the UI surfaces
    a toast.

### 4. EXCO approval
- Approvers hit `/salvage/approvals` in omni.
- See pending rows with `requested_amount` vs `threshold_amount`, plain-English `notes`.
- **Approve** / **Reject** — both require notes for audit.
- Resolution writes `approved_by` + `resolved_at`.

### 5. Record sale
- Staff hit `/salvage/sales` → "Record sale" modal.
- Picks an item (filtered to `status='quoted'`), buyer details, sale_price, payment method.
- On submit:
  - `SalvageItem.status` → `sold`, `sold_date` set.
  - **Auto JE posting** (see GL section).
  - If `sale_price < item.reserve_price` OR `sale_price > SALVAGE_APPROVAL_THRESHOLD_BWP`:
    a `SalvageApproval(kind=sale, status=pending)` row auto-creates.

### 6. GL posting
On every Sale create, `salvage.services.post_sale_to_gl` runs:

```
DR  <SALVAGE_CASH_ACCOUNT_CODE>      sale.sale_price
CR  <SALVAGE_INCOME_ACCOUNT_CODE>    sale.sale_price
```

- JournalEntry is created in DRAFT then immediately `.post(_allow_direct=True)`.
- `sale.journal_entry` FK is set on the Sale row for traceability.
- If the configured account codes aren't in the CoA, posting is **skipped silently**
  (logged at WARNING). The Sale row stays; CFO can repost manually.
- Failures don't roll back the Sale row.

## Configuration

All values in `/etc/alpha-finance/.env` on prod. Defaults shown.

```ini
SALVAGE_APPROVAL_THRESHOLD_BWP=10000     # any sale or quote over this → auto-approval
SALVAGE_CASH_ACCOUNT_CODE=110100         # DR side of the GL post
SALVAGE_INCOME_ACCOUNT_CODE=400010       # CR side (Sale of Salvage Vehicles)
```

To change: edit `.env`, then `sudo docker compose --env-file /etc/alpha-finance/.env -f /opt/alpha-finance/docker-compose.yml up -d backend`.

## API surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/salvage-items/` | Staff inventory list |
| GET | `/api/v1/salvage-items/{id}/` | Staff inventory detail |
| GET | `/api/v1/salvage/buyer-quotes/` | Quotes queue |
| POST | `/api/v1/salvage/buyer-quotes/{id}/review/` | Accept / counter / reject |
| GET | `/api/v1/salvage/sales/` | Sales register |
| POST | `/api/v1/salvage/sales/` | Record sale (triggers GL + threshold approval) |
| GET | `/api/v1/salvage/approvals/?status=pending` | EXCO queue |
| POST | `/api/v1/salvage/approvals/{id}/resolve/` | Approve / reject |
| GET | `/api/v1/salvage/public/items/` | Public catalogue (no auth, 60/min) |
| GET | `/api/v1/salvage/public/items/{item_code}/` | Public detail (no auth) |
| POST | `/api/v1/salvage/public/quotes/` | Public offer submit (no auth) |
| GET | `/api/v1/salvage/me-can-access/` | Sidebar visibility probe |

## Imports from Motor Liquidators (legacy Node + SQLite)

```bash
python manage.py import_motor_liquidators \
    --source .claude/specs/salvage-portal/source/salvage.db \
    --company VCM
```

- Idempotent on `item_code` for items, natural key on quotes/sales.
- Status mapping: motor-liq `in_stock/quoted/sold/written_off/disposed` →
  alpha-finance `available/quoted/sold/written_off/disposed`.
- Loads: part_categories, vehicle_brands, vehicle_models, salvage_items,
  buyer_quotes, sales.

## Prod state at last check (2026-05-18)

- 51 SalvageItems (5 available for public catalogue, others quoted/sold)
- 38 BuyerQuotes imported from motor-liquidators
- 42 part categories, 32 vehicle brands, 79 vehicle models

## Open / next iteration

- Beneficiary verification on Sale (vendor bank check before payout cheques).
- Frontend `/salvage/inventory` already exists — add a "Receive new stock" form.
- Email notification to buyer on quote decision (depends on docs/email-service which is separate).
- Public storefront filter UX: brand / category / price range dropdowns.
