"""System-Prompt: statischer Prefix, KV-Cache-freundlich.

Wird beim ersten Zugriff einmal aus Verfassung + Registry gebaut und
prozessweit gecacht — nicht pro Request. Nachrichten sind append-only;
der Prefix ändert sich innerhalb einer Session nie.
"""

from .. import registry
from ..db import get_conn
from ..mcp_server import _constitution

_cached_prompt: str | None = None


def _vocabulary_block(vocab: dict) -> str:
    types = "\n".join(
        f"- {t['id']} ({t['kind']}, parent={t['parent_id'] or '—'})"
        + (" [abstrakt]" if t.get("abstract") else "")
        for t in vocab["types"]
    )
    predicates = "\n".join(
        "- {id}: {domain} → {range} ({card})".format(
            id=p["id"],
            domain=p["domain_type"] or p["domain_interface"] or "?",
            range=p["range_type"] or p["range_kind"],
            card=p["cardinality"] or "n:n",
        )
        + (" [identifying]" if p.get("identifying") else "")
        for p in vocab["predicates"]
    )
    interfaces = ", ".join(i["id"] for i in vocab["interfaces"]) or "—"
    return f"TYPEN:\n{types}\n\nPRÄDIKATE:\n{predicates}\n\nINTERFACES: {interfaces}"


def build_system_prompt() -> str:
    global _cached_prompt
    if _cached_prompt is not None:
        return _cached_prompt

    constitution_text, version = _constitution()
    conn = get_conn()
    try:
        vocab = registry.vocabulary(conn)
    finally:
        conn.close()

    _cached_prompt = f"""Du bist WorldAI, Analyst über Jonas' Weltmodell — ein privater, \
reifizierter Statement-Store (PostgreSQL) über Entities und Events aus beliebigen Domänen.

Sprache: Deutsch. Stil: direkt, präzise, keine Floskeln. Antworte mit dem \
Ergebnis, nicht mit Meta-Kommentaren über deine Arbeitsweise. Nenne bei \
Analysen die Datenbasis (welche Tools, wie viele Treffer). Wenn du eine \
Entity erwähnst, die du aufgelöst hast, schreibe sie als \
[[entity:<id>|<label>]] — das UI rendert daraus einen klickbaren Chip.

== VERFASSUNG (Version {version}) ==
{constitution_text}

== VOKABULAR (Registry — nur dieses verwenden, nie improvisieren) ==
{_vocabulary_block(vocab)}

== TOOL-NUTZUNG ==
- IMMER welt_resolve, bevor du dich auf eine Entity beziehst — nie IDs raten.
  Bereits aufgelöste Entities stehen im Anker-Cache-Block der Konversation;
  die musst du nicht erneut auflösen.
- Mengen- und Analysefragen („wer folgt A und B", „über wen kennen sich X
  und Y"): die Analyse-Tools welt_match, welt_set, welt_path, welt_common,
  welt_rank bevorzugen — EIN Roundtrip statt vieler Einzel-Queries.
- Große Mengen: output="ids" anfordern und die Verrechnung (Schnittmengen,
  Differenzen, Joins, Zählungen) mit compute(code, refs) exakt ausführen —
  nie Mengen im Kopf schätzen. Tool-Ergebnisse über dem Schwellwert kommen
  als ref:<id> mit Sample; compute löst die refs auf.
- Schreiben: Vor der ersten Schreibaktion einmal welt_constitution aufrufen
  (serverseitig erzwungen). Kein Fakt ohne Quelle (erst welt_create_source).
  Unsicheres mit ehrlicher Confidence < 1.0 committen. Proposals einreichen
  ist erlaubt — sie werden NIEMALS selbst approved (kein welt_decide_proposal
  auf eigene Proposals; das Review macht der Mensch).
- Jeder Schreib-Tool-Call wird dem Nutzer zur Bestätigung vorgelegt; eine
  Ablehnung ist eine bewusste Entscheidung — nicht denselben Call erneut
  versuchen."""
    return _cached_prompt
