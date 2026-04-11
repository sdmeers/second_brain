# 🧠 Centaur Brain

<img src="centaur_brain_icon.jpg" width="120" alt="Centaur Brain Logo">

Centaur Brain is an agentic "Second Brain" system designed to bridge the gap between high-velocity information consumption (web, YouTube, PDFs) and permanent, structured knowledge in **Obsidian**.

Inspired by Andrej Karpathy's vision of agentic workflows, it moves beyond simple "web clipping" to provide a multi-layered ontology extraction using the **Gemini 3.1 Flash Lite** model.

## 🖼️ Visual Showcase

### 🗺️ Knowledge Graph
The system automatically builds a dense, interconnected graph of high-value concepts.
![Graph Overview](screenshots/centaur_brain_graph_example.png)

### 🔍 Concept Synthesis
Detailed, LLM-curated concept pages that synthesize insights from multiple sources while preserving nuance.
![Concept Example](screenshots/centaur_brain_concept_example.png)

### 📄 Source Summaries
Richly formatted summaries with metadata, primary/related themes, and core takeaways.
![Summary Example](screenshots/centaur_brain_summary_example.png)

## 🏗️ Architecture

The solution consists of three primary components:

1.  **Chrome Extension:** A lightweight capture tool that extracts clean text from articles or triggers the backend to fetch transcripts from YouTube and process full PDFs.
2.  **FastAPI Backend:** A Python service that orchestrates the heavy lifting:
    *   Full-text extraction from multi-page PDFs.
    *   Strategic summarization using the Gemini API.
    *   **Proactive Deduplication:** Uses RAG (Retrieval-Augmented Generation) by injecting your existing concept dictionary into the prompt to prevent creating redundant synonyms during ingestion.
    *   API Throttling & Quota Management to respect free-tier limits.
3.  **Obsidian Vault:** The destination for all captured knowledge, structured into `Summaries`, `Atlas` (themes), and `Concepts`.

## 🚀 Key Features

*   **Full PDF Ingestion:** Unlike most tools, the backend processes every page of a document, ensuring deep analysis of long-form reports.
*   **Agentic Concept Mapping:** For every source ingested, the system identifies 5-15 highly specific concepts and automatically updates their dedicated pages in your vault with backlinks and contextual snippets.
*   **Auto-Healing Janitor:** A maintenance script (`janitor_clean.py`) that periodically scans your vault to:
    *   **Deduplicate & Merge:** Intelligently synthesizes overlapping concepts (e.g., "AI" vs "Artificial Intelligence") into single canonical pages while healing all links vault-wide.
    *   **Link Healing:** Automatically updates wikilinks and detects "orphaned" mentions to generate new concept pages.
    *   **Hash-based State Tracking:** Prevents redundant refactoring and "semantic smoothing" by only re-processing files that have actually changed since the last pass.
*   **Background Automation:** Includes a systemd user service for Linux (Fedora) users to keep the intelligence layer running persistently.

## 🛠️ Installation

### 1. Prerequisites
*   [uv](https://github.com/astral-sh/uv) (The extremely fast Python package manager)
*   A Google AI Studio API Key (Free tier supported)
*   Obsidian installed locally

### 2. Backend Setup
```bash
cd backend
cp .env.example .env
# Edit .env with your GEMINI_API_KEY and OBSIDIAN_VAULT_PATH
uv run main.py
```

### 3. Extension Setup
1.  Open Chrome and navigate to `chrome://extensions/`.
2.  Enable **Developer mode**.
3.  Click **Load unpacked** and select the `extension/` folder from this repository.

### 4. Background Service (Linux/Fedora)
To run the backend automatically on login:
```bash
# Copy the service template or create it at:
# ~/.config/systemd/user/centaur-brain.service
systemctl --user enable centaur-brain.service
systemctl --user start centaur-brain.service
```

## 🧹 Maintenance

Run the Janitor pass to keep your graph clean and your concepts synthesized:
```bash
cd backend
# Runs deduplication, link healing, and refactoring with quota safety
uv run janitor_clean.py
```

## 🛡️ Privacy & Security

*   The system operates **locally first**. Your raw document text is only sent to the Gemini API for analysis; it is never stored on a third-party server other than your local machine and your chosen LLM provider.
*   `.env` files and local source lists (`sources_all.txt`) are ignored by Git to prevent credential leakage.

## 📜 License
MIT
