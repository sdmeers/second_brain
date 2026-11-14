"""Automatic context feeds (journal, calendar, AI chats, email, household documents).

Shared rules for every feed, from docs/implementation_plan/ingestion/INTENT.md:

- a feed calls a model only through ``feeds.llm.generate`` (masking, budget, ledger, no tools);
- a feed writes notes only through ``feeds.notes.write_feed_note`` (frontmatter, idempotency);
- every entry point accepts ``dry_run`` and then writes nothing and spends nothing.
"""
