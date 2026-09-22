'use client'

import { Suspense } from 'react'
import RecurringJEForm from '../_form'

export default function EditRecurringJEPage() {
  return (
    <Suspense fallback={null}>
      <RecurringJEForm mode="edit" />
    </Suspense>
  )
}
