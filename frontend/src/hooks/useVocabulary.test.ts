import { describe, expect, it } from 'vitest'
import type { Vocabulary } from '../api/types'
import { buildHelpers } from './useVocabulary'

const vocab: Vocabulary = {
  types: [
    { id: 'Continuant', parent_id: null, kind: 'continuant', label: 'C', abstract: true, label_predicate: null, wikidata_qid: null },
    { id: 'Agent', parent_id: 'Continuant', kind: 'continuant', label: 'A', abstract: true, label_predicate: null, wikidata_qid: null },
    { id: 'Person', parent_id: 'Agent', kind: 'continuant', label: 'P', abstract: false, label_predicate: 'name', wikidata_qid: null },
    { id: 'Occurrent', parent_id: null, kind: 'occurrent', label: 'O', abstract: true, label_predicate: null, wikidata_qid: null },
    { id: 'Mention', parent_id: 'Occurrent', kind: 'occurrent', label: 'M', abstract: false, label_predicate: null, wikidata_qid: null },
  ],
  interfaces: [{ id: 'Nameable', label: 'N' }],
  implementations: [{ type_id: 'Person', interface_id: 'Nameable' }],
  predicates: [
    { id: 'knows', label: 'kennt', domain_type: 'Person', domain_interface: null, range_kind: 'entity', range_type: 'Person', cardinality: 'n:m', inverse_id: 'knows', identifying: false, wikidata_pid: null, schema_org: null },
    { id: 'invests_in', label: 'investiert', domain_type: 'Agent', domain_interface: null, range_kind: 'entity', range_type: null, cardinality: 'n:m', inverse_id: null, identifying: false, wikidata_pid: null, schema_org: null },
    { id: 'name', label: 'Name', domain_type: null, domain_interface: 'Nameable', range_kind: 'string', range_type: null, cardinality: '1:n', inverse_id: null, identifying: false, wikidata_pid: null, schema_org: null },
    { id: 'text', label: 'Text', domain_type: 'Mention', domain_interface: null, range_kind: 'string', range_type: null, cardinality: '1:1', inverse_id: null, identifying: false, wikidata_pid: null, schema_org: null },
  ],
}

describe('buildHelpers — Domain-Filterung wie das Backend (§2.3)', () => {
  const helpers = buildHelpers(vocab)

  it('ancestors: Person → Agent → Continuant', () => {
    expect(helpers.ancestors('Person')).toEqual(['Person', 'Agent', 'Continuant'])
  })

  it('Person darf: eigener Domain-Typ, geerbter Domain-Typ, Interface-Domain', () => {
    const ids = helpers.predicatesFor('Person').map((p) => p.id)
    expect(ids).toContain('knows')       // domain Person
    expect(ids).toContain('invests_in')  // domain Agent (vererbt)
    expect(ids).toContain('name')        // domain-Interface Nameable
    expect(ids).not.toContain('text')    // domain Mention
  })

  it('Mention darf text, aber kein knows', () => {
    const ids = helpers.predicatesFor('Mention').map((p) => p.id)
    expect(ids).toContain('text')
    expect(ids).not.toContain('knows')
    expect(ids).not.toContain('name')
  })

  it('kindOf unterscheidet den Top-Split', () => {
    expect(helpers.kindOf('Person')).toBe('continuant')
    expect(helpers.kindOf('Mention')).toBe('occurrent')
  })
})
