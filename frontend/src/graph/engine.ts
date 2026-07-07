/* GraphEngine — Degree-of-Interest-Rendering auf sigma.js + graphology.

   Architekturprinzip: Es wird NIE der ganze Bestand gerendert, sondern ein
   repräsentatives Skeleton plus fokusgetriebene Expansion; das Node-Budget
   bleibt konstant (~3k), egal ob die DB 100 oder 500k Entities hält.

   Arbeitsteilung:
   - sigma (WebGL): Nodes (Kreis/Raute), Kanten, Node-Labels mit LOD-Grid,
     Kamera (Pan/Zoom/Pinch), Picking.
   - Web Worker: d3-force — der Main Thread simuliert nie (60-FPS-Regel).
   - Overlay-Canvas: Ghost-Badges (+N nicht geladene Nachbarn — Pflicht,
     sonst wirkt der Ausschnitt fälschlich vollständig), horizontale
     Kantenlabel-Chips bei Fokus (R3), Auswahl-/Hover-Ringe.

   Die Interaktionslogik (Fokus-Dimming 12%, Label-LOD, 20px-Treffradius,
   Kanten-Chips) ist der Port der Referenz-Demo aus
   weltmodell-design-system.html, Tab 04. */

import Graph from 'graphology'
import Sigma from 'sigma'
import { EdgeArrowProgram, NodeCircleProgram } from 'sigma/rendering'
import type { EdgeDisplayData, NodeDisplayData } from 'sigma/types'
import type { GraphEdgeDTO, GraphNodeDTO, Kind } from '../api/types'
import { NodeDiamondProgram } from './diamond'
import {
  BADGE_BG, CHIP_BG, CHIP_BORDER, DIM, EDGE_BASE, EDGE_HL, MATCH_GOLD,
  TEXT_BRIGHT, TEXT_DIM, TEXT_META, nodeColor, nodeSize, withAlpha,
} from './palette'
import type { LayoutMsgIn, LayoutMsgOut, WorkerEdgeIn, WorkerNodeIn } from './layout.worker'

export interface EngineCallbacks {
  onSelect?: (id: string | null) => void
  onOpen?: (id: string) => void
  /** Klick auf Ghost-Badge oder Node mit nicht geladenen Nachbarn. */
  onExpand?: (id: string) => void
  /** Layout konvergiert → Positionen zum Persistieren (R4). */
  onSettled?: (positions: { id: string; x: number; y: number }[]) => void
  onStats?: (stats: { nodes: number; edges: number }) => void
}

export interface EngineFilters {
  hiddenKinds?: Kind[]
  dimPredicate?: string
  matchText?: string
}

const HIT_MIN = 20 // R8: Treffradius min. 20px, unabhängig vom visuellen Radius
const FADE_MS = 200
const EVICT_FADE_MS = 160

interface Fade { from: number; to: number; start: number; drop: boolean }

export class GraphEngine {
  readonly graph: Graph
  private sigma: Sigma
  private worker: Worker
  private workerIds: string[] = []
  private overlay: HTMLCanvasElement
  private octx: CanvasRenderingContext2D
  private container: HTMLElement
  private kindOf: (typeId: string) => Kind | undefined
  private cb: EngineCallbacks
  private budget: number

  private filters: EngineFilters = {}
  private hover: string | null = null
  private selected: string | null = null
  private neighborhood: Set<string> | null = null
  private litByPredicate: Set<string> | null = null
  private badgeRects: { x: number; y: number; w: number; h: number; id: string }[] = []
  private fades = new Map<string, Fade>()
  private fadeRaf = 0
  private fitAfterSettle = false
  private dragged: string | null = null
  private dragMoved = false
  private destroyed = false
  private reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches

