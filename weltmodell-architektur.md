# Weltmodell — Architektur-Blueprint

> Ein privates, unendlich erweiterbares Substrat, auf dem beliebige Entities und Events aus jeder Domäne (Menschen → Märkte → Kriege → Metaphysik) nicht nur abgelegt, sondern miteinander verbunden werden. Kein Social-CRM. Ein Fundament, das nie redesignt werden muss und mit jedem eingeschriebenen Fakt mächtiger wird.

**Erster Vertical:** Social (`Person` ist der am dichtesten vernetzte Entity-Typ → bester Stresstest fürs Substrat).
**Eigentlicher Deliverable:** das Substrat, nicht das Social-Schema.

---

## 0. Die eine Entscheidung, die alles andere bestimmt

**Genau eine Source of Truth: PostgreSQL.** Kein Polyglot Persistence.

Der naheliegende Reflex (Neo4j + Postgres + Qdrant nebeneinander) ist ein Anti-Pattern für ein Solo-Weltmodell: mehrere Sources of Truth = Sync-Probleme, mehrere Backups, Konsistenz-Bugs an den Nahtstellen. Genau da sterben solche Projekte. Ein Weltmodell braucht **einen** Ort, an dem die Wahrheit liegt.

Postgres kann alles, was das Modell braucht, in **einer** Engine:

| Anforderung | Postgres-Mechanismus |
|---|---|
| Graph-Traversierung (Multi-Hop) | Recursive CTEs → später **Apache AGE** (Cypher-über-Postgres) |
| Semantik / Embeddings / Dedup / RAG | **pgvector** — im selben Store, kein externes Qdrant |
| Aggregation (Finance-Vertical, Dashboard) | natives SQL |
| Bitemporalität | Timestamp-Spalten + Constraints |
| Geo (Locatable-Interface) | **PostGIS** |
| Ein Backup, eine Engine, betreibst du eh | Coolify/Hetzner, done |

Neo4j wäre beim reinen „von jedem Node wegpivotieren"-Gefühl minimal angenehmer — aber Reifizierung (Statements + Provenance), Bitemporalität und SQL-Aggregation sind dort verbose bis fehlend, und Vektorsuche zöge dich zurück ins Polyglot. Die Entscheidung ist damit durch.

---

## 1. Das fundamentale Modell: reifizierter Statement-Store

Der zentrale Denkfehler, den fast jeder macht: mit **konkreten Objects** anfangen (`Person`, `Account`, `Mention`). Dann ist jede neue Domäne ein neues Schema, und irgendwann klatschst du unter Zeitdruck behelfsmäßige Types rein — genau das Szenario, das du vermeiden willst.

Die Lösung ist eine Ebene tiefer. **Die kleinste Einheit ist nicht das Object, sondern das Statement** — ein reifiziertes Tripel:

```
(subject, predicate, value)  +  Qualifier  +  Reference  +  Rank  +  Gültigkeit  +  Confidence
```

„Reifiziert" (reification) heißt: das Tripel selbst wird zum first-class Objekt, an das du Metadaten hängen kannst — wer behauptet das, seit wann gilt es, wie sicher ist es. Das ist der Unterschied zwischen einem naiven Key-Value-Store (der zu Chaos führt) und einem echten Weltmodell.

Dieses Modell ist **nicht selbst erfunden.** Es steht auf vier ausgereiften Standards — das ist die strukturelle Antwort auf „keine behelfsmäßigen Types":

| Standard | Was du davon nimmst |
|---|---|
| **BFO / DOLCE** (Upper Ontologies aus der KR-Forschung) | Der fundamentale Top-Split: **Continuant** vs. **Occurrent** |
| **Wikidata-Datenmodell** (battle-tested bei 100M+ Entities) | Item · Statement · Qualifier · Reference · Rank |
| **PROV-O** (W3C Provenance) | Entity · Activity · Agent — „nichts ist Fakt, alles ist Claim von Quelle X" |
| **schema.org** | Pragmatisches Vokabular für den Web-Alltag (Person, Organization, Event) |

