import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { SearchHit } from '../api/types'
import { SimilarityBar } from './bits'

interface Props {
  placeholder?: string
  typeId?: string
  onSelect: (hit: SearchHit | null) => void
  selected?: SearchHit | null
}

/** Entity-Suche (semantisch + Label) mit Dropdown. */
export function EntityAutocomplete({ placeholder, typeId, onSelect, selected }: Props) {
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<SearchHit[]>([])
  const [open, setOpen] = useState(false)
  const timer = useRef<number>(undefined)

  useEffect(() => {
    window.clearTimeout(timer.current)
    if (query.trim().length < 2) {
      setHits([])
      return
    }
    timer.current = window.setTimeout(() => {
      api.search(query, typeId).then((result) => {
        setHits(result)
        setOpen(true)
      }).catch(() => setHits([]))
    }, 220)
    return () => window.clearTimeout(timer.current)
  }, [query, typeId])

  if (selected) {
    return (
      <div className="inline">
        <span className="chip">{selected.type_id}</span>
        <strong>{selected.label ?? selected.id.slice(0, 8)}</strong>
        <button type="button" className="ghost" onClick={() => { onSelect(null); setQuery('') }}>
          ändern
        </button>
      </div>
    )
  }

  return (
    <div className="autocomplete">
      <input
        value={query}
        placeholder={placeholder ?? 'Entity suchen …'}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => hits.length && setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
      />
      {open && hits.length > 0 && (
        <div className="options">
          {hits.map((hit) => (
            <button key={hit.id} type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => { onSelect(hit); setOpen(false) }}>
              <span className="chip">{hit.type_id}</span>
              <span style={{ flex: 1 }}>{hit.label ?? hit.id.slice(0, 8)}</span>
              <SimilarityBar value={hit.similarity} />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
