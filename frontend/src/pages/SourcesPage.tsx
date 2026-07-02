import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { PipelineReport } from '../api/types'
import { EntityLink, ErrorBox, Field, OkBox, PageHead, fmtDate } from '../components/bits'

export function SourcesPage() {
  const sources = useQuery({
    queryKey: ['sources', 'list'],
    queryFn: () => api.listSources({ limit: 100 }),
  })

  return (
    <div className="page">
      <PageHead
        eyebrow="Provenance · PROV-O"
        title="Quellen"
        sub="Nichts ist Fakt, alles ist Behauptung von Quelle X. Jedes Statement referenziert mindestens ein Dokument hier."
      />

      <IngestPanel />

      <div className="panel">
        <h2>Dokumente ({sources.data?.total ?? '–'})</h2>
        <table>
          <thead>
            <tr><th>Activity</th><th>Agent</th><th>URL</th><th>Statements</th><th>Abgerufen</th></tr>
          </thead>
          <tbody>
            {sources.data?.items.map((s) => (
              <tr key={s.id}>
                <td><Link to={`/sources/${s.id}`} className="mono">{s.activity ?? s.id.slice(0, 8)}</Link></td>
                <td className="mono small">{s.agent ?? '—'}</td>
                <td className="small">{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.url}</a> : '—'}</td>
                <td className="mono">{s.statement_count}</td>
                <td className="muted small">{fmtDate(s.retrieved_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function IngestPanel() {
  const queryClient = useQueryClient()
  const [rawText, setRawText] = useState(
    '{\n  "kind": "social_profile",\n  "name": "",\n  "email": ""\n}',
  )
  const [activity, setActivity] = useState('manual:ingest')
  const [url, setUrl] = useState('')
  const [extractor, setExtractor] = useState('rule-based')
  const [report, setReport] = useState<PipelineReport | null>(null)
  const [jsonError, setJsonError] = useState<string | null>(null)

  const ingest = useMutation({
    mutationFn: (raw: unknown) => api.ingest({
      activity, agent: 'weltmodell-ui', raw, url: url || undefined, extractor,
    }),
    onSuccess: (result) => {
      setReport(result.pipeline)
      queryClient.invalidateQueries({ queryKey: ['sources'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
      queryClient.invalidateQueries({ queryKey: ['proposals'] })
    },
  })

  return (
    <form
      className="panel"
      onSubmit={(e) => {
        e.preventDefault()
        setReport(null)
        setJsonError(null)
        try {
          ingest.mutate(JSON.parse(rawText))
        } catch {
          setJsonError('Rohdokument ist kein gültiges JSON.')
        }
      }}
    >
      <h2>Manueller Ingest</h2>
      <p className="muted small">
        Dokument speichern → Extract → Resolve (Dedup) → Validate → Commit.
        Unbekannte Felder landen als Proposal im <Link to="/gate">Gate</Link>.
      </p>
      <Field label="Rohdokument (JSON)">
        <textarea value={rawText} onChange={(e) => setRawText(e.target.value)} rows={8} />
      </Field>
      <div className="row">
        <Field label="Activity">
          <input value={activity} onChange={(e) => setActivity(e.target.value)} />
        </Field>
        <Field label="URL (optional)">
          <input value={url} onChange={(e) => setUrl(e.target.value)} />
        </Field>
        <Field label="Extraktor">
          <select value={extractor} onChange={(e) => setExtractor(e.target.value)}>
            <option value="rule-based">rule-based (strukturierte Profile)</option>
            <option value="llm">llm (OpenRouter, freier Text)</option>
          </select>
        </Field>
        <div style={{ flex: '0 0 auto' }}>
          <button type="submit" className="primary" disabled={ingest.isPending}>
            {ingest.isPending ? 'Pipeline läuft …' : 'Ingest starten'}
          </button>
        </div>
      </div>
      {jsonError && <div className="error-box">{jsonError}</div>}
      <ErrorBox error={ingest.error} />
      {report && (
        <OkBox>
          <strong>Pipeline-Report:</strong> {report.committed.length} Statements committed,{' '}
          {report.entities_created.length} Entities neu, {report.proposals.length} Proposals,{' '}
          {report.rejected.length} Rejects.
          {report.entities_created.length > 0 && (
            <div className="inline" style={{ marginTop: 6 }}>
              {report.entities_created.map((id) => <EntityLink key={id} id={id} />)}
            </div>
          )}
          {report.rejected.length > 0 && (
            <ul>
              {report.rejected.map((r, i) => (
                <li key={i}>{r.predicate ?? r.proposal}: {r.problems.join('; ')}</li>
              ))}
            </ul>
          )}
        </OkBox>
      )}
    </form>
  )
}

export function SourceDetailPage() {
  const { id = '' } = useParams()
  const detail = useQuery({ queryKey: ['source', id], queryFn: () => api.source(id) })

  if (detail.isLoading) return <p className="muted">Lade …</p>
  if (detail.error) return <ErrorBox error={detail.error} />
  const { source, statements } = detail.data!

  return (
    <div className="page">
      <PageHead
        eyebrow={`Quelle · ${source.id}`}
        title={source.activity ?? 'Dokument'}
        sub={
          <span className="inline">
            <span className="mono small">{source.agent}</span>
            {source.url && <a href={source.url} target="_blank" rel="noreferrer">{source.url}</a>}
            <span className="muted small">{fmtDate(source.retrieved_at)}</span>
          </span>
        }
      />
      <div className="panel">
        <h2>Rohdokument</h2>
        <pre className="mono small" style={{ overflowX: 'auto', margin: 0 }}>
          {JSON.stringify(source.raw, null, 2)}
        </pre>
      </div>
      <div className="panel">
        <h2>Belegte Statements ({statements.length})</h2>
        <table>
          <thead><tr><th>Subjekt</th><th>Prädikat</th><th>Wert</th><th>Rank</th><th>Conf.</th></tr></thead>
          <tbody>
            {statements.map((s) => (
              <tr key={s.id}>
                <td><EntityLink id={s.subject_id} label={s.subject_label} /></td>
                <td><span className="predicate">{s.predicate_id}</span></td>
                <td className="small">
                  {s.value_type === 'entity'
                    ? <EntityLink id={s.object_id!} label={s.object_label} />
                    : (s.value_text ?? s.value_number ?? fmtDate(s.value_datetime))}
                  {s.value_unit ? ` ${s.value_unit}` : ''}
                </td>
                <td><span className={`rank ${s.rank}`}>{s.rank}</span></td>
                <td className="mono small">{s.confidence.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
