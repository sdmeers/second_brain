import os
import sys
import asyncio
import uvicorn

# Ensure the parent directory is in sys.path so 'backend' package imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.api.routes import app
from backend.ingestion.watcher import watch_inbox_loop
from backend.config import PORT

@app.on_event("startup")
async def startup_event():
    """Starts the background inbox watcher loop when the FastAPI server initializes."""
    print("Backend [Startup]: Registering background watch_inbox_loop task.")
    asyncio.create_task(watch_inbox_loop())

if __name__ == "__main__":
    # Run the server natively
    uvicorn.run("backend.main:app", host="127.0.0.1", port=PORT, reload=True)
