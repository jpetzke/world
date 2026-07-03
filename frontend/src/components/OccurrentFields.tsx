import { useMemo, useState } from 'react'
import type { Predicate, SearchHit, ValuePayload } from '../api/types'
import { EntityAutocomplete } from './EntityAutocomplete'
import { Field } from './bits'
import { Close } from './icons'
import type { VocabHelpers } from '../hooks/useVocabulary'
import { useVocabulary } from '../hooks/useVocabulary'

/** Minimaler Fakt, wie ihn die Wann/Wo/Wer-Felder liefern (CreatePage ergänzt
 * rank/confidence/…-Defaults beim Schreiben). */
export interface OccurrentFact {
  predicate_id: string
  value: ValuePayload
  display: string
}

export type FieldGroup = 'Wann' | 'Wo' | 'Wer' | 'Details'

/** Registry-getriebene Gruppierung: neue Ereignis-Typen aus dem Gate bekommen
 * ihr Formular automatisch. geo/json/number bleiben dem FactComposer. */
export function groupOf(p: Predicate, helpers: VocabHelpers): FieldGroup | null {
  if (p.range_kind === 'datetime') return 'Wann'
  if (p.range_kind === 'entity' && p.range_type) {
    return helpers.ancestors(p.range_type).includes('Ort') ? 'Wo' : 'Wer'
  }
  if (p.range_kind === 'string') return 'Details'
  return null
}

const GROUPS: FieldGroup[] = ['Wann', 'Wo', 'Wer', 'Details']

export function OccurrentFields({ typeId, exclude, onChange }: {
  typeId: string
  /** Prädikat des Primärfelds (label_predicate) — nicht doppelt anbieten. */
  exclude?: string
  onChange: (facts: OccurrentFact[]) => void
}) {
  const { helpers } = useVocabulary()
  // Texteingaben (Wann/Details) und Entity-Auswahlen (Wo/Wer) je Prädikat.
  const [texts, setTexts] = useState<Record<string, string>>({})
  const [picks, setPicks] = useState<Record<string, SearchHit[]>>({})

  const grouped = useMemo(() => {
    if (!helpers) return new Map<FieldGroup, Predicate[]>()
    // Nur Prädikate des eigenen Typ-Asts (domain_type in der Ahnenkette) —
    // Interface-Prädikate (name/alias) und domainlose (since) bleiben draußen.
    const chain = helpers.ancestors(typeId)
    const preds = helpers.predicatesFor(typeId).filter(
      (p) => p.id !== exclude && p.domain_type && chain.includes(p.domain_type),
    )
    const map = new Map<FieldGroup, Predicate[]>()
    for (const p of preds) {
      const group = groupOf(p, helpers)
      if (group) map.set(group, [...(map.get(group) ?? []), p])
    }
    return map
  }, [helpers, typeId, exclude])

  const emit = (nextTexts: Record<string, string>, nextPicks: Record<string, SearchHit[]>) => {
    const facts: OccurrentFact[] = []
    for (const preds of grouped.values()) {
      for (const p of preds) {
        const text = nextTexts[p.id]?.trim()
        if (text) {
          facts.push(p.range_kind === 'datetime'
            ? { predicate_id: p.id, value: { type: 'datetime', datetime: text }, display: text }
            : { predicate_id: p.id, value: { type: 'string', text }, display: text })
        }
        for (const hit of nextPicks[p.id] ?? []) {
          facts.push({
            predicate_id: p.id,
            value: { type: 'entity', object_id: hit.id },
            display: hit.label ?? hit.id.slice(0, 8),
          })
        }
      }
    }
    onChange(facts)
  }

  const setText = (predId: string, value: string) => {
    const next = { ...texts, [predId]: value }
    setTexts(next)
    emit(next, picks)
  }
  const addPick = (predId: string, hit: SearchHit) => {
    const next = { ...picks, [predId]: [...(picks[predId] ?? []), hit] }
    setPicks(next)
    emit(texts, next)
  }
  const removePick = (predId: string, index: number) => {
    const next = { ...picks, [predId]: (picks[predId] ?? []).filter((_, i) => i !== index) }
    setPicks(next)
    emit(texts, next)
  }

  if (grouped.size === 0) return null

  return (
    <div className="panel occurrent-fields">
      {GROUPS.filter((g) => grouped.has(g)).map((group) => (
        <div key={group} className="occurrent-group">
          <span className="field-label">{group}</span>
          <div className="row">
            {grouped.get(group)!.map((p) => {
              if (p.range_kind === 'datetime') {
                return (
                  <Field key={p.id} label={p.label}>
                    <input type="datetime-local" value={texts[p.id] ?? ''}
                      onChange={(e) => setText(p.id, e.target.value)} />
                  </Field>
                )
              }
              if (p.range_kind === 'string') {
                return (
                  <Field key={p.id} label={p.label}>
                    <input value={texts[p.id] ?? ''} placeholder={p.label}
                      onChange={(e) => setText(p.id, e.target.value)} />
                  </Field>
                )
              }
              // entity: 1:1 → eine Auswahl, n:m → Chips + weiter suchen
              const selected = picks[p.id] ?? []
              const multi = p.cardinality === 'n:m'
              return (
                <Field key={p.id} label={`${p.label} → ${p.range_type}`}>
                  {multi && selected.length > 0 && (
                    <div className="inline" style={{ marginBottom: 6 }}>
                      {selected.map((hit, i) => (
                        <span key={hit.id} className="chip">
                          {hit.label ?? hit.id.slice(0, 8)}
                          <button type="button" className="ghost icon-btn sm"
                            aria-label={`${hit.label ?? hit.id} entfernen`}
                            onClick={() => removePick(p.id, i)}><Close /></button>
                        </span>
                      ))}
                    </div>
                  )}
                  {(multi || selected.length === 0) ? (
                    <EntityAutocomplete
                      key={selected.length /* Eingabe nach jeder Auswahl leeren */}
                      typeId={p.range_type ?? undefined}
                      placeholder={`${p.range_type} suchen …`}
                      onSelect={(hit) => hit && addPick(p.id, hit)}
                    />
                  ) : (
                    <div className="inline">
                      <span className="chip">{selected[0].label ?? selected[0].id.slice(0, 8)}</span>
                      <button type="button" className="ghost" onClick={() => removePick(p.id, 0)}>
                        ändern
                      </button>
                    </div>
                  )}
                </Field>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
