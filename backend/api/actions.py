"""HTTP API behind the actions web app (``webapp/``).

``GET  /api/actions``      every list, in priority order.
``POST /api/actions/ops``  an ordered batch of changes, replayable after being offline.
"""

import asyncio
import os
from collections import OrderedDict
from typing import Literal

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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
