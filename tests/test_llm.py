"""LLM-Extraktor über OpenRouter (Spec §7). Läuft nur mit OPENROUTER_API_KEY —
echter API-Call, kein Mock. Die Kernaussage: auch mit LLM im Loop hält das
Gate (VALIDATE rejected oder Proposal), nie freie Writes."""

import pytest

from weltmodell.config import get_openrouter_key

pytestmark = pytest.mark.skipif(
    not get_openrouter_key(), reason="OPENROUTER_API_KEY nicht gesetzt"
)

ARTICLE = {
    "kind": "news_article",
    "headline": "Jonas Beispiel und Tanja Beispiel vernetzen sich",
    "body": (
        "Der Berliner Entwickler Jonas Beispiel (jonas@example.org) kennt "
        "Tanja Beispiel seit Jahren. Sein LinkedIn-Account ist jbeispiel."
    ),
    "published": "2026-06-15",
}


def test_llm_extraction_end_to_end(conn):
    import httpx

    from weltmodell.llm import LLMExtractor
    from weltmodell.pipeline import ingest_document, run_pipeline

    doc = ingest_document(
        conn, raw=ARTICLE, url="https://example.org/artikel",
        activity="scrapling:news", agent="llm-extractor",
    )
    try:
        report = run_pipeline(
            conn, source_id=str(doc["id"]), extractor=LLMExtractor(),
            agent="llm-extractor",
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429 or exc.response.status_code >= 500:
            pytest.skip(f"OpenRouter nicht verfügbar: {exc.response.status_code}")
        raise

    # Nicht-deterministisch — aber die Invarianten gelten immer:
    # alles, was durchkam, hat Provenance und registriertes Vokabular.
    assert report["committed"] or report["rejected"] or report["proposals"], (
        "LLM lieferte weder Statements noch Proposals"
    )
    for sid in report["committed"]:
        row = conn.execute(
            """SELECT s.predicate_id,
                      (SELECT count(*) FROM reference r
                       WHERE r.statement_id = s.id) AS refs
               FROM statement s WHERE s.id = %s""",
            (sid,),
        ).fetchone()
        assert row["refs"] >= 1, "Kein Fakt ohne Provenance (Invariante 3)"
