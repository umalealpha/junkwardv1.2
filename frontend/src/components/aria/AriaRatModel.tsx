'use client'
/**
 * AriaRatModel — the CFO's original 3D animated rat mascot (restored 2026-06-13).
 *
 * Real GLB from Tripo3D (`public/aria-rat-3d.glb`), 5 embedded animations:
 *   0 idle · 1 cheer/success · 2 frustrated/error · 3 jump_down · 4 look_around/thinking
 *
 * This is the mascot that shipped 2026-05-22 (grey rat, pink ears, big eyes,
 * pink tail) and was later swapped for the ARIA particle face (fe38650). The CFO
 * asked for the rat BACK in Fun mode (2026-06-13), so AriaRatGLB now renders this
 * in theme-fun and the particle face in Classic/Heavenly. Render path is the
 * original, unchanged: @react-three/fiber + drei + three (all in deps).
 */
import { Suspense, useEffect, useRef, useState } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { useGLTF, useAnimations, OrbitControls } from '@react-three/drei'
import * as THREE from 'three'
import { ErrorBoundary } from '@/components/ErrorBoundary'

export type RatMood = 'idle' | 'thinking' | 'error' | 'success'

interface Props {
  blink?:    boolean
  dx?:       number
  dy?:       number
  excited?:  boolean
  compact?:  boolean
  mood?:     RatMood
}

const MOOD_TO_ANIM: Record<RatMood, number> = {
  idle:     0,
  success:  1,
  error:    2,
  thinking: 4,
}

function AriaModel({ mood, excited, onReady }: { mood: RatMood; excited: boolean; onReady?: () => void }) {
  const groupRef = useRef<THREE.Group>(null!)
  const fitRef   = useRef<THREE.Group>(null!)
  const { scene, animations } = useGLTF('/aria-rat-3d.glb?v=sky')
  const { actions, names } = useAnimations(animations, groupRef)
  const prevMoodRef = useRef<RatMood>(mood)

  // Deterministic auto-fit: centre at origin, scale tallest axis to fill view.
  useEffect(() => {
    if (!fitRef.current) return
    const box = new THREE.Box3().setFromObject(scene)
    const size = box.getSize(new THREE.Vector3())
    const center = box.getCenter(new THREE.Vector3())
    const maxDim = Math.max(size.x, size.y, size.z) || 1
    const targetH = 1.7
    const s = targetH / maxDim
    fitRef.current.scale.setScalar(s)
    fitRef.current.position.set(-center.x * s, -center.y * s, -center.z * s)
    fitRef.current.rotation.y = Math.PI + 1.20   // both legs face front (CFO 2026-05-27)
  }, [scene])

  useEffect(() => {
    const animIdx = MOOD_TO_ANIM[mood]
    const animName = names[animIdx] || names[0]
    const action = actions[animName]
    if (!action) return
    const prevIdx = MOOD_TO_ANIM[prevMoodRef.current]
    const prevName = names[prevIdx] || names[0]
    const prevAction = actions[prevName]
    if (prevAction && prevAction !== action) prevAction.fadeOut(0.4)
    // idle/thinking hold ONE frozen standing pose (no vertical bob — CFO 2026-06-04);
    // success/error play once.
    if (mood === 'success' || mood === 'error') {
      action.reset(); action.timeScale = 1; action.paused = false
      action.clampWhenFinished = true; action.setLoop(THREE.LoopOnce, 1)
      action.fadeIn(0.3).play()
    } else {
      Object.values(actions).forEach((a) => { if (a) a.stop() })
      action.reset(); action.play(); action.paused = true; action.time = 0
    }
    prevMoodRef.current = mood
    return () => { action.fadeOut(0.3) }
  }, [mood, actions, names])

  useFrame(() => {
    if (!groupRef.current) return
    groupRef.current.rotation.y = 0
    groupRef.current.position.y = 0   // fully static — no bob, no sway
  })

  useEffect(() => { onReady?.() }, [onReady])

  return (
    <group ref={groupRef}>
      <group ref={fitRef}>
        <primitive object={scene} />
      </group>
    </group>
  )
}

export function AriaRatModel({ excited = false, mood, compact = false }: Props) {
  const resolvedMood: RatMood = mood ?? (excited ? 'thinking' : 'idle')
  const [loaded, setLoaded] = useState(false)

  useEffect(() => { useGLTF.preload('/aria-rat-3d.glb?v=sky') }, [])

  const [glowPhase, setGlowPhase] = useState(0)
  useEffect(() => {
    let raf: number
    const animate = () => { setGlowPhase(Date.now() / 1000); raf = requestAnimationFrame(animate) }
    raf = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(raf)
  }, [])
  const glowPulse = excited ? 0.6 + Math.sin(glowPhase * 3) * 0.4 : 0.3 + Math.sin(glowPhase * 1.5) * 0.15

  return (
    <div
      style={{
        width: '100%', height: '100%',
        pointerEvents: 'none',   // decorative; the only hotspot is in AriaFloatingA (BUG-014)
        background: 'transparent', position: 'relative',
        filter: excited
          ? `drop-shadow(0 6px 10px rgba(13,27,42,0.20)) drop-shadow(0 0 12px rgba(244,166,35,${glowPulse * 0.5}))`
          : 'drop-shadow(0 6px 10px rgba(13,27,42,0.18))',
        transition: 'filter 0.4s ease',
      }}
      aria-label="ARIA"
      title="ARIA — your finance copilot"
    >
      <ErrorBoundary
        surface="aria-rat-3d"
        fallback={() => (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'transparent', pointerEvents: 'none' }}>
            <div style={{ width: 14, height: 14, borderRadius: '50%', background: '#F07F00', opacity: 0.4, animation: 'ariaPulse 1.1s ease-in-out infinite' }} />
            <style>{`@keyframes ariaPulse{0%,100%{transform:scale(0.7);opacity:.3}50%{transform:scale(1.1);opacity:.6}}`}</style>
          </div>
        )}
      >
        <Canvas
          camera={{ position: [0, 0, 2.2], fov: 45 }}
          style={{ width: '100%', height: '100%', pointerEvents: 'none' }}
          gl={{ antialias: true, alpha: true }}
          dpr={[1, 2]}
        >
          <ambientLight intensity={0.6} />
          <directionalLight position={[2, 3, 4]} intensity={1.2} />
          <directionalLight position={[-2, 1, -2]} intensity={0.4} color="#a0c4ff" />
          <pointLight position={[0, 2, 1]} intensity={0.5} color="#F4A623" />
          <Suspense fallback={null}>
            <AriaModel mood={resolvedMood} excited={excited} onReady={() => setLoaded(true)} />
          </Suspense>
          {!compact && (
            <OrbitControls enableZoom={false} enablePan={false}
              maxPolarAngle={Math.PI / 1.8} minPolarAngle={Math.PI / 2.5} autoRotate={false} />
          )}
        </Canvas>
      </ErrorBoundary>

      {!loaded && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'transparent', pointerEvents: 'none' }}>
          <div style={{ width: 14, height: 14, borderRadius: '50%', background: '#F07F00', opacity: 0.5, animation: 'ariaPulse 1.1s ease-in-out infinite' }} />
          <style>{`@keyframes ariaPulse{0%,100%{transform:scale(0.7);opacity:.35}50%{transform:scale(1.1);opacity:.7}}`}</style>
        </div>
      )}
    </div>
  )
}
