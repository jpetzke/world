// Kleine Inline-SVG-Icons — stroke folgt currentColor, Größe = 1em.
// Keine Emoji, keine Text-Glyphen im UI.

export function Plus() {
  return <svg className="icon" viewBox="0 0 16 16" aria-hidden><path d="M8 3.5v9M3.5 8h9" /></svg>
}

export function Close() {
  return <svg className="icon" viewBox="0 0 16 16" aria-hidden><path d="M4 4l8 8M12 4l-8 8" /></svg>
}

export function ArrowLeft() {
  return <svg className="icon" viewBox="0 0 16 16" aria-hidden><path d="M13 8H4M7.5 4L4 8l3.5 4" /></svg>
}

export function ChevronRight() {
  return <svg className="icon" viewBox="0 0 16 16" aria-hidden><path d="M6 4l4 4-4 4" /></svg>
}

export function ChevronDown() {
  return <svg className="icon" viewBox="0 0 16 16" aria-hidden><path d="M4 6l4 4 4-4" /></svg>
}
