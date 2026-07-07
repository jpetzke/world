/** Kollabierbare Step-Karte für einen Tool-Call: Name, Parameter, Dauer,
    Ergebnis-Digest; welt_path/welt_traverse zusätzlich als Mini-Graph. */

import { useState } from 'react'
import { MiniGraph, parseGraphResult } from './MiniGraph'

export type StepInfo = {
  id: string
  name: string
  arguments: unknown
  status: 'running' | 'done' | 'error' | 'rejected' | 'pending'
  durationMs?: number
  offloaded?: boolean
  ref?: string | null
  error?: string
  display?: unknown
}

const GRAPH_TOOLS = new Set(['welt_path', 'welt_traverse'])

function short(value: unknown, max = 2000): string {
  const s = JSON.stringify(value, null, 2) ?? ''
  return s.length > max ? s.slice(0, max) + ' …' : s
}

export function StepCard({ step }: { step: StepInfo }) {
  const [open, setOpen] = useState(false)
  const graph = GRAPH_TOOLS.has(step.name) && step.status === 'done'
    ? parseGraphResult(step.display)
    : null

  const statusIcon = {
    running: <span className="spinner" aria-label="läuft" />,
    done: <span className="ai-step-ok">✓</span>,
    error: <span className="ai-step-err">✕</span>,
    rejected: <span className="ai-step-err">⊘</span>,
    pending: <span className="ai-step-wait">?</span>,
  }[step.status]

  return (
    <div className={`ai-step ai-step-${step.status}`}>
      <button type="button" className="ai-step-head" onClick={() => setOpen(!open)}>
        <span className="ai-step-status">{statusIcon}</span>
        <code className="ai-step-name">{step.name}</code>
        <span className="ai-step-meta">
          {step.durationMs !== undefined && `${step.durationMs} ms`}
          {step.offloaded && ' · offloaded'}
          {step.status === 'rejected' && ' · abgelehnt'}
        </span>
        <span className="ai-step-chevron">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="ai-step-body">
          <div className="ai-step-sect">Parameter</div>
          <pre>{short(step.arguments)}</pre>
          {step.error ? (
            <>
              <div className="ai-step-sect">Fehler</div>
              <pre className="ai-step-errtext">{step.error}</pre>
            </>
          ) : step.status === 'done' ? (
            <>
              <div className="ai-step-sect">
                Ergebnis{step.ref ? <code className="muted"> · {step.ref}</code> : null}
              </div>
              <pre>{short(step.display)}</pre>
            </>
          ) : null}
        </div>
      )}
      {graph && <MiniGraph data={graph} />}
    </div>
  )
}
