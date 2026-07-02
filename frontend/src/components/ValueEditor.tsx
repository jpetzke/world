import { useState } from 'react'
import type { RangeKind, SearchHit, ValuePayload } from '../api/types'
import { EntityAutocomplete } from './EntityAutocomplete'
import { Field } from './bits'

interface Props {
  rangeKind: RangeKind
  rangeType?: string | null
  value: ValuePayload | null
  onChange: (value: ValuePayload | null) => void
}

/** Polymorpher Wert-Editor (§3.1): eine Struktur, sieben Wertarten. */
export function ValueEditor({ rangeKind, rangeType, value, onChange }: Props) {
  const [entityHit, setEntityHit] = useState<SearchHit | null>(null)
  const [jsonError, setJsonError] = useState<string | null>(null)

  switch (rangeKind) {
    case 'entity':
      return (
        <Field label={`Objekt-Entity${rangeType ? ` (${rangeType})` : ''}`}>
          <EntityAutocomplete
            typeId={rangeType ?? undefined}
            selected={entityHit}
            onSelect={(hit) => {
              setEntityHit(hit)
              onChange(hit ? { type: 'entity', object_id: hit.id } : null)
            }}
          />
        </Field>
      )
    case 'string':
      return (
        <Field label="Text">
          <input
            value={value?.type === 'string' ? value.text : ''}
            onChange={(e) => onChange(e.target.value ? { type: 'string', text: e.target.value } : null)}
          />
        </Field>
      )
    case 'number':
      return (
        <Field label="Zahl">
          <input
            type="number" step="any"
            value={value?.type === 'number' ? value.number : ''}
            onChange={(e) => onChange(e.target.value === '' ? null : { type: 'number', number: Number(e.target.value) })}
          />
        </Field>
      )
    case 'quantity': {
      const current = value?.type === 'quantity' ? value : null
      return (
        <div className="row">
          <Field label="Wert">
            <input
              type="number" step="any"
              value={current?.number ?? ''}
              onChange={(e) => {
                const num = e.target.value === '' ? null : Number(e.target.value)
                onChange(num === null ? null : { type: 'quantity', number: num, unit: current?.unit ?? '' })
              }}
            />
          </Field>
          <Field label="Einheit">
            <input
              placeholder="EUR, km, % …"
              value={current?.unit ?? ''}
              onChange={(e) => onChange(current === null && e.target.value === ''
                ? null
                : { type: 'quantity', number: current?.number ?? 0, unit: e.target.value })}
            />
          </Field>
        </div>
      )
    }
    case 'datetime':
      return (
        <Field label="Zeitpunkt">
          <input
            type="datetime-local"
            value={value?.type === 'datetime' ? value.datetime : ''}
            onChange={(e) => onChange(e.target.value ? { type: 'datetime', datetime: e.target.value } : null)}
          />
        </Field>
      )
    case 'geo': {
      const current = value?.type === 'geo' ? value : null
      return (
        <div className="row">
          <Field label="Breite (lat)">
            <input
              type="number" step="any" min={-90} max={90}
              value={current?.lat ?? ''}
              onChange={(e) => onChange(e.target.value === ''
                ? null
                : { type: 'geo', lat: Number(e.target.value), lon: current?.lon ?? 0 })}
            />
          </Field>
          <Field label="Länge (lon)">
            <input
              type="number" step="any" min={-180} max={180}
              value={current?.lon ?? ''}
              onChange={(e) => onChange(e.target.value === ''
                ? null
                : { type: 'geo', lat: current?.lat ?? 0, lon: Number(e.target.value) })}
            />
          </Field>
        </div>
      )
    }
    case 'json':
      return (
        <Field label="JSON">
          <textarea
            defaultValue={value?.type === 'json' ? JSON.stringify(value.json, null, 2) : ''}
            onChange={(e) => {
              if (!e.target.value.trim()) {
                setJsonError(null)
                onChange(null)
                return
              }
              try {
                onChange({ type: 'json', json: JSON.parse(e.target.value) })
                setJsonError(null)
              } catch {
                setJsonError('Ungültiges JSON')
                onChange(null)
              }
            }}
          />
          {jsonError && <span className="muted small">{jsonError}</span>}
        </Field>
      )
  }
}
