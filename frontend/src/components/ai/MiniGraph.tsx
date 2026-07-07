/** Interaktiver Mini-Graph für welt_path/welt_traverse-Ergebnisse:
    d3-force-Layout in SVG — Knoten mit Label + Typ-Farbe, Kanten mit
    Prädikat-Beschriftung. Klick auf Knoten → Entity-Seite. */

import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from 'd3-force'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { nodeColor } from '../../graph/palette'

type GNode = SimulationNodeDatum & { id: string; label: string; typeId: string }
type GEdge = SimulationLinkDatum<GNode> & { predicate: string }

type Parsed = { nodes: GNode[]; edges: GEdge[] } | null

/** welt_traverse: {nodes, edges} · welt_path: {paths: [{nodes, edges}]}.
    Defensiv normalisieren — bei Offloading kommt nur das Sample. */
export function parseGraphResult(display: unknown): Parsed {
  if (!display || typeof display !== 'object') return null
  const d = display as Record<string, unknown>
  const source = d.sample && typeof d.sample === 'object' ? (d.sample as Record<string, unknown>) : d

  const nodeById = new Map<string, GNode>()
  const edges: GEdge[] = []

  const addNode = (n: unknown) => {
    if (!n || typeof n !== 'object') return
    const node = n as Record<string, unknown>
    const id = String(node.id ?? '')
    if (!id || nodeById.has(id)) return
    nodeById.set(id, {
      id,
      label: String(node.label ?? id.slice(0, 8)),
      typeId: String(node.type_id ?? ''),
    })
  }
  const addEdge = (e: unknown) => {
    if (!e || typeof e !== 'object') return
    const edge = e as Record<string, unknown>
    const source = String(edge.subject ?? edge.subject_id ?? '')
    const target = String(edge.object ?? edge.object_id ?? '')
    if (!source || !target) return
    edges.push({ source, target, predicate: String(edge.predicate ?? edge.predicate_id ?? '') })
  }

  if (Array.isArray(source.nodes)) source.nodes.forEach(addNode)
  if (Array.isArray(source.edges)) source.edges.forEach(addEdge)
  if (Array.isArray(source.paths)) {
    for (const p of source.paths as Record<string, unknown>[]) {
      if (Array.isArray(p?.nodes)) p.nodes.forEach(addNode)
      if (Array.isArray(p?.edges)) p.edges.forEach(addEdge)
    }
  }
  // Kanten auf bekannte Knoten beschränken (ids-Output hat keine Objekte).
  const valid = edges.filter(
    (e) => nodeById.has(e.source as string) && nodeById.has(e.target as string),
  )
  if (nodeById.size < 2) return null
  return { nodes: [...nodeById.values()], edges: valid }
}

const WIDTH = 640
const HEIGHT = 360

export function MiniGraph({ data }: { data: NonNullable<Parsed> }) {
  const navigate = useNavigate()
  const [tick, setTick] = useState(0)
  // Simulation mutiert die Node-Objekte in place; useMemo hält Identität.
  const sim = useMemo(() => {
    const nodes = data.nodes.map((n) => ({ ...n }))
    const byId = new Map(nodes.map((n) => [n.id, n]))
    const links = data.edges
      .map((e) => ({ ...e, source: byId.get(e.source as string)!, target: byId.get(e.target as string)! }))
      .filter((e) => e.source && e.target)
    const simulation = forceSimulation(nodes)
      .force('charge', forceManyBody().strength(-220))
      .force('link', forceLink<GNode, GEdge>(links).id((n) => n.id).distance(90))
      .force('center', forceCenter(WIDTH / 2, HEIGHT / 2))
      .force('collide', forceCollide(26))
      .stop()
    return { nodes, links, simulation }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])

  useEffect(() => {
    sim.simulation.on('tick', () => setTick((t) => t + 1)).restart()
    return () => {
      sim.simulation.stop()
    }
  }, [sim])

  void tick
  const clamp = (v: number | undefined, max: number) => Math.max(18, Math.min(max - 18, v ?? max / 2))

  return (
    <svg
      className="ai-minigraph"
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      role="img"
      aria-label={`Graph mit ${sim.nodes.length} Knoten`}
    >
      {sim.links.map((l, i) => {
        const s = l.source as GNode
        const t = l.target as GNode
        const x1 = clamp(s.x, WIDTH)
        const y1 = clamp(s.y, HEIGHT)
        const x2 = clamp(t.x, WIDTH)
        const y2 = clamp(t.y, HEIGHT)
        return (
          <g key={i}>
            <line x1={x1} y1={y1} x2={x2} y2={y2} className="ai-mg-edge" />
            {l.predicate && (
              <text x={(x1 + x2) / 2} y={(y1 + y2) / 2 - 4} className="ai-mg-pred">
                {l.predicate}
              </text>
            )}
          </g>
        )
      })}
      {sim.nodes.map((n) => {
        const x = clamp(n.x, WIDTH)
        const y = clamp(n.y, HEIGHT)
        return (
          <g
            key={n.id}
            transform={`translate(${x},${y})`}
            className="ai-mg-node"
            onClick={() => navigate(`/entity/${n.id}`)}
          >
            <circle r={9} fill={nodeColor(n.typeId, undefined)} />
            <title>{`${n.label} (${n.typeId})`}</title>
            <text y={-14} className="ai-mg-label">
              {n.label.length > 24 ? n.label.slice(0, 23) + '…' : n.label}
            </text>
          </g>
        )
      })}
    </svg>
  )
}
