/* Dünner React-Wrapper um die framework-freie GraphEngine — React bleibt aus
   den Per-Frame-Pfaden raus. Die Pages liefern Skeleton-Daten + Filter und
   bekommen Callbacks; Expansion läuft imperativ über das Handle. */

import {
  forwardRef, useEffect, useImperativeHandle, useRef,
} from 'react'
import type { GraphEdgeDTO, GraphNodeDTO, Kind } from '../api/types'
import { GraphEngine } from './engine'

export interface GraphViewHandle {
  /** Zum Knoten schwenken + selektieren. false = nicht geladen. */
  focusOn: (id: string) => boolean
  fit: () => void
  relayout: () => void
  /** Expansion/Suchtreffer/Pfad einfügen (Fade-in nahe des Ankers). */
  addSubgraph: (nodes: GraphNodeDTO[], edges: GraphEdgeDTO[], anchorId?: string) => void
  /** Aktuell geladene Node-IDs (Ziel-Menge für Pfad-zum-Skeleton). */
  loadedIds: () => string[]
  ghostCount: (id: string) => number
  dbDegree: (id: string) => number | undefined
}

interface Props {
  nodes: GraphNodeDTO[]
  edges: GraphEdgeDTO[]
  kindOf: (typeId: string) => Kind | undefined
  hiddenKinds?: Kind[]
  dimPredicate?: string
  matchText?: string
  budget?: number
  onSelect?: (id: string | null) => void
  onOpen?: (id: string) => void
  onExpand?: (id: string) => void
  onSettled?: (positions: { id: string; x: number; y: number }[]) => void
  onStats?: (stats: { nodes: number; edges: number }) => void
}

export const GraphView = forwardRef<GraphViewHandle, Props>(function GraphView(
  { nodes, edges, kindOf, hiddenKinds, dimPredicate, matchText, budget,
    onSelect, onOpen, onExpand, onSettled, onStats },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const engineRef = useRef<GraphEngine | null>(null)

  // Callbacks über Refs — die Engine wird nie wegen einer neuen
  // Callback-Identität neu aufgebaut.
  const cbRef = useRef({ onSelect, onOpen, onExpand, onSettled, onStats })
  cbRef.current = { onSelect, onOpen, onExpand, onSettled, onStats }

  useEffect(() => {
    if (!containerRef.current) return
    const engine = new GraphEngine(
      containerRef.current,
      kindOf,
      {
        onSelect: (id) => cbRef.current.onSelect?.(id),
        onOpen: (id) => cbRef.current.onOpen?.(id),
        onExpand: (id) => cbRef.current.onExpand?.(id),
        onSettled: (p) => cbRef.current.onSettled?.(p),
        onStats: (s) => cbRef.current.onStats?.(s),
      },
      { budget },
    )
    engineRef.current = engine
    // Für die Playwright-Verifikations-Suite (Perf-Messung, Drag-Test) —
    // Single-User-App, kein Geheimnis auf window.
    ;(window as unknown as Record<string, unknown>).__graphEngine = engine
    return () => {
      engine.destroy()
      engineRef.current = null
    }
    // kindOf/budget sind pro Ansicht stabil; Neuaufbau nur beim Unmount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Grundgerüst (neu) setzen, wenn die Daten wechseln.
  useEffect(() => {
    engineRef.current?.setSkeleton(nodes, edges)
  }, [nodes, edges])

  useEffect(() => {
    engineRef.current?.setFilters({ hiddenKinds, dimPredicate, matchText })
  }, [hiddenKinds, dimPredicate, matchText])

  useImperativeHandle(ref, () => ({
    focusOn: (id) => engineRef.current?.focusOn(id) ?? false,
    fit: () => engineRef.current?.fit(),
    relayout: () => engineRef.current?.relayout(),
    addSubgraph: (n, e, anchor) => engineRef.current?.addSubgraph(n, e, anchor),
    loadedIds: () => engineRef.current?.loadedIds() ?? [],
    ghostCount: (id) => engineRef.current?.ghostCount(id) ?? 0,
    dbDegree: (id) => engineRef.current?.dbDegree(id),
  }), [])

  return <div ref={containerRef} className="graph-canvas graph-stage" />
})
