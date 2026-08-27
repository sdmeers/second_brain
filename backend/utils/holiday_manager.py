import os
import json
from datetime import datetime
from backend.config import OBSIDIAN_VAULT_PATH
from backend.vault.writer import atomic_write

HOLIDAY_STATE_PATH = os.path.join(OBSIDIAN_VAULT_PATH, "00_System", "Internal", "holiday_mode.json")

def get_holiday_state() -> dict:
    """Reads current holiday pause state."""
    if os.path.exists(HOLIDAY_STATE_PATH):
        try:
            with open(HOLIDAY_STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"personal_until": None, "professional_until": None, "all_until": None}

def save_holiday_state(state: dict):
    """Saves holiday pause state atomically."""
    os.makedirs(os.path.dirname(HOLIDAY_STATE_PATH), exist_ok=True)
    atomic_write(HOLIDAY_STATE_PATH, json.dumps(state, indent=2))

def set_holiday_pause(scope: str, until_date: datetime.date):
    """Sets a holiday pause for 'personal', 'professional', or 'all'."""
    state = get_holiday_state()
    date_iso = until_date.strftime("%Y-%m-%d")
    
    scope_lower = scope.lower().replace("#", "").strip()
    if scope_lower in ["personal", "home"]:
        state["personal_until"] = date_iso
    elif scope_lower in ["professional", "work"]:
        state["professional_until"] = date_iso
    elif scope_lower in ["all", "everything"]:
        state["all_until"] = date_iso
    else:
        state["all_until"] = date_iso

    state["updated_at"] = datetime.now().isoformat()
    save_holiday_state(state)

def clear_holiday_pause(scope: str = "all"):
    """Clears holiday pause for 'personal', 'professional', or 'all' (default all)."""
    state = get_holiday_state()
    scope_lower = scope.lower().replace("#", "").strip()
    
    if scope_lower == "personal":
        state["personal_until"] = None
    elif scope_lower == "professional":
        state["professional_until"] = None
    elif scope_lower in ["all", "resume", "end"]:
        state["personal_until"] = None
        state["professional_until"] = None
        state["all_until"] = None
        
    state["updated_at"] = datetime.now().isoformat()
    save_holiday_state(state)

def is_scope_paused(scope: str) -> tuple[bool, str | None]:
    """Checks if a given scope ('personal', 'professional', 'all') is paused today.
    Returns (is_paused: bool, until_date_display: str)."""
    state = get_holiday_state()
    today = datetime.now().date()
    
    # Check 'all' pause first
    if state.get("all_until"):
        try:
            pause_date = datetime.strptime(state["all_until"], "%Y-%m-%d").date()
            if pause_date >= today:
                return True, pause_date.strftime("%d-%m-%Y")
        except ValueError:
            pass

    scope_key = f"{scope.lower().replace('#', '').strip()}_until"
    if scope_key in state and state[scope_key]:
        try:
            pause_date = datetime.strptime(state[scope_key], "%Y-%m-%d").date()
            if pause_date >= today:
                return True, pause_date.strftime("%d-%m-%Y")
        except ValueError:
            pass

    return False, None
