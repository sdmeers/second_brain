from pydantic import BaseModel, Field
from google.genai import types
from backend.utils.gemini import call_gemini_with_retry
from backend.config import GEMINI_MODEL
from backend.prompts import load_prompt

class ClassificationResult(BaseModel):
    category: str = Field(
        description="The classified category of the input. Must be one of: "
                    "'quick_note', 'task', 'meeting_transcript', 'deep_research', "
                    "'reference', 'voice_dump', 'url_capture', 'idea', 'instruction'."
    )
    confidence: float = Field(
        description="Confidence score for this classification, between 0.0 and 1.0."
    )
    reasoning: str = Field(
        description="Brief explanation of why this category was chosen."
    )

SYSTEM_INSTRUCTION = load_prompt("router")

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
