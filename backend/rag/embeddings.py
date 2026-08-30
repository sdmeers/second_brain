import asyncio
import re
import time
from collections import deque

from backend.utils.gemini import client

# The Gemini embedding quota (EmbedContentPerMinutePerProjectPerUserPerModel) is counted in
# *texts*, not HTTP requests -- a single call carrying 100 chunks spends 100 of it. The old
# code fired batches 0.1s apart, which can offer ~60,000 texts/minute at a ceiling of 3,000,
# so any bulk re-index tripped 429s within seconds. These structures pace on the same unit
# the quota uses: a trailing-60s window of (timestamp, texts) reservations.
_rate_lock = asyncio.Lock()
_recent_texts: deque[tuple[float, int]] = deque()


def _retry_delay_from_error(error_str: str) -> float | None:
    """Extracts the server's own suggested retry delay from a 429 payload, if present."""
    for pattern in (r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'", r"retry in (\d+(?:\.\d+)?)s"):
        match = re.search(pattern, error_str)
        if match:
            return float(match.group(1))
    return None


async def _reserve_capacity(text_count: int) -> None:
    """Blocks until `text_count` more texts fit within the trailing-minute budget."""
    from backend.config import EMBEDDING_TEXTS_PER_MINUTE

    while True:
        async with _rate_lock:
            now = time.monotonic()
            while _recent_texts and now - _recent_texts[0][0] >= 60.0:
                _recent_texts.popleft()

            used = sum(count for _, count in _recent_texts)
            # The `not _recent_texts` arm lets a batch larger than the whole ceiling through
            # once the window has drained, rather than waiting on capacity that can never come.
            if used + text_count <= EMBEDDING_TEXTS_PER_MINUTE or not _recent_texts:
                _recent_texts.append((now, text_count))
                return

            wait_sec = 60.0 - (now - _recent_texts[0][0])

        print(
            f"RAG [Embeddings]: Pacing - {used} texts embedded in the last minute "
            f"(ceiling {EMBEDDING_TEXTS_PER_MINUTE}). Waiting {wait_sec:.1f}s..."
        )
        await asyncio.sleep(max(wait_sec, 0.0) + 0.05)


async def get_embedding(text: str) -> list[float]:
    """Generates a vector embedding for a single text string."""
    from backend.config import EMBEDDING_PROVIDER
    if EMBEDDING_PROVIDER != "gemini":
        from backend.utils.llm_client import get_embedding as client_get_embedding
        return await client_get_embedding(text)

    from backend.config import EMBEDDING_MODEL
    try:
        await _reserve_capacity(1)
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

async def get_embeddings_batch(texts: list[str], batch_size: int | None = None) -> list[list[float]]:
    """Generates vector embeddings for a list of texts in batches to respect API limits."""
    from backend.config import EMBEDDING_PROVIDER
    if EMBEDDING_PROVIDER != "gemini":
        from backend.utils.llm_client import get_embeddings_batch as client_batch
        return await client_batch(texts)

    from backend.config import EMBEDDING_BATCH_SIZE, EMBEDDING_MODEL
    if batch_size is None:
        batch_size = EMBEDDING_BATCH_SIZE
    embeddings = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        print(f"RAG [Embeddings]: Generating embeddings for batch of {len(batch)} items (index {i} to {i+len(batch)})...")
        max_retries = 5
        for attempt in range(max_retries):
            try:
                await _reserve_capacity(len(batch))
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
                    # Prefer the delay the server itself asked for; otherwise back off
                    # exponentially rather than retrying on a flat 25s three times.
                    wait_sec = _retry_delay_from_error(err_str) or min(60.0, 5.0 * (2 ** attempt))
                    print(
                        f"RAG [Embeddings]: Rate limit 429 hit. Retrying in {wait_sec:.1f}s "
                        f"(attempt {attempt+1}/{max_retries})..."
                    )
                    await asyncio.sleep(wait_sec)
                else:
                    print(f"RAG [Embeddings]: Error in batch embeddings generation: {e}")
                    raise e

    return embeddings
