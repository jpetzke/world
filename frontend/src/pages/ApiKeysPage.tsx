import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import type { ApiKey, ApiKeyScope } from '../api/types'
import { Empty, ErrorBox, Field, Loading, PageHead, fmtDate } from '../components/bits'

const SCOPE_LABEL: Record<ApiKeyScope, string> = {
  read: 'read — nur lesen',
  write: 'write — lesen + schreiben',
  admin: 'admin — alles, inkl. Gate',
}

export function ApiKeysPage() {
  const qc = useQueryClient()
  const keys = useQuery({ queryKey: ['apiKeys'], queryFn: api.keys.list })
  const invalidate = () => qc.invalidateQueries({ queryKey: ['apiKeys'] })

  const rotate = useMutation({ mutationFn: api.keys.rotate, onSuccess: invalidate })
  const remove = useMutation({ mutationFn: api.keys.remove, onSuccess: invalidate })

  return (
    <div className="page">
      <PageHead
        eyebrow="Maschinenzugriff"
        title="API-Keys"
        sub={
          <>
            Externe Automationen (n8n & Co.) sprechen dieselbe <code>/api</code>-Fläche —
            per <code>Authorization: Bearer &lt;key&gt;</code> oder <code>X-API-Key</code>.
            Scopes sind hierarchisch: read &lt; write &lt; admin.
          </>
        }
      />

      <CreateKeyForm onCreated={invalidate} />

      {keys.isLoading && <Loading label="Lade Keys …" />}
      <ErrorBox error={keys.error ?? rotate.error ?? remove.error} />

      {keys.data && keys.data.length === 0 && (
        <Empty title="Noch keine API-Keys">Oben einen Key anlegen — der Scope bestimmt, was er darf.</Empty>
      )}

      {keys.data && keys.data.length > 0 && (
        <div className="panel" style={{ overflowX: 'auto' }}>
          <table>
            <thead>
              <tr>
                <th>Name</th><th>Scope</th><th>Key</th>
                <th>Erstellt</th><th>Rotiert</th><th>Zuletzt benutzt</th><th />
              </tr>
            </thead>
            <tbody>
              {keys.data.map((k) => (
                <KeyRow
                  key={k.id}
                  apiKey={k}
                  onRotate={() => {
                    if (window.confirm(`Key "${k.name}" rotieren? Der alte Key wird sofort ungültig.`)) {
                      rotate.mutate(k.id)
                    }
                  }}
                  onDelete={() => {
                    if (window.confirm(`Key "${k.name}" endgültig löschen?`)) {
                      remove.mutate(k.id)
                    }
                  }}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function CreateKeyForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState('')
  const [scope, setScope] = useState<ApiKeyScope>('read')

  const create = useMutation({
    mutationFn: () => api.keys.create(name.trim(), scope),
    onSuccess: () => {
      setName('')
      onCreated()
    },
  })

  return (
    <form
      className="panel"
      onSubmit={(e) => {
        e.preventDefault()
        if (name.trim()) create.mutate()
      }}
    >
      <div className="row" style={{ alignItems: 'flex-end', gap: '12px', flexWrap: 'wrap' }}>
        <Field label="Name">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="z. B. n8n-live-import"
            required
          />
        </Field>
        <Field label="Scope">
          <select value={scope} onChange={(e) => setScope(e.target.value as ApiKeyScope)}>
            {(Object.keys(SCOPE_LABEL) as ApiKeyScope[]).map((s) => (
              <option key={s} value={s}>{SCOPE_LABEL[s]}</option>
            ))}
          </select>
        </Field>
        <button type="submit" disabled={create.isPending || !name.trim()}>
          Key anlegen
        </button>
      </div>
      <ErrorBox error={create.error} />
    </form>
  )
}

function KeyRow({ apiKey, onRotate, onDelete }: { apiKey: ApiKey; onRotate: () => void; onDelete: () => void }) {
  const [revealed, setRevealed] = useState(false)
  const [copied, setCopied] = useState(false)

  async function copy() {
    await navigator.clipboard.writeText(apiKey.secret)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <tr>
      <td>{apiKey.name}</td>
      <td><span className="chip" title={SCOPE_LABEL[apiKey.scope]}>{apiKey.scope}</span></td>
      <td className="mono small">
        <span style={{ marginRight: 8 }}>
          {revealed ? apiKey.secret : `${apiKey.secret.slice(0, 6)}…${apiKey.secret.slice(-4)}`}
        </span>
        <button type="button" className="linklike" onClick={() => setRevealed(!revealed)}>
          {revealed ? 'verbergen' : 'anzeigen'}
        </button>{' '}
        <button type="button" className="linklike" onClick={copy}>
          {copied ? 'kopiert ✓' : 'kopieren'}
        </button>
      </td>
      <td className="muted small">{fmtDate(apiKey.created_at)}</td>
      <td className="muted small">{fmtDate(apiKey.rotated_at)}</td>
      <td className="muted small">{fmtDate(apiKey.last_used_at)}</td>
      <td>
        <button type="button" className="linklike" onClick={onRotate}>rotieren</button>{' '}
        <button type="button" className="linklike danger" onClick={onDelete}>löschen</button>
      </td>
    </tr>
  )
}
