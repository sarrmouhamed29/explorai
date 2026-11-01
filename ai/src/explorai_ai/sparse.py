"""Local sparse encoder: hashed tokens with BM25-style term weights.

No model download, so tests, CI and containers need nothing. Qdrant applies the IDF
part at query time (collection configured with the IDF modifier). The hash is
blake2b, not Python's hash(), which is salted per process (PYTHONHASHSEED) and would
make indexed vectors unmatchable after a restart.
"""

import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

K1 = 1.2


@dataclass(frozen=True)
class SparseVector:
    indices: list[int]
    values: list[float]


def tokenize(text: str) -> list[str]:
    """Lowercase, fold accents, split on anything that is not a letter or digit.

    NFKD separates accents into combining marks, which are dropped, so "cafe" and
    "cafe" with an accent share a token. Non-Latin scripts are kept as word tokens.
    """
    decomposed = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return re.findall(r"[^\W_]+", folded)


def _index(token: str) -> int:
    digest = hashlib.blake2b(token.encode(), digest_size=4).digest()
    return int.from_bytes(digest, "big")


def encode_document(text: str) -> SparseVector:
    """Term-frequency weights saturating at K1 + 1, one entry per distinct index."""
    counts: Counter[int] = Counter(_index(t) for t in tokenize(text))
    indices = sorted(counts)
    values = [counts[i] * (K1 + 1) / (counts[i] + K1) for i in indices]
    return SparseVector(indices, values)


def encode_query(text: str) -> SparseVector:
    indices = sorted({_index(t) for t in tokenize(text)})
    return SparseVector(indices, [1.0] * len(indices))
