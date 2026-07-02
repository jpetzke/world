import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Predicate, ResolveResult, SearchHit, ValuePayload } from '../api/types'
import { EntityAutocomplete } from '../components/EntityAutocomplete'
import { SourcePicker, emptySourceDraft, ensureSource, type SourceDraft } from '../components/SourcePicker'
import { ValueEditor } from '../components/ValueEditor'
import { ErrorBox, Field, KindBadge, OkBox, PageHead, SimilarityBar } from '../components/bits'
import { useVocabulary } from '../hooks/useVocabulary'

export function CreatePage() {
  const [params] = useSearchParams()
  const [tab, setTab] = useState<'entity' | 'statement'>(
    params.get('statement_subject') ? 'statement' : 'entity',
  )
  return (
    <div className="page">
      <PageHead
        eyebrow="Manuelle Eingabe"
        title="Anlegen"
        sub="Beides läuft durch dasselbe Gate wie jeder Agent — Registry-Vokabular, Shape-Check, Provenance-Pflicht."
      />
      <div className="tabs">
        <button type="button" className={tab === 'entity' ? 'active' : ''} onClick={() => setTab('entity')}>
          Entity
        </button>
        <button type="button" className={tab === 'statement' ? 'active' : ''} onClick={() => setTab('statement')}>
          Statement
        </button>
      </div>
      {tab === 'entity' ? <EntityForm /> : <StatementForm />}
    </div>
  )
}

// --- Entity-Formular mit Live-Dedup ------------------------------------------

function EntityForm() {
  const { vocab, helpers } = useVocabulary()
  const queryClient = useQueryClient()
  const [typeId, setTypeId] = useState('Person')
  const [label, setLabel] = useState('')
  const [dedup, setDedup] = useState<ResolveResult | null>(null)
  const [created, setCreated] = useState<{ id: string; label: string } | null>(null)
  const debounce = useRef<number>(undefined)

  // Live-Dedup: „Meintest du …?" bevor ein Duplikat entsteht (§7.2)
  useEffect(() => {
    window.clearTimeout(debounce.current)
    setDedup(null)
    if (label.trim().length < 3) return
    debounce.current = window.setTimeout(() => {
      api.resolve({ type_id: typeId, label }).then(setDedup).catch(() => setDedup(null))
    }, 350)
    return () => window.clearTimeout(debounce.current)
  }, [label, typeId])

  const create = useMutation({
    mutationFn: () => api.createEntity({ type_id: typeId, label: label.trim() }),
    onSuccess: (entity) => {
      setCreated({ id: entity.id, label: label.trim() })
      setLabel('')
      setDedup(null)
      queryClient.invalidateQueries({ queryKey: ['stats'] })
      queryClient.invalidateQueries({ queryKey: ['entities'] })
    },
  })

  const sortedTypes = useMemo(
    () => (vocab?.types ?? [])
      .filter((t) => t.parent_id) // Wurzeltypen sind abstrakt
      .sort((a, b) => (a.kind + a.id).localeCompare(b.kind + b.id)),
    [vocab],
  )
  const candidates = dedup?.candidates ?? []

  return (
    <form
      className="panel"
      onSubmit={(e) => {
        e.preventDefault()
        create.mutate()
      }}
    >
      {created && (
        <OkBox>
          Angelegt: <Link to={`/entity/${created.id}`}>{created.label}</Link> —{' '}
          <Link to={`/create?statement_subject=${created.id}`}>gleich Statements anhängen?</Link>
        </OkBox>
      )}
      <div className="row">
        <Field label="Typ">
          <select value={typeId} onChange={(e) => setTypeId(e.target.value)}>
            {sortedTypes.map((t) => (
              <option key={t.id} value={t.id}>
                {t.kind === 'occurrent' ? '◆' : '●'} {t.id}
              </option>
            ))}
          </select>
        </Field>
        <div style={{ flex: 2 }}>
          <Field label="Label">
            <input
              value={label}
              placeholder="z. B. Jonas Petzke"
              onChange={(e) => setLabel(e.target.value)}
            />
          </Field>
        </div>
        <div style={{ flex: '0 0 auto' }}>
          <button type="submit" className="primary" disabled={!label.trim() || create.isPending}>
            Entity anlegen
          </button>
        </div>
      </div>
      <p className="muted small inline">
        <KindBadge kind={helpers?.kindOf(typeId)} typeId={typeId} />
        {helpers?.kindOf(typeId) === 'occurrent'
          ? 'Occurrent: passiert in einem Zeitfenster (Ereignis).'
          : 'Continuant: existiert durch die Zeit, hat Identität.'}
        {' '}Interfaces: {[...(helpers?.interfacesOf(typeId) ?? [])].join(', ') || '—'}
      </p>

      {candidates.length > 0 && (
        <div className="panel" style={{ borderColor: 'var(--warn)' }}>
          <span className="field-label">Meintest du …? (mögliche Duplikate)</span>
          {candidates.map((c) => (
            <div key={c.id} className="spread" style={{ padding: '4px 0' }}>
              <Link to={`/entity/${c.id}`}>{c.label}</Link>
              <SimilarityBar value={c.similarity} />
            </div>
          ))}
          <p className="muted small">Trifft keiner? Dann einfach anlegen.</p>
        </div>
      )}
      <ErrorBox error={create.error} />
    </form>
  )
}

