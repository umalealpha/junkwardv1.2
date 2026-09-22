'use client'

/**
 * FunModeGalaxyScene — the actual Three.js star-field for HRIS "Fun Mode".
 *
 * Split out of FunModeGalaxy.tsx (CFO 2026-07-16, "make omni faster") so the
 * heavy three.js library is NOT bundled into the HRIS page's initial load.
 * FunModeGalaxy.tsx now lazy-loads this file only when the easter egg fires,
 * so three.js downloads on the Konami trigger and never before.
 *
 * Behaviour is unchanged — this is the same component, just in its own module.
 * CFO directive 2026-05-20 (Manus HRIS Part 4 — wow-factor extensions).
 */
import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'

export function FunModeGalaxy({ open, onClose, initials = [] }:
                              { open: boolean; onClose: () => void; initials?: string[] }) {
  const mountRef = useRef<HTMLDivElement | null>(null)
  const rafRef = useRef<number | null>(null)
  const [mounted, setMounted] = useState(false)

  useEffect(() => { setMounted(true) }, [])

  useEffect(() => {
    if (!open || !mountRef.current) return
    const mount = mountRef.current
    const w = mount.clientWidth
    const h = mount.clientHeight

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x05080f)

    const camera = new THREE.PerspectiveCamera(70, w / h, 0.1, 1000)
    camera.position.z = 30

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(w, h)
    mount.appendChild(renderer.domElement)

    // Star-field — 4 000 points distributed in a sphere
    const STAR_COUNT = 4000
    const starGeo = new THREE.BufferGeometry()
    const positions = new Float32Array(STAR_COUNT * 3)
    const colors = new Float32Array(STAR_COUNT * 3)
    for (let i = 0; i < STAR_COUNT; i++) {
      const r = 50 + Math.random() * 250
      const theta = Math.random() * 2 * Math.PI
      const phi = Math.acos(2 * Math.random() - 1)
      positions[i * 3]     = r * Math.sin(phi) * Math.cos(theta)
      positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta)
      positions[i * 3 + 2] = r * Math.cos(phi)
      // Alpha Direct orange-tinted stars sprinkled in a white field
      if (Math.random() < 0.08) {
        colors[i * 3]     = 1.0   // R
        colors[i * 3 + 1] = 0.50
        colors[i * 3 + 2] = 0.07
      } else {
        const v = 0.6 + Math.random() * 0.4
        colors[i * 3]     = v
        colors[i * 3 + 1] = v
        colors[i * 3 + 2] = v
      }
    }
    starGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    starGeo.setAttribute('color',    new THREE.BufferAttribute(colors, 3))
    const stars = new THREE.Points(
      starGeo,
      new THREE.PointsMaterial({ size: 0.7, vertexColors: true, transparent: true, opacity: 0.95 }),
    )
    scene.add(stars)

    // Floating Alpha Direct orange "core"
    const core = new THREE.Mesh(
      new THREE.IcosahedronGeometry(3, 1),
      new THREE.MeshBasicMaterial({ color: 0xF07F00, wireframe: true }),
    )
    scene.add(core)

    // Floating employee initials as sprites
    const sprites: THREE.Sprite[] = []
    const sampleInitials = initials.length ? initials : ['AD', 'PR', 'KG', 'PK', 'UN', 'AR']
    for (let i = 0; i < Math.min(60, sampleInitials.length * 10); i++) {
      const text = sampleInitials[i % sampleInitials.length]
      const canvas = document.createElement('canvas')
      canvas.width = 128; canvas.height = 128
      const ctx = canvas.getContext('2d')!
      ctx.fillStyle = '#0D1B2A'
      ctx.beginPath(); ctx.arc(64, 64, 60, 0, 2 * Math.PI); ctx.fill()
      ctx.strokeStyle = '#F07F00'; ctx.lineWidth = 4
      ctx.beginPath(); ctx.arc(64, 64, 60, 0, 2 * Math.PI); ctx.stroke()
      ctx.fillStyle = '#fff'
      ctx.font = 'bold 48px sans-serif'
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
      ctx.fillText(text, 64, 68)
      const tex = new THREE.CanvasTexture(canvas)
      const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true }))
      const r = 15 + Math.random() * 30
      const a = Math.random() * Math.PI * 2
      const z = (Math.random() - 0.5) * 30
      sprite.position.set(r * Math.cos(a), r * Math.sin(a), z)
      sprite.scale.setScalar(2.5)
      scene.add(sprite)
      sprites.push(sprite)
    }

    let t = 0
    function tick() {
      t += 0.005
      stars.rotation.y = t * 0.3
      stars.rotation.x = t * 0.1
      core.rotation.x = t
      core.rotation.y = t * 1.4
      sprites.forEach((s, i) => {
        const speed = 0.3 + (i % 5) * 0.05
        s.position.x = Math.cos(t * speed + i) * (15 + (i % 7) * 2)
        s.position.y = Math.sin(t * speed + i) * (15 + (i % 7) * 2)
      })
      renderer.render(scene, camera)
      rafRef.current = requestAnimationFrame(tick)
    }
    tick()

    function onResize() {
      const nw = mount.clientWidth
      const nh = mount.clientHeight
      camera.aspect = nw / nh
      camera.updateProjectionMatrix()
      renderer.setSize(nw, nh)
    }
    window.addEventListener('resize', onResize)

    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)

    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
      window.removeEventListener('resize', onResize)
      window.removeEventListener('keydown', onKey)
      renderer.dispose()
      starGeo.dispose()
      sprites.forEach(s => {
        s.material.map?.dispose()
        s.material.dispose()
      })
      core.geometry.dispose()
      ;(core.material as THREE.Material).dispose()
      if (renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement)
      }
    }
  }, [open, onClose, initials])

  if (!mounted || !open) return null

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 9999,
        background: 'rgba(5, 8, 15, 0.95)',
        cursor: 'pointer',
      }}
    >
      <div ref={mountRef} style={{ width: '100%', height: '100%' }} />
      <div style={{
        position: 'absolute', bottom: 24, left: 0, right: 0,
        textAlign: 'center', color: '#fff', fontFamily: 'sans-serif',
        pointerEvents: 'none',
      }}>
        <div style={{ fontSize: 11, opacity: 0.6, letterSpacing: 4, textTransform: 'uppercase' }}>
          Alpha Direct · Fun Mode
        </div>
        <div style={{ fontSize: 24, fontStyle: 'italic', marginTop: 4, color: '#F07F00' }}>
          One Alpha. Many stars.
        </div>
        <div style={{ fontSize: 11, opacity: 0.5, marginTop: 8 }}>
          Click anywhere or press <kbd style={{ padding: '1px 6px', border: '1px solid #fff5', borderRadius: 3 }}>Esc</kbd> to exit
        </div>
      </div>
    </div>
  )
}
