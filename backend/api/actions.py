"""HTTP API behind the actions web app (``webapp/``).

``GET  /api/actions``      every list, in priority order.
``POST /api/actions/ops``  an ordered batch of changes, replayable after being offline.
"""

import asyncio
import os
from datetime import datetime
from collections import OrderedDict
from typing import Literal

from fastapi import APIRouter, FastAPI, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.processing import voice_capture
from backend.vault import actions

router = APIRouter(prefix="/api/actions", tags=["actions"])

WEBAPP_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "webapp", "dist")


def mount_webapp(app: FastAPI) -> bool:
    """Serve the built actions web app at /app, if it has been built.

    The static shell is deliberately outside the API token check: it holds no data, and
    it is where the token gets entered. Returns False when ``webapp/dist`` is missing
    (run ``npm run build`` in ``webapp/``) so the rest of the backend still starts.
    """
    if not os.path.isfile(os.path.join(WEBAPP_DIST, "index.html")):
        print("Backend [Startup]: webapp/dist not found; the actions app is not being served.")
        return False
    app.mount("/app", StaticFiles(directory=WEBAPP_DIST, html=True), name="actions-app")
    return True


# `Op` has a field called `list`; once that has a default it shadows the builtin inside the
# class body, so the list-of-strings type is named out here.
StrList = list[str]


class Op(BaseModel):
    op_id: str = Field(min_length=1, max_length=64)
    type: Literal["complete", "edit", "move", "reorder", "capture", "clarify"]
    list: str = "captured"
    raw: str | None = None
    # capture
    text: str | None = None
    captured_at: str | None = None
    # clarify: where the captured item goes; its details travel in `fields`
    outcome: str | None = None
    # edit: only the keys present are changed; null clears a field
    fields: dict | None = None
    # move
    to: str | None = None
    waiting_on: str | None = None
    follow_up: str | None = None
    # reorder
    category: str | None = None
    order: StrList | None = None


class OpsRequest(BaseModel):
    ops: list[Op] = Field(max_length=500)


# Results of recently applied ops, keyed by the client's op_id. If a response is lost on
# a flaky connection the client resends the batch; replaying e.g. a `complete` would
# then report "not found" for a change that in fact went through. Remembering the
# outcome makes a resend harmless. Lost on restart, which only costs a spurious
# "couldn't apply" notice.
_recent: OrderedDict[str, dict] = OrderedDict()
_RECENT_MAX = 2000

_background: set = set()


def _apply(op: Op, alias: dict[str, str]) -> actions.OpResult:
    def current(raw: str | None) -> str:
        """Follow edits made earlier in this batch, so a queued edit-then-complete of
        the same action still finds its line under the new text."""
        if raw is None:
            raise actions.OpSkipped("invalid")
        raw = raw.strip()
        seen = set()
        while raw in alias and raw not in seen:
            seen.add(raw)
            raw = alias[raw]
        return raw

    if op.type == "complete":
        return actions.complete_action(op.list, current(op.raw))
    if op.type == "edit":
        return actions.edit_action(op.list, current(op.raw), op.fields or {})
    if op.type == "move":
        return actions.move_action(op.list, op.to or "", current(op.raw), op.waiting_on, op.follow_up)
    if op.type == "reorder":
        return actions.reorder_actions(op.list, op.category or "", [current(r) for r in op.order or []])
    if op.type == "capture":
        return actions.capture(op.text or "", op.captured_at)
    return actions.clarify(current(op.raw), op.outcome or "", op.fields or {})


def _prompt_project_promotion(project: str) -> None:
    """Offer the project's next step in Telegram, as /done does. Fire and forget."""
    from backend.utils.tasks_dashboard import _prompt_promotion_for

    task = asyncio.create_task(_prompt_promotion_for([f"#project/{project}"]))
    _background.add(task)
    task.add_done_callback(_background.discard)


@router.get("")
async def get_actions():
    return actions.read_action_lists()


@router.post("/ops")
async def post_ops(payload: OpsRequest):
    # No `await` from here to the final read: the whole batch runs as one uninterrupted
    # step on the event loop, so the sweeper cannot rewrite a file halfway through.
    results = []
    alias: dict[str, str] = {}
    projects = []
    for op in payload.ops:
        if op.op_id in _recent:
            result = _recent[op.op_id]
        else:
            try:
                done = _apply(op, alias)
                result = {"op_id": op.op_id, "status": "applied", "new_raw": done.new_raw}
                if done.project:
                    projects.append(done.project)
            except actions.OpSkipped as skip:
                result = {"op_id": op.op_id, "status": "skipped", "reason": skip.reason}
            _recent[op.op_id] = result
            while len(_recent) > _RECENT_MAX:
                _recent.popitem(last=False)
        if op.raw and result.get("new_raw"):
            alias[op.raw.strip()] = result["new_raw"]
        results.append(result)

    state = actions.read_action_lists()
    for project in dict.fromkeys(projects):
        _prompt_project_promotion(project)
    return {"results": results, **state}


MAX_AUDIO_BYTES = 25 * 1024 * 1024
_uploads_in_progress: set[str] = set()


@router.post("/capture-audio")
async def capture_audio(file: UploadFile, upload_id: str = Form(min_length=1, max_length=64),
                        captured_at: str | None = Form(default=None)):
    """Voice capture from the web app: transcribe a recording and add it to Captured.

    ``upload_id`` makes a resend harmless. Transcription can take a few seconds, long
    enough for a phone to drop the connection and try again; without it the same note
    would be captured twice.
    """
    if upload_id in _recent:
        return {**_recent[upload_id], **actions.read_action_lists()}
    if upload_id in _uploads_in_progress:
        raise HTTPException(status_code=409, detail="This recording is still being transcribed")

    ext = voice_capture.extension_for(file.content_type)
    if not ext:
        raise HTTPException(status_code=415, detail=f"Unsupported audio type: {file.content_type}")
    when = None
    if captured_at:
        try:
            when = datetime.strptime(captured_at, "%Y-%m-%d %H:%M")
        except ValueError:
            raise HTTPException(status_code=422, detail="captured_at must be YYYY-MM-DD HH:MM") from None
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="Empty recording")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Recording too large")

    _uploads_in_progress.add(upload_id)
    try:
        try:
            transcript = (await voice_capture.transcribe(data, file.content_type)).strip()
        except voice_capture.UnreadableAudio as e:
            # Unlike a transcriber hiccup this will never succeed, so say so: the client
            # drops the recording instead of retrying it forever.
            print(f"Backend [Actions]: Unreadable voice upload: {e}")
            raise HTTPException(status_code=422, detail="That recording couldn't be read") from None
        except Exception as e:
            # Not the client's fault and worth retrying: it keeps the recording queued.
            print(f"Backend [Actions]: Voice transcription failed: {e}")
            raise HTTPException(status_code=502, detail="Transcription failed") from None

        if not transcript:
            result = {"op_id": upload_id, "status": "skipped", "reason": "silent"}
        else:
            line = voice_capture.save_voice_capture(data, ext, transcript, via="GTD app Voice", when=when)
            result = {"op_id": upload_id, "status": "applied", "new_raw": line, "text": transcript}
        _recent[upload_id] = result
        while len(_recent) > _RECENT_MAX:
            _recent.popitem(last=False)
    finally:
        _uploads_in_progress.discard(upload_id)
    return {**result, **actions.read_action_lists()}
