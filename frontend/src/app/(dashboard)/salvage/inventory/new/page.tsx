'use client'

/**
 * /salvage/inventory/new — add a salvage item (Phase 1 of CFO Salvage
 * Enhancements memo 2026-05-20).
 *
 * Backend SalvageItem already carries VIN, policy_number, brand,
 * model, year, colour, category, condition, asking/reserve pricing,
 * yard_section, shelf_row. This form now exposes all of them and
 * supports a Vehicle vs Part type toggle.
 *
 * Intake JE (unchanged):
 *   DR  1320 Salvage Inventory   (cost_basis)
 *   CR  105004 Salvages & Recoveries
 *
 * Photo upload (16-Sep-2026): DONE, and it lives on the item's own page, not
 * here — saving redirects to /salvage/inventory/<id> where the "Add photos"
 * panel is. This line used to read "Phase 2 (deferred): photo upload UI
 * wiring", which is precisely why prod held 52 salvage items and not one
 * photo until Bharath asked for it.
 *
 * Phase 2 (still deferred): write-off classification fields, public buyer
 * portal bidding, VIN search, yard performance reports.
 */
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ArrowLeft, Save, Info, AlertCircle, CheckCircle2, Loader2, Car, Wrench } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { apiFetch } from '@/lib/api'
import { localYmd } from '@/lib/utils'

const CONDITIONS = ['excellent', 'good', 'fair', 'poor', 'scrap'] as const
const STATUSES   = ['available', 'reserved', 'on_hold', 'written_off', 'scrapped'] as const
type SalvageType = 'vehicle' | 'part'

interface CreatedItem {
  id: string
  item_code: string
  intake_journal_entry: string | null
}

interface OptionRow { id: string; code?: string; name: string }
interface PaginatedRows<T> { results?: T[]; count?: number }

/**
 * Brand / model / category endpoints set `pagination_class = None`
 * (salvage/api_views.py), so they answer with a BARE ARRAY, not the
 * `{count, results}` envelope every paginated list uses. Reading
 * `.results` off an array yields undefined, so these three dropdowns
 * rendered nothing but the placeholder no matter how many rows the
 * table held — Bharath, 17-Sep-2026: "Brand and Model is currently not
 * working". Accept both shapes so it cannot break again if pagination
 * is ever switched back on.
 */
function rows<T>(d: PaginatedRows<T> | T[] | null | undefined): T[] {
  if (Array.isArray(d)) return d
  return d?.results ?? []
}

