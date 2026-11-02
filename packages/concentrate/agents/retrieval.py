"""Hybrid retrieval: BM25 + dense embeddings, fused with reciprocal rank fusion (RRF).

This is the "agent + top-k" retriever the baselines use. Embeddings sit behind a small
``Embedder`` protocol; the default is a local sentence-transformers model, and tests pass a
deterministic fake so they never download weights.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

import numpy as np
from rank_bm25 import BM25Okapi

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
RRF_K = 60  # standard RRF constant (Cormack et al., 2009)
_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class Embedder(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Return L2-normalized embeddings, shape (len(texts), dim)."""
        ...


class SentenceTransformerEmbedder:
    """Local embeddings; the model is downloaded once and cached by sentence-transformers."""

    def __init__(self, model: str = DEFAULT_EMBEDDING_MODEL) -> None:
        from sentence_transformers import SentenceTransformer

        self.name = model
        self._model = SentenceTransformer(model, device="cpu")

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        vectors = self._model.encode(
            list(texts), normalize_embeddings=True, convert_to_numpy=True, batch_size=32
        )
        return np.asarray(vectors, dtype=np.float32)


def rrf(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> list[int]:
    """Fuse several rankings (lists of doc indices, best first) into one."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, doc in enumerate(ranking):
            scores[doc] = scores.get(doc, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda d: (-scores[d], d))


class HybridIndex:
    """BM25 + (optional) embeddings over a fixed list of documents."""

    def __init__(self, docs: Sequence[str], embedder: Embedder | None = None) -> None:
        self.docs = list(docs)
        self._bm25 = BM25Okapi([tokenize(d) or ["_"] for d in self.docs])
        self._embedder = embedder
        self._vectors = embedder.embed(self.docs) if embedder and self.docs else None

    def bm25_ranking(self, query: str) -> list[int]:
        scores = self._bm25.get_scores(tokenize(query))
        return [int(i) for i in np.argsort(-scores, kind="stable") if scores[i] > 0]

    def dense_ranking(self, query: str) -> list[int]:
        if self._embedder is None or self._vectors is None:
            return []
        q = self._embedder.embed([query])[0]
        return [int(i) for i in np.argsort(-(self._vectors @ q), kind="stable")]

    def search(self, query: str, k: int) -> list[int]:
        """Top-``k`` doc indices by RRF of BM25 and dense rankings."""
        rankings = [r for r in (self.bm25_ranking(query), self.dense_ranking(query)) if r]
        return rrf(rankings)[:k] if rankings else []
