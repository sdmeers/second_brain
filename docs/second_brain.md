## Problem it solves

Centaur Brain bridges the gap between high-velocity information consumption—from the web, YouTube, and long-form PDFs—and building permanent, structured knowledge. It solves the problem of scattered notes, passive reading, and manual organization by acting as an agentic "Second Brain." 

Moving beyond simple web clipping, it performs multi-layered ontology extraction, automatically deduplicating concepts and identifying high-value connections so you can focus on learning rather than managing files.

## What it does

The application automatically builds a dense, interconnected knowledge graph of high-value concepts extracted from your source materials, storing them directly in your Obsidian Vault.

![Graph Overview](https://github.com/sdmeers/centaur_brain/blob/main/docs/screenshots/centaur_brain_graph_example.png?raw=true)

It provides detailed, LLM-curated concept pages that synthesize insights from multiple sources while preserving nuance, eliminating the need to manually compile information on specific topics.

![Concept Example](https://github.com/sdmeers/centaur_brain/blob/main/docs/screenshots/centaur_brain_concept_example.png?raw=true)

![Concept Graph Closeup](https://github.com/sdmeers/centaur_brain/blob/main/docs/screenshots/centaur_brain_graph_example_closeup.png?raw=true)

Centaur Brain also generates richly formatted source summaries, capturing metadata, primary and related themes, and core takeaways from the original material.

![Summary Example](https://github.com/sdmeers/centaur_brain/blob/main/docs/screenshots/centaur_brain_summary_example.png?raw=true)

The system operates through a lightweight Chrome Extension that captures text and triggers a Python FastAPI backend. This backend leverages the Gemini model to summarize content, map concepts agentically, and proactively prevent redundant synonyms. An auto-healing "Brain Cleaner" script periodically scans your vault to merge overlapping concepts (e.g., "AI" vs "Artificial Intelligence") and fix broken wikilinks.

## How to use it

Centaur Brain operates as a local-first system consisting of a backend service and a browser extension. 

* **Capture Information:** Use the Chrome Extension to extract clean text from articles or trigger the backend to process YouTube transcripts and PDFs.
* **Review Summaries:** Open your Obsidian Vault to find newly created source summaries detailing core takeaways, themes, and automatically extracted concepts.
* **Explore Concepts:** Navigate through the generated concept pages and follow the interconnected backlinks to discover relationships between ideas in your personal knowledge graph.
* **Maintain the Brain:** Run the `brain_cleaner.py` utility periodically (or set it up as a background service) to deduplicate concepts, merge overlapping ideas, and automatically heal links across your vault.
