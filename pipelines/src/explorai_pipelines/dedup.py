"""Exact and near-duplicate detection."""

import hashlib

import numpy as np
from datasketch import MinHash

from explorai_pipelines.clean import normalize

NUM_PERM = 128
SHINGLE_SIZE = 5


def text_hash(text: str) -> str:
    """SHA-256 of the normalized text: equal for texts that only differ in form."""
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def _minhash(text: str, num_perm: int) -> MinHash:
    words = normalize(text).lower().split()
    if len(words) < SHINGLE_SIZE:
        shingles = [" ".join(words)]
    else:
        shingles = [
            " ".join(words[i : i + SHINGLE_SIZE])
            for i in range(len(words) - SHINGLE_SIZE + 1)
        ]
    signature = MinHash(num_perm=num_perm)
    for shingle in shingles:
        signature.update(shingle.encode("utf-8"))
    return signature


def minhash_signature(text: str, num_perm: int = NUM_PERM) -> bytes:
    return bytes(_minhash(text, num_perm).hashvalues.astype("<u8").tobytes())


def _values(signature: bytes) -> "np.ndarray[tuple[int], np.dtype[np.uint64]]":
    return np.frombuffer(signature, dtype="<u8")


class NearDuplicateIndex:
    """Signature index keyed by an arbitrary string id.

    Similarity is the MinHash estimate of the Jaccard index: the fraction of
    equal signature slots. The scan is linear in the number of stored
    signatures, which is fine for a corpus of thousands of documents. An LSH
    index (datasketch's MinHashLSH) is the scalable replacement, but it is
    probabilistic around its threshold and not needed yet.
    """

    def __init__(self, threshold: float = 0.9) -> None:
        self._threshold = threshold
        self._signatures: dict[str, np.ndarray] = {}

    def add(self, key: str, signature: bytes) -> None:
        self._signatures.setdefault(key, _values(signature))

    def find(
        self, signature: bytes, ignore: frozenset[str] = frozenset()
    ) -> str | None:
        probe = _values(signature)
        best_key: str | None = None
        best_score = self._threshold
        for key, stored in sorted(self._signatures.items()):
            if key in ignore:
                continue
            score = float(np.mean(stored == probe))
            if score >= best_score:
                best_key, best_score = key, score
        return best_key