  constructor(
    container: HTMLElement,
    kindOf: (typeId: string) => Kind | undefined,
    callbacks: EngineCallbacks,
    opts?: { budget?: number },
  ) {
    this.container = container
    this.kindOf = kindOf
    this.cb = callbacks
    this.budget = opts?.budget ?? 3000
    this.graph = new Graph({ multi: true, type: 'directed' })

    this.sigma = new Sigma(this.graph, container, {
      defaultNodeType: 'circle',
      defaultEdgeType: 'arrow',
      nodeProgramClasses: { circle: NodeCircleProgram, diamond: NodeDiamondProgram },
      edgeProgramClasses: { arrow: EdgeArrowProgram },
      // Label-LOD (R1): Schwelle wirkt auf die GERENDERTE Größe = size/zoom.
      // size ∝ sqrt(Grad) → Sichtbarkeit = f(Grad × Zoom); das Grid darunter
      // übernimmt die Kollisionsvermeidung.
      labelRenderedSizeThreshold: 7,
      labelDensity: 1.4,
      labelGridCellSize: 90,
      labelFont: '"IBM Plex Sans", system-ui, sans-serif',
      labelSize: 11,
      labelWeight: '500',
      labelColor: { color: TEXT_DIM },
      defaultDrawNodeLabel: this.drawNodeLabel,
      defaultDrawNodeHover: () => {}, // Ringe zeichnet das Overlay
      renderEdgeLabels: false, // Kanten-Chips nur bei Fokus, via Overlay (R3)
      stagePadding: 48,
      minCameraRatio: 0.04,
      maxCameraRatio: 6,
      zoomingRatio: 1.5,
      zIndex: true,
      allowInvalidContainer: true,
      nodeReducer: this.nodeReducer,
      edgeReducer: this.edgeReducer,
    })

    this.overlay = document.createElement('canvas')
    this.overlay.className = 'graph-overlay'
    container.appendChild(this.overlay)
    this.octx = this.overlay.getContext('2d')!
    this.resizeOverlay()

    this.worker = new Worker(new URL('./layout.worker.ts', import.meta.url), { type: 'module' })
    this.worker.onmessage = (ev: MessageEvent<LayoutMsgOut>) => this.onWorker(ev.data)

    this.wireEvents()
  }

  // --- Öffentliche API -------------------------------------------------------

  setSkeleton(nodes: GraphNodeDTO[], edges: GraphEdgeDTO[]) {
    this.graph.clear()
    this.hover = null
    this.selected = null
    this.updateFocus()
    nodes.forEach((n, i) => this.addNode(n, i))
    for (const e of edges) this.addEdge(e)
    this.graph.forEachNode((id) => this.graph.setNodeAttribute(id, 'skeleton', true))
    this.pushInit()
    this.emitStats()
  }

  /** Expansion: Nachbarschaft/Suchtreffer/Pfad nahe des Ankers einfügen.
      Fade-in; Eviction hält das Budget (LRU × Grad; Skeleton, Selektion und
      Anker sind nie Kandidaten). */
  addSubgraph(nodes: GraphNodeDTO[], edges: GraphEdgeDTO[], anchorId?: string) {
    const anchor = anchorId && this.graph.hasNode(anchorId)
      ? { x: this.graph.getNodeAttribute(anchorId, 'x') as number,
          y: this.graph.getNodeAttribute(anchorId, 'y') as number }
      : null
    const fresh: GraphNodeDTO[] = []
    for (const n of nodes) {
      if (this.graph.hasNode(n.id)) { this.touch(n.id); continue }
      fresh.push(n)
    }
    fresh.forEach((n, i) => {
      this.addNode(n, i, anchor)
      if (!this.reducedMotion) {
        const target = this.graph.getNodeAttribute(n.id, 'size') as number
        this.graph.setNodeAttribute(n.id, 'size', 0.01)
        this.fades.set(n.id, { from: 0.01, to: target, start: performance.now(), drop: false })
      }
    })
    const newEdges: GraphEdgeDTO[] = []
    for (const e of edges) {
      if (this.addEdge(e)) newEdges.push(e)
    }
    if (anchorId) this.touch(anchorId)
    this.startFades()
    this.evictToBudget(new Set([...fresh.map((n) => n.id), anchorId ?? '']))
    if (fresh.length || newEdges.length) {
      this.worker.postMessage({
        type: 'add',
        nodes: fresh.map((n) => this.toWorkerNode(n)),
        edges: newEdges.map((e) => this.toWorkerEdge(e)),
      } satisfies LayoutMsgIn)
    }
    this.emitStats()
    this.refresh()
  }

