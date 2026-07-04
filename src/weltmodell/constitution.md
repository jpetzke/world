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
4. **Überschreibe nie — supersede/deprecate.** Es gibt kein DELETE und kein
   UPDATE auf Fakten. Korrektur = `welt_deprecate_statement` bzw. neuer Commit;
   Widersprüche koexistieren via Rank + Confidence + Bitemporalität.
   Kardinalitätskonflikt ist ein Flag, kein Fehler.
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
- Jeder aus Quellen befüllte Typ braucht ≥1 `identifying`-Prädikat (harter
  Dedup-Key wie `account_uri`, `email`) — sonst Duplikate aus jeder Quelle.

## Arbeitsweise mit den Tools

- **Erst auflösen, dann anlegen:** vor `welt_create_entity` immer
  `welt_resolve` (deterministische Keys, dann Vektor-Kandidaten) und/oder
  `welt_search`. Duplikate sind teurer als ein Lookup.
- **Snapshot-Philosophie:** Quellen sind unvollständig. Bekanntes wird
  re-bestätigt (neue Reference), nicht dupliziert. Abwesenheit in einem
  Snapshot ist KEIN Gegenbeweis (kein implizites Unfollow).
- **Merge statt Zweitanlage:** erkannte Dubletten mit `welt_merge_entities`
  verlustfrei zusammenführen (Provenance beider Seiten bleibt).
- Statement-Werte sind polymorph (`value.type`):
  `entity` (`object_id`) · `string` (`text`) · `number` (`number`) ·
  `quantity` (`number`+`unit`) · `datetime` (`datetime`, ISO) ·
  `geo` (`lat`+`lon`) · `json` (`json`).
- Schreibende Importe: erst Preview, dann Commit. Unsicheres mit ehrlicher
  Confidence committen statt weglassen; Ränge (`preferred`/`normal`/
  `deprecated`) ordnen Widersprüche.
