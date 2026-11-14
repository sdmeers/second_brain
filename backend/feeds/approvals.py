"""Approve / Cancel buttons for runs that need Steven's say-so before spending.

The daemon only records the decision. The feed's own timer picks it up on its next run,
so no long job ever runs inside the Telegram handler.
"""

from datetime import datetime

from backend.feeds import state


def buttons(export_id: int) -> dict:
    return {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": f"feeds:approve:{export_id}"},
        {"text": "✖️ Cancel", "callback_data": f"feeds:cancel:{export_id}"}]]}


def set_status(export_id: int, status: str, only_from: tuple[str, ...] | None = None) -> bool:
    with state.connect() as conn:
        row = conn.execute("SELECT status FROM chat_exports WHERE id = ?", (export_id,)).fetchone()
        if not row or (only_from and row["status"] not in only_from):
            return False
        conn.execute("UPDATE chat_exports SET status = ?, updated_at = ? WHERE id = ?",
                     (status, datetime.now().isoformat(timespec="seconds"), export_id))
    return True


async def handle_callback(data: str, chat_id: int, message_id: int, token: str, http_client) -> None:
    try:
        _, action, raw_id = data.split(":")
        export_id = int(raw_id)
    except ValueError:
        return
    if action == "approve" and set_status(export_id, "approved", only_from=("awaiting_approval",)):
        text = "✅ Approved. Processing starts within 15 minutes; I'll message you when it finishes."
    elif action == "cancel" and set_status(export_id, "cancelled", only_from=("awaiting_approval",)):
        text = "✖️ Cancelled. Nothing was sent to the model."
    else:
        text = "That request has already been dealt with."
    await http_client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                           json={"chat_id": chat_id, "text": text})
