import asyncio
from google.genai import types
from backend.utils.gemini import client

EMBEDDING_MODEL = "gemini-embedding-2"

async def get_embedding(text: str) -> list[float]:
    """Generates a 3072-dimension vector embedding for a single text string using Gemini."""
    try:
        response = await client.aio.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text
        )
        # response.embeddings contains the list of embeddings
        if response.embeddings:
            return response.embeddings[0].values
        raise ValueError("No embeddings returned from Gemini API.")
    except Exception as e:
        print(f"RAG [Embeddings]: Error generating single embedding: {e}")
        raise e

async def get_embeddings_batch(texts: list[str], batch_size: int = 100) -> list[list[float]]:
    """Generates vector embeddings for a list of texts in batches to respect API limits."""
    embeddings = []
    
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        print(f"RAG [Embeddings]: Generating embeddings for batch of {len(batch)} items (index {i} to {i+len(batch)})...")
        try:
            response = await client.aio.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=batch
            )
            if response.embeddings:
                embeddings.extend([emb.values for emb in response.embeddings])
            else:
                raise ValueError("No embeddings returned from batch API call.")
        except Exception as e:
            print(f"RAG [Embeddings]: Error in batch embeddings generation: {e}")
            raise e
            
        # Throttling to prevent hitting embedding API rate limits (1500 RPM is very high, but 0.2s sleep is safe)
        await asyncio.sleep(0.1)
        
    return embeddings
