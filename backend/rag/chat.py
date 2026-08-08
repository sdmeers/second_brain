import os
from google.genai import types
from backend.config import GEMINI_MODEL
from backend.utils.gemini import call_gemini_with_retry
from backend.rag.embeddings import get_embedding
from backend.rag.vectorstore import search_similar_chunks

async def query_brain(user_query: str, chat_history: list = None, top_k: int = 5) -> dict:
    """Performs vector search on the vault context and uses Gemini to answer a conversational query."""
    query_lower = user_query.lower().strip()
    
    # Intercept SITREP and Daily Summary queries directly
    if any(kw in query_lower for kw in ["sitrep", "daily summary", "daily digest", "current summary", "status report", "what happened today", "what happened this week"]):
        from backend.brain_cleaner import generate_sitrep_summary
        sitrep_text = await generate_sitrep_summary()
        return {
            "answer": sitrep_text,
            "sources": ["00_System/Daily_Digest.md"]
        }

    # 1. Embed the query
    try:
        query_vector = await get_embedding(user_query)
    except Exception as e:
        return {
            "answer": f"Error generating search vector: {e}",
            "sources": []
        }
        
    # 2. Search for similar chunks (increase k for list/enumeration queries)
    query_lower = user_query.lower()
    is_list_query = any(kw in query_lower for kw in ["list", "all", "books", "reading", "projects", "overview", "show me"])
    effective_k = 25 if is_list_query else top_k
    
    chunks = await search_similar_chunks(query_vector, top_k=effective_k)
    
    # Hybrid Retrieval: Dynamically scan matching P.A.R.A Projects or Areas directories
    from backend.config import OBSIDIAN_VAULT_PATH
    matched_para_dirs = []
    
    if OBSIDIAN_VAULT_PATH:
        for parent_folder in ["02_Projects", "03_Areas"]:
            parent_path = os.path.join(OBSIDIAN_VAULT_PATH, parent_folder)
            if os.path.exists(parent_path):
                for sub in os.listdir(parent_path):
                    full_sub = os.path.join(parent_path, sub)
                    if os.path.isdir(full_sub) and not sub.startswith((".", "_")):
                        sub_clean = sub.lower()
                        # Match if subfolder name or key words appear in query
                        if sub_clean in query_lower or any(w in query_lower for w in sub_clean.split() if len(w) > 3):
                            matched_para_dirs.append((parent_folder, sub, full_sub))
                            
        # Explicit query keyword aliases
        if any(kw in query_lower for kw in ["book", "books", "reading", "to read"]):
            reading_dir = os.path.join(OBSIDIAN_VAULT_PATH, "03_Areas", "Reading")
            if os.path.exists(reading_dir) and not any(d[1] == "Reading" for d in matched_para_dirs):
                matched_para_dirs.append(("03_Areas", "Reading", reading_dir))
                
        if any(kw in query_lower for kw in ["project", "projects", "active projects"]):
            proj_dir = os.path.join(OBSIDIAN_VAULT_PATH, "02_Projects")
            if os.path.exists(proj_dir):
                for sub in os.listdir(proj_dir):
                    full_sub = os.path.join(proj_dir, sub)
                    if os.path.isdir(full_sub) and not sub.startswith((".", "_")):
                        if not any(d[2] == full_sub for d in matched_para_dirs):
                            matched_para_dirs.append(("02_Projects", sub, full_sub))
                            
        # Append note contents from all matched P.A.R.A folders to context
        for parent_name, folder_name, folder_path in matched_para_dirs:
            for file in os.listdir(folder_path):
                if file.endswith(".md") and not file.startswith("."):
                    rel_path = f"{parent_name}/{folder_name}/{file}"
                    if not any(c["file_path"] == rel_path for c in chunks):
                        file_full = os.path.join(folder_path, file)
                        try:
                            with open(file_full, "r", encoding="utf-8") as f:
                                txt = f.read()[:1200]
                            chunks.append({
                                "file_path": rel_path,
                                "chunk_index": 0,
                                "text": txt,
                                "distance": 0.0
                            })
                        except Exception:
                            pass
                            
    if not chunks:
        return {
            "answer": "No relevant context found in your second brain notes to answer this question.",
            "sources": []
        }
        
    # 3. Format the context and prepare system instruction
    from datetime import datetime
    current_date_str = datetime.now().strftime("%A, %B %d, %Y")
    
    system_instruction = f"""You are Steven's personal knowledge librarian.
Today's date is {current_date_str}.
Your goal is to answer Steven's question accurately using only the provided context chunks from his personal Obsidian vault.

CRITICAL RULES:
1. TRUTHFULNESS: Only answer based on the retrieved context. If the context does not contain enough information to answer the question, state politely that you couldn't find the answer in his notes.
2. CITATIONS: Always cite the source files you used to answer the question. Cite them using Obsidian wikilinks format: [[Filename]] (omit the .md extension and directory paths, e.g. [[Syncthing IDs for Second Brain]]).
3. TONE: Be direct, structured, and concise. No fluff.
"""


    context_str = ""
    sources = set()
    for idx, chunk in enumerate(chunks, 1):
        # Extract filename without extension as wiki-link representation
        filename_only = os.path.basename(chunk["file_path"]).rsplit(".", 1)[0]
        context_str += f"[{idx}] Source: [[{filename_only}]]\\nContent: {chunk['text']}\\n\\n"
        sources.add(chunk["file_path"])
        
    history_str = ""
    if chat_history:
        history_str = "\\nRecent Conversation History:\\n"
        for msg in chat_history:
            role = "User" if msg["role"] == "user" else "Librarian"
            history_str += f"{role}: {msg['text']}\\n"

    prompt = f"Retrieved Vault Context:\\n\\n{context_str}{history_str}\\nUser Question: {user_query}"

    
    # 4. Call Gemini Flash
    try:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.2,
        )
        
        print("RAG [Query]: Generating response with Gemini...")
        response = await call_gemini_with_retry(
            model=GEMINI_MODEL,
            contents=prompt,
            config=config
        )
        
        answer = response.text.strip()
        return {
            "answer": answer,
            "sources": list(sources)
        }
    except Exception as e:
        print(f"RAG [Query]: Error calling Gemini for chat generation: {e}")
        return {
            "answer": f"Error generating answer with Gemini: {e}",
            "sources": list(sources)
        }
