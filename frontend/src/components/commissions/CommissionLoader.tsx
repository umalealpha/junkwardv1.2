'use client'

/**
 * CommissionLoader — the "Load agent commission sheets" wizard on /commissions.
 *
 * Three steps, the standard spreadsheet-import UX (file → check → confirm):
 *   1. Pick group + month, drop the Excel file (drag-and-drop or click).
 *   2. We parse it server-side and show WHAT WE READ (names + amounts, total)
 *      before anything is saved — the "check before you commit" step.
 *   3. Confirm → the agents drop straight into the review queue.
 *
 * No column-mapping step: the commission sheets have fixed layouts the importer
 * already understands. Nothing is written until the user confirms.
 */
import { useRef, useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { UploadCloud, FileSpreadsheet, CheckCircle2, ArrowLeft, Loader2, AlertCircle } from 'lucide-react'
import {
  commissionUpload, commissionUploadPreview,
  type CommissionGroup, type CommissionPreview,
} from '@/lib/api'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const SERIF = '"Book Antiqua", "Palatino Linotype", Palatino, Georgia, serif'
const money = (v: string | number) =>
  'BWP ' + Number(v || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const cleanErr = (e: unknown) =>
  e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Something went wrong'

type Props = {
  groups: CommissionGroup[]
  period: string
  onPeriodChange: (p: string) => void
  onLoaded: (msg: string) => void   // parent refreshes the queue + shows the banner
}

export function CommissionLoader({ groups, period, onPeriodChange, onLoaded }: Props) {
  const [group, setGroup] = useState('in_house')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<CommissionPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const isInhouse = group === 'in_house'

  function reset() { setFile(null); setPreview(null); setErr(null) }

  async function onPick(f: File) {
    if (!/\.(xlsx|xlsm|xlsb|xls|ods)$/i.test(f.name)) { setErr('That’s not a spreadsheet — pick a .xlsx, .xlsm, .xlsb, .xls or .ods.'); return }
    if (!/^\d{4}-\d{2}$/.test(period)) { setErr('Pick the month first.'); return }
    setFile(f); setErr(null); setBusy(true); setPreview(null)
    try {
      const p = await commissionUploadPreview(f, period, { group, inhouse: isInhouse })
      setPreview(p)
      if (!p.count) setErr('We couldn’t find any commission rows in that sheet. Check the group, or open the sheet and confirm it’s the right one.')
    } catch (e) { setErr(cleanErr(e)) }
    finally { setBusy(false) }
  }

  async function confirmLoad() {
    if (!file) return
    setBusy(true); setErr(null)
    try {
      const r = await commissionUpload(file, period, { group, inhouse: isInhouse, submit: true })
      const n = r.agents != null ? (r.created ?? r.agents) : 1
      onLoaded(r.agents != null
        ? `Loaded ${n} ${groupName(groups, group)} agent(s) for ${period} — now in the review queue ✓`
        : `Loaded ${r.lines} line(s) for ${r.agent} — now in the review queue ✓`)
      reset()
    } catch (e) { setErr(cleanErr(e)) }
    finally { setBusy(false) }
  }

  return (
    <Card>
      <CardContent className="p-5 space-y-4" style={{ borderTop: `3px solid ${ORANGE}` }}>
        <div className="flex items-center gap-2">
          <UploadCloud className="h-5 w-5" style={{ color: ORANGE }} />
          <h2 className="font-semibold text-base" style={{ fontFamily: SERIF }}>Load agent commission sheets</h2>
        </div>
        <p className="text-xs text-muted-foreground">
          Load a whole month straight from the Excel sheets — no one retypes anything. Pick the
          group and month, drop the file, and check what we read before it goes to review.
        </p>

        {/* Group + month */}
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor="ld-group" className="block text-xs text-muted-foreground mb-1">Group</label>
            <select id="ld-group" value={group} disabled={busy}
                    onChange={e => { setGroup(e.target.value); reset() }}
                    className="px-3 py-1.5 text-sm border rounded-md bg-background">
              {groups.map(g => <option key={g.key} value={g.key}>{g.name}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="ld-month" className="block text-xs text-muted-foreground mb-1">Month</label>
            <input id="ld-month" type="month" value={period} disabled={busy}
                   onChange={e => { onPeriodChange(e.target.value); reset() }}
                   className="px-3 py-1.5 text-sm border rounded-md bg-background" />
          </div>
        </div>

        {/* Step 1 — dropzone (hidden until a preview is showing) */}
        {!preview && (
          <div
            role="button" tabIndex={0} aria-label="Drop an Excel file here or click to choose"
            onClick={() => !busy && inputRef.current?.click()}
            onKeyDown={e => { if ((e.key === 'Enter' || e.key === ' ') && !busy) inputRef.current?.click() }}
            onDragOver={e => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={e => { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files?.[0]; if (f) onPick(f) }}
            className="rounded-lg border-2 border-dashed p-8 text-center cursor-pointer transition-colors"
            style={{ borderColor: dragOver ? ORANGE : 'var(--border)', background: dragOver ? 'rgba(244,166,35,0.06)' : 'transparent' }}
          >
            <input ref={inputRef} type="file" accept=".xlsx,.xlsm,.xlsb,.xls,.ods" hidden
                   onChange={e => { const f = e.target.files?.[0]; if (f) onPick(f); e.target.value = '' }} />
            {busy ? (
              <div className="flex flex-col items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-6 w-6 animate-spin" style={{ color: ORANGE }} />
                Reading “{file?.name}”…
              </div>
            ) : (
              <div className="flex flex-col items-center gap-1.5">
                <UploadCloud className="h-8 w-8 text-muted-foreground" />
                <div className="text-sm font-medium">Drop the Excel sheet here, or click to choose</div>
                <div className="text-xs text-muted-foreground">
                  {isInhouse ? 'In-house: a summary sheet with everyone on it, OR one agent’s detailed sheet — we read whichever you drop and keep the per-policy detail.'
                             : 'One sheet per agent — the name is read from the file name.'}
                </div>
                <div className="text-[11px] text-muted-foreground mt-1">.xlsx · .xlsm · .xlsb · .xls · .ods</div>
              </div>
            )}
          </div>
        )}

        {err && (
          <div className="flex items-start gap-2 text-sm rounded-md p-3" style={{ background: 'rgba(220,38,38,0.06)', color: '#b91c1c' }}>
            <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />{err}
          </div>
        )}

        {/* Step 2 — check what we read */}
        {preview && preview.count > 0 && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md p-3" style={{ background: 'rgba(13,27,42,0.04)' }}>
              <FileSpreadsheet className="h-4 w-4" style={{ color: NAVY }} />
              <span className="text-sm font-medium">{file?.name}</span>
              <span className="text-sm">
                {preview.mode === 'inhouse'
                  ? <><b>{preview.count}</b> agent(s)</>
                  : <><b>{preview.count}</b> line(s) for <b>{preview.agent}</b></>}
              </span>
              <span className="text-sm">total <b style={{ color: ORANGE }}>{money(preview.total)}</b></span>
            </div>

            <div className="border rounded-md max-h-80 overflow-auto">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-muted">
                  {preview.mode === 'inhouse' ? (
                    <tr className="text-left text-muted-foreground">
                      <th className="px-2 py-1.5">Agent</th><th className="px-2 py-1.5 text-right">Gross commission</th>
                    </tr>
                  ) : (
                    <tr className="text-left text-muted-foreground">
                      <th className="px-2 py-1.5">Policy #</th><th className="px-2 py-1.5">Client</th>
                      <th className="px-2 py-1.5">Type</th><th className="px-2 py-1.5 text-right">Commission</th>
                    </tr>
                  )}
                </thead>
                <tbody>
                  {preview.rows.map((r, i) => preview.mode === 'inhouse' ? (
                    <tr key={i} className="border-t">
                      <td className="px-2 py-1">{r.name}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{money(r.gross || 0)}</td>
                    </tr>
                  ) : (
                    <tr key={i} className="border-t">
                      <td className="px-2 py-1">{r.policy_number}</td>
                      <td className="px-2 py-1">{r.client_name}</td>
                      <td className="px-2 py-1">{r.transaction_type?.replace('_', ' ')}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{money(r.commission_amount || 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {preview.truncated && <p className="text-[11px] text-muted-foreground">Showing the first 500 rows — all of them will be loaded.</p>}

            <div className="flex items-center gap-2">
              <Button size="sm" disabled={busy} onClick={confirmLoad} style={{ background: NAVY, color: '#fff' }}>
                {busy ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <CheckCircle2 className="h-4 w-4 mr-1" />}
                Looks right — load {preview.count} into review
              </Button>
              <Button size="sm" variant="outline" disabled={busy} onClick={reset}>
                <ArrowLeft className="h-4 w-4 mr-1" />Choose a different file
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function groupName(groups: CommissionGroup[], key: string) {
  return groups.find(g => g.key === key)?.name || key
}
