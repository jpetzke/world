import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ApiError, api } from '../api/client'
import type { Kind, ResolveResult, SearchHit, ValuePayload } from '../api/types'
import { Combobox } from '../components/Combobox'
import { EntityAutocomplete } from '../components/EntityAutocomplete'
import { SourcePicker, ensureSource, type SourceDraft } from '../components/SourcePicker'
import { ValueEditor } from '../components/ValueEditor'
import { ErrorBox, Field, OkBox, PageHead, SimilarityBar } from '../components/bits'
import { ArrowLeft, ChevronDown, ChevronRight, Close, Plus } from '../components/icons'
import { useVocabulary } from '../hooks/useVocabulary'

// Ein Fakt in der Warteschlange, bevor er (mit gemeinsamer Quelle) geschrieben wird.
interface FactDraft {
  predicate_id: string
  value: ValuePayload
  display: string
  rank: string
  confidence: number
  valid_from: string | null
  valid_to: string | null
  qualifiers: { predicate_id: string; value: ValuePayload }[]
}

// Eine Quelle deckt eine ganze Anlege-Session (Provenance-Pflicht, Invariante 3).
const wizardSource: SourceDraft = {
  mode: 'new', sourceId: null, url: '', activity: 'manual:ui', agent: 'weltmodell-ui',
}

const kindText = (k?: Kind) =>
  k === 'occurrent'
    ? 'Occurrent — passiert in einem Zeitfenster (Ereignis).'
    : 'Continuant — existiert durch die Zeit, hat Identität.'

function valueLabel(v: ValuePayload): string {
  switch (v.type) {
    case 'string': return v.text
    case 'number': return String(v.number)
    case 'quantity': return `${v.number} ${v.unit}`
    case 'datetime': return v.datetime
    case 'entity': return v.object_id.slice(0, 8)
    case 'geo': return `${v.lat}, ${v.lon}`
    case 'json': return 'JSON'
  }
}

function errText(e: unknown): string {
  return e instanceof ApiError ? e.problems.join('; ') : String(e)
}

// Ein Fakt-Draft (auch der Primär-Bezeichner) → Statement mit gemeinsamer Quelle.
function writeFact(subjectId: string, sourceId: string, f: FactDraft) {
  return api.createStatement({
    subject_id: subjectId,
    source_ids: [sourceId],
    predicate_id: f.predicate_id,
    value: f.value,
    rank: f.rank,
    confidence: f.confidence,
    valid_from: f.valid_from,
    valid_to: f.valid_to,
    qualifiers: f.qualifiers,
  })
}

// FactDraft für ein einfaches string-Prädikat (z. B. der Primär-Bezeichner name/handle).
function stringFact(predicateId: string, text: string): FactDraft {
  return {
    predicate_id: predicateId, value: { type: 'string', text }, display: text,
    rank: 'normal', confidence: 1, valid_from: null, valid_to: null, qualifiers: [],
  }
}

// --- Seite: erst Auswahl, dann der passende Wizard --------------------------

export function CreatePage() {
  const [params] = useSearchParams()
  const presetSubject = params.get('statement_subject')
  // Deep-Link „+ Statement" überspringt die Auswahl und landet direkt im Fakt-Wizard.
  const [choice, setChoice] = useState<{ kind: 'type'; typeId: string } | { kind: 'statement' } | null>(
    presetSubject ? { kind: 'statement' } : null,
  )

  return (
    <div className="page">
      <PageHead eyebrow="Manuelle Eingabe" title="Anlegen" />
      {choice === null && (
        <SelectionGrid
          onPickType={(typeId) => setChoice({ kind: 'type', typeId })}
          onPickStatement={() => setChoice({ kind: 'statement' })}
        />
      )}
      {choice?.kind === 'type' && (
        <EntityWizard typeId={choice.typeId} onBack={() => setChoice(null)} />
      )}
      {choice?.kind === 'statement' && (
        <StatementWizard presetSubjectId={presetSubject} onBack={() => setChoice(null)} />
      )}
    </div>
  )
}

// --- Schritt 1: Was möchtest du anlegen? ------------------------------------

