import cytoscape from 'cytoscape'
import {
  forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef,
} from 'react'
import type { Kind } from '../api/types'
import { GRAPH_OPTIONS, GRAPH_STYLE, graphLayout, kindColor, kindShape } from './style'

export interface GraphNode {
  id: string
  type_id: string
  label: string | null
  degree: number
  depth?: number
}
export interface GraphEdge {
  id: string
  subject_id: string
  object_id: string
  predicate_id: string
  confidence: number
}

export interface GraphCanvasHandle {
  /** Zum Knoten schwenken + fokussieren. false = nicht im aktuellen Ausschnitt. */
  focusOn: (id: string) => boolean
  fit: () => void
}

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  kindOf: (typeId: string) => Kind | undefined
  /** Ego-Sicht: dieser Knoten wird als Anker markiert. */
  startId?: string
  hiddenKinds?: Kind[]
  dimPredicate?: string
  /** Live-Filter: passende Labels leuchten, der Rest tritt zurück. */
  matchText?: string
  onSelect?: (id: string | null) => void
  onOpen?: (id: string) => void
}

/** Eine Graph-Engine für alle Ansichten: fCoSE-Layout, Label-LOD und
    Fokus+Kontext (Hover/Klick hebt die Nachbarschaft hervor, dimmt den Rest).
    Die Pages liefern nur Daten + Toolbar/Seitenpanel drumherum. */
