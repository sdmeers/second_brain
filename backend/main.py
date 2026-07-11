import os
import sys
import asyncio
import uvicorn

# Ensure the parent directory is in sys.path so 'backend' package imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.api.routes import app
from backend.config import PORT

if __name__ == "__main__":
    # Run the server natively
    uvicorn.run("backend.main:app", host="127.0.0.1", port=PORT, reload=True)