function SelectionGrid({ onPickType, onPickStatement }: {
  onPickType: (typeId: string) => void
  onPickStatement: () => void
}) {
  const { vocab } = useVocabulary()
  const types = useMemo(
    () => (vocab?.types ?? []).filter((t) => !t.abstract).slice().sort(
      (a, b) => (a.kind === b.kind ? 0 : a.kind === 'continuant' ? -1 : 1)
        || (a.label ?? a.id).localeCompare(b.label ?? b.id),
    ),
    [vocab],
  )

  return (
    <>
      <div className="choice-eyebrow">Was möchtest du anlegen?</div>
      <div className="choice-grid">
        {types.map((t) => (
          <button type="button" key={t.id} className={`choice-card ${t.kind}`} onClick={() => onPickType(t.id)}>
            <span className={`choice-glyph ${t.kind}`} aria-hidden />
            <span className="choice-title">{t.label ?? t.id}</span>
            <span className="choice-desc">
              {t.kind === 'occurrent' ? 'Ereignis — passiert in der Zeit' : 'Ding — besteht durch die Zeit'}
            </span>
          </button>
        ))}
      </div>

      <div className="choice-sep"><span>oder</span></div>

      <button type="button" className="choice-card fact wide" onClick={onPickStatement}>
        <span className="choice-glyph fact" aria-hidden>
          <svg viewBox="0 0 46 24">
            <line x1="2" y1="12" x2="26" y2="12" />
            <path d="M20 6 L27 12 L20 18" />
            <circle cx="36" cy="12" r="7" />
          </svg>
        </span>
        <span className="choice-body">
          <span className="choice-title">Fakt zu einer bestehenden Entity</span>
          <span className="choice-desc">Eine Aussage an eine schon vorhandene Entity hängen</span>
        </span>
      </button>
    </>
  )
}

// --- Schritt 2a: Entity-Wizard ----------------------------------------------

function EntityWizard({ typeId, onBack }: { typeId: string; onBack: () => void }) {
  const { vocab, helpers } = useVocabulary()
  const queryClient = useQueryClient()
  const [primary, setPrimary] = useState('')
  const [facts, setFacts] = useState<FactDraft[]>([])
  const [source, setSource] = useState<SourceDraft>(wizardSource)
  const [dedup, setDedup] = useState<ResolveResult | null>(null)
  const [result, setResult] = useState<{ id: string; label: string; written: number; rejected: string[] } | null>(null)
  const debounce = useRef<number>(undefined)

  const kind = helpers?.kindOf(typeId)
  const typeLabel = typeId
  const interfaces = useMemo(() => [...(helpers?.interfacesOf(typeId) ?? [])], [helpers, typeId])
  // Typ-abhängiger Primär-Bezeichner: Person→name, Account→handle (§label_predicate)
  const type = vocab?.types.find((t) => t.id === typeId)
  const labelPred = vocab?.predicates.find((p) => p.id === type?.label_predicate) ?? null
  const primaryLabel = labelPred?.label ?? 'Name'

  // Live-Dedup: „Meintest du …?" bevor ein Duplikat entsteht (§7.2)
  useEffect(() => {
    window.clearTimeout(debounce.current)
    setDedup(null)
    if (primary.trim().length < 3) return
    debounce.current = window.setTimeout(() => {
      api.resolve({ type_id: typeId, label: primary }).then(setDedup).catch(() => setDedup(null))
    }, 350)
    return () => window.clearTimeout(debounce.current)
  }, [primary, typeId])

  const create = useMutation({
    mutationFn: async () => {
      const entity = await api.createEntity({ type_id: typeId, label: primary.trim() })
      // Primär-Bezeichner als echtes Statement (SoT), nicht nur label-Cache.
      const writes = labelPred ? [stringFact(labelPred.id, primary.trim()), ...facts] : facts
      let written = 0
      const rejected: string[] = []
      if (writes.length) {
        const sourceId = await ensureSource(source)
        for (const f of writes) {
          try { await writeFact(entity.id, sourceId, f); written++ }
          catch (e) { rejected.push(`${f.predicate_id} — ${errText(e)}`) }
        }
      }
      return { id: entity.id, label: primary.trim(), written, rejected }
    },
    onSuccess: (r) => {
      setResult(r)
      setPrimary(''); setFacts([]); setDedup(null)
      queryClient.invalidateQueries({ queryKey: ['stats'] })
      queryClient.invalidateQueries({ queryKey: ['entities'] })
    },
  })

  const candidates = dedup?.candidates ?? []

  if (result) {
    return (
      <div className="panel">
        <OkBox>
          Angelegt: <Link to={`/entity/${result.id}`}>{result.label}</Link>
          {result.written > 0 && ` · ${result.written} Fakt${result.written === 1 ? '' : 'en'} geschrieben`}
        </OkBox>
        {result.rejected.length > 0 && (
          <div className="error-box" role="alert">
            <strong>Vom Gate abgelehnt</strong>
            <ul>{result.rejected.map((r, i) => <li key={i}>{r}</li>)}</ul>
          </div>
        )}
        <div className="inline" style={{ marginTop: 12 }}>
          <button type="button" className="primary" onClick={() => setResult(null)}>Noch eine anlegen</button>
          <button type="button" className="ghost" onClick={onBack}>Zur Auswahl</button>
        </div>
      </div>
    )
  }

  return (
    <form
      className="wizard"
      onSubmit={(e) => { e.preventDefault(); create.mutate() }}
    >
      <button type="button" className="backlink" onClick={onBack}><ArrowLeft /> Alle Typen</button>

      <div className="wizard-head">
        <span className={`choice-glyph ${kind} lg`} aria-hidden />
        <div>
          <div className="eyebrow">Neue Entity</div>
          <h1>{typeLabel}</h1>
          <p className="wizard-desc">{kindText(kind)}</p>
          {interfaces.length > 0 && (
            <div className="inline" style={{ marginTop: 8 }}>
              <span className="field-label" style={{ margin: 0 }}>Fähigkeiten</span>
              {interfaces.map((i) => <span key={i} className="chip">{i}</span>)}
            </div>
          )}
        </div>
      </div>

      <div className="panel name-field">
        <Field label={primaryLabel}>
          <input
            autoFocus
            value={primary}
            placeholder={labelPred?.id === 'handle'
              ? 'z. B. alice_wonderful (ohne @)'
              : `${primaryLabel} der ${typeLabel}`}
            onChange={(e) => setPrimary(e.target.value)}
          />
        </Field>
        {candidates.length > 0 && (
          <div className="dedup">
            <span className="field-label">Meintest du …? (mögliche Duplikate)</span>
            {candidates.map((c) => (
              <div key={c.id} className="spread" style={{ padding: '3px 0' }}>
                <Link to={`/entity/${c.id}`}>{c.label}</Link>
                <SimilarityBar value={c.similarity} />
              </div>
            ))}
            <p className="muted small">Trifft keiner? Dann einfach anlegen.</p>
          </div>
        )}
      </div>

      <FactComposer subjectTypeId={typeId} facts={facts} onChange={setFacts} exclude={labelPred?.id} />
      <SourceBar draft={source} onChange={setSource} />

      <ErrorBox error={create.error} />
      <button type="submit" className="primary big" disabled={!primary.trim() || create.isPending}>
        {facts.length > 0 ? `Anlegen · ${facts.length} Fakt${facts.length === 1 ? '' : 'en'}` : 'Anlegen'}
      </button>
    </form>
  )
}

