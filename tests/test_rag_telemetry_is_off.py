"""The RAG retriever's vector store does not report to a third party.

`chromadb.PersistentClient` enables anonymized product telemetry by default and
sends it to PostHog. Starting the retriever logged, measured 2026-09-24:

    chromadb.telemetry.product.posthog: Anonymized telemetry enabled.

Nothing in `app/`, the compose files, the Dockerfiles or `.env.example` turned
it off, so production reported to an outside service the first time Copilot
searched. The same module already sets `HF_HUB_OFFLINE` and
`TRANSFORMERS_OFFLINE` -- outbound traffic from this code path was a decision
someone had made once, and the vector store was the half it did not reach.

`tests/test_outbound_url_policy.py` does not cover this and should not: it is
about addresses the server fetches on a user's behalf. Library telemetry has no
user and no address in this codebase, which is why nothing counted it.

The test builds the real `RagRetriever` -- its own `__init__`, its own
`PersistentClient` call -- and asks the client it created for its settings.
Only the embedding model and the collection fill are replaced, because they are
heavy and are not what is being asked about. A test that grepped the source for
`anonymized_telemetry=False` would pass on a string in a comment.
"""
import pytest


def test_the_retriever_builds_its_vector_store_with_telemetry_off(tmp_path, monkeypatch):
    from app.services.rag import retriever as r

    monkeypatch.setattr(r, "_CHROMA_DIR", tmp_path / ".chroma")
    monkeypatch.setattr(r, "SentenceTransformer", lambda *_a, **_k: object())
    monkeypatch.setattr(r.RagRetriever, "_ensure_collection", lambda self: None)

    built = r.RagRetriever()
    settings = built.client.get_settings()

    assert settings.anonymized_telemetry is False, (
        "the retriever's chromadb client has product telemetry on, so every "
        "process that searches reports to PostHog. Pass "
        "Settings(anonymized_telemetry=False) to PersistentClient."
    )
    assert (tmp_path / ".chroma").exists(), (
        "the client was not built at the patched path, so this test did not "
        "inspect the client the retriever makes"
    )
