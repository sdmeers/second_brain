from datetime import datetime
from backend.config import TASKS_MD_FILE
from backend.utils.gemini import call_gemini_with_retry
from backend.config import GEMINI_MODEL
from google.genai import types
from pydantic import BaseModel, Field
from backend.prompts import load_prompt

class ExtractedTask(BaseModel):
    task_description: str = Field(description="The clean action item text.")
    deadline: str = Field(description="Deadline or date constraint if mentioned, otherwise 'None'.")
    context: str = Field(description="Related context, project, or domain if implied.")
    tags: list[str] = Field(description="Relevant tags for the task (e.g. ['#work', '#personal', '#coding']).")

class TaskListExtraction(BaseModel):
    tasks: list[ExtractedTask] = Field(description="List of all distinct action items extracted from the text.")

async def process_task_item(content_text: str, source_info: str) -> str:
    """Extracts tasks from the input text and appends them to Next_Actions.md."""
    tasks_file = TASKS_MD_FILE
    
    # 1. Use Gemini to extract structured tasks
    system_instruction = load_prompt("task_extract")
    try:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=TaskListExtraction,
        )
        
        response = await call_gemini_with_retry(
            model=GEMINI_MODEL,
            contents=f"Extract tasks from this input:\n\n{content_text}",
            config=config
        )
        
        extracted = TaskListExtraction.model_validate_json(response.text.strip())
    except Exception as e:
        print(f"Task Handler: Error extracting tasks with Gemini: {e}")
        # Secure fallback: treat the whole text as a single task
        extracted = TaskListExtraction(tasks=[
            ExtractedTask(
                task_description=f"Review inbox item: {content_text[:60]}...",
                deadline="None",
                context="inbox_fallback",
                tags=["#inbox"]
            )
        ])

    if not extracted.tasks:
        print("Task Handler: No tasks found in input.")
        return "No tasks identified to add."

    # 2. Append to Next_Actions.md
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_entries = []
    
    for task in extracted.tasks:
        tag_str = " ".join(task.tags)
        deadline_str = f" 📅 {task.deadline}" if task.deadline and task.deadline.lower() != "none" else ""
        entry = f"- [ ] {task.task_description}{deadline_str} {tag_str} (Captured from {source_info} on {timestamp})\n"
        new_entries.append(entry)
        
    try:
        # Read existing Next_Actions.md if it exists, otherwise initialize it
        from backend.utils.tasks_dashboard import append_gtd_item
        for entry in new_entries:
            append_gtd_item(tasks_file, entry)
        print(f"Task Handler: Appended {len(new_entries)} tasks to Next_Actions.md")
        return f"Successfully added {len(new_entries)} tasks to your Tasks list."
    except Exception as e:
        print(f"Task Handler: Error writing to Next_Actions.md: {e}")
        raise e
