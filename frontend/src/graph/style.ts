import cytoscape from 'cytoscape'
// @ts-expect-error – cytoscape-d3-force ships no types
import d3Force from 'cytoscape-d3-force'
import type { Kind } from '../api/types'

cytoscape.use(d3Force)

export const NODE_COLORS: Record<Kind, string> = {
  continuant: '#5fb0d4',
  occurrent: '#e08e39',
}

export const kindColor = (kind: Kind | undefined) => NODE_COLORS[kind ?? 'continuant']
export const kindShape = (kind: Kind | undefined) =>
  kind === 'occurrent' ? 'diamond' : 'ellipse'

/** Lebendige Physik statt Einmal-Layout: d3-force läuft kontinuierlich, stößt
    Knoten ab (manyBody) und lässt sie nie überlappen (collide = Radius+Rand).
    infinite:true hält die Simulation drag-reaktiv — beim Ziehen heizt sie auf
    und die Nachbarn weichen aus; im Ruhezustand stoppt d3 den eigenen Timer
    (alphaMin), also 0 CPU, wenn nichts passiert. Kein Spektral-Freeze wie fcose.
    Bei mehr Knoten setzt sie sich schneller (höheres alphaDecay) → bleibt flott. */
export const graphLayout = (nodeCount: number): cytoscape.LayoutOptions =>
  ({
    name: 'd3-force',
    animate: true,
    infinite: true,
    // Startpositionen kommen aus dem Seed-Layout (GraphCanvas) — so muss die
    // Simulation nur nachjustieren statt aus dem Chaos zu kühlen (schnell + ruckelfrei).
    randomize: false,
    fixedAfterDragging: false,
    linkId: (d: { id: string }) => d.id,
    linkDistance: 80,
    linkStrength: 0.4,
    manyBodyStrength: nodeCount > 1500 ? -80 : -160,
    collideRadius: (d: { size?: number }) => (d.size ?? 20) / 2 + 7,
    collideStrength: 0.9,
    velocityDecay: 0.5,
    // Zügig auskühlen → in ~1–2 s in Ruhe (d3 stoppt bei alphaMin, 0 CPU).
    alphaDecay: 0.06,
    alphaMin: 0.02,
  }) as unknown as cytoscape.LayoutOptions

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
  pixelRatio: 1,
  // Kanten während Pan/Zoom ausblenden + Textur-Snapshot: hält den
  // Canvas-Renderer flüssig, wo er sonst an Kanten+Labels erstickt.
  hideEdgesOnViewport: true,
  textureOnViewport: true,
} as const
