import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import type { Kind, Rank } from '../api/types'

export function KindBadge({ kind, typeId }: { kind?: Kind; typeId?: string }) {
  if (!kind) return typeId ? <span className="chip">{typeId}</span> : null
  return <span className={`kind ${kind}`}>{typeId ?? kind}</span>
}

export function RankChip({ rank }: { rank: Rank }) {
  return <span className={`rank ${rank}`}>{rank}</span>
}

export function EntityLink({ id, label, typeId }: { id: string; label?: string | null; typeId?: string | null }) {
  return (
    <Link to={`/entity/${id}`} title={id}>
      {label ?? <code>{id.slice(0, 8)}</code>}
      {typeId ? <span className="muted small"> · {typeId}</span> : null}
    </Link>
  )
}

export function SimilarityBar({ value }: { value: number | null }) {
  if (value === null) return <span className="muted small mono">label</span>
  return (
    <span className="inline mono small" title={`Similarity ${value.toFixed(3)}`}>
      <span className="simbar"><i style={{ width: `${Math.round(value * 100)}%` }} /></span>
      {value.toFixed(2)}
    </span>
  )
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null
  if (error instanceof ApiError) {
    return (
      <div className="error-box" role="alert">
        <strong>{error.status === 422 ? 'Vom Gate abgelehnt' : `Fehler ${error.status}`}</strong>
        <ul>{error.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>
      </div>
    )
  }
  return <div className="error-box" role="alert">{String(error)}</div>
}

export function OkBox({ children }: { children: ReactNode }) {
  return <div className="ok-box">{children}</div>
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  )
}

export function PageHead({ eyebrow, title, sub }: { eyebrow: string; title: ReactNode; sub?: ReactNode }) {
  return (
    <header className="page-head">
      <div className="eyebrow">{eyebrow}</div>
      <h1>{title}</h1>
      {sub ? <div className="sub">{sub}</div> : null}
    </header>
  )
}

export function fmtDate(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString('de-DE', {
    dateStyle: 'medium', timeStyle: 'short',
  })
}
