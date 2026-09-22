'use client'

/** The hand-off from Snap (/app/snap) to the flow it opens (raise-payment,
 * receipt, raise-po). The compressed photo + what Omni read travel in
 * sessionStorage — never in the URL — and are read ONCE by the target screen.
 * A stale hand-off (older than 10 minutes) is ignored. */

export const SNAP_HANDOFF_KEY = 'omni_snap_handoff'
const MAX_AGE_MS = 10 * 60_000

export type SnapKind = 'invoice' | 'receipt' | 'quote' | 'id_document' | 'other'
export type SnapRoute = 'raise-payment' | 'receipt' | 'raise-po'

export interface SnapHandoff {
  kind: SnapKind
  fields: Record<string, string>
  photo: string      // data URL of the compressed photo
  name: string       // file name to re-create the File with
  type: string       // MIME type
  at: number         // Date.now() when saved
}

export function saveSnapHandoff(h: SnapHandoff): boolean {
  try { sessionStorage.setItem(SNAP_HANDOFF_KEY, JSON.stringify(h)); return true } catch { return false }
}

/** Read AND clear the hand-off (one shot). Returns null when there is none,
 * it is stale, or it was left for a different kind of flow. */
export function takeSnapHandoff(kind?: SnapKind): SnapHandoff | null {
  try {
    const raw = sessionStorage.getItem(SNAP_HANDOFF_KEY)
    if (!raw) return null
    sessionStorage.removeItem(SNAP_HANDOFF_KEY)
    const h = JSON.parse(raw) as Partial<SnapHandoff>
    if (typeof h.photo !== 'string' || typeof h.at !== 'number') return null
    if (Date.now() - h.at > MAX_AGE_MS) return null
    if (kind && h.kind !== kind) return null
    return { kind: h.kind ?? 'other', fields: h.fields ?? {}, photo: h.photo, name: h.name || 'snap.jpg', type: h.type || 'image/jpeg', at: h.at }
  } catch { return null }
}

/** The photo back as a File, ready for the same FormData the screen would build. */
export async function handoffFile(h: SnapHandoff): Promise<File> {
  const blob = await (await fetch(h.photo)).blob()
  return new File([blob], h.name, { type: h.type || blob.type })
}

export function fileToDataUrl(f: File): Promise<string> {
  return new Promise((res, rej) => {
    const r = new FileReader(); r.onload = () => res(String(r.result)); r.onerror = () => rej(new Error('Could not read the photo.'))
    r.readAsDataURL(f)
  })
}
