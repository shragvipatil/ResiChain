"""
tests/test_agent2.py
====================
Unit tests for agents/agent2.py

All external I/O is mocked:
  - ChromaDB  (_collection, _embedder)
  - Gemini    (genai.GenerativeModel)
  - Redis     (redis.from_url  → _get_redis)
  - spaCy     (spacy.load)

Run with:
    docker exec -it resichain_fastapi python -m pytest tests/test_agent2.py -v
"""

from __future__ import annotations

import hashlib
import json
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

PATCH_COLLECTION  = "agents.agent2._collection"
PATCH_EMBEDDER    = "agents.agent2._embedder"
PATCH_GENAI       = "agents.agent2.genai"
PATCH_REDIS_URL   = "agents.agent2._get_redis"
PATCH_SPACY_LOAD  = "agents.agent2.spacy"
PATCH_CHROMA_HTTP = "agents.agent2.chromadb.HttpClient"
PATCH_ST_MODEL    = "agents.agent2.SentenceTransformer"

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_EVENT = {
    "event_id":    "test-uuid-001",
    "event":       "Iran threatens to close the Strait of Hormuz in response to US sanctions.",
    "corridor":    "Hormuz",
    "stage":       "CONFIRMED",
    "confidence":  0.87,
}

MOCK_SIMILAR = [
    {
        "text":       "2019 Abqaiq attack disrupted 5% of global oil supply.",
        "metadata":   {"date": "2019-09-14", "corridor": "Hormuz", "severity": "high"},
        "similarity": 0.82,
    },
    {
        "text":       "2011 Libya civil war removed 1.6 mbd from global supply.",
        "metadata":   {"date": "2011-02-17", "corridor": "Unknown", "severity": "high"},
        "similarity": 0.61,
    },
    {
        "text":       "2003 Iraq invasion spiked Brent by $10.",
        "metadata":   {"date": "2003-03-20", "corridor": "Hormuz", "severity": "high"},
        "similarity": 0.55,
    },
]

MOCK_GEMINI_JSON = {
    "event_type":                "ThreatOfClosure",
    "location":                  "Strait of Hormuz",
    "corridor_affected":         "Hormuz",
    "severity":                  8,
    "disruption_type":           "GeopoliticalThreat",
    "similar_historical_events": ["2019 Abqaiq attack"],
    "key_entities":              ["Iran", "US"],
    "extraction_method":         "gemini_structured",
}


def _make_mock_collection(doc_count: int = 50) -> MagicMock:
    col = MagicMock()
    col.count.return_value = doc_count
    col.metadata = {"embedding_model": "all-MiniLM-L6-v2"}
    col.query.return_value = {
        "documents": [[e["text"] for e in MOCK_SIMILAR]],
        "metadatas": [[e["metadata"] for e in MOCK_SIMILAR]],
        "distances": [[1.0 - e["similarity"] * 2 for e in MOCK_SIMILAR]],  # inverse of our formula
    }
    return col


def _make_mock_embedder() -> MagicMock:
    import numpy as np
    emb = MagicMock()
    emb.encode.return_value = [0.1] * 384
    return emb


# ===========================================================================
# Tests — init_chromadb / Fix 12
# ===========================================================================

class TestInitChromaDB:

    def test_init_sets_collection_and_embedder(self):
        """init_chromadb() must set module-level _collection and _embedder."""
        mock_col = _make_mock_collection()
        mock_client = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_col

        with patch(PATCH_CHROMA_HTTP, return_value=mock_client), \
             patch(PATCH_ST_MODEL, return_value=_make_mock_embedder()):
            import agents.agent2 as a2
            a2.init_chromadb()
            assert a2._collection is not None
            assert a2._embedder is not None

    def test_model_mismatch_raises_value_error(self):
        """Fix 12 — stored model ≠ config model must raise ValueError."""
        mock_col = MagicMock()
        mock_col.count.return_value = 5
        mock_col.metadata = {"embedding_model": "different-model-v99"}

        mock_client = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_col

        with patch(PATCH_CHROMA_HTTP, return_value=mock_client), \
             patch(PATCH_ST_MODEL, return_value=_make_mock_embedder()):
            import agents.agent2 as a2
            with pytest.raises(ValueError, match="different-model-v99"):
                a2.init_chromadb()

    def test_model_match_does_not_raise(self):
        """Fix 12 — matching model name must not raise."""
        mock_col = _make_mock_collection()
        mock_client = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_col

        with patch(PATCH_CHROMA_HTTP, return_value=mock_client), \
             patch(PATCH_ST_MODEL, return_value=_make_mock_embedder()):
            import agents.agent2 as a2
            a2.init_chromadb()   # must not raise

    def test_no_metadata_key_does_not_raise(self):
        """Fresh collection with no metadata key must not raise."""
        mock_col = MagicMock()
        mock_col.count.return_value = 0
        mock_col.metadata = {}   # no embedding_model key yet
        mock_client = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_col

        with patch(PATCH_CHROMA_HTTP, return_value=mock_client), \
             patch(PATCH_ST_MODEL, return_value=_make_mock_embedder()):
            import agents.agent2 as a2
            a2.init_chromadb()   # must not raise


