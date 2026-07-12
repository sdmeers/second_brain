# 🧠 Second Brain Orchestrator

The **Second Brain Orchestrator** is an "Agent-as-Librarian" personal knowledge operating system running 24/7 on your home server. It bridges the gap between passive note-taking (Obsidian markdown files) and active automation (Gemini AI parsing, local Whisper audio transcription, SQLite-backed task queues, and vector Q&A).

All elements are stored as plain text markdown files in a standard P.A.R.A. folder structure, synced bidirectionally to your laptop and mobile devices via Syncthing.

---

## 🚀 Core Features

1. **Unified PARA Ingestion Router:** Classifies incoming links, voice notes, tasks, emails, and documents via Gemini structured JSON outputs, automatically routing them to the correct Projects (`02_Projects/`), Areas (`03_Areas/`), Research Brain (`04_Resources/Research_Brain/`), or reference folders.
2. **Local GPU Voice Note Transcription:** Uses `faster-whisper` (`large-v3` model) running directly on the homeserver's GTX 1070 GPU via CUDA for fast, zero-cost, private voice transcription.
3. **Research Brain Ontology Pipeline:** Automatically extracts core concepts, summaries, wikilinks, and metadata from research papers, web articles, and books, building an interconnected knowledge graph.
4. **Master Task Sorter & Archiver:** Running every 15 seconds, it automatically parses active tasks in `Tasks.md`, categorizes them into smart deadline sections (`🚨 Overdue`, `📅 Due Today`, `🗓️ Upcoming`, `🗃️ Backlog`), and archives completed items (`- [x]`) into `Completed_Tasks.md` with completion timestamps.
5. **Fast RAG Vector Q&A:** Leverages `sqlite-vss` (vector search extension) to store chunked embeddings of your vault. Ask questions through Telegram or CLI to retrieve sourced citations. Operates in SQLite WAL (Write-Ahead Logging) mode to prevent concurrency locks.
6. **Robust Daemon Architecture:** Operates as a systemd user service with crash recovery, atomic write safety, and automatic Git commits on every successful ingestion.

---

## 🛠️ Getting Started

### Managing the Background Service
The orchestrator runs natively as a systemd user service. You do not need sudo to manage it:

*   **Check Daemon Status:**
    ```bash
    systemctl --user status second-brain.service
    ```
*   **Restart the Service:**
    ```bash
    systemctl --user restart second-brain.service
    ```
*   **Stop the Service:**
    ```bash
    systemctl --user stop second-brain.service
    ```
*   **Enable/Disable Start on Boot:**
    ```bash
    systemctl --user enable second-brain.service
    systemctl --user disable second-brain.service
    ```
*   **View Real-Time Logs:**
    ```bash
    journalctl --user -u second-brain.service -f
    ```

---

## 💻 Local Network Ingest (Samba)
To facilitate rapid file copying from your laptop, the `/home/sdmeers/vault/01_Inbox` directory is exposed as a read/write Samba share on the homeserver. 

You can mount it on your Fedora laptop using:
```bash
sudo mkdir -p /mnt/Inbox
sudo mount -t cifs -o username=sdmeers,uid=1000,gid=1000 //192.168.50.101/Inbox /mnt/Inbox
ln -s /mnt/Inbox/ ~/SecondBrainInbox
```
Files dropped directly into `~/SecondBrainInbox/` will be swallowed by the server's watcher, processed, filed, and cleared automatically within 15 seconds.

---

## 📈 Monitoring & Health

You can query the live orchestrator health using the custom endpoint:
```bash
curl -s http://127.0.0.1:8085/health | jq .
```
This returns the current system status:
*   **`status`**: Current health status (`healthy`).
*   **`disk`**: Live disk space usage in the Obsidian vault (Total, Used, Free, Percent Free).
*   **`queue`**: Ingestion queue metrics (pending items, processing items, failed retries, and the timestamp of the last successful processing).

---

## 📂 Configuration Options

Environment variables are managed in the `backend/.env` file:
*   **`OBSIDIAN_VAULT_PATH`**: Path to your Obsidian vault directory (e.g. `/home/sdmeers/vault`).
*   **`GEMINI_API_KEY`**: Google AI Studio API key.
*   **`TELEGRAM_BOT_TOKEN`**: Bot token generated via BotFather for remote Telegram capture.
*   **`PORT`**: HTTP Port for the API server (default: `8085`).
*   **`ENABLE_OBSIDIAN_INBOX_POLLING`**: Scans the inbox folder for file drops (default: `True`).
*   **`ENABLE_TELEGRAM_BOT`**: Actively polls Telegram for messages and voice notes (default: `True`).
*   **`ENABLE_GMAIL_POLLING`**: Checks specified inbox for forwarded emails (default: `True`).

---

## 📖 Documentation Index
Explore the detailed guides located in this folder:
*   [[product_description|Product Vision & Design Decisions]]
*   [[user_guide|User Workflows & Guide]]
*   [[technical_implementation|Technical Architecture & Implementation Details]]
