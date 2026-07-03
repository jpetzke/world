import { Link } from 'react-router-dom'
import type { TimelineItem } from '../api/types'

const fmt = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' }) : null

/** Zeitleiste: ◆ Ereignis-Entities (klickbar) + ○ abgeleitete Meilensteine
 * (datetime-Statements, Label-Wechsel) — chronologisch, ohne Datum am Ende. */
export function EntityTimeline({ items }: { items: TimelineItem[] }) {
  return (
    <ol className="timeline">
      {items.map((item, i) => (
        <li key={i} className={`timeline-item ${item.kind}`}>
          <span className="timeline-marker" aria-hidden />
          <span className="timeline-date">{fmt(item.at) ?? 'ohne Datum'}</span>
          {item.kind === 'ereignis' ? (
            <span className="timeline-body">
              <Link to={`/entity/${item.entity_id}`}>{item.label ?? item.entity_id.slice(0, 8)}</Link>
              <span className="chip">{item.type_id}</span>
              {item.via.map((p) => <span key={p} className="chip">{p}</span>)}
              {item.ende && <span className="muted small">bis {fmt(item.ende)}</span>}
            </span>
          ) : (
            <span className="timeline-body">
              {item.predicate_label}
              {item.detail && <span className="timeline-detail">{item.detail}</span>}
            </span>
          )}
        </li>
      ))}
    </ol>
  )
}
