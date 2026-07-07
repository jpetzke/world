import type { Kind } from '../api/types'

/* Orbital-Palette (Spec Tab 02) — muss mit den Tokens in theme.css
   übereinstimmen; WebGL braucht konkrete Werte, keine CSS-Variablen.
   R5: Farbe kodiert Typ (6 Hues), Form kodiert Kategorie (Kreis/Raute). */

export const CONT = '#3EC5F0'
export const OCC = '#FFB454'

/** Typ-Hue je Registry-Typ; Occurrents sind immer Amber (Form trägt die
    Kategorie, der Hue bleibt der Ereignis-Farbe vorbehalten). */
const TYPE_HUES: Record<string, string> = {
  Person: '#3EC5F0',
  Organization: '#8B8FF8',
  Unternehmen: '#8B8FF8',
  Ort: '#4ADE80',
  Wertpapier: '#5E7CE2',
  Platform: '#C778DD',
  SocialMediaAccount: '#C778DD',
  Post: '#C778DD',
  Projekt: '#2DD4BF',
}

export function nodeColor(typeId: string, kind: Kind | undefined): string {
  if (kind === 'occurrent') return OCC
  return TYPE_HUES[typeId] ?? CONT
}

/** Grad → Größe (px): Hubs fallen auf, sqrt dämpft Ausreißer. */
export function nodeSize(degree: number): number {
  return Math.min(22, 5 + Math.sqrt(Math.max(0, degree)) * 1.6)
}

/** Deckkraft in eine Hex-Farbe einbrennen — PREMULTIPLIED.
    sigma blendet mit (ONE, ONE_MINUS_SRC_ALPHA), die Shader multiplizieren
    RGB aber nicht mit Alpha: nacktes #rrggbbaa wirkt dort additiv statt
    transparent. Darum RGB hier selbst mit Alpha skalieren. */
export function withAlpha(hex: string, alpha: number): string {
  const r = Math.round(parseInt(hex.slice(1, 3), 16) * alpha)
  const g = Math.round(parseInt(hex.slice(3, 5), 16) * alpha)
  const b = Math.round(parseInt(hex.slice(5, 7), 16) * alpha)
  const a = Math.round(alpha * 255)
  const h = (v: number) => v.toString(16).padStart(2, '0')
  return `#${h(r)}${h(g)}${h(b)}${h(a)}`
}

/* Kanten: leiser als Knoten. */
export const EDGE_BASE = '#33517d'
export const EDGE_HL = '#7AA2D8'
export const DIM = 0.12 // Fokus-Dimming (R6)

export const TEXT_BRIGHT = '#EAF0FA'
export const TEXT_DIM = '#A6B2C8'
export const TEXT_META = '#76839C'
export const CHIP_BG = 'rgba(10, 15, 25, 0.9)'
export const CHIP_BORDER = 'rgba(45, 61, 96, 0.8)'
export const BADGE_BG = 'rgba(12, 18, 29, 0.92)'
export const MATCH_GOLD = '#FFD166'
