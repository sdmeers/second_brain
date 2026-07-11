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
