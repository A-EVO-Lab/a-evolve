"""Shared text embedder for solve-time adaptation (and skill ranking).

Extracted verbatim from the embedder that has long lived in
``benchmarks/cl_bench.py`` so multiple consumers (cl_bench skill ranking,
the adaptation CatalogIndex) share one lazily-loaded model + cache rather
than each loading their own.

Model: ``BAAI/bge-base-en-v1.5`` via ``sentence_transformers`` (imported
lazily so this module is import-safe even where the dependency is absent —
the import only happens when an embedding is actually requested).

Behavior-preserving contract for cl_bench: ``get_embedder()`` returns the
same singleton, and embeddings are L2-normalized with the same call
(``encode(..., normalize_embeddings=True, show_progress_bar=False)``), so
cosine similarity is a plain dot product.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_EMBED_MODEL = "BAAI/bge-base-en-v1.5"

_embedder = None
_embedder_lock = threading.Lock()

# Cache: tuple(texts) -> normalized embedding matrix. Mirrors cl_bench's
# "only keep the latest set" cache so memory stays bounded.
_emb_cache: dict[tuple, Any] = {}
_emb_cache_lock = threading.Lock()


def get_embedder():
    """Return the lazily-loaded sentence-transformers model (singleton)."""
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                from sentence_transformers import SentenceTransformer
                _embedder = SentenceTransformer(_EMBED_MODEL)
                logger.info("Loaded embedding model: %s", _EMBED_MODEL)
    return _embedder


def embed_texts(texts: list[str], *, use_cache: bool = True):
    """Embed a list of texts into an L2-normalized matrix (n x d).

    When ``use_cache`` is True, repeated identical text-lists return the
    cached matrix (latest-set-only, like cl_bench).
    """
    key = tuple(texts)
    if use_cache:
        with _emb_cache_lock:
            cached = _emb_cache.get(key)
        if cached is not None:
            return cached
    embedder = get_embedder()
    embs = embedder.encode(
        list(texts), normalize_embeddings=True, show_progress_bar=False
    )
    if use_cache:
        with _emb_cache_lock:
            _emb_cache.clear()  # only keep the latest set
            _emb_cache[key] = embs
    return embs


def embed_query(text: str):
    """Embed a single query string into a normalized vector (uncached)."""
    embedder = get_embedder()
    return embedder.encode(
        [text], normalize_embeddings=True, show_progress_bar=False
    )[0]


def cosine_topk(query_vec, item_matrix, k: int) -> list[tuple[int, float]]:
    """Return [(item_index, score), ...] for the top-k items by cosine.

    Assumes both ``query_vec`` and rows of ``item_matrix`` are already
    L2-normalized (as produced by ``embed_texts``/``embed_query``), so
    cosine is a dot product.
    """
    import numpy as np
    if item_matrix is None or len(item_matrix) == 0:
        return []
    sims = np.dot(item_matrix, query_vec)
    n = len(sims)
    k = max(0, min(k, n))
    if k == 0:
        return []
    # argpartition for top-k, then sort those by score desc.
    idx = np.argpartition(-sims, k - 1)[:k]
    ranked = sorted(idx.tolist(), key=lambda i: float(sims[i]), reverse=True)
    return [(int(i), float(sims[i])) for i in ranked]
