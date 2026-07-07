import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { EntityListItem } from '../api/types'
import { Empty, EntityLink, KindBadge, Loading, PageHead, SimilarityBar, fmtDate, fmtRelative } from '../components/bits'
import { Combobox } from '../components/Combobox'
import { useVocabulary } from '../hooks/useVocabulary'

/** Aufeinanderfolgende Einträge derselben Import-Minute + desselben Typs zu
    einer Zeile bündeln — 16× „06.07.2026, 23:49" ist Rauschen, „16 Entities
    importiert" ist Information. */
function groupBulk(items: EntityListItem[]): (
  | { kind: 'single'; item: EntityListItem }
  | { kind: 'bulk'; items: EntityListItem[]; minute: string }
)[] {
  const out: ReturnType<typeof groupBulk> = []
  let run: EntityListItem[] = []
  const minuteOf = (e: EntityListItem) => (e.created_at ?? '').slice(0, 16)
  const flush = () => {
    if (run.length >= 3) out.push({ kind: 'bulk', items: run, minute: minuteOf(run[0]) })
    else run.forEach((item) => out.push({ kind: 'single', item }))
    run = []
  }
  for (const e of items) {
    if (run.length && (minuteOf(e) !== minuteOf(run[0]) || e.type_id !== run[0].type_id)) flush()
    run.push(e)
  }
  flush()
  return out
}

export function Dashboard() {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [expandedBulk, setExpandedBulk] = useState<string | null>(null)
  const { vocab, helpers } = useVocabulary()

  const stats = useQuery({ queryKey: ['stats'], queryFn: api.stats })
  const search = useQuery({
    queryKey: ['search', query, typeFilter],
    queryFn: () => api.search(query, typeFilter || undefined),
    enabled: query.trim().length >= 2,
  })
  const recent = useQuery({
    queryKey: ['entities', 'recent', typeFilter],
    queryFn: () => api.listEntities({ limit: 30, type_id: typeFilter || undefined }),
  })

  const showSearch = query.trim().length >= 2
  const grouped = useMemo(() => groupBulk(recent.data?.items ?? []), [recent.data])

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
            placeholder="Nach Name suchen …"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div style={{ flex: 1, minWidth: 180 }}>
          <Combobox
            options={[{ id: '', label: 'Alle Typen' },
              ...(vocab?.types ?? []).filter((t) => !t.abstract).map((t) => ({ id: t.id, label: t.id }))]}
            value={typeFilter}
            onChange={setTypeFilter}
            placeholder="Alle Typen"
          />
        </div>
      </div>

      {showSearch ? (
        <div className="panel">
          <h2>Treffer</h2>
          {search.isFetching && !search.data ? (
            <Loading label="Suche läuft …" />
          ) : search.data && search.data.length === 0 ? (
            <Empty title="Nichts gefunden">
              Kein Treffer für „{query.trim()}". <Link to="/create">Neu anlegen?</Link>
            </Empty>
          ) : (
            <table>
              <tbody>
                {search.data?.map((hit) => (
                  <tr key={hit.id}>
                    <td><KindBadge kind={helpers?.kindOf(hit.type_id)} typeId={hit.type_id} /></td>
                    <td><EntityLink id={hit.id} label={hit.label} /></td>
                    <td><SimilarityBar value={hit.similarity} /></td>
                    <td>
                      <button type="button" className="sm" onClick={() => navigate(`/graph/${hit.id}`)}>
                        Graph →
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : (
        <>
          {/* Stat-Cards: Zahlen sind Filter/Einstiege, keine toten Ziffern. */}
          <div className="gstats" style={{ marginLeft: 0, marginBottom: 20, flexWrap: 'wrap' }}>
            <button type="button" className="gstat" title="Alle Entities zeigen"
              onClick={() => setTypeFilter('')}>
              <b>{stats.data?.entities ?? '–'}</b><span>Entities</span>
            </button>
            <button type="button" className="gstat" title="Zum Graphen"
              onClick={() => navigate('/')}>
              <b>{stats.data?.statements ?? '–'}</b><span>Statements</span>
            </button>
            <button type="button" className="gstat" title="Zu den Quellen"
              onClick={() => navigate('/sources')}>
              <b>{stats.data?.sources ?? '–'}</b><span>Quellen</span>
            </button>
            <button type="button" className="gstat" title="Zum Gate"
              onClick={() => navigate('/gate')}>
              <b>{stats.data?.pending_proposals ?? '–'}</b><span>Proposals offen</span>
            </button>
          </div>

          <div className="inline" style={{ marginBottom: 20 }}>
            {stats.data?.by_type.map((t) => (
              <button key={t.type_id} type="button"
                className={`tchip${typeFilter === t.type_id ? ' on' : ''}`}
                aria-pressed={typeFilter === t.type_id}
                onClick={() => setTypeFilter(typeFilter === t.type_id ? '' : t.type_id)}>
                {t.type_id} <span className="n">{t.n}</span>
              </button>
            ))}
          </div>

          <div className="panel">
            <h2>Zuletzt angelegt{typeFilter ? ` — ${typeFilter}` : ''}</h2>
            {recent.isLoading ? (
              <Loading />
            ) : recent.data && recent.data.items.length === 0 ? (
              <Empty title="Noch keine Entities">
                {typeFilter
                  ? <>Kein Eintrag vom Typ {typeFilter}.</>
                  : <><Link to="/create">Lege die erste an</Link> oder <Link to="/sources">ingestiere ein Dokument</Link>.</>}
              </Empty>
            ) : (
              <table>
                <thead>
                  <tr><th>Typ</th><th>Label</th><th>Statements</th><th>Angelegt</th><th /></tr>
                </thead>
                <tbody>
                  {grouped.map((g) => {
                    if (g.kind === 'single') {
                      const e = g.item
                      return (
                        <tr key={e.id}>
                          <td><KindBadge kind={helpers?.kindOf(e.type_id)} typeId={e.type_id} /></td>
                          <td><EntityLink id={e.id} label={e.label} /></td>
                          <td className="mono">{e.statement_count}</td>
                          <td className="muted small" title={fmtDate(e.created_at)}>{fmtRelative(e.created_at)}</td>
                          <td>
                            <button type="button" className="sm" onClick={() => navigate(`/graph/${e.id}`)}>
                              Graph →
                            </button>
                          </td>
                        </tr>
                      )
                    }
                    const open = expandedBulk === g.minute
                    return [
                      <tr key={`bulk-${g.minute}`}>
                        <td><KindBadge kind={helpers?.kindOf(g.items[0].type_id)} typeId={g.items[0].type_id} /></td>
                        <td colSpan={2}>
                          <button type="button" className="linklike"
                            aria-expanded={open}
                            onClick={() => setExpandedBulk(open ? null : g.minute)}>
                            {open ? '▾' : '▸'} {g.items.length} Entities importiert
                          </button>
                        </td>
                        <td className="muted small" title={fmtDate(g.items[0].created_at)}>
                          {fmtRelative(g.items[0].created_at)}
                        </td>
                        <td />
                      </tr>,
                      ...(open ? g.items.map((e) => (
                        <tr key={e.id} className="bulk-row">
                          <td />
                          <td><EntityLink id={e.id} label={e.label} /></td>
                          <td className="mono">{e.statement_count}</td>
                          <td />
                          <td>
                            <button type="button" className="sm" onClick={() => navigate(`/graph/${e.id}`)}>
                              Graph →
                            </button>
                          </td>
                        </tr>
                      )) : []),
                    ]
                  })}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  )
}
