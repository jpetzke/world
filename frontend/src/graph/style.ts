import cytoscape from 'cytoscape'
// @ts-expect-error – cytoscape-d3-force ships no types
import d3Force from 'cytoscape-d3-force'
import type { Kind } from '../api/types'

cytoscape.use(d3Force)

/* Kosmograph-Palette — muss mit den Tokens in theme.css übereinstimmen
   (Cytoscape braucht konkrete Farbwerte, keine CSS-Variablen). */
export const NODE_COLORS: Record<Kind, string> = {
  continuant: '#56b8ff',
  occurrent: '#ffa044',
}
const EDGE = '#3a4f78'
const EDGE_HL = '#7aa2d8'
const TEXT_DIM = '#98a2ba'
const TEXT_BRIGHT = '#ecf0fa'
const GOLD = '#ffd166'

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
    // Kantenlänge = beide Radien + fester Luftspalt. Fixe 80px zogen große Hubs
    // ineinander (Summe der Radien > 80) — jetzt bleibt immer Abstand.
    linkDistance: (d: { source: { size?: number }; target: { size?: number } }) =>
      (d.source.size ?? 20) / 2 + (d.target.size ?? 20) / 2 + 55,
    linkStrength: 0.25,
    manyBodyStrength: nodeCount > 1500 ? -140 : -320,
    // Kollisions-Radius = Knoten-Radius + Rand; strength 1 + 2 Iterationen
    // trennen hart, damit nichts aneinander klebt (auch nicht unter Kantenzug).
    collideRadius: (d: { size?: number }) => (d.size ?? 20) / 2 + 14,
    collideStrength: 1,
    collideIterations: 2,
    velocityDecay: 0.5,
    // Zügig auskühlen → in ~1–2 s in Ruhe (d3 stoppt bei alphaMin, 0 CPU).
    // Höher, weil der Grid-Seed schon nah am Ziel startet (weniger Ticks nötig).
    alphaDecay: 0.1,
    alphaMin: 0.05,
  }) as unknown as cytoscape.LayoutOptions

/** Gemeinsamer Look für alle Graph-Ansichten (Nachtarchiv).
    Kanten-Labels sind per Default aus (LOD/Performance) und erscheinen nur an
    hervorgehobenen Kanten — sonst kostet Autorotate-Text bei 1000+ Kanten. */
export const GRAPH_STYLE: cytoscape.StylesheetJson = [
  {
    selector: 'node',
    style: {
      label: 'data(label)',
      color: TEXT_DIM,
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
      'line-color': EDGE,
      'target-arrow-color': EDGE,
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
    style: { 'border-width': 3, 'border-color': GOLD },
  },
  // Fokus + Kontext: Nachbarschaft leuchtet, der Rest tritt zurück.
  {
    selector: '.faded',
    style: { opacity: 0.08, 'text-opacity': 0, 'underlay-opacity': 0 },
  },
  {
    selector: 'node.hl-node',
    style: { 'border-width': 2, 'border-color': TEXT_BRIGHT },
  },
  {
    selector: 'edge.hl-edge',
    style: {
      opacity: 1,
      'line-color': EDGE_HL,
      'target-arrow-color': EDGE_HL,
      width: 2,
      label: 'data(label)',
      color: '#c9d6ee',
      'font-size': 9,
      'font-family': 'IBM Plex Mono, monospace',
      'min-zoomed-font-size': 6,
      'text-rotation': 'autorotate',
      'text-background-color': '#070b14',
      'text-background-opacity': 0.85,
      'text-background-padding': '2px',
    },
  },
  // Während des Erst-Setzens ausgeblendet: 1000+ Bézier-Kanten pro Tick zu
  // zeichnen drückt die FPS; Knoten-only setzt sich flüssig, dann schnappen
  // die Kanten ein. (Beim Ziehen bleiben Kanten sichtbar — das ist das Lebendige.)
  {
    selector: 'edge.settling',
    style: { display: 'none' },
  },
  // Suchtreffer: goldener Ring, auch wenn nicht im Fokus.
  {
    selector: 'node.match',
    style: { 'border-width': 3, 'border-color': GOLD },
  },
  {
    selector: 'node:selected',
    style: { 'border-width': 3, 'border-color': TEXT_BRIGHT },
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
