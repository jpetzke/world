/// <reference lib="webworker" />
/* Force-Layout im Web Worker — der Main Thread rendert nur (60-FPS-Regel).

   Kein d3-Timer (braucht rAF): manuelles Stepping per setInterval, jede
   Iteration postet die Positionen als transferables Float32Array. Skeleton-
   Nodes mit persistierten Koordinaten sind gepinnt (fx/fy) — Expansion
   simuliert nur die NEU eingefügten Nodes um ihren Anker (kein globales
   Re-Layout, R4). `relayout` löst alle Pins: das ist der explizite
   Nutzerwunsch „Layout neu rechnen". */

import {
  forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY,
  type Simulation, type SimulationLinkDatum, type SimulationNodeDatum,
} from 'd3-force'

interface SimNode extends SimulationNodeDatum {
  id: string
  degree: number
}
type SimLink = SimulationLinkDatum<SimNode>

export interface WorkerNodeIn {
  id: string
  degree: number
  x: number | null
  y: number | null
  pinned: boolean
}
export interface WorkerEdgeIn { source: string; target: string }

export type LayoutMsgIn =
  | { type: 'init'; nodes: WorkerNodeIn[]; edges: WorkerEdgeIn[] }
  | { type: 'add'; nodes: WorkerNodeIn[]; edges: WorkerEdgeIn[] }
  | { type: 'remove'; ids: string[] }
  | { type: 'drag'; id: string; x: number; y: number }
  | { type: 'dragEnd'; id: string }
  | { type: 'relayout' }

export type LayoutMsgOut =
  | { type: 'ids'; ids: string[] }
  | { type: 'tick'; xy: Float32Array }
  | { type: 'settled' }

const post = (msg: LayoutMsgOut, transfer?: Transferable[]) =>
  (self as unknown as Worker).postMessage(msg, transfer ?? [])

let nodes: SimNode[] = []
let links: SimLink[] = []
let byId = new Map<string, SimNode>()
let sim: Simulation<SimNode, SimLink> | null = null
let timer: ReturnType<typeof setInterval> | null = null

const radius = (d: SimNode) => 5 + Math.sqrt(Math.max(0, d.degree)) * 1.6

function buildSim() {
  sim?.stop()
  sim = forceSimulation(nodes)
    .force('link', forceLink<SimNode, SimLink>(links)
      .id((d) => d.id)
      .distance((l) => radius(l.source as SimNode) + radius(l.target as SimNode) + 42)
      .strength(0.3))
    .force('charge', forceManyBody<SimNode>()
      .strength(nodes.length > 1500 ? -110 : -240)
      .distanceMax(600))
    .force('collide', forceCollide<SimNode>((d) => radius(d) + 10)
      .strength(1).iterations(2))
    // R7: Orphans (Grad ≤ 1) ziehen stärker zur Mitte — sie sollen die
    // Fläche nicht sprengen.
    .force('gx', forceX<SimNode>(0).strength((d) => (d.degree <= 1 ? 0.06 : 0.008)))
    .force('gy', forceY<SimNode>(0).strength((d) => (d.degree <= 1 ? 0.06 : 0.008)))
    .velocityDecay(0.45)
    .alphaDecay(0.055)
    .alphaMin(0.02)
    .stop()
}

function postIds() {
  post({ type: 'ids', ids: nodes.map((n) => n.id) })
}

function postPositions() {
  const xy = new Float32Array(nodes.length * 2)
  for (let i = 0; i < nodes.length; i++) {
    xy[i * 2] = nodes[i].x ?? 0
    xy[i * 2 + 1] = nodes[i].y ?? 0
  }
  post({ type: 'tick', xy }, [xy.buffer])
}

function run(alpha: number) {
  if (!sim) return
  sim.alpha(Math.max(sim.alpha(), alpha))
  if (timer) return
  timer = setInterval(() => {
    if (!sim) return
    sim.tick()
    postPositions()
    if (sim.alpha() < (sim.alphaMin() ?? 0.02)) {
      clearInterval(timer!)
      timer = null
      post({ type: 'settled' })
    }
  }, 16)
}

/** Golden-Angle-Seed wie in der Referenz-Demo: Hubs innen, Rest in Ringen —
    startet nah am Gleichgewicht statt aus dem Chaos. */
function seed(n: SimNode, i: number) {
  const a = i * 2.399963
  const r = n.degree > 8 ? 30 : (n.degree > 1 ? 260 : 420) + (i % 7) * 12
  n.x = Math.cos(a) * r
  n.y = Math.sin(a) * r
}

self.onmessage = (ev: MessageEvent<LayoutMsgIn>) => {
  const msg = ev.data
  switch (msg.type) {
    case 'init': {
      nodes = msg.nodes.map((m, i) => {
        const n: SimNode = { id: m.id, degree: m.degree, x: m.x ?? 0, y: m.y ?? 0 }
        if (m.x == null || m.y == null) seed(n, i)
        if (m.pinned && m.x != null && m.y != null) { n.fx = m.x; n.fy = m.y }
        return n
      })
      byId = new Map(nodes.map((n) => [n.id, n]))
      links = msg.edges
        .filter((e) => byId.has(e.source) && byId.has(e.target))
        .map((e) => ({ source: e.source, target: e.target }))
      buildSim()
      postIds()
      // Alles gepinnt (voll persistiertes Layout) → nichts zu simulieren.
      const free = nodes.filter((n) => n.fx == null).length
      if (free > 0) run(1)
      else { postPositions(); post({ type: 'settled' }) }
      break
    }
    case 'add': {
      // Bestand einfrieren: Expansion darf das stehende Layout nicht bewegen.
      for (const n of nodes) { n.fx = n.x; n.fy = n.y }
      for (const m of msg.nodes) {
        if (byId.has(m.id)) continue
        const n: SimNode = { id: m.id, degree: m.degree, x: m.x ?? 0, y: m.y ?? 0 }
        if (m.pinned && m.x != null && m.y != null) { n.fx = m.x; n.fy = m.y }
        nodes.push(n)
        byId.set(m.id, n)
      }
      for (const e of msg.edges) {
        if (byId.has(e.source) && byId.has(e.target)) {
          links.push({ source: e.source, target: e.target })
        }
      }
      buildSim()
      postIds()
      run(0.9)
      break
    }
    case 'remove': {
      const gone = new Set(msg.ids)
      nodes = nodes.filter((n) => !gone.has(n.id))
      byId = new Map(nodes.map((n) => [n.id, n]))
      links = links.filter((l) =>
        !gone.has((l.source as SimNode).id) && !gone.has((l.target as SimNode).id))
      buildSim()
      postIds()
      postPositions()
      break
    }
    case 'drag': {
      const n = byId.get(msg.id)
      if (!n) break
      n.fx = msg.x; n.fy = msg.y; n.x = msg.x; n.y = msg.y
      // Nachbarn weichen live aus — die Simulation bleibt drag-reaktiv.
      run(0.35)
      break
    }
    case 'dragEnd': {
      // Node bleibt, wo der Nutzer ihn abgelegt hat (fx/fy bestehen) —
      // mentale Karte gehört dem Nutzer, nicht der Physik.
      break
    }
    case 'relayout': {
      for (const n of nodes) { n.fx = null; n.fy = null }
      nodes.forEach((n, i) => seed(n, i))
      buildSim()
      run(1)
      break
    }
  }
}