// --- Statement-Formular --------------------------------------------------------

function StatementForm() {
  const [params] = useSearchParams()
  const { helpers } = useVocabulary()
  const queryClient = useQueryClient()

  const [subject, setSubject] = useState<SearchHit | null>(null)
  const [predicateId, setPredicateId] = useState('')
  const [value, setValue] = useState<ValuePayload | null>(null)
  const [qualifiers, setQualifiers] = useState<{ predicate_id: string; value: ValuePayload }[]>([])
  const [sourceDraft, setSourceDraft] = useState<SourceDraft>(emptySourceDraft)
  const [rank, setRank] = useState('normal')
  const [confidence, setConfidence] = useState(1.0)
  const [validFrom, setValidFrom] = useState('')
  const [validTo, setValidTo] = useState('')
  const [okStatement, setOkStatement] = useState<string | null>(null)

  // Subjekt aus ?statement_subject= vorbelegen
  useEffect(() => {
    const preset = params.get('statement_subject')
    if (preset && !subject) {
      api.entity(preset).then((view) =>
        setSubject({
          id: view.entity.id,
          label: view.entity.label,
          type_id: view.entity.type_id,
          similarity: null,
        }),
      ).catch(() => undefined)
    }
  }, [params, subject])

  const predicates: Predicate[] = useMemo(
    () => (subject && helpers ? helpers.predicatesFor(subject.type_id) : []),
    [subject, helpers],
  )
  const predicate = predicates.find((p) => p.id === predicateId) ?? null

  const submit = useMutation({
    mutationFn: async () => {
      if (!subject || !predicate || !value) throw new Error('Formular unvollständig')
      const sourceId = await ensureSource(sourceDraft)
      return api.createStatement({
        subject_id: subject.id,
        predicate_id: predicate.id,
        value,
        source_ids: [sourceId],
        rank,
        confidence,
        valid_from: validFrom || null,
        valid_to: validTo || null,
        qualifiers,
      })
    },
    onSuccess: (statement) => {
      setOkStatement(statement.id)
      setValue(null)
      setQualifiers([])
      queryClient.invalidateQueries({ queryKey: ['entity'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        setOkStatement(null)
        submit.mutate()
      }}
    >
      {okStatement && subject && (
        <OkBox>
          Statement geschrieben — <Link to={`/entity/${subject.id}`}>zu {subject.label ?? 'Entity'}</Link>
        </OkBox>
      )}

      <div className="panel">
        <Field label="Subjekt">
          <EntityAutocomplete
            selected={subject}
            onSelect={(hit) => {
              setSubject(hit)
              setPredicateId('')
              setValue(null)
            }}
          />
        </Field>

        {subject && (
          <>
            <Field label={`Prädikat (${predicates.length} passend zur Domain von ${subject.type_id})`}>
              <select
                value={predicateId}
                onChange={(e) => {
                  setPredicateId(e.target.value)
                  setValue(null)
                }}
              >
                <option value="">— wählen —</option>
                {predicates.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.id} → {p.range_kind}{p.range_type ? `(${p.range_type})` : ''}
                  </option>
                ))}
              </select>
            </Field>

            {predicate && (
              <ValueEditor
                key={predicate.id}
                rangeKind={predicate.range_kind}
                rangeType={predicate.range_type}
                value={value}
                onChange={setValue}
              />
            )}
          </>
        )}
      </div>

      {predicate && (
        <>
          <QualifierEditor qualifiers={qualifiers} onChange={setQualifiers} />
          <SourcePicker draft={sourceDraft} onChange={setSourceDraft} />

          <div className="panel">
            <div className="row">
              <Field label="Rank">
                <select value={rank} onChange={(e) => setRank(e.target.value)}>
                  <option value="normal">normal</option>
                  <option value="preferred">preferred</option>
                </select>
              </Field>
              <Field label={`Confidence — ${confidence.toFixed(2)}`}>
                <input
                  type="range" min={0} max={1} step={0.05}
                  value={confidence}
                  onChange={(e) => setConfidence(Number(e.target.value))}
                />
              </Field>
              <Field label="Gültig ab (valid_from)">
                <input type="datetime-local" value={validFrom} onChange={(e) => setValidFrom(e.target.value)} />
              </Field>
              <Field label="Gültig bis (valid_to)">
                <input type="datetime-local" value={validTo} onChange={(e) => setValidTo(e.target.value)} />
              </Field>
            </div>
          </div>
        </>
      )}

      <ErrorBox error={submit.error} />
      <button type="submit" className="primary" disabled={!subject || !predicate || !value || submit.isPending}>
        Statement schreiben
      </button>
    </form>
  )
}

