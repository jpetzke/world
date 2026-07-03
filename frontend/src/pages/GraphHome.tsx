import { useQuery } from '@tanstack/react-query'
import { useCallback, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Kind } from '../api/types'
import { EntityLink, ErrorBox, KindBadge } from '../components/bits'
import { useOptionNav } from '../components/useOptionNav'
import { GraphCanvas, type GraphCanvasHandle } from '../graph/GraphCanvas'
import { NODE_COLORS } from '../graph/style'
import { useVocabulary } from '../hooks/useVocabulary'

/** Home: das ganze Weltmodell als Graph. Suche springt zum Knoten und hebt
    seine Nachbarschaft hervor; Tippen leuchtet Treffer live; Filter dimmen
    statt zu verstecken — die Welt bleibt sichtbar. */
export function GraphHome() {
  const navigate = useNavigate()
  const { helpers } = useVocabulary()
  const canvasRef = useRef<GraphCanvasHandle>(null)

  const [selected, setSelected] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [kindFilter, setKindFilter] = useState<Record<Kind, boolean>>({
    continuant: true,
    occurrent: true,
  })
  const [predicateFilter, setPredicateFilter] = useState<string>('')

  const graph = useQuery({ queryKey: ['graph'], queryFn: () => api.graph(600) })
  const stats = useQuery({ queryKey: ['stats'], queryFn: api.stats })
  const selectedView = useQuery({
    queryKey: ['entity', selected, 'panel'],
    queryFn: () => api.entity(selected!),
    enabled: !!selected,
  })

  const predicatesInGraph = useMemo(
    () => [...new Set((graph.data?.edges ?? []).map((e) => e.predicate_id))].sort(),
    [graph.data],
  )
  const hiddenKinds = useMemo(
    () => (Object.entries(kindFilter) as [Kind, boolean][])
      .filter(([, on]) => !on).map(([k]) => k),
    [kindFilter],
  )
  const kindOf = useCallback((t: string) => helpers?.kindOf(t), [helpers])

  // Suche im Server (findet auch Knoten außerhalb des Ausschnitts).
  const search = useQuery({
    queryKey: ['search', query],
    queryFn: () => api.search(query),
    enabled: query.trim().length >= 2,
  })
  const results = query.trim().length >= 2 ? (search.data ?? []).slice(0, 8) : []

  const spotlight = (id: string) => {
    // Erst im Ausschnitt anspringen; sonst zur Entity-Seite (außerhalb Snapshot).
    if (!canvasRef.current?.focusOn(id)) navigate(`/entity/${id}`)
  }

  const { active, listRef, onKeyDown } = useOptionNav(
    results,
    (hit) => { spotlight(hit.id); setQuery('') },
    () => setQuery(''),
  )

  if (graph.error) return <ErrorBox error={graph.error} />

  return (
    <div className="page" style={{ maxWidth: 'none' }}>
      <div className="graph-toolbar" style={{ alignItems: 'center' }}>
        <div className="autocomplete" style={{ width: 320 }}>
          <input
            placeholder="Weltmodell durchsuchen …"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            aria-label="Weltmodell durchsuchen"
            role="combobox"
            aria-expanded={results.length > 0}
          />
          {results.length > 0 && (
            <div className="options" role="listbox" ref={listRef}>
              {results.map((hit, i) => (
                <button key={hit.id} type="button" role="option" aria-selected={i === active}
                  className={i === active ? 'active' : undefined}
                  onClick={() => { spotlight(hit.id); setQuery('') }}>
                  <span className="chip">{hit.type_id}</span>
                  <span>{hit.label ?? hit.id.slice(0, 8)}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {(['continuant', 'occurrent'] as Kind[]).map((kind) => (
          <label key={kind} className="chip" style={{ cursor: 'pointer', color: NODE_COLORS[kind] }}>
            <input
              type="checkbox"
              checked={kindFilter[kind]}
              onChange={(e) => setKindFilter({ ...kindFilter, [kind]: e.target.checked })}
            />
            {kind === 'continuant' ? '● Continuants' : '◆ Occurrents'}
          </label>
        ))}

        <select
          value={predicateFilter}
          style={{ width: 180 }}
          onChange={(e) => setPredicateFilter(e.target.value)}
          aria-label="Prädikat hervorheben"
        >
          <option value="">Alle Prädikate</option>
          {predicatesInGraph.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>

        <button type="button" className="ghost" onClick={() => canvasRef.current?.fit()}>
          Einpassen
        </button>

        <span className="mono small muted" style={{ marginLeft: 'auto' }}>
          {graph.data?.nodes.length ?? 0}/{graph.data?.total_nodes ?? 0} Knoten ·{' '}
          {graph.data?.edges.length ?? 0} Kanten · {stats.data?.statements ?? '–'} Statements
        </span>
      </div>

      <div className="graph-wrap" style={{ height: 'calc(100vh - 130px)' }}>
        {graph.data && helpers ? (
          <GraphCanvas
            ref={canvasRef}
            nodes={graph.data.nodes}
            edges={graph.data.edges}
            kindOf={kindOf}
            hiddenKinds={hiddenKinds}
            dimPredicate={predicateFilter || undefined}
            matchText={query.trim().length >= 2 ? query : undefined}
            onSelect={setSelected}
            onOpen={(id) => navigate(`/entity/${id}`)}
          />
        ) : (
          <div className="graph-canvas" />
        )}
        <aside className="graph-side">
          {!selected && (
            <div className="stack">
              <p className="muted small" style={{ margin: 0 }}>
                Das ganze Weltmodell. Hover: Nachbarschaft · Klick: Details ·
                Doppelklick: Entity-Seite.
              </p>
              {graph.data?.nodes.length === 0 && (
                <p className="small">
                  Noch leer. <Link to="/create">Erste Entity anlegen</Link> oder{' '}
                  <Link to="/sources">Dokument ingestieren</Link>.
                </p>
              )}
              {(stats.data?.pending_proposals ?? 0) > 0 && (
                <p className="small">
                  <Link to="/gate">{stats.data!.pending_proposals} Proposals warten im Gate →</Link>
                </p>
              )}
            </div>
          )}
          {selected && selectedView.data && (
            <div className="stack">
              <div>
                <div className="eyebrow">{selectedView.data.entity.type_id}</div>
                <h2 style={{ marginBottom: 4 }}>
                  <EntityLink id={selectedView.data.entity.id} label={selectedView.data.entity.label} />
                </h2>
                <div className="inline">
                  <KindBadge
                    kind={helpers?.kindOf(selectedView.data.entity.type_id)}
                    typeId={selectedView.data.entity.type_id}
                  />
                  <Link to={`/graph/${selected}`} className="small">Traverse →</Link>
                  <Link to={`/create?statement_subject=${selected}`} className="small">+ Statement</Link>
                </div>
              </div>
              {selectedView.data.statements.slice(0, 14).map((s) => (
                <div key={s.id} className="small">
                  <span className="predicate">{s.predicate_id}</span>{' '}
                  {s.value_type === 'entity'
                    ? <EntityLink id={s.object_id!} label={s.object_label} />
                    : <span>{s.value_text ?? s.value_number ?? ''}{s.value_unit ? ` ${s.value_unit}` : ''}</span>}
                </div>
              ))}
            </div>
          )}
        </aside>
      </div>
    </div>
  )
}
