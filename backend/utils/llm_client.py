import os
import json
import asyncio
import hashlib
import time
from typing import Optional, List, Type, Any
from datetime import datetime
from pydantic import BaseModel

from backend.config import (
    LLM_PROVIDER, LLM_MODEL,
    EMBEDDING_PROVIDER, EMBEDDING_MODEL,
    OPENAI_API_KEY, OLLAMA_BASE_URL
)

# Providers initialization
# Gemini
gemini_client = None
if LLM_PROVIDER == "gemini" or EMBEDDING_PROVIDER == "gemini":
    from backend.utils.gemini import client as g_client
    from google.genai import types
    gemini_client = g_client

# OpenAI / Ollama
openai_client = None
ollama_client = None

if LLM_PROVIDER in ["openai", "ollama"] or EMBEDDING_PROVIDER in ["openai", "ollama"]:
    from openai import AsyncOpenAI

if LLM_PROVIDER == "openai" or EMBEDDING_PROVIDER == "openai":
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

if LLM_PROVIDER == "ollama" or EMBEDDING_PROVIDER == "ollama":
    ollama_client = AsyncOpenAI(base_url=f"{OLLAMA_BASE_URL}/v1", api_key="ollama")

async def generate_text(prompt: str, system_instruction: str = None, temperature: float = 0.1) -> str:
    """Generate text using the configured LLM provider."""
    if LLM_PROVIDER == "gemini":
        from backend.utils.gemini import call_gemini_with_retry
        config_kwargs = {"temperature": temperature}
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        config = types.GenerateContentConfig(**config_kwargs)
        
        response = await call_gemini_with_retry(
            model=LLM_MODEL,
            contents=prompt,
            config=config
        )
        return response.text

    elif LLM_PROVIDER in ["openai", "ollama"]:
        client = openai_client if LLM_PROVIDER == "openai" else ollama_client
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})
        
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=temperature
        )
        return response.choices[0].message.content

    else:
        raise ValueError(f"Unsupported LLM_PROVIDER: {LLM_PROVIDER}")

async def generate_structured_output(prompt: str, response_schema: Type[BaseModel], system_instruction: str = None, temperature: float = 0.1) -> BaseModel:
    """Generate structured output matching a Pydantic schema using the configured LLM provider."""
    if LLM_PROVIDER == "gemini":
        from backend.utils.gemini import call_gemini_with_retry
        config_kwargs = {
            "temperature": temperature,
            "response_mime_type": "application/json",
            "response_schema": response_schema
        }
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        config = types.GenerateContentConfig(**config_kwargs)
        
        response = await call_gemini_with_retry(
            model=LLM_MODEL,
            contents=prompt,
            config=config
        )
        return response_schema.model_validate_json(response.text)

    elif LLM_PROVIDER in ["openai", "ollama"]:
        client = openai_client if LLM_PROVIDER == "openai" else ollama_client
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})
        
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=temperature,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.__name__,
                    "schema": response_schema.model_json_schema()
                }
            }
        )
        return response_schema.model_validate_json(response.choices[0].message.content)

    else:
        raise ValueError(f"Unsupported LLM_PROVIDER: {LLM_PROVIDER}")

async def get_embedding(text: str) -> list[float]:
    """Generates a vector embedding for a single text string."""
    if EMBEDDING_PROVIDER == "gemini":
        response = await gemini_client.aio.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text
        )
        if response.embeddings:
            return response.embeddings[0].values
        raise ValueError("No embeddings returned from Gemini API.")

    elif EMBEDDING_PROVIDER in ["openai", "ollama"]:
        client = openai_client if EMBEDDING_PROVIDER == "openai" else ollama_client
        response = await client.embeddings.create(
            input=text,
            model=EMBEDDING_MODEL
        )
        return response.data[0].embedding

    elif EMBEDDING_PROVIDER == "local":
        raise NotImplementedError("Local embedding provider not yet implemented.")
    
    else:
        raise ValueError(f"Unsupported EMBEDDING_PROVIDER: {EMBEDDING_PROVIDER}")

async def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Generates vector embeddings for a list of texts."""
    if EMBEDDING_PROVIDER == "gemini":
        from backend.rag.embeddings import get_embeddings_batch as gemini_batch
        return await gemini_batch(texts)

    elif EMBEDDING_PROVIDER in ["openai", "ollama"]:
        client = openai_client if EMBEDDING_PROVIDER == "openai" else ollama_client
        embeddings = []
        batch_size = 100
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            response = await client.embeddings.create(
                input=batch,
                model=EMBEDDING_MODEL
            )
            embeddings.extend([data.embedding for data in response.data])
        return embeddings

    elif EMBEDDING_PROVIDER == "local":
        raise NotImplementedError("Local embedding provider not yet implemented.")
    
    else:
        raise ValueError(f"Unsupported EMBEDDING_PROVIDER: {EMBEDDING_PROVIDER}")

async def get_or_create_context_cache(system_instruction: str) -> Optional[str]:
    """Returns a cache name if Gemini supports it and conditions are met. Otherwise returns None."""
    if LLM_PROVIDER != "gemini":
        return None
        
    from backend.utils.gemini import get_or_create_context_cache as gemini_cache
    return await gemini_cache(model=LLM_MODEL, system_instruction=system_instruction)
