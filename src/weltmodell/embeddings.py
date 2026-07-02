"""Embedding-Provider für Dedup & semantische Suche (Spec §7.2).

Embeddings sind ableitbar und jederzeit neu berechenbar (Invariante 1) —
der Provider ist deshalb austauschbar. Default ist ein deterministischer
Feature-Hashing-Embedder (Char-Trigramme): kein semantisches Modell, aber
robust für Fuzzy-Namensähnlichkeit und ohne externe Abhängigkeit. Ein
echtes Modell (z. B. API-Embeddings) implementiert dasselbe Protokoll.
"""

import hashlib
import math
from typing import Protocol

DIM = 1024  # muss zu vector(1024) in entity.embedding passen


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    def embed(self, text: str) -> list[float]:
        vec = [0.0] * DIM
        padded = f"  {text.lower().strip()}  "
        for i in range(len(padded) - 2):
            trigram = padded[i : i + 3]
            h = int.from_bytes(hashlib.sha1(trigram.encode()).digest()[:8], "big")
            sign = 1.0 if (h >> 63) & 1 else -1.0
            vec[h % DIM] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


_default: EmbeddingProvider = HashingEmbedder()


def get_embedder() -> EmbeddingProvider:
    return _default
