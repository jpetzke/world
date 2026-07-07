import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import { Field } from './bits'
import { Combobox } from './Combobox'

export interface SourceDraft {
  mode: 'existing' | 'new'
  sourceId: string | null
  url: string
  activity: string
  agent: string
}

export const emptySourceDraft: SourceDraft = {
  mode: 'existing',
  sourceId: null,
  url: '',
  activity: 'manual:ui',
  agent: 'weltmodell-ui',
}

/** Liefert die source_id — legt bei mode='new' erst die Quelle an. */
export async function ensureSource(draft: SourceDraft): Promise<string> {
  if (draft.mode === 'existing') {
    if (!draft.sourceId) throw new Error('Quelle wählen (kein Fakt ohne Provenance)')
    return draft.sourceId
  }
  const created = await api.createSource({
    activity: draft.activity || 'manual:ui',
    agent: draft.agent || 'weltmodell-ui',
    url: draft.url || undefined,
  })
  return created.id
}

export function SourcePicker({ draft, onChange }: {
  draft: SourceDraft
  onChange: (draft: SourceDraft) => void
}) {
  const { data } = useQuery({
    queryKey: ['sources', 'picker'],
    queryFn: () => api.listSources({ limit: 30 }),
  })
  const [initialised, setInitialised] = useState(false)
  if (!initialised && data && data.items.length === 0 && draft.mode === 'existing') {
    onChange({ ...draft, mode: 'new' })
    setInitialised(true)
  }

  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <div className="spread" style={{ marginBottom: 10 }}>
        <span className="field-label" style={{ margin: 0 }}>Provenance — Pflicht (Invariante 3)</span>
        <div className="inline">
          <button
            type="button"
            className={draft.mode === 'existing' ? 'primary' : ''}
            onClick={() => onChange({ ...draft, mode: 'existing' })}
          >
            Vorhandene Quelle
          </button>
          <button
            type="button"
            className={draft.mode === 'new' ? 'primary' : ''}
            onClick={() => onChange({ ...draft, mode: 'new' })}
          >
            Neue Quelle
          </button>
        </div>
      </div>

      {draft.mode === 'existing' ? (
        <Field label="Quelle">
          <Combobox
            options={[{ id: '', label: '— wählen —' },
              ...(data?.items ?? []).map((s) => ({
                id: s.id,
                label: (s.activity ?? 'unbekannt') + (s.url ? ` · ${s.url}` : ''),
              }))]}
            value={draft.sourceId ?? ''}
            onChange={(id) => onChange({ ...draft, sourceId: id || null })}
          />
        </Field>
      ) : (
        <div className="row">
          <Field label="URL (optional)">
            <input value={draft.url} onChange={(e) => onChange({ ...draft, url: e.target.value })} />
          </Field>
          <Field label="Activity">
            <input value={draft.activity} onChange={(e) => onChange({ ...draft, activity: e.target.value })} />
          </Field>
          <Field label="Agent">
            <input value={draft.agent} onChange={(e) => onChange({ ...draft, agent: e.target.value })} />
          </Field>
        </div>
      )}
    </div>
  )
}
