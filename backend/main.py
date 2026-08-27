import os
import sys
import uvicorn

# Ensure the parent directory is in sys.path so 'backend' package imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import PORT

if __name__ == "__main__":
    # Run the server natively (listening on 0.0.0.0 for localhost & Tailscale access)
    uvicorn.run("backend.main:app", host="0.0.0.0", port=PORT, reload=False)
