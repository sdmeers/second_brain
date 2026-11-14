"""Cost ledger: a hard monthly cap on what the feeds spend on model calls.

The cap is enforced *before* a call, against a worst-case estimate (input size plus the
full output allowance), so the month can never end over budget. The actual cost is
recorded afterwards from the provider's token counts.
"""

from datetime import datetime

from backend import config
from backend.feeds import state

# USD per million tokens, from https://ai.google.dev/gemini-api/docs/pricing (standard
# paid tier, read 2026-11-14). Output includes thinking tokens. A model missing from this
# table is charged at FALLBACK_PRICE, which is deliberately expensive.
PRICES_USD_PER_M = {
    "gemini-3.1-flash-lite": {"input": 0.25, "audio_input": 0.50, "output": 1.50},
    "gemini-embedding-2": {"input": 0.20, "audio_input": 0.20, "output": 0.0},
}
FALLBACK_PRICE = {"input": 5.0, "audio_input": 5.0, "output": 25.0}


class BudgetExceeded(RuntimeError):
    """The call would take this month's feed spend over the cap."""


class ApprovalRequired(RuntimeError):
    """The run's estimate is over the threshold (or it is a backfill) and is not approved."""

    def __init__(self, feed: str, estimate_pence: float, reason: str):
        self.feed, self.estimate_pence, self.reason = feed, estimate_pence, reason
        super().__init__(f"{feed}: estimated {format_pence(estimate_pence)} needs approval ({reason})")


def format_pence(pence: float) -> str:
    return f"£{pence / 100:.2f}" if pence >= 100 else f"{pence:.2f}p"


def estimate_tokens(text: str) -> int:
    """Upper-leaning token estimate: about three characters a token."""
    return len(text) // 3 + 1


def cost_pence(model: str, input_tokens: int, output_tokens: int, audio: bool = False) -> float:
    price = PRICES_USD_PER_M.get(model, FALLBACK_PRICE)
    usd = (input_tokens * price["audio_input" if audio else "input"]
           + output_tokens * price["output"]) / 1_000_000
    return usd * config.FEEDS_USD_TO_GBP * 100


def current_month() -> str:
    return datetime.now().strftime("%Y-%m")


def month_spent_pence(month: str | None = None) -> float:
    with state.connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_pence), 0) FROM ledger WHERE month = ?",
            (month or current_month(),)).fetchone()
    return float(row[0])


def remaining_pence() -> float:
    return config.FEEDS_MONTHLY_BUDGET_PENCE - month_spent_pence()


def check_budget(feed: str, worst_case_pence: float, use_reserve: bool = False) -> None:
    """Raise BudgetExceeded unless the worst case still fits under this month's cap.

    Model calls stop short of the cap by the indexing reserve; only note writes pass
    ``use_reserve=True``. Total spend therefore never passes the cap.
    """
    remaining = remaining_pence() - (0 if use_reserve else config.FEEDS_INDEXING_RESERVE_PENCE)
    if worst_case_pence > remaining:
        raise BudgetExceeded(
            f"{feed}: call could cost {format_pence(worst_case_pence)} but only "
            f"{format_pence(max(remaining, 0))} of the monthly "
            f"{format_pence(config.FEEDS_MONTHLY_BUDGET_PENCE)} is left")


def check_run(feed: str, estimate_pence: float, backfill: bool = False, approved: bool = False) -> None:
    """Gate a whole run: over the monthly cap is refused outright; a backfill or a run
    over the threshold needs ``approved=True``."""
    check_budget(feed, estimate_pence)
    if approved:
        return
    if backfill:
        raise ApprovalRequired(feed, estimate_pence, "backfill")
    if estimate_pence > config.FEEDS_APPROVAL_THRESHOLD_PENCE:
        raise ApprovalRequired(
            feed, estimate_pence, f"over the {format_pence(config.FEEDS_APPROVAL_THRESHOLD_PENCE)} threshold")


def record(feed: str, model: str, kind: str, input_tokens: int, output_tokens: int,
           audio: bool = False) -> float:
    """Add one ledger row and return its cost in pence."""
    pence = cost_pence(model, input_tokens, output_tokens, audio=audio)
    now = datetime.now()
    with state.connect() as conn:
        conn.execute(
            "INSERT INTO ledger (ts, month, feed, model, kind, input_tokens, output_tokens, cost_pence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now.isoformat(timespec="seconds"), now.strftime("%Y-%m"), feed, model, kind,
             input_tokens, output_tokens, pence))
    return pence


def summary(month: str | None = None) -> dict:
    """Spend per feed for a month, plus the total and what is left."""
    month = month or current_month()
    with state.connect() as conn:
        rows = conn.execute(
            "SELECT feed, SUM(cost_pence) FROM ledger WHERE month = ? GROUP BY feed", (month,)).fetchall()
    by_feed = {r[0]: float(r[1]) for r in rows}
    total = sum(by_feed.values())
    return {"month": month, "by_feed": by_feed, "total_pence": total,
            "remaining_pence": config.FEEDS_MONTHLY_BUDGET_PENCE - total}


def spent_today_pence(feed: str) -> float:
    with state.connect() as conn:
        row = conn.execute("SELECT COALESCE(SUM(cost_pence), 0) FROM ledger WHERE feed = ? AND ts LIKE ?",
                           (feed, datetime.now().strftime("%Y-%m-%d") + "%")).fetchone()
    return float(row[0])
