from backend.config import GEMINI_MODEL
from backend.utils.gemini import call_gemini_with_retry
from google.genai import types
from pydantic import BaseModel, Field
from backend.ingestion.queue import add_to_queue
from backend.prompts import load_prompt

class SplitVoiceItem(BaseModel):
    category: str = Field(description="Must be one of: 'quick_note', 'task', 'idea', 'project_note'.")
    content: str = Field(description="The extracted transcript/text content for this specific item, cleaned and formatted.")
    reasoning: str = Field(description="Brief explanation of why this was split.")

class VoiceDumpSplitResult(BaseModel):
    items: list[SplitVoiceItem] = Field(description="List of split items from the voice note.")

async def process_voice_dump_item(content_text: str, source_info: str) -> str:
    """Splits a multi-topic voice dump transcription into separate tasks/notes and re-queues them."""
    print("Voice Splitter: Analyzing voice note transcription...")
    
    system_instruction = load_prompt("voice_split")

    try:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=VoiceDumpSplitResult,
        )
        
        response = await call_gemini_with_retry(
            model=GEMINI_MODEL,
            contents=f"Split this voice transcription:\n\n{content_text}",
            config=config
        )
        
        split_result = VoiceDumpSplitResult.model_validate_json(response.text.strip())
    except Exception as e:
        print(f"Voice Splitter: Error splitting voice note via Gemini: {e}")
        # Fallback: keep the entire thing as a single quick_note
        split_result = VoiceDumpSplitResult(items=[
            SplitVoiceItem(
                category="quick_note",
                content=content_text,
                reasoning=f"Fallback due to error: {e}"
            )
        ])

    if not split_result.items:
        print("Voice Splitter: No items identified to split.")
        return "Voice note transcription was empty or had no distinct items."

    # Re-queue each item separately
    requeued_count = 0
    for item in split_result.items:
        payload = {
            "source": "voice_splitter",
            "original_source": source_info,
            "split_category": item.category,
            "reasoning": item.reasoning
        }
        
        # Add to the queue. The watchdog worker will process this item in the next loops!
        add_to_queue(
            source_type="telegram",  # Keep telegram or mark as sub-item
            raw_payload=payload,
            content_text=item.content
        )
        requeued_count += 1
        print(f"Voice Splitter: Re-queued split item [{item.category}] -> content: '{item.content[:40]}...'")

    return f"Successfully split voice note into {requeued_count} separate items and queued them for processing."
