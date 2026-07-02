import { useQuery } from '@tanstack/react-query'
import cytoscape from 'cytoscape'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { EntityLink, ErrorBox, KindBadge, PageHead } from '../components/bits'
import { GRAPH_OPTIONS, GRAPH_STYLE, NODE_COLORS, kindColor, kindShape } from '../graph/style'
import { useVocabulary } from '../hooks/useVocabulary'

export function GraphPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const { helpers } = useVocabulary()
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<cytoscape.Core | null>(null)

  const [depth, setDepth] = useState(3)
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

  // Kanten-Prädikate, die im aktuellen Walk vorkommen (für den Filter)
  const seenPredicates = useMemo(() => {
    const set = new Set<string>()
    for (const node of walk.data ?? []) for (const via of node.via) set.add(via)
    return [...set].sort()
  }, [walk.data])

  useEffect(() => {
    if (!containerRef.current || !start.data || !walk.data || !helpers) return

    const nodes = new Map<string, cytoscape.ElementDefinition>()
    const startEntity = start.data.entity
    nodes.set(startEntity.id, {
      data: { id: startEntity.id, label: startEntity.label ?? startEntity.id.slice(0, 8), size: 44 },
      style: {
        'background-color': kindColor(helpers.kindOf(startEntity.type_id)),
        shape: kindShape(helpers.kindOf(startEntity.type_id)),
        'border-width': 3, 'border-color': '#e9e4d6',
      },
    })
    const edges: cytoscape.ElementDefinition[] = []
    for (const node of walk.data) {
      nodes.set(node.entity_id, {
        data: { id: node.entity_id, label: node.label ?? node.entity_id.slice(0, 8), size: 28 },
        style: {
          'background-color': kindColor(helpers.kindOf(node.type_id)),
          shape: kindShape(helpers.kindOf(node.type_id)),
        },
      })
      const from = node.path[node.path.length - 2]
      const via = node.via[node.via.length - 1]
      edges.push({
        data: {
          id: `${from}-${via}-${node.entity_id}`,
          source: from, target: node.entity_id, label: via,
        },
      })
    }

    const cy = cytoscape({
      container: containerRef.current,
      elements: [...nodes.values(), ...edges],
      style: GRAPH_STYLE,
      layout: { name: 'cose', animate: false, padding: 40 },
      ...GRAPH_OPTIONS,
    })
    cy.on('tap', 'node', (event) => setSelected(event.target.id()))
    cy.on('dbltap', 'node', (event) => navigate(`/entity/${event.target.id()}`))
    cyRef.current = cy
    return () => {
      cy.destroy()
      cyRef.current = null
    }
  }, [start.data, walk.data, helpers, navigate])

  if (start.error) return <ErrorBox error={start.error} />

  return (
    <div className="page" style={{ maxWidth: 'none' }}>
      <PageHead
        eyebrow="Traverse · Recursive CTE"
        title={
          <span className="inline">
            Graph um {start.data?.entity.label ?? '…'}
            {start.data && (
              <KindBadge kind={helpers?.kindOf(start.data.entity.type_id)} typeId={start.data.entity.type_id} />
            )}
          </span>
        }
        sub={<>Klick: Details · Doppelklick: Entity-Seite · <span style={{ color: NODE_COLORS.continuant }}>● Continuant</span> <span style={{ color: NODE_COLORS.occurrent }}>◆ Occurrent</span></>}
      />

      <div className="graph-toolbar">
        <label>
          Tiefe {depth}
          <input
            type="range" min={1} max={5} value={depth}
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
      </div>

      <div className="graph-wrap">
        <div ref={containerRef} className="graph-canvas" />
        <aside className="graph-side">
          {!selected && (
            <p className="muted small">
              {walk.data?.length ?? 0} erreichbare Entities in ≤{depth} Hops.
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
      {walk.data?.length === 0 && (
        <p className="muted" style={{ marginTop: 12 }}>
          Keine ausgehenden Kanten. <Link to={`/create?statement_subject=${id}`}>Statement anlegen?</Link>
        </p>
      )}
    </div>
  )
}
