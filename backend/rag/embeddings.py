import asyncio
from google.genai import types
from backend.utils.gemini import client

async def get_embedding(text: str) -> list[float]:
    """Generates a vector embedding for a single text string."""
    from backend.config import EMBEDDING_PROVIDER
    if EMBEDDING_PROVIDER != "gemini":
        from backend.utils.llm_client import get_embedding as client_get_embedding
        return await client_get_embedding(text)
        
    from backend.config import EMBEDDING_MODEL
    try:
        response = await client.aio.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text
        )
        if response.embeddings:
            return response.embeddings[0].values
        raise ValueError("No embeddings returned from Gemini API.")
    except Exception as e:
        print(f"RAG [Embeddings]: Error generating single embedding: {e}")
        raise e

async def get_embeddings_batch(texts: list[str], batch_size: int = 100) -> list[list[float]]:
    """Generates vector embeddings for a list of texts in batches to respect API limits."""
    from backend.config import EMBEDDING_PROVIDER
    if EMBEDDING_PROVIDER != "gemini":
        from backend.utils.llm_client import get_embeddings_batch as client_batch
        return await client_batch(texts)
        
    from backend.config import EMBEDDING_MODEL
    embeddings = []
    
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        print(f"RAG [Embeddings]: Generating embeddings for batch of {len(batch)} items (index {i} to {i+len(batch)})...")
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = await client.aio.models.embed_content(
                    model=EMBEDDING_MODEL,
                    contents=batch
                )
                if response.embeddings:
                    embeddings.extend([emb.values for emb in response.embeddings])
                    break
                else:
                    raise ValueError("No embeddings returned from batch API call.")
            except Exception as e:
                err_str = str(e)
                if ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str) and attempt < max_retries - 1:
                    wait_sec = 25
                    print(f"RAG [Embeddings]: Rate limit 429 hit. Retrying in {wait_sec}s (attempt {attempt+1}/{max_retries})...")
                    await asyncio.sleep(wait_sec)
                else:
                    print(f"RAG [Embeddings]: Error in batch embeddings generation: {e}")
                    raise e
            
        await asyncio.sleep(0.1)
        
    return embeddings
