from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.ingestion.router import router as ingestion_router

app = FastAPI(title="Centaur Brain API")

# Configure CORS for Chrome Extension
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust this in production if needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include ingestion router
app.include_router(ingestion_router)

from backend.ingestion.watcher import watch_inbox_loop
import asyncio

@app.on_event("startup")
async def startup_event():
    """Starts the background inbox watcher loop when the FastAPI server initializes."""
    print("Backend [Startup]: Registering background watch_inbox_loop task.")
    asyncio.create_task(watch_inbox_loop())

from pydantic import BaseModel
from backend.rag.chat import query_brain

class QueryRequest(BaseModel):
    query: str
    top_k: int = 5

@app.post("/query")
async def handle_query(payload: QueryRequest):
    result = await query_brain(payload.query, top_k=payload.top_k)
    return result

import shutil
from datetime import datetime
from backend.config import OBSIDIAN_VAULT_PATH
from backend.ingestion.queue import get_db_connection

@app.get("/health")
async def get_health():
    """Returns the orchestrator health status including disk space and queue status."""
    # 1. Disk usage
    total, used, free = shutil.disk_usage(OBSIDIAN_VAULT_PATH or ".")
    disk_info = {
        "total_gb": round(total / (1024**3), 2),
        "used_gb": round(used / (1024**3), 2),
        "free_gb": round(free / (1024**3), 2),
        "percent_free": round((free / total) * 100, 2)
    }
    
    # 2. Queue status
    queue_info = {
        "pending": 0,
        "processing": 0,
        "completed": 0,
        "failed": 0,
        "last_success": None
    }
    try:
        db_conn = get_db_connection()
        cursor = db_conn.cursor()
        
        # Get counts grouped by status
        cursor.execute("SELECT status, COUNT(*) FROM ingestion_queue GROUP BY status")
        for status, count in cursor.fetchall():
            if status in queue_info:
                queue_info[status] = count
                
        # Get last success timestamp
        cursor.execute("SELECT MAX(updated_at) FROM ingestion_queue WHERE status = 'completed'")
        row = cursor.fetchone()
        if row and row[0]:
            queue_info["last_success"] = row[0]
            
        db_conn.close()
    except Exception as e:
        queue_info["error"] = str(e)
        
    return {
        "status": "healthy",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "disk": disk_info,
        "queue": queue_info
    }