  setFilters(filters: EngineFilters) {
    this.filters = filters
    const dim = filters.dimPredicate
    if (dim) {
      const lit = new Set<string>()
      this.graph.forEachEdge((_e, attrs, src, tgt) => {
        if (attrs.predicate === dim) { lit.add(src); lit.add(tgt) }
      })
      this.litByPredicate = lit
    } else {
      this.litByPredicate = null
    }
    this.refresh()
  }

  select(id: string | null) {
    if (id && !this.graph.hasNode(id)) return
    this.selected = id
    if (id) this.touch(id)
    this.updateFocus()
    this.refresh()
  }

  /** Zum Knoten schwenken + selektieren. false = nicht geladen. */
  focusOn(id: string): boolean {
    if (!this.graph.hasNode(id)) return false
    this.select(id)
    this.cb.onSelect?.(id)
    const data = this.sigma.getNodeDisplayData(id)
    if (data) {
      this.sigma.getCamera().animate(
        { x: data.x, y: data.y, ratio: 0.25 },
        { duration: this.reducedMotion ? 0 : 350 },
      )
    }
    return true
  }

  fit() {
    this.sigma.getCamera().animatedReset({ duration: this.reducedMotion ? 0 : 300 })
  }

  /** Globales Re-Layout — nur auf expliziten Nutzerwunsch (R4). */
  relayout() {
    this.fitAfterSettle = true
    this.worker.postMessage({ type: 'relayout' } satisfies LayoutMsgIn)
  }

  ghostCount(id: string): number {
    if (!this.graph.hasNode(id)) return 0
    const db = this.graph.getNodeAttribute(id, 'dbDegree') as number
    return Math.max(0, db - this.graph.degree(id))
  }

  loadedIds(): string[] {
    return this.graph.nodes()
  }

  positions(): { id: string; x: number; y: number }[] {
    return this.graph.mapNodes((id, a) => ({ id, x: a.x as number, y: a.y as number }))
  }

  destroy() {
    this.destroyed = true
    cancelAnimationFrame(this.fadeRaf)
    this.worker.terminate()
    this.sigma.kill()
    this.overlay.remove()
  }

  // --- Graph-Aufbau ----------------------------------------------------------

  private addNode(n: GraphNodeDTO, seedIndex: number, anchor?: { x: number; y: number } | null) {
    const kind = this.kindOf(n.type_id) ?? 'continuant'
    let x = n.x ?? null
    let y = n.y ?? null
    if (x == null || y == null) {
      if (anchor) {
        // Neue Nachbarn wachsen aus dem Anker heraus, nicht vom Ursprung.
        const a = seedIndex * 2.399963
        const r = 30 + (seedIndex % 5) * 14
        x = anchor.x + Math.cos(a) * r
        y = anchor.y + Math.sin(a) * r
      } else {
        const a = seedIndex * 2.399963
        const r = n.degree > 8 ? 30 : (n.degree > 1 ? 260 : 420) + (seedIndex % 7) * 12
        x = Math.cos(a) * r
        y = Math.sin(a) * r
      }
    }
    this.graph.addNode(n.id, {
      x, y,
      size: nodeSize(n.degree),
      color: nodeColor(n.type_id, kind),
      type: kind === 'occurrent' ? 'diamond' : 'circle',
      label: n.label ?? n.id.slice(0, 8),
      kind,
      typeId: n.type_id,
      dbDegree: n.degree,
      persisted: n.x != null && n.y != null,
      skeleton: false,
      lastTouch: performance.now(),
    })
  }