Du importierst eine bewährte Upper Ontology, statt eine zu erfinden. Das ist der ganze Trick.

### 1.1 Der Top-Split: Continuant vs. Occurrent

Zwei Wurzeltypen, nicht mehr:

- **Continuant (Entity)** — existiert *durch* die Zeit, hat Identität: `Person`, `Organization`, `Country`, `Account`, `StockIndex`, `Concept`.
- **Occurrent (Event)** — *passiert* in einem Zeitfenster: `Interaction`, `Mention`, `War`, `StockCrash`, `NaturalDisaster`, `Election`.

Warum das load-bearing ist: Ohne diesen Split wird „Krieg" später ein hässlicher Sonderfall statt einfach ein neuer `Event`-Subtyp. **Eine Erwähnung ist ein Event, keine Entity** — sie passiert zu einem Zeitpunkt, sie persistiert nicht. Diesen Fehler baust du jetzt schon raus, bevor er teuer wird.

---

## 2. Schema-als-Daten: die Anti-Drift-Registry

Das Herzstück gegen „behelfsmäßige Types". **Typen, Interfaces und Prädikate sind Daten, keine DDL.**

Einen neuen Typ (`NaturalDisaster`) hinzufügen = ein `INSERT`, keine Migration. Das ist die Mechanik hinter „unendlich erweiterbar ohne Redesign".

### 2.1 Type-Registry (`entity_type`)

Hierarchisch, self-referential: `Person ⊂ Agent ⊂ Continuant ⊂ Entity`. Jeder Typ hat einen Parent und deklariert, welche Interfaces er implementiert.

### 2.2 Interfaces (Traits)

Abstrakte, wiederverwendbare Property-Sets — exakt das, was Palantir Foundry heute „Object Type Interfaces" nennt:

- `Nameable` (name, aliases)
- `Locatable` (geo, address)
- `Temporal` (valid_from, valid_to)
- `Quantifiable` (value, unit)
- `Embeddable` (vector)

`Country` = Nameable + Locatable. `StockIndex` = Nameable + Quantifiable + Temporal. `NaturalDisaster` = Locatable + Temporal + Quantifiable (Schadenshöhe). **Kein Redesign, wenn morgen eine neue Domäne kommt** — der Typ setzt sich aus vorhandenen Interfaces zusammen.

### 2.3 Predicate-Registry (Controlled Vocabulary)

Das, was den KI-Extraktor daran hindert, Prädikate zu erfinden. Jedes Prädikat deklariert:

- `domain` — welche Typen/Interfaces dürfen Subjekt sein
- `range` — was muss der Wert sein (Entity welchen Typs? welcher Datentyp? welche Einheit?)
- `cardinality` — 1:1, 1:n, n:m
- `inverse` — `invests_in` ↔ `has_investor` (automatische Gegenrichtung)
- `wikidata_pid` / `schema_org_prop` — Mapping auf externes Vokabular, wo billig

Der LLM-Extraktor bekommt diese Registry als **erlaubtes Vokabular**. Er *mappt* auf existierende Prädikate — oder schlägt ein neues durch ein Gate vor (§7). Er schreibt nie frei.

