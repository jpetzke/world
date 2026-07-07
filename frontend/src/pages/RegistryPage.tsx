import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api } from '../api/client'
import type { EntityType } from '../api/types'
import { Combobox } from '../components/Combobox'
import { ErrorBox, Field, KindBadge, OkBox, PageHead } from '../components/bits'
import { useVocabulary } from '../hooks/useVocabulary'

export function RegistryPage() {
  const [tab, setTab] = useState<'types' | 'predicates' | 'interfaces'>('types')
  const { vocab, helpers } = useVocabulary()

  return (
    <div className="page">
      <PageHead
        eyebrow="Schema-als-Daten"
        title="Registry"
        sub="Typen, Interfaces und Prädikate sind Daten. Neues entsteht nur über Proposals — das Gate erzwingt Parent + Interfaces bzw. domain/range/cardinality."
      />
      <div className="tabs">
        <button type="button" className={tab === 'types' ? 'active' : ''} onClick={() => setTab('types')}>
          Typen ({vocab?.types.length ?? 0})
        </button>
        <button type="button" className={tab === 'predicates' ? 'active' : ''} onClick={() => setTab('predicates')}>
          Prädikate ({vocab?.predicates.length ?? 0})
        </button>
        <button type="button" className={tab === 'interfaces' ? 'active' : ''} onClick={() => setTab('interfaces')}>
          Interfaces ({vocab?.interfaces.length ?? 0})
        </button>
      </div>

      {tab === 'types' && vocab && (
        <>
          <div className="panel type-tree">
            <TypeTree types={vocab.types} interfacesOf={(t) => helpers?.interfacesOf(t) ?? new Set()} />
          </div>
          <TypeProposalForm />
        </>
      )}

      {tab === 'predicates' && vocab && (
        <>
          <div className="panel" style={{ overflowX: 'auto' }}>
            <table>
              <thead>
                <tr>
                  <th>Prädikat</th><th>Domain</th><th>Range</th><th>Card.</th>
                  <th>Invers</th><th>ID-Key</th><th>Extern</th>
                </tr>
              </thead>
              <tbody>
                {vocab.predicates.map((p) => (
                  <tr key={p.id}>
                    <td><span className="predicate">{p.id}</span></td>
                    <td className="mono small">{p.domain_type ?? (p.domain_interface ? `⟨${p.domain_interface}⟩` : '∗')}</td>
                    <td className="mono small">{p.range_kind}{p.range_type ? `(${p.range_type})` : ''}</td>
                    <td className="mono small">{p.cardinality ?? '—'}</td>
                    <td className="mono small">{p.inverse_id ?? '—'}</td>
                    <td>{p.identifying ? <span className="chip" title="Deterministischer Dedup-Key">key</span> : ''}</td>
                    <td className="mono small muted">{[p.wikidata_pid, p.schema_org].filter(Boolean).join(' · ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <PredicateProposalForm />
        </>
      )}

      {tab === 'interfaces' && vocab && (
        <div className="panel">
          <table>
            <thead><tr><th>Interface</th><th>Beschreibung</th><th>Implementiert von</th></tr></thead>
            <tbody>
              {vocab.interfaces.map((iface) => (
                <tr key={iface.id}>
                  <td className="mono">{iface.id}</td>
                  <td className="muted">{iface.label}</td>
                  <td className="small">
                    {vocab.implementations
                      .filter((impl) => impl.interface_id === iface.id)
                      .map((impl) => impl.type_id)
                      .join(', ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function TypeTree({ types, interfacesOf }: {
  types: EntityType[]
  interfacesOf: (typeId: string) => Set<string>
}) {
  const children = useMemo(() => {
    const map = new Map<string | null, EntityType[]>()
    for (const t of types) {
      const list = map.get(t.parent_id) ?? []
      list.push(t)
      map.set(t.parent_id, list)
    }
    return map
  }, [types])

  const renderLevel = (parentId: string | null) => {
    const level = children.get(parentId)
    if (!level?.length) return null
    return (
      <ul>
        {level.sort((a, b) => a.id.localeCompare(b.id)).map((t) => (
          <li key={t.id}>
            <div className="type-node">
              <KindBadge kind={t.kind} typeId={t.id} />
              {[...interfacesOf(t.id)].map((iface) => (
                <span key={iface} className="chip">{iface}</span>
              ))}
              {t.wikidata_qid && <span className="muted mono small">{t.wikidata_qid}</span>}
            </div>
            {renderLevel(t.id)}
          </li>
        ))}
      </ul>
    )
  }

  return <div className="type-tree">{renderLevel(null)}</div>
}

function TypeProposalForm() {
  const { vocab } = useVocabulary()
  const queryClient = useQueryClient()
  const [form, setForm] = useState({ type_id: '', parent_id: 'Person', label: '', interfaces: [] as string[], rationale: '' })
  const [ok, setOk] = useState(false)

  const parentKind = vocab?.types.find((t) => t.id === form.parent_id)?.kind ?? 'continuant'

  const propose = useMutation({
    mutationFn: () => api.proposeType({
      ...form,
      kind: parentKind,
      label: form.label || form.type_id,
      proposed_by: 'weltmodell-ui',
    }),
    onSuccess: () => {
      setOk(true)
      setForm({ type_id: '', parent_id: 'Person', label: '', interfaces: [], rationale: '' })
      queryClient.invalidateQueries({ queryKey: ['proposals'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })

  return (
    <form className="panel" onSubmit={(e) => { e.preventDefault(); setOk(false); propose.mutate() }}>
      <span className="field-label">Neuen Typ vorschlagen (geht ins Gate)</span>
      {ok && <OkBox>Proposal eingereicht — Review im Gate.</OkBox>}
      <div className="row">
        <Field label="Typ-ID">
          <input value={form.type_id} placeholder="PascalCase, z. B. Influencer" onChange={(e) => setForm({ ...form, type_id: e.target.value })} />
        </Field>
        <Field label={`Parent (vererbt kind: ${parentKind})`}>
          <Combobox
            options={(vocab?.types ?? []).map((t) => ({ id: t.id, label: t.id }))}
            value={form.parent_id}
            onChange={(id) => setForm({ ...form, parent_id: id })}
          />
        </Field>
        <Field label="Label">
          <input value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} />
        </Field>
      </div>
      <Field label="Interfaces">
        <div className="inline">
          {vocab?.interfaces.map((iface) => (
            <button key={iface.id} type="button"
              className={`tchip${form.interfaces.includes(iface.id) ? ' on' : ''}`}
              aria-pressed={form.interfaces.includes(iface.id)}
              onClick={() => setForm({
                  ...form,
                  interfaces: !form.interfaces.includes(iface.id)
                    ? [...form.interfaces, iface.id]
                    : form.interfaces.filter((x) => x !== iface.id),
                })}>
              {iface.id}
            </button>
          ))}
        </div>
      </Field>
      <Field label="Begründung">
        <input value={form.rationale} onChange={(e) => setForm({ ...form, rationale: e.target.value })} />
      </Field>
      <ErrorBox error={propose.error} />
      <button type="submit" disabled={!form.type_id || propose.isPending}>Typ vorschlagen</button>
    </form>
  )
}

function PredicateProposalForm() {
  const { vocab } = useVocabulary()
  const queryClient = useQueryClient()
  const empty = {
    predicate_id: '', label: '', domain_type: 'Person', domain_interface: '',
    range_kind: 'string', range_type: '', cardinality: '1:n', inverse_id: '', rationale: '',
  }
  const [form, setForm] = useState(empty)
  const [ok, setOk] = useState(false)

  const propose = useMutation({
    mutationFn: () => api.proposePredicate({
      predicate_id: form.predicate_id,
      label: form.label || form.predicate_id,
      domain_type: form.domain_type || null,
      domain_interface: form.domain_interface || null,
      range_kind: form.range_kind,
      range_type: form.range_kind === 'entity' ? form.range_type || null : null,
      cardinality: form.cardinality,
      inverse_id: form.inverse_id || null,
      rationale: form.rationale || null,
      proposed_by: 'weltmodell-ui',
    }),
    onSuccess: () => {
      setOk(true)
      setForm(empty)
      queryClient.invalidateQueries({ queryKey: ['proposals'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })

  return (
    <form className="panel" onSubmit={(e) => { e.preventDefault(); setOk(false); propose.mutate() }}>
      <span className="field-label">Neues Prädikat vorschlagen (geht ins Gate)</span>
      {ok && <OkBox>Proposal eingereicht — Review im Gate.</OkBox>}
      <div className="row">
        <Field label="Prädikat-ID">
          <input value={form.predicate_id} placeholder="member_of" onChange={(e) => setForm({ ...form, predicate_id: e.target.value })} />
        </Field>
        <Field label="Domain-Typ">
          <Combobox
            options={[{ id: '', label: '— (Interface nutzen)' },
              ...(vocab?.types ?? []).map((t) => ({ id: t.id, label: t.id }))]}
            value={form.domain_type}
            onChange={(id) => setForm({ ...form, domain_type: id })}
          />
        </Field>
        <Field label="oder Domain-Interface">
          <Combobox
            options={[{ id: '', label: '—' },
              ...(vocab?.interfaces ?? []).map((i) => ({ id: i.id, label: i.id }))]}
            value={form.domain_interface}
            onChange={(id) => setForm({ ...form, domain_interface: id })}
          />
        </Field>
      </div>
      <div className="row">
        <Field label="Range">
          <Combobox
            options={['entity', 'string', 'number', 'quantity', 'datetime', 'geo', 'json']
              .map((k) => ({ id: k, label: k }))}
            value={form.range_kind}
            onChange={(id) => setForm({ ...form, range_kind: id })}
          />
        </Field>
        {form.range_kind === 'entity' && (
          <Field label="Range-Typ">
            <Combobox
              options={[{ id: '', label: 'beliebige Entity' },
                ...(vocab?.types ?? []).map((t) => ({ id: t.id, label: t.id }))]}
              value={form.range_type}
              onChange={(id) => setForm({ ...form, range_type: id })}
            />
          </Field>
        )}
        <Field label="Kardinalität">
          <div className="seg" role="group" aria-label="Kardinalität">
            {['1:1', '1:n', 'n:m'].map((c) => (
              <button key={c} type="button" className={form.cardinality === c ? 'on' : undefined}
                onClick={() => setForm({ ...form, cardinality: c })}>{c}</button>
            ))}
          </div>
        </Field>
        <Field label="Invers zu (optional)">
          <Combobox
            options={[{ id: '', label: '—' },
              ...(vocab?.predicates ?? []).map((pr) => ({ id: pr.id, label: pr.id }))]}
            value={form.inverse_id}
            onChange={(id) => setForm({ ...form, inverse_id: id })}
          />
        </Field>
      </div>
      <Field label="Begründung">
        <input value={form.rationale} onChange={(e) => setForm({ ...form, rationale: e.target.value })} />
      </Field>
      <ErrorBox error={propose.error} />
      <button type="submit" disabled={!form.predicate_id || propose.isPending}>Prädikat vorschlagen</button>
    </form>
  )
}
