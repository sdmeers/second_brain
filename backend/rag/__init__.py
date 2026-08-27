"""Public surface of the RAG subsystem."""

from backend.rag.chat import query_brain
from backend.rag.embeddings import get_embedding, get_embeddings_batch
from backend.rag.vectorstore import index_vault, search_similar_chunks

__all__ = [
    "get_embedding",
    "get_embeddings_batch",
    "index_vault",
    "query_brain",
    "search_similar_chunks",
]