> **Der Unterschied zu naivem EAV** (Entity-Attribute-Value, dem klassischen „flexiblen" Schema-Antipattern): EAV wird zu Chaos, weil jeder alles reinschreiben kann. Hier ist derselbe generische Mechanismus **durch die Registry + Shape-Validierung diszipliniert**. Flexibilität mit Leitplanken statt Flexibilität als Freifahrtschein.

---

## 3. Das Statement im Detail

Ein Statement trägt weit mehr als ein Tripel:

- **Qualifier** — verfeinern das Statement, ohne das Prädikat zu explodieren. Statt eines Spezial-Prädikats `works_at_as_werkstudent_since_2024` schreibst du `Person –works_at→ Org` + Qualifier `role: Werkstudent`, `start: 2024`. Das hält die Prädikat-Menge klein und sauber.
- **Rank** (`preferred` / `normal` / `deprecated`) — wenn mehrere Werte konkurrieren, markiert `preferred` den aktuell besten. Wikidatas Mechanismus für „der jetzt gültige Wert".
- **Confidence** (0..1) — wie sicher ist die Behauptung. Ein philosophischer Claim („Theorie der Realität") ist einfach ein Statement mit niedriger Confidence — kein Sonderfall.
- **Bitemporalität** — zwei Zeitachsen (§4).
- **Reference** — Pflicht-Verweis auf ≥1 Quelle (§5).

### 3.1 Wert-Polymorphie

Der Value ist polymorph: entweder ein Verweis auf eine andere Entity (`object_id`) oder ein typisiertes Literal (Text, Zahl+Einheit, Datum, Geo, JSON). Diskriminator-Spalte `value_type` + typisierte Spalten → indexierbar und sauber. So ist derselbe Mechanismus für „Person kennt Person", „Aktie hat Kurs 142.30 EUR" und „Krieg begann am 24.02.2022" zuständig — **eine Struktur, keine neue Tabelle pro Beziehungstyp.**

---

## 4. Bitemporalität — zwei Zeitachsen

Das ist bei einem Weltmodell, das widersprüchliche News frisst, nicht optional:

- **Valid Time** (`valid_from` / `valid_to`) — wann ist der Fakt *in der Welt* wahr.
- **Transaction Time** (`system_from` / `system_to`) — wann *wusste das System* davon.

Damit beantwortest du zwei fundamental verschiedene Fragen:

1. *„Was war am Datum D über X wahr?"* → Realität rekonstruieren.
2. *„Was habe ich am Datum D über X geglaubt?"* → Audit, Widerspruchs-Auflösung, „wann kam die Korrektur rein".

Für die KI-Fill-Ebene ist Achse 2 Gold: du siehst, welche Quelle wann welchen Claim eingebracht hat.

---

## 5. Provenance — Source-first

**Nichts ist ein Fakt. Alles ist eine Behauptung von Quelle X mit Confidence Y.** Palantir wurde ursprünglich genau dafür gebaut (Intelligence-Kontext), und für dein Modell ist es essenziell, sobald sich Quellen widersprechen.

PROV-O-Triade, leichtgewichtig:

- **Entity** (die Daten) — das Statement / Source-Document
- **Activity** (die Erzeugung) — der Scraper-Run, die n8n-Execution, die LLM-Extraktion
- **Agent** (der Verantwortliche) — welche Pipeline / welches Modell

Jedes Statement → ≥1 `reference` → `source_document` (URL, Scraper-Run-ID, Retrieval-Timestamp, n8n-Execution-ID, Apify-Run-ID). Provenance ist ein eigenes Objekt, kein Feld.

---

## 6. Widersprüche & mehrere Wahrheiten

Weil Statements reifiziert sind (Rank + Confidence + Source), **koexistieren widersprüchliche Statements** statt sich zu überschreiben:

```
Statement A: (Land1) at_war_with (Land2)   confidence 0.9  source: Reuters   valid_from 2022-02-24
Statement B: (Land1) at_war_with (Land2)   rank: deprecated  valid_to 2025-01-15  (Waffenstillstand, source: AP)
```

Kein Overwrite, kein Datenverlust. `preferred`-Rank + `valid_to` liefern die aktuelle Sicht; die Historie bleibt vollständig. Das trägt deinen „Theorie der Realität"-Fall elegant: konkurrierende Claims über die Realität liegen einfach mit unterschiedlicher Confidence und Provenance nebeneinander.

---

## 7. Der KI-Fill-Layer — das Substrat, das sich selbst füllt

Der eigentliche Grund, warum das Substrat so gebaut ist: **ein LLM-Agent soll es autonom wachsen lassen.** Der Flywheel — mehr Quellen → mehr Statements → dichterer Graph → besseres Dedup & Inferenz → wertvollere Queries → lohnt mehr Quellen.

Die Pipeline, jede Stufe schreibt Provenance:

```
1. INGEST   n8n / Apify / Scrapling         → rohes source_document gespeichert
2. EXTRACT  LLM, constrained auf Registry    → Kandidaten-Statements
3. RESOLVE  pgvector + deterministische Keys → Entity-Resolution / Dedup
4. VALIDATE Shape-Check gegen domain/range   → reject oder flag
5. COMMIT   Statement + Provenance geschrieben
6. INFER    (später) abgeleitete Statements  → als inferred markiert
```

### 7.1 Die strukturelle Garantie gegen Drift

**Der Extraktor schreibt nie direkt.** Er bekommt Type- und Predicate-Registry als erlaubtes Vokabular und muss:

- entweder auf ein **existierendes** Prädikat/Typ mappen,
- oder ein `proposed_predicate` / `proposed_type` emittieren, das in ein **Review-Gate** geht (das anfangs du bist, später eine Confidence-Schwelle + Auto-Approve für Trivialfälle).

Neue Types/Prädikate durchlaufen zwingend die Registry-Regeln: Typ braucht Parent + Interfaces, Prädikat braucht domain/range/cardinality (+ Vokabular-Mapping wo möglich). Erzwungen im Code (FastAPI-Action), nicht per Konvention. **Gilt für Menschen- und LLM-Writes gleichermaßen** — sonst klatschst du selbst unter Zeitdruck Ad-hoc-Labels rein.

### 7.2 Entity-Resolution (Dedup)

Sonst hast du fünf Duplikate derselben Person aus fünf Quellen. Zweistufig:

- **Deterministisch** — harte Keys (E-Mail, Handle+Plattform, Wikidata-QID).
- **Fuzzy/Vektor** — pgvector-Ähnlichkeit über Namen + Kontext-Embedding, Schwelle → Merge-Kandidat.

`merge_entity`-Action führt zusammen, ohne Statements zu verlieren (Provenance beider Quellen bleibt).

### 7.3 Asserted vs. Inferred

Abgeleitete Fakten (Transitivität, Inverse, Klassen-Inferenz) werden als `inferred` markiert (Provenance = Reasoner). Sie verschmutzen nie die asserted Facts und sind jederzeit neu berechenbar. Reasoning ist damit **designed-for-later** — das Statement-Modell trägt es ohne Redesign.

---

## 8. Kern-DDL (Skelett)

Bewusst schlank — die Wahrheit liegt in den Statements, `entity` ist nur ein Identitäts-Anker.

```sql
-- === REGISTRY (Schema-als-Daten) ===
CREATE TABLE entity_type (
  id           text PRIMARY KEY,          -- 'Person', 'War', ...
  parent_id    text REFERENCES entity_type(id),
  kind         text NOT NULL CHECK (kind IN ('continuant','occurrent')),
  label        text NOT NULL,
  wikidata_qid text
);

CREATE TABLE interface (
  id    text PRIMARY KEY,                 -- 'Nameable', 'Locatable', ...
  label text NOT NULL
);

CREATE TABLE type_implements (
  type_id      text REFERENCES entity_type(id),
  interface_id text REFERENCES interface(id),
  PRIMARY KEY (type_id, interface_id)
);

CREATE TABLE predicate (
  id             text PRIMARY KEY,        -- 'works_at', 'invests_in', ...
  label          text NOT NULL,
  domain_type    text REFERENCES entity_type(id),   -- oder Interface-Ref
  range_kind     text NOT NULL CHECK (range_kind IN
                   ('entity','string','number','datetime','geo','json','quantity')),
  range_type     text REFERENCES entity_type(id),   -- falls range_kind='entity'
  cardinality    text CHECK (cardinality IN ('1:1','1:n','n:m')),
  inverse_id     text REFERENCES predicate(id),
  wikidata_pid   text,
  schema_org     text
);

-- === ENTITIES (nur Identitäts-Anker) ===
CREATE TABLE entity (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  type_id    text NOT NULL REFERENCES entity_type(id),
  label      text,                        -- denormalisierter Cache, kein SoT
  embedding  vector(1024),                -- pgvector, für Dedup & Suche
  created_at timestamptz DEFAULT now()
);

-- === PROVENANCE ===
CREATE TABLE source_document (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  url           text,
  retrieved_at  timestamptz,
  activity      text,                     -- 'apify:linkedin', 'n8n:exec:123', ...
  agent         text,                     -- Pipeline / Modellname
  raw           jsonb
);

-- === STATEMENT (das reifizierte Tripel) ===
CREATE TABLE statement (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subject_id    uuid NOT NULL REFERENCES entity(id),
  predicate_id  text NOT NULL REFERENCES predicate(id),

  value_type    text NOT NULL CHECK (value_type IN
                  ('entity','string','number','datetime','geo','json','quantity')),
  object_id     uuid REFERENCES entity(id),   -- value_type='entity'
  value_text    text,
  value_number  numeric,
  value_unit    text,                          -- value_type='quantity'
  value_datetime timestamptz,
  value_geo     geography,
  value_json    jsonb,

  rank          text DEFAULT 'normal' CHECK (rank IN ('preferred','normal','deprecated')),
  confidence    real DEFAULT 1.0,
  origin        text DEFAULT 'asserted' CHECK (origin IN ('asserted','inferred')),

  valid_from    timestamptz,              -- Valid Time
  valid_to      timestamptz,
  system_from   timestamptz DEFAULT now(),-- Transaction Time
  system_to     timestamptz              -- NULL = aktuell
);

CREATE TABLE qualifier (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  statement_id  uuid NOT NULL REFERENCES statement(id) ON DELETE CASCADE,
  predicate_id  text NOT NULL REFERENCES predicate(id),
  value_type    text NOT NULL,
  value_text    text, value_number numeric, value_datetime timestamptz,
  object_id     uuid REFERENCES entity(id)
);

CREATE TABLE reference (
  statement_id  uuid NOT NULL REFERENCES statement(id) ON DELETE CASCADE,
  source_id     uuid NOT NULL REFERENCES source_document(id),
  PRIMARY KEY (statement_id, source_id)
);

CREATE INDEX ON statement (subject_id, predicate_id);
CREATE INDEX ON statement (object_id);
CREATE INDEX ON entity USING hnsw (embedding vector_cosine_ops);
```

Optional als Ergonomie-Layer: eine **materialisierte „current view"** (JSONB-Cache der `preferred`, aktuell gültigen Statements pro Entity). Ableitbar → nie zweite Source of Truth, nur ein Index für schnelle Reads.

---

## 9. Worked Example: Social Vertical

**Types:** `Person` (Continuant, Agent, Nameable+Embeddable), `Organization` (Continuant, Nameable), `Account` (Continuant, Nameable), `Mention` (**Occurrent**, Temporal).

**Predicates:** `knows`, `romantic_partner_of` (inverse = sich selbst), `works_at`, `owns_account`, `subject_of` (Person ist Subjekt einer Mention).

**Rows (konzeptuell):**

```
entity: e1=Jonas (Person), e2=Tanja (Person), e3=BLAID (Organization),
        e4=@jpetzke (Account), e5=Mention#1 (Mention/Event)

statement s1: e1 --works_at--> e3
   qualifier: role="Werkstudent", hours=16
   reference: source_document(url=blaid.de, activity=manual)
   valid_from: 2024-10, confidence 1.0

statement s2: e1 --romantic_partner_of--> e2   confidence 1.0
statement s3: e1 --owns_account--> e4          source: apify:linkedin
statement s4: e5 --subject_of(inverse)--> e1   (Person in Artikel erwähnt)
   value: e5.value_text=snippet, valid_from=Artikeldatum, source: scrapling(url)
```

**Widerspruch-Fall (warum das Modell trägt):**

```
s5: e1 --works_at--> e3   rank=deprecated  valid_to=2027-XX  source: LinkedIn(veraltet)
s6: e1 --works_at--> e7   rank=preferred   valid_from=2027-XX source: neuer Artikel
```

Beide bleiben. Die „current view" zeigt s6, die Historie kennt s5.

---

## 10. Cross-Domain-Beweis: warum Finance später gratis andockt

Der Payoff des Substrats zeigt sich erst beim **zweiten** Vertical. Weil alles denselben Statement-Mechanismus + geteilten Prädikat-Namespace nutzt, entstehen Cross-Domain-Ketten ohne eine einzige neue Tabelle:

```
Person(Jonas) --invests_in--> Company(TSMC)
Company(TSMC) --affected_by--> Event(Taiwan-Konflikt)     [Occurrent]
Event(Taiwan-Konflikt) --located_in--> Country(Taiwan)
Country(Taiwan) --at_war_with--> Country(...)
```

Query: *„Zeig mir alle Personen in meinem Netzwerk, deren Investments von einem laufenden geopolitischen Event betroffen sind."* — ein Multi-Hop-Traverse über **eine** Struktur. Das ist der Moment, in dem sich das ganze Fundament auszahlt: Social und Finance waren nie getrennte Systeme, sie sind Regionen desselben Graphen.

---

## 11. Phasenplan

| Phase | Inhalt | Ergebnis |
|---|---|---|
| **0** | Core-Tables + Upper Ontology seeden (Continuant/Occurrent, Base-Interfaces) | Substrat steht |
| **1** | Social-Vertical: Person/Org/Account/Mention + Ingest (Apify/Scrapling → extract → resolve → commit) | Erster echter Datenfluss |
| **2** | KI-Fill-Loop: Dedup (pgvector), Semantic Search, Write-Gate | Selbstwachsend |
| **3** | Zweiter Vertical (Finance) → **Cross-Domain-Link beweisen** | Substrat zahlt sich aus |
| **4** | Inferenz/Reasoning (asserted vs. inferred), Apache AGE für schwere Traversierung | Mächtiger Graph |

---

## 12. Die Invarianten — Regeln, die du nie brichst

Diese fünf halten das System über Jahre sauber. Bruch jeder einzelnen führt zurück ins Ad-hoc-Chaos:

1. **Eine Source of Truth.** Alles Ableitbare (Caches, Embeddings, Inferenz) ist explizit ableitbar markiert und neu berechenbar.
2. **Kein direkter Write ohne Registry.** Neuer Typ/Prädikat nur durch das Gate (Parent + Interfaces bzw. domain/range/cardinality). Gilt für dich UND jeden Agenten.
3. **Kein Fakt ohne Provenance.** Jedes Statement hat ≥1 Reference. Confidence < 1.0 ist der Normalfall, nicht die Ausnahme.
4. **Überschreibe nie — deprecate.** Widersprüche koexistieren via Rank + Bitemporalität.
5. **Der Continuant/Occurrent-Split ist heilig.** Passiert etwas in einem Zeitfenster → Event. Existiert es mit Identität → Entity. Nie vermischen.

---

## 13. Bewusst später (kein Redesign nötig)

- **OWL-Style-Reasoning** — das Statement-Modell trägt abgeleitete Fakten schon jetzt (`origin='inferred'`); der Reasoner kommt in Phase 4.
- **Apache AGE** — starte mit Recursive CTEs, rüste Cypher nach, wenn Traversierung schmerzt.
- **Auto-Approve-Gate** — anfangs reviewst du Proposals selbst; später Confidence-Schwelle + Auto-Approve für Trivialfälle.
- **Materialized current-view** — erst bauen, wenn Reads spürbar langsam werden.

---

*Fundament: BFO (Top-Split) · Wikidata (Statement-Modell) · PROV-O (Provenance) · schema.org (Vokabular). Kein selbst erfundenes Meta-Modell — importierte, bewährte Ontologie.*
