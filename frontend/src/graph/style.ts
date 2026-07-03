import cytoscape from 'cytoscape'
// @ts-expect-error – cytoscape-fcose ships no types
import fcose from 'cytoscape-fcose'
import type { Kind } from '../api/types'

cytoscape.use(fcose)

export const NODE_COLORS: Record<Kind, string> = {
  continuant: '#5fb0d4',
  occurrent: '#e08e39',
}

export const kindColor = (kind: Kind | undefined) => NODE_COLORS[kind ?? 'continuant']
export const kindShape = (kind: Kind | undefined) =>
  kind === 'occurrent' ? 'diamond' : 'ellipse'

/** fCoSE: force-directed, aber ~10× schneller als 'cose' und stabiler bei Hubs.
    Ein Aufruf reicht für 100–2000 Knoten ohne den Main-Thread zu blockieren. */
export const graphLayout = (nodeCount: number): cytoscape.LayoutOptions =>
  ({
    name: 'fcose',
    quality: nodeCount > 500 ? 'default' : 'proof',
    animate: false,
    randomize: true,
    packComponents: true,
    nodeRepulsion: () => 9000,
    idealEdgeLength: () => 90,
    nodeSeparation: 90,
    gravity: 0.25,
    numIter: nodeCount > 800 ? 1500 : 2500,
    padding: 40,
  }) as cytoscape.LayoutOptions

/** Gemeinsamer Look für alle Graph-Ansichten (Nachtarchiv).
    Kanten-Labels sind per Default aus (LOD/Performance) und erscheinen nur an
    hervorgehobenen Kanten — sonst kostet Autorotate-Text bei 1000+ Kanten. */
export const GRAPH_STYLE: cytoscape.StylesheetJson = [
  {
    selector: 'node',
    style: {
      label: 'data(label)',
      color: '#9aa1b0',
      'font-size': 10,
      'font-family': 'IBM Plex Mono, monospace',
      // LOD: Labels verschwinden automatisch, wenn zu weit rausgezoomt.
      'min-zoomed-font-size': 7,
      'text-valign': 'bottom',
      'text-margin-y': 6,
      'text-wrap': 'ellipsis',
      'text-max-width': '140px',
      width: 'data(size)',
      height: 'data(size)',
      'transition-property': 'opacity, border-width',
      'transition-duration': 120,
    },
  },
  {
    selector: 'edge',
    style: {
      'curve-style': 'bezier',
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.8,
      'line-color': '#33405a',
      'target-arrow-color': '#33405a',
      width: 1.5,
      // Konfidenz → Deckkraft (per mapData, damit .faded es überschreiben kann).
      // ponytail: mapData ist ein Cytoscape-Feature, das die Typen nicht kennen.
      opacity: 'mapData(confidence, 0, 1, 0.4, 0.9)' as unknown as number,
      'transition-property': 'opacity, line-color, width',
      'transition-duration': 120,
    },
  },
  // Startknoten der Ego-Sicht: klar als Anker erkennbar.
  {
    selector: 'node.start-node',
    style: { 'border-width': 3, 'border-color': '#d4b35b' },
  },
  // Fokus + Kontext: Nachbarschaft leuchtet, der Rest tritt zurück.
  {
    selector: '.faded',
    style: { opacity: 0.08, 'text-opacity': 0 },
  },
  {
    selector: 'node.hl-node',
    style: { 'border-width': 2, 'border-color': '#e9e4d6' },
  },
  {
    selector: 'edge.hl-edge',
    style: {
      opacity: 1,
      'line-color': '#7d8aa8',
      'target-arrow-color': '#7d8aa8',
      width: 2,
      label: 'data(label)',
      color: '#c7cede',
      'font-size': 9,
      'font-family': 'IBM Plex Mono, monospace',
      'min-zoomed-font-size': 6,
      'text-rotation': 'autorotate',
      'text-background-color': '#10141d',
      'text-background-opacity': 0.85,
      'text-background-padding': '2px',
    },
  },
  // Suchtreffer: goldener Ring, auch wenn nicht im Fokus.
  {
    selector: 'node.match',
    style: { 'border-width': 3, 'border-color': '#d4b35b' },
  },
  {
    selector: 'node:selected',
    style: { 'border-width': 3, 'border-color': '#e9e4d6' },
  },
]

export const GRAPH_OPTIONS = {
  wheelSensitivity: 0.3,
  maxZoom: 2,
  minZoom: 0.05,
  // Bei vielen Kanten: kein Textur-Redraw während Pan/Zoom → flüssig.
  textureOnViewport: true,
  pixelRatio: 1,
  hideEdgesOnViewport: true,
} as const
