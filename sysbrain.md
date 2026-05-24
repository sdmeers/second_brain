# Centaur Brain: SysAdmin & Coding Expansion
**User Requirements Document (URD) & Implementation Plan**

## 1. Overview & Objectives
The goal of this expansion is to adapt Centaur Brain from a purely academic/conceptual summarization tool into a hybrid system that also tracks coding projects, sysadmin configurations, and learning tutorials. 

It must preserve the ability to recreate complex environments (by saving specific terminal commands/configs) while also providing a high-level, synthesized overview of the project's current state.

## 2. User Requirements (URD)

### 2.1 Ingestion (Frontend)
*   **Context Types:** The Chrome Extension must allow the user to select the type of context being ingested: `Article` (default), `Coding/SysAdmin`, or `Learning`.
*   **Project Tagging:** The extension must include an optional text input for `Project Name` (e.g., "OpenClaw", "Fedora G14 Setup").
*   **CLI Integration:** A local terminal script or command must exist to allow the user to pipe log files, command histories, or Gemini CLI chat histories directly to the backend.

### 2.2 Processing (Backend)
*   **Prompt Routing:** The backend must route the text to different LLM System Prompts based on the `Context Type` selected.
    *   *Article:* Extracts high-level concepts and philosophical summaries.
    *   *Coding/SysAdmin:* Extracts system architectures, specific terminal commands, configuration snippets, and a 1-2 sentence rationale for each.
*   **Concept vs. Tool Extraction:** Technical terms (e.g., `systemd`, `Podman`) should be extracted as entities but flagged in their YAML frontmatter as `type: tool` or `type: technology` to keep them distinct from abstract concepts like `AGI`.

### 2.3 Output & Obsidian Structure
*   **Hybrid Project Pages:** When data is sent to a project (e.g., `05 Projects/OpenClaw.md`), the system must *never* overwrite existing terminal commands. The file structure must adhere to:
    1.  **Project Overview:** Synthesized and rewritten by the LLM based on all historical and new context.
    2.  **Commands & Configurations:** A strictly additive list. New commands and their rationales are appended here.
    3.  **Dev Log:** A chronologically appended list (Date + Summary of the update).
*   **Vault Structure:** The existing 00-06 folder structure remains unchanged. Extracted tools go into `04 Concepts` but are isolated via YAML frontmatter tags (`tags: [technology]`).

---

## 3. Implementation Plan

### Phase 1: Frontend (Chrome Extension)
1.  **Update `popup.html`:** Add radio buttons for `Type` (Article, Coding, Learning) and a text input for `Project`.
2.  **Update `popup.js` / `background.js`:** Read the new fields and append them to the JSON payload sent to the local Python backend.

### Phase 2: CLI Ingestion Script
1.  **Create `backend/ingest_cli.py`:** A script that accepts text from stdin or a file path, alongside `--type` and `--project` flags.
2.  **Workflow:** `cat history.txt | uv run ingest_cli.py --type coding --project "Fedora Setup"`

### Phase 3: Backend Routing & Prompts
1.  **Modify `main.py`:** Parse the new `type` and `project` keys from the incoming payload.
2.  **Create `prompts.py`:** Define the `CODING_SYSTEM_PROMPT` specifically instructing the LLM to format output as:
    *   `"overview": "..."`
    *   `"commands": [{"command": "...", "rationale": "..."}]`
    *   `"technologies": ["Podman", "systemd"]`

### Phase 4: The Hybrid File Updater
1.  **File Parsing:** If a file exists in `05 Projects/`, parse the Markdown to extract the current Overview, Commands, and Dev Log sections.
2.  **LLM Synthesis:** Send the *old* overview + the *new* context to the LLM to generate a *new* overview.
3.  **Reconstruction:** Programmatically write back the file: New Overview + Old Commands + New Commands + Old Dev Log + New Dev Log Entry.

---

## 4. Unit Tests

To ensure the new features work and do not break the existing paper-summarization workflow:

**Test 1: Prompt Routing**
*   *Action:* Send a payload with `type: article`.
*   *Expected:* It uses the default system prompt.
*   *Action:* Send a payload with `type: coding`.
*   *Expected:* It uses the new `CODING_SYSTEM_PROMPT`.

**Test 2: Hybrid File Append (New File)**
*   *Action:* Ingest a coding chat for a non-existent project "TestProj".
*   *Expected:* Creates `05 Projects/TestProj.md` with all three sections (Overview, Commands, Dev Log).

**Test 3: Hybrid File Append (Existing File)**
*   *Action:* Ingest a *second* coding chat for "TestProj".
*   *Expected:* The Overview is updated. The Commands section contains commands from *both* ingestions. The Dev Log has two dated entries. No commands from the first ingestion are lost.

**Test 4: Tagging Technologies**
*   *Action:* Ingest a coding chat mentioning `Docker`.
*   *Expected:* Creates `04 Concepts/Docker.md` containing `type: technology` in the frontmatter.