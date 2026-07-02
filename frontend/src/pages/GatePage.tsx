import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import { ErrorBox, KindBadge, PageHead, fmtDate } from '../components/bits'

export function GatePage() {
  const [status, setStatus] = useState<'pending' | 'approved' | 'rejected'>('pending')
  const queryClient = useQueryClient()
  const proposals = useQuery({
    queryKey: ['proposals', status],
    queryFn: () => api.proposals(status),
  })

  const decide = useMutation({
    mutationFn: ({ kind, id, decision }: { kind: 'types' | 'predicates'; id: string; decision: 'approve' | 'reject' }) =>
      api.decideProposal(kind, id, decision),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['proposals'] })
      queryClient.invalidateQueries({ queryKey: ['vocabulary'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })

  const typeProposals = proposals.data?.types ?? []
  const predicateProposals = proposals.data?.predicates ?? []
  const empty = typeProposals.length === 0 && predicateProposals.length === 0

  return (
    <div className="page">
      <PageHead
        eyebrow="Review-Gate · §7.1"
        title="Gate"
        sub="Der Extraktor schreibt nie frei. Approve erzwingt die Registry-Regeln; Reject verwirft ohne Spuren im Vokabular."
      />

      <div className="tabs">
        {(['pending', 'approved', 'rejected'] as const).map((s) => (
          <button key={s} type="button" className={status === s ? 'active' : ''} onClick={() => setStatus(s)}>
            {s}
          </button>
        ))}
      </div>

      <ErrorBox error={decide.error} />
      {empty && <p className="muted">Keine Proposals mit Status „{status}".</p>}

      {typeProposals.map((p) => (
        <div key={p.id} className="panel">
          <div className="spread">
            <div>
              <div className="eyebrow">Typ-Proposal · von {p.proposed_by} · {fmtDate(p.created_at)}</div>
              <div className="inline" style={{ marginTop: 6 }}>
                <KindBadge kind={p.kind} typeId={p.type_id} />
                <span className="muted">⊂ {p.parent_id}</span>
                {p.interfaces.map((iface) => <span key={iface} className="chip">{iface}</span>)}
              </div>
              {p.rationale && <p className="small muted" style={{ marginBottom: 0 }}>{p.rationale}</p>}
            </div>
            {status === 'pending' && (
              <div className="inline">
                <button type="button" className="affirm"
                  onClick={() => decide.mutate({ kind: 'types', id: p.id, decision: 'approve' })}>
                  Approve
                </button>
                <button type="button" className="danger"
                  onClick={() => decide.mutate({ kind: 'types', id: p.id, decision: 'reject' })}>
                  Reject
                </button>
              </div>
            )}
          </div>
        </div>
      ))}

      {predicateProposals.map((p) => (
        <div key={p.id} className="panel">
          <div className="spread">
            <div>
              <div className="eyebrow">Prädikat-Proposal · von {p.proposed_by} · {fmtDate(p.created_at)}</div>
              <div className="inline" style={{ marginTop: 6 }}>
                <span className="predicate">{p.predicate_id}</span>
                <span className="mono small muted">
                  {p.domain_type ?? (p.domain_interface ? `⟨${p.domain_interface}⟩` : '∗')}
                  {' → '}{p.range_kind}{p.range_type ? `(${p.range_type})` : ''}
                  {' · '}{p.cardinality ?? 'ohne Kardinalität'}
                </span>
              </div>
              {p.rationale && <p className="small muted" style={{ marginBottom: 0 }}>{p.rationale}</p>}
            </div>
            {status === 'pending' && (
              <div className="inline">
                <button type="button" className="affirm"
                  onClick={() => decide.mutate({ kind: 'predicates', id: p.id, decision: 'approve' })}>
                  Approve
                </button>
                <button type="button" className="danger"
                  onClick={() => decide.mutate({ kind: 'predicates', id: p.id, decision: 'reject' })}>
                  Reject
                </button>
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