  private addEdge(e: GraphEdgeDTO): boolean {
    if (this.graph.hasEdge(e.id)) return false
    if (!this.graph.hasNode(e.subject_id) || !this.graph.hasNode(e.object_id)) return false

    this.graph.addEdgeWithKey(e.id, e.subject_id, e.object_id, {
      // Konfidenz → Deckkraft UND Breite: Licht ∝ Sicherheit.
      color: withAlpha(EDGE_BASE, 0.35 + 0.5 * e.confidence),
      size: 1 + e.confidence * 1.2,
      predicate: e.predicate_id,
      confidence: e.confidence,
    })
    return true
  }

  private toWorkerNode(n: GraphNodeDTO): WorkerNodeIn {
    return {
      id: n.id, degree: n.degree,
      x: this.graph.getNodeAttribute(n.id, 'x') as number,
      y: this.graph.getNodeAttribute(n.id, 'y') as number,
      pinned: this.graph.getNodeAttribute(n.id, 'persisted') as boolean,
    }
  }

  private toWorkerEdge(e: GraphEdgeDTO): WorkerEdgeIn {
    return { source: e.subject_id, target: e.object_id }
  }

  private pushInit() {
    const nodes: WorkerNodeIn[] = this.graph.mapNodes((id, a) => ({
      id, degree: a.dbDegree as number,
      x: a.persisted ? (a.x as number) : null,
      y: a.persisted ? (a.y as number) : null,
      pinned: a.persisted as boolean,
    }))
    const edges: WorkerEdgeIn[] = this.graph.mapEdges((_id, _a, src, tgt) =>
      ({ source: src, target: tgt }))
    this.fitAfterSettle = true
    this.worker.postMessage({ type: 'init', nodes, edges } satisfies LayoutMsgIn)
  }

  // --- Eviction (LRU × Grad) ---------------------------------------------------

  private evictToBudget(protectedIds: Set<string>) {
    const excess = this.graph.order - this.budget
    if (excess <= 0) return
    const candidates = this.graph
      .filterNodes((id, a) =>
        !a.skeleton && id !== this.selected && !protectedIds.has(id))
      .sort((a, b) => {
        const ta = this.graph.getNodeAttribute(a, 'lastTouch') as number
        const tb = this.graph.getNodeAttribute(b, 'lastTouch') as number
        if (ta !== tb) return ta - tb
        return (this.graph.getNodeAttribute(a, 'dbDegree') as number)
          - (this.graph.getNodeAttribute(b, 'dbDegree') as number)
      })
      .slice(0, excess)
    if (!candidates.length) return
    if (this.reducedMotion) {
      this.dropNodes(candidates)
    } else {
      for (const id of candidates) {
        this.fades.set(id, {
          from: this.graph.getNodeAttribute(id, 'size') as number,
          to: 0.01, start: performance.now(), drop: true,
        })
      }
      this.startFades()
    }
  }

  private dropNodes(ids: string[]) {
    for (const id of ids) {
      if (this.graph.hasNode(id)) this.graph.dropNode(id)
      if (this.hover === id) this.hover = null
    }
    this.worker.postMessage({ type: 'remove', ids } satisfies LayoutMsgIn)
    this.updateFocus()
    this.emitStats()
  }

  private touch(id: string) {
    if (this.graph.hasNode(id)) {
      this.graph.setNodeAttribute(id, 'lastTouch', performance.now())
    }
  }

  // --- Fade-Animationen (Größe; reduced-motion überspringt sie) ----------------

