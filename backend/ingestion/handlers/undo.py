"""/undo: revert the last automated vault commit, after confirmation."""

import asyncio
import html

import httpx

from backend.ingestion.handlers.engage import send_msg
from backend.utils.git import get_last_commit_info, revert_orchestrator_commit

MAX_FILES_SHOWN = 15


def build_undo_prompt(info: dict | None) -> tuple[str, dict | None]:
    """Returns (message_html, reply_markup) for the /undo confirmation step."""
    if not info:
        return "❌ No git history found in the vault.", None

    subject = html.escape(info["subject"])
    if not info["is_orchestrator"]:
        return (f"🛑 The last vault commit wasn't made by the orchestrator, so /undo won't touch it:\n\n"
                f"<code>{subject}</code>"), None

    files = info["files"]
    lines = "\n".join(f"• {html.escape(f)}" for f in files[:MAX_FILES_SHOWN])
    if len(files) > MAX_FILES_SHOWN:
        lines += f"\n… and {len(files) - MAX_FILES_SHOWN} more"
    text = (f"↩️ <b>Revert this commit?</b>\n\n<code>{subject}</code>\n\n"
            f"Files changed ({len(files)}):\n{lines}\n\n"
            "⚠️ The commit includes every vault change present when it was made, "
            "including any edits synced from your other devices.")
    markup = {"inline_keyboard": [[
        {"text": "↩️ Revert", "callback_data": f"undo:confirm:{info['sha'][:12]}"},
        {"text": "✖️ Cancel", "callback_data": "undo:cancel"},
    ]]}
    return text, markup


async def handle_undo_command(chat_id: int, token: str, http_client: httpx.AsyncClient):
    info = await asyncio.to_thread(get_last_commit_info)
    text, markup = build_undo_prompt(info)
    await send_msg(chat_id, token, http_client, text, reply_markup=markup)


async def handle_undo_callback(callback_query: dict, token: str, http_client: httpx.AsyncClient):
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    data = callback_query.get("data", "")

    if data.startswith("undo:confirm:"):
        short_sha = data.split(":", 2)[2]
        info = await asyncio.to_thread(get_last_commit_info)
        if info and info["sha"].startswith(short_sha):
            ok, message = await asyncio.to_thread(revert_orchestrator_commit, info["sha"])
        else:
            ok, message = False, "The vault has had a new commit since you asked, so nothing was reverted. Run /undo again."
        text = f"{'✅' if ok else '❌'} {html.escape(message)}"
    else:
        text = "✖️ Undo cancelled."

    await http_client.post(
        f"https://api.telegram.org/bot{token}/editMessageText",
        json={"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML",
              "reply_markup": {"inline_keyboard": []}},
    )
