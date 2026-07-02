import type cytoscape from 'cytoscape'
import type { Kind } from '../api/types'

export const NODE_COLORS: Record<Kind, string> = {
  continuant: '#5fb0d4',
  occurrent: '#e08e39',
}

export const kindColor = (kind: Kind | undefined) => NODE_COLORS[kind ?? 'continuant']
export const kindShape = (kind: Kind | undefined) =>
  kind === 'occurrent' ? 'diamond' : 'ellipse'

/** Gemeinsamer Look für alle Graph-Ansichten (Nachtarchiv). */
export const GRAPH_STYLE: cytoscape.StylesheetJson = [
  {
    selector: 'node',
    style: {
      label: 'data(label)',
      color: '#9aa1b0',
      'font-size': 10,
      'font-family': 'IBM Plex Mono, monospace',
      'text-valign': 'bottom',
      'text-margin-y': 6,
      'text-wrap': 'ellipsis',
      'text-max-width': '140px',
      width: 'data(size)',
      height: 'data(size)',
    },
  },
  {
    selector: 'edge',
    style: {
      label: 'data(label)',
      color: '#667082',
      'font-size': 8,
      'font-family': 'IBM Plex Mono, monospace',
      'curve-style': 'bezier',
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.8,
      'line-color': '#33405a',
      'target-arrow-color': '#33405a',
      width: 1.5,
      'text-rotation': 'autorotate',
    },
  },
  {
    selector: 'node:selected',
    style: { 'border-width': 3, 'border-color': '#e9e4d6' },
  },
  {
    selector: '.dimmed',
    style: { opacity: 0.15 },
  },
  {
    selector: '.spotlight',
    style: { 'border-width': 4, 'border-color': '#d4b35b' },
  },
]

export const GRAPH_OPTIONS = {
  wheelSensitivity: 0.3,
  maxZoom: 1.5,
  minZoom: 0.1,
} as const
