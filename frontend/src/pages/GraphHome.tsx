import { useMutation, useQuery } from '@tanstack/react-query'
import { useCallback, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Kind } from '../api/types'
import { ErrorBox } from '../components/bits'
import { Combobox } from '../components/Combobox'
import { Inspector } from '../components/Inspector'
import { useOptionNav } from '../components/useOptionNav'
import { GraphView, type GraphViewHandle } from '../graph/GraphView'
import { useVocabulary } from '../hooks/useVocabulary'

// DoI-Rendering: Das Skeleton (Top-PageRank je Community + Hubs) ist der
// Startzustand; alles Weitere kommt fokusgetrieben per Expansion dazu.
// Das Budget hält den Renderer konstant, egal wie groß die DB ist.
const SKELETON_BUDGET = 800
const NODE_BUDGET = 3000
const EXPAND_MAX = 150
const DEPTH_DEFAULT = 2

/** Home: repräsentatives Grundgerüst des Weltmodells. Klick auf Node oder
    Ghost-Badge lädt die Nachbarschaft nach; Suche lädt Treffer + Umfeld +
    kürzesten Pfad zum bestehenden Ausschnitt (sonst Insel mit Badges). */
export function GraphHome() {
  const navigate = useNavigate()
  const { helpers } = useVocabulary()
  const viewRef = useRef<GraphViewHandle>(null)

  const [selected, setSelected] = useState<string | null>(null)
  const [depth, setDepth] = useState(DEPTH_DEFAULT)
  const [query, setQuery] = useState('')
  const [kindFilter, setKindFilter] = useState<Record<Kind, boolean>>({
    continuant: true,
    occurrent: true,
  })
  const [predicateFilter, setPredicateFilter] = useState<string>('')
  const [loaded, setLoaded] = useState({ nodes: 0, edges: 0 })

  const skeleton = useQuery({
    queryKey: ['skeleton', SKELETON_BUDGET],
    queryFn: () => api.skeleton(SKELETON_BUDGET),
  })
  const stats = useQuery({ queryKey: ['stats'], queryFn: api.stats })

  // Konvergierte Positionen persistieren (R4) — gesammelt, nicht pro Tick.
  const savePositions = useMutation({ mutationFn: api.savePositions })
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const onSettled = useCallback((positions: { id: string; x: number; y: number }[]) => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      if (positions.length) savePositions.mutate(positions.slice(0, 5000))
    }, 1500)
  }, [savePositions])

  // Expansion: Nachbarschaft eines Nodes nachladen und am Anker einfügen.
  const expanding = useRef(new Set<string>())
  const expand = useCallback(async (id: string, maxDepth = 1) => {
    if (expanding.current.has(id)) return
    expanding.current.add(id)
    try {
      const data = await api.traverse({ start_id: id, max_depth: maxDepth, max_nodes: EXPAND_MAX })
      viewRef.current?.addSubgraph(data.nodes, data.edges, data.start_id)
    } finally {
      expanding.current.delete(id)
    }
  }, [])

  // Suchtreffer: Umfeld per Tiefe + kürzester Pfad zum bestehenden Ausschnitt,
  // damit der Treffer nicht kontextlos schwebt. Kein Pfad → Insel mit Badges.
  const spotlight = useCallback(async (id: string) => {
    const targets = viewRef.current?.loadedIds() ?? []
    const [env, path] = await Promise.all([
      api.traverse({ start_id: id, max_depth: depth, max_nodes: EXPAND_MAX }),
      targets.length
        ? api.graphPath(id, targets.slice(0, 5000)).catch(() => null)
        : Promise.resolve(null),
    ])
    if (path?.found && path.nodes.length) {
      viewRef.current?.addSubgraph(path.nodes, path.edges)
    }
    viewRef.current?.addSubgraph(env.nodes, env.edges, env.start_id)
    viewRef.current?.focusOn(env.start_id)
    setSelected(env.start_id)
  }, [depth])

  const predicatesInGraph = useMemo(
    () => [...new Set((skeleton.data?.edges ?? []).map((e) => e.predicate_id))].sort(),
    [skeleton.data],
  )
  const hiddenKinds = useMemo(
    () => (Object.entries(kindFilter) as [Kind, boolean][])
      .filter(([, on]) => !on).map(([k]) => k),
    [kindFilter],
  )
  const kindOf = useCallback((t: string) => helpers?.kindOf(t), [helpers])

  const search = useQuery({
    queryKey: ['search', query],
    queryFn: () => api.search(query),
    enabled: query.trim().length >= 2,
  })
  const results = query.trim().length >= 2 ? (search.data ?? []).slice(0, 8) : []

  const { active, listRef, onKeyDown } = useOptionNav(
    results,
    (hit) => { void spotlight(hit.id); setQuery('') },
    () => setQuery(''),
  )

  if (skeleton.error) return <ErrorBox error={skeleton.error} />

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
                  onClick={() => { void spotlight(hit.id); setQuery('') }}>
                  <span className="chip">{hit.type_id}</span>
                  <span>{hit.label ?? hit.id.slice(0, 8)}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {(['continuant', 'occurrent'] as Kind[]).map((kind) => (
          <button
            key={kind}
            type="button"
            className={`tchip${kindFilter[kind] ? ' on' : ''}${kind === 'occurrent' ? ' occ' : ''}`}
            aria-pressed={kindFilter[kind]}
            onClick={() => setKindFilter({ ...kindFilter, [kind]: !kindFilter[kind] })}
          >
            <span className={`dot${kind === 'occurrent' ? ' diamond' : ''}`} />
            {kind === 'continuant' ? 'Continuants' : 'Occurrents'}
          </button>
        ))}

        <div style={{ width: 200 }}>
          <Combobox
            options={[{ id: '', label: 'Alle Prädikate' },
              ...predicatesInGraph.map((p) => ({ id: p, label: p }))]}
            value={predicateFilter}
            onChange={setPredicateFilter}
            placeholder="Prädikat hervorheben"
          />
        </div>

        <div className="seg" role="group" aria-label="Tiefe der Expansion bei Suche">
          {[1, 2, 3].map((d) => (
            <button key={d} type="button" className={depth === d ? 'on' : undefined}
              onClick={() => setDepth(d)}>
              Tiefe {d}
            </button>
          ))}
        </div>

        <button type="button" className="ghost" onClick={() => viewRef.current?.fit()}>
          Einpassen
        </button>
        <button type="button" className="ghost" onClick={() => viewRef.current?.relayout()}
          title="Layout neu berechnen (verwirft gespeicherte Positionen)">
          Neu layouten
        </button>

        <div className="gstats">
          <div className="gstat"><b>{loaded.nodes}</b><span>geladen</span></div>
          <div className="gstat"><b>{skeleton.data?.total_nodes ?? 0}</b><span>Entities</span></div>
          <div className="gstat"><b>{stats.data?.statements ?? '–'}</b><span>Statements</span></div>
        </div>
      </div>

      <div className="graph-wrap" style={{ height: 'calc(100vh - 130px)' }}>
        {skeleton.data && helpers ? (
          <GraphView
            ref={viewRef}
            nodes={skeleton.data.nodes}
            edges={skeleton.data.edges}
            kindOf={kindOf}
            hiddenKinds={hiddenKinds}
            dimPredicate={predicateFilter || undefined}
            matchText={query.trim().length >= 2 ? query : undefined}
            budget={NODE_BUDGET}
            onSelect={setSelected}
            onOpen={(id) => navigate(`/entity/${id}`)}
            onExpand={(id) => { void expand(id) }}
            onSettled={onSettled}
            onStats={setLoaded}
          />
        ) : (
          <div className="graph-canvas" />
        )}
        <aside className={`graph-side${selected ? ' open' : ''}`}>
          {!selected && (
            <div className="stack">
              <p className="muted small" style={{ margin: 0 }}>
                Repräsentatives Grundgerüst (Top-PageRank je Community).
                „+N"-Badges zeigen nicht geladene Nachbarn — Klick lädt sie nach.
                Suche holt Treffer samt Pfad hierher.
              </p>
              {skeleton.data?.nodes.length === 0 && (
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
          {selected && (
            <Inspector
              entityId={selected}
              degree={viewRef.current?.dbDegree(selected)}
              onClose={() => setSelected(null)}
            />
          )}
        </aside>
      </div>
    </div>
  )
}
