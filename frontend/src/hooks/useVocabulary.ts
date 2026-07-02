import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { api } from '../api/client'
import type { Kind, Predicate, Vocabulary } from '../api/types'

export interface VocabHelpers {
  ancestors: (typeId: string) => string[]
  interfacesOf: (typeId: string) => Set<string>
  /** Prädikate, deren domain zum Subjekt-Typ passt (Typ-Hierarchie + Interfaces). */
  predicatesFor: (subjectTypeId: string) => Predicate[]
  kindOf: (typeId: string) => Kind | undefined
}

export function buildHelpers(vocab: Vocabulary): VocabHelpers {
  const typeMap = new Map(vocab.types.map((t) => [t.id, t]))
  const implMap = new Map<string, string[]>()
  for (const impl of vocab.implementations) {
    const list = implMap.get(impl.type_id) ?? []
    list.push(impl.interface_id)
    implMap.set(impl.type_id, list)
  }

  const ancestors = (typeId: string): string[] => {
    const chain: string[] = []
    let current = typeMap.get(typeId)
    while (current && !chain.includes(current.id)) {
      chain.push(current.id)
      current = current.parent_id ? typeMap.get(current.parent_id) : undefined
    }
    return chain
  }

  const interfacesOf = (typeId: string): Set<string> => {
    const result = new Set<string>()
    for (const ancestor of ancestors(typeId)) {
      for (const iface of implMap.get(ancestor) ?? []) result.add(iface)
    }
    return result
  }

  const predicatesFor = (subjectTypeId: string): Predicate[] => {
    const chain = ancestors(subjectTypeId)
    const ifaces = interfacesOf(subjectTypeId)
    return vocab.predicates.filter((p) => {
      if (!p.domain_type && !p.domain_interface) return true
      if (p.domain_type && chain.includes(p.domain_type)) return true
      if (p.domain_interface && ifaces.has(p.domain_interface)) return true
      return false
    })
  }

  return {
    ancestors,
    interfacesOf,
    predicatesFor,
    kindOf: (typeId) => typeMap.get(typeId)?.kind,
  }
}

export function useVocabulary() {
  const query = useQuery({
    queryKey: ['vocabulary'],
    queryFn: api.vocabulary,
    staleTime: 60_000,
  })
  const helpers = useMemo(
    () => (query.data ? buildHelpers(query.data) : null),
    [query.data],
  )
  return { vocab: query.data, helpers, isLoading: query.isLoading }
}
