import type { Statement } from '../api/types'
import { EntityLink, RankChip, fmtDate } from './bits'

function renderValue(s: Statement) {
  switch (s.value_type) {
    case 'entity':
      return <EntityLink id={s.object_id!} label={s.object_label} typeId={s.object_type} />
    case 'quantity':
      return <span className="mono">{s.value_number} {s.value_unit}</span>
    case 'number':
      return <span className="mono">{s.value_number}</span>
    case 'datetime':
      return <span className="mono">{fmtDate(s.value_datetime)}</span>
    case 'geo':
      return s.value_geojson
        ? <span className="mono">{s.value_geojson.coordinates[1]}, {s.value_geojson.coordinates[0]}</span>
        : <span className="muted">geo</span>
    case 'json':
      return <code>{JSON.stringify(s.value_json)}</code>
    default:
      return <span>{s.value_text}</span>
  }
}

function renderQualifierValue(q: NonNullable<Statement['qualifiers']>[number]) {
  if (q.value_text !== null) return q.value_text
  if (q.value_number !== null) return String(q.value_number)
  if (q.value_datetime !== null) return fmtDate(q.value_datetime)
  if (q.object_id !== null) return q.object_id.slice(0, 8)
  return '—'
}

interface Props {
  statement: Statement
  onDeprecate?: (id: string) => void
  onSetRank?: (id: string, rank: string) => void
  /** Für incoming-Statements: Subjekt statt Objekt anzeigen. */
  subjectLabel?: { id: string; label: string | null; typeId?: string | null }
}

/** Eine Zeile im Behauptungs-Ledger. Links: Konfidenz-Kante (Signature). */
export function StatementCard({ statement: s, onDeprecate, onSetRank, subjectLabel }: Props) {
  const validity = s.valid_from || s.valid_to
    ? `gültig ${s.valid_from ? fmtDate(s.valid_from) : '…'} → ${s.valid_to ? fmtDate(s.valid_to) : 'offen'}`
    : null
  return (
    <div className={`stmt ${s.rank}`}>
      <span className="conf-edge" title={`Confidence ${s.confidence.toFixed(2)}`}>
        <i style={{ height: `${Math.round(s.confidence * 100)}%` }} />
      </span>
      <div className="stmt-head">
        {subjectLabel && (
          <span className="stmt-value">
            <EntityLink id={subjectLabel.id} label={subjectLabel.label} typeId={subjectLabel.typeId} />
            <span className="muted"> —{s.predicate_id}→</span>
          </span>
        )}
        {!subjectLabel && <span className="stmt-value">{renderValue(s)}</span>}
        <RankChip rank={s.rank} />
        {s.origin === 'inferred' && <span className="chip">inferred</span>}
        {(s.qualifiers ?? []).map((q) => (
          <span key={q.id} className="qualifier">
            {q.predicate_id}: {renderQualifierValue(q)}
          </span>
        ))}
        {(onDeprecate || onSetRank) && s.rank !== 'deprecated' && (
          <span className="inline" style={{ marginLeft: 'auto' }}>
            {onSetRank && s.rank !== 'preferred' && (
              <button type="button" className="ghost affirm" onClick={() => onSetRank(s.id, 'preferred')}>
                bevorzugen
              </button>
            )}
            {onDeprecate && (
              <button type="button" className="ghost danger" onClick={() => onDeprecate(s.id)}>
                deprecaten
              </button>
            )}
          </span>
        )}
      </div>
      <div className="stmt-meta">
        <span>conf {s.confidence.toFixed(2)}</span>
        {validity && <span>{validity}</span>}
        <span>erfasst {fmtDate(s.system_from)}</span>
        {s.system_to && <span>geschlossen {fmtDate(s.system_to)}</span>}
      </div>
      {(s.references ?? []).length > 0 && (
        <details className="refs">
          <summary>{s.references!.length} Quelle{s.references!.length > 1 ? 'n' : ''}</summary>
          <ul>
            {s.references!.map((r) => (
              <li key={r.id}>
                <span className="mono">{r.activity ?? '—'}</span>
                {r.agent && <span className="muted"> · {r.agent}</span>}
                {r.url && <> · <a href={r.url} target="_blank" rel="noreferrer">{r.url}</a></>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