# ===========================================================================
# Tests — seed_historical_events / Fix 4
# ===========================================================================

class TestSeedHistoricalEvents:

    def _sample_events(self, n: int = 3) -> list[dict]:
        return [
            {
                "text":     f"Historical disruption event {i}.",
                "date":     f"2019-0{i}-01",
                "corridor": "Hormuz",
                "severity": "high",
                "outcome":  f"Outcome {i}",
            }
            for i in range(1, n + 1)
        ]

    def test_upsert_called_not_add(self):
        """Fix 4 — seed must call collection.upsert, never collection.add."""
        mock_col = _make_mock_collection()
        mock_col.count.return_value = 3

        with patch(PATCH_COLLECTION, mock_col), \
             patch(PATCH_EMBEDDER, _make_mock_embedder()):
            import agents.agent2 as a2
            a2._collection = mock_col
            a2._embedder   = _make_mock_embedder()
            a2.seed_historical_events(self._sample_events(3))
            mock_col.upsert.assert_called_once()
            mock_col.add.assert_not_called()

    def test_ids_are_sha256_hashes(self):
        """Fix 4 — each document ID must be the SHA-256 hash of its text."""
        mock_col = _make_mock_collection()
        mock_col.count.return_value = 3
        events = self._sample_events(2)

        with patch(PATCH_COLLECTION, mock_col), \
             patch(PATCH_EMBEDDER, _make_mock_embedder()):
            import agents.agent2 as a2
            a2._collection = mock_col
            a2._embedder   = _make_mock_embedder()
            a2.seed_historical_events(events)

            call_kwargs = mock_col.upsert.call_args[1]
            for ev, doc_id in zip(events, call_kwargs["ids"]):
                expected = hashlib.sha256(ev["text"].encode()).hexdigest()
                assert doc_id == expected

    def test_running_three_times_idempotent(self):
        """Fix 4 — running seed 3x must call upsert 3x with same IDs."""
        mock_col = _make_mock_collection()
        mock_col.count.return_value = 3
        events = self._sample_events(3)

        with patch(PATCH_COLLECTION, mock_col), \
             patch(PATCH_EMBEDDER, _make_mock_embedder()):
            import agents.agent2 as a2
            a2._collection = mock_col
            a2._embedder   = _make_mock_embedder()
            ids_per_run = []
            for _ in range(3):
                a2.seed_historical_events(events)
                call_kwargs = mock_col.upsert.call_args[1]
                ids_per_run.append(call_kwargs["ids"])

            assert ids_per_run[0] == ids_per_run[1] == ids_per_run[2]


# ===========================================================================
# Tests — extract_intelligence (Gemini happy path)
# ===========================================================================

class TestExtractIntelligenceGemini:

    def _run(self, event=None):
        import agents.agent2 as a2
        mock_col = _make_mock_collection()
        a2._collection = mock_col
        a2._embedder   = _make_mock_embedder()

        mock_response = MagicMock()
        mock_response.text = json.dumps(MOCK_GEMINI_JSON)

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        mock_genai = MagicMock()
        mock_genai.GenerativeModel.return_value = mock_model
        mock_genai.GenerationConfig = MagicMock()

        with patch(PATCH_GENAI, mock_genai):
            return a2.extract_intelligence(event or SAMPLE_EVENT)

    def test_returns_all_required_keys(self):
        result = self._run()
        for key in (
            "event_id", "original_event", "extracted", "similar_historical_events",
            "llm_extraction_score", "max_rag_similarity", "confidence",
            "extraction_method", "processed_at",
        ):
            assert key in result, f"Missing key: {key}"

    def test_confidence_formula_correct(self):
        """confidence = 0.4 × llm_score + 0.6 × max_rag_similarity."""
        result = self._run()
        llm_score = result["llm_extraction_score"]
        max_rag   = result["max_rag_similarity"]
        expected  = round(0.4 * llm_score + 0.6 * max_rag, 4)
        assert abs(result["confidence"] - expected) < 0.001

    def test_extraction_method_is_gemini(self):
        result = self._run()
        assert result["extraction_method"] == "gemini_structured"

    def test_event_id_preserved(self):
        result = self._run()
        assert result["event_id"] == SAMPLE_EVENT["event_id"]

    def test_similar_events_returned(self):
        result = self._run()
        assert len(result["similar_historical_events"]) == 3

    def test_corridor_extracted(self):
        result = self._run()
        assert result["extracted"]["corridor_affected"] == "Hormuz"


