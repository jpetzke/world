import { describe, expect, it } from 'vitest'
import type { Predicate, Vocabulary } from '../api/types'
import { buildHelpers } from '../hooks/useVocabulary'
import { groupOf } from './OccurrentFields'

const vocab: Vocabulary = {
  types: [
    { id: 'Agent', parent_id: null, kind: 'continuant', label: 'A', abstract: true, label_predicate: null, wikidata_qid: null },
    { id: 'Person', parent_id: 'Agent', kind: 'continuant', label: 'P', abstract: false, label_predicate: 'name', wikidata_qid: null },
    { id: 'Ort', parent_id: null, kind: 'continuant', label: 'Ort', abstract: false, label_predicate: 'name', wikidata_qid: null },
    { id: 'Stadt', parent_id: 'Ort', kind: 'continuant', label: 'Stadt', abstract: false, label_predicate: 'name', wikidata_qid: null },
    { id: 'Ereignis', parent_id: null, kind: 'occurrent', label: 'E', abstract: true, label_predicate: null, wikidata_qid: null },
  ],
  interfaces: [],
  implementations: [],
  predicates: [],
}

const pred = (over: Partial<Predicate>): Predicate => ({
  id: 'x', label: 'X', domain_type: 'Ereignis', domain_interface: null,
  range_kind: 'string', range_type: null, cardinality: '1:1', inverse_id: null,
  identifying: false, wikidata_pid: null, schema_org: null, ...over,
})

describe('groupOf — Registry-getriebene Wann/Wo/Wer-Gruppierung', () => {
  const helpers = buildHelpers(vocab)

  it('datetime → Wann', () => {
    expect(groupOf(pred({ range_kind: 'datetime' }), helpers)).toBe('Wann')
  })

  it('entity → Ort (auch Subtypen) → Wo', () => {
    expect(groupOf(pred({ range_kind: 'entity', range_type: 'Ort' }), helpers)).toBe('Wo')
    expect(groupOf(pred({ range_kind: 'entity', range_type: 'Stadt' }), helpers)).toBe('Wo')
  })

  it('entity → Agent → Wer', () => {
    expect(groupOf(pred({ range_kind: 'entity', range_type: 'Agent' }), helpers)).toBe('Wer')
    expect(groupOf(pred({ range_kind: 'entity', range_type: 'Person' }), helpers)).toBe('Wer')
  })

  it('string → Details', () => {
    expect(groupOf(pred({ range_kind: 'string' }), helpers)).toBe('Details')
  })

  it('geo/json/number → null (bleiben dem FactComposer)', () => {
    expect(groupOf(pred({ range_kind: 'geo' }), helpers)).toBeNull()
    expect(groupOf(pred({ range_kind: 'json' }), helpers)).toBeNull()
    expect(groupOf(pred({ range_kind: 'number' }), helpers)).toBeNull()
  })
})
