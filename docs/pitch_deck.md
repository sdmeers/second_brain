---
marp: true
theme: default
class: lead
backgroundColor: #f8f9fa
color: #212529
style: |
  h1 { color: #0d6efd; }
  h2 { color: #0d6efd; }
---

# The Team Brain
## From Passive Storage to Active Intelligence
Redefining how we capture, share, and act on team knowledge.

---

## 🛑 The Problem: Passive Archives
Our current knowledge systems (Sharepoint, Teams and Jira) rely on manual effort.
* **Friction in Capture:** We lose ideas because documenting them takes too long.
* **Siloed Knowledge:** What one team member researches is rarely discovered by others.
* **The "Data-Entry" Tax:** We spend hours extracting tasks from Teams transcripts and updating status tickets.

---

## 🚀 The Solution: "Agent-as-Librarian"
Imagine a system where you stop organizing and start executing.
* **The Team Brain** is an AI-driven, local-first system that acts as our Chief of Staff.
* You dump messy inputs (voice notes, PDFs, meeting transcripts, emails and PowerPoint Presentations) into an Inbox.
* The AI agent automatically reads, categorizes, links, and extracts actionable tasks.

---

## 🎙️ Frictionless Capture
Capture knowledge exactly where you are, without formatting.

1. **Voice Notes on the Go:** Speak a messy thought into Slack/Telegram. The AI transcribes and formats it.
2. **Teams Meetings:** Drop a transcript file. The AI extracts the decisions made.
3. **Important Emails:** Forward a client request. The AI separates the tasks from the reference material.

---

## ⚡ The Multiplier Effect (Collective Intelligence)
It’s not just a search engine; it’s an active participant.

* **Automatic Task Delegation:** The AI reads a transcript, notes *"David agreed to update the slides,"* and updates the master Task list, assigning it to David.
* **Conversational Retrieval (RAG):** Ask the chatbot, *"What were the client requirements the sales team discussed last week?"*
* **Instant Onboarding:** New hires can ask the Team Brain for the history of a project and its active blockers, instantly catching up.

---

## 🔗 Jira Integration (Working where we work)
The Team Brain doesn't replace Jira; it supercharges it.

* **Agentic Ticket Creation:** The AI identifies a software bug in a meeting transcript and automatically hits the Jira API to create and assign the ticket.
* **Live Syncing:** Active Jira tickets sync to our local brain as plain text, giving the RAG chatbot live context on what we are building.
* **Status Updates via Chat:** Tell the bot, "I finished the login refactor," and it moves the Jira ticket to 'Done'.

---

## 🛠️ The Architecture (Future-Proof & Cost-Effective)
* **Plain Text Foundation:** The entire "database" is just Markdown files. No proprietary databases. No vendor lock-in.
* **Self-Evolving Rules:** The AI's behavior is dictated by plain text files. Want the AI to handle tasks differently? Just tell it to update its instructions.
* **Git-Backed:** Every change made by a human or the AI is tracked via Git for a perfect audit trail.
* **Near-Zero Cost:** Runs on a standard server using free-tier LLM APIs (like Gemini) and local models for embedding and transcription.

---

# 🚀 Next Steps
Let's stop acting as data-entry clerks for our knowledge bases.

**Proposal:** A 2-week pilot with a small team using a shared Slack channel and a centralized Inbox.