# ===========================================================================
# Tests — Fix 6: exponential back-off + spaCy fallback
# ===========================================================================

class TestGeminiFallback:

    def _run_with_failing_gemini(self, event=None, fail_times=3):
        import agents.agent2 as a2
        mock_col = _make_mock_collection()
        a2._collection = mock_col
        a2._embedder   = _make_mock_embedder()
        a2._nlp = None  # reset spaCy singleton

        call_count = {"n": 0}

        def flaky_generate(*args, **kwargs):
            call_count["n"] += 1
            raise RuntimeError("Simulated Gemini failure")

        mock_model = MagicMock()
        mock_model.generate_content.side_effect = flaky_generate

        mock_genai = MagicMock()
        mock_genai.GenerativeModel.return_value = mock_model
        mock_genai.GenerationConfig = MagicMock()
        mock_genai.configure = MagicMock()

        # Mock spaCy
        mock_ent_loc = MagicMock(); mock_ent_loc.text = "Hormuz"; mock_ent_loc.label_ = "LOC"
        mock_ent_org = MagicMock(); mock_ent_org.text = "Iran";   mock_ent_org.label_ = "ORG"
        mock_doc = MagicMock()
        mock_doc.ents = [mock_ent_loc, mock_ent_org]
        mock_nlp = MagicMock()
        mock_nlp.return_value = mock_doc

        mock_spacy = MagicMock()
        mock_spacy.load.return_value = mock_nlp

        with patch(PATCH_GENAI, mock_genai), \
             patch(PATCH_SPACY_LOAD, mock_spacy), \
             patch("agents.agent2.time.sleep"):     # skip real delays
            result = a2.extract_intelligence(event or SAMPLE_EVENT)
            return result, call_count["n"]

    def test_fallback_activates_after_3_failures(self):
        """Fix 6 — exactly 3 Gemini attempts before falling back."""
        result, attempts = self._run_with_failing_gemini()
        assert attempts == 3

    def test_fallback_method_is_fallback_ner(self):
        """Fix 6 — extraction_method must be 'fallback_ner' on spaCy fallback."""
        result, _ = self._run_with_failing_gemini()
        assert result["extraction_method"] == "fallback_ner"

    def test_fallback_result_has_required_keys(self):
        """spaCy fallback must return same schema keys as Gemini."""
        result, _ = self._run_with_failing_gemini()
        extracted = result["extracted"]
        for key in ("event_type", "location", "corridor_affected",
                    "severity", "disruption_type", "similar_historical_events"):
            assert key in extracted, f"Missing key in fallback: {key}"

    def test_fallback_confidence_still_computed(self):
        """confidence formula must still run even on spaCy fallback."""
        result, _ = self._run_with_failing_gemini()
        assert 0.0 <= result["confidence"] <= 1.0

    def test_fallback_corridor_detected_from_text(self):
        """spaCy fallback must detect 'Hormuz' in event text."""
        result, _ = self._run_with_failing_gemini()
        assert result["extracted"]["corridor_affected"] == "Hormuz"


# ===========================================================================
# Tests — LLM extraction score
# ===========================================================================

class TestLlmExtractionScore:

    def test_all_fields_present_gives_1(self):
        import agents.agent2 as a2
        score = a2._llm_extraction_score(MOCK_GEMINI_JSON)
        assert score == 1.0

    def test_missing_field_penalises(self):
        import agents.agent2 as a2
        incomplete = {k: v for k, v in MOCK_GEMINI_JSON.items() if k != "location"}
        score = a2._llm_extraction_score(incomplete)
        assert score < 1.0

    def test_all_fields_unknown_gives_zero(self):
        import agents.agent2 as a2
        all_unknown = {k: "Unknown" for k in a2._REQUIRED_FIELDS}
        all_unknown["similar_historical_events"] = []
        score = a2._llm_extraction_score(all_unknown)
        assert score == 0.0

    def test_score_bounded_0_to_1(self):
        import agents.agent2 as a2
        assert 0.0 <= a2._llm_extraction_score({}) <= 1.0
        assert 0.0 <= a2._llm_extraction_score(MOCK_GEMINI_JSON) <= 1.0


