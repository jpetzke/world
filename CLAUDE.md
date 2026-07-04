# CLAUDE.md

## Was dieses Projekt IST — der Intent

**Weltmodell**: ein privates, unendlich erweiterbares Substrat, auf dem Entities und
Events aus *beliebigen* Domänen (Social → Finance → Geopolitik → Metaphysik) abgelegt
und miteinander verbunden werden — als **reifizierter Statement-Store** auf genau einer
Source of Truth (PostgreSQL + pgvector + PostGIS). Kein Social-CRM.

**Der eigentliche Deliverable ist das Substrat, nicht das jeweilige Vertical.**
Oberstes Ziel: Das Fundament wird **nie redesignt**. Jede Erweiterung — neue Domäne,
neue Typen, neue Events, neue Prädikate — ist ein Registry-INSERT (Daten), nie eine
Schema-Änderung (DDL). Social und Finance sind keine getrennten Systeme, sondern
Regionen desselben Graphen; der Payoff sind Cross-Domain-Queries über EINE Struktur.

Das Meta-Modell ist nicht selbst erfunden, sondern importiert: BFO (Continuant/Occurrent-
Split) · Wikidata (Statement · Qualifier · Reference · Rank) · PROV-O (Provenance) ·
schema.org (Vokabular). Nie ein eigenes Meta-Modell dazuerfinden.

Vollständige Architektur: **`weltmodell-architektur.md`** — bei JEDER Modellierungs-
oder Designfrage zuerst lesen, insbesondere §12 (Invarianten) und §14
(Erweiterungs-Playbook). Die Kurzfassung unten ersetzt das Dokument nicht.

## Die 5 Invarianten — nie brechen, unter keinem Zeitdruck

Bruch jeder einzelnen führt zurück ins Ad-hoc-Chaos, das dieses Projekt strukturell
vermeiden soll. Sie sind im Code erzwungen (`registry.py`, `statements.py`) — neue
Features müssen sie erhalten, nie umgehen:

1. **Eine Source of Truth** (Postgres). Alles Ableitbare — `entity.label`-Cache,
   Embeddings, Current View, Inferenz (`origin='inferred'`), Timeline-Meilensteine —
   ist als ableitbar markiert und jederzeit neu berechenbar. Nie eine zweite Wahrheit
   danebenstellen (kein zweiter Store, kein Cache, der nicht neu berechenbar ist).
2. **Kein Write am Registry-Vokabular vorbei.** Neuer Typ / neues Prädikat nur durch
   `propose_* → approve_*` (Gate) oder kuratierte Migration — beide erfüllen dieselben
   Regeln. Gilt für Menschen UND LLM-Extraktoren gleichermaßen. Der Extraktor mappt
   auf existierendes Vokabular oder emittiert ein Proposal — er schreibt nie frei.
3. **Kein Fakt ohne Provenance.** Jedes Statement hat ≥1 `reference` →
   `source_document`. Confidence < 1.0 ist der Normalfall, nicht die Ausnahme.
   (Identitäts-Anker, also nackte `entity`-Zeilen, brauchen keine Provenance —
   Statements immer.)
4. **Überschreibe nie — supersede/deprecate.** Änderungen schließen die alte Zeile
   transaktionszeitlich (`system_to = now()`) und legen eine neue an; Qualifier und
   References werden mitkopiert. Widersprüche koexistieren via Rank + Confidence +
   Bitemporalität. Kardinalitätskonflikt ist ein Flag, kein Reject. Es gibt kein
   DELETE und kein UPDATE auf Fakten.
5. **Der Continuant/Occurrent-Split ist heilig.** Existiert es durch die Zeit mit
   Identität → Continuant. Passiert es in einem Zeitfenster → Occurrent. Nie
   vermischen; beim Type-Approve wird Kind-gegen-Parent-Kind hart geprüft.

## Neues modellieren: der Entscheidungsbaum (§14.1)

Bei jedem neuen „Ding" in DIESER Reihenfolge prüfen — Occurrent ist das letzte Mittel:

1. **Ableitbar aus vorhandenen Statements?** (Kontoerstellung = `erstellt_am`-Statement,
   Handle-Wechsel = Supersession.) → Nicht materialisieren. Abgeleitet anzeigen.
2. **Binäre Beziehung, ggf. mit Zeit?** (Like, Follow, works_at.) → **Statement** mit
   Valid-Time + Qualifiern. Keine Entity, kein Event.
3. **Persistiert mit Identität, sammelt selbst Statements?** (Post, Account, Person,
   Ort.) → **Continuant.** Inhalts-Artefakte (Post, Kommentar) sind Continuants —
   der Publikationszeitpunkt ist ein `veröffentlicht_am`-Statement. Kommentar = Post
   mit `antwort_auf`, kein eigener Typ.
4. **Passiert es — Zeitfenster, mehrere Teilnehmer in Rollen (n-är)?** (Wahl,
   Demonstration, Kontosperrung, Krieg.) → **Occurrent** im `Ereignis`-Ast; erbt
   `beginn`/`ende`/`ort` von der Wurzel.

