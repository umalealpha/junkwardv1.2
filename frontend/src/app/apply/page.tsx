'use client'

/**
 * /apply — PUBLIC (no login) apply-to-join link for new ADH service providers.
 * A light "express interest" form; it creates a pending application the ADH team
 * reviews. The full AFA agreement / e-sign comes later, once accepted.
 */
import { useState } from 'react'
import { apiFetch } from '@/lib/api'

const NAVY = '#1D3270'
const ORANGE = '#F47C20'

const DISCIPLINES = [
  'GP', 'Pharmacy', 'Dental', 'Optometrist', 'Hospital', 'Laboratory',
  'Physiotherapy', 'Radiography', 'Specialist', 'Other',
]

export default function ProviderApplyPage() {
  const [f, setF] = useState({
    name: '', discipline: '', town: '', contact_number: '', email: '',
    practice_number: '', note: '',
  })
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const set = (k: string, v: string) => setF(s => ({ ...s, [k]: v }))

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    if (!f.name.trim()) { setError('Please give the practice or provider name.'); return }
    if (!f.email.trim() && !f.contact_number.trim()) {
      setError('Please give an email or a phone number so we can reach you.'); return
    }
    setBusy(true)
    try {
      await apiFetch('/health/provider-apply/', { method: 'POST', body: JSON.stringify(f) })
      setDone(true)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Something went wrong. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const field = (label: string, k: string, type = 'text', required = false) => (
    <label style={{ display: 'block', marginBottom: 14 }}>
      <span style={{ display: 'block', fontSize: 13, fontWeight: 600, color: NAVY, marginBottom: 5 }}>
        {label}{required && <span style={{ color: ORANGE }}> *</span>}
      </span>
      <input type={type} value={(f as Record<string, string>)[k]}
        onChange={e => set(k, e.target.value)}
        style={{ width: '100%', padding: '10px 12px', border: '1px solid #D6DAE2',
                 borderRadius: 8, fontSize: 15, boxSizing: 'border-box' }} />
    </label>
  )

  return (
    <div style={{ minHeight: '100vh', background: '#EEF1F5',
                  fontFamily: "Montserrat,-apple-system,Segoe UI,Roboto,Arial,sans-serif",
                  padding: '32px 16px' }}>
      <div style={{ maxWidth: 560, margin: '0 auto', background: '#fff',
                    borderRadius: 16, overflow: 'hidden',
                    boxShadow: '0 10px 40px rgba(15,23,42,0.10)' }}>
        <div style={{ background: NAVY, padding: '26px 28px' }}>
          <div style={{ color: '#fff', fontSize: 20, fontWeight: 700 }}>Alpha Direct Health</div>
          <div style={{ color: ORANGE, fontSize: 14, fontWeight: 600, marginTop: 3 }}>
            Join our service-provider network
          </div>
        </div>

        {done ? (
          <div style={{ padding: '40px 28px', textAlign: 'center' }}>
            <div style={{ fontSize: 44, marginBottom: 10 }}>✓</div>
            <h2 style={{ color: NAVY, fontSize: 20, margin: '0 0 8px' }}>Thank you</h2>
            <p style={{ color: '#475569', fontSize: 15, lineHeight: 1.5, margin: 0 }}>
              Your application has reached the Alpha Direct Health team. We will be in touch.
            </p>
          </div>
        ) : (
          <form onSubmit={submit} style={{ padding: '26px 28px' }}>
            <p style={{ color: '#475569', fontSize: 14, lineHeight: 1.5, margin: '0 0 20px' }}>
              Are you a doctor, pharmacy, dental, optical or other healthcare practice?
              Tell us a little about your practice and our team will reach out.
            </p>

            {field('Practice / provider name', 'name', 'text', true)}

            <label style={{ display: 'block', marginBottom: 14 }}>
              <span style={{ display: 'block', fontSize: 13, fontWeight: 600, color: NAVY, marginBottom: 5 }}>
                Discipline
              </span>
              <select value={f.discipline} onChange={e => set('discipline', e.target.value)}
                style={{ width: '100%', padding: '10px 12px', border: '1px solid #D6DAE2',
                         borderRadius: 8, fontSize: 15, boxSizing: 'border-box', background: '#fff' }}>
                <option value="">Select…</option>
                {DISCIPLINES.map(d => <option key={d} value={d}>{d}</option>)}
              </select>
            </label>

            {field('Town / city', 'town')}
            {field('Email', 'email', 'email')}
            {field('Phone number', 'contact_number', 'tel')}
            {field('AFA practice number (if you have one)', 'practice_number')}

            <label style={{ display: 'block', marginBottom: 18 }}>
              <span style={{ display: 'block', fontSize: 13, fontWeight: 600, color: NAVY, marginBottom: 5 }}>
                Anything else
              </span>
              <textarea value={f.note} onChange={e => set('note', e.target.value)} rows={3}
                style={{ width: '100%', padding: '10px 12px', border: '1px solid #D6DAE2',
                         borderRadius: 8, fontSize: 15, boxSizing: 'border-box', resize: 'vertical' }} />
            </label>

            {error && (
              <div style={{ background: '#FEF2F2', border: '1px solid #FECACA', color: '#B42318',
                            borderRadius: 8, padding: '10px 12px', fontSize: 14, marginBottom: 16 }}>
                {error}
              </div>
            )}

            <button type="submit" disabled={busy}
              style={{ width: '100%', padding: '13px', background: ORANGE, color: '#fff',
                       border: 'none', borderRadius: 8, fontSize: 16, fontWeight: 700,
                       cursor: busy ? 'default' : 'pointer', opacity: busy ? 0.7 : 1 }}>
              {busy ? 'Sending…' : 'Submit application'}
            </button>
            <p style={{ color: '#94A3B8', fontSize: 12, textAlign: 'center', margin: '14px 0 0' }}>
              Alpha Direct Insurance · Gaborone, Botswana
            </p>
          </form>
        )}
      </div>
    </div>
  )
}
