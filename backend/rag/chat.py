import os
from google.genai import types
from backend.config import GEMINI_MODEL
from backend.utils.gemini import call_gemini_with_retry
from backend.rag.embeddings import get_embedding
from backend.rag.vectorstore import search_similar_chunks

async def query_brain(user_query: str, top_k: int = 5) -> dict:
    """Performs vector search on the vault context and uses Gemini to answer a conversational query."""
    print(f"RAG [Query]: Querying brain for: '{user_query}'...")
    
    # 1. Embed the query
    try:
        query_vector = await get_embedding(user_query)
    except Exception as e:
        return {
            "answer": f"Error generating search vector: {e}",
            "sources": []
        }
        
    # 2. Search for similar chunks
    chunks = await search_similar_chunks(query_vector, top_k=top_k)
    
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
        context_str += f"[{idx}] Source: [[{filename_only}]]\nContent: {chunk['text']}\n\n"
        sources.add(chunk["file_path"])
        
    prompt = f"Retrieved Vault Context:\n\n{context_str}\nUser Question: {user_query}"
    
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