// --- Schritt 2b: Fakt zu bestehender Entity ---------------------------------

function StatementWizard({ presetSubjectId, onBack }: { presetSubjectId: string | null; onBack: () => void }) {
  const queryClient = useQueryClient()
  const [subject, setSubject] = useState<SearchHit | null>(null)
  const [facts, setFacts] = useState<FactDraft[]>([])
  const [source, setSource] = useState<SourceDraft>(wizardSource)
  const [result, setResult] = useState<{ written: number; rejected: string[] } | null>(null)

  useEffect(() => {
    if (presetSubjectId && !subject) {
      api.entity(presetSubjectId).then((view) =>
        setSubject({ id: view.entity.id, label: view.entity.label, type_id: view.entity.type_id, similarity: null }),
      ).catch(() => undefined)
    }
  }, [presetSubjectId, subject])

  const write = useMutation({
    mutationFn: async () => {
      if (!subject) throw new Error('Subjekt fehlt')
      const sourceId = await ensureSource(source)
      let written = 0
      const rejected: string[] = []
      for (const f of facts) {
        try { await writeFact(subject.id, sourceId, f); written++ }
        catch (e) { rejected.push(`${f.predicate_id} — ${errText(e)}`) }
      }
      return { written, rejected }
    },
    onSuccess: (r) => {
      setResult(r)
      setFacts([])
      queryClient.invalidateQueries({ queryKey: ['entity'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })

  if (result && subject) {
    return (
      <div className="panel">
        <OkBox>
          {result.written} Fakt{result.written === 1 ? '' : 'en'} geschrieben —{' '}
          <Link to={`/entity/${subject.id}`}>zu {subject.label ?? 'Entity'}</Link>
        </OkBox>
        {result.rejected.length > 0 && (
          <div className="error-box" role="alert">
            <strong>Vom Gate abgelehnt</strong>
            <ul>{result.rejected.map((r, i) => <li key={i}>{r}</li>)}</ul>
          </div>
        )}
        <div className="inline" style={{ marginTop: 12 }}>
          <button type="button" className="primary" onClick={() => setResult(null)}>Weitere Fakten</button>
          <button type="button" className="ghost" onClick={onBack}>Zur Auswahl</button>
        </div>
      </div>
    )
  }

  return (
    <form onSubmit={(e) => { e.preventDefault(); write.mutate() }}>
      <button type="button" className="backlink" onClick={onBack}><ArrowLeft /> Zur Auswahl</button>

      <div className="wizard-head">
        <span className="choice-glyph fact lg" aria-hidden>
          <svg viewBox="0 0 46 24">
            <line x1="2" y1="12" x2="26" y2="12" />
            <path d="M20 6 L27 12 L20 18" />
            <circle cx="36" cy="12" r="7" />
          </svg>
        </span>
        <div>
          <div className="eyebrow">Fakt zu bestehender Entity</div>
          <h1>{subject ? subject.label ?? subject.id.slice(0, 8) : 'Subjekt wählen'}</h1>
          {subject && <p className="wizard-desc">Typ: {subject.type_id}</p>}
        </div>
      </div>

      <div className="panel">
        <Field label="Subjekt">
          <EntityAutocomplete
            selected={subject}
            onSelect={(hit) => { setSubject(hit); setFacts([]) }}
          />
        </Field>
      </div>

      {subject && (
        <>
          <FactComposer subjectTypeId={subject.type_id} facts={facts} onChange={setFacts} />
          {facts.length > 0 && <SourceBar draft={source} onChange={setSource} />}
          <ErrorBox error={write.error} />
          <button type="submit" className="primary big" disabled={facts.length === 0 || write.isPending}>
            {facts.length > 0 ? `Schreiben · ${facts.length} Fakt${facts.length === 1 ? '' : 'en'}` : 'Fakt hinzufügen'}
          </button>
        </>
      )}
    </form>
  )
}

// --- Fakt-Editor: Liste + Schnelleingabe mit „mehr" -------------------------

function FactComposer({ subjectTypeId, facts, onChange, exclude }: {
  subjectTypeId: string
  facts: FactDraft[]
  onChange: (facts: FactDraft[]) => void
  /** Prädikat, das schon das Primärfeld belegt (z. B. handle) — nicht doppelt anbieten. */
  exclude?: string
}) {
  const { helpers } = useVocabulary()
  const predicates = useMemo(
    () => (helpers ? helpers.predicatesFor(subjectTypeId) : []).filter((p) => p.id !== exclude),
    [helpers, subjectTypeId, exclude],
  )
  const byId = (id: string) => predicates.find((p) => p.id === id) ?? null

  const [predicateId, setPredicateId] = useState('')
  const [value, setValue] = useState<ValuePayload | null>(null)
  const [hitLabel, setHitLabel] = useState<string | null>(null)
  const [more, setMore] = useState(false)
  const [rank, setRank] = useState('normal')
  const [confidence, setConfidence] = useState(1)
  const [validFrom, setValidFrom] = useState('')
  const [validTo, setValidTo] = useState('')
  const [qualifiers, setQualifiers] = useState<{ predicate_id: string; value: ValuePayload }[]>([])
  const predicate = byId(predicateId)

  const reset = () => {
    setPredicateId(''); setValue(null); setHitLabel(null); setMore(false)
    setRank('normal'); setConfidence(1); setValidFrom(''); setValidTo(''); setQualifiers([])
  }

  const add = () => {
    if (!predicate || !value) return
    const display = value.type === 'entity' ? (hitLabel ?? valueLabel(value)) : valueLabel(value)
    onChange([...facts, {
      predicate_id: predicate.id, value, display, rank, confidence,
      valid_from: validFrom || null, valid_to: validTo || null, qualifiers,
    }])
    reset()
  }

  return (
    <div className="panel fact-composer">
      <span className="field-label">Fakten {facts.length === 0 && <span className="muted"> · optional</span>}</span>

      {facts.length > 0 && (
        <div className="fact-list">
          {facts.map((f, i) => (
            <div key={i} className="fact-row">
              <span className="predicate">{byId(f.predicate_id)?.label ?? f.predicate_id}</span>
              <span className="fact-val">{f.display}</span>
              {f.qualifiers.length > 0 && <span className="muted small">+{f.qualifiers.length} Qualifier</span>}
              <button type="button" className="ghost icon-btn sm" aria-label="Fakt entfernen"
                onClick={() => onChange(facts.filter((_, j) => j !== i))}><Close /></button>
            </div>
          ))}
        </div>
      )}

      <div className="fact-add">
        <div className="row">
          <Field label="Prädikat">
            <Combobox
              value={predicateId}
              onChange={(id) => { setPredicateId(id); setValue(null) }}
              options={predicates.map((p) => ({
                id: p.id,
                label: `${p.label}${p.range_type ? ` → ${p.range_type}` : ''}`,
              }))}
            />
          </Field>
          {predicate && (
            <div style={{ flex: 2 }}>
              <ValueEditor
                key={predicate.id}
                rangeKind={predicate.range_kind}
                rangeType={predicate.range_type}
                value={value}
                onChange={setValue}
                onHit={(hit) => setHitLabel(hit?.label ?? null)}
              />
            </div>
          )}
          <div style={{ flex: '0 0 auto' }}>
            <button type="button" className="icon-btn" aria-label="Fakt hinzufügen"
              disabled={!predicate || !value} onClick={add}><Plus /></button>
          </div>
        </div>

        {predicate && (
          <>
            <button type="button" className="more-toggle" onClick={() => setMore(!more)}>
              {more ? <ChevronDown /> : <ChevronRight />} mehr — Rank, Konfidenz, Gültigkeit, Qualifier
            </button>
            {more && (
              <div className="fact-more">
                <div className="row">
                  <Field label="Rank">
                    <select value={rank} onChange={(e) => setRank(e.target.value)}>
                      <option value="normal">normal</option>
                      <option value="preferred">preferred</option>
                    </select>
                  </Field>
                  <Field label={`Konfidenz — ${confidence.toFixed(2)}`}>
                    <input type="range" min={0} max={1} step={0.05} value={confidence}
                      onChange={(e) => setConfidence(Number(e.target.value))} />
                  </Field>
                  <Field label="Gültig ab">
                    <input type="datetime-local" value={validFrom} onChange={(e) => setValidFrom(e.target.value)} />
                  </Field>
                  <Field label="Gültig bis">
                    <input type="datetime-local" value={validTo} onChange={(e) => setValidTo(e.target.value)} />
                  </Field>
                </div>
                <QualifierEditor qualifiers={qualifiers} onChange={setQualifiers} />
              </div>
            )}
          </>
        )}
      </div>
    </div>
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
    <div className="qualifier-block">
      <span className="field-label">Qualifier (verfeinern den Fakt, §3)</span>
      {qualifiers.length > 0 && (
        <div className="inline" style={{ marginBottom: 10 }}>
          {qualifiers.map((q, i) => (
            <span key={i} className="qualifier">
              {q.predicate_id}: {JSON.stringify(Object.values(q.value)[1] ?? '')}
              <button type="button" className="ghost icon-btn sm" aria-label="Qualifier entfernen"
                onClick={() => onChange(qualifiers.filter((_, j) => j !== i))}><Close /></button>
            </span>
          ))}
        </div>
      )}
      <div className="row">
        <Field label="Prädikat">
          <Combobox
            value={predicateId}
            onChange={(id) => { setPredicateId(id); setValue(null) }}
            options={allowed.map((p) => ({ id: p.id, label: p.id }))}
          />
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
          <button type="button" className="icon-text" disabled={!predicate || !value}
            onClick={() => {
              if (predicate && value) {
                onChange([...qualifiers, { predicate_id: predicate.id, value }])
                setPredicateId(''); setValue(null)
              }
            }}><Plus /> Qualifier</button>
        </div>
      </div>
    </div>
  )
}

// --- Quelle: eingeklappt, eine pro Session ----------------------------------

function SourceBar({ draft, onChange }: { draft: SourceDraft; onChange: (d: SourceDraft) => void }) {
  const [open, setOpen] = useState(false)
  const summary = draft.mode === 'existing'
    ? (draft.sourceId ? 'gewählte Quelle' : 'keine gewählt — bitte wählen')
    : (draft.activity === 'manual:ui' ? 'Manuelle Eingabe' : draft.activity || 'neue Quelle')

  return (
    <div className="source-bar">
      <button type="button" className="source-toggle" onClick={() => setOpen(!open)}>
        <span className="inline">
          <span className="field-label" style={{ margin: 0 }}>Quelle</span>
          <span className="kind continuant">{summary}</span>
        </span>
        <span className="muted small icon-text">{open ? 'schließen' : 'ändern'}{open ? <ChevronDown /> : <ChevronRight />}</span>
      </button>
      {open && <SourcePicker draft={draft} onChange={onChange} />}
    </div>
  )
}
