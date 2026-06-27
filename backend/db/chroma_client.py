# ============================================================
# ResiChain — ChromaDB Client
# Vector store for RAG: historical disruption reports
# Fix: Uses upsert with content hash IDs to prevent duplicates
# ============================================================

import chromadb
import hashlib
import os
import logging

logger = logging.getLogger(__name__)

_client = None
_collection = None

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
COLLECTION_NAME = "disruption_reports"

async def init_chroma():
    """
    Called on FastAPI startup.
    Connects to ChromaDB and gets/creates the collection.
    Fix: Stores embedding model in metadata to detect mismatches.
    """
    global _client, _collection

    _client = chromadb.HttpClient(
        host=os.getenv("CHROMA_HOST", "chromadb"),
        port=int(os.getenv("CHROMA_PORT", 8000))
    )

    # Verify connection
    _client.heartbeat()

    # Get or create collection with embedding model metadata
    _collection = _client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={
            "embedding_model": EMBEDDING_MODEL,
            "description": "Historical energy disruption reports for RAG"
        }
    )

    # Check for embedding model mismatch (Fix for ChromaDB model mismatch)
    existing_model = _collection.metadata.get("embedding_model")
    if existing_model and existing_model != EMBEDDING_MODEL:
        raise RuntimeError(
            f"ChromaDB embedding model mismatch! "
            f"Collection has '{existing_model}' but config uses '{EMBEDDING_MODEL}'. "
            f"Delete the collection and re-seed."
        )

    count = _collection.count()
    logger.info(f"ChromaDB connected. Collection '{COLLECTION_NAME}' has {count} documents.")

def get_collection():
    """Returns the ChromaDB collection."""
    if _collection is None:
        raise RuntimeError("ChromaDB not initialised. Call init_chroma() first.")
    return _collection

def _make_doc_id(text: str) -> str:
    """
    Creates a stable unique ID from document content.
    Fix: Prevents duplicate documents on re-seeding.
    Same content = same hash = upsert replaces instead of duplicating.
    """
    return hashlib.md5(text.encode()).hexdigest()

async def add_document(text: str, metadata: dict = None):
    """
    Adds a document to ChromaDB with deduplication.
    Uses content hash as ID so the same document is never added twice.
    """
    collection = get_collection()
    doc_id = _make_doc_id(text)

    collection.upsert(  # upsert = insert or replace, never duplicate
        documents=[text],
        ids=[doc_id],
        metadatas=[metadata or {}]
    )
    return doc_id

async def search_similar(query: str, n_results: int = 3) -> list:
    """
    Agent 2 uses this.
    Finds the most similar historical disruption reports to the current event.
    Returns list of (document, similarity_score, metadata) tuples.
    """
    collection = get_collection()

    if collection.count() == 0:
        logger.warning("ChromaDB collection is empty. No RAG context available.")
        return []

    results = collection.query(
        query_texts=[query],
        n_results=min(n_results, collection.count())
    )

    output = []
    if results and results["documents"]:
        for i, doc in enumerate(results["documents"][0]):
            distance = results["distances"][0][i] if results.get("distances") else 1.0
            similarity = 1 - distance  # Convert distance to similarity
            meta = results["metadatas"][0][i] if results.get("metadatas") else {}
            output.append({
                "document": doc,
                "similarity": round(similarity, 4),
                "metadata": meta
            })

    return output

async def seed_sample_historical_events():
    """
    Seeds ChromaDB with sample historical disruption summaries.
    In production, replace these with real EIA/PPAC PDF extracts.
    """
    sample_events = [
        {
            "text": "2019 Abqaiq Attack: Drone strikes on Saudi Aramco facilities at Abqaiq and Khurais on September 14, 2019. Initial disruption of 5.7 million barrels per day, approximately 5% of global supply. Brent crude spiked 15% on the day, the largest single-day jump since 1991 Gulf War. Supply restored within 2 weeks. Severity: 8/10.",
            "metadata": {"year": 2019, "event_type": "infrastructure_attack", "corridor": "Gulf", "price_impact_pct": 15, "duration_days": 14}
        },
        {
            "text": "2024 Red Sea Crisis: Houthi rebel attacks on commercial shipping in the Red Sea beginning November 2023, escalating through 2024. Over 100 vessels attacked or threatened. Major shipping lines rerouted via Cape of Good Hope adding 10-14 days transit time and $1M+ per voyage in fuel costs. Oil tanker diversions increased significantly.",
            "metadata": {"year": 2024, "event_type": "maritime_attack", "corridor": "Red_Sea", "price_impact_pct": 5, "duration_days": 180}
        },
        {
            "text": "1990 Gulf War Oil Shock: Iraq invasion of Kuwait August 1990 removed 4.3 million barrels per day from global markets. Brent crude rose from $17 to $36 per barrel. Saudi Arabia and other OPEC members increased production to compensate. Strategic Petroleum Reserve releases coordinated by IEA member countries.",
            "metadata": {"year": 1990, "event_type": "conflict", "corridor": "Hormuz", "price_impact_pct": 112, "duration_days": 210}
        },
        {
            "text": "2011 Libya Civil War: Libyan crude production collapsed from 1.6 million to 0.1 million barrels per day during civil war. IEA coordinated strategic reserve release of 60 million barrels. Light sweet crude premium spiked due to Libyan grade being preferred by European refineries.",
            "metadata": {"year": 2011, "event_type": "conflict", "corridor": "Mediterranean", "price_impact_pct": 20, "duration_days": 180}
        },
        {
            "text": "2019 Iran Sanctions Escalation: US reimposition of Iran sanctions in 2018-2019 removed approximately 1-2 million barrels per day of Iranian crude from global markets. India received sanction waivers initially but was forced to cut Iranian imports to zero by May 2019. India replaced Iranian supply primarily with Saudi and Iraqi crude at higher cost.",
            "metadata": {"year": 2019, "event_type": "sanctions", "corridor": "Hormuz", "price_impact_pct": 8, "duration_days": 365}
        },
    ]

    for event in sample_events:
        await add_document(event["text"], event["metadata"])

    logger.info(f"Seeded {len(sample_events)} historical events into ChromaDB") 