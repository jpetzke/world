# Verfassung des Weltmodells

Du schreibst in ein privates Weltmodell: einen reifizierten Statement-Store auf
genau einer Source of Truth (PostgreSQL). Entities und Events aus beliebigen
Domänen liegen in EINEM Graphen; der Payoff sind Cross-Domain-Queries über eine
Struktur. Das Fundament wird nie redesignt: Jede Erweiterung ist ein
Registry-Eintrag (Daten), nie eine Schema-Änderung. Das Meta-Modell ist
importiert (BFO, Wikidata-Statements, PROV-O, schema.org) — erfinde nie ein
eigenes dazu.

## Die 5 Invarianten — unter keinen Umständen brechen

1. **Eine Source of Truth.** Alles Ableitbare (Label-Cache, Embeddings,
   Timeline) ist jederzeit neu berechenbar. Lege nie eine zweite Wahrheit an.
2. **Kein Write am Registry-Vokabular vorbei.** Du mappst auf existierendes
   Vokabular (`welt_vocabulary`) oder emittierst ein Proposal
   (`welt_propose_type` / `welt_propose_predicate`) — du schreibst nie frei.
   Approve nur nach echter Prüfung gegen diese Verfassung.
3. **Kein Fakt ohne Provenance.** Jedes Statement braucht ≥1 Quelle
   (`welt_create_source` zuerst, dann `source_ids`). Confidence < 1.0 ist der
   Normalfall. Nur nackte Entity-Anker brauchen keine Quelle.
4. **Überschreibe nie — supersede/deprecate.** Ändert sich die WELT oder kommt
   eine bessere Behauptung: `welt_deprecate_statement` bzw. neuer Commit
   (+ `welt_set_rank`); Widersprüche koexistieren via Rank + Confidence +
   Bitemporalität, Historie bleibt. Kardinalitätskonflikt ist ein Flag, kein
   Fehler. Einzige Ausnahme ist das ERRATUM für echte Fehler im Record —
   Dinge, die so nie hätten existieren dürfen, niemals um einen Zeitverlauf
   zu modellieren: `welt_fix_statement` korrigiert/löscht ein Statement in
   place; `welt_fix_entity` löscht einen versehentlich angelegten Anker, aber
   nur ohne aktive (nicht-deprecated) Statements — benutzte Dubletten gehören
   zu `welt_merge_entities`. Beide verlangen einen reason (Audit).
5. **Continuant/Occurrent-Split ist heilig.** Existiert es durch die Zeit mit
   Identität → Continuant. Passiert es in einem Zeitfenster → Occurrent
   (`Ereignis`-Ast). Nie vermischen.

## Entscheidungsbaum für jedes neue „Ding" (in DIESER Reihenfolge)

1. **Ableitbar aus vorhandenen Statements?** (Kontoerstellung =
   `erstellt_am`-Statement.) → Nicht materialisieren.
2. **Binäre Beziehung, ggf. mit Zeit?** (Follow, Like, works_at.) →
   **Statement** mit Valid-Time + Qualifiern. Keine Entity, kein Event.
3. **Persistiert mit Identität, sammelt selbst Statements?** (Person, Account,
   Post, Ort.) → **Continuant.** Posts/Kommentare sind Continuants; der
   Publikationszeitpunkt ist ein `veröffentlicht_am`-Statement; Kommentar =
   Post mit `antwort_auf`.
4. **Passiert es — Zeitfenster, mehrere Teilnehmer in Rollen?** (Wahl,
   Kontosperrung, Krieg.) → **Occurrent** im `Ereignis`-Ast; erbt
   `beginn`/`ende`/`ort`.

Reifiziere eine Beziehung erst zur Entity, wenn sie n-är ist — wenn Qualifier
nicht mehr reichen.

**Ereigniszeit ≠ Behauptungszeit:** `beginn`/`ende` sind eigene
datetime-Statements (mit Provenance). `valid_from`/`valid_to` sagen, wann die
*Behauptung* gilt. Nie verwechseln.

## Prädikat- und Typ-Design (für Proposals und Approves)

- **Prädikate so hoch wie möglich aufhängen:** Domain auf den abstraktesten
  sinnvollen Typ oder ein Interface (`beginn` auf `Ereignis`, `name` auf
  `Nameable`). Subtypen erben gratis.
- **Scharfe Rollen-Prädikate** (`kandidat`, `gewinner`) statt generischem
  `participant` + Rollen-Qualifier. Aber keine Explosion: Verfeinerung
  (`role`) ist Qualifier-Job, zeitliche Gültigkeit (seit/bis) ist
  Valid-Time-Job (valid_from/valid_to) — `works_at` + Qualifier, nie
  `works_at_as_werkstudent`.
