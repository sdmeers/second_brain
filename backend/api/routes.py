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