  private startFades() {
    if (this.fadeRaf || !this.fades.size) return
    const step = () => {
      this.fadeRaf = 0
      if (this.destroyed) return
      const now = performance.now()
      const toDrop: string[] = []
      for (const [id, f] of this.fades) {
        if (!this.graph.hasNode(id)) { this.fades.delete(id); continue }
        const dur = f.drop ? EVICT_FADE_MS : FADE_MS
        const t = Math.min(1, (now - f.start) / dur)
        const eased = 1 - (1 - t) * (1 - t)
        this.graph.setNodeAttribute(id, 'size', f.from + (f.to - f.from) * eased)
        if (t >= 1) {
          this.fades.delete(id)
          if (f.drop) toDrop.push(id)
        }
      }
      if (toDrop.length) this.dropNodes(toDrop)
      if (this.fades.size) this.fadeRaf = requestAnimationFrame(step)
    }
    this.fadeRaf = requestAnimationFrame(step)
  }

  // --- Worker-Rückkanal --------------------------------------------------------

  private onWorker(msg: LayoutMsgOut) {
    if (this.destroyed) return
    switch (msg.type) {
      case 'ids':
        this.workerIds = msg.ids
        break
      case 'tick': {
        const xy = msg.xy
        const ids = this.workerIds
        // Ein Batch-Update statt n einzelner Events: eine Neuzeichnung pro Tick.
        const index = new Map<string, number>()
        for (let i = 0; i < ids.length; i++) index.set(ids[i], i)
        this.graph.updateEachNodeAttributes((id, attrs) => {
          const i = index.get(id)
          if (i === undefined) return attrs
          // Der gezogene Node gehört dem Pointer, nicht der Physik.
          if (id === this.dragged) return attrs
          return { ...attrs, x: xy[i * 2], y: xy[i * 2 + 1] }
        }, { attributes: ['x', 'y'] })
        break
      }
      case 'settled': {
        if (this.fitAfterSettle) {
          this.fitAfterSettle = false
          this.fit() // Auto-Fit nach Konvergenz (R7)
        }
        this.graph.forEachNode((id) => this.graph.setNodeAttribute(id, 'persisted', true))
        this.cb.onSettled?.(this.positions())
        break
      }
    }
  }

  // --- Reducer (Fokus-Dimming R6, Filter, Match) --------------------------------

  private nodeReducer = (id: string, data: Record<string, unknown>): Partial<NodeDisplayData> => {
    const res: Partial<NodeDisplayData> & Record<string, unknown> = {
      ...data,
      zIndex: 0,
    }
    const kind = data.kind as Kind
    if (this.filters.hiddenKinds?.includes(kind)) {
      res.hidden = true
      return res
    }
    const q = (this.filters.matchText ?? '').trim().toLowerCase()
    const label = (data.label as string | null) ?? ''
    const matches = q ? label.toLowerCase().includes(q) : false

    if (id === this.selected || id === this.hover) {
      res.zIndex = 2
      res.forceLabel = true
      res.highlighted = true
    } else if (this.neighborhood) {
      if (this.neighborhood.has(id)) {
        res.zIndex = 1
        res.forceLabel = true
      } else {
        res.color = withAlpha(data.color as string, DIM)
        res.label = null
        res.zIndex = 0
      }
    }
    if (this.litByPredicate && !this.litByPredicate.has(id) && !this.neighborhood) {
      res.color = withAlpha(data.color as string, DIM)
      res.label = null
    }
    if (q) {
      if (matches) {
        res.forceLabel = true
        res.zIndex = 2
        res.highlighted = true
      } else if (!this.neighborhood) {
        res.color = withAlpha(data.color as string, DIM)
        res.label = null
      }
    }
    return res
  }

  private edgeReducer = (id: string, data: Record<string, unknown>): Partial<EdgeDisplayData> => {
    const res: Partial<EdgeDisplayData> = { ...data }
    const [src, tgt] = this.graph.extremities(id)
    const focus = this.hover ?? this.selected
    const hiddenKinds = this.filters.hiddenKinds
    if (hiddenKinds?.length) {
      const ks = this.graph.getNodeAttribute(src, 'kind') as Kind
      const kt = this.graph.getNodeAttribute(tgt, 'kind') as Kind
      if (hiddenKinds.includes(ks) || hiddenKinds.includes(kt)) {
        res.hidden = true
        return res
      }
    }
    if (focus) {
      if (src === focus || tgt === focus) {
        res.color = EDGE_HL
        res.size = (data.size as number) + 0.6
        res.zIndex = 1
      } else {
        res.color = withAlpha(EDGE_BASE, 0.05)
      }
      return res
    }
    if (this.filters.dimPredicate) {
      if ((data.predicate as string) === this.filters.dimPredicate) {
        res.color = EDGE_HL
        res.size = (data.size as number) + 0.6
      } else {
        res.color = withAlpha(EDGE_BASE, 0.05)
      }
    }
    return res
  }

