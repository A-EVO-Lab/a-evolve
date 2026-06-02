"""Shared text embedder for solve-time adaptation (and skill ranking).

Pluggable backend, selected by the ``SKILLROUTER_EMB_MODEL_OR_PATH`` env var
or the ``embed_backend``/``embed_model`` adaptation config:

  - "bge" (default): ``BAAI/bge-base-en-v1.5`` via sentence-transformers.
    CPU-friendly, no GPU; this is what cl_bench has always used.
  - "skillrouter": SkillRouter's encoder ``pipizhao/SkillRouter-Embedding-0.6B``
    (https://github.com/zhengyanzhao1997/SkillRouter) via sentence-transformers
    / transformers. Requires the model to be downloadable (and ideally a GPU);
    falls back to BGE with a warning if it cannot load.

Either way embeddings are L2-normalized, so cosine similarity is a dot
product. Extracted from cl_bench so cl_bench + the adaptation CatalogIndex
share one lazily-loaded model + cache.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "BAAI/bge-base-en-v1.5"
_SKILLROUTER_MODEL = "pipizhao/SkillRouter-Embedding-0.6B"

_embedder = None
_embedder_model_id = None
_embedder_lock = threading.Lock()

_emb_cache: dict[tuple, Any] = {}
_emb_cache_lock = threading.Lock()


def _resolve_model() -> str:
    """Pick the embedding model: env override > SkillRouter flag > BGE."""
    env = os.environ.get("SKILLROUTER_EMB_MODEL_OR_PATH")
    if env:
        return env
    backend = os.environ.get("ADAPTATION_EMBED_BACKEND", "bge").lower()
    if backend == "skillrouter":
        return _SKILLROUTER_MODEL
    return _DEFAULT_MODEL


def get_embedder():
    """Return the lazily-loaded sentence-transformers model (singleton).

    Loads the resolved model; if a non-default (e.g. SkillRouter) model fails
    to load, falls back to BGE so retrieval still runs.
    """
    global _embedder, _embedder_model_id
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                from sentence_transformers import SentenceTransformer
                model_id = _resolve_model()
                try:
                    _embedder = SentenceTransformer(model_id)
                    _embedder_model_id = model_id
                except Exception as e:
                    if model_id != _DEFAULT_MODEL:
                        logger.warning("Embedder %s failed to load (%s); "
                                       "falling back to %s", model_id, e, _DEFAULT_MODEL)
                        _embedder = SentenceTransformer(_DEFAULT_MODEL)
                        _embedder_model_id = _DEFAULT_MODEL
                    else:
                        raise
                logger.info("Loaded embedding model: %s", _embedder_model_id)
    return _embedder


def embed_texts(texts: list[str], *, use_cache: bool = True):
    """Embed a list of texts into an L2-normalized matrix (n x d)."""
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

    Assumes ``query_vec`` and rows of ``item_matrix`` are L2-normalized.
    """
    import numpy as np
    if item_matrix is None or len(item_matrix) == 0:
        return []
    sims = np.dot(item_matrix, query_vec)
    n = len(sims)
    k = max(0, min(k, n))
    if k == 0:
        return []
    idx = np.argpartition(-sims, k - 1)[:k]
    ranked = sorted(idx.tolist(), key=lambda i: float(sims[i]), reverse=True)
    return [(int(i), float(sims[i])) for i in ranked]
