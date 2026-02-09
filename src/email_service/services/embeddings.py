import chromadb

from email_service.config import settings
from email_service.services.llm import embed

_client = None
_collection = None


def init_vectorstore():
    global _client, _collection
    _client = chromadb.PersistentClient(path=str(settings.chroma_path))
    _collection = _client.get_or_create_collection(
        name="emails", metadata={"hnsw:space": "cosine"}
    )


def store(doc_id: str, text: str, metadata: dict | None = None):
    vector = embed(text)
    _collection.upsert(
        ids=[doc_id], embeddings=[vector], documents=[text], metadatas=[metadata or {}]
    )


def search(query: str, n_results: int = 5, where: dict | None = None) -> list[dict]:
    vector = embed(query)
    kwargs = {"query_embeddings": [vector], "n_results": n_results}

    if where:
        kwargs["where"] = where

    results = _collection.query(**kwargs)

    hits = []
    for i in range(len(results["ids"][0])):
        hits.append(
            {
                "id": results["ids"][0][i],
                "document": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i],
            }
        )

    return hits


def is_available() -> bool:
    return _collection is not None
