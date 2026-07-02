import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { EntityLink, KindBadge, PageHead, SimilarityBar, fmtDate } from '../components/bits'
import { useVocabulary } from '../hooks/useVocabulary'

export function Dashboard() {
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const { vocab, helpers } = useVocabulary()

  const stats = useQuery({ queryKey: ['stats'], queryFn: api.stats })
  const search = useQuery({
    queryKey: ['search', query, typeFilter],
    queryFn: () => api.search(query, typeFilter || undefined),
    enabled: query.trim().length >= 2,
  })
  const recent = useQuery({
    queryKey: ['entities', 'recent', typeFilter],
    queryFn: () => api.listEntities({ limit: 15, type_id: typeFilter || undefined }),
  })

  const showSearch = query.trim().length >= 2

  return (
    <div className="page">
      <PageHead
        eyebrow="Weltmodell"
        title="Suche"
        sub="Semantische Suche über alle Entities — pgvector + Label."
      />

      <div className="row" style={{ marginBottom: 20 }}>
        <div style={{ flex: 3 }}>
          <input
            autoFocus
            placeholder="Wonach suchst du? (Person, Firma, Ereignis …)"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div>
          <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
            <option value="">Alle Typen</option>
            {vocab?.types.filter((t) => t.parent_id).map((t) => (
              <option key={t.id} value={t.id}>{t.id}</option>
            ))}
          </select>
        </div>
      </div>

      {showSearch ? (
        <div className="panel">
          <h2>Treffer</h2>
          {search.data?.length === 0 && (
            <p className="muted">
              Nichts gefunden. <Link to="/create">Neu anlegen?</Link>
            </p>
          )}
          <table>
            <tbody>
              {search.data?.map((hit) => (
                <tr key={hit.id}>
                  <td><KindBadge kind={helpers?.kindOf(hit.type_id)} typeId={hit.type_id} /></td>
                  <td><EntityLink id={hit.id} label={hit.label} /></td>
                  <td><SimilarityBar value={hit.similarity} /></td>
                  <td><Link to={`/graph/${hit.id}`} className="mono small">graph →</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <>
          <div className="inline" style={{ gap: 28, marginBottom: 20 }}>
            <div className="stat"><b>{stats.data?.entities ?? '–'}</b><span>Entities</span></div>
            <div className="stat"><b>{stats.data?.statements ?? '–'}</b><span>Statements</span></div>
            <div className="stat"><b>{stats.data?.sources ?? '–'}</b><span>Quellen</span></div>
            <div className="stat">
              <b>{stats.data?.pending_proposals ?? '–'}</b>
              <span>{stats.data?.pending_proposals ? <Link to="/gate">Proposals offen</Link> : 'Proposals offen'}</span>
            </div>
          </div>

          <div className="inline" style={{ marginBottom: 20 }}>
            {stats.data?.by_type.map((t) => (
              <button key={t.type_id} type="button" className="chip" style={{ cursor: 'pointer' }}
                onClick={() => setTypeFilter(t.type_id)}>
                {t.type_id} · {t.n}
              </button>
            ))}
          </div>

          <div className="panel">
            <h2>Zuletzt angelegt{typeFilter ? ` — ${typeFilter}` : ''}</h2>
            <table>
              <thead>
                <tr><th>Typ</th><th>Label</th><th>Statements</th><th>Angelegt</th><th /></tr>
              </thead>
              <tbody>
                {recent.data?.items.map((e) => (
                  <tr key={e.id}>
                    <td><KindBadge kind={helpers?.kindOf(e.type_id)} typeId={e.type_id} /></td>
                    <td><EntityLink id={e.id} label={e.label} /></td>
                    <td className="mono">{e.statement_count}</td>
                    <td className="muted small">{fmtDate(e.created_at)}</td>
                    <td><Link to={`/graph/${e.id}`} className="mono small">graph →</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {recent.data?.items.length === 0 && (
              <p className="muted">Noch keine Entities. <Link to="/create">Lege die erste an.</Link></p>
            )}
          </div>
        </>
      )}
    </div>
  )
}
