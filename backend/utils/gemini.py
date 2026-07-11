import os
import time
import json
import hashlib
import asyncio
from typing import Optional
from datetime import datetime
from google import genai
from google.genai import types
from backend.config import GEMINI_API_KEY, GEMINI_MODEL

client = genai.Client(api_key=GEMINI_API_KEY)

CACHE_METADATA_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache_metadata.json")

def load_cache_metadata():
    if os.path.exists(CACHE_METADATA_FILE):
        try:
            with open(CACHE_METADATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_cache_metadata(metadata):
    try:
        with open(CACHE_METADATA_FILE, "w") as f:
            json.dump(metadata, f, indent=2)
    except Exception as e:
        print(f"Error saving cache metadata: {e}")

async def call_gemini_with_retry(model: str, contents, config: types.GenerateContentConfig = None, max_retries: int = 5):
    """Wrapper to call Gemini API with exponential backoff for 429 and 503 errors."""
    for attempt in range(max_retries):
        try:
            return await client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=config
            )
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "503" in error_str:
                wait_time = (2 ** attempt) + 2  # 3s, 4s, 6s, 10s, 18s
                print(f"Backend [Gemini]: Encountered {error_str[:3]} error. Retrying in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                await asyncio.sleep(wait_time)
                if attempt == max_retries - 1:
                    raise RuntimeError(f"Failed after {max_retries} attempts: {error_str}")
            else:
                raise e

async def get_or_create_context_cache(model: str, system_instruction: str) -> Optional[str]:
    """
    Checks if there's an active context cache for this system_instruction.
    If not, and the instruction meets the minimum token size (4,096 tokens),
    it creates a new cache.
    Returns the cache name or None.
    """
    try:
        token_res = await client.aio.models.count_tokens(
            model=model,
            contents=system_instruction
        )
        total_tokens = token_res.total_tokens
        print(f"Backend [Cache]: System instruction token count is {total_tokens}.")
        if total_tokens < 4096:
            print("Backend [Cache]: Token count is below 4,096. Caching is not recommended or supported.")
            return None
    except Exception as e:
        print(f"Backend [Cache]: Error counting tokens for caching: {e}")
        return None

    instr_hash = hashlib.md5(system_instruction.encode("utf-8")).hexdigest()
    metadata = load_cache_metadata()
    cached_info = metadata.get(model)
    current_time = time.time()
    
    if cached_info:
        cached_name = cached_info.get("name")
        cached_hash = cached_info.get("hash")
        expire_time = cached_info.get("expire_time", 0)
        
        if cached_hash == instr_hash and expire_time > current_time + 300:
            print(f"Backend [Cache]: Reusing active context cache: {cached_name}")
            return cached_name
            
        if cached_name:
            print(f"Backend [Cache]: Cleaning up old or expired cache: {cached_name}")
            try:
                await client.aio.caches.delete(name=cached_name)
            except Exception as e:
                print(f"Backend [Cache]: Error deleting old cache: {e}")
    
    print("Backend [Cache]: Creating new context cache for system instruction...")
    try:
        cache = await client.aio.caches.create(
            model=model,
            config=types.CreateCachedContentConfig(
                display_name=f"centaur_brain_sys_instr_{model.replace('-', '_')}",
                system_instruction=system_instruction,
                ttl="3600s"
            )
        )
        
        expire_timestamp = current_time + 3600
        expire_time_attr = getattr(cache, "expire_time", None)
        if expire_time_attr:
            if isinstance(expire_time_attr, datetime):
                expire_timestamp = expire_time_attr.timestamp()
            else:
                try:
                    dt_str = str(expire_time_attr).replace("Z", "+00:00")
                    expire_timestamp = datetime.fromisoformat(dt_str).timestamp()
                except Exception as dt_e:
                    print(f"Backend [Cache]: Error parsing expire_time: {dt_e}")
                    
        metadata[model] = {
            "name": cache.name,
            "hash": instr_hash,
            "expire_time": expire_timestamp
        }
        save_cache_metadata(metadata)
        print(f"Backend [Cache]: Successfully created context cache: {cache.name}")
        return cache.name
    except Exception as e:
        print(f"Backend [Cache]: Failed to create context cache: {e}. Falling back.")
        return None
