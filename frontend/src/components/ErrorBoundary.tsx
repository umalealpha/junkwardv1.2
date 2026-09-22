'use client'

/**
 * Global React error boundary.
 *
 * CFO structural-audit directive 2026-05-19: "If any component fails to
 * render, the entire React tree crashes to a white screen." This boundary
 * catches the crash, shows a recoverable UI, and posts the error to the
 * /api/v1/client-errors/ endpoint (best-effort — failure is silent so we
 * don't recurse).
 *
 * Wrapped around the dashboard children in app/(dashboard)/layout.tsx and
 * the root <body> in app/layout.tsx.
 */

import React from 'react'

interface Props {
  children: React.ReactNode
  /** Optional label so we know which surface crashed. */
  surface?: string
  /** Optional override for the fallback UI. */
  fallback?: (e: Error, reset: () => void) => React.ReactNode
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Best-effort console telemetry only. Do NOT POST to a server endpoint:
    // /api/v1/client-errors/ doesn't exist yet, and using apiFetch here
    // would trigger the global 4xx toast → infinite loop on a real crash.
    try {
      // eslint-disable-next-line no-console
      console.error('[ErrorBoundary]', this.props.surface || 'unknown', error, info)
    } catch {
      /* never let logging crash */
    }
  }

  reset = () => this.setState({ error: null })

  render() {
    if (this.state.error) {
      if (this.props.fallback) {
        return this.props.fallback(this.state.error, this.reset)
      }
      return (
        <div role="alert" style={{
          padding: '32px 28px',
          margin: '24px auto',
          maxWidth: 720,
          background: '#FFFFFF',
          border: '1px solid #F5C2BE',
          borderLeft: '6px solid #8E1F12',
          borderRadius: 10,
          color: '#0D1B2A',
          boxShadow: '0 4px 12px rgba(13,27,42,0.08)',
        }}>
          <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>Something broke.</h2>
          <p style={{ marginTop: 8, color: '#6B7480', fontSize: 14 }}>
            We caught the error so the rest of omni stays usable.
            {this.props.surface && (
              <> Surface: <code>{this.props.surface}</code>.</>
            )}
          </p>
          <pre style={{
            marginTop: 12,
            padding: 12,
            background: '#F5F7FB',
            borderRadius: 6,
            fontSize: 12,
            color: '#2A3B4D',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            maxHeight: 220,
            overflow: 'auto',
          }}>{this.state.error.message}</pre>
          <div style={{ marginTop: 16, display: 'flex', gap: 10 }}>
            <button
              type="button"
              onClick={this.reset}
              style={{
                background: '#F4A623', color: '#0D1B2A',
                border: '1px solid #F4A623', borderRadius: 8,
                padding: '8px 14px', fontWeight: 600, cursor: 'pointer',
              }}
            >Try again</button>
            <button
              type="button"
              onClick={() => { window.location.href = '/dashboard' }}
              style={{
                background: '#FFFFFF', color: '#0D1B2A',
                border: '1px solid #D6DAE2', borderRadius: 8,
                padding: '8px 14px', cursor: 'pointer',
              }}
            >Back to dashboard</button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

export default ErrorBoundary
