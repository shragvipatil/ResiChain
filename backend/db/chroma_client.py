"""
backend/db/chroma_client.py

Shared ChromaDB helper for ResiChain.

Implements:
- Collection: disruptionreports
- Embedding model: all-MiniLM-L6-v2
- Fix 4: collection.upsert with SHA-256 content hash IDs
- Fix 12: verify collection metadata embedding_model at startup
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

import chromadb
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

COLLECTION_NAME = "disruptionreports"  # must match agents/agent2.py's COLLECTION_NAME — this was previously "disruptionreports" (no underscore), a different collection than the one the running app queries
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

CHROMA_HOST = os.getenv("CHROMA_HOST", os.getenv("CHROMAHOST", "chromadb"))
CHROMA_PORT = int(os.getenv("CHROMA_PORT", os.getenv("CHROMAPORT", "8000")))

_chroma_client: Any = None
_collection: Any = None
_embedder: SentenceTransformer | None = None


def init_chroma() -> None:
    global _chroma_client, _collection

    _chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)


    _collection = _chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"embedding_model": EMBEDDING_MODEL_NAME},
    )

    metadata = getattr(_collection, "metadata", {}) or {}
    stored_model = metadata.get("embedding_model")

    if stored_model != EMBEDDING_MODEL_NAME:
        raise ValueError(
            f"ChromaDB model mismatch for collection '{COLLECTION_NAME}': "
            f"stored='{stored_model}', current='{EMBEDDING_MODEL_NAME}'."
        )

    logger.info(
        "Chroma initialized host=%s port=%s collection=%s count=%d",
        CHROMA_HOST,
        CHROMA_PORT,
        COLLECTION_NAME,
        _collection.count(),
    )


def get_collection():
    if _collection is None:
        raise RuntimeError("Chroma not initialized. Call init_chroma() first.")
    return _collection


def get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _embedder


def hash_document(content: str) -> str:
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()


def embed_text(text: str) -> list[float]:
    model = get_embedder()
    vector = model.encode(text)
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    return [float(x) for x in vector]


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = get_embedder()
    vectors = model.encode(texts)
    if hasattr(vectors, "tolist"):
        vectors = vectors.tolist()
    return [[float(x) for x in vec] for vec in vectors]


def upsert_documents(documents: list[str], metadatas: list[dict[str, Any]] | None = None) -> int:
    collection = get_collection()

    if not documents:
        return collection.count()

    paired: list[tuple[str, dict[str, Any]]] = []
    if metadatas is None:
        metadatas = [{} for _ in documents]

    if len(metadatas) != len(documents):
        raise ValueError("Length of metadatas must match length of documents.")

    for doc, meta in zip(documents, metadatas):
        cleaned = (doc or "").strip()
        if cleaned:
            paired.append((cleaned, meta or {}))

    if not paired:
        return collection.count()

    cleaned_docs = [doc for doc, _ in paired]
    cleaned_metas = [meta for _, meta in paired]
    ids = [hash_document(doc) for doc in cleaned_docs]
    embeddings = embed_texts(cleaned_docs)

    collection.upsert(
        ids=ids,
        documents=cleaned_docs,
        embeddings=embeddings,
        metadatas=cleaned_metas,
    )
    return collection.count()


def seed_historical_events(events: list[dict[str, Any]]) -> int:
    """
    Expected format:
    [
      {
        "text": "...",
        "date": "2021-03-23",
        "corridor": "Suez",
        "severity": "high",
        "outcome": "..."
      }
    ]
    """
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []

    for event in events:
        text = str(event.get("text", "")).strip()
        if not text:
            continue

        documents.append(text)
        metadatas.append(
            {
                "date": event.get("date", ""),
                "corridor": event.get("corridor", "Unknown"),
                "severity": event.get("severity", "medium"),
                "outcome": event.get("outcome", ""),
            }
        )

    total = upsert_documents(documents, metadatas)
    logger.info("Seeded Chroma historical events. total=%d", total)
    return total


def _distance_to_similarity(distance: float) -> float:
    similarity = 1.0 - (float(distance) / 2.0)
    return max(0.0, min(1.0, similarity))


def query_similar(text: str, n_results: int = 3) -> list[dict[str, Any]]:
    collection = get_collection()

    count = collection.count()
    if count == 0:
        return []

    query_embedding = embed_text(text)
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(n_results, count),
        include=["documents", "metadatas", "distances"],
    )

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    output: list[dict[str, Any]] = []
    for doc, meta, dist in zip(documents, metadatas, distances):
        output.append(
            {
                "text": doc,
                "metadata": meta or {},
                "similarity": _distance_to_similarity(dist),
            }
        )

    return output