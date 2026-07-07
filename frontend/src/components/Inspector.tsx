import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Statement } from '../api/types'
import { useVocabulary } from '../hooks/useVocabulary'
import { EntityLink, KindBadge, fmtDate } from './bits'
import { Close } from './icons'

function valueOf(s: Statement) {
  switch (s.value_type) {
    case 'entity':
      return <EntityLink id={s.object_id!} label={s.object_label} />
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

function qualifierText(q: NonNullable<Statement['qualifiers']>[number]): string {
  const v = q.value_text
    ?? (q.value_number !== null ? String(q.value_number) : null)
    ?? (q.value_datetime !== null ? fmtDate(q.value_datetime) : null)
    ?? (q.object_id !== null ? q.object_id.slice(0, 8) : '—')
  return `${q.predicate_id}: ${v}`
}

/** Inspector (Spec Tab 04): Typ-Eyebrow, Titel, Badges, Aktions-Buttons,
    dann Statements als kv-Rows — Prädikat mono/gedimmt links, Wert als Link
    rechts, Qualifier als Sub-Chip. Desktop: Seitenpanel; ≤720px rendert das
    umgebende .graph-side als Bottom-Sheet. */
export function Inspector({ entityId, degree, onClose }: {
  entityId: string
  /** Voller Grad aus dem Graph (DB-Grad), falls bekannt. */
  degree?: number
  onClose: () => void
}) {
  const navigate = useNavigate()
  const { helpers } = useVocabulary()
  const view = useQuery({
    queryKey: ['entity', entityId, 'panel'],
    queryFn: () => api.entity(entityId),
  })

  if (!view.data) return null
  const { entity, statements } = view.data
  const kind = helpers?.kindOf(entity.type_id)

  return (
    <div className="inspector-body">
      <button type="button" className="ghost inspector-close" onClick={onClose}
        aria-label="Inspector schließen"><Close /></button>
      <div className="etype">{entity.type_id}</div>
      <h2 className="inspector-title">
        <EntityLink id={entity.id} label={entity.label ?? '(ohne Label)'} />
      </h2>
      <div className="inline" style={{ marginBottom: 12 }}>
        <KindBadge kind={kind} typeId={entity.type_id} />
        {degree !== undefined && <span className="chip">Grad {degree}</span>}
      </div>
      <div className="inspector-actions">
        <button type="button" onClick={() => navigate(`/graph/${entity.id}`)}>
          Traverse →
        </button>
        <button type="button" className="primary"
          onClick={() => navigate(`/create?statement_subject=${entity.id}`)}>
          + Statement
        </button>
      </div>
      <div className="kv">
        {statements.slice(0, 14).map((s) => (
          <div key={s.id} className="row">
            <span className="k">{s.predicate_id}</span>
            <span className="v">
              {valueOf(s)}
              {(s.qualifiers ?? []).map((q) => (
                <span key={q.id} className="qual">{qualifierText(q)}</span>
              ))}
            </span>
          </div>
        ))}
      </div>
      {statements.length > 14 && (
        <p className="muted small" style={{ marginTop: 8 }}>
          {statements.length - 14} weitere — <Link to={`/entity/${entity.id}`}>Entity-Seite →</Link>
        </p>
      )}
    </div>
  )
}
