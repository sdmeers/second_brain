"""Collects what a run did, or in dry-run mode what it would have done and cost."""

from dataclasses import dataclass, field

from backend.feeds.ledger import format_pence


@dataclass
class RunReport:
    feed: str
    dry_run: bool = False
    notes: list[str] = field(default_factory=list)        # vault-relative paths written (or planned)
    skipped: list[str] = field(default_factory=list)      # source IDs already ingested and unchanged
    raw_files: list[str] = field(default_factory=list)
    model_calls: int = 0
    cost_pence: float = 0.0                               # actual, or worst-case estimate in dry run

    def text(self) -> str:
        verb = "would write" if self.dry_run else "wrote"
        cost = "estimated cost up to" if self.dry_run else "cost"
        lines = [f"{self.feed}{' (dry run)' if self.dry_run else ''}: {verb} {len(self.notes)} note(s), "
                 f"skipped {len(self.skipped)} unchanged, {self.model_calls} model call(s), "
                 f"{cost} {format_pence(self.cost_pence)}"]
        lines += [f"  {verb}: {p}" for p in self.notes]
        return "\n".join(lines)
