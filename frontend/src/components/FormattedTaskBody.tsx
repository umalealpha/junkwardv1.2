'use client'

/**
 * FormattedTaskBody — pretty-print a pasted task/comment body in the house
 * colours (CFO 2026-07-15: pasted payment/claim blocks rendered as a raw
 * "dog vomit" wall of text).
 *
 * Deterministic, client-side, NO AI: the pasted text is already structured
 * (KEY: value headers, "N. description — BWP amount" line items, a TOTAL
 * line). We recognise those shapes and lay them out — no model call, nothing
 * leaves the browser, instant. Every line that matches no rule is rendered
 * verbatim, so content is NEVER dropped.
 */
import { useMemo } from 'react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

// "- Jane Doe: https://…/manager-feedback/<token>/?profile=…"  — a tappable
// person line. The name is the link; the raw URL (and its access token) is NEVER
// shown as text (CFO 2026-09-07: a manager saw a wall of raw links and got confused).
const PERSON_LINK = /^\s*[-•]\s+(.+?):\s+(https?:\/\/\S+)\s*$/
// "1. SPECIALISED 5%DISC [CLAIMS PAYABLE] — BWP 35,124.52"  (— or - or –)
const ITEM = /^\s*(\d+)\.\s+(.*?)\s+[—–-]\s*(BWP\s[\d,]+\.\d{2})\s*$/
// "TOTAL PAYABLE: BWP 190,295.55"  /  "TOTAL: BWP ..."
const TOTAL = /^\s*(TOTAL[A-Z ]*?):?\s*(BWP\s[\d,]+\.\d{2})\s*$/i
// "REF: ...", "TO: ...", "SUBJECT: ..." — an ALL-CAPS key then a value
const KV = /^\s*([A-Z][A-Z0-9 /_.&-]{1,28}):\s*(.+?)\s*$/
// A short ALL-CAPS line with no value = a section header
const isHeader = (l: string) =>
  /^[A-Z0-9][A-Z0-9 /&'.-]{2,60}$/.test(l.trim()) && !/\d{2,}/.test(l)

type Block =
  | { kind: 'header'; text: string }
  | { kind: 'kv'; k: string; v: string }
  | { kind: 'items'; rows: { n: string; desc: string; amt: string }[] }
  | { kind: 'total'; label: string; amt: string }
  | { kind: 'people'; rows: { name: string; url: string }[] }
  | { kind: 'text'; text: string }

function parse(body: string): Block[] {
  const lines = body.replace(/\r\n/g, '\n').split('\n')
  const blocks: Block[] = []
  let buf: string[] = []

  const flushText = () => {
    if (buf.length) { blocks.push({ kind: 'text', text: buf.join('\n').trim() }); buf = [] }
  }

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '')
    if (!line.trim()) { flushText(); continue }

    const person = line.match(PERSON_LINK)
    if (person) {
      flushText()
      const last = blocks[blocks.length - 1]
      const row = { name: person[1].trim(), url: person[2].trim() }
      if (last && last.kind === 'people') last.rows.push(row)
      else blocks.push({ kind: 'people', rows: [row] })
      continue
    }
    const item = line.match(ITEM)
    if (item) {
      flushText()
      const last = blocks[blocks.length - 1]
      const row = { n: item[1], desc: item[2].trim(), amt: item[3] }
      if (last && last.kind === 'items') last.rows.push(row)
      else blocks.push({ kind: 'items', rows: [row] })
      continue
    }
    const total = line.match(TOTAL)
    if (total) { flushText(); blocks.push({ kind: 'total', label: total[1].trim(), amt: total[2] }); continue }

    const kv = line.match(KV)
    if (kv) { flushText(); blocks.push({ kind: 'kv', k: kv[1].trim(), v: kv[2].trim() }); continue }

    if (isHeader(line)) { flushText(); blocks.push({ kind: 'header', text: line.trim() }); continue }

    buf.push(line)
  }
  flushText()
  return blocks
}

export function FormattedTaskBody({ text }: { text: string }) {
  const blocks = useMemo(() => parse(text), [text])

  return (
    <div className="text-sm bg-[#F9FAFB] dark:bg-[#1E293B] rounded-lg p-4 mb-4 space-y-2">
      {blocks.map((b, i) => {
        if (b.kind === 'header') {
          return (
            <div key={i} className="font-bold text-[13px] tracking-wide pt-1"
                 style={{ color: NAVY, borderBottom: `2px solid ${ORANGE}`, paddingBottom: 4 }}>
              <span className="dark:text-white">{b.text}</span>
            </div>
          )
        }
        if (b.kind === 'kv') {
          return (
            <div key={i} className="flex gap-2 text-[13px]">
              <span className="text-[#6B7280] shrink-0">{b.k}</span>
              <span className="font-semibold text-[#0D1B2A] dark:text-white">{b.v}</span>
            </div>
          )
        }
        if (b.kind === 'items') {
          return (
            <table key={i} className="w-full text-[13px] my-1" style={{ borderCollapse: 'collapse' }}>
              <tbody>
                {b.rows.map((r, j) => (
                  <tr key={j} className={j % 2 ? 'bg-black/[0.02] dark:bg-white/[0.03]' : ''}>
                    <td className="py-1.5 pr-2 align-top text-[#9CA3AF] tabular-nums" style={{ width: 24 }}>{r.n}</td>
                    <td className="py-1.5 pr-3 align-top text-[#0D1B2A] dark:text-[#E5E7EB]">{r.desc}</td>
                    <td className="py-1.5 align-top text-right font-semibold tabular-nums whitespace-nowrap text-[#0D1B2A] dark:text-white">{r.amt}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
        if (b.kind === 'total') {
          return (
            <div key={i} className="flex items-center justify-between rounded-md px-3 py-2 mt-1"
                 style={{ background: NAVY }}>
              <span className="font-bold text-white text-[13px]">{b.label}</span>
              <span className="font-bold tabular-nums whitespace-nowrap" style={{ color: ORANGE }}>{b.amt}</span>
            </div>
          )
        }
        if (b.kind === 'people') {
          return (
            <div key={i} className="flex flex-col gap-1.5 my-1">
              {b.rows.map((r, j) => (
                <a key={j} href={r.url} target="_blank" rel="noopener noreferrer"
                   className="inline-flex items-center gap-2 rounded-md px-3 py-2 text-[13px] font-semibold no-underline transition-colors hover:brightness-95"
                   style={{ background: '#FFF7EC', color: NAVY, border: `1px solid ${ORANGE}` }}>
                  <span aria-hidden style={{ color: ORANGE }}>✎</span>
                  <span className="dark:text-white">{r.name}</span>
                </a>
              ))}
            </div>
          )
        }
        return (
          <div key={i} className="whitespace-pre-wrap text-[#374151] dark:text-[#CBD5E1]">{linkify(b.text)}</div>
        )
      })}
    </div>
  )
}

// Turn bare URLs in free text into tappable links. A long link (a token URL)
// shows a friendly "open ↗" instead of its raw characters, so the access token
// is never rendered as visible text; a short, human-readable URL shows as-is.
const URL_RE = /(https?:\/\/[^\s]+)/g
function linkify(text: string) {
  const parts = text.split(URL_RE)
  return parts.map((part, i) => {
    if (i % 2 === 1) {
      const label = part.length > 48 ? 'open ↗' : part
      return (
        <a key={i} href={part} target="_blank" rel="noopener noreferrer"
           className="font-medium underline" style={{ color: NAVY }}>{label}</a>
      )
    }
    return <span key={i}>{part}</span>
  })
}

export default FormattedTaskBody
