"""Lokales Tool compute(code, refs): JavaScript in einer quickjs-Sandbox.

Kein Netzwerk, kein Dateisystem (die Engine hat schlicht keine IO-APIs),
3 Sekunden CPU-Limit, Memory-Cap. Result-Store-Einträge werden als
Variablen injiziert; damit rechnet das Modell Schnittmengen, Differenzen,
Zählungen und Joins exakt, statt Mengen im Kopf zu schätzen.
"""

import json
import re
from typing import Any

import quickjs

from ..errors import ValidationError, WeltmodellError

TIME_LIMIT_SECONDS = 3
MEMORY_LIMIT_BYTES = 128 * 1024 * 1024

_VAR_NAME = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")

COMPUTE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "compute",
        "description": (
            "Führt JavaScript in einer Sandbox aus (kein Netz, kein "
            "Dateisystem, 3s-Limit). code ist ein FUNKTIONSKÖRPER — das "
            "Ergebnis mit `return` zurückgeben. refs mappt Variablennamen "
            "auf Result-Store-Referenzen (ref:<id>); die Einträge stehen im "
            "Code unter diesen Namen als Werte bereit. Für exakte "
            "Schnittmengen, Differenzen, Zählungen und Joins über große "
            "Tool-Ergebnisse — nie Mengen im Kopf schätzen. Beispiel: "
            'refs={"a":"ref:…","b":"ref:…"}, code="const s=new Set(b.ids); '
            'return a.ids.filter(x=>s.has(x))".'
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "JavaScript-Funktionskörper mit return.",
                },
                "refs": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Variablenname → ref:<id> aus dem Result-Store.",
                },
            },
            "required": ["code"],
        },
    },
}


class ComputeError(WeltmodellError):
    """Sandbox-Fehler (Syntax, Laufzeit, Timeout, Memory)."""


def run_compute(code: str, refs: dict[str, Any] | None = None) -> Any:
    """refs: Variablenname → bereits aufgelöster Wert. Rückgabe ist der
    JSON-dekodierte return-Wert des Codes (unterliegt danach selbst dem
    Offloading)."""
    context = quickjs.Context()
    context.set_memory_limit(MEMORY_LIMIT_BYTES)
    context.set_time_limit(TIME_LIMIT_SECONDS)

    for name, value in (refs or {}).items():
        if not _VAR_NAME.match(name):
            raise ValidationError(f"Ungültiger Variablenname '{name}' in refs")
        # JSON ist gültiges JS-Literal (quickjs kann ES2019+ mit U+2028/29).
        context.eval(
            f"globalThis[{json.dumps(name)}] = "
            f"({json.dumps(value, ensure_ascii=False, default=str)});"
        )

    # Promises hart ablehnen: der Wrapper pumpt keine Job-Queue — ein
    # pending Promise (z. B. import('fs')) würde sonst still zu {} werden.
    wrapped = (
        "(function(){ const __r = (function(){\n" + code + "\n})();"
        " if (__r instanceof Promise)"
        "   throw new Error('Promises/async werden nicht unterstützt');"
        " return JSON.stringify(__r ?? null); })()"
    )
    try:
        raw = context.eval(wrapped)
    except quickjs.JSException as exc:
        raise ComputeError(f"JavaScript-Fehler: {exc}") from exc
    except Exception as exc:  # Memory-Limit wirft quickjs-intern anders
        raise ComputeError(f"Sandbox-Abbruch: {exc}") from exc
    if raw is None:
        return None
    return json.loads(raw)
