import { useEffect, useRef, useState, type KeyboardEvent } from 'react'

/**
 * Tastatur-Navigation für Autocomplete-Listen.
 * Tab / Pfeil-runter cyclen nach unten (mit Wraparound), Shift+Tab / Pfeil-hoch nach oben,
 * Enter wählt den hervorgehobenen Treffer, Escape schließt.
 * `active` ist der hervorgehobene Index, `listRef` an den Options-Container hängen.
 */
export function useOptionNav<T>(
  items: T[],
  onChoose: (item: T) => void,
  onEscape?: () => void,
) {
  const [active, setActive] = useState(0)
  const listRef = useRef<HTMLDivElement>(null)

  // Neue/veränderte Trefferliste → wieder oben anfangen.
  // ponytail: auf length statt Array-Referenz, sonst reset bei jedem Render (slice() gibt neue Referenz).
  useEffect(() => { setActive(0) }, [items.length])

  // Hervorgehobenen Eintrag in den sichtbaren Bereich scrollen.
  useEffect(() => {
    ;(listRef.current?.children[active] as HTMLElement | undefined)?.scrollIntoView({ block: 'nearest' })
  }, [active])

  const onKeyDown = (e: KeyboardEvent) => {
    if (items.length === 0) return
    const step = (delta: number) => {
      e.preventDefault()
      setActive((i) => (i + delta + items.length) % items.length)
    }
    switch (e.key) {
      case 'ArrowDown': return step(1)
      case 'ArrowUp': return step(-1)
      case 'Tab': return step(e.shiftKey ? -1 : 1)
      case 'Enter': {
        const item = items[active]
        if (item !== undefined) { e.preventDefault(); onChoose(item) }
        return
      }
      case 'Escape': return onEscape?.()
    }
  }

  return { active, listRef, onKeyDown }
}