  // --- Label-Rendering (Canvas-Layer von sigma) ---------------------------------

  private drawNodeLabel = (
    ctx: CanvasRenderingContext2D,
    data: Pick<NodeDisplayData, 'x' | 'y' | 'size' | 'color' | 'label'>
      & Partial<Pick<NodeDisplayData, 'highlighted'>>,
    settings: { labelFont: string; labelSize: number; labelWeight: string },
  ) => {
    if (!data.label) return
    const important = !!data.highlighted
    const size = important ? settings.labelSize + 1 : settings.labelSize
    ctx.font = `${important ? 600 : 500} ${size}px ${settings.labelFont}`
    const w = ctx.measureText(data.label).width + 8
    const x = data.x - w / 2 + 4
    const y = data.y + data.size + 4
    ctx.fillStyle = 'rgba(7, 11, 18, 0.75)'
    ctx.fillRect(x - 4, y, w, size + 6)
    ctx.fillStyle = important ? TEXT_BRIGHT : TEXT_DIM
    ctx.textAlign = 'center'
    ctx.textBaseline = 'top'
    ctx.fillText(data.label, data.x, y + 3)
  }

  // --- Overlay: Badges, Kanten-Chips, Ringe -------------------------------------

  private resizeOverlay() {
    const rect = this.container.getBoundingClientRect()
    const dpr = window.devicePixelRatio || 1
    this.overlay.width = rect.width * dpr
    this.overlay.height = rect.height * dpr
    this.overlay.style.width = `${rect.width}px`
    this.overlay.style.height = `${rect.height}px`
    this.octx.setTransform(dpr, 0, 0, dpr, 0, 0)
  }

