import sys
import os
import asyncio

# Ensure parent directory is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.rag.vectorstore import index_vault

async def main():
    force = "--force" in sys.argv
    print(f"Starting vault vector indexing (Force Reindex: {force})...")
    await index_vault(force_reindex=force)

if __name__ == "__main__":
    asyncio.run(main())