// --- Qualifier -------------------------------------------------------------------

function QualifierEditor({ qualifiers, onChange }: {
  qualifiers: { predicate_id: string; value: ValuePayload }[]
  onChange: (q: { predicate_id: string; value: ValuePayload }[]) => void
}) {
  const { vocab } = useVocabulary()
  const [predicateId, setPredicateId] = useState('')
  const [value, setValue] = useState<ValuePayload | null>(null)
  const predicate = vocab?.predicates.find((p) => p.id === predicateId) ?? null
  // Qualifier tragen nur diese Wertarten (Schema von `qualifier`)
  const allowed = vocab?.predicates.filter((p) =>
    ['entity', 'string', 'number', 'datetime'].includes(p.range_kind),
  ) ?? []

  return (
    <div className="panel">
      <span className="field-label">Qualifier (verfeinern das Statement, §3)</span>
      {qualifiers.length > 0 && (
        <div className="inline" style={{ marginBottom: 10 }}>
          {qualifiers.map((q, i) => (
            <span key={i} className="qualifier">
              {q.predicate_id}: {JSON.stringify(Object.values(q.value)[1] ?? '')}
              <button
                type="button" className="ghost"
                style={{ padding: '0 4px' }}
                onClick={() => onChange(qualifiers.filter((_, j) => j !== i))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="row">
        <Field label="Prädikat">
          <select value={predicateId} onChange={(e) => { setPredicateId(e.target.value); setValue(null) }}>
            <option value="">— wählen —</option>
            {allowed.map((p) => <option key={p.id} value={p.id}>{p.id}</option>)}
          </select>
        </Field>
        <div style={{ flex: 2 }}>
          {predicate && (
            <ValueEditor
              key={predicate.id}
              rangeKind={predicate.range_kind}
              rangeType={predicate.range_type}
              value={value}
              onChange={setValue}
            />
          )}
        </div>
        <div style={{ flex: '0 0 auto' }}>
          <button
            type="button"
            disabled={!predicate || !value}
            onClick={() => {
              if (predicate && value) {
                onChange([...qualifiers, { predicate_id: predicate.id, value }])
                setPredicateId('')
                setValue(null)
              }
            }}
          >
            + Qualifier
          </button>
        </div>
      </div>
    </div>
  )
}
