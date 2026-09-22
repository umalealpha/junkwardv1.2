'use client'

/**
 * /settings/digital-assistant — Upload the GLB for the AriaRatGLB widget.
 *
 * Drop a CC0 / CC-BY rat .glb (e.g. from meshy.ai) here. File lands at
 * MEDIA_ROOT/aria/rat.glb on the backend volume and is served back at
 * /api/v1/admin/digital-assistant/asset/rat.glb. AriaFloatingA picks it
 * up automatically on next page load.
 */

import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { apiFetchBinary, getToken, API_BASE } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Upload, CheckCircle2, AlertTriangle, ExternalLink } from 'lucide-react'

export default function DigitalAssistantPage() {
  const router = useRouter()
  const fileRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [currentSize, setCurrentSize] = useState<number | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    fetch('/api/v1/admin/digital-assistant/asset/rat.glb', { method: 'HEAD' })
      .then(r => r.ok ? Number(r.headers.get('content-length') || '0') : null)
      .then(setCurrentSize)
      .catch(() => setCurrentSize(null))
  }, [reloadKey, router])

  async function onFile(file: File | null | undefined) {
    if (!file) return
    if (!file.name.toLowerCase().match(/\.(glb|gltf)$/)) {
      setErr('Pick a .glb or .gltf file.'); return
    }
    setBusy(true); setErr(null); setInfo(null)
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('slug', 'rat')
      const res = await apiFetchBinary(
        `${API_BASE}/admin/digital-assistant/upload/`,
        { method: 'POST', body: form, headers: {} },
      )
      if (!res.ok) {
        const txt = await res.text().catch(() => '')
        throw new Error(`HTTP ${res.status}: ${txt.slice(0, 200)}`)
      }
      const body = await res.json()
      setInfo(`Uploaded ${body.size.toLocaleString()} bytes. Refresh any open tab to see the new rat.`)
      setReloadKey(k => k + 1)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Upload failed')
    } finally { setBusy(false) }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Digital assistant"
        subtitle="Upload the 3D GLB for the floating rat (Aria)"
        breadcrumbs={[{ label: 'Settings', href: '/settings' }, { label: 'Digital assistant' }]}
      />
      <main className="flex-1 p-6 space-y-4 max-w-2xl">

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">How to source a rat</CardTitle>
          </CardHeader>
          <CardContent className="text-sm space-y-2">
            <p>
              Visit <a className="text-[#F4A623] hover:underline inline-flex items-center gap-1"
                href="https://www.meshy.ai/tags/rat" target="_blank" rel="noopener">
                meshy.ai/tags/rat <ExternalLink className="w-3 h-3" />
              </a> &middot; all assets there ship under <strong>CC0</strong> (no attribution).
            </p>
            <p>
              Pick a rat, hit the green <em>Download</em> button, choose <strong>GLB</strong> format.
              Drag the file into the drop zone below.
            </p>
            <p className="text-xs text-[#6B7280]">
              The file is stored on the server at <code>MEDIA_ROOT/aria/rat.glb</code> and
              served from <code>/api/v1/admin/digital-assistant/asset/rat.glb</code>.
              Cap 200&nbsp;MB.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              <Upload className="w-4 h-4" /> Drop the GLB here
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div
              onDragOver={e => e.preventDefault()}
              onDrop={e => { e.preventDefault(); onFile(e.dataTransfer.files?.[0]) }}
              className="border-2 border-dashed rounded-lg p-10 text-center hover:bg-muted/20 cursor-pointer"
              onClick={() => fileRef.current?.click()}
            >
              <Upload className="w-7 h-7 mx-auto mb-2 text-muted-foreground" />
              <p className="text-sm">{busy ? 'Uploading…' : 'Drop a .glb or click to pick.'}</p>
              <p className="text-xs text-muted-foreground mt-1">.glb / .gltf, max 200 MB</p>
              <input
                ref={fileRef} type="file" accept=".glb,.gltf,model/gltf-binary"
                className="hidden"
                onChange={e => onFile(e.target.files?.[0])}
                disabled={busy}
              />
            </div>

            {err && (
              <div className="mt-3 flex items-start gap-1 text-sm text-red-700">
                <AlertTriangle className="w-4 h-4 mt-0.5" /> {err}
              </div>
            )}
            {info && (
              <div className="mt-3 flex items-start gap-1 text-sm text-emerald-700">
                <CheckCircle2 className="w-4 h-4 mt-0.5" /> {info}
              </div>
            )}

            <div className="mt-4 text-xs text-[#6B7280]">
              {currentSize == null ? (
                <span>No GLB uploaded yet. The widget will show the placeholder silhouette.</span>
              ) : (
                <span>Current GLB on disk: <strong>{currentSize.toLocaleString()} bytes</strong>.</span>
              )}
            </div>

            {currentSize != null && (
              <div className="mt-4">
                <Button variant="secondary" size="sm" onClick={() => setReloadKey(k => k + 1)}>
                  Re-probe
                </Button>
              </div>
            )}
          </CardContent>
        </Card>
      </main>
    </div>
  )
}
