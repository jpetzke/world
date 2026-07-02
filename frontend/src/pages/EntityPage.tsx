import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { SearchHit, Statement } from '../api/types'
import { EntityAutocomplete } from '../components/EntityAutocomplete'
import { StatementCard } from '../components/StatementCard'
import { ErrorBox, Field, KindBadge, PageHead } from '../components/bits'
import { useVocabulary } from '../hooks/useVocabulary'

export function EntityPage() {
  const { id = '' } = useParams()
  const queryClient = useQueryClient()
  const { helpers } = useVocabulary()

  // Zeitreise (§4): valid_at = „was war wahr?", system_at = „was glaubte ich?"
  const [validAt, setValidAt] = useState('')
  const [systemAt, setSystemAt] = useState('')
  const [includeDeprecated, setIncludeDeprecated] = useState(false)
  const [mergeTarget, setMergeTarget] = useState<SearchHit | null>(null)
  const [actionError, setActionError] = useState<unknown>(null)

  const view = useQuery({
    queryKey: ['entity', id, validAt, systemAt, includeDeprecated],
    queryFn: () => api.entity(id, {
      valid_at: validAt || undefined,
      system_at: systemAt || undefined,
      include_deprecated: includeDeprecated || undefined,
    }),
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['entity', id] })
    queryClient.invalidateQueries({ queryKey: ['stats'] })
  }

  const deprecate = useMutation({
    mutationFn: (statementId: string) => api.deprecateStatement(statementId),
    onSuccess: invalidate,
    onError: setActionError,
  })
  const setRank = useMutation({
    mutationFn: ({ statementId, rank }: { statementId: string; rank: string }) =>
      api.setRank(statementId, rank),
    onSuccess: invalidate,
    onError: setActionError,
  })
  const merge = useMutation({
    mutationFn: (targetId: string) => api.merge(id, targetId),
    onSuccess: () => {
      setMergeTarget(null)
      invalidate()
    },
    onError: setActionError,
  })

  const grouped = useMemo(() => {
    const groups = new Map<string, Statement[]>()
    for (const s of view.data?.statements ?? []) {
      const list = groups.get(s.predicate_id) ?? []
      list.push(s)
      groups.set(s.predicate_id, list)
    }
    return [...groups.entries()]
  }, [view.data])

  if (view.isLoading) return <p className="muted">Lade …</p>
  if (view.error) return <ErrorBox error={view.error} />
  const { entity, incoming } = view.data!
  const timeTravelActive = validAt || systemAt || includeDeprecated

  return (
    <div className="page">
      <PageHead
        eyebrow={`Entity · ${entity.id}`}
        title={
          <span className="inline">
            {entity.label ?? '(ohne Label)'}
            <KindBadge kind={helpers?.kindOf(entity.type_id)} typeId={entity.type_id} />
          </span>
        }
        sub={
          <span className="inline">
            <Link to={`/graph/${entity.id}`}>Im Graph öffnen →</Link>
            <Link to={`/create?statement_subject=${entity.id}`}>Statement hinzufügen +</Link>
          </span>
        }
      />

      <div className="panel">
        <div className="row">
          <Field label="Was war wahr am … (valid_at)">
            <input type="datetime-local" value={validAt} onChange={(e) => setValidAt(e.target.value)} />
          </Field>
          <Field label="Was glaubte ich am … (system_at)">
            <input type="datetime-local" value={systemAt} onChange={(e) => setSystemAt(e.target.value)} />
          </Field>
          <label className="field" style={{ flex: '0 0 auto' }}>
            <span>Deprecated zeigen</span>
            <input
              type="checkbox"
              checked={includeDeprecated}
              onChange={(e) => setIncludeDeprecated(e.target.checked)}
            />
          </label>
          {timeTravelActive ? (
            <div style={{ flex: '0 0 auto' }}>
              <button type="button" onClick={() => { setValidAt(''); setSystemAt(''); setIncludeDeprecated(false) }}>
                Jetzt
              </button>
            </div>
          ) : null}
        </div>
        {timeTravelActive ? <p className="muted small">Historische Sicht — Aktionen sind ausgeblendet.</p> : null}
      </div>

      <ErrorBox error={actionError} />

      {grouped.length === 0 && <p className="muted">Keine Statements in dieser Sicht.</p>}
      {grouped.map(([predicateId, statements]) => (
        <section key={predicateId} className="stmt-group">
          <span className="predicate">{predicateId}</span>
          {statements.map((s) => (
            <StatementCard
              key={s.id}
              statement={s}
              onDeprecate={timeTravelActive ? undefined : (sid) => deprecate.mutate(sid)}
              onSetRank={timeTravelActive ? undefined : (sid, rank) => setRank.mutate({ statementId: sid, rank })}
            />
          ))}
        </section>
      ))}

      {incoming.length > 0 && (
        <section className="stmt-group">
          <h2>Eingehend</h2>
          {incoming.map((s) => (
            <StatementCard
              key={s.id}
              statement={s}
              subjectLabel={{ id: s.subject_id, label: s.subject_label ?? null, typeId: s.subject_type }}
            />
          ))}
        </section>
      )}

      {!timeTravelActive && (
        <div className="panel">
          <h2>In andere Entity mergen</h2>
          <p className="muted small">
            Verlustfrei: alle Statements wandern zum Ziel, Provenance bleibt (§7.2).
          </p>
          <div className="row">
            <div style={{ flex: 3 }}>
              <EntityAutocomplete
                typeId={entity.type_id}
                selected={mergeTarget}
                onSelect={setMergeTarget}
                placeholder={`Ziel-${entity.type_id} suchen …`}
              />
            </div>
            <div style={{ flex: '0 0 auto' }}>
              <button
                type="button"
                className="danger"
                disabled={!mergeTarget || merge.isPending}
                onClick={() => {
                  if (mergeTarget && window.confirm(`"${entity.label}" in "${mergeTarget.label}" mergen?`)) {
                    merge.mutate(mergeTarget.id)
                  }
                }}
              >
                Mergen
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
