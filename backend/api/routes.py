import asyncio
import shutil
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.api.actions import mount_webapp
from backend.api.actions import router as actions_router
from backend.config import API_AUTH_TOKEN, OBSIDIAN_VAULT_PATH
from backend.ingestion.queue import get_db_connection
from backend.ingestion.router import router as ingestion_router
from backend.ingestion.watcher import watch_inbox_loop
from backend.rag.chat import query_brain

# Keep a strong reference to the watcher task; the loop only holds a weak one.
_background_tasks: set = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the background inbox watcher for the lifetime of the server.

    Replaces the deprecated @app.on_event("startup") hook.
    """
    print("Backend [Startup]: Registering background watch_inbox_loop task.")
    task = asyncio.create_task(watch_inbox_loop())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        print("Backend [Shutdown]: Background watcher stopped.")


app = FastAPI(title="Centaur Brain API", lifespan=lifespan)

# The API binds 0.0.0.0 so the Chrome extension can reach it over Tailscale/LAN.
# Credentials are never used cross-origin, so do not advertise them: allow_credentials
# with a wildcard origin is rejected by browsers anyway.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


async def require_api_token(authorization: str = Header(default="")):
    """Optional shared-secret auth for the HTTP API.

    Set API_AUTH_TOKEN in backend/.env to require callers to send
    ``Authorization: Bearer <token>``. Left unset the API stays open, preserving the
    current behaviour for existing extension installs.
    """
    if not API_AUTH_TOKEN:
        return
    expected = f"Bearer {API_AUTH_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API token")


app.include_router(ingestion_router, dependencies=[Depends(require_api_token)])
app.include_router(actions_router, dependencies=[Depends(require_api_token)])
mount_webapp(app)


class QueryRequest(BaseModel):
    query: str
    top_k: int = 5


@app.post("/query", dependencies=[Depends(require_api_token)])
async def handle_query(payload: QueryRequest):
    return await query_brain(payload.query, top_k=payload.top_k)


@app.get("/health")
async def get_health():
    """Returns the orchestrator health status including disk space and queue status."""
    total, used, free = shutil.disk_usage(OBSIDIAN_VAULT_PATH or ".")
    disk_info = {
        "total_gb": round(total / (1024**3), 2),
        "used_gb": round(used / (1024**3), 2),
        "free_gb": round(free / (1024**3), 2),
        "percent_free": round((free / total) * 100, 2)
    }

    queue_info = {
        "pending": 0,
        "processing": 0,
        "completed": 0,
        "failed": 0,
        "last_success": None
    }
    try:
        db_conn = get_db_connection()
        try:
            cursor = db_conn.cursor()
            cursor.execute("SELECT status, COUNT(*) FROM ingestion_queue GROUP BY status")
            for status, count in cursor.fetchall():
                if status in queue_info:
                    queue_info[status] = count

            cursor.execute("SELECT MAX(updated_at) FROM ingestion_queue WHERE status = 'completed'")
            row = cursor.fetchone()
            if row and row[0]:
                queue_info["last_success"] = row[0]
        finally:
            db_conn.close()
    except Exception as e:
        queue_info["error"] = str(e)

    return {
        "status": "healthy",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "disk": disk_info,
        "queue": queue_info
    }
