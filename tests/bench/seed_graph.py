"""Synthetischer Benchmark-Graph: 100k Entities, 300k Kanten, SBM-Communities.

Misst das DoI-Rendering gegen realistische Community-Struktur (Stochastic
Block Model: dichte Blöcke, dünne Brücken). Läuft gegen eine eigene Bench-DB
(weltmodell_bench) — nie gegen dev/prod. Invariante 3 bleibt gewahrt: eine
synthetische Quelle, jedes Statement bekommt eine reference.

    uv run python tests/bench/seed_graph.py                # 100k/300k
    uv run python tests/bench/seed_graph.py --nodes 10000 --edges 30000

Danach App gegen die Bench-DB starten:
    WELTMODELL_DSN=postgresql://weltmodell:weltmodell@localhost:5433/weltmodell_bench \\
      uv run uvicorn weltmodell.api:app --port 8100
"""

import argparse
import random
import sys
import time
import uuid

import igraph as ig
import psycopg

sys.path.insert(0, "src")
from weltmodell.db import run_migrations  # noqa: E402

ADMIN_DSN = "postgresql://weltmodell:weltmodell@localhost:5433/weltmodell"
BENCH_DSN = "postgresql://weltmodell:weltmodell@localhost:5433/weltmodell_bench"


def build_sbm(n_nodes: int, n_edges: int, n_blocks: int, seed: int) -> ig.Graph:
    """SBM mit ~85% Intra-Block-Kanten; Blockgrößen leicht gestreut."""
    random.seed(seed)
    base = n_nodes // n_blocks
    sizes = [base] * n_blocks
    sizes[-1] += n_nodes - sum(sizes)

    intra_target = 0.85 * n_edges
    inter_target = 0.15 * n_edges
    intra_pairs = sum(s * (s - 1) / 2 for s in sizes)
    inter_pairs = n_nodes * (n_nodes - 1) / 2 - intra_pairs
    p_in = intra_target / intra_pairs
    p_out = inter_target / inter_pairs

    pref = [[p_out] * n_blocks for _ in range(n_blocks)]
    for i in range(n_blocks):
        pref[i][i] = p_in
    return ig.Graph.SBM(pref, sizes)


def seed_db(dsn: str, g: ig.Graph, *, drop: bool) -> None:
    dbname = dsn.rsplit("/", 1)[1]
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        if drop:
            admin.execute(f"DROP DATABASE IF EXISTS {dbname} WITH (FORCE)")
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)
        ).fetchone()
        if not exists:
            admin.execute(f"CREATE DATABASE {dbname}")
    run_migrations(dsn)

    node_ids = [str(uuid.uuid4()) for _ in range(g.vcount())]
    with psycopg.connect(dsn) as conn:
        source_id = conn.execute(
            """INSERT INTO source_document (activity, agent, raw)
               VALUES ('bench:sbm', 'seed_graph.py', '{"synthetic": true}')
               RETURNING id"""
        ).fetchone()[0]

        t0 = time.monotonic()
        with conn.cursor().copy(
            "COPY entity (id, type_id, label) FROM STDIN"
        ) as copy:
            for i, eid in enumerate(node_ids):
                copy.write_row((eid, "Person", f"Bench {i:06d}"))
        print(f"  entities: {g.vcount():,} in {time.monotonic() - t0:.1f}s")

        t0 = time.monotonic()
        stmt_ids = [str(uuid.uuid4()) for _ in range(g.ecount())]
        with conn.cursor().copy(
            """COPY statement (id, subject_id, predicate_id, value_type,
                               object_id, confidence) FROM STDIN"""
        ) as copy:
            for sid, (a, b) in zip(stmt_ids, g.get_edgelist()):
                copy.write_row(
                    (sid, node_ids[a], "knows", "entity", node_ids[b],
                     round(random.uniform(0.5, 1.0), 2))
                )
        print(f"  statements: {g.ecount():,} in {time.monotonic() - t0:.1f}s")

        t0 = time.monotonic()
        with conn.cursor().copy(
            "COPY reference (statement_id, source_id) FROM STDIN"
        ) as copy:
            for sid in stmt_ids:
                copy.write_row((sid, source_id))
        print(f"  references: {len(stmt_ids):,} in {time.monotonic() - t0:.1f}s")
        conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes", type=int, default=100_000)
    ap.add_argument("--edges", type=int, default=300_000)
    ap.add_argument("--blocks", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dsn", default=BENCH_DSN)
    ap.add_argument("--keep", action="store_true",
                    help="Bench-DB nicht droppen, nur nachlegen")
    args = ap.parse_args()

    print(f"SBM: {args.nodes:,} Nodes, Ziel {args.edges:,} Kanten, "
          f"{args.blocks} Blöcke …")
    t0 = time.monotonic()
    g = build_sbm(args.nodes, args.edges, args.blocks, args.seed)
    print(f"  generiert: {g.ecount():,} Kanten in {time.monotonic() - t0:.1f}s")

    seed_db(args.dsn, g, drop=not args.keep)
    print(f"fertig → {args.dsn}")


if __name__ == "__main__":
    main()