  private drawOverlay = () => {
    const ctx = this.octx
    const rect = this.container.getBoundingClientRect()
    ctx.clearRect(0, 0, rect.width, rect.height)
    this.badgeRects = []
    const ratio = this.sigma.getCamera().ratio
    const focus = this.hover ?? this.selected

    // Ghost-Badges: LOD — weit rausgezoomt wären 3k Badges nur Rauschen.
    if (ratio < 1.6) {
      this.graph.forEachNode((id, a) => {
        if (a.hidden) return
        const kind = a.kind as Kind
        if (this.filters.hiddenKinds?.includes(kind)) return
        if (this.neighborhood && !this.neighborhood.has(id)) return
        const ghost = this.ghostCount(id)
        if (ghost <= 0) return
        const p = this.sigma.graphToViewport({ x: a.x as number, y: a.y as number })
        if (p.x < -40 || p.y < -40 || p.x > rect.width + 40 || p.y > rect.height + 40) return
        const size = (a.size as number) / Math.sqrt(ratio)
        const text = ghost > 99 ? '99+' : `+${ghost}`
        ctx.font = '500 10px "IBM Plex Mono", monospace'
        const w = ctx.measureText(text).width + 10
        const bx = p.x + size * 0.6 + 2
        const by = p.y - size * 0.6 - 16
        ctx.fillStyle = BADGE_BG
        ctx.strokeStyle = CHIP_BORDER
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.roundRect(bx, by, w, 15, 8)
        ctx.fill()
        ctx.stroke()
        ctx.fillStyle = TEXT_META
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillText(text, bx + w / 2, by + 8)
        // Hit-Area großzügiger als die Pille (R8).
        this.badgeRects.push({ x: bx - 4, y: by - 6, w: w + 8, h: 27, id })
      })
    }

    // Kantenlabel-Chips: nur am Fokus, horizontal, nie rotiert (R3).
    // Nicht während eines Drags — 60 mitziehende Chips sind nur Rauschen.
    if (focus && !this.dragged && this.graph.hasNode(focus)) {
      ctx.font = '500 10px "IBM Plex Mono", monospace'
      let shown = 0
      this.graph.forEachEdge(focus, (_edge, attrs, _src, _tgt, sa, ta) => {
        if (shown >= 60) return
        shown++
        const mx = (sa.x as number + (ta.x as number)) / 2
        const my = (sa.y as number + (ta.y as number)) / 2
        const p = this.sigma.graphToViewport({ x: mx, y: my })
        const label = attrs.predicate as string
        const w = ctx.measureText(label).width + 12
        ctx.fillStyle = CHIP_BG
        ctx.strokeStyle = CHIP_BORDER
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.roundRect(p.x - w / 2, p.y - 9, w, 17, 8)
        ctx.fill()
        ctx.stroke()
        ctx.fillStyle = TEXT_DIM
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillText(label, p.x, p.y)
      })
    }

    // Ringe: Auswahl (gestrichelt) + Hover (fein) + Match (gold).
    const ring = (id: string, style: string, dashed: boolean, pad: number) => {
      if (!this.graph.hasNode(id)) return
      const a = this.graph.getNodeAttributes(id)
      const p = this.sigma.graphToViewport({ x: a.x as number, y: a.y as number })
      const r = (a.size as number) / Math.sqrt(ratio) + pad
      ctx.strokeStyle = style
      ctx.lineWidth = dashed ? 2 : 1.5
      if (dashed) ctx.setLineDash([4, 5])
      ctx.beginPath()
      ctx.arc(p.x, p.y, r, 0, Math.PI * 2)
      ctx.stroke()
      ctx.setLineDash([])
    }
    const q = (this.filters.matchText ?? '').trim().toLowerCase()
    if (q) {
      this.graph.forEachNode((id, a) => {
        const label = ((a.label as string | null) ?? '').toLowerCase()
        if (label.includes(q)) ring(id, MATCH_GOLD, false, 5)
      })
    }
    if (this.selected) ring(this.selected, TEXT_BRIGHT, true, 7)
    if (this.hover && this.hover !== this.selected) ring(this.hover, 'rgba(234,240,250,.8)', false, 5)
  }

  // --- Interaktion ---------------------------------------------------------------

  /** Nächster Node im Umkreis — R8: Treffradius min. 20px. */
  private pick(vx: number, vy: number): string | null {
    let best: string | null = null
    let bd = Infinity
    const ratio = this.sigma.getCamera().ratio
    this.graph.forEachNode((id, a) => {
      if (a.hidden) return
      if (this.filters.hiddenKinds?.includes(a.kind as Kind)) return
      const p = this.sigma.graphToViewport({ x: a.x as number, y: a.y as number })
      const d = Math.hypot(p.x - vx, p.y - vy)
      const hit = Math.max(HIT_MIN, (a.size as number) / Math.sqrt(ratio) + 6)
      if (d < hit && d < bd) { bd = d; best = id }
    })
    return best
  }

  private pickBadge(vx: number, vy: number): string | null {
    for (const r of this.badgeRects) {
      if (vx >= r.x && vx <= r.x + r.w && vy >= r.y && vy <= r.y + r.h) return r.id
    }
    return null
  }

  private updateFocus() {
    const focus = this.hover ?? this.selected
    if (focus && this.graph.hasNode(focus)) {
      const hood = new Set<string>([focus])
      this.graph.forEachNeighbor(focus, (n) => hood.add(n))
      this.neighborhood = hood
    } else {
      this.neighborhood = null
    }
  }

