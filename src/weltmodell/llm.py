"""LLM-Extraktor (Spec §7, Stufe EXTRACT) über OpenRouter.

Die strukturelle Garantie gegen Drift (§7.1) hängt NICHT an der Modellgüte:
Das Modell bekommt die Registry als erlaubtes Vokabular und liefert nur
Kandidaten-Statements. Alles läuft danach durch RESOLVE → VALIDATE → COMMIT;
erfundene Prädikate werden dort rejected oder landen als Proposal im Gate.
"""

import json
import re
from typing import Any

import httpx

from .config import get_llm_model, get_openrouter_key
from .errors import WeltmodellError
from .pipeline import CandidateStatement, EntityRef, ExtractionResult

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """Du extrahierst Fakten aus einem Quelldokument in ein Weltmodell.

Antworte mit GENAU EINEM JSON-Objekt, ohne Erklärtext:
{
  "statements": [
    {
      "subject": {"type_id": "...", "label": "...", "identifiers": {"email": "..."}},
      "predicate_id": "...",
      "value": {"type": "string|number|quantity|datetime|geo|json", ...}
             ODER {"type": "entity", "ref": {"type_id": "...", "label": "...", "identifiers": {}}},
      "confidence": 0.0-1.0,
      "valid_from": "ISO-Datum oder null",
      "qualifiers": [{"predicate_id": "...", "value": {"type": "...", ...}}]
    }
  ],
  "proposed_predicates": [
    {"predicate_id": "...", "label": "...", "domain_type": "...",
     "range_kind": "string", "cardinality": "1:n", "rationale": "..."}
  ],
  "proposed_types": []
}

Wertformate: string→{"type":"string","text":...}, number→{"type":"number","number":...},
quantity→{"type":"quantity","number":...,"unit":...}, datetime→{"type":"datetime","datetime":...},
geo→{"type":"geo","lat":...,"lon":...}.

Harte Regeln:
- NUR predicate_id und type_id aus dem erlaubten Vokabular verwenden. Nie erfinden.
- Passt ein Fakt auf kein Prädikat: NICHT schreiben, sondern unter proposed_predicates
  vorschlagen (mit domain/range/cardinality).
- Identifiers (email, wikidata_qid, account_uri) immer mitgeben, wenn im Dokument vorhanden.
- Confidence ehrlich schätzen; 1.0 nur bei direkt belegten Fakten.
- Ereignisse (Erwähnungen, Vorfälle) sind Occurrents (z. B. Mention), keine Continuants."""


class LLMExtractor:
    """Extractor-Protokoll-Implementierung, constrained auf die Registry."""

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 timeout: float = 120.0):
        self.model = model or get_llm_model()
        self.api_key = api_key or get_openrouter_key()
        self.timeout = timeout
        if not self.api_key:
            raise WeltmodellError(
                "OPENROUTER_API_KEY fehlt (.env oder Umgebung)"
            )

    def extract(self, raw: dict, vocabulary: dict) -> ExtractionResult:
        payload = self._parse(self._complete(self._user_prompt(raw, vocabulary)))
        return self._to_result(payload)

    # --- Bausteine -----------------------------------------------------------

    def _user_prompt(self, raw: dict, vocabulary: dict) -> str:
        types = [
            {"id": t["id"], "parent": t["parent_id"], "kind": t["kind"]}
            for t in vocabulary["types"]
        ]
        predicates = [
            {
                "id": p["id"], "label": p["label"],
                "domain": p["domain_type"] or p["domain_interface"],
                "range_kind": p["range_kind"], "range_type": p["range_type"],
            }
            for p in vocabulary["predicates"]
        ]
        return (
            f"ERLAUBTE TYPEN:\n{json.dumps(types, ensure_ascii=False)}\n\n"
            f"ERLAUBTE PRÄDIKATE:\n{json.dumps(predicates, ensure_ascii=False)}\n\n"
            f"QUELLDOKUMENT:\n{json.dumps(raw, ensure_ascii=False)}"
        )

    def _complete(self, user_prompt: str) -> str:
        response = httpx.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise WeltmodellError(f"Unerwartete OpenRouter-Antwort: {data}") from exc

    @staticmethod
    def _parse(content: str) -> dict:
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < 0:
            raise WeltmodellError(f"Keine JSON-Antwort vom Modell: {content[:200]}")
        return json.loads(text[start : end + 1])

    @staticmethod
    def _to_result(payload: dict) -> ExtractionResult:
        result = ExtractionResult(
            proposed_predicates=payload.get("proposed_predicates") or [],
            proposed_types=payload.get("proposed_types") or [],
        )

        def ref(node: dict[str, Any]) -> EntityRef:
            return EntityRef(
                type_id=node["type_id"],
                label=node.get("label"),
                identifiers=node.get("identifiers") or {},
            )

        for s in payload.get("statements") or []:
            value = s.get("value") or {}
            parsed_value: dict[str, Any] | EntityRef = (
                ref(value["ref"]) if value.get("type") == "entity" and value.get("ref")
                else value
            )
            result.statements.append(
                CandidateStatement(
                    subject=ref(s["subject"]),
                    predicate_id=s["predicate_id"],
                    value=parsed_value,
                    confidence=float(s.get("confidence", 0.7)),
                    valid_from=s.get("valid_from"),
                    valid_to=s.get("valid_to"),
                    qualifiers=s.get("qualifiers") or [],
                )
            )
        return result
