"""
Embedding backends (Section 6.5.1 of the spec).

Every tool is embedded from a canonical string built by
`Tool.embedding_input()`:

    f"{name}. {description} Tags: {tag names}"

Two interchangeable backends produce *identically shaped* 384-dim, unit-length
vectors so the entire downstream stack (pgvector storage, cosine distance
ranking, preference-vector maths) is backend-agnostic:

  * SentenceTransformerEmbedder — the real model, `all-MiniLM-L6-v2`
    (sentence-transformers, 384-dim, CPU friendly). Needs network access the
    first time it loads the model weights.

  * HashingEmbedder — a deterministic offline fallback implementing the signed
    feature-hashing trick over unigrams + bigrams. It yields cosine similarity
    roughly proportional to lexical overlap: no learned semantics, but stable,
    instant, dependency-free and perfect for tests / air-gapped demos.

IMPORTANT: embeddings are NEVER computed inside a request/response cycle for
catalog writes — see catalog/tasks.py. (Embedding a single *search query* at
request time is explicitly required by the spec's semantic search layer and is
served from an LRU cache with a lazily-loaded singleton model.)
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from functools import lru_cache

import numpy as np
from django.conf import settings

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
class BaseEmbedder:
    """Common interface: batch-encode texts into unit-length rows."""

    name = "base"

    def encode(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


class SentenceTransformerEmbedder(BaseEmbedder):
    """Wraps sentence-transformers/all-MiniLM-L6-v2 (384-dim)."""

    name = "sentence-transformers"

    def __init__(self, model_name: str, dim: int) -> None:
        from sentence_transformers import SentenceTransformer  # heavy import

        self._model = SentenceTransformer(model_name)
        actual = self._model.get_sentence_embedding_dimension()
        if actual != dim:
            # Refuse to silently mix dimensions — pgvector columns are fixed.
            raise ValueError(
                f"{model_name} produces {actual}-dim vectors but this deployment "
                f"expects {dim}. Rebuild the schema or change EMBEDDING_MODEL."
            )
        self._model_name = model_name

    def encode(self, texts: list[str]) -> np.ndarray:
        vectors = self._model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            normalize_embeddings=True,  # unit length -> dot product == cosine
            convert_to_numpy=True,
        )
        return np.asarray(vectors, dtype=np.float32)


class HashingEmbedder(BaseEmbedder):
    """Deterministic offline embedder via the signed feature-hashing trick.

    Each unigram/bigram of the normalised text is hashed into one of `dim`
    buckets with a pseudo-random sign; bucket counts are accumulated and the
    result is L2-normalised. Two texts therefore end up "similar" when they
    share vocabulary — a cheap stand-in for MiniLM when the model can't be
    downloaded (restricted networks, CI, unit tests).
    """

    name = "hash"

    def __init__(self, dim: int) -> None:
        self.dim = dim

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _tokens(text: str) -> list[str]:
        return _WORD_RE.findall(text.lower())

    @classmethod
    def _ngrams(cls, text: str) -> list[str]:
        tokens = cls._tokens(text)
        grams = list(tokens)
        grams.extend(f"{a} {b}" for a, b in zip(tokens, tokens[1:]))
        return grams

    def _bucket_and_sign(self, gram: str) -> tuple[int, int]:
        digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "little")
        return value % self.dim, 1 if (value >> 63) & 1 else -1

    # -- interface ------------------------------------------------------------
    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for gram in self._ngrams(text):
                bucket, sign = self._bucket_and_sign(gram)
                out[row, bucket] += sign
        # L2-normalise (zero vectors — e.g. empty text — stay zero).
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        np.divide(out, norms, out=out, where=norms > 0)
        return out


# ---------------------------------------------------------------------------
# Singleton factory (backend selected via settings.EMBEDDING_BACKEND)
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_embedder: BaseEmbedder | None = None


def _build_embedder() -> BaseEmbedder:
    choice = settings.EMBEDDING_BACKEND
    if choice in ("sentence-transformers", "auto"):
        try:
            return SentenceTransformerEmbedder(settings.EMBEDDING_MODEL, settings.EMBEDDING_DIM)
        except Exception as exc:  # pragma: no cover - depends on network/env
            if choice == "sentence-transformers":
                raise
            logger.warning(
                "sentence-transformers backend unavailable (%s); "
                "falling back to the deterministic hashing embedder.",
                exc,
            )
    if choice in ("hash", "auto"):
        return HashingEmbedder(dim=settings.EMBEDDING_DIM)
    raise ValueError(f"Unknown EMBEDDING_BACKEND: {choice!r}")


def get_embedder() -> BaseEmbedder:
    """Process-wide singleton — the model is loaded at most once."""
    global _embedder
    if _embedder is None:
        with _lock:
            if _embedder is None:
                _embedder = _build_embedder()
    return _embedder


def embedding_backend_name() -> str:
    try:
        return get_embedder().name
    except Exception:  # pragma: no cover
        return "unavailable"


# ---------------------------------------------------------------------------
# Query-time embedding for semantic search. Embedding a search query at request
# time is cheap (one short string, singleton model) and required for the
# semantic layer — unlike catalog writes, which always go through the queue.
# A small LRU keeps repeated queries free.
# ---------------------------------------------------------------------------
@lru_cache(maxsize=512)
def embed_query_cached(text: str) -> tuple[float, ...]:
    return tuple(float(v) for v in get_embedder().encode_one(text))


def embed_query(text: str) -> np.ndarray:
    return np.asarray(embed_query_cached(text), dtype=np.float32)