  private refresh() {
    // Reducer-only-Refresh: partialGraph + skipIndexation schreibt die
    // WebGL-Buffer neu, ohne Quadtree/Indizes anzufassen. (Ohne partialGraph
    // rechnet sigma die Reducer, aktualisiert aber die Programme nie.)
    // Vor dem allerersten Render existieren die Program-Indizes noch nicht —
    // dann reicht der volle (sowieso anstehende) Refresh.
    try {
      this.sigma.refresh({
        partialGraph: { nodes: this.graph.nodes(), edges: this.graph.edges() },
        skipIndexation: true,
        schedule: true,
      })
    } catch {
      this.sigma.refresh({ schedule: true })
    }
  }

  private emitStats() {
    this.cb.onStats?.({ nodes: this.graph.order, edges: this.graph.size })
  }

  private wireEvents() {
    this.sigma.on('afterRender', this.drawOverlay)
    this.sigma.on('resize', () => this.resizeOverlay())

    // Node-Drag: Kanten bleiben sichtbar und ziehen in Echtzeit mit — sigma
    // zeichnet die Kantengeometrie jeden Frame aus den Node-Positionen.
    this.sigma.on('downNode', (e) => {
      this.dragged = e.node
      this.dragMoved = false
    })
    const mouse = this.sigma.getMouseCaptor()
    mouse.on('mousemovebody', (e) => {
      if (this.dragged) {
        this.dragMoved = true
        const pos = this.sigma.viewportToGraph(e)
        this.graph.setNodeAttribute(this.dragged, 'x', pos.x)
        this.graph.setNodeAttribute(this.dragged, 'y', pos.y)
        this.worker.postMessage(
          { type: 'drag', id: this.dragged, x: pos.x, y: pos.y } satisfies LayoutMsgIn)
        // Kamera-Pan unterbinden, solange ein Node gezogen wird.
        e.preventSigmaDefault()
        e.original.preventDefault()
        e.original.stopPropagation()
        return
      }
      // Hover mit Mindest-Treffradius (R8) — ersetzt sigmas size-gebundenes Hover.
      const hit = this.pickBadge(e.x, e.y) ? null : this.pick(e.x, e.y)
      const overBadge = !!this.pickBadge(e.x, e.y)
      this.container.style.cursor = hit || overBadge ? 'pointer' : ''
      if (hit !== this.hover) {
        this.hover = hit
        this.updateFocus()
        this.refresh()
      }
    })
    mouse.on('mouseup', () => {
      if (this.dragged) {
        if (this.dragMoved) {
          this.worker.postMessage({ type: 'dragEnd', id: this.dragged } satisfies LayoutMsgIn)
          this.touch(this.dragged)
        }
        this.dragged = null
      }
    })
    this.container.addEventListener('mouseleave', () => {
      if (this.hover) {
        this.hover = null
        this.updateFocus()
        this.refresh()
      }
    })

    // Klick-Logik zentral: Badge schlägt Node schlägt Fläche.
    this.sigma.on('clickStage', (e) => {
      if (this.dragMoved) { this.dragMoved = false; return }
      this.handleClick(e.event.x, e.event.y, null)
    })
    this.sigma.on('clickNode', (e) => {
      if (this.dragMoved) { this.dragMoved = false; return }
      this.handleClick(e.event.x, e.event.y, e.node)
    })
    this.sigma.on('doubleClickNode', (e) => {
      e.preventSigmaDefault()
      this.cb.onOpen?.(e.node)
    })
  }

  private handleClick(vx: number, vy: number, nativeNode: string | null) {
    const badge = this.pickBadge(vx, vy)
    if (badge) {
      this.touch(badge)
      this.cb.onExpand?.(badge)
      return
    }
    const node = nativeNode ?? this.pick(vx, vy)
    if (node) {
      this.select(node)
      this.cb.onSelect?.(node)
      if (this.ghostCount(node) > 0) this.cb.onExpand?.(node)
    } else {
      this.select(null)
      this.cb.onSelect?.(null)
    }
  }
}