export const GraphCanvas = forwardRef<GraphCanvasHandle, Props>(function GraphCanvas(
  { nodes, edges, kindOf, startId, hiddenKinds, dimPredicate, matchText, onSelect, onOpen },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<cytoscape.Core | null>(null)
  const hoverRef = useRef<string | null>(null)
  const clickRef = useRef<string | null>(null)

  // Aktuelle Werte für paint() ohne cy neu aufzubauen (Refs statt Deps).
  const filters = useRef({ startId, hiddenKinds, dimPredicate, matchText })
  filters.current = { startId, hiddenKinds, dimPredicate, matchText }
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect
  const onOpenRef = useRef(onOpen)
  onOpenRef.current = onOpen

  const elements = useMemo<cytoscape.ElementDefinition[]>(
    () => [
      ...nodes.map((n) => {
        const kind = kindOf(n.type_id) ?? 'continuant'
        return {
          data: {
            id: n.id,
            label: n.label ?? n.id.slice(0, 8),
            // Grad → Größe: Hubs fallen auf, sqrt dämpft Ausreißer.
            size: 18 + Math.round(Math.sqrt(n.degree) * 8),
            kind,
          },
          style: { 'background-color': kindColor(kind), shape: kindShape(kind) },
        }
      }),
      ...edges.map((e) => ({
        data: {
          id: e.id,
          source: e.subject_id,
          target: e.object_id,
          label: e.predicate_id,
          confidence: e.confidence,
        },
      })),
    ],
    [nodes, edges, kindOf],
  )

  // Fokus + Kontext + Filter aus einer Hand: eine Quelle der Wahrheit.
  const paint = useCallback(() => {
    const cy = cyRef.current
    if (!cy) return
    const f = filters.current
    const hidden = f.hiddenKinds ?? []
    const q = (f.matchText ?? '').trim().toLowerCase()
    cy.batch(() => {
      cy.elements().removeClass('faded hl-node hl-edge match start-node')
      if (f.startId) cy.$id(f.startId).addClass('start-node')

      // 1) Einzelfokus (Hover/Klick) schlägt alles andere.
      const focus = hoverRef.current ?? clickRef.current
      const node = focus ? cy.$id(focus) : null
      if (node && !node.empty()) {
        const keep = node.closedNeighborhood()
        cy.elements().not(keep).addClass('faded')
        keep.nodes().addClass('hl-node')
        node.connectedEdges().addClass('hl-edge')
        return
      }

      // 2) Filtermodus: Kinds ausblenden + Live-Suche.
      let keepNodes = cy.nodes()
      let filtering = false
      if (hidden.length) {
        keepNodes = keepNodes.filter((n) => !hidden.includes(n.data('kind')))
        filtering = true
      }
      if (q) {
        keepNodes = keepNodes.filter((n) => (n.data('label') || '').toLowerCase().includes(q))
        keepNodes.addClass('match')
        filtering = true
      }
      if (filtering) {
        cy.nodes().not(keepNodes).addClass('faded')
        cy.edges().forEach((e) => {
          if (e.source().hasClass('faded') || e.target().hasClass('faded')) e.addClass('faded')
        })
      }

      // 3) Prädikat hervorheben (dimmen statt verstecken — die Welt bleibt da).
      if (f.dimPredicate) {
        const lit = cy.edges().filter((e) => e.data('label') === f.dimPredicate)
        cy.edges().not(lit).addClass('faded')
        lit.removeClass('faded').addClass('hl-edge')
        cy.nodes().not(lit.connectedNodes()).addClass('faded')
      }
    })
  }, [])

  // cy nur neu aufbauen, wenn sich die Daten ändern.
  useEffect(() => {
    if (!containerRef.current) return
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: GRAPH_STYLE,
      ...GRAPH_OPTIONS,
    })
    cyRef.current = cy
    cy.on('mouseover', 'node', (e) => { hoverRef.current = e.target.id(); paint() })
    cy.on('mouseout', 'node', () => { hoverRef.current = null; paint() })
    cy.on('tap', 'node', (e) => {
      clickRef.current = e.target.id()
      onSelectRef.current?.(e.target.id())
      paint()
    })
    cy.on('tap', (e) => {
      if (e.target === cy) {
        clickRef.current = null
        onSelectRef.current?.(null)
        paint()
      }
    })
    cy.on('dbltap', 'node', (e) => onOpenRef.current?.(e.target.id()))

    // Seed: gefülltes Raster (überlappungsfrei) startet die Physik schon nah
    // am Endzustand (gepackte Scheibe) → kaum Nachjustieren, kurzes Setzen.
    // Ein Kreis-Seed wäre der schlechteste Start (alles am Rand → langes Zappeln).
    cy.layout({ name: 'grid', avoidOverlap: true, condense: true, animate: false }).run()
    // Kanten fürs Setzen ausblenden → flüssige Knoten-only-Ticks.
    cy.edges().addClass('settling')
    // Live-Physik: läuft animiert vom Seed ins Gleichgewicht, bleibt drag-
    // reaktiv (infinite) und stoppt in Ruhe bei alphaMin → 0 CPU.
    const layout = cy.layout(graphLayout(nodes.length))
    layout.run()
    // Nach dem Setzen: Kanten einschnappen + einpassen (Dauer skaliert mit N).
    const settleMs = Math.min(2600, 900 + nodes.length * 0.7)
    const settleTimer = setTimeout(() => {
      cyRef.current?.edges().removeClass('settling')
      cyRef.current?.fit(undefined, 40)
    }, settleMs)
    paint()
    return () => {
      clearTimeout(settleTimer)
      layout.stop()
      cy.destroy()
      cyRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [elements, paint])

  // Filter/Anker-Props ändern → nur neu einfärben (primitive Deps).
  useEffect(() => {
    paint()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startId, hiddenKinds?.join(','), dimPredicate, matchText, paint])

  useImperativeHandle(ref, () => ({
    focusOn(id) {
      const cy = cyRef.current
      if (!cy) return false
      const node = cy.$id(id)
      if (node.empty()) return false
      clickRef.current = id
      onSelectRef.current?.(id)
      cy.animate({ center: { eles: node }, zoom: 1.1, duration: 350 })
      paint()
      return true
    },
    fit() { cyRef.current?.fit(undefined, 40) },
  }), [paint])

  return <div ref={containerRef} className="graph-canvas" />
})
