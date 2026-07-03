/** Parser für Instagram-Follower-/Following-Listen-Pastes.
 *
 * HTML (DevTools-Copy des Dialogs) ist der zuverlässige Pfad: Username steht
 * kanonisch im href, Display-Name in einem Span außerhalb von <a>/<button>.
 * Plain-Text (Select-All) ist Heuristik: Zeilen, die wie Usernames aussehen,
 * können auch lowercase Display-Names sein → solche Rows werden `ambiguous`
 * geflaggt und im Preview vom User entschieden.
 */

export interface ParsedRow {
  username: string
  displayName: string | null
  /** Plain-Text: könnte auch Display-Name der vorherigen Row sein. */
  ambiguous: boolean
}

export interface ParseResult {
  format: 'html' | 'text'
  rows: ParsedRow[]
  warnings: string[]
}

const USERNAME_RE = /^[a-z0-9._]{1,30}$/
const PROFILE_HREF_RE = /^\/([a-z0-9._]{1,30})\/$/

// Instagram-Routen, die wie Profil-Links aussehen, aber keine sind.
const RESERVED_PATHS = new Set([
  'explore', 'reels', 'direct', 'stories', 'accounts', 'p', 'tv',
  'about', 'legal', 'privacy', 'terms', 'help', 'developer', 'directory',
])

const HEADER_LINES = new Set(['search', 'suche', 'suchen'])

export function parseFollowerList(paste: string): ParseResult {
  const trimmed = paste.trim()
  if (!trimmed) throw new Error('Leerer Paste — nichts zu parsen.')
  return trimmed.includes('href=') ? parseHtml(trimmed) : parseText(trimmed)
}

// --- HTML-Pfad ---------------------------------------------------------------

function profileUsername(href: string | null): string | null {
  const match = href?.match(PROFILE_HREF_RE)
  if (!match || RESERVED_PATHS.has(match[1])) return null
  return match[1]
}

/** Größter Ancestor, dessen Profil-Links alle auf diesen Username zeigen —
 * das ist der Row-Container (Profilbild-Link + Namens-Link + Button). */
function rowContainer(anchor: Element, username: string): Element {
  let container: Element = anchor
  let el = anchor.parentElement
  while (el) {
    const usernames = Array.from(el.querySelectorAll('a[href]'))
      .map((a) => profileUsername(a.getAttribute('href')))
      .filter(Boolean)
    if (usernames.some((u) => u !== username)) break
    container = el
    el = el.parentElement
  }
  return container
}

/** Erster Textknoten im Container, der weder in <a> noch in <button> hängt —
 * so bleibt der Button-Text (Follow/Following/…) sprachunabhängig außen vor. */
function displayNameIn(container: Element): string | null {
  const walker = container.ownerDocument.createTreeWalker(container, NodeFilter.SHOW_TEXT)
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent?.trim()
    if (!text) continue
    let el = node.parentElement
    let excluded = false
    while (el && el !== container) {
      if (el.tagName === 'A' || el.tagName === 'BUTTON') { excluded = true; break }
      el = el.parentElement
    }
    if (!excluded) return text
  }
  return null
}

function parseHtml(paste: string): ParseResult {
  const doc = new DOMParser().parseFromString(paste, 'text/html')
  const allAnchors = Array.from(doc.querySelectorAll('a[href]'))
  const profileAnchors = allAnchors.filter((a) => profileUsername(a.getAttribute('href')))

  if (profileAnchors.length === 0) {
    throw new Error('Keine Profil-Links gefunden — ist das der Follower-Dialog?')
  }
  // Der Listen-Dialog besteht fast nur aus Profil-Links; eine ganze
  // Instagram-Seite hat Nav/Post/Footer-Links → Verhältnis kippt.
  if (profileAnchors.length / allAnchors.length < 0.5) {
    throw new Error(
      'Das sieht nach einer ganzen Seite aus, nicht nach dem Follower-Dialog — '
      + 'bitte nur das Listen-Element kopieren.',
    )
  }

  const rows: ParsedRow[] = []
  const seen = new Set<string>()
  for (const anchor of profileAnchors) {
    const username = profileUsername(anchor.getAttribute('href'))!
    if (seen.has(username)) continue // Profilbild- und Namens-Link derselben Row
    seen.add(username)
    const container = rowContainer(anchor, username)
    const displayName = displayNameIn(container)
    rows.push({
      username,
      displayName: displayName && displayName !== username ? displayName : null,
      ambiguous: false,
    })
  }
  return { format: 'html', rows, warnings: [] }
}

// --- Plain-Text-Pfad ---------------------------------------------------------

function parseText(paste: string): ParseResult {
  const lines = paste.split('\n').map((l) => l.trim()).filter(Boolean)
    .filter((l) => !HEADER_LINES.has(l.toLowerCase()))

  const rows: ParsedRow[] = []
  const warnings: string[] = []
  let dropped = 0
  for (const line of lines) {
    const prev = rows[rows.length - 1]
    if (USERNAME_RE.test(line)) {
      // Direkt nach einem Username ohne Display-Name ist eine Username-artige
      // Zeile ambig: nächster Account oder lowercase Display-Name?
      rows.push({
        username: line,
        displayName: null,
        ambiguous: prev !== undefined && prev.displayName === null,
      })
    } else if (prev && prev.displayName === null) {
      prev.displayName = line
    } else {
      dropped += 1
    }
  }

  if (rows.length === 0) throw new Error('Keine Usernames erkannt.')
  if (dropped > lines.length / 2) {
    throw new Error(
      'Mehr als die Hälfte der Zeilen sind keiner Row zuzuordnen — '
      + 'ist das wirklich eine Follower-Liste?',
    )
  }
  if (dropped > 0) warnings.push(`${dropped} Zeile(n) nicht zuordenbar, verworfen.`)
  return { format: 'text', rows, warnings }
}

/** Ambiguitäts-Korrektur im Preview: Row i war doch der Display-Name der
 * vorherigen Row → mergen, Row entfernen. Gibt ein neues Array zurück. */
export function mergeIntoPrevious(rows: ParsedRow[], index: number): ParsedRow[] {
  if (index <= 0 || index >= rows.length) return rows
  const next = rows.slice()
  const [row] = next.splice(index, 1)
  next[index - 1] = { ...next[index - 1], displayName: row.username }
  return next
}
