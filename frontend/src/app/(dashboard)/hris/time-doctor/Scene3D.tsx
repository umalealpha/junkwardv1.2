'use client'

/**
 * 3D workforce scene (R3F). A grid of "hour towers" — one per employee, height
 * ∝ tracked hours, colour by utilization. Idle (untracked) employees render as
 * flat dim tiles, so the adoption gap is visible at a glance. Slow auto-rotate,
 * orbit on drag. Client-only (dynamic import with ssr:false from the page).
 */
import { Canvas, useFrame } from '@react-three/fiber'
import { OrbitControls, Grid, Text, Float } from '@react-three/drei'
import { useMemo, useRef } from 'react'
import * as THREE from 'three'

export interface TowerDatum {
  label: string
  hours: number
  utilization: number | null
  tracking: boolean
}

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

function colorFor(d: TowerDatum): string {
  if (!d.tracking) return '#2A3A4F'                 // idle — dim slate
  const u = d.utilization ?? 0
  if (u >= 80) return '#10B981'                     // healthy
  if (u >= 40) return ORANGE                        // mid
  return '#EF4444'                                  // low utilization
}

function Towers({ data }: { data: TowerDatum[] }) {
  const group = useRef<THREE.Group>(null)
  const maxH = useMemo(() => Math.max(1, ...data.map(d => d.hours)), [data])
  const cols = Math.ceil(Math.sqrt(Math.max(1, data.length)))
  const spacing = 1.5

  useFrame((_, dt) => {
    if (group.current) group.current.rotation.y += dt * 0.12
  })

  return (
    <group ref={group}>
      {data.map((d, i) => {
        const x = ((i % cols) - cols / 2) * spacing
        const z = (Math.floor(i / cols) - cols / 2) * spacing
        const h = d.tracking ? 0.3 + (d.hours / maxH) * 6 : 0.12
        return (
          <mesh key={i} position={[x, h / 2, z]} castShadow>
            <boxGeometry args={[0.9, h, 0.9]} />
            <meshStandardMaterial
              color={colorFor(d)}
              emissive={colorFor(d)}
              emissiveIntensity={d.tracking ? 0.35 : 0.05}
              metalness={0.3}
              roughness={0.4}
            />
          </mesh>
        )
      })}
    </group>
  )
}

export default function Scene3D({ data }: { data: TowerDatum[] }) {
  const hasData = data.some(d => d.tracking)
  return (
    <Canvas shadows camera={{ position: [9, 8, 9], fov: 42 }} dpr={[1, 2]}
            style={{ width: '100%', height: '100%' }}>
      <color attach="background" args={[NAVY]} />
      <fog attach="fog" args={[NAVY, 14, 34]} />
      <ambientLight intensity={0.55} />
      <directionalLight position={[8, 14, 6]} intensity={1.1} castShadow />
      <pointLight position={[-8, 6, -6]} intensity={0.5} color={ORANGE} />
      <Towers data={data} />
      <Grid args={[40, 40]} cellColor="#1B2A3F" sectionColor="#27405E"
            position={[0, 0, 0]} infiniteGrid fadeDistance={32} />
      {!hasData && (
        <Float speed={2} floatIntensity={0.6} rotationIntensity={0.3}>
          <Text position={[0, 3, 0]} fontSize={0.7} color={ORANGE} anchorX="center" anchorY="middle">
            awaiting first pull
          </Text>
        </Float>
      )}
      <OrbitControls enablePan={false} minDistance={6} maxDistance={22}
                     maxPolarAngle={Math.PI / 2.1} />
    </Canvas>
  )
}
