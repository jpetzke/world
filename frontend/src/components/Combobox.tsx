import { useState } from 'react'
import { useOptionNav } from './useOptionNav'

interface Option { id: string; label: string }

/**
 * Durchsuchbares Dropdown: tippen filtert, Tab/Pfeile cyclen von oben nach unten,
 * Enter wählt, Escape schließt. Ersatz für native <select>, wo Tastatur-Auswahl
 * wie bei der Entity-Suche gewünscht ist.
 */
export function Combobox({ options, value, onChange, placeholder = '— wählen —' }: {
  options: Option[]
  value: string
  onChange: (id: string) => void
  placeholder?: string
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const selected = options.find((o) => o.id === value) ?? null

  const q = query.trim().toLowerCase()
  const filtered = q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options

  const choose = (o: Option) => { onChange(o.id); setQuery(''); setOpen(false) }
  const { active, listRef, onKeyDown } = useOptionNav(filtered, choose, () => setOpen(false))

  return (
    <div className="autocomplete">
      <input
        value={open ? query : (selected?.label ?? '')}
        placeholder={placeholder}
        onChange={(e) => { setQuery(e.target.value); setOpen(true) }}
        onFocus={() => { setQuery(''); setOpen(true) }}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        onKeyDown={open ? onKeyDown : undefined}
        role="combobox"
        aria-expanded={open && filtered.length > 0}
      />
      {open && filtered.length > 0 && (
        <div className="options" role="listbox" ref={listRef}>
          {filtered.map((o, i) => (
            <button key={o.id} type="button" role="option" aria-selected={i === active}
              className={i === active ? 'active' : undefined}
              onMouseDown={(e) => e.preventDefault()} onClick={() => choose(o)}>
              {o.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