# ===========================================================================
# Tests — confidence formula
# ===========================================================================

class TestConfidenceFormula:

    def test_formula_exact(self):
        import agents.agent2 as a2
        conf = a2._compute_confidence(0.8, MOCK_SIMILAR)
        expected = round(0.4 * 0.8 + 0.6 * 0.82, 4)
        assert abs(conf - expected) < 0.001

    def test_zero_rag_zero_llm_gives_zero(self):
        import agents.agent2 as a2
        conf = a2._compute_confidence(0.0, [{"similarity": 0.0}])
        assert conf == 0.0

    def test_max_rag_used_not_average(self):
        """confidence uses max similarity, not average."""
        import agents.agent2 as a2
        similar = [{"similarity": 0.3}, {"similarity": 0.9}, {"similarity": 0.1}]
        conf = a2._compute_confidence(1.0, similar)
        expected = round(0.4 * 1.0 + 0.6 * 0.9, 4)
        assert abs(conf - expected) < 0.001

    def test_confidence_bounded_0_to_1(self):
        import agents.agent2 as a2
        conf = a2._compute_confidence(1.0, [{"similarity": 1.0}])
        assert 0.0 <= conf <= 1.0


# ===========================================================================
# Tests — doc_id SHA-256
# ===========================================================================

class TestDocId:

    def test_sha256_deterministic(self):
        import agents.agent2 as a2
        text = "Some disruption report text."
        assert a2._doc_id(text) == a2._doc_id(text)

    def test_different_texts_different_ids(self):
        import agents.agent2 as a2
        assert a2._doc_id("text A") != a2._doc_id("text B")

    def test_id_is_64_hex_chars(self):
        import agents.agent2 as a2
        doc_id = a2._doc_id("any text")
        assert len(doc_id) == 64
        assert all(c in "0123456789abcdef" for c in doc_id)


# ===========================================================================
# Tests — Redis consumer group setup
# ===========================================================================

class TestRedisConsumerGroup:

    def test_busygroup_error_is_silently_ignored(self):
        """BUSYGROUP error means group already exists — must not crash."""
        import agents.agent2 as a2
        mock_r = MagicMock()
        mock_r.xgroup_create.side_effect = redis_busygroup_error()

        with patch(PATCH_REDIS_URL, return_value=mock_r):
            a2._ensure_consumer_group(mock_r)   # must not raise

    def test_other_redis_error_propagates(self):
        """Non-BUSYGROUP Redis errors must propagate."""
        import agents.agent2 as a2
        import redis as redis_lib
        mock_r = MagicMock()
        mock_r.xgroup_create.side_effect = redis_lib.exceptions.ResponseError("SOMETHINGELSE")

        with pytest.raises(redis_lib.exceptions.ResponseError):
            a2._ensure_consumer_group(mock_r)


def redis_busygroup_error():
    import redis as redis_lib
    return redis_lib.exceptions.ResponseError("BUSYGROUP Consumer Group name already exists")
# ===========================================================================
# Tests -- Fix 6: backoff delay timing
# ===========================================================================

class TestGeminiBackoffTiming:
    def test_backoff_delays_are_1_then_2_seconds(self):
        import agents.agent2 as a2
        mock_col = _make_mock_collection()
        a2._collection = mock_col
        a2._embedder = _make_mock_embedder()
        a2._nlp = None

        mock_model = MagicMock()
        mock_model.generate_content.side_effect = RuntimeError("Simulated failure")

        mock_genai = MagicMock()
        mock_genai.GenerativeModel.return_value = mock_model
        mock_genai.GenerationConfig = MagicMock()
        mock_genai.configure = MagicMock()

        mock_ent = MagicMock(); mock_ent.text = "Hormuz"; mock_ent.label_ = "LOC"
        mock_doc = MagicMock(); mock_doc.ents = [mock_ent]
        mock_nlp = MagicMock(); mock_nlp.return_value = mock_doc
        mock_spacy = MagicMock(); mock_spacy.load.return_value = mock_nlp

        with patch(PATCH_GENAI, mock_genai), \
             patch(PATCH_SPACY_LOAD, mock_spacy), \
             patch("agents.agent2.time.sleep") as mock_sleep:
            a2.extract_intelligence(SAMPLE_EVENT)

        actual_delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert actual_delays == [1, 2], f"Expected [1, 2], got {actual_delays}"