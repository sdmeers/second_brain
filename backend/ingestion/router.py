from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.processing.capture import process_capture_core, DuplicateURLError

router = APIRouter()

class CapturePayload(BaseModel):
    source: str
    url: str
    title: str | None = "Untitled Capture"
    authorHint: str | None = ""
    markdownText: str | None = ""

@router.post("/process")
async def process_capture(payload: CapturePayload):
    try:
        result = await process_capture_core(
            url=payload.url,
            title=payload.title,
            author_hint=payload.authorHint,
            markdown_text=payload.markdownText
        )
        return result
    except DuplicateURLError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