export default function NewSalvageItemPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const today = localYmd(new Date())

  // Salvage type drives which fields are required + emphasised.
  const [salvageType, setSalvageType] = useState<SalvageType>('vehicle')

  // Shared
  const [partName, setPartName]   = useState('')
  const [claimNumber, setClaim]   = useState('')
  const [policyNumber, setPolicy] = useState('')
  const [vinNumber, setVin]       = useState('')
  const [condition, setCondition] = useState<typeof CONDITIONS[number]>('fair')
  const [statusVal, setStatusVal] = useState<typeof STATUSES[number]>('available')
  const [askingPrice, setAsking]  = useState('')
  const [reservePrice, setReserve] = useState('')
  const [costBasis, setCostBasis] = useState('')
  const [yardSection, setYardSection] = useState('')
  const [shelfRow, setShelfRow]   = useState('')
  const [receivedDate, setReceived] = useState(today)
  const [notes, setNotes]         = useState('')

  // Vehicle-side fields
  const [brand, setBrand]         = useState('')
  const [model, setModel]         = useState('')
  const [year, setYear]           = useState('')
  const [colour, setColour]       = useState('')

  // Part-side
  const [category, setCategory]   = useState('')
  const [quantity, setQuantity]   = useState('1')

  // Reference data
  const [brands, setBrands]       = useState<OptionRow[]>([])
  const [models, setModels]       = useState<OptionRow[]>([])
  const [categories, setCategories] = useState<OptionRow[]>([])

  const [busy, setBusy]           = useState(false)
  const [err, setErr]             = useState<string | null>(null)
  const [ok, setOk]               = useState<CreatedItem | null>(null)

  // Load brands + categories once.
  useEffect(() => {
    apiFetch<PaginatedRows<OptionRow> | OptionRow[]>('/salvage-brands/?page_size=500')
      .then((d) => setBrands(rows(d)))
      .catch(() => { /* dropdown stays empty — user can type vin manually */ })
    apiFetch<PaginatedRows<OptionRow> | OptionRow[]>('/salvage-categories/?page_size=500')
      .then((d) => setCategories(rows(d)))
      .catch(() => {})
  }, [])

  // When brand changes, refresh models scoped to it.
  useEffect(() => {
    if (!brand) { setModels([]); return }
    apiFetch<PaginatedRows<OptionRow> | OptionRow[]>(`/salvage-models/?brand=${brand}&page_size=500`)
      .then((d) => setModels(rows(d)))
      .catch(() => setModels([]))
  }, [brand])

  function reserveBlur() {
    // Default cost_basis to reserve_price on first edit of reserve, so
    // the operator doesn't have to think about both numbers for routine
    // intake.
    if (!costBasis && reservePrice) setCostBasis(reservePrice)
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setErr(null); setOk(null)
    // item_code is no longer typed. Leave it out and the server draws the
    // next ML-#### under a lock, which is the only way two people booking in
    // at the same moment cannot land on the same code.
    if (!partName.trim()) {
      setErr('Part name is required.')
      return
    }
    if (salvageType === 'vehicle' && !vinNumber.trim()) {
      setErr('VIN number is required for vehicle salvage.')
      return
    }
    setBusy(true)
    try {
      const body: Record<string, unknown> = {
        part_name:     partName.trim(),
        claim_number:  claimNumber.trim(),
        policy_number: policyNumber.trim(),
        vin_number:    vinNumber.trim(),
        condition,
        status:        statusVal,
        asking_price:  askingPrice  || '0',
        reserve_price: reservePrice || '0',
        cost_basis:    costBasis    || reservePrice || '0',
        yard_section:  yardSection.trim(),
        shelf_row:     shelfRow.trim(),
        received_date: receivedDate || null,
        notes:         notes.trim(),
        // Kgosi 87a249f3: no company is sent — the server records every
        // salvage item under Veritas Capital (VCM) and refuses any other.
        // Vehicle-side
        vehicle_brand:   brand  || null,
        vehicle_model:   model  || null,
        vehicle_year:    year   ? parseInt(year, 10) : null,
        vehicle_colour:  colour.trim(),
        // Part-side
        category:        category || null,
        quantity:        quantity ? parseInt(quantity, 10) : 1,
      }
      const r = await apiFetch<CreatedItem>('/salvage-items/', {
        method: 'POST', body: JSON.stringify(body),
      })
      setOk(r)
      // Land on the item we just made, not back on the list. CFO decision
      // 16-Sep-2026 on Bharath's photo request: save the item first, then
      // attach photos on its own page. Photos need a real record to hang off,
      // so staging them on this form would mean temporary files and stray
      // uploads whenever a save failed. Going to the item page instead puts
      // the "Add photos" panel straight in front of whoever just booked the
      // salvage in, which is the moment they have the photos in their hand.
      setTimeout(() => router.push(`/salvage/inventory/${r.id}`), 1200)
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : 'Failed to create item.')
    } finally {
      setBusy(false)
    }
  }

  const isVehicle = salvageType === 'vehicle'
  const isPart    = salvageType === 'part'

  return (
    <div className="min-h-screen" style={{ background: theme.bg }}>
      <TopBar title="New Salvage Item" />
      <div className="p-4 lg:p-6 max-w-[960px] mx-auto space-y-5">
        <Link href="/salvage/inventory"
              className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ArrowLeft className="w-4 h-4" /> Back to inventory
        </Link>

        <div className="rounded-2xl p-4" style={{ background: theme.oL, border: `1px solid ${theme.orange}33` }}>
          <p className="text-xs leading-relaxed" style={{ color: theme.text }}>
            <Info className="w-3.5 h-3.5 inline mr-1" style={{ color: theme.orange }} />
            Adding an item posts a balanced JE at intake:
            <strong> Dr 1320 Salvage Inventory · Cr 105004 Salvages &amp; Recoveries</strong>{' '}
            at <strong>cost_basis</strong>. Sale later releases the inventory at carrying value
            and books any gain / loss to 400010.
          </p>
        </div>

        {/* Company (locked to Veritas) — Kgosi 87a249f3 */}
        <div className="rounded-2xl p-4" style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}` }}>
          <div className="flex items-center gap-3">
            <div className="flex-1">
              <p className="text-xs font-semibold uppercase tracking-wider mb-1" style={{ color: theme.t2 }}>
                Recording company
              </p>
              <p className="text-sm font-medium" style={{ color: theme.text }}>
                Veritas Capital
              </p>
              <p className="text-[11px] mt-1" style={{ color: theme.t3 }}>
                Salvage is recorded under Veritas Capital (VCM) only.
              </p>
            </div>
          </div>
        </div>

        {/* Salvage type toggle */}
        <div className="rounded-2xl p-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <div className="flex items-center gap-3">
            <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: theme.t2 }}>
              Salvage type
            </span>
            <button type="button" onClick={() => setSalvageType('vehicle')}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium"
                    style={{
                      background: isVehicle ? theme.orange : theme.g100,
                      color: isVehicle ? '#fff' : theme.text,
                      border: `1px solid ${isVehicle ? theme.orange : theme.cardBdr}`,
                    }}>
              <Car className="w-3.5 h-3.5" /> Full Vehicle Salvage
            </button>
            <button type="button" onClick={() => setSalvageType('part')}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium"
                    style={{
                      background: isPart ? theme.orange : theme.g100,
                      color: isPart ? '#fff' : theme.text,
                      border: `1px solid ${isPart ? theme.orange : theme.cardBdr}`,
                    }}>
              <Wrench className="w-3.5 h-3.5" /> Individual Part
            </button>
          </div>
        </div>

        <form onSubmit={submit} className="rounded-2xl p-5 space-y-5"
              style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          {/* Identifiers */}
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: theme.t2 }}>
              Identifiers
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Field label="Item code" theme={theme}>
                <input value="Generated when you save" readOnly disabled
                       aria-label="Item code — generated when you save"
                       {...inputProps(theme)} />
                <p className="mt-1 text-[11px]" style={{ color: theme.t3 }}>
                  The next number in the yard&apos;s own run. Nobody can type a
                  code twice by accident.
                </p>
              </Field>
              <Field label="Received date" theme={theme}>
                <input type="date" value={receivedDate} onChange={e => setReceived(e.target.value)}
                       {...inputProps(theme)} />
              </Field>
              <Field label="Claim number" theme={theme}>
                <input value={claimNumber} onChange={e => setClaim(e.target.value)}
                       placeholder="CLM-2026-…" {...inputProps(theme)} />
              </Field>
              <Field label="Policy number" theme={theme}>
                <input value={policyNumber} onChange={e => setPolicy(e.target.value)}
                       placeholder="POL-…" {...inputProps(theme)} />
              </Field>
              <Field label={`VIN number${isVehicle ? ' *' : ''}`}
                     hint={isVehicle
                        ? '17-char VIN — required for vehicle salvage.'
                        : 'VIN of the vehicle this part was removed from.'}
                     theme={theme}>
                <input value={vinNumber} onChange={e => setVin(e.target.value.toUpperCase())}
                       maxLength={17} placeholder="WDC1660042A123456"
                       required={isVehicle} {...inputProps(theme)} />
              </Field>
              <Field label="Description / part name *" theme={theme}>
                <input value={partName} onChange={e => setPartName(e.target.value)}
                       placeholder={isVehicle ? "2018 Toyota Hilux 2.4D" : "Front bumper assembly"}
                       required {...inputProps(theme)} />
              </Field>
            </div>
          </div>

          {/* Vehicle / part details */}
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: theme.t2 }}>
              {isVehicle ? 'Vehicle details' : 'Part details'}
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {isPart && (
                <>
                  <Field label="Category" theme={theme}>
                    <select value={category} onChange={e => setCategory(e.target.value)} {...inputProps(theme)}>
                      <option value="">— pick category —</option>
                      {categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </select>
                  </Field>
                  <Field label="Quantity" theme={theme}>
                    <input type="number" min="1" step="1" value={quantity}
                           onChange={e => setQuantity(e.target.value)} {...inputProps(theme)} />
                  </Field>
                </>
              )}
              <Field label="Brand" theme={theme}>
                <select value={brand} onChange={e => setBrand(e.target.value)} {...inputProps(theme)}>
                  <option value="">— pick brand —</option>
                  {brands.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
                </select>
              </Field>
              <Field label="Model" theme={theme}>
                <select value={model} onChange={e => setModel(e.target.value)}
                        disabled={!brand} {...inputProps(theme)}>
                  <option value="">{brand ? '— pick model —' : 'pick brand first'}</option>
                  {models.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
                </select>
              </Field>
              <Field label="Year" theme={theme}>
                <input type="number" min="1980" max="2030" value={year}
                       onChange={e => setYear(e.target.value)}
                       placeholder="2018" {...inputProps(theme)} />
              </Field>
              <Field label="Colour" theme={theme}>
                <input value={colour} onChange={e => setColour(e.target.value)}
                       placeholder="Silver" {...inputProps(theme)} />
              </Field>
            </div>
          </div>

          {/* Condition + pricing */}
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: theme.t2 }}>
              Condition &amp; pricing
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Field label="Condition" theme={theme}>
                <select value={condition} onChange={e => setCondition(e.target.value as typeof CONDITIONS[number])}
                        {...inputProps(theme)}>
                  {CONDITIONS.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </Field>
              <Field label="Status" theme={theme}>
                <select value={statusVal} onChange={e => setStatusVal(e.target.value as typeof STATUSES[number])}
                        {...inputProps(theme)}>
                  {STATUSES.map(s => <option key={s} value={s}>{s.replaceAll('_', ' ')}</option>)}
                </select>
              </Field>
              <Field label="Asking price (BWP)" theme={theme}>
                <input type="number" step="0.01" min="0" inputMode="decimal" value={askingPrice}
                       onChange={e => setAsking(e.target.value)}
                       placeholder="0.00" {...inputProps(theme)} />
              </Field>
              <Field label="Reserve price (BWP)" theme={theme}>
                <input type="number" step="0.01" min="0" inputMode="decimal" value={reservePrice}
                       onChange={e => setReserve(e.target.value)} onBlur={reserveBlur}
                       placeholder="0.00" {...inputProps(theme)} />
              </Field>
              <Field label="Cost basis (BWP) — hits BS"
                     hint="Defaults to reserve price. Carrying value released on sale."
                     theme={theme}>
                <input type="number" step="0.01" min="0" inputMode="decimal" value={costBasis}
                       onChange={e => setCostBasis(e.target.value)}
                       placeholder="0.00" {...inputProps(theme)} />
              </Field>
            </div>
          </div>

          {/* Location */}
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: theme.t2 }}>
              Yard location
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Field label="Yard section" theme={theme}>
                <input value={yardSection} onChange={e => setYardSection(e.target.value)}
                       placeholder="A" {...inputProps(theme)} />
              </Field>
              <Field label="Shelf / row" theme={theme}>
                <input value={shelfRow} onChange={e => setShelfRow(e.target.value)}
                       placeholder="R12" {...inputProps(theme)} />
              </Field>
            </div>
          </div>

          <Field label="Notes" theme={theme}>
            <textarea rows={2} value={notes} onChange={e => setNotes(e.target.value)}
                      placeholder="Optional — broker, sale window, defects, damage description"
                      className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-y"
                      style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
          </Field>

          {err && (
            <div className="rounded-lg px-3 py-2 text-sm flex items-start gap-2"
                 style={{ background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>
              <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
              <span>{err}</span>
            </div>
          )}
          {ok && (
            <div className="rounded-lg px-3 py-2 text-sm flex items-start gap-2"
                 style={{ background: theme.okB, color: theme.ok, border: `1px solid ${theme.ok}40` }}>
              <CheckCircle2 className="w-4 h-4 mt-0.5 flex-shrink-0" />
              <div>
                Saved {ok.item_code}.
                {ok.intake_journal_entry
                  ? <> Intake JE posted — <Link href={`/journal-entries/${ok.intake_journal_entry}`}
                                                style={{ color: theme.orange, textDecoration: 'underline' }}>
                       view in ledger</Link>.</>
                  : <> Intake JE skipped (cost_basis was 0).</>}
                {' '}<Link href={`/salvage/inventory/${ok.id}`}
                           style={{ color: theme.orange, textDecoration: 'underline' }}>
                  Open detail page to add photos.
                </Link>
              </div>
            </div>
          )}

          <div className="flex items-center justify-end gap-2 pt-2">
            <Link href="/salvage/inventory"
                  className="px-3 py-1.5 rounded-lg text-sm font-medium"
                  style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
              Cancel
            </Link>
            <button type="submit" disabled={busy}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-semibold disabled:opacity-50"
                    style={{ background: theme.orange, color: '#fff' }}>
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
              {busy ? 'Saving…' : 'Save & post intake'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function Field({ label, hint, theme, children }:
  { label: string; hint?: string; theme: { t2: string }; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>{label}</label>
      {children}
      {hint && <p className="text-[11px] mt-1" style={{ color: theme.t2 }}>{hint}</p>}
    </div>
  )
}

function inputProps(theme: { g100: string; cardBdr: string; text: string }) {
  return {
    className: 'w-full px-3 py-2 rounded-lg text-sm outline-none',
    style: { background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text },
  } as { className: string; style: React.CSSProperties }
}
