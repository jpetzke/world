-- Abgeleitete Graph-Metriken + Layout-Persistenz für das Frontend-Skeleton.
--
-- Rationale (Entscheidungsbaum §14.1, Fall 1: ableitbar — nicht modellieren):
-- Community, PageRank, Grad und konvergierte Layout-Positionen sind vollständig
-- aus den aktuellen Entity-Statements ableitbar. Sie werden darum NICHT als
-- Statements modelliert (keine Provenance, kein Rank, keine Bitemporalität),
-- sondern als expliziter, jederzeit neu berechenbarer Cache neben entity.label
-- und entity.embedding (Invariante 1: als ableitbar markiert, eine Wahrheit).
-- Konsum: Skeleton-Auswahl (Degree-of-Interest-Rendering statt Voll-Load) und
-- stabile mentale Karte (Layout-Persistenz, Spec-Regel R4).
--
-- Kein DELETE-Trigger auf statement nötig: Recompute (graph_metrics.recompute)
-- ersetzt den gesamten Metrik-Stand; x/y überleben Recomputes, weil sie vom
-- Client kommen (POST /api/graph/positions) und nur dort geschrieben werden.
-- entity_id-FK mit ON DELETE CASCADE räumt hart gelöschte Entities ab
-- (Entities werden zwar nie gelöscht, nur gemerged — defensiv trotzdem).

CREATE TABLE IF NOT EXISTS graph_metrics (
  entity_id  uuid PRIMARY KEY REFERENCES entity(id) ON DELETE CASCADE,
  community  integer,
  pagerank   real,
  degree     integer NOT NULL DEFAULT 0,
  x          real,
  y          real,
  metrics_at timestamptz,  -- letzter Community/PageRank/Grad-Recompute
  layout_at  timestamptz   -- letzte Positions-Persistenz vom Client
);

-- Skeleton-Auswahl liest "Top-K PageRank pro Community" + globale Hubs.
CREATE INDEX IF NOT EXISTS graph_metrics_community_pagerank
  ON graph_metrics (community, pagerank DESC);
CREATE INDEX IF NOT EXISTS graph_metrics_pagerank
  ON graph_metrics (pagerank DESC);