- Pflicht am Prädikat: Domain, range_kind (+ range_type bei `entity`),
  Cardinality. Inverse und `wikidata_pid`/`schema_org` mitgeben, wo existent.
- Range eng, aber abstrakt bei mehreren legitimen Subtypen (`teilnehmer` →
  `Agent`, nicht `Person`).
- Typen: `kind` muss zum Parent passen; in vorhandene Äste hängen. Interfaces
  aus vorhandenen komponieren (`Nameable`, `Locatable`, `Temporal`, …).
  `label_predicate` setzen, wo der Typ einen Anzeige-Bezeichner hat.
- **Wurzeltypen sind die Ausnahme:** `parent_id` darf im Proposal fehlen, wenn
  ein Ding in keinen vorhandenen Ast gehört (Vorbild `Wertpapier`). Der Approve
  validiert dann nur das `kind`-Etikett — die Begründung, warum kein Ast passt,
  gehört in die rationale.
- Im Typ-Proposal proposebar: `label_predicate` (muss existieren und
  domain-kompatibel zum neuen Typ sein) und `abstract` (true = Typ bündelt
  einen Ast und ist nicht instanziierbar; `welt_create_entity` nennt im
  Fehlertext die konkreten Subtypen).
- Jeder aus Quellen befüllte Typ braucht ≥1 `identifying`-Prädikat (harter
  Dedup-Key wie `account_uri`, `email`) — sonst Duplikate aus jeder Quelle.
- **identifying-Regeln:** `identifying=true` erfordert `range_kind='string'`
  und `cardinality='1:1'` (Stufe-1-Resolve matcht exakt auf den Textwert).
  Die DB erzwingt Eindeutigkeit pro identifying-Key (partieller Unique-Index
  auf aktuellen, nicht-deprecated Statements); Approve und Migration lehnen
  ab, wenn Bestandsdaten Dubletten haben — kuratieren statt still löschen.
  Ein Commit desselben identifying-Werts auf DIESELBE Entity re-bestätigt das
  bestehende Statement (neue Reference, Flag `reconfirmed`) statt zu
  duplizieren; derselbe Wert auf einer anderen Entity ist ein Fehler.

## Arbeitsweise mit den Tools

- **Erst auflösen, dann anlegen:** vor `welt_create_entity` immer
  `welt_resolve` (deterministische Keys, dann Vektor-Kandidaten) und/oder
  `welt_search`. Duplikate sind teurer als ein Lookup.
- **Snapshot-Philosophie:** Quellen sind unvollständig. Bekanntes wird
  re-bestätigt (neue Reference), nicht dupliziert. Abwesenheit in einem
  Snapshot ist KEIN Gegenbeweis (kein implizites Unfollow).
- **Merge statt Zweitanlage:** erkannte Dubletten mit `welt_merge_entities`
  verlustfrei zusammenführen (Provenance beider Seiten bleibt).
- **Bulk bevorzugen:** mehrere Anker/Fakten auf einmal immer per
  `welt_create_entities` / `welt_commit_statements` (ein Roundtrip) statt
  vieler Einzelaufrufe — schneller und atomar.
- Statement-Werte sind polymorph (`value.type`):
  `entity` (`object_id`) · `string` (`text`) · `number` (`number`) ·
  `quantity` (`number`+`unit`) · `datetime` (`datetime`, ISO) ·
  `geo` (`lat`+`lon`) · `json` (`json`).
- **Qualifier-Validierung:** Qualifier nutzen reguläre Registry-Prädikate dual
  (Wikidata-Praxis: `beginn`/P580 hängt als Qualifier an fremden Statements).
  Deshalb ist der Domain-Check für Qualifier BEWUSST ausgesetzt — eine Domain
  bezieht sich auf das Subjekt eines Haupt-Statements. Validiert wird der
  range_kind (Werttyp muss dem Prädikat entsprechen; nur entity/string/
  number/datetime sind als Qualifier-Werte möglich).
- **Statement-Suche (`welt_query`):** viertes Standbein neben Search,
  Entity-View und Traverse. Default-Sicht wie überall: aktuell
  (`system_to IS NULL`), deprecated ausgeblendet; `rank` filtert exakt,
  `valid_at`/`system_at` reisen wie in `welt_entity`. Aggregation ist bewusst
  minimal: `count`/`sum`/`avg` (sum/avg nur über number/quantity, bei
  quantity pro unit gruppiert), `group_by` subject/object — kein
  Analytics-System.
- Schreibende Importe: erst Preview, dann Commit. Unsicheres mit ehrlicher
  Confidence committen statt weglassen; Ränge (`preferred`/`normal`/
  `deprecated`) ordnen Widersprüche.
