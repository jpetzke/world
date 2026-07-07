import { useMutation, useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { EntityLink, ErrorBox, KindBadge, PageHead } from '../components/bits'
import { GraphView, type GraphViewHandle } from '../graph/GraphView'
import { CONT, OCC } from '../graph/palette'
import { useVocabulary } from '../hooks/useVocabulary'

const EXPAND_MAX = 150

export function GraphPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const { helpers } = useVocabulary()
  const viewRef = useRef<GraphViewHandle>(null)

  const [depth, setDepth] = useState(1)
  const [predicateFilter, setPredicateFilter] = useState<string[]>([])
  const [selected, setSelected] = useState<string | null>(null)

  const start = useQuery({ queryKey: ['entity', id], queryFn: () => api.entity(id) })
  const walk = useQuery({
    queryKey: ['traverse', id, depth, predicateFilter],
    queryFn: () => api.traverse({
      start_id: id,
      max_depth: depth,
      predicates: predicateFilter.length ? predicateFilter : null,
    }),
    enabled: !!id,
  })
  const selectedView = useQuery({
    queryKey: ['entity', selected, 'panel'],
    queryFn: () => api.entity(selected!),
    enabled: !!selected,
  })

  const savePositions = useMutation({ mutationFn: api.savePositions })
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const onSettled = useCallback((positions: { id: string; x: number; y: number }[]) => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      if (positions.length) savePositions.mutate(positions.slice(0, 5000))
    }, 1500)
  }, [savePositions])

  const expanding = useRef(new Set<string>())
  const expand = useCallback(async (nid: string) => {
    if (expanding.current.has(nid)) return
    expanding.current.add(nid)
    try {
      const data = await api.traverse({ start_id: nid, max_depth: 1, max_nodes: EXPAND_MAX })
      viewRef.current?.addSubgraph(data.nodes, data.edges, data.start_id)
    } finally {
      expanding.current.delete(nid)
    }
  }, [])

  // Startknoten der Ego-Sicht markieren, sobald der Teilgraph steht.
  useEffect(() => {
    if (walk.data) viewRef.current?.focusOn(walk.data.start_id)
  }, [walk.data])

  const seenPredicates = useMemo(
    () => [...new Set((walk.data?.edges ?? []).map((e) => e.predicate_id))].sort(),
    [walk.data],
  )
  const kindOf = useCallback((t: string) => helpers?.kindOf(t), [helpers])
  const capped = walk.data && walk.data.total_nodes > walk.data.nodes.length

  if (start.error) return <ErrorBox error={start.error} />

  return (
    <div className="page" style={{ maxWidth: 'none' }}>
      <PageHead
        eyebrow="Nachbarschaft · induzierter Teilgraph"
        title={
          <span className="inline">
            Graph um {start.data?.entity.label ?? '…'}
            {start.data && (
              <KindBadge kind={helpers?.kindOf(start.data.entity.type_id)} typeId={start.data.entity.type_id} />
            )}
          </span>
        }
        sub={<>Klick: Details + Nachladen · Doppelklick: Entity-Seite · <span style={{ color: CONT }}>● Continuant</span> <span style={{ color: OCC }}>◆ Occurrent</span></>}
      />

      <div className="graph-toolbar">
        <label>
          Tiefe {depth}
          <input
            type="range" min={1} max={3} value={depth}
            style={{ width: 120 }}
            onChange={(e) => setDepth(Number(e.target.value))}
          />
        </label>
        {seenPredicates.map((p) => (
          <label key={p} className="chip" style={{ cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={predicateFilter.length === 0 || predicateFilter.includes(p)}
              onChange={(e) => {
                const base = predicateFilter.length === 0 ? seenPredicates : predicateFilter
                setPredicateFilter(
                  e.target.checked ? [...base, p] : base.filter((x) => x !== p),
                )
              }}
            />
            {p}
          </label>
        ))}
        {predicateFilter.length > 0 && (
          <button type="button" className="ghost" onClick={() => setPredicateFilter([])}>
            Filter zurücksetzen
          </button>
        )}
        <button type="button" className="ghost" onClick={() => viewRef.current?.fit()}>
          Einpassen
        </button>
        <span className="mono small muted" style={{ marginLeft: 'auto' }}>
          {capped
            ? <>zeigt {walk.data!.nodes.length} von {walk.data!.total_nodes} (nächste zuerst)</>
            : <>{walk.data?.nodes.length ?? 0} Knoten · {walk.data?.edges.length ?? 0} Kanten</>}
        </span>
      </div>

      <div className="graph-wrap">
        {walk.data && helpers ? (
          <GraphView
            ref={viewRef}
            nodes={walk.data.nodes}
            edges={walk.data.edges}
            kindOf={kindOf}
            onSelect={setSelected}
            onOpen={(nid) => navigate(`/entity/${nid}`)}
            onExpand={(nid) => { void expand(nid) }}
            onSettled={onSettled}
          />
        ) : (
          <div className="graph-canvas" />
        )}
        <aside className="graph-side">
          {!selected && (
            <p className="muted small">
              {walk.data?.nodes.length ?? 0} Entities in ≤{depth} Hops (ungerichtet).
              Knoten anklicken für Details.
            </p>
          )}
          {selected && selectedView.data && (
            <div className="stack">
              <div>
                <div className="eyebrow">{selectedView.data.entity.type_id}</div>
                <h2 style={{ marginBottom: 4 }}>
                  <EntityLink id={selectedView.data.entity.id} label={selectedView.data.entity.label} />
                </h2>
                <button type="button" className="ghost" onClick={() => navigate(`/graph/${selected}`)}>
                  Als Startpunkt →
                </button>
              </div>
              {selectedView.data.statements.slice(0, 12).map((s) => (
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
      {walk.data?.nodes.length === 1 && (
        <p className="muted" style={{ marginTop: 12 }}>
          Keine verknüpften Entities. <Link to={`/create?statement_subject=${id}`}>Statement anlegen?</Link>
        </p>
      )}
    </div>
  )
}
