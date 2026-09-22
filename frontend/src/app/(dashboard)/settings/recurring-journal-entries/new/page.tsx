'use client'

import { Suspense } from 'react'
import RecurringJEForm from '../_form'

export default function NewRecurringJEPage() {
  return (
    <Suspense fallback={null}>
      <RecurringJEForm mode="create" />
    </Suspense>
  )
}