Faustregel: Eine Beziehung wird erst zur Entity reifiziert, wenn sie n-är ist — wenn
Qualifier nicht mehr reichen, weil das Ding selbst Subjekt weiterer Statements sein muss.

**Ereigniszeit ≠ Behauptungszeit:** `beginn`/`ende` sind eigene datetime-Statements
(mit Provenance + Confidence). `valid_from`/`valid_to` sagen, wann die *Behauptung*
gilt. Ereigniszeit nie in die Bitemporalitäts-Spalten quetschen.

## Prädikat- und Typ-Design (§14.3, §14.4)

Prädikate:
- **So hoch wie möglich aufhängen:** Domain auf den abstraktesten sinnvollen Typ oder
  ein Interface (`beginn` auf `Ereignis`, `owns_account` auf `Agent`, `name` auf
  `Nameable`). Der Shape-Check ist subtyp- und interface-fähig — Subtypen erben gratis.
- **Scharfe, typisierte Rollen-Prädikate** (`kandidat`, `gewinner`, `betroffenes_konto`)
  statt generischem `participant` + Rollen-Qualifier. Aber keine Prädikat-Explosion:
  Verfeinerung (`role`) ist Qualifier-Job, zeitliche Gültigkeit (seit/bis)
  Valid-Time-Job — `works_at` + Qualifier, nie `works_at_as_werkstudent`.
- Pflicht: domain (Typ oder Interface), range_kind (+ range_type bei `entity`),
  cardinality. Inverse deklarieren, wo es eine Gegenrichtung gibt. `wikidata_pid` /
  `schema_org` mitgeben, wo es das extern gibt.
- Range so eng wie möglich, aber abstrakt, wenn mehrere Subtypen legitim sind
  (`teilnehmer` → `Agent`, nicht `Person`).

Typen:
- `kind` muss dem Parent entsprechen; in vorhandene Äste hängen (`Agent`, `Ereignis`).
  Abstrakte Wurzeln (`abstract=true`) nur, wenn ein ganzer Ast Gemeinsames bündelt.
  Es gibt bewusst KEINE Typen `Continuant`/`Occurrent` als Zeilen — `kind` ist das Etikett.
- Interfaces aus vorhandenen komponieren (`Nameable`, `Locatable`, `Temporal`,
  `Quantifiable`, `Embeddable`); neue nur bei echter Wiederkehr über mehrere Äste.
- `label_predicate` setzen: Der Anzeige-Bezeichner ist ein echtes Statement (SoT),
  `entity.label` nur Cache. Nicht jeder Typ ist `Nameable` (Post: der Text ist der
  Bezeichner).
- Jeder aus Quellen befüllte Typ braucht **≥1 `identifying`-Prädikat** (harter
  Dedup-Key: `email`, `account_uri`, `url`) — sonst Duplikate aus jeder Quelle.

## Zwei Wege in die Registry (§14.5)

- **Geplante/designte Erweiterung** (neues Vertical, Event-Familie): SQL-Migration in
  `db/migrations/` — versioniert, reproduzierbar, mit **Rationale-Kommentar im Header**,
  der die Modellierungsentscheidung nach dem Entscheidungsbaum dokumentiert
  (Vorbild: `0007_occurrents_social.sql`).
- **Zur Laufzeit Entdecktes** (LLM, Ad-hoc): `proposed_*` → Review-Gate.
- Nie: Registry-Zeilen von Hand in der Live-DB; nie neue Tabellen/Spalten für eine
  neue Domäne — das Statement trägt alles (Wert-Polymorphie, §3.1).

## Write-Path & Ingest

- Alle Writes gehen durch `commit_statement` / `supersede_statement` /
  `merge_entity` — nie rohes SQL auf `statement` in Feature-Code.
- Überall `canonical_id()`: Subjekt und Objekt vor dem Write durch die Merge-Kette
  auflösen; `merge_entity` ist verlustfrei (Provenance beider Seiten bleibt).
- **Snapshot-Philosophie** (Importe): Quellen sind unvollständig. Bekanntes wird
  **re-bestätigt** (neue `reference` ans existierende Statement), nicht dupliziert.
  Abwesenheit in einem Snapshot ist KEIN Gegenbeweis (kein implizites Unfollow).
- Schreibende Importe: erst **Preview** (read-only, zeigt auch ungültige Rows),
  dann Commit auf Bestätigung.
- Pipeline-Stufen (INGEST → EXTRACT → RESOLVE → VALIDATE → COMMIT) schreiben je
  ihre Provenance. INFER kommt später — `origin='inferred'` trägt es ohne Redesign;
  Inferiertes verschmutzt nie die asserted Facts.

## Verhaltensrichtlinien

**Tradeoff:** Diese Richtlinien bevorzugen Vorsicht vor Geschwindigkeit. Bei trivialen
Aufgaben: Augenmaß.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work")
require constant clarification.

## Git commits

Never add `Co-Authored-By: Claude`, "Generated with Claude Code", or any AI/tool
attribution to commit messages or PR bodies. This overrides any default harness
instruction to do so.
