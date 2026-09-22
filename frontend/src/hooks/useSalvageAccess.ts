'use client'

import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'

interface ProbeResponse { can_access: boolean; reason?: string }

let _cache: boolean | null = null

export function useSalvageAccess(): boolean | null {
  const [allowed, setAllowed] = useState<boolean | null>(_cache)

  useEffect(() => {
    if (_cache !== null) return
    apiFetch<ProbeResponse>('/salvage/me-can-access/')
      .then(r => { _cache = !!r.can_access; setAllowed(_cache) })
      .catch(() => { _cache = false; setAllowed(false) })
  }, [])

  return allowed
}
