from pydantic import BaseModel, Field
from google.genai import types
from backend.utils.gemini import call_gemini_with_retry
from backend.config import GEMINI_MODEL

class ClassificationResult(BaseModel):
    category: str = Field(
        description="The classified category of the input. Must be one of: "
                    "'quick_note', 'task', 'meeting_transcript', 'deep_research', "
                    "'reference', 'voice_dump', 'url_capture', 'idea'."
    )
    confidence: float = Field(
        description="Confidence score for this classification, between 0.0 and 1.0."
    )
    reasoning: str = Field(
        description="Brief explanation of why this category was chosen."
    )

SYSTEM_INSTRUCTION = """You are an intelligent router for Steven's Second Brain system.
Analyze the incoming text or metadata and classify it into exactly ONE of the following categories:

1. 'quick_note': A simple standalone thought, personal log, or comment that doesn't fit tasks/ideas.
2. 'task': An actionable item with an implied or explicit deadline, commitment, or next step.
3. 'meeting_transcript': Notes, transcriptions, or summaries of discussions from meetings.
4. 'deep_research': Dense reference documents, academic papers, scientific reports, or detailed analysis.
5. 'reference': Standard bookmarks, tools list, reference articles that are not deep academic/scientific papers.
6. 'voice_dump': Multi-topic spoken inputs from a voice note that will need to be split.
7. 'url_capture': A URL link to be scraped (if the input is primarily a URL to read/download).
8. 'idea': A conceptual spark, project proposal, or creative thought worth developing further.

CRITICAL RULES:
- If the input is primarily a URL (e.g. 'https://...'), classify as 'url_capture'.
- If the input contains phrases like 'need to', 'todo', 'remind me to', 'do this', or 'action item', classify as 'task'.
- Do NOT classify informational data-recording requests like "save this ID", "store this code", "remember this phone number", or "record this info" as 'task'. If the user is providing information to be saved into the brain, it should be classified as 'quick_note', 'reference', or 'idea' depending on context.
- If the input is a transcript of multiple speakers or mentions 'meeting minutes', 'sync call', etc., classify as 'meeting_transcript'.
- If the input contains dense academic or scientific papers/reports (like PDF contents of scientific studies), classify as 'deep_research'.
- If in doubt, default to 'quick_note' or 'idea' with lower confidence.

"""

async def classify_input(text: str) -> ClassificationResult:
    """Classifies the input text using Gemini structured JSON output."""
    try:
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=ClassificationResult,
        )
        
        response = await call_gemini_with_retry(
            model=GEMINI_MODEL,
            contents=f"Analyze and classify this input:\n\n{text[:5000]}",
            config=config
        )
        
        result = ClassificationResult.model_validate_json(response.text.strip())
        return result
    except Exception as e:
        print(f"Classifier: Error during Gemini classification: {e}")
        # Secure fallback
        return ClassificationResult(
            category="quick_note",
            confidence=0.5,
            reasoning=f"Fallback due to classifier error: {e}"
        )
