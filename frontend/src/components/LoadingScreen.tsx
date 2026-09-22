'use client'

import { useState, useEffect } from 'react'
import Image from 'next/image'
import { BRAND } from '@/lib/brand'
import { getQuote } from '@/lib/quotes'
import { useTheme } from '@/contexts/ThemeContext'

const CYCLE_MASCOTS = [
  BRAND.mascots.boyStand,
  BRAND.mascots.girlStand,
  BRAND.mascots.boyDab,
]

interface LoadingScreenProps {
  page: string
}

export function LoadingScreen({ page }: LoadingScreenProps) {
  const { theme, reduceMotion } = useTheme()
  // Random quote is picked client-side only — picking it during SSR (or in
  // useState's lazy initialiser) causes a hydration mismatch because the
  // server and client both call Math.random and get different strings.
  const [quote, setQuote] = useState<string>('Loading...')
  const [mascotIndex, setMascotIndex] = useState(0)

  useEffect(() => {
    setQuote(getQuote(page))
  }, [page])

  useEffect(() => {
    if (reduceMotion) return
    const id = setInterval(() => {
      setMascotIndex(prev => (prev + 1) % CYCLE_MASCOTS.length)
    }, 800)
    return () => clearInterval(id)
  }, [reduceMotion])

  return (
    <div
      className="flex flex-col items-center justify-center"
      style={{ padding: 60, gap: 16 }}
    >
      <Image
        src={CYCLE_MASCOTS[mascotIndex]}
        alt="Loading..."
        width={120}
        height={120}
        className="object-contain"
        style={{ height: 'auto' }}
        priority
      />

      <p
        className="text-[15px] font-bold text-center max-w-xs"
        style={{ color: theme.orange }}
      >
        {quote}
      </p>

      {/* Progress bar */}
      <div
        className="rounded-full overflow-hidden"
        style={{
          width: 180,
          height: 4,
          background: theme.g200,
        }}
      >
        <div
          className="h-full rounded-full"
          style={{
            width: '70%',
            background: theme.orange,
          }}
        />
      </div>
    </div>
  )
}
